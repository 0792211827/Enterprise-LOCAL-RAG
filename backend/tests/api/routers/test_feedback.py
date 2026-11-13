"""Feedback and consent-based sharing: a thumbs and comment on an answer, and an explicit per-rating choice to
share that one exchange with admins. Admins see ratings without Conversation text; only shared exchanges are
visible, and they export as Golden set candidates.
"""

import pytest
import yaml
from src.auth import Role
from src.services.eval import load_golden_set
from tests.chat_support import ask, caller, exchange, stack, start_conversation, world  # noqa: F401

BASE = "/api/v1/chat/conversations"
ADMIN = "/api/v1/chat-admin"
QUESTION = "How much leave do I get?"
SECRET_ANSWER = "25 days."


def rate(stack, conversation_id, message_id, rating="down", comment=None, share=False):
    return stack.http.put(
        f"{BASE}/{conversation_id}/messages/{message_id}/feedback",
        json={"rating": rating, "comment": comment, "share_with_admins": share},
    )


def answer_id(frames_):
    return frames_[-1]["message_id"]


def as_admin(stack):
    stack.act_as(caller("root", roles=(Role.ADMIN, Role.QUERIER)))


def test_an_employee_rates_an_answer_and_sees_their_rating_when_the_conversation_is_reopened(stack, world):
    conversation_id, answer = exchange(stack, world, QUESTION)

    response = rate(stack, conversation_id, answer_id(answer), "down", "Missed the carry-over rule", share=False)

    assert response.status_code == 200
    assert response.json() == {"rating": "down", "comment": "Missed the carry-over rule", "share_with_admins": False}
    reopened = stack.http.get(f"{BASE}/{conversation_id}").json()
    assert reopened["messages"][0]["feedback"] is None
    assert reopened["messages"][1]["feedback"] == response.json()


def test_a_rating_can_be_changed_and_withdrawn(stack, world):
    conversation_id, answer = exchange(stack, world, QUESTION)
    message_id = answer_id(answer)
    rate(stack, conversation_id, message_id, "down", "bad")

    changed = rate(stack, conversation_id, message_id, "up", None, share=True)
    assert changed.json() == {"rating": "up", "comment": None, "share_with_admins": True}

    assert stack.http.delete(f"{BASE}/{conversation_id}/messages/{message_id}/feedback").status_code == 204
    assert stack.http.get(f"{BASE}/{conversation_id}").json()["messages"][1]["feedback"] is None


def test_only_answers_can_be_rated(stack, world):
    conversation_id, _ = exchange(stack, world, QUESTION)
    question = stack.http.get(f"{BASE}/{conversation_id}").json()["messages"][0]
    assert rate(stack, conversation_id, question["id"]).status_code == 400


@pytest.mark.parametrize("roles", [(Role.QUERIER,), (Role.ADMIN, Role.QUERIER)], ids=["querier", "admin"])
def test_a_user_cannot_rate_another_users_message(stack, world, roles):
    conversation_id, answer = exchange(stack, world, QUESTION)
    stack.act_as(caller("mallory", "staff", roles=roles))

    assert rate(stack, conversation_id, answer_id(answer), "down", "trolling", share=True).status_code == 404
    assert stack.http.delete(f"{BASE}/{conversation_id}/messages/{answer_id(answer)}/feedback").status_code == 404

    as_admin(stack)
    assert stack.http.get(f"{ADMIN}/ratings").json() == []


def test_a_message_cannot_be_rated_through_a_different_conversation(stack, world):
    first, answer = exchange(stack, world, QUESTION)
    second = start_conversation(stack, world)["id"]
    assert rate(stack, second, answer_id(answer)).status_code == 404
    assert rate(stack, first, "00000000-0000-0000-0000-0000000000ff").status_code == 404


def test_admins_see_ratings_per_application_with_message_ids_and_never_conversation_text(stack, world):
    c1, a1 = exchange(stack, world, QUESTION)
    c2, a2 = exchange(stack, world, "another private question")
    rate(stack, c1, answer_id(a1), "up")
    rate(stack, c2, answer_id(a2), "down", "wrong", share=False)
    as_admin(stack)

    summary = stack.http.get(f"{ADMIN}/ratings/summary").json()
    assert summary == [{"application_id": world.apps["Handbook"], "application_name": "Handbook", "up": 1, "down": 1}]

    listing = stack.http.get(f"{ADMIN}/ratings", params={"application_id": world.apps["Handbook"]})
    assert {r["message_id"] for r in listing.json()} == {answer_id(a1), answer_id(a2)}
    down = next(r for r in listing.json() if r["rating"] == "down")
    assert (down["comment"], down["shared"], down["application_id"]) == ("wrong", False, world.apps["Handbook"])
    for secret in (QUESTION, "private question", SECRET_ANSWER, "Employees get"):
        assert secret not in listing.text and secret not in stack.http.get(f"{ADMIN}/ratings/summary").text


def test_ratings_are_filtered_by_application(stack, world):
    c1, a1 = exchange(stack, world, QUESTION, application="Handbook")
    c2, a2 = exchange(stack, world, QUESTION, application="Quiet")
    rate(stack, c1, answer_id(a1))
    rate(stack, c2, answer_id(a2))
    as_admin(stack)

    listing = stack.http.get(f"{ADMIN}/ratings", params={"application_id": world.apps["Quiet"]}).json()
    assert [r["message_id"] for r in listing] == [answer_id(a2)]


def test_an_admin_cannot_read_an_unshared_exchange(stack, world):
    conversation_id, answer = exchange(stack, world, QUESTION)
    rate(stack, conversation_id, answer_id(answer), "down", "meh", share=False)
    as_admin(stack)

    shared = stack.http.get(f"{ADMIN}/shared-exchanges")
    assert shared.status_code == 200 and shared.json() == []
    assert (
        QUESTION not in stack.http.get(f"{ADMIN}/shared-exchanges/export", params={"application_id": world.apps["Handbook"]}).text
    )
    # ...and the Conversation itself stays closed to them.
    assert stack.http.get(f"{BASE}/{conversation_id}").status_code == 404


def test_an_exchange_the_owner_shared_becomes_visible_to_admins_with_its_question_answer_and_sources(stack, world):
    conversation_id, answer = exchange(stack, world, QUESTION)
    exchange(stack, world, "unshared sibling question")
    rate(stack, conversation_id, answer_id(answer), "down", "incomplete", share=True)
    as_admin(stack)

    [item] = stack.http.get(f"{ADMIN}/shared-exchanges").json()

    assert item["message_id"] == answer_id(answer)
    assert (item["question"], item["answer"], item["comment"], item["rating"]) == (QUESTION, SECRET_ANSWER, "incomplete", "down")
    assert item["sources"] == ["hr doc"]
    assert item["application_id"] == world.apps["Handbook"]
    assert "unshared sibling" not in stack.http.get(f"{ADMIN}/shared-exchanges").text


def test_withdrawing_the_share_or_deleting_the_conversation_takes_the_exchange_back(stack, world):
    c1, a1 = exchange(stack, world, QUESTION)
    c2, a2 = exchange(stack, world, "second question")
    rate(stack, c1, answer_id(a1), "up", share=True)
    rate(stack, c2, answer_id(a2), "up", share=True)
    as_admin(stack)
    assert len(stack.http.get(f"{ADMIN}/shared-exchanges").json()) == 2

    stack.act_as(caller("alice", "staff"))
    rate(stack, c1, answer_id(a1), "up", share=False)
    stack.http.delete(f"{BASE}/{c2}")

    as_admin(stack)
    assert stack.http.get(f"{ADMIN}/shared-exchanges").json() == []
    assert stack.http.get(f"{ADMIN}/ratings").json()[0]["shared"] is False


def test_shared_exchanges_export_in_the_golden_set_format(stack, world, tmp_path):
    c1, a1 = exchange(stack, world, QUESTION)
    c2, a2 = exchange(stack, world, "not shared")
    rate(stack, c1, answer_id(a1), "up", share=True)
    rate(stack, c2, answer_id(a2), "down", share=False)
    as_admin(stack)

    response = stack.http.get(f"{ADMIN}/shared-exchanges/export", params={"application_id": world.apps["Handbook"]})

    assert response.status_code == 200 and "yaml" in response.headers["content-type"]
    path = tmp_path / "candidates.yaml"
    path.write_text(response.text)
    golden = load_golden_set(path)  # the same loader the eval harness uses
    assert golden.application == "handbook"
    [case] = golden.cases
    assert case.question == QUESTION
    assert case.expected_documents == ("hr doc",)
    assert case.principal is None  # nobody's identity leaves with the export
    assert yaml.safe_load(response.text)["version"] == 1
    assert "alice" not in response.text and "not shared" not in response.text


def test_the_export_of_an_application_with_nothing_shared_is_an_empty_candidate_list(stack, world):
    as_admin(stack)
    response = stack.http.get(f"{ADMIN}/shared-exchanges/export", params={"application_id": world.apps["Handbook"]})
    assert response.status_code == 200 and yaml.safe_load(response.text)["cases"] == []


def test_the_export_needs_a_known_application(stack, world):
    as_admin(stack)
    assert stack.http.get(f"{ADMIN}/shared-exchanges/export").status_code == 422
    missing = stack.http.get(
        f"{ADMIN}/shared-exchanges/export", params={"application_id": "00000000-0000-0000-0000-0000000000ff"}
    )
    assert missing.status_code == 404


@pytest.mark.parametrize("path", ["/ratings", "/ratings/summary", "/shared-exchanges", "/conversation-counts"])
def test_only_admins_reach_the_feedback_views(stack, path):
    assert stack.http.get(f"{ADMIN}{path}").status_code == 403


def test_a_thumbs_down_exchange_is_exported_without_expected_documents(stack, world):
    # What a bad answer cited is not known to be right, so it must not become the expectation.
    conversation_id, answer = exchange(stack, world, QUESTION)
    rate(stack, conversation_id, answer_id(answer), "down", share=True)
    as_admin(stack)

    response = stack.http.get(f"{ADMIN}/shared-exchanges/export", params={"application_id": world.apps["Handbook"]})

    [case] = yaml.safe_load(response.text)["cases"]
    assert case["question"] == QUESTION and "expected" not in case
