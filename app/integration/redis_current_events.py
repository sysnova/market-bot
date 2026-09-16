"""Persistent, atomic latest analytical state, one envelope per exact subject."""

import asyncio
from typing import cast

from redis import Redis

from app.contracts import EventEnvelope
from app.event_bus.codec import decode_envelope, encode_envelope
from app.event_bus.current_state import event_order

_PUT = """
local previous = redis.call('HGET', KEYS[1], 'order')
if previous == ARGV[1] and redis.call('HGET', KEYS[1], 'payload') == ARGV[2] then
  return 1
end
if not previous or ARGV[1] >= previous then
  redis.call('HSET', KEYS[1], 'order', ARGV[1], 'payload', ARGV[2])
  redis.call('EXPIRE', KEYS[1], ARGV[3])
end
return 1
"""


class RedisCurrentEvents:
    def __init__(self, redis: Redis, *, namespace: str) -> None:
        self.redis = redis
        self.namespace = namespace
        self._put = redis.register_script(_PUT)

    async def get(self, subject: str) -> EventEnvelope | None:
        value = await asyncio.to_thread(self.redis.hget, self.namespace + subject, "payload")
        if value is None:
            return None
        payload = value.encode() if isinstance(value, str) else cast(bytes, value)
        return decode_envelope(payload)

    async def put(self, subject: str, envelope: EventEnvelope) -> None:
        await asyncio.to_thread(
            self._put,
            keys=[self.namespace + subject],
            args=[event_order(envelope), encode_envelope(envelope), 7 * 24 * 60 * 60],
        )
