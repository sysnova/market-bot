"""Authenticated loopback JSON transport for the process-shared RAM cache."""

from __future__ import annotations

import atexit
import contextlib
import hmac
import json
import socket
import threading
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

from app.common.context_cache import set_context_cache
from app.common.settings import AppSettings

from .redis_ticker_cache import RedisTickerCache
from .shared_ticker_cache import TickerCache

_client: CacheClient | RedisTickerCache | None = None


class CacheClient:
    def __init__(self, port: int, token: str) -> None:
        self.port = port
        self._token = token
        self.owner = uuid4().hex
        self._stopped = threading.Event()
        self._local = threading.local()
        self._connections: list[HTTPConnection] = []
        self._connections_lock = threading.Lock()

    def call(self, operation: str, *args: object) -> Any:  # noqa: ANN401
        connection: HTTPConnection | None = getattr(self._local, "connection", None)
        if connection is None:
            connection = HTTPConnection("127.0.0.1", self.port, timeout=10)
            self._local.connection = connection
            with self._connections_lock:
                self._connections.append(connection)
        try:
            connection.request(
                "POST",
                "/cache",
                json.dumps({"owner": self.owner, "operation": operation, "args": args}),
                {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            body = response.read()
            if response.status != 200:
                raise RuntimeError(f"shared ticker cache rejected {operation}: {response.status}")
            return json.loads(body)
        except OSError, HTTPException, RuntimeError:
            connection.close()
            self._local.connection = None
            with self._connections_lock:
                if connection in self._connections:
                    self._connections.remove(connection)
            raise

    def start(self) -> None:
        self.call("touch")
        threading.Thread(target=self._heartbeat, daemon=True, name="ticker-cache-lease").start()
        atexit.register(self.close)

    def _heartbeat(self) -> None:
        while not self._stopped.wait(30):
            try:
                self.call("touch")
            except OSError, HTTPException, RuntimeError:
                # Reads/writes fail closed; never silently switch to a private cache.
                continue

    def close(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        with contextlib.suppress(OSError, HTTPException, RuntimeError):
            self.call("release")
        with self._connections_lock:
            for connection in self._connections:
                connection.close()
            self._connections.clear()

    def view(self, capacity: int = 2_000) -> str:
        view = uuid4().hex
        self.call("open", view, capacity)
        return view

    def persistent_view(self, scope: str, capacity: int = 2_000) -> str:
        # Compatibility for the legacy isolated HTTP test harness only.
        return self.view(capacity)

    def close_view(self, view: str) -> None:
        if not self._stopped.is_set():
            with contextlib.suppress(OSError, HTTPException, RuntimeError):
                self.call("close", view)


def configure_shared_cache(path: Path) -> None:
    global _client
    if _client is not None:
        raise RuntimeError("shared ticker cache already configured")
    endpoint = json.loads(path.read_text(encoding="utf-8"))
    client = client_from_endpoint(endpoint)
    client.start()
    _client = client
    set_context_cache(client)


def client_from_endpoint(endpoint: dict[str, Any]) -> CacheClient | RedisTickerCache:
    if endpoint.get("backend") == "redis":
        return RedisTickerCache.connect(endpoint["url"])
    return CacheClient(endpoint["port"], endpoint["token"])


def shared_cache_client() -> CacheClient | RedisTickerCache | None:
    return _client


def make_cache_server(token: str) -> ThreadingHTTPServer:
    cache = TickerCache()
    lock = threading.RLock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            super().setup()

        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            if self.path != "/cache" or not hmac.compare_digest(
                self.headers.get("Authorization", ""), f"Bearer {token}"
            ):
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16 * 1024 * 1024:
                    raise ValueError("invalid request size")
                request = json.loads(self.rfile.read(length))
                owner = request["owner"]
                args = request["args"]
                operation = request["operation"]
                with lock:
                    cache.expire(monotonic() - 180)
                    cache.touch(owner)
                    match operation:
                        case "touch":
                            result = None
                        case "open":
                            result = cache.open(owner, *args)
                        case "release":
                            result = cache.release(owner)
                        case "add":
                            result = cache.add(*args)
                        case "history":
                            result = cache.history(*args)
                        case "put":
                            result = cache.put(*args)
                        case "get":
                            result = cache.get(*args)
                        case "keys":
                            result = cache.keys(*args)
                        case "snapshot":
                            result = cache.snapshot(*args)
                        case "remove":
                            result = cache.remove(*args)
                        case "retain_symbols":
                            result = cache.retain_symbols(*args)
                        case "close":
                            result = cache.close(*args)
                        case "stats":
                            result = cache.stats()
                        case _:
                            raise ValueError("unknown operation")
                body = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except ValueError, KeyError, TypeError:
                self.send_error(400)

    class Server(ThreadingHTTPServer):
        def service_actions(self) -> None:
            with lock:
                cache.expire(monotonic() - 180)

    return Server(("127.0.0.1", 0), Handler)


def run_cache_server(*, endpoint_path: Path, ready_path: Path) -> None:
    url = AppSettings().redis_url.get_secret_value()
    client = RedisTickerCache.connect(url)
    endpoint_path.parent.mkdir(parents=True, exist_ok=True)
    ready_path.parent.mkdir(parents=True, exist_ok=True)
    endpoint_path.touch(mode=0o600)
    endpoint_path.chmod(0o600)
    endpoint_path.write_text(
        json.dumps(
            {
                "backend": "redis",
                "url": url,
            }
        ),
        encoding="utf-8",
    )
    ready_path.write_text(json.dumps({"status": "ready", "backend": "redis"}), encoding="utf-8")
    client.start()
    try:
        while not threading.Event().wait(30):
            client.redis.ping()
    finally:
        client.close()
        ready_path.unlink(missing_ok=True)
        endpoint_path.unlink(missing_ok=True)
