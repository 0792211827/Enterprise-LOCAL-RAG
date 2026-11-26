"""Telemetry of the legacy answer routes and the agentic route, through the real app (ticket 03).

The same fakes as the application-path tests: the index and the model are faked, the middleware, the retrieval seam,
the routers and the agentic graph run for real.
"""

import json
import os
from unittest.mock import AsyncMock

import httpx
import pytest
from src.auth import Principal, Role, get_principal
from src.dependencies import get_llm_provider, get_reranker
from src.main import app
from src.services.providers.llm.openai_compatible_provider import OpenAICompatibleLLMProvider

from tests.api.test_telemetry_application_path import (  # noqa: F401  (fixtures)
    ANSWER,
    PASSAGES,
    QUESTION,
    GradesTwice,
    index,
    llm,
    stack,
)
from tests.unit.services.test_application_agentic import Backend
from tests.unit.services.test_reranker import FakeEndpoint, reranker_for

pytestmark = pytest.mark.skipif(os.environ.get("RAG_TEST_TELEMETRY", "on") == "off", reason="telemetry pinned off for this run")

ASK, STREAM, SEARCH, AGENTIC = "/api/v1/ask", "/api/v1/stream", "/api/v1/hybrid-search/", "/api/v1/ask-agentic"
LEGACY = {ASK: {"query": QUESTION}, STREAM: {"query": QUESTION}, SEARCH: {"query": QUESTION}}


@pytest.fixture(autouse=True)
def passages(index):
    index.passages["kb"] = list(PASSAGES.values())


def children_of(telemetry, parent):
    return {
        s.name
        for s in telemetry.spans()
        if s.parent and s.parent.span_id == parent.context.span_id and not (s.attributes or {}).get("dependency")
    }


def stage_names(telemetry, root):
    """The stages of one request's trace (a dependency call is not a stage)."""
    stages = [
        s
        for s in telemetry.spans()
        if s.context.trace_id == root.context.trace_id and not (s.attributes or {}).get("dependency") and s is not root
    ]
    return [s.name for s in sorted(stages, key=lambda s: s.start_time)]


class TestRequestMetrics:
    @pytest.mark.parametrize("route", [ASK, STREAM, SEARCH])
    async def test_each_route_is_measured_like_the_application_path_with_an_unknown_application(self, stack, telemetry, route):
        response = await stack.post(route, json=LEGACY[route])

        assert response.status_code == 200
        labels = {"route": route, "method": "POST", "status": "200", "application": "unknown"}
        assert telemetry.value("rag.http.requests", **labels) == 1
        assert telemetry.value("rag.http.request.duration", **labels) == 1
        assert telemetry.value("rag.http.request.errors") == 0
        assert {labels["application"] for labels, _ in telemetry.points("rag.http.requests")} == {"unknown"}

    async def test_the_agentic_route_is_measured_too(self, stack, telemetry):
        provider = OpenAICompatibleLLMProvider("http://backend/v1", "m", transport=httpx.MockTransport(Backend()))
        app.dependency_overrides[get_llm_provider] = lambda: provider

        assert (await stack.post(AGENTIC, json={"query": QUESTION})).status_code == 200

        labels = {"route": AGENTIC, "application": "unknown", "status": "200"}
        assert telemetry.value("rag.http.requests", **labels) == 1


class TestStageSpans:
    async def test_ask_has_a_span_per_stage_under_the_request(self, stack, telemetry):
        app.dependency_overrides[get_reranker] = lambda: reranker_for(FakeEndpoint(ranking=list(PASSAGES.values())), candidates=5)

        await stack.post(ASK, json=LEGACY[ASK])

        root = telemetry.span_named(f"POST {ASK}")
        assert children_of(telemetry, root) >= {"retrieve", "generate_answer"}
        assert sorted(stage_names(telemetry, root)) == ["generate_answer", "rerank", "retrieve"]

    async def test_stream_has_a_span_per_stage_and_records_the_first_token(self, stack, telemetry):
        response = await stack.post(STREAM, json=LEGACY[STREAM])

        assert "Twenty-five" in response.text
        root = telemetry.span_named(f"POST {STREAM}")
        assert sorted(stage_names(telemetry, root)) == ["generate_answer", "retrieve"]
        assert telemetry.value("rag.answer.time_to_first_token", application="unknown") == 1

    async def test_a_stream_that_produces_no_token_records_no_first_token(self, stack, index, telemetry):
        index.passages.clear()  # nothing found, so nothing is generated

        await stack.post(STREAM, json=LEGACY[STREAM])

        assert telemetry.value("rag.answer.time_to_first_token") == 0

    async def test_hybrid_search_has_a_retrieve_span_and_no_answer_stage(self, stack, telemetry):
        await stack.post(SEARCH, json=LEGACY[SEARCH])

        root = telemetry.span_named(f"POST {SEARCH}")
        assert stage_names(telemetry, root) == ["retrieve"]
        assert telemetry.value("rag.retrieval.searches") == 1


class TestFailures:
    async def test_a_failing_index_is_an_error_in_the_metric_and_on_the_stage_span(self, stack, index, telemetry):
        index.search_unified.side_effect = ConnectionError("opensearch is down")

        for route in (ASK, SEARCH):
            await stack.post(route, json=LEGACY[route])

            labels = {"route": route, "status": "500"}
            assert telemetry.value("rag.http.request.errors", **labels) == 1
            assert telemetry.span_named(f"POST {route}").status.status_code.name == "ERROR"
        assert {s.status.status_code.name for s in telemetry.spans() if s.name == "retrieve"} == {"ERROR"}

    async def test_a_model_that_fails_is_an_error_on_generate_answer(self, stack, llm, telemetry):
        llm.generate_rag_answer.side_effect = RuntimeError("model crashed")

        await stack.post(ASK, json=LEGACY[ASK])

        assert telemetry.span_named("generate_answer").status.status_code.name == "ERROR"
        assert telemetry.span_named("retrieve").status.status_code.name == "UNSET"
        assert telemetry.value("rag.http.request.errors", route=ASK) == 1

    async def test_a_stream_that_breaks_after_it_began_still_counts_as_a_request_error(self, stack, llm, telemetry):
        async def broken(**kwargs):
            yield {"response": "Twenty-five "}
            raise RuntimeError("model crashed")

        llm.generate_rag_answer_stream = broken

        response = await stack.post(STREAM, json=LEGACY[STREAM])

        assert response.status_code == 200  # too late to say otherwise: the stream had begun
        assert telemetry.value("rag.http.request.errors", route=STREAM, status="200") == 1
        assert telemetry.span_named(f"POST {STREAM}").status.status_code.name == "ERROR"
        assert telemetry.span_named("generate_answer").status.status_code.name == "ERROR"


class TestAgenticNodeSpans:
    @pytest.fixture(autouse=True)
    def reranked(self):
        app.dependency_overrides[get_reranker] = lambda: reranker_for(FakeEndpoint(ranking=list(PASSAGES.values())), candidates=5)

    def use(self, backend):
        provider = OpenAICompatibleLLMProvider("http://backend/v1", "m", transport=httpx.MockTransport(backend))
        app.dependency_overrides[get_llm_provider] = lambda: provider

    async def test_one_span_per_node_that_ran_under_the_request(self, stack, telemetry):
        self.use(Backend())

        response = await stack.post(AGENTIC, json={"query": QUESTION})

        assert response.status_code == 200, response.text
        root = telemetry.span_named(f"POST {AGENTIC}")
        assert stage_names(telemetry, root) == ["guardrail", "retrieve", "rerank", "grade_documents", "generate_answer"]
        assert children_of(telemetry, root) == {"guardrail", "retrieve", "grade_documents", "generate_answer"}
        assert telemetry.span_named("rerank").parent.span_id == telemetry.span_named("retrieve").context.span_id

    async def test_the_rewrite_and_retrieve_loop_gives_a_span_per_pass(self, stack, telemetry):
        self.use(GradesTwice())

        assert (await stack.post(AGENTIC, json={"query": QUESTION})).status_code == 200

        root = telemetry.span_named(f"POST {AGENTIC}")
        names = [n for n in stage_names(telemetry, root) if n != "rerank"]
        assert names == [
            "guardrail",
            "retrieve",
            "grade_documents",
            "rewrite_query",
            "retrieve",
            "grade_documents",
            "generate_answer",
        ]
        assert all(s.parent.span_id == root.context.span_id for s in telemetry.spans() if s.name in names)

    async def test_a_refusal_is_an_out_of_scope_span_and_nothing_after_it(self, stack, telemetry):
        self.use(Backend(guardrail_score=5))

        await stack.post(AGENTIC, json={"query": QUESTION})

        root = telemetry.span_named(f"POST {AGENTIC}")
        assert stage_names(telemetry, root) == ["guardrail", "out_of_scope"]
        assert children_of(telemetry, root) == {"guardrail", "out_of_scope"}

    async def test_the_model_calls_are_chat_calls_in_the_request_trace(self, stack, telemetry):
        self.use(Backend())

        await stack.post(AGENTIC, json={"query": QUESTION})

        root = telemetry.span_named(f"POST {AGENTIC}")
        stages = {s.context.span_id for s in telemetry.spans() if s.name in {"guardrail", "grade_documents", "generate_answer"}}
        calls = [s for s in telemetry.spans() if s.name == "dependency.chat"]
        assert len(calls) == 3 and all(s.parent.span_id in stages | {root.context.span_id} for s in calls)
        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="ok") == 3

    async def test_a_model_that_fails_to_answer_is_an_error_for_chat_and_on_its_call_span(self, stack, telemetry):
        class FailsToAnswer(Backend):
            def __call__(self, request):
                if "response_format" not in json.loads(request.content):
                    return httpx.Response(500)
                return super().__call__(request)

        self.use(FailsToAnswer())

        response = await stack.post(AGENTIC, json={"query": QUESTION})

        # The node degrades to an apology rather than failing the request, as it always has; the failure is the model's.
        assert response.status_code == 200
        assert telemetry.value("rag.dependency.calls", dependency="chat", outcome="error") == 1
        errors = {
            labels["dependency"] for labels, count in telemetry.points("rag.dependency.calls") if labels["outcome"] == "error"
        }
        assert errors == {"chat"}
        failed = [s for s in telemetry.spans() if s.name == "dependency.chat" and s.status.status_code.name == "ERROR"]
        assert len(failed) == 1


class TestDependencyCallsOnTheRoutes:
    async def test_postgres_calls_are_children_of_the_request_and_nothing_else_is_blamed(self, stack, index, telemetry):
        from tests.api.test_telemetry_application_path import make_application, play

        hr, _ = await make_application(stack, index)
        before = telemetry.value("rag.dependency.calls", dependency="postgres")

        await play(stack, hr)

        assert telemetry.value("rag.dependency.calls", dependency="postgres") > before
        root = telemetry.span_named(f"POST /api/v1/applications/{{application_id}}/ask")
        stage_ids = {s.context.span_id for s in telemetry.spans() if s.name in {"retrieve", "generate_answer"}}
        postgres = [
            s for s in telemetry.spans() if s.name == "dependency.postgres" and s.context.trace_id == root.context.trace_id
        ]
        assert postgres and all(s.parent.span_id in stage_ids | {root.context.span_id} for s in postgres)
        assert telemetry.value("rag.dependency.calls", outcome="error") == 0

    async def test_a_reranker_that_is_down_is_an_error_for_rerank_alone(self, stack, telemetry):
        app.dependency_overrides[get_reranker] = lambda: reranker_for(FakeEndpoint(raises=httpx.ReadTimeout("slow")))

        response = await stack.post(ASK, json=LEGACY[ASK])

        assert response.status_code == 200  # the answer still comes, in the original order
        errors = {labels["dependency"] for labels, _ in telemetry.points("rag.dependency.calls") if labels["outcome"] == "error"}
        assert errors == {"rerank"}
        failed = [s for s in telemetry.spans() if s.name == "dependency.rerank"]
        assert [s.status.status_code.name for s in failed] == ["ERROR"]
        assert failed[0].parent.span_id == telemetry.span_named("rerank").context.span_id


class TestLegacyAnswersAreTheSameWithTelemetryOnAndOff:
    async def test_every_route_answers_alike(self, stack, telemetry):
        from src.services.telemetry import NOOP_TELEMETRY

        app.dependency_overrides[get_reranker] = lambda: reranker_for(FakeEndpoint(ranking=list(PASSAGES.values())), candidates=5)
        provider = OpenAICompatibleLLMProvider("http://backend/v1", "m", transport=httpx.MockTransport(Backend()))
        outcomes = []
        for state in (NOOP_TELEMETRY, telemetry):
            app.state.telemetry = state
            responses = [await stack.post(route, json=body) for route, body in LEGACY.items()]
            kept = app.dependency_overrides[get_llm_provider]
            app.dependency_overrides[get_llm_provider] = lambda: provider
            responses.append(await stack.post(AGENTIC, json={"query": QUESTION}))
            app.dependency_overrides[get_llm_provider] = kept
            outcomes.append([(r.status_code, r.text) for r in responses])

        assert outcomes[0] == outcomes[1]
        assert telemetry.value("rag.http.requests") == 4  # only the "on" round was measured


class TestPrivacyOnTheseRoutes:
    SUBJECT, GROUP, ROLE = "dana.whitfield@corp.example", "group-finance-confidential", "role-payroll-supervisor"

    async def test_nothing_confidential_is_in_any_label_attribute_or_log_record(self, stack, llm, telemetry, caplog):
        app.dependency_overrides[get_reranker] = lambda: reranker_for(FakeEndpoint(ranking=list(PASSAGES.values())), candidates=5)
        app.dependency_overrides[get_principal] = lambda: Principal(
            subject=self.SUBJECT,
            roles=frozenset(Role),
            groups=frozenset({self.GROUP}),
            raw_roles=frozenset({self.ROLE}),
            auth_method="jwt",
        )
        provider = OpenAICompatibleLLMProvider("http://backend/v1", "m", transport=httpx.MockTransport(Backend()))

        with caplog.at_level("DEBUG"):
            for route, body in LEGACY.items():
                await stack.post(route, json=body)
            llm.generate_rag_answer.side_effect = RuntimeError(f"model crashed on: {QUESTION}")
            await stack.post(ASK, json=LEGACY[ASK])
            app.dependency_overrides[get_llm_provider] = lambda: provider
            await stack.post(AGENTIC, json={"query": QUESTION})

        emitted = [str(v) for _, _, v in telemetry.labels_and_attributes()]
        emitted += [r.getMessage() for r in caplog.records if r.name.startswith("src.services.telemetry")]
        assert {"dependency.chat", "dependency.rerank", "guardrail", "grade_documents"} <= set(emitted)
        secrets = [
            QUESTION,
            ANSWER,
            "Fourteen days",
            *PASSAGES.values(),
            self.SUBJECT,
            self.GROUP,
            self.ROLE,
            "Dana",
            "Whitfield",
            "parental",
        ]
        leaks = [(secret, value) for value in emitted for secret in secrets if secret.lower() in value.lower()]
        assert leaks == []
        assert not any(s.status.description for s in telemetry.spans()), "an error span carries the exception text"
        assert not any(s.events for s in telemetry.spans()), "a span recorded an exception, whose message may hold a question"
