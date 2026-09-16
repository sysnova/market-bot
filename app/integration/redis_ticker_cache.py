"""Redis owns payloads and bounded indexes; Python clients retain only handles.

Lua makes corrections, reference counts and window eviction one atomic operation.
Persistent history views are independent of engine leases and survive bot restarts.
"""

from __future__ import annotations

import atexit
import json
import threading
from collections.abc import Iterator
from hashlib import sha256
from time import time
from typing import Any, cast
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError

_SCRIPT = r"""
local p, op, owner, now = KEYS[1], ARGV[1], ARGV[2], tonumber(ARGV[3])
local a = cjson.decode(ARGV[4])
local payloads, refs = p..'payloads', p..'refs'
local function drop(h)
  if h and redis.call('HINCRBY', refs, h, -1) <= 0 then
    redis.call('HDEL', refs, h); redis.call('HDEL', payloads, h)
  end
end
local function put(key, field, value, h)
  local prev = redis.call('HGET', key, field)
  if prev ~= h then
    redis.call('HSET', payloads, h, value)
    redis.call('HINCRBY', refs, h, 1)
    redis.call('HSET', key, field, h)
    drop(prev)
  end
end
local function clearseries(s)
  for _, h in ipairs(redis.call('HVALS', s..':data')) do drop(h) end
  redis.call('DEL', s, s..':data', s..':final')
end
local function close(v)
  for _, s in ipairs(redis.call('SMEMBERS', v..':series')) do clearseries(s) end
  for _, h in ipairs(redis.call('HVALS', v..':values')) do drop(h) end
  redis.call('DEL', v, v..':series', v..':values')
end
local function release(o)
  for _, v in ipairs(redis.call('SMEMBERS', p..'owner:'..o)) do close(v) end
  redis.call('DEL', p..'owner:'..o)
  redis.call('ZREM', p..'owners', o)
end
if op == 'expire' then
  for _, o in ipairs(redis.call('ZRANGEBYSCORE', p..'owners', '-inf', now-180)) do
    release(o)
  end
  return cjson.encode(true)
end
redis.call('ZADD', p..'owners', now, owner)
if op == 'touch' then return cjson.encode(true) end
if op == 'release' then release(owner); return cjson.encode(true) end
if op == 'stats' then
  return cjson.encode({unique_payloads=redis.call('HLEN',payloads),
    owners=redis.call('ZCARD',p..'owners')})
end
local v = p..'view:'..a[1]
if op == 'open' or op == 'persistent' then
  local cap = tonumber(a[2])
  if cap < 1 or cap > 100000 then return redis.error_reply('invalid capacity') end
  local old = tonumber(redis.call('GET',v) or '0')
  redis.call('SET', v, math.max(cap, old))
  if op == 'open' then redis.call('SADD',p..'owner:'..owner,v) end
  return cjson.encode(true)
end
if op == 'close' then
  close(v); redis.call('SREM',p..'owner:'..owner,v); return cjson.encode(true)
end
local capacity = tonumber(redis.call('GET',v))
if not capacity then return redis.error_reply('cache view missing; restart consumer') end
if op == 'add' then
  for _, r in ipairs(a[2]) do
    local s, t = v..':'..r[1]..':'..r[2], tostring(r[3])
    redis.call('SADD',v..':series',s)
    put(s..':data',t,r[5],r[6])
    redis.call('ZADD',s,r[3],t)
    if r[4] then redis.call('ZADD',s..':final',r[3],t)
    else redis.call('ZREM',s..':final',t) end
    local overflow = redis.call('ZCARD',s) - capacity
    if overflow > 0 then
      for _, old in ipairs(redis.call('ZRANGE',s,0,overflow-1)) do
        drop(redis.call('HGET',s..':data',old))
        redis.call('HDEL',s..':data',old)
        redis.call('ZREM',s,old); redis.call('ZREM',s..':final',old)
      end
    end
  end
  return cjson.encode(true)
elseif op == 'history' then
  local s = v..':'..a[2]..':'..a[3]
  local idx = s
  if a[5] then idx = s..':final' end
  local limit = a[4]
  if limit == cjson.null then limit = capacity end
  if limit < 1 then return redis.error_reply('invalid limit') end
  local out = {}
  for _, t in ipairs(redis.call('ZRANGE',idx,-limit,-1)) do
    local h = redis.call('HGET',s..':data',t)
    table.insert(out,redis.call('HGET',payloads,h))
  end
  if #out == 0 then return '[]' end
  return cjson.encode(out)
elseif op == 'retain_symbols' then
  local allowed = {}
  for _, symbol in ipairs(a[2]) do allowed[symbol] = true end
  for _, s in ipairs(redis.call('SMEMBERS',v..':series')) do
    local symbol = string.match(string.sub(s,string.len(v)+2),'^([^:]+):')
    if not allowed[symbol] then clearseries(s); redis.call('SREM',v..':series',s) end
  end
elseif op == 'put' then put(v..':values',a[2],a[3],a[4])
elseif op == 'get' then
  local h = redis.call('HGET',v..':values',a[2])
  if not h then return 'null' end
  return cjson.encode(redis.call('HGET',payloads,h))
elseif op == 'remove' then
  drop(redis.call('HGET',v..':values',a[2])); redis.call('HDEL',v..':values',a[2])
elseif op == 'keys' then
  local keys = redis.call('HKEYS',v..':values')
  if #keys == 0 then return '[]' end
  return cjson.encode(keys)
elseif op == 'snapshot' then
  local out = {}
  local values = redis.call('HGETALL',v..':values')
  for i=1,#values,2 do out[values[i]]=redis.call('HGET',payloads,values[i+1]) end
  return cjson.encode(out)
else return redis.error_reply('unknown operation') end
return cjson.encode(true)
"""


# Deletion must remain possible after noeviction rejects ordinary write scripts.
_DROP_HISTORY = """#!lua flags=allow-oom
local v, payloads, refs, coverage = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
for _, s in ipairs(redis.call('SMEMBERS', v..':series')) do
  for _, h in ipairs(redis.call('HVALS', s..':data')) do
    local remaining = tonumber(redis.call('HGET', refs, h) or '0') - 1
    if remaining <= 0 then
      redis.call('HDEL', refs, h); redis.call('HDEL', payloads, h)
    else redis.call('HINCRBY', refs, h, -1) end
  end
  redis.call('DEL', s, s..':data', s..':final')
end
return redis.call('DEL', v, v..':series', coverage)
"""


_DROP_ENGINE_VIEW = """#!lua flags=allow-oom
local v, payloads, refs = KEYS[1], KEYS[2], KEYS[3]
local function drop(h)
  local count = tonumber(redis.call('HGET', refs, h) or '0')
  if count <= 1 then
    redis.call('HDEL', refs, h); redis.call('HDEL', payloads, h)
  else redis.call('HINCRBY', refs, h, -1) end
end
for _, s in ipairs(redis.call('SMEMBERS', v..':series')) do
  for _, h in ipairs(redis.call('HVALS', s..':data')) do drop(h) end
  redis.call('DEL', s, s..':data', s..':final')
end
for _, h in ipairs(redis.call('HVALS', v..':values')) do drop(h) end
redis.call('DEL', v..':series', v..':values')
return redis.call('DEL', v)
"""


class RedisTickerCache:
    def __init__(self, redis: Redis, *, namespace: str = "marketbot:cache:v2:") -> None:
        self.redis = redis
        self.namespace = namespace
        self.owner = uuid4().hex
        self._script = redis.register_script(_SCRIPT)
        self._stopped = threading.Event()

    @classmethod
    def connect(cls, url: str) -> RedisTickerCache:
        client = cls(
            Redis.from_url(
                url,
                decode_responses=True,
                socket_timeout=30,
                socket_connect_timeout=5,
                health_check_interval=30,
            )
        )
        client.redis.ping()
        return client

    def call(self, operation: str, *args: object) -> Any:  # noqa: ANN401
        if operation == "add":
            args = (
                args[0],
                [
                    [*row, sha256(str(row[4]).encode()).hexdigest()]
                    for row in cast(list[list[object]], args[1])
                ],
            )
        elif operation == "put":
            args = (*args, sha256(str(args[2]).encode()).hexdigest())
        result = json.loads(
            cast(
                str,
                self._script(
                    keys=[self.namespace], args=[operation, self.owner, time(), json.dumps(args)]
                ),
            )
        )
        # Lua CJSON implementations can encode an empty table as an array.
        return {} if operation == "snapshot" and result == [] else result

    def view(self, capacity: int = 2_000) -> str:
        view = uuid4().hex
        self.call("open", view, capacity)
        return view

    def open_persistent(self, view: str, capacity: int) -> None:
        self.call("persistent", view, capacity)

    def discard_history(self, view: str) -> None:
        if not view.startswith("history:"):
            raise ValueError("only canonical histories can be discarded")
        self.redis.eval(
            _DROP_HISTORY,
            4,
            self.namespace + "view:" + view,
            self.namespace + "payloads",
            self.namespace + "refs",
            self.namespace + view + ":coverage",
        )

    def prune_unused_extended_history(self) -> int:
        """Retire oversized v2 bootstrap windows before registering new clients."""
        prefix = self.namespace + "view:"
        removed = 0
        for timeframe in ("15Min", "1Hour"):
            keys = cast(
                Iterator[str], self.redis.scan_iter(match=prefix + f"history:*:{timeframe}:all")
            )
            for key in keys:
                self.discard_history(key[len(prefix) :])
                removed += 1
        return removed

    def persistent_view(self, scope: str, capacity: int = 2_000) -> str:
        view = "context:" + scope
        self.open_persistent(view, capacity)
        return view

    def close_view(self, view: str) -> None:
        if not self._stopped.is_set():
            self.call("close", view)

    def start(self) -> None:
        self.call("touch")
        threading.Thread(target=self._heartbeat, daemon=True, name="redis-cache-lease").start()
        atexit.register(self.close)

    def reset_for_startup(self) -> int:
        """Drop engine windows before consumers start; preserve canonical bars and coverage."""
        if not self.namespace:
            raise ValueError("startup reset requires a nonempty cache namespace")
        prefix = self.namespace + "view:"
        removed = 0
        while True:
            deleted = 0
            keys = cast(Iterator[str], self.redis.scan_iter(match=prefix + "*", count=500))
            for key in keys:
                if key.startswith(prefix + "history:") or self.redis.type(key) != "string":
                    continue
                deleted += cast(
                    int,
                    self.redis.eval(
                        _DROP_ENGINE_VIEW,
                        3,
                        key,
                        self.namespace + "payloads",
                        self.namespace + "refs",
                    ),
                )
            removed += deleted
            if deleted == 0:
                break
        # No consumers exist yet. Their old leases must not survive the reset.
        while True:
            owners = list(
                cast(Iterator[str], self.redis.scan_iter(match=self.namespace + "owner:*"))
            )
            if not owners:
                break
            for offset in range(0, len(owners), 500):
                self.redis.delete(*owners[offset : offset + 500])
        self.redis.delete(self.namespace + "owners")
        return removed

    def _heartbeat(self) -> None:
        while not self._stopped.wait(30):
            try:
                self.call("touch")
                self.call("expire")
            except RedisError:
                continue

    def close(self) -> None:
        if not self._stopped.is_set():
            self._stopped.set()
            try:
                self.call("release")
            finally:
                self.redis.close()
