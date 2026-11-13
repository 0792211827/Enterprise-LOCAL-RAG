"""The chat limiter: per-user request rate and concurrent generations, backed by Redis with an in-process fallback."""

import asyncio

import pytest
import redis
from src.services.chat import ChatLimiter, RateLimited


class Clock:
    def __init__(self, now=1_000_020.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeRedis:
    """Just the commands the limiter uses; ``down`` makes every call fail like an unreachable server."""

    def __init__(self):
        self.counters, self.sets, self.down, self.calls = {}, {}, False, 0

    def _check(self):
        self.calls += 1
        if self.down:
            raise redis.ConnectionError("down")

    def pipeline(self):
        return FakePipeline(self)

    def zrem(self, key, member):
        self._check()
        self.sets.get(key, {}).pop(member, None)


class FakePipeline:
    def __init__(self, server):
        self.server, self.ops = server, []

    def __getattr__(self, name):
        return lambda *args: self.ops.append((name, args))

    def execute(self):
        self.server._check()
        results = []
        for name, args in self.ops:
            if name == "incr":
                self.server.counters[args[0]] = self.server.counters.get(args[0], 0) + 1
                results.append(self.server.counters[args[0]])
            elif name == "zremrangebyscore":
                members = self.server.sets.setdefault(args[0], {})
                for member in [m for m, score in members.items() if score <= args[2]]:
                    del members[member]
                results.append(None)
            elif name == "zadd":
                self.server.sets.setdefault(args[0], {}).update(args[1])
                results.append(1)
            elif name == "zcard":
                results.append(len(self.server.sets.get(args[0], {})))
            else:
                results.append(True)
        return results


def admit(limiter, subject="alice"):
    return asyncio.run(limiter.admit(subject))


@pytest.fixture(params=["memory", "redis"])
def make(request):
    def build(**kwargs):
        return ChatLimiter(FakeRedis() if request.param == "redis" else None, **kwargs)

    return build


def test_requests_per_minute_are_limited_and_the_window_resets(make):
    clock = Clock(1_000_040.0)  # 20 seconds into a minute
    limiter = make(per_minute=2, max_concurrent=0, clock=clock)
    admit(limiter)
    admit(limiter)

    with pytest.raises(RateLimited) as limited:
        admit(limiter)
    assert (limited.value.reason, limited.value.retry_after) == ("rate", 40)

    clock.now += 40
    admit(limiter)


def test_concurrent_generations_are_limited_until_a_slot_is_released(make):
    limiter = make(per_minute=0, max_concurrent=2)
    first, second = admit(limiter), admit(limiter)

    with pytest.raises(RateLimited) as limited:
        admit(limiter)
    assert limited.value.reason == "concurrency" and limited.value.retry_after >= 1

    first.release()
    first.release()  # releasing twice frees only one slot
    third = admit(limiter)
    with pytest.raises(RateLimited):
        admit(limiter)
    second.release(), third.release()


def test_limits_are_per_user(make):
    limiter = make(per_minute=1, max_concurrent=1)
    admit(limiter, "alice")

    with pytest.raises(RateLimited):
        admit(limiter, "alice")
    admit(limiter, "bob")  # alice's traffic does not count against bob


def test_a_slot_that_is_never_released_frees_itself_when_its_lease_ends(make):
    clock = Clock()
    limiter = make(per_minute=0, max_concurrent=1, slot_seconds=60, clock=clock)
    admit(limiter)

    with pytest.raises(RateLimited):
        admit(limiter)
    clock.now += 61
    admit(limiter)


def test_a_limit_of_zero_is_off():
    limiter = ChatLimiter(per_minute=0, max_concurrent=0)
    assert not limiter.enabled
    assert all(admit(limiter) is None for _ in range(50))


def test_counts_are_shared_through_redis():
    server = FakeRedis()
    one, other = ChatLimiter(server, per_minute=0, max_concurrent=1), ChatLimiter(server, per_minute=0, max_concurrent=1)
    admit(one)

    with pytest.raises(RateLimited):  # a second worker sees the first's slot
        admit(other)


def test_when_redis_is_down_limits_still_hold_in_this_process_and_redis_is_retried_later():
    server, clock = FakeRedis(), Clock()
    server.down = True
    limiter = ChatLimiter(server, per_minute=0, max_concurrent=1, clock=clock)

    slot = admit(limiter)  # served, by local counts
    with pytest.raises(RateLimited):
        admit(limiter)
    calls = server.calls
    slot.release()
    admit(limiter)
    assert server.calls == calls  # Redis is left alone during the pause rather than timing out on every request

    server.down = False
    clock.now += 60
    admit(limiter)
    assert server.calls > calls  # and used again once it may have recovered
