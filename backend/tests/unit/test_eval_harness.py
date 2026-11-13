"""The eval command: Golden set in, structured report out, non-zero only on permission failures."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import src.models  # noqa: F401  (register models on Base.metadata)
from src.cli import eval as eval_cli
from src.config import AuthSettings, Settings
from src.models import KnowledgeBase
from src.models.application import RAGApplication
from src.models.provider import ModelConfiguration
from src.models.retrieval import RetrievalConfiguration
from src.repositories import GrantRepository
from src.services.eval import EvalContext

from tests.audit_support import opener, sqlite_session_factory

DOCS = [
    {"kb": "hr", "document_id": "d-leave", "title": "Leave Policy", "chunk_text": "Employees get 25 days of annual leave."},
    {"kb": "hr", "document_id": "d-remote", "title": "Remote Work", "chunk_text": "Remote work is allowed two days a week."},
    {"kb": "fin", "document_id": "d-salary", "title": "Salary Bands", "chunk_text": "The salary band for level 4 is confidential."},
]


class FakeIndex:
    """Honours the knowledge-base filter it is given, like the real index, and ranks by word overlap."""

    def __init__(self):
        self.calls = []

    def search_unified(self, query, size=10, knowledge_base_ids=None, **kwargs):
        self.calls.append(knowledge_base_ids)
        words = set(query.lower().replace("?", "").split())
        pool = [d for d in DOCS if knowledge_base_ids is None or d["kb_id"] in knowledge_base_ids]
        scored = sorted(pool, key=lambda d: -len(words & set(d["chunk_text"].lower().replace(".", "").split())))
        hits = [{k: v for k, v in d.items() if k not in ("kb", "kb_id")} | {"knowledge_base_id": d["kb_id"], "score": 1.0} for d in scored]
        return {"total": len(hits), "hits": hits[:size]}


@pytest.fixture
def world():
    factory = sqlite_session_factory()
    ids = {}
    with factory() as session:
        for slug in ("hr", "fin"):
            kb = KnowledgeBase(name=slug, slug=slug, index_name=f"{slug}-idx")
            session.add(kb)
            session.commit()
            ids[slug] = kb
        config = ModelConfiguration(name="m", llm_model="served")
        retrieval = RetrievalConfiguration(name="r", mode="bm25", top_k=3)
        session.add_all([config, retrieval])
        session.commit()
        application = RAGApplication(
            name="Handbook",
            slug="handbook",
            model_configuration_id=config.id,
            retrieval_configuration_id=retrieval.id,
        )
        application.knowledge_bases = [ids["hr"], ids["fin"]]
        session.add(application)
        session.commit()
        grants = GrantRepository(session)
        grants.replace_for_knowledge_base(ids["hr"].id, [("group", "staff")])
        grants.replace_for_knowledge_base(ids["fin"].id, [("group", "finance")])
        kb_ids = {slug: str(kb.id) for slug, kb in ids.items()}
    for doc in DOCS:
        doc["kb_id"] = kb_ids[doc["kb"]]
    return SimpleNamespace(factory=factory, kb_ids=kb_ids)


@pytest.fixture
def context(world):
    llm = AsyncMock()
    llm.generate_rag_answer = AsyncMock(return_value={"answer": "25 days."})
    embeddings = AsyncMock()
    embeddings.embed_query = AsyncMock(return_value=[0.1])
    return EvalContext(
        settings=Settings(auth=AuthSettings(mode="jwt")),
        open_session=opener(world.factory),
        opensearch=FakeIndex(),
        embeddings=embeddings,
        llm=llm,
    )


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "golden.yaml"
    path.write_text(text)
    return path


def run(tmp_path, context, text, *extra):
    golden = write(tmp_path, text)
    out = tmp_path / "report.json"
    code = eval_cli.main(["run", str(golden), "--application", "handbook", "--out", str(out), *extra], context=context)
    return code, json.loads(out.read_text()) if out.exists() else None


QUALITY = """
version: 3
name: handbook
cases:
  - id: leave
    question: How many days of annual leave do employees get
    expected: {documents: [Leave Policy], passages: ["25 days"]}
  - id: remote
    question: Is remote work allowed
    expected: {documents: [d-remote]}
  - id: miss
    question: What is the parking policy
    expected: {documents: [Parking Guide]}
"""


def test_report_has_recall_mrr_and_per_case_results(tmp_path, context):
    code, report = run(tmp_path, context, QUALITY, "--k", "2")

    assert code == 0
    assert report["report_version"] == 1
    assert report["golden_set"]["name"] == "handbook" and report["golden_set"]["version"] == "3"
    assert report["application"]["slug"] == "handbook" and report["k"] == 2
    # leave: both expectations found at rank 1; remote: found at rank 1; miss: nothing found.
    assert report["metrics"]["recall_at_k"] == pytest.approx((1.0 + 1.0 + 0.0) / 3)
    assert report["metrics"]["mrr"] == pytest.approx((1.0 + 1.0 + 0.0) / 3)
    assert report["metrics"]["cases_total"] == 3 and report["metrics"]["cases_scored"] == 3
    by_id = {c["id"]: c for c in report["cases"]}
    assert [c["id"] for c in report["cases"]] == ["leave", "remote", "miss"]
    assert by_id["leave"]["recall_at_k"] == 1.0 and by_id["leave"]["reciprocal_rank"] == 1.0
    assert by_id["leave"]["retrieved"][0]["title"] == "Leave Policy" and by_id["leave"]["retrieved"][0]["rank"] == 1
    assert by_id["miss"]["expected"]["missing"] == ["document: Parking Guide"]
    assert by_id["leave"]["answer"] == "25 days."


def test_a_lower_ranked_hit_gives_a_fractional_reciprocal_rank(tmp_path, context):
    text = """
cases:
  - question: remote work allowed two days
    expected: {documents: [Salary Bands]}
"""
    code, report = run(tmp_path, context, text, "--k", "3", "--retrieval-only")
    case = report["cases"][0]
    assert code == 0
    assert case["reciprocal_rank"] == pytest.approx(1 / 3) and report["metrics"]["mrr"] == pytest.approx(1 / 3)


def test_poor_quality_never_changes_the_exit_code(tmp_path, context):
    text = """
cases:
  - question: nothing matches this
    expected: {documents: [Does Not Exist], passages: [nope]}
"""
    code, report = run(tmp_path, context, text)
    assert code == 0 and report["metrics"]["recall_at_k"] == 0.0 and report["permissions"]["violations"] == 0


def test_cases_run_as_their_principal_and_the_report_says_what_they_could_search(tmp_path, context, world):
    text = """
cases:
  - id: staff
    question: salary band confidential
    principal: {subject: alice, groups: [staff]}
    must_not_return: {documents: [Salary Bands], passages: ["salary band"]}
  - id: finance
    question: salary band confidential
    principal: {subject: bob, groups: [finance]}
    expected: {documents: [Salary Bands]}
"""
    code, report = run(tmp_path, context, text)

    assert code == 0
    staff, finance = report["cases"]
    assert staff["scope"]["permitted_kb_ids"] == [world.kb_ids["hr"]]
    assert finance["scope"]["permitted_kb_ids"] == [world.kb_ids["fin"]]
    assert staff["must_not_return"]["violations"] == [] and finance["recall_at_k"] == 1.0
    assert report["permissions"] == {"cases_checked": 1, "violations": 0, "errors": 0, "passed": True}
    assert staff["principal"] == {"subject": "alice", "groups": ["staff"], "roles": []}


def test_leaked_content_makes_the_command_fail(tmp_path, context, world):
    # A misconfigured grant: the finance knowledge base is readable by everyone.
    with context.open_session() as session:
        GrantRepository(session).replace_for_knowledge_base(world.kb_ids["fin"], [("everyone", "*")])
    text = """
cases:
  - id: leak
    question: salary band confidential
    principal: {subject: alice, groups: [staff]}
    must_not_return: {documents: ["Salary Bands"], passages: ["Confidential"]}
"""
    code, report = run(tmp_path, context, text)

    assert code == 1
    assert report["permissions"]["violations"] == 1 and report["permissions"]["passed"] is False
    assert report["cases"][0]["must_not_return"]["violations"] == ["document: Salary Bands", "passage: Confidential"]


def test_a_permission_case_that_could_not_run_is_not_a_pass(tmp_path, context):
    context.opensearch.search_unified = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("index down"))
    text = """
cases:
  - question: anything
    principal: {subject: alice, groups: [staff]}
    must_not_return: {passages: [secret]}
"""
    code, report = run(tmp_path, context, text)

    assert code == 1
    assert report["permissions"]["errors"] == 1 and "index down" in report["cases"][0]["error"]


def test_retrieval_only_skips_generation(tmp_path, context):
    code, report = run(tmp_path, context, QUALITY, "--retrieval-only")
    assert code == 0 and report["cases"][0]["answer"] is None
    context.llm.generate_rag_answer.assert_not_called()


def test_an_unknown_application_is_a_usage_error(tmp_path, context, capsys):
    golden = write(tmp_path, QUALITY)
    assert eval_cli.main(["run", str(golden), "--application", "nope"], context=context) == 2
    assert "nope" in capsys.readouterr().err


@pytest.mark.parametrize(
    "text, message",
    [
        ("cases: []", "non-empty"),
        ("cases:\n  - question: q\n    must_not_retun: {passages: [x]}", "must_not_retun"),
        ("cases:\n  - expected: {documents: [a]}", "question"),
        ("cases:\n  - {id: a, question: q}\n  - {id: a, question: r}", "unique"),
        ("cases:\n  - question: q\n    principal: {groups: [x]}", "subject"),
        ("cases: [", "valid YAML"),
    ],
)
def test_an_invalid_golden_set_is_refused_with_what_to_fix(tmp_path, context, capsys, text, message):
    code = eval_cli.main(["run", str(write(tmp_path, text)), "--application", "handbook"], context=context)
    assert code == 2 and message in capsys.readouterr().err


def test_the_report_is_stable_so_runs_diff_cleanly(tmp_path, context):
    _, first = run(tmp_path, context, QUALITY)
    _, second = run(tmp_path, context, QUALITY)
    first.pop("generated_at"), second.pop("generated_at")
    assert first == second


def test_the_example_golden_set_is_valid():
    from src.services.eval import load_golden_set

    example = Path(__file__).resolve().parents[3] / "evals" / "example-golden-set.yaml"
    golden = load_golden_set(example)
    assert any(case.has_permission_check for case in golden.cases)


# --------------------------------------------------------------------------- #
# Groundedness
# --------------------------------------------------------------------------- #
class FakeJudge:
    def __init__(self, scores=None, fail_on=()):
        self.scores, self.fail_on, self.calls = scores or {}, set(fail_on), []

    async def judge(self, question, answer, passages):
        self.calls.append((question, answer, passages))
        if any(token in question for token in self.fail_on):
            raise RuntimeError("judge model unreachable")
        return self.scores.get(question, 1.0)


GROUNDED = """
cases:
  - {id: leave, question: "annual leave days employees", expected: {documents: [Leave Policy]}}
  - {id: remote, question: "remote work allowed", expected: {documents: [Remote Work]}}
"""


def test_groundedness_is_scored_per_case_and_averaged(tmp_path, context):
    context.judge = FakeJudge({"annual leave days employees": 1.0, "remote work allowed": 0.5})
    code, report = run(tmp_path, context, GROUNDED, "--groundedness")

    assert code == 0
    assert [c["groundedness"]["score"] for c in report["cases"]] == [1.0, 0.5]
    assert report["metrics"]["groundedness"] == 0.75
    question, answer, passages = context.judge.calls[0]
    assert answer == "25 days." and passages[0] == "Employees get 25 days of annual leave."


def test_a_judge_failure_is_unscored_never_a_pass_or_a_crash(tmp_path, context):
    context.judge = FakeJudge({"annual leave days employees": 0.5}, fail_on=["remote"])
    code, report = run(tmp_path, context, GROUNDED, "--groundedness")

    assert code == 0
    failed = report["cases"][1]["groundedness"]
    assert failed["score"] is None and failed["status"] == "unscored" and "unreachable" in failed["reason"]
    # The failure does not drag the mean down or count as a pass: only scored cases contribute.
    assert report["metrics"]["groundedness"] == 0.5 and report["metrics"]["groundedness_scored"] == 1


def test_nothing_to_judge_is_unscored(tmp_path, context):
    context.judge = FakeJudge()
    text = "cases:\n  - {id: none, question: zzz qqq, principal: {subject: nobody}, expected: {documents: [x]}}"
    _, report = run(tmp_path, context, text, "--groundedness")
    assert report["cases"][0]["groundedness"]["status"] == "unscored"

    _, retrieval_only = run(tmp_path, context, GROUNDED, "--groundedness", "--retrieval-only")
    assert {c["groundedness"]["reason"] for c in retrieval_only["cases"]} == {"no answer was generated"}
    assert retrieval_only["metrics"]["groundedness"] is None


def test_bad_scores_never_change_the_exit_code(tmp_path, context):
    context.judge = FakeJudge(scores={"annual leave days employees": 0.0, "remote work allowed": 0.0})
    code, report = run(tmp_path, context, GROUNDED, "--groundedness")
    assert code == 0 and report["metrics"]["groundedness"] == 0.0


@pytest.mark.parametrize(
    "reply, expected",
    [
        ('{"score": 5, "reason": "fully supported"}', 1.0),
        ('Sure! {"score": 3}', 0.5),
        ("Score: 1", 0.0),
        ("I would say 4 out of 5.", 0.75),
    ],
)
async def test_the_llm_judge_reads_the_scores_small_models_actually_produce(reply, expected):
    from src.services.eval.judge import LLMGroundednessJudge

    provider = AsyncMock()
    provider.generate = AsyncMock(return_value={"response": reply})
    judge = LLMGroundednessJudge(provider, model="judge-model")

    assert await judge.judge("q", "a", ["passage"]) == pytest.approx(expected)
    call = provider.generate.call_args
    assert call.kwargs["model"] == "judge-model" and "passage" in call.kwargs["prompt"]


@pytest.mark.parametrize("reply", ["", "I cannot decide.", '{"score": 9}', '{"score": "high"}'])
async def test_an_unusable_judge_reply_raises_so_the_case_is_unscored(reply):
    from src.services.eval.judge import JudgeError, LLMGroundednessJudge

    provider = AsyncMock()
    provider.generate = AsyncMock(return_value={"response": reply})
    with pytest.raises(JudgeError):
        await LLMGroundednessJudge(provider, model="m").judge("q", "a", ["p"])


# --------------------------------------------------------------------------- #
# Comparing reports
# --------------------------------------------------------------------------- #
def report_dict(recall, mrr, groundedness, cases, reranking="disabled", sha="abc", violations=0):
    return {
        "report_version": 1,
        "golden_set": {"name": "handbook", "version": "1", "sha256": sha},
        "application": {"slug": "handbook"},
        "k": 3,
        "reranking": reranking,
        "metrics": {"recall_at_k": recall, "mrr": mrr, "groundedness": groundedness, "cases_total": len(cases)},
        "permissions": {"cases_checked": 1, "violations": violations, "errors": 0, "passed": violations == 0},
        "cases": cases,
    }


def case_dict(case_id, recall, rr, titles, groundedness=None):
    return {
        "id": case_id,
        "recall_at_k": recall,
        "reciprocal_rank": rr,
        "groundedness": None if groundedness is None else {"score": groundedness},
        "retrieved": [{"rank": i, "title": t} for i, t in enumerate(titles, 1)],
        "must_not_return": None,
    }


BEFORE = report_dict(
    0.5, 0.5, 0.6,
    [case_dict("a", 1.0, 0.5, ["Other", "Leave Policy"], 0.8), case_dict("b", 0.0, 0.0, ["Nope"], 0.4), case_dict("gone", 1.0, 1.0, ["X"])],
)
AFTER = report_dict(
    0.75, 0.75, 0.7,
    [case_dict("a", 1.0, 1.0, ["Leave Policy", "Other"], 0.9), case_dict("b", 0.0, 0.0, ["Nope"], 0.4), case_dict("new", 1.0, 1.0, ["Y"])],
    reranking="enabled",
)


def test_compare_shows_per_metric_and_per_case_differences():
    from src.services.eval.compare import compare_reports

    diff = compare_reports(BEFORE, AFTER)

    assert diff["metrics"]["mrr"] == {"before": 0.5, "after": 0.75, "delta": 0.25}
    assert diff["metrics"]["recall_at_k"]["delta"] == 0.25
    assert diff["metrics"]["groundedness"]["delta"] == pytest.approx(0.1)
    assert [c["id"] for c in diff["cases"]["changed"]] == ["a"]
    changed = diff["cases"]["changed"][0]
    assert changed["reciprocal_rank"] == {"before": 0.5, "after": 1.0, "delta": 0.5}
    assert changed["ranking"] == {"before": ["Other", "Leave Policy"], "after": ["Leave Policy", "Other"]}
    assert changed["groundedness"]["delta"] == pytest.approx(0.1)
    assert diff["cases"]["unchanged"] == 1
    assert diff["cases"]["only_in_before"] == ["gone"] and diff["cases"]["only_in_after"] == ["new"]
    assert diff["context"]["reranking"] == {"before": "disabled", "after": "enabled"}


def test_compare_warns_when_the_golden_sets_differ():
    from src.services.eval.compare import compare_reports

    diff = compare_reports(BEFORE, report_dict(0.5, 0.5, None, [], sha="different"))
    assert any("different Golden set" in w for w in diff["warnings"])


def test_compare_command_prints_the_differences_and_never_fails_on_quality(tmp_path, capsys):
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps(AFTER))
    after.write_text(json.dumps(BEFORE))  # the "after" run is worse

    assert eval_cli.main(["compare", str(before), str(after)]) == 0
    out = capsys.readouterr().out
    assert "mrr" in out and "0.750 -> 0.500" in out and "-0.250" in out
    assert "a:" in out

    assert eval_cli.main(["compare", str(before), str(after), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["mrr"]["delta"] == -0.25


def test_compare_flags_new_permission_violations_without_failing(tmp_path, capsys):
    leaky = report_dict(0.5, 0.5, None, [], violations=2)
    paths = [tmp_path / "a.json", tmp_path / "b.json"]
    paths[0].write_text(json.dumps(report_dict(0.5, 0.5, None, [])))
    paths[1].write_text(json.dumps(leaky))

    assert eval_cli.main(["compare", *map(str, paths)]) == 0
    assert "permission violations: 0 -> 2" in capsys.readouterr().out


def test_compare_with_an_unreadable_report_is_a_usage_error(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(BEFORE))
    assert eval_cli.main(["compare", str(good), str(tmp_path / "missing.json")]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert eval_cli.main(["compare", str(good), str(bad)]) == 2
    assert "bad.json" in capsys.readouterr().err
