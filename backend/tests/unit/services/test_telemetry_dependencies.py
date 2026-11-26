"""Dependency calls and token counts, seen from the clients that make them (ticket 04).

Each client is real and talks to a faked endpoint, inside a request scope as it would be in the app; what is read
back is what telemetry emitted.
"""

import json
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import create_engine, text
from src.config import RedisSettings, Settings
from src.schemas.api.ask import AskRequest
from src.services.cache.client import CacheClient
from src.services.ollama.client import OllamaClient
from src.services.opensearch.client import OpenSearchClient
from src.services.providers.embeddings.ollama_provider import OllamaEmbeddingProvider
from src.services.providers.embeddings.openai_compatible_provider import OpenAICompatibleEmbeddingProvider
from src.services.providers.llm.openai_compatible_provider import OpenAICompatibleLLMProvider
from src.services.telemetry import InMemoryTelemetry, instrument_engine
from tests.unit.services.test_reranker import FakeEndpoint, reranker_for

CHUNKS = [{"chunk_text": "Parental leave is 25 days.", "title": "Handbook", "document_id": "d1"}]


@pytest.fixture
def telemetry():
    return InMemoryTelemetry()


@pytest.fixture
def request_scope(telemetry):
    """A request in flight, as the middleware opens one, for the length of a test."""
    handle = telemetry.start_request("POST", "rid-1")
    yield telemetry
    handle.finish("/ask", 200)


def completion(usage=None, content="25 days."):
    body = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def provider(handler):
    return OpenAICompatibleLLMProvider("http://llm/v1", "m", transport=httpx.MockTransport(handler))


class TestChatCalls:
    async def test_a_call_is_counted_and_timed_by_outcome_and_is_a_child_of_the_stage(self, request_scope, telemetry):
        llm = provider(lambda request: completion({"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}))

        with telemetry.stage("generate_answer"):
            await llm.generate_rag_answer("q", CHUNKS, "m")

        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="ok") == 1
        assert telemetry.value("rag.dependency.duration", dependency="chat", outcome="ok") == 1
        call, stage = telemetry.span_named("dependency.chat"), telemetry.span_named("generate_answer")
        assert call.parent.span_id == stage.context.span_id

    async def test_a_failing_endpoint_is_an_error_for_chat_only(self, request_scope, telemetry):
        llm = provider(lambda request: httpx.Response(503))

        with pytest.raises(httpx.HTTPStatusError):
            await llm.generate_rag_answer("q", CHUNKS, "m")

        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="error") == 1
        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="ok") == 0
        assert {labels["dependency"] for labels, _ in telemetry.points("rag.dependency.calls")} == {"chat"}
        assert telemetry.span_named("dependency.chat").status.status_code.name == "ERROR"


def sse(*events):
    body = "".join(f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def delta(text):
    return {"choices": [{"delta": {"content": text}}]}


class TestStreamedChatCalls:
    async def test_a_stream_is_one_call_that_ends_when_the_endpoint_is_done_even_if_the_consumer_stops_there(
        self, request_scope, telemetry
    ):
        usage = {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}}
        llm = provider(lambda request: sse(delta("25 "), delta("days."), usage, "[DONE]"))

        async for chunk in llm.generate_rag_answer_stream("q", CHUNKS, "llama3"):
            if chunk["done"]:
                break  # the consumer leaves the generator open, as the answer routes do

        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="ok") == 1
        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="cancelled") == 0
        assert telemetry.value("rag.llm.tokens.input", model="llama3") == 7
        assert telemetry.value("rag.llm.tokens.output", model="llama3") == 2

    async def test_a_stream_that_reports_no_usage_records_no_tokens(self, request_scope, telemetry):
        llm = provider(lambda request: sse(delta("25 days."), "[DONE]"))

        assert [c async for c in llm.generate_rag_answer_stream("q", CHUNKS, "llama3")]

        assert telemetry.points("rag.llm.tokens.input") == []

    async def test_a_stream_the_consumer_abandons_is_cancelled_not_failed(self, request_scope, telemetry):
        llm = provider(lambda request: sse(delta("25 "), delta("days."), "[DONE]"))

        stream = llm.generate_rag_answer_stream("q", CHUNKS, "llama3")
        await stream.__anext__()
        await stream.aclose()

        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="cancelled") == 1
        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="error") == 0

    async def test_a_stream_that_breaks_is_an_error(self, request_scope, telemetry):
        llm = provider(lambda request: sse(delta("25 "), {"error": {"message": "out of memory"}}))

        with pytest.raises(Exception):
            [c async for c in llm.generate_rag_answer_stream("q", CHUNKS, "llama3")]

        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="error") == 1


class TestTokens:
    async def test_tokens_in_and_out_are_counted_per_application_and_model(self, request_scope, telemetry):
        llm = provider(lambda request: completion({"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}))
        telemetry.observe_application(type("Application", (), {"slug": "hr-bot"})())

        await llm.generate_rag_answer("q", CHUNKS, "llama3")
        await llm.generate_rag_answer("q", CHUNKS, "llama3")

        assert telemetry.value("rag.llm.tokens.input", application="hr-bot", model="llama3") == 20
        assert telemetry.value("rag.llm.tokens.output", application="hr-bot", model="llama3") == 8

    async def test_a_backend_that_reports_no_usage_records_no_tokens(self, request_scope, telemetry):
        llm = provider(lambda request: completion(usage=None))

        await llm.generate_rag_answer("q", CHUNKS, "llama3")

        assert telemetry.points("rag.llm.tokens.input") == []
        assert telemetry.points("rag.llm.tokens.output") == []
        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="ok") == 1

    async def test_legacy_paths_count_tokens_against_unknown(self, request_scope, telemetry):
        llm = provider(lambda request: completion({"prompt_tokens": 3, "completion_tokens": 2}))

        await llm.generate_rag_answer("q", CHUNKS, "llama3")

        assert telemetry.value("rag.llm.tokens.input", application="unknown", model="llama3") == 3


def dependencies_called(telemetry):
    return {labels["dependency"] for labels, _ in telemetry.points("rag.dependency.calls")}


class TestOllamaChat:
    @pytest.fixture
    def ollama(self, monkeypatch):
        def serve(handler):
            real = httpx.AsyncClient
            monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
            return OllamaClient(Settings())

        return serve

    async def test_a_generation_is_a_chat_call_and_its_tokens_are_counted(self, request_scope, telemetry, ollama):
        client = ollama(
            lambda request: httpx.Response(200, json={"response": "25 days.", "prompt_eval_count": 12, "eval_count": 5})
        )

        await client.generate_rag_answer("q", CHUNKS, "llama3")

        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="ok") == 1
        assert telemetry.value("rag.llm.tokens.input", model="llama3") == 12
        assert telemetry.value("rag.llm.tokens.output", model="llama3") == 5

    async def test_a_generation_with_no_usage_records_no_tokens(self, request_scope, telemetry, ollama):
        client = ollama(lambda request: httpx.Response(200, json={"response": "25 days."}))

        await client.generate_rag_answer("q", CHUNKS, "llama3")

        assert telemetry.points("rag.llm.tokens.input") == []

    async def test_a_server_error_is_a_chat_error(self, request_scope, telemetry, ollama):
        client = ollama(lambda request: httpx.Response(500))

        with pytest.raises(Exception):
            await client.generate_rag_answer("q", CHUNKS, "llama3")

        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="error") == 1

    async def test_a_stream_is_one_call_with_the_tokens_of_its_last_chunk(self, request_scope, telemetry, ollama):
        lines = [{"response": "25 "}, {"response": "days.", "done": True, "prompt_eval_count": 9, "eval_count": 3}]
        client = ollama(lambda request: httpx.Response(200, content="\n".join(json.dumps(line) for line in lines).encode()))

        assert [c async for c in client.generate_rag_answer_stream("q", CHUNKS, "llama3")]

        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="ok") == 1
        assert telemetry.value("rag.llm.tokens.input", model="llama3") == 9
        assert telemetry.value("rag.llm.tokens.output", model="llama3") == 3


class TestEmbeddingCalls:
    @pytest.mark.parametrize(
        "make, reply",
        [
            (lambda: OllamaEmbeddingProvider("http://embed", "m", 2), {"embeddings": [[0.1, 0.2]]}),
            (
                lambda: OpenAICompatibleEmbeddingProvider("http://embed/v1", "m", 2),
                {"data": [{"index": 0, "embedding": [0.1, 0.2]}]},
            ),
        ],
        ids=["ollama", "openai-compatible"],
    )
    async def test_an_embedding_is_counted_by_outcome(self, request_scope, telemetry, make, reply):
        provider_ = make()
        provider_._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=reply)))
        assert await provider_.embed_query("q") == [0.1, 0.2]

        provider_._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
        with pytest.raises(httpx.HTTPStatusError):
            await provider_.embed_query("q")

        assert telemetry.value("rag.dependency.calls", dependency="embedding", outcome="ok") == 1
        assert telemetry.value("rag.dependency.calls", dependency="embedding", outcome="error") == 1
        assert dependencies_called(telemetry) == {"embedding"}


class TestRerankCalls:
    HITS = [{"chunk_text": "alpha"}, {"chunk_text": "bravo"}]

    async def test_a_rerank_is_counted_by_outcome(self, request_scope, telemetry):
        await reranker_for(FakeEndpoint(ranking=["bravo", "alpha"])).rerank("q", self.HITS)
        with pytest.raises(Exception):
            await reranker_for(FakeEndpoint(raises=httpx.ReadTimeout("slow"))).rerank("q", self.HITS)

        assert telemetry.value("rag.dependency.calls", dependency="rerank", outcome="ok") == 1
        assert telemetry.value("rag.dependency.calls", dependency="rerank", outcome="error") == 1
        assert dependencies_called(telemetry) == {"rerank"}


class TestRedisCalls:
    REQUEST = AskRequest(query="What is the leave policy?")

    async def test_a_cache_call_is_counted_by_outcome_even_though_the_cache_swallows_the_failure(self, request_scope, telemetry):
        redis = MagicMock()
        redis.get.return_value = None
        cache = CacheClient(redis, RedisSettings())

        assert await cache.find_cached_response(self.REQUEST) is None
        redis.get.side_effect = ConnectionError("redis is down")
        assert await cache.find_cached_response(self.REQUEST) is None  # the cache fails open, so the caller sees a miss

        assert telemetry.value("rag.dependency.calls", dependency="redis", outcome="ok") == 1
        assert telemetry.value("rag.dependency.calls", dependency="redis", outcome="error") == 1
        assert dependencies_called(telemetry) == {"redis"}

    async def test_storing_an_answer_is_a_call_too(self, request_scope, telemetry):
        redis = MagicMock()
        redis.set.return_value = True
        cache = CacheClient(redis, RedisSettings())

        await cache.store_response(self.REQUEST, MagicMock(model_dump_json=lambda: "{}"))

        assert telemetry.value("rag.dependency.calls", dependency="redis", outcome="ok") == 1


class TestOpenSearchCalls:
    @pytest.fixture
    def opensearch(self):
        return OpenSearchClient(host="http://opensearch.invalid:9200", settings=Settings())

    async def test_a_call_is_counted_by_outcome(self, request_scope, telemetry, opensearch, monkeypatch):
        from opensearchpy.connection import Urllib3HttpConnection

        replies = [(200, {"content-type": "application/json"}, '{"count": 4}'), ConnectionError("opensearch is down")]

        def perform_request(self, *args, **kwargs):
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        monkeypatch.setattr(Urllib3HttpConnection, "perform_request", perform_request)
        opensearch.client.transport.max_retries = 0

        with telemetry.stage("retrieve"):
            assert opensearch.count_chunks(knowledge_base_id="kb") == 4
            with pytest.raises(Exception):
                opensearch.count_chunks(knowledge_base_id="kb")

        assert telemetry.value("rag.dependency.calls", dependency="opensearch", outcome="ok") == 1
        assert telemetry.value("rag.dependency.calls", dependency="opensearch", outcome="error") == 1
        retrieve = telemetry.span_named("retrieve")
        calls = [s for s in telemetry.spans() if s.name == "dependency.opensearch"]
        assert {s.parent.span_id for s in calls} == {retrieve.context.span_id}


class TestPostgresCalls:
    def test_each_statement_is_a_call_counted_by_outcome_under_the_stage_that_made_it(self, request_scope, telemetry):
        engine = create_engine("sqlite://")
        instrument_engine(engine)

        with telemetry.stage("retrieve"), engine.connect() as connection:
            connection.execute(text("select 1"))
            with pytest.raises(Exception):
                connection.execute(text("select * from no_such_table"))

        assert telemetry.value("rag.dependency.calls", dependency="postgres", outcome="ok") == 1
        assert telemetry.value("rag.dependency.calls", dependency="postgres", outcome="error") == 1
        assert dependencies_called(telemetry) == {"postgres"}
        retrieve = telemetry.span_named("retrieve")
        assert {s.parent.span_id for s in telemetry.spans() if s.name == "dependency.postgres"} == {retrieve.context.span_id}

    def test_a_statement_never_appears_in_telemetry(self, request_scope, telemetry):
        engine = create_engine("sqlite://")
        instrument_engine(engine)

        with engine.connect() as connection:
            connection.execute(text("select 'dana.whitfield@corp.example'"))

        assert "dana" not in " ".join(str(v) for _, _, v in telemetry.labels_and_attributes())


class TestPostgresWiring:
    def test_the_engine_the_database_starts_is_instrumented(self, request_scope, telemetry, monkeypatch):
        import src.db.interfaces.postgresql as postgresql
        from sqlalchemy.pool import StaticPool

        monkeypatch.setattr(
            postgresql,
            "create_engine",
            lambda *a, **kw: create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}),
        )
        database = postgresql.PostgreSQLDatabase(
            MagicMock(database_url="postgresql://rag@db/rag", pool_size=1, max_overflow=0, echo_sql=False)
        )
        database.startup()
        before = telemetry.value("rag.dependency.calls", dependency="postgres")

        with database.get_session() as session:
            session.execute(text("select 1"))

        assert telemetry.value("rag.dependency.calls", dependency="postgres") == before + 1


class TestLangChainChatCalls:
    """The agentic graph's models are LangChain's, so their calls are seen through a callback."""

    async def test_a_call_is_a_chat_call_with_the_tokens_the_model_reported(self, request_scope, telemetry):
        llm = provider(lambda request: completion({"prompt_tokens": 21, "completion_tokens": 6, "total_tokens": 27}))
        telemetry.observe_application(type("Application", (), {"slug": "hr-bot"})())

        with telemetry.stage("guardrail"):
            answer = await llm.get_langchain_model("llama3").ainvoke("Is this in scope?")

        assert answer.content == "25 days."
        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="ok") == 1
        assert telemetry.value("rag.llm.tokens.input", application="hr-bot", model="llama3") == 21
        assert telemetry.value("rag.llm.tokens.output", application="hr-bot", model="llama3") == 6
        assert telemetry.span_named("dependency.chat").parent.span_id == telemetry.span_named("guardrail").context.span_id

    async def test_a_failing_call_is_an_error_and_a_call_with_no_usage_adds_no_tokens(self, request_scope, telemetry):
        replies = [completion(usage=None), httpx.Response(500)]
        llm = provider(lambda request: replies.pop(0))
        model = llm.get_langchain_model("llama3", max_retries=0)

        await model.ainvoke("one")
        with pytest.raises(Exception):
            await model.ainvoke("two")

        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="ok") == 1
        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="error") == 1
        assert telemetry.points("rag.llm.tokens.input") == []

    async def test_the_ollama_model_is_seen_too(self, request_scope, telemetry, monkeypatch):
        reply = {"model": "llama3", "message": {"role": "assistant", "content": "25 days."}, "done": True, "done_reason": "stop"}
        reply |= {"prompt_eval_count": 8, "eval_count": 3}
        real = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kw: real(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=reply)), **kw),
        )

        await OllamaClient(Settings()).get_langchain_model("llama3").ainvoke("Is this in scope?")

        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="ok") == 1
        assert telemetry.value("rag.llm.tokens.input", model="llama3") == 8
        assert telemetry.value("rag.llm.tokens.output", model="llama3") == 3


class TestModelLabels:
    async def test_a_caller_cannot_mint_unbounded_series_by_naming_models(self, request_scope, telemetry):
        llm = provider(lambda request: completion({"prompt_tokens": 1, "completion_tokens": 1}))

        for i in range(80):
            await llm.generate_rag_answer("q", CHUNKS, f"model-{i}")

        models = {labels["model"] for labels, _ in telemetry.points("rag.llm.tokens.input")}
        assert len(models) <= 51 and "other" in models
        assert telemetry.value("rag.llm.tokens.input") == 80


async def test_nothing_is_emitted_outside_a_request():
    """A call made with no request in flight (startup, a script) is not measured, and not an error."""
    llm = provider(lambda request: completion({"prompt_tokens": 1, "completion_tokens": 1}))

    assert (await llm.generate_rag_answer("q", CHUNKS, "m"))["answer"] == "25 days."
