"""Document restrictions (ADR-0001): an admin narrows one Document to chosen Principals, and from then on only callers who
hold the Knowledge Base Grant *and* match a restriction can see it -- in answers, citations and search, on every path.

The real OpenSearchClient runs over an index fake that honours the filters it is sent (both the portable and the lucene
kNN form), so a path that forgot the restriction filter shows up as a leaked passage. Fixtures: tests/chat_support.py.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends
from src.auth import Role, require_role
from src.config import AuthSettings, Settings
from src.dependencies import get_agentic_rag_service, get_langfuse_tracer, get_opensearch_client, get_retrieval_scope
from src.models import AuditEvent
from src.models.document import Document
from src.repositories import RestrictionRepository
from src.routers import agentic_ask, applications, documents, hybrid_search
from src.routers.ask import ask_router, stream_router
from src.services.access import resolve_scope
from src.services.applications import answer_agentic
from src.services.opensearch.client import KNN_ENGINE, OpenSearchClient
from tests.chat_support import ask, caller, frames, stack, start_conversation, world  # noqa: F401
from tests.opensearch_fakes import FilteringIndex

OPEN_TEXT = "Annual leave is 25 days. OPENMARKER"
SECRET_TEXT = "Annual leave for the leadership team is 40 days. SECRETMARKER"
ADMIN = caller("root", roles=(Role.ADMIN, Role.QUERIER))
ALICE = caller("alice", "staff")  # holds the hr Grant
LEAD = caller("lena", "staff", "hr-leads")  # holds the Grant and matches the restriction
OUTSIDER = caller("carol", "hr-leads")  # matches the restriction but holds no Grant on hr


@pytest.fixture(params=["portable", "lucene"])
def engine(request):
    return request.param


@pytest.fixture
def index(stack, world, engine):
    """The real client over a filtering fake that holds one open and one (soon to be restricted) hr Document."""
    fake = FilteringIndex()
    client = OpenSearchClient.__new__(OpenSearchClient)
    client.client, client.index_name, client.settings = fake, "chunks", None
    if engine == "lucene":
        client._engine, client._engine_read_at = KNN_ENGINE, time.monotonic()

    with world.factory() as session:
        documents_by_name = {}
        for name, text in (("open", OPEN_TEXT), ("secret", SECRET_TEXT)):
            document = Document(
                knowledge_base_id=world.kbs["hr"], title=f"{name} policy", raw_text=text, status="COMPLETED", chunk_count=1
            )
            session.add(document)
            session.commit()
            documents_by_name[name] = str(document.id)
            fake.add(
                {
                    "chunk_text": text,
                    "title": f"{name} policy",
                    "document_id": str(document.id),
                    "knowledge_base_id": world.kbs["hr"],
                    "chunk_index": 0,
                    "embedding": [0.1],
                }
            )
        # Another Knowledge Base's Document, restricted to a group that is also named below: restrictions never widen.
        fin = Document(knowledge_base_id=world.kbs["fin"], title="fin policy", raw_text="FINMARKER", status="COMPLETED")
        session.add(fin)
        session.commit()
        fake.add(
            {
                "chunk_text": "FINMARKER",
                "title": "fin policy",
                "document_id": str(fin.id),
                "knowledge_base_id": world.kbs["fin"],
                "chunk_index": 0,
                "embedding": [0.1],
            }
        )

    app = stack.app
    app.dependency_overrides[get_opensearch_client] = lambda: client
    app.state.ollama_client = stack.llm
    app.dependency_overrides[get_langfuse_tracer] = lambda: MagicMock()  # the legacy routes trace through Langfuse
    stack.llm.generate_rag_answer = AsyncMock(return_value={"answer": "An answer.", "sources": [], "confidence": "low", "citations": []})
    querier, admin = [Depends(require_role(Role.QUERIER))], [Depends(require_role(Role.ADMIN))]
    app.include_router(hybrid_search.router, prefix="/api/v1", dependencies=querier)
    app.include_router(ask_router, prefix="/api/v1", dependencies=querier)
    app.include_router(stream_router, prefix="/api/v1", dependencies=querier)
    app.include_router(documents.router, dependencies=admin)
    app.include_router(applications.router, dependencies=admin)
    stack.use_settings(Settings(auth=AuthSettings(mode="jwt")))
    stack.docs = documents_by_name
    stack.fake, stack.client = fake, client
    return fake


def restrict(stack, document_id, *entries):
    """Restrict a Document as an admin, then go back to being whoever was calling."""
    stack.act_as(ADMIN)
    response = stack.http.put(
        f"/api/v1/documents/{document_id}/restrictions",
        json={"restrictions": [{"principal_type": t, "principal_value": v} for t, v in entries]},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def restricted(stack, world, index):
    """The secret Document is restricted to the hr-leads group (and nobody has changed identity yet)."""
    restrict(stack, stack.docs["secret"], ("group", "hr-leads"))
    stack.act_as(ALICE)
    return stack


def leaked(response) -> bool:
    assert response.status_code == 200, response.text
    return "SECRETMARKER" in response.text or "secret policy" in response.text


def served(response) -> bool:
    return "OPENMARKER" in response.text or "open policy" in response.text


# --- every retrieval path hides the restricted Document ------------------------------------------------------------ #
class TestNoLeakage:
    LEGACY = [
        ("/api/v1/ask", {"query": "annual leave days"}),
        ("/api/v1/ask", {"query": "annual leave days", "use_hybrid": False}),
        ("/api/v1/stream", {"query": "annual leave days"}),
        ("/api/v1/hybrid-search/", {"query": "annual leave days", "use_hybrid": True}),
        ("/api/v1/hybrid-search/", {"query": "annual leave days", "use_hybrid": False}),
    ]

    @pytest.mark.parametrize("path, body", LEGACY)
    def test_legacy_routes_do_not_return_it_to_a_caller_who_does_not_match(self, restricted, path, body):
        response = restricted.http.post(path, json=body)

        assert served(response), "the unrestricted Document must still be found"
        assert not leaked(response)

    @pytest.mark.parametrize("path, body", LEGACY)
    def test_legacy_routes_return_it_to_a_caller_who_matches(self, restricted, path, body):
        restricted.act_as(LEAD)

        assert leaked(restricted.http.post(path, json=body)) and served(restricted.http.post(path, json=body))

    def test_the_application_playground_does_not_cite_it(self, restricted, world):
        # The playground answers as the admin, whose own groups decide what is retrievable.
        restricted.act_as(caller("root", "staff", roles=(Role.ADMIN, Role.QUERIER)))

        response = restricted.http.post(f"/api/v1/applications/{world.apps['Handbook']}/ask", json={"query": "annual leave days"})

        assert served(response) and not leaked(response)
        assert "SECRETMARKER" not in str(restricted.llm.generate_rag_answer.call_args)

    def test_an_employee_chat_answer_does_not_cite_or_prompt_with_it(self, restricted, world):
        conversation = start_conversation(restricted, world)["id"]

        sent = ask(restricted, conversation, "annual leave days")

        text = str(sent)
        assert "OPENMARKER" in text and "SECRETMARKER" not in text and "secret policy" not in text
        assert "SECRETMARKER" not in str(restricted.llm.stream_calls[-1])

    def test_an_employee_chat_answer_cites_it_for_a_caller_who_matches(self, restricted, world):
        restricted.act_as(LEAD)
        conversation = start_conversation(restricted, world)["id"]

        assert "SECRETMARKER" in str(ask(restricted, conversation, "annual leave days"))

    def test_a_citation_cannot_be_opened_by_a_caller_who_does_not_match(self, restricted):
        url = "/api/v1/chat/documents/"

        assert restricted.http.get(url + restricted.docs["secret"]).status_code == 404
        assert restricted.http.get(url + restricted.docs["open"]).status_code == 200
        restricted.act_as(LEAD)
        assert restricted.http.get(url + restricted.docs["secret"]).status_code == 200

    def test_the_agentic_graph_retriever_is_filtered_by_the_dependency_wiring(self, restricted, world, monkeypatch):
        tools = []
        import src.services.agents.agentic_rag as agentic_rag

        real = agentic_rag.create_retriever_tool
        monkeypatch.setattr(agentic_rag, "create_retriever_tool", lambda **kw: tools.append(real(**kw)) or tools[-1])
        with world.factory() as session:
            scope = resolve_scope(ALICE, session, Settings(auth=AuthSettings(mode="jwt")))

        get_agentic_rag_service(restricted.client, MagicMock(), restricted.embeddings, MagicMock(), Settings(), scope)

        import asyncio

        found = asyncio.run(tools[0].ainvoke({"query": "annual leave days"}))
        assert [d.metadata["title"] for d in found] == ["open policy"]

    def test_the_application_agentic_path_is_filtered_too(self, restricted, world, monkeypatch):
        tools = []
        import src.services.agents.agentic_rag as agentic_rag

        real = agentic_rag.create_retriever_tool
        monkeypatch.setattr(agentic_rag, "create_retriever_tool", lambda **kw: tools.append(real(**kw)) or tools[-1])
        monkeypatch.setattr(agentic_rag.AgenticRAGService, "ask", AsyncMock(return_value={"answer": "a"}))
        import asyncio

        from src.repositories import ApplicationRepository

        with world.factory() as session:
            scope = resolve_scope(ALICE, session, Settings(auth=AuthSettings(mode="jwt")))
            application = ApplicationRepository(session).get_by_id(world.apps["Handbook"])
            asyncio.run(answer_agentic(application, "q", restricted.client, restricted.embeddings, MagicMock(), scope=scope))

        found = asyncio.run(tools[0].ainvoke({"query": "annual leave days"}))
        assert [d.metadata["title"] for d in found] == ["open policy"]

    def test_a_restriction_never_widens_access_beyond_the_knowledge_base_grant(self, restricted, world):
        restricted.act_as(OUTSIDER)  # matches the restriction, holds no Grant on hr

        response = restricted.http.post("/api/v1/hybrid-search/", json={"query": "annual leave days", "use_hybrid": False})

        assert response.status_code == 200 and response.json()["hits"] == []
        assert restricted.http.get(f"/api/v1/chat/documents/{restricted.docs['secret']}").status_code == 404

    def test_other_knowledge_bases_are_unaffected_by_a_restriction_elsewhere(self, restricted):
        restricted.act_as(caller("fred", "finance"))

        response = restricted.http.post("/api/v1/hybrid-search/", json={"query": "q", "use_hybrid": False})

        assert [h["title"] for h in response.json()["hits"]] == ["fin policy"]

    def test_an_unrestricted_caller_is_not_filtered(self, restricted):
        # An application API key (or authentication off) is unrestricted at the scope layer.
        from src.services.access import RetrievalScope

        restricted.app.dependency_overrides[get_retrieval_scope] = lambda: RetrievalScope.unrestricted("test")

        assert leaked(restricted.http.post("/api/v1/hybrid-search/", json={"query": "q", "use_hybrid": False}))


# --- changing a restriction ---------------------------------------------------------------------------------------- #
class TestSettingAndClearing:
    def test_it_updates_existing_chunks_in_place_without_reingesting(self, stack, index):
        body = restrict(stack, stack.docs["secret"], ("group", "hr-leads"), ("subject", "lena"))

        assert body["chunks_updated"] == 1
        assert sorted(index.docs[k]["restriction_principals"] for k in index.docs if "restriction_principals" in index.docs[k]) == [
            ["group:hr-leads", "subject:lena"]
        ]
        assert not any("restriction_principals" in s for s in index.docs.values() if s["document_id"] == stack.docs["open"])
        stack.embeddings.embed_passages.assert_not_called()  # no re-embedding: the index was edited, not rebuilt

    def test_clearing_makes_the_document_visible_again(self, restricted):
        assert not leaked(restricted.http.post("/api/v1/hybrid-search/", json={"query": "q", "use_hybrid": False}))

        restricted.act_as(ADMIN)
        assert restricted.http.delete(f"/api/v1/documents/{restricted.docs['secret']}/restrictions").status_code == 204
        restricted.act_as(ALICE)

        assert leaked(restricted.http.post("/api/v1/hybrid-search/", json={"query": "q", "use_hybrid": False}))
        assert all("restriction_principals" not in s for s in restricted.fake.docs.values())

    def test_putting_an_empty_set_also_clears(self, restricted):
        body = restrict(restricted, restricted.docs["secret"])

        assert body["restrictions"] == [] and all("restriction_principals" not in s for s in restricted.fake.docs.values())

    def test_restrictions_are_listed_for_the_document_and_the_knowledge_base(self, restricted, world):
        restricted.act_as(ADMIN)

        one = restricted.http.get(f"/api/v1/documents/{restricted.docs['secret']}/restrictions").json()
        every = restricted.http.get(f"/api/v1/knowledge-bases/{world.kbs['hr']}/document-restrictions").json()

        assert [(r["principal_type"], r["principal_value"]) for r in one["restrictions"]] == [("group", "hr-leads")]
        assert list(every) == [restricted.docs["secret"]]
        assert restricted.http.get(f"/api/v1/documents/{restricted.docs['open']}/restrictions").json()["restrictions"] == []

    def test_a_value_matching_no_known_claim_value_is_warned_about(self, stack, index):
        body = restrict(stack, stack.docs["secret"], ("group", "staff"), ("group", "hr-leeds"), ("everyone", ""))

        assert [(w["principal_type"], w["principal_value"]) for w in body["warnings"]] == [("group", "hr-leeds")]
        assert "typo" in body["warnings"][0]["message"]

    def test_a_value_seen_on_a_caller_in_the_audit_log_is_known(self, stack, index, world):
        with world.factory() as session:
            session.add(AuditEvent(action="query.answer", outcome="ok", actor_subject="lena", actor_groups=["hr-leads"]))
            session.commit()

        body = restrict(stack, stack.docs["secret"], ("group", "hr-leads"), ("subject", "lena"))

        assert body["warnings"] == []

    def test_a_failed_index_update_changes_nothing_and_says_so(self, stack, index, world):
        restrict(stack, stack.docs["secret"], ("group", "old"))
        stack.client.client = MagicMock()
        stack.client.client.update_by_query.side_effect = ConnectionError("down")
        stack.act_as(ADMIN)

        response = stack.http.put(
            f"/api/v1/documents/{stack.docs['secret']}/restrictions",
            json={"restrictions": [{"principal_type": "group", "principal_value": "x"}]},
        )

        assert response.status_code == 502
        with world.factory() as session:
            kept = RestrictionRepository(session).list_for_document(stack.docs["secret"])
        assert [(r.principal_type, r.principal_value) for r in kept] == [("group", "old")]  # the old one is back

    def test_chunks_left_behind_by_a_version_conflict_are_a_failure_not_a_success(self, stack, index):
        stack.client.client = MagicMock()
        stack.client.client.update_by_query.return_value = {"updated": 0, "failures": [], "version_conflicts": 1}
        stack.act_as(ADMIN)

        response = stack.http.put(
            f"/api/v1/documents/{stack.docs['secret']}/restrictions",
            json={"restrictions": [{"principal_type": "group", "principal_value": "x"}]},
        )

        assert response.status_code == 502

    def test_a_restriction_is_removed_with_its_document(self, stack, index, world):
        restrict(stack, stack.docs["secret"], ("group", "hr-leads"))
        stack.act_as(ADMIN)

        with world.factory() as session:
            session.delete(session.get(Document, stack.docs["secret"]))
            session.commit()
            assert RestrictionRepository(session).list_for_document(stack.docs["secret"]) == []

    def test_cached_answers_are_dropped_when_a_restriction_changes(self, stack, index):
        stack.app.state.cache_client = MagicMock()

        restrict(stack, stack.docs["secret"], ("group", "hr-leads"))

        stack.app.state.cache_client.clear.assert_called_once()


# --- who may administer it ---------------------------------------------------------------------------------------- #
class TestAuthorisation:
    BODY = {"restrictions": [{"principal_type": "group", "principal_value": "staff"}]}

    def test_a_querier_cannot_set_read_or_clear_restrictions(self, stack, index):
        stack.act_as(ALICE)
        url = f"/api/v1/documents/{stack.docs['secret']}/restrictions"

        assert stack.http.put(url, json=self.BODY).status_code == 403
        assert stack.http.get(url).status_code == 403
        assert stack.http.delete(url).status_code == 403
        assert stack.http.get(f"/api/v1/documents/{stack.docs['secret']}/access-check?subject=alice").status_code == 403
        assert all("restriction_principals" not in s for s in index.docs.values())

    def test_an_unknown_document_is_a_404(self, stack, index):
        stack.act_as(ADMIN)

        assert stack.http.put("/api/v1/documents/00000000-0000-0000-0000-000000000000/restrictions", json=self.BODY).status_code == 404

    def test_a_value_is_required_for_everything_but_everyone(self, stack, index):
        stack.act_as(ADMIN)
        url = f"/api/v1/documents/{stack.docs['secret']}/restrictions"

        assert stack.http.put(url, json={"restrictions": [{"principal_type": "group", "principal_value": " "}]}).status_code == 422


# --- the access check ---------------------------------------------------------------------------------------------- #
class TestAccessCheck:
    def check(self, stack, document, subject, groups="", roles=""):
        stack.act_as(ADMIN)
        response = stack.http.get(
            f"/api/v1/documents/{document}/access-check", params={"subject": subject, "groups": groups, "roles": roles}
        )
        assert response.status_code == 200, response.text
        return response.json()

    def test_it_follows_the_rule_grant_and_no_restriction_or_a_match(self, restricted):
        secret, open_ = restricted.docs["secret"], restricted.docs["open"]

        assert self.check(restricted, secret, "alice", "staff")["can_read"] is False
        assert self.check(restricted, secret, "lena", "staff,hr-leads")["can_read"] is True
        assert self.check(restricted, secret, "carol", "hr-leads")["can_read"] is False  # a match without a Grant
        assert self.check(restricted, open_, "alice", "staff")["can_read"] is True  # unrestricted inherits the Grant
        assert self.check(restricted, open_, "carol", "hr-leads")["can_read"] is False

    def test_it_explains_itself(self, restricted):
        denied = self.check(restricted, restricted.docs["secret"], "alice", "staff")

        assert denied["knowledge_base_granted"] is True and denied["restricted"] is True
        assert denied["restriction_matched"] is False and "none of the document's restrictions" in denied["reason"]

    def test_a_subject_or_role_restriction_matches_by_that_claim(self, stack, index):
        restrict(stack, stack.docs["secret"], ("subject", "alice"), ("role", "hr-director"))

        assert self.check(stack, stack.docs["secret"], "alice", "staff")["can_read"] is True
        assert self.check(stack, stack.docs["secret"], "bob", "staff", "hr-director")["can_read"] is True
        assert self.check(stack, stack.docs["secret"], "bob", "staff")["can_read"] is False

    def test_everyone_matches_any_caller_who_holds_the_grant(self, stack, index):
        restrict(stack, stack.docs["secret"], ("everyone", ""))

        assert self.check(stack, stack.docs["secret"], "bob", "staff")["can_read"] is True
        assert self.check(stack, stack.docs["secret"], "bob", "elsewhere")["can_read"] is False


# --- scope ---------------------------------------------------------------------------------------------------------- #
class TestCacheToken:
    def test_callers_of_one_knowledge_base_with_different_principals_never_share_cached_answers(self, world):
        settings = Settings(auth=AuthSettings(mode="jwt"))
        with world.factory() as session:
            alice = resolve_scope(ALICE, session, settings)
            lead = resolve_scope(LEAD, session, settings)
            alice_again = resolve_scope(ALICE, session, settings)

        assert alice.kb_ids == lead.kb_ids
        assert alice.cache_token() != lead.cache_token()
        assert alice.cache_token() == alice_again.cache_token()
