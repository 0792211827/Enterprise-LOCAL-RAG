"""Per-user limits on chat: requests per minute and concurrent generations.

Counts live in Redis so every API worker shares them. Redis is an accelerator, not a dependency: when it is
unreachable the limiter falls back to counts held in this process (so each worker enforces the limits on its
own, looser overall but never off) and tries Redis again after a short pause. A limit of 0 turns it off.

Users are told apart by IdP subject, hashed before it becomes a key, so one user's traffic never touches
another's counts.
"""

import asyncio
import hashlib
import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Protocol, Tuple

logger = logging.getLogger(__name__)

RATE_WINDOW_SECONDS = 60
CONCURRENCY_RETRY_SECONDS = 5  # a generation takes seconds to minutes; this is only a hint to try again soon
REDIS_RETRY_SECONDS = 15  # after a Redis failure, how long before trying it again


class RateLimited(Exception):
    """The caller is over a limit. ``retry_after`` is a whole number of seconds."""

    def __init__(self, reason: str, retry_after: int):
        super().__init__(reason)
        self.reason = reason  # "rate" or "concurrency"
        self.retry_after = max(1, int(retry_after))


class _Backend(Protocol):
    def hit(self, user: str, now: float) -> int: ...
    def acquire(self, user: str, slot: str, limit: int, ttl: int, now: float) -> bool: ...
    def release(self, user: str, slot: str) -> None: ...


class MemoryBackend:
    """Counts held in this process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: Dict[Tuple[str, int], int] = {}
        self._slots: Dict[str, Dict[str, float]] = {}

    def hit(self, user: str, now: float) -> int:
        window = int(now // RATE_WINDOW_SECONDS)
        with self._lock:
            for key in [k for k in self._windows if k[1] < window]:
                del self._windows[key]
            self._windows[(user, window)] = self._windows.get((user, window), 0) + 1
            return self._windows[(user, window)]

    def acquire(self, user: str, slot: str, limit: int, ttl: int, now: float) -> bool:
        with self._lock:
            held = {s: expires for s, expires in self._slots.get(user, {}).items() if expires > now}
            if len(held) >= limit:
                self._slots[user] = held
                return False
            held[slot] = now + ttl
            self._slots[user] = held
            return True

    def release(self, user: str, slot: str) -> None:
        with self._lock:
            held = self._slots.get(user)
            if held is not None:
                held.pop(slot, None)
                if not held:
                    del self._slots[user]

    def active(self, user: str, now: float) -> int:
        with self._lock:
            return sum(1 for expires in self._slots.get(user, {}).values() if expires > now)


class RedisBackend:
    """Counts shared through Redis: a counter per user per minute, and a sorted set of leased generation slots."""

    def __init__(self, client, prefix: str = "chat:limit"):
        self.client = client
        self.prefix = prefix

    def hit(self, user: str, now: float) -> int:
        key = f"{self.prefix}:rate:{user}:{int(now // RATE_WINDOW_SECONDS)}"
        pipe = self.client.pipeline()
        pipe.incr(key)
        pipe.expire(key, RATE_WINDOW_SECONDS * 2)
        return int(pipe.execute()[0])

    def acquire(self, user: str, slot: str, limit: int, ttl: int, now: float) -> bool:
        key = f"{self.prefix}:slots:{user}"
        pipe = self.client.pipeline()  # one transaction: no other request sees the set between these steps
        pipe.zremrangebyscore(key, "-inf", now)
        pipe.zadd(key, {slot: now + ttl})
        pipe.zcard(key)
        pipe.expire(key, ttl)
        held = int(pipe.execute()[2])
        if held > limit:
            self.client.zrem(key, slot)
            return False
        return True

    def release(self, user: str, slot: str) -> None:
        self.client.zrem(f"{self.prefix}:slots:{user}", slot)


@dataclass
class Slot:
    """One running generation. ``release`` is idempotent and never raises."""

    _backend: _Backend
    _user: str
    _id: str
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            self._backend.release(self._user, self._id)
        except Exception as exc:  # noqa: BLE001 - the lease expires on its own
            logger.warning("Could not release a generation slot (%s); it expires with its lease", type(exc).__name__)


class ChatLimiter:
    def __init__(
        self,
        redis_client=None,
        *,
        per_minute: int = 30,
        max_concurrent: int = 3,
        slot_seconds: int = 600,
        clock: Callable[[], float] = time.time,
    ):
        self.per_minute = per_minute
        self.max_concurrent = max_concurrent
        self.slot_seconds = slot_seconds
        self._clock = clock
        self._memory = MemoryBackend()
        self._redis: Optional[RedisBackend] = RedisBackend(redis_client) if redis_client is not None else None
        self._redis_down_until = 0.0

    @property
    def enabled(self) -> bool:
        return self.per_minute > 0 or self.max_concurrent > 0

    def _pick(self) -> _Backend:
        if self._redis is not None and self._clock() >= self._redis_down_until:
            return self._redis
        return self._memory

    def _redis_failed(self, exc: Exception) -> None:
        self._redis_down_until = self._clock() + REDIS_RETRY_SECONDS
        logger.warning(
            "Chat limits: Redis unavailable (%s); counting in this process for %ds", type(exc).__name__, REDIS_RETRY_SECONDS
        )

    def _run(self, call: Callable[[_Backend], object]):
        """Run ``call`` against Redis, or this process's own counts if Redis is down or fails."""
        backend = self._pick()
        if backend is self._redis:
            try:
                return call(backend), backend
            except Exception as exc:  # noqa: BLE001 - any Redis failure means "use local counts"
                self._redis_failed(exc)
        return call(self._memory), self._memory

    def _admit_sync(self, subject: str) -> Optional[Slot]:
        user = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:32]
        now = self._clock()
        if self.per_minute > 0:
            count, _ = self._run(lambda b: b.hit(user, now))
            if count > self.per_minute:
                raise RateLimited("rate", math.ceil(RATE_WINDOW_SECONDS - now % RATE_WINDOW_SECONDS))
        if self.max_concurrent <= 0:
            return None
        slot_id = uuid.uuid4().hex
        granted, backend = self._run(lambda b: b.acquire(user, slot_id, self.max_concurrent, self.slot_seconds, now))
        if not granted:
            raise RateLimited("concurrency", CONCURRENCY_RETRY_SECONDS)
        return Slot(backend, user, slot_id)

    async def admit(self, subject: str) -> Optional[Slot]:
        """Count this request and take a generation slot for ``subject``.

        :raises RateLimited: over the per-minute or the concurrent limit; nothing is held in that case.
        :returns: the slot to release when the answer ends, or ``None`` when concurrency is not limited.
        """
        if not self.enabled:
            return None
        return await asyncio.to_thread(self._admit_sync, subject)


def make_chat_limiter(settings) -> ChatLimiter:
    """The limiter configured by ``CHAT__*``. Its Redis client connects lazily, so a Redis that is down at
    startup (or later) never stops the API and is picked up again once it is back."""
    from src.services.cache.factory import make_redis_client

    chat = settings.chat
    client = None
    try:
        if chat.rate_limit_per_minute or chat.max_concurrent_generations:
            client = make_redis_client(settings, verify=False, timeout=1)
    except Exception as exc:  # noqa: BLE001 - fall back to in-process counts
        logger.warning("Chat limits: no Redis client (%s); counting in this process", type(exc).__name__)
    return ChatLimiter(
        client,
        per_minute=chat.rate_limit_per_minute,
        max_concurrent=chat.max_concurrent_generations,
        slot_seconds=chat.generation_slot_seconds,
    )
