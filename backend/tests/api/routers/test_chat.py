"""The employee chat surface: who is offered which Applications, streamed answers with citations, role enforcement.

Part one runs the real application to prove the server-side role split. Part two runs the chat router
over in-memory SQLite, with real grants and the real scope resolver, offline (fixtures in tests/chat_support.py).
"""

import pytest
import src.main as main
from src.auth import Principal, Role, get_principal
from src.config import AuthSettings, Settings
from tests.audit_support import events
from tests.chat_support import DOC_TEXT, ask, caller, exchange, frames, stack, start_conversation, world  # noqa: F401

CHAT_PATHS = [
    ("GET", "/api/v1/chat/applications", None),
    ("POST", "/api/v1/chat/conversations", {"application_id": "00000000-0000-0000-0000-000000000001"}),
    ("GET", "/api/v1/chat/conversations", None),
    ("POST", "/api/v1/chat/conversations/00000000-0000-0000-0000-000000000001/messages", {"query": "hi"}),
    ("GET", "/api/v1/chat/documents/00000000-0000-0000-0000-000000000001", None),
]


# --------------------------------------------------------------------------- #
# Role-to-surface split on the real application
# --------------------------------------------------------------------------- #
@pytest.fixture
def as_caller():
    def _set(*roles: Role):
        main.app.dependency_overrides[get_principal] = lambda: Principal(
            subject="tester", roles=frozenset(roles), auth_method="jwt"
        )

    yield _set
    main.app.dependency_overrides.pop(get_principal, None)


@pytest.mark.parametrize("method, path, body", CHAT_PATHS)
@pytest.mark.parametrize("roles", [(), (Role.AUDITOR,)], ids=["no-role", "auditor-only"])
async def test_callers_without_the_querier_role_cannot_reach_chat(client, as_caller, method, path, body, roles):
    as_caller(*roles)
    response = await client.request(method, path, json=body)
    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient role"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/knowledge-bases",
        "/api/v1/applications",
        "/api/v1/providers",
        "/api/v1/audit/events",
        "/api/v1/chat-admin/conversation-counts",
    ],
)
async def test_a_querier_reaches_chat_but_neither_the_control_plane_nor_the_audit_log(client, as_caller, path):
    as_caller(Role.QUERIER)
    assert (await client.get(path)).status_code == 403


async def test_an_auditor_reaches_the_audit_log_and_not_the_control_plane(client, as_caller):
    as_caller(Role.AUDITOR)
    assert (await client.get("/api/v1/knowledge-bases")).status_code == 403


async def test_a_user_with_several_roles_gets_each_surface(client, as_caller):
    as_caller(Role.QUERIER, Role.AUDITOR)
    # The role guard admits them on both surfaces (the audit log itself needs the real database).
    chat_response = await client.get("/api/v1/chat/applications")
    assert chat_response.status_code != 403
    assert (await client.get("/api/v1/audit/events")).status_code != 403
    assert (await client.get("/api/v1/knowledge-bases")).status_code == 403


# --------------------------------------------------------------------------- #
# Chat over SQLite, with real grants
# --------------------------------------------------------------------------- #
def listed(stack):
    response = stack.http.get("/api/v1/chat/applications")
    assert response.status_code == 200, response.text
    return sorted(a["name"] for a in response.json())


def test_an_application_is_listed_only_if_the_caller_holds_a_grant_on_every_knowledge_base(stack):
    assert listed(stack) == ["Handbook", "Quiet"]

    stack.act_as(caller("carol", "staff", "finance"))
    assert listed(stack) == ["Both", "Finance", "Handbook", "Quiet"]

    stack.act_as(caller("dave", "finance"))
    assert listed(stack) == ["Finance"]

    stack.act_as(caller("erin"))
    assert listed(stack) == []


def test_the_listing_exposes_nothing_an_end_user_should_not_see(stack):
    [handbook] = [a for a in stack.http.get("/api/v1/chat/applications").json() if a["name"] == "Handbook"]
    assert set(handbook) == {"id", "name", "slug", "description", "citations_enabled"}
    assert "SECRET PROMPT" not in stack.http.get("/api/v1/chat/applications").text


def test_an_admin_without_the_bypass_sees_only_what_is_granted_like_anyone_else(stack):
    stack.act_as(caller("root", roles=(Role.ADMIN, Role.QUERIER)))
    assert listed(stack) == []

    stack.use_settings(Settings(auth=AuthSettings(mode="jwt", admin_bypass_retrieval=True)))
    assert listed(stack) == ["Both", "Finance", "Handbook", "Quiet"]  # an application with nothing attached is never offered


def test_the_answer_streams_deltas_then_citations_then_done(stack, world):
    conversation = start_conversation(stack, world)
    events_ = ask(stack, conversation["id"])

    assert [e["type"] for e in events_] == ["delta", "delta", "citations", "done"]
    assert "".join(e["content"] for e in events_ if e["type"] == "delta") == "25 days."
    [citation] = events_[2]["citations"]
    assert citation == {
        "index": 1,
        "document_id": world.docs["hr"],
        "title": "hr doc",
        "section_title": "Leave",
        "chunk_id": "c1",
        "passage": "Employees get 25 days of annual leave.",
        "score": 0.9,
    }
    assert stack.index.search_unified.call_args.kwargs["knowledge_base_ids"] == [world.kbs["hr"]]


def test_the_stream_is_server_sent_events(stack, world):
    conversation = start_conversation(stack, world)
    response = stack.http.post(f"/api/v1/chat/conversations/{conversation['id']}/messages", json={"query": "q"})
    assert response.headers["content-type"].startswith("text/event-stream")


def test_an_application_with_citations_off_sends_none(stack, world):
    _, events_ = exchange(stack, world, application="Quiet")
    assert [e["type"] for e in events_] == ["delta", "delta", "citations", "done"]
    assert events_[2]["citations"] == []


@pytest.mark.parametrize("name", ["Finance", "Both", "Empty"])
def test_starting_a_conversation_with_an_application_that_is_not_offered_is_a_404_and_searches_nothing(stack, world, name):
    response = stack.http.post("/api/v1/chat/conversations", json={"application_id": world.apps[name]})
    assert response.status_code == 404
    stack.index.search_unified.assert_not_called()


def test_a_nonexistent_application_looks_the_same_as_one_that_is_not_offered(stack, world):
    missing = stack.http.post("/api/v1/chat/conversations", json={"application_id": "00000000-0000-0000-0000-0000000000ff"})
    hidden = stack.http.post("/api/v1/chat/conversations", json={"application_id": world.apps["Finance"]})
    assert (missing.status_code, missing.json()) == (hidden.status_code, hidden.json())


def test_an_application_that_stops_being_offered_can_no_longer_be_asked(stack, world):
    conversation = start_conversation(stack, world)
    stack.act_as(caller("alice"))  # alice's grants are gone
    response = stack.http.post(f"/api/v1/chat/conversations/{conversation['id']}/messages", json={"query": "q"})
    assert response.status_code == 404
    stack.index.search_unified.assert_not_called()


def test_an_empty_question_is_rejected(stack, world):
    conversation = start_conversation(stack, world)
    assert stack.http.post(f"/api/v1/chat/conversations/{conversation['id']}/messages", json={"query": ""}).status_code == 422


def test_a_citation_opens_the_source_document_for_a_reader_of_its_knowledge_base(stack, world):
    response = stack.http.get(f"/api/v1/chat/documents/{world.docs['hr']}")

    assert response.status_code == 200
    assert response.json() == {
        "id": world.docs["hr"],
        "title": "hr doc",
        "source_uri": None,
        "content_type": None,
        "text": DOC_TEXT,
    }


def test_a_document_in_an_unreadable_knowledge_base_is_a_404(stack, world):
    assert stack.http.get(f"/api/v1/chat/documents/{world.docs['fin']}").status_code == 404
    assert stack.http.get("/api/v1/chat/documents/00000000-0000-0000-0000-0000000000ff").status_code == 404


def test_the_chat_surface_does_not_serve_callers_without_the_role(stack, world):
    conversation = start_conversation(stack, world)
    stack.act_as(caller("auditor", roles=(Role.AUDITOR,)))
    assert stack.http.get("/api/v1/chat/applications").status_code == 403
    assert stack.http.get("/api/v1/chat/conversations").status_code == 403
    assert stack.http.post(f"/api/v1/chat/conversations/{conversation['id']}/messages", json={"query": "q"}).status_code == 403


def test_a_chat_answer_is_audited_with_the_caller_and_the_applications_scope(stack, world):
    exchange(stack, world)

    [event] = [e for e in events(world.factory) if e.action == "query.answer"]
    assert (event.actor_subject, event.outcome) == ("alice", "ok")
    assert event.permitted_kb_ids == [world.kbs["hr"]]
    assert event.route == "/api/v1/chat/conversations/{conversation_id}/messages"
    assert str(event.application_id) == world.apps["Handbook"]
    assert event.retrieved[0]["chunk_id"] == "c1"


def test_a_failure_while_streaming_ends_the_stream_with_an_error_event(stack, world):
    async def broken(**kwargs):
        yield {"response": "25 "}
        raise RuntimeError("model crashed")

    stack.llm.generate_rag_answer_stream = broken
    conversation = start_conversation(stack, world)
    response = stack.http.post(f"/api/v1/chat/conversations/{conversation['id']}/messages", json={"query": "q"})

    types = [e["type"] for e in frames(response)]
    assert response.status_code == 200 and types == ["delta", "error", "done"]
    assert "model crashed" not in response.text  # internals stay out of what an employee sees
    [event] = [e for e in events(world.factory) if e.action == "query.answer"]
    assert event.outcome == "error"
