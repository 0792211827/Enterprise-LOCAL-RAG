"""Per-user chat limits over the HTTP seam: 429 with a retry hint, per user, and slots freed however a stream ends."""

import asyncio
import json

from src.services.chat import ChatLimiter, RateLimited
from tests.chat_support import ask, caller, exchange, start_conversation, stack, world  # noqa: F401

BASE = "/api/v1/chat/conversations"


def send(stack, conversation_id, query="How much leave?"):
    return stack.http.post(f"{BASE}/{conversation_id}/messages", json={"query": query})


def test_going_over_the_per_minute_limit_is_a_429_with_a_retry_hint(stack, world):
    stack.use_limiter(ChatLimiter(per_minute=2, max_concurrent=0))
    conversation = start_conversation(stack, world)
    ask(stack, conversation["id"])
    ask(stack, conversation["id"])

    limited = send(stack, conversation["id"])

    assert limited.status_code == 429
    assert 1 <= int(limited.headers["Retry-After"]) <= 60
    assert "too quickly" in limited.json()["detail"] and limited.headers["Retry-After"] in limited.json()["detail"]
    # Nothing of the refused message was kept or sent to the model.
    assert len(stack.llm.stream_calls) == 2
    assert len(stack.http.get(f"{BASE}/{conversation['id']}").json()["messages"]) == 4


def test_going_over_the_concurrent_limit_is_a_429_until_a_generation_finishes(stack, world):
    limiter = ChatLimiter(per_minute=0, max_concurrent=1)
    stack.use_limiter(limiter)
    conversation = start_conversation(stack, world)
    running = asyncio.run(limiter.admit("alice"))  # alice already has an answer being written

    limited = send(stack, conversation["id"])
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1
    assert "being written" in limited.json()["detail"]

    running.release()
    assert send(stack, conversation["id"]).status_code == 200


def test_one_users_traffic_does_not_limit_another(stack, world):
    stack.use_limiter(ChatLimiter(per_minute=1, max_concurrent=1))
    alice, _ = exchange(stack, world)
    assert send(stack, alice).status_code == 429

    stack.act_as(caller("bob", "staff"))
    assert exchange(stack, world)[1][-1]["type"] == "done"


def test_the_slot_is_released_when_a_stream_finishes_or_fails(stack, world):
    limiter = ChatLimiter(per_minute=0, max_concurrent=1)
    stack.use_limiter(limiter)
    conversation = start_conversation(stack, world)

    for _ in range(2):  # a second answer only works because the first one freed its slot
        assert send(stack, conversation["id"]).status_code == 200

    async def broken(**kwargs):
        raise RuntimeError("model down")
        yield  # pragma: no cover

    stack.llm.generate_rag_answer_stream = broken
    assert "error" in send(stack, conversation["id"]).text
    stack.llm.generate_rag_answer_stream = None
    assert asyncio.run(limiter.admit("alice")) is not None  # still free after the failure


def test_the_slot_is_released_when_the_client_disconnects_mid_answer(stack, world):
    limiter = ChatLimiter(per_minute=0, max_concurrent=1)
    stack.use_limiter(limiter)
    conversation = start_conversation(stack, world)

    async def slow(**kwargs):
        yield {"response": "25 "}
        await asyncio.sleep(3600)

    stack.llm.generate_rag_answer_stream = slow

    async def disconnect_after_first_chunk():
        body = json.dumps({"query": "How much leave?"}).encode()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "path": f"{BASE}/{conversation['id']}/messages",
            "raw_path": f"{BASE}/{conversation['id']}/messages".encode(),
            "query_string": b"",
            "root_path": "",
            "scheme": "http",
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            "client": ("testclient", 1),
            "server": ("test", 80),
            "state": {},
        }
        first_chunk, gone, sent_body = asyncio.Event(), asyncio.Event(), False

        async def receive():
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            await gone.wait()
            return {"type": "http.disconnect"}

        async def send_(message):
            if message["type"] == "http.response.body" and b"delta" in message.get("body", b""):
                first_chunk.set()

        app_task = asyncio.create_task(stack.app(scope, receive, send_))
        await asyncio.wait_for(first_chunk.wait(), 10)
        try:
            await limiter.admit("alice")
            held = False
        except RateLimited:
            held = True
        gone.set()
        await asyncio.wait_for(app_task, 10)
        return held

    assert asyncio.run(disconnect_after_first_chunk()) is True  # the slot was held while streaming...
    assert asyncio.run(limiter.admit("alice")) is not None  # ...and freed by the disconnect
