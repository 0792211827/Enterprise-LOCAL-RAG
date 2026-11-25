"""Telemetry of the application answer path, through the real app: the playground and /v1/chat/completions.

The platform is mocked at its edges (index, models) exactly as in the audit tests; the middleware, the shared
answer module, the retrieval seam and the agentic graph all run for real.
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from src.auth import Principal, Role, get_principal
from src.dependencies import get_db_session, get_embeddings_service, get_llm_provider, get_opensearch_client, get_reranker
from src.main import app
from src.services.providers.llm.openai_compatible_provider import OpenAICompatibleLLMProvider

from tests.audit_support import sqlite_session_factory
from tests.unit.services.test_application_agentic import Backend
from tests.unit.services.test_reranker import FakeEndpoint, reranker_for

pytestmark = pytest.mark.skipif(os.environ.get("RAG_TEST_TELEMETRY", "on") == "off", reason="telemetry pinned off for this run")

QUESTION = "How much parental leave does Dana Whitfield get?"
ANSWER = "Twenty-five days of parental leave."
PASSAGES = {"alpha": "Parental leave is 25 days.", "bravo": "Leave must be requested in advance."}


class Index:
    """A fake chunk index holding one passage per Knowledge Base, tagged as the real index tags them."""

    def __init__(self):
        self.passages = {}
        self.search_unified = MagicMock(side_effect=self._search)

    def _search(self, **kwargs):
        wanted = kwargs.get("knowledge_base_ids") or list(self.passages)
        hits = [
            {
                "chunk_id": f"c-{kb}-{i}",
                "chunk_text": text,
                "title": f"Handbook {kb}",
                "document_id": f"d-{kb}",
                "knowledge_base_id": kb,
                "score": 0.9,
            }
            for kb in wanted
            for i, text in enumerate(self.passages.get(kb, []))
        ]
        return {"total": len(hits), "hits": hits[: kwargs.get("size", 10)]}


@pytest.fixture
def index():
    return Index()


@pytest.fixture
def llm():
    mock = AsyncMock()
    mock.generate_rag_answer = AsyncMock(return_value={"answer": ANSWER, "sources": []})

    async def stream(**kwargs):
        for piece in ["Twenty-five ", "days."]:
            yield {"response": piece}

    mock.generate_rag_answer_stream = stream
    return mock


@pytest.fixture
def stack(client, index, llm):
    """The real app over an in-memory SQLite database, with the index and the model faked."""
    factory = sqlite_session_factory()

    def session():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    embeddings = AsyncMock()
    embeddings.embed_query = AsyncMock(return_value=[0.1] * 8)
    app.dependency_overrides[get_db_session] = session
    app.dependency_overrides[get_opensearch_client] = lambda: index
    app.dependency_overrides[get_embeddings_service] = lambda: embeddings
    app.dependency_overrides[get_llm_provider] = lambda: llm
    yield client
    for dependency in (
        get_db_session,
        get_opensearch_client,
        get_embeddings_service,
        get_llm_provider,
        get_reranker,
        get_principal,
    ):
        app.dependency_overrides.pop(dependency, None)


async def make_application(client, index, name="HR Bot", kb_count=1, strategy=None):
    kb_ids = []
    for i in range(kb_count):
        kb_ids.append((await client.post("/api/v1/knowledge-bases", json={"name": f"{name} KB{i}"})).json()["id"])
    body = {"name": name, "knowledge_base_ids": kb_ids, **({"rag_strategy": strategy} if strategy else {})}
    application = (await client.post("/api/v1/applications", json=body)).json()
    for kb, text in zip(kb_ids, PASSAGES.values()):
        index.passages[kb] = [text]
    return application, kb_ids


async def ask(client, application, stream=False):
    body = {"model": application["slug"], "messages": [{"role": "user", "content": QUESTION}], "stream": stream}
    return await client.post("/v1/chat/completions", json=body)


async def play(client, application):
    return await client.post(f"/api/v1/applications/{application['id']}/ask", json={"query": QUESTION})


ASK_ROUTE = "/api/v1/applications/{application_id}/ask"
COMPLETIONS_ROUTE = "/v1/chat/completions"


class TestRequestMetricsPerApplication:
    async def test_both_front_doors_label_requests_with_the_application(self, stack, index, telemetry):
        hr, _ = await make_application(stack, index, "HR Bot")

        assert (await play(stack, hr)).status_code == 200
        assert (await ask(stack, hr)).status_code == 200

        for route in (ASK_ROUTE, COMPLETIONS_ROUTE):
            labels = {"route": route, "application": hr["slug"], "status": "200"}
            assert telemetry.value("rag.http.requests", **labels) == 1
            assert telemetry.value("rag.http.request.duration", **labels) == 1

    async def test_a_failing_application_is_counted_as_errors_for_that_application(self, stack, index, llm, telemetry):
        hr, _ = await make_application(stack, index, "HR Bot")
        legal, _ = await make_application(stack, index, "Legal Bot")
        llm.generate_rag_answer.side_effect = [RuntimeError("model crashed"), {"answer": ANSWER}]

        with pytest.raises(RuntimeError):
            await play(stack, hr)
        await play(stack, legal)

        assert telemetry.value("rag.http.request.errors", application=hr["slug"]) == 1
        assert telemetry.value("rag.http.request.errors", application=legal["slug"]) == 0

    async def test_renaming_an_application_keeps_one_series(self, stack, index, telemetry):
        hr, _ = await make_application(stack, index, "HR Bot")
        await play(stack, hr)

        await stack.patch(f"/api/v1/applications/{hr['id']}", json={"name": "People Assistant"})
        await play(stack, hr)

        series = [labels for labels, _ in telemetry.points("rag.http.requests") if labels["route"] == ASK_ROUTE]
        assert [s["application"] for s in series] == [hr["slug"]]
        assert telemetry.value("rag.http.requests", route=ASK_ROUTE) == 2

    async def test_an_unknown_application_is_labelled_unknown_whatever_the_caller_typed(self, stack, telemetry):
        await stack.post(f"/api/v1/applications/{'0' * 8}-0000-0000-0000-{'0' * 12}/ask", json={"query": QUESTION})
        await stack.post(COMPLETIONS_ROUTE, json={"model": "made-up-by-a-client", "messages": [{"role": "user", "content": "q"}]})

        applications = {labels.get("application") for labels, _ in telemetry.points("rag.http.requests")}
        assert applications == {"unknown"}
        assert "made-up-by-a-client" not in {v for _, _, v in telemetry.labels_and_attributes()}


class TestStageSpans:
    def children_of(self, telemetry, parent):
        return {s.name for s in telemetry.spans() if s.parent and s.parent.span_id == parent.context.span_id}

    def trace_of(self, telemetry, root):
        return [s for s in telemetry.spans() if s.context.trace_id == root.context.trace_id]

    async def test_a_traditional_answer_has_a_span_per_stage_under_the_request(self, stack, index, telemetry):
        app.dependency_overrides[get_reranker] = lambda: reranker_for(FakeEndpoint(ranking=list(PASSAGES.values())), candidates=5)
        hr, _ = await make_application(stack, index, kb_count=1)
        index.search_unified.side_effect = lambda **kw: {
            "hits": [
                {"chunk_text": t, "title": t, "document_id": t, "knowledge_base_id": kw["knowledge_base_ids"][0]}
                for t in PASSAGES.values()
            ]
        }

        await play(stack, hr)

        root = telemetry.span_named(f"POST {ASK_ROUTE}")
        retrieve = telemetry.span_named("retrieve")
        assert self.children_of(telemetry, root) == {"retrieve", "generate_answer"}
        assert telemetry.span_named("rerank").parent.span_id == retrieve.context.span_id
        assert {s.name for s in self.trace_of(telemetry, root)} == {f"POST {ASK_ROUTE}", "retrieve", "rerank", "generate_answer"}

    async def test_no_rerank_span_when_no_reranker_ran(self, stack, index, telemetry):
        hr, _ = await make_application(stack, index)

        await play(stack, hr)

        root = telemetry.span_named(f"POST {ASK_ROUTE}")
        assert {s.name for s in self.trace_of(telemetry, root)} == {root.name, "retrieve", "generate_answer"}

    async def test_an_agentic_answer_has_a_span_per_stage_that_ran(self, stack, index, telemetry):
        class GradesTwice(Backend):
            """Rejects the first retrieval so the graph rewrites the query and retrieves again."""

            def __call__(self, request):
                body = json.loads(request.content)
                if ((body.get("response_format") or {}).get("json_schema") or {}).get("name") == "GradeDocuments":
                    self.grades = getattr(self, "grades", 0) + 1
                    if self.grades == 1:
                        return httpx.Response(
                            200,
                            json={
                                "id": "x",
                                "object": "chat.completion",
                                "created": 1,
                                "model": body["model"],
                                "choices": [
                                    {
                                        "index": 0,
                                        "finish_reason": "stop",
                                        "message": {
                                            "role": "assistant",
                                            "content": json.dumps({"binary_score": "no", "reasoning": "no"}),
                                        },
                                    }
                                ],
                            },
                        )
                return super().__call__(request)

        provider = OpenAICompatibleLLMProvider("http://backend/v1", "m", transport=httpx.MockTransport(GradesTwice()))
        app.dependency_overrides[get_llm_provider] = lambda: provider
        app.dependency_overrides[get_reranker] = lambda: reranker_for(FakeEndpoint(ranking=list(PASSAGES.values())), candidates=5)
        hr, _ = await make_application(stack, index, strategy="agentic")
        index.search_unified.side_effect = lambda **kw: {
            "hits": [
                {"chunk_text": t, "title": t, "document_id": t, "knowledge_base_id": kw["knowledge_base_ids"][0]}
                for t in PASSAGES.values()
            ]
        }

        response = await play(stack, hr)

        assert response.status_code == 200, response.text
        root = telemetry.span_named(f"POST {ASK_ROUTE}")
        names = [s.name for s in self.trace_of(telemetry, root)]
        assert {"guardrail", "retrieve", "rerank", "rewrite_query", "generate_answer"} <= set(names)
        assert names.count("retrieve") == 2  # once before the rewrite, once after
        assert {"guardrail", "retrieve", "rewrite_query", "generate_answer"} <= self.children_of(telemetry, root)


class TestRetrievalPerKnowledgeBase:
    async def test_two_knowledge_bases_count_once_as_a_request_and_twice_as_retrieval(self, stack, index, telemetry):
        hr, (kb_a, kb_b) = await make_application(stack, index, kb_count=2)
        index.passages[kb_b] = []  # the second Knowledge Base has nothing for this question

        await play(stack, hr)

        assert telemetry.value("rag.http.requests", route=ASK_ROUTE) == 1
        assert telemetry.value("rag.retrieval.searches") == 2
        assert telemetry.value("rag.retrieval.searches", knowledge_base=kb_a) == 1
        assert telemetry.value("rag.retrieval.chunks_returned", knowledge_base=kb_a) == 1
        assert telemetry.value("rag.retrieval.chunks_returned", knowledge_base=kb_b) == 0
        assert telemetry.value("rag.retrieval.empty_results") == 1
        assert telemetry.value("rag.retrieval.empty_results", knowledge_base=kb_b) == 1

    async def test_a_reranker_that_failed_is_counted_per_knowledge_base_searched(self, stack, index, telemetry):
        app.dependency_overrides[get_reranker] = lambda: reranker_for(FakeEndpoint(raises=httpx.ReadTimeout("slow")))
        hr, (kb_a, kb_b) = await make_application(stack, index, kb_count=2)
        index.passages[kb_a] = list(PASSAGES.values())  # more than one hit, so there is something to rerank

        assert (await play(stack, hr)).status_code == 200

        assert telemetry.value("rag.retrieval.rerank_skipped", knowledge_base=kb_a) == 1
        assert telemetry.value("rag.retrieval.rerank_skipped", knowledge_base=kb_b) == 1
        assert telemetry.value("rag.retrieval.rerank_skipped") == 2


class TestGuardrail:
    async def test_an_out_of_scope_refusal_is_counted_for_the_application(self, stack, index, telemetry):
        provider = OpenAICompatibleLLMProvider(
            "http://backend/v1", "m", transport=httpx.MockTransport(Backend(guardrail_score=5))
        )
        app.dependency_overrides[get_llm_provider] = lambda: provider
        hr, _ = await make_application(stack, index, strategy="agentic")

        await play(stack, hr)
        await play(stack, hr)

        assert telemetry.value("rag.guardrail.out_of_scope", application=hr["slug"]) == 2
        assert telemetry.value("rag.retrieval.searches") == 0


class TestTimeToFirstToken:
    async def test_a_streamed_answer_records_when_its_first_token_came(self, stack, index, telemetry):
        hr, _ = await make_application(stack, index)

        response = await ask(stack, hr, stream=True)

        assert response.text.rstrip().endswith("data: [DONE]")
        assert telemetry.value("rag.answer.time_to_first_token", application=hr["slug"]) == 1

    async def test_an_answer_that_is_not_streamed_records_none(self, stack, index, telemetry):
        hr, _ = await make_application(stack, index)

        await ask(stack, hr)

        assert telemetry.value("rag.answer.time_to_first_token") == 0


class TestAnswersAreTheSameWithTelemetryOnAndOff:
    async def test_the_same_question_gets_the_same_answer(self, stack, index, telemetry):
        from src.services.telemetry import NOOP_TELEMETRY

        hr, _ = await make_application(stack, index)
        outcomes = []
        for state in (NOOP_TELEMETRY, telemetry):
            app.state.telemetry = state
            plain = await play(stack, hr)
            completion = (await ask(stack, hr)).json()
            streamed = (await ask(stack, hr, stream=True)).text
            frames = [f[len("data: ") :] for f in streamed.splitlines() if f.startswith("data: ") and "[DONE]" not in f]
            deltas = [json.loads(f)["choices"][0]["delta"] for f in frames]
            outcomes.append((plain.status_code, plain.json(), completion["choices"], deltas))

        assert outcomes[0] == outcomes[1]
        assert telemetry.value("rag.http.requests", route=ASK_ROUTE) == 1  # only the "on" round was measured


class TestPrivacy:
    """No question, answer, Document text, or End user subject, group or role in anything telemetry emits."""

    SUBJECT, GROUP, ROLE = "dana.whitfield@corp.example", "group-finance-confidential", "role-payroll-supervisor"

    async def test_nothing_confidential_is_in_any_label_attribute_or_log_record(self, stack, index, llm, telemetry, caplog):
        app.dependency_overrides[get_reranker] = lambda: reranker_for(FakeEndpoint(ranking=list(PASSAGES.values())), candidates=5)
        hr, _ = await make_application(stack, index, kb_count=2)
        agentic, _ = await make_application(stack, index, "Agent Bot", strategy="agentic")
        llm.generate_rag_answer.side_effect = [{"answer": ANSWER}, RuntimeError(f"model crashed on: {QUESTION}")]
        app.dependency_overrides[get_principal] = lambda: Principal(
            subject=self.SUBJECT,
            roles=frozenset(Role),
            groups=frozenset({self.GROUP}),
            raw_roles=frozenset({self.ROLE}),
            auth_method="jwt",
        )

        with caplog.at_level("DEBUG"):
            await play(stack, hr)
            with pytest.raises(RuntimeError):
                await play(stack, hr)  # an exception whose message holds the question
            await ask(stack, hr, stream=True)
            await ask(stack, agentic)

        emitted = [str(v) for _, _, v in telemetry.labels_and_attributes()]
        emitted += [r.getMessage() for r in caplog.records if r.name.startswith("src.services.telemetry")]
        assert emitted, "the representative requests produced no telemetry to inspect"
        secrets = [QUESTION, ANSWER, *PASSAGES.values(), self.SUBJECT, self.GROUP, self.ROLE, "Dana", "Whitfield", "parental"]
        leaks = [(secret, value) for value in emitted for secret in secrets if secret.lower() in value.lower()]
        assert leaks == []
        assert not any(s.status.description for s in telemetry.spans()), "an error span carries the exception text"
        assert not any(s.events for s in telemetry.spans()), "a span recorded an exception, whose message may hold a question"
