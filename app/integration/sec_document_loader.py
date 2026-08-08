"""Bounded, cached retrieval of primary SEC Archive documents."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from pathlib import Path

import httpx

from app.dilution_sec_engine import (
    FilingDocumentReference,
    LoadedFilingDocument,
    SecConfigurationError,
    SecHttpStatusError,
    SecRateLimitError,
    SecTimeoutError,
    SecTransportError,
    build_filing_document_url,
)


@dataclass(slots=True)
class SecArchiveDocumentLoader:
    """Read at most ``max_bytes`` and cache the bounded document by accession."""

    client: httpx.AsyncClient
    user_agent: str
    cache_root: Path
    max_bytes: int = 350_000
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if isinstance(self.max_bytes, bool) or self.max_bytes <= 0:
            raise SecConfigurationError("SEC document max_bytes must be positive")
        if not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise SecConfigurationError("SEC document timeout_seconds must be positive")
        if not self.user_agent.strip():
            raise SecConfigurationError("SEC document User-Agent cannot be blank")

    async def load_text(
        self, reference: FilingDocumentReference
    ) -> LoadedFilingDocument:
        cache_path = self._cache_path(reference)
        cached = await asyncio.to_thread(self._read_cache, cache_path)
        if cached is not None:
            return cached

        url = build_filing_document_url(reference)
        try:
            async with self.client.stream(
                "GET",
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "text/html,application/xhtml+xml,text/plain",
                    "Accept-Encoding": "gzip, deflate",
                },
                timeout=self.timeout_seconds,
            ) as response:
                self._raise_for_status(response)
                content = bytearray()
                truncated = False
                async for chunk in response.aiter_bytes():
                    remaining = self.max_bytes - len(content)
                    if remaining <= 0:
                        truncated = True
                        break
                    content.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        truncated = True
                        break
        except httpx.TimeoutException as error:
            raise SecTimeoutError("SEC primary document request timed out") from error
        except httpx.HTTPError as error:
            raise SecTransportError("SEC primary document transport error") from error

        document = LoadedFilingDocument(
            text=bytes(content).decode("utf-8", errors="replace"),
            truncated=truncated,
        )
        await asyncio.to_thread(self._write_cache, cache_path, document)
        return document

    def _cache_path(self, reference: FilingDocumentReference) -> Path:
        document_hash = sha256(reference.primary_document.encode("utf-8")).hexdigest()[:16]
        accession = reference.accession_number.replace("-", "")
        return self.cache_root / f"{accession}-{document_hash}.json"

    @staticmethod
    def _read_cache(path: Path) -> LoadedFilingDocument | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return LoadedFilingDocument(
                text=str(payload["text"]),
                truncated=bool(payload.get("truncated", False)),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @staticmethod
    def _write_cache(path: Path, document: LoadedFilingDocument) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {"text": document.text, "truncated": document.truncated},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            return

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        endpoint = response.request.url.path
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            seconds = int(retry_after) if retry_after and retry_after.isdigit() else None
            raise SecRateLimitError(endpoint, seconds)
        if not 200 <= response.status_code < 300:
            raise SecHttpStatusError(endpoint, response.status_code)
