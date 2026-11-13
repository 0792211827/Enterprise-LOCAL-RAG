"""Score an Application against a Golden set.

    python -m src.cli.eval run evals/example-golden-set.yaml --application hr-assistant
    python -m src.cli.eval run golden.yaml --application hr-assistant --k 5 --out report.json
    python -m src.cli.eval run golden.yaml --application hr-assistant --retrieval-only

or ``make eval GOLDEN=evals/example-golden-set.yaml APP=hr-assistant`` against the running ``rag-api`` container.

Writes a JSON report (recall@k, MRR and per-case results) to ``--out``, or to
stdout. Cases run through the production scope resolver and answer path, as the
Principal each case names. Exit status:

    0  every ``must_not_return`` check passed (quality scores never change this)
    1  a ``must_not_return`` case leaked content, or could not be verified
    2  the Golden set or arguments are unusable (e.g. unknown Application)

    python -m src.cli.eval compare before.json after.json    # what changed between two runs (always exits 0)

Small local models give noisy quality scores; judge them with a larger model.
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from src.services.eval import EvalContext, GoldenSetError, load_golden_set, run_golden_set
from src.services.eval.compare import ReportError, check_report, compare_reports, format_comparison
from src.services.eval.judge import LLMGroundednessJudge

EXIT_OK, EXIT_PERMISSION_FAILURE, EXIT_USAGE = 0, 1, 2


def build_context() -> EvalContext:
    """The collaborators the API itself is built from."""
    from src.config import get_settings
    from src.db.factory import make_database
    from src.services.embeddings.factory import make_embeddings_service
    from src.services.opensearch.factory import make_opensearch_client
    from src.services.providers.llm.factory import make_llm_provider, make_provider_registry
    from src.services.rerank.factory import make_reranker

    settings = get_settings()
    database = make_database()
    return EvalContext(
        settings=settings,
        open_session=database.get_session,
        opensearch=make_opensearch_client(),
        embeddings=make_embeddings_service(),
        llm=make_llm_provider(settings),
        providers=make_provider_registry(settings),
        reranker=make_reranker(settings),
    )


def _summary(report: dict) -> str:
    metrics, permissions = report["metrics"], report["permissions"]

    def show(value: Optional[float]) -> str:
        return "n/a" if value is None else f"{value:.3f}"

    lines = [
        f"{report['golden_set']['name']} on {report['application']['slug']} (k={report['k']}, reranking {report['reranking']})",
        f"  recall@{report['k']}: {show(metrics['recall_at_k'])}   MRR: {show(metrics['mrr'])}   "
        f"cases: {metrics['cases_total']} ({metrics['cases_scored']} scored)",
    ]
    if metrics.get("groundedness") is not None:
        lines.append(f"  groundedness: {show(metrics['groundedness'])}")
    status = "PASS" if permissions["passed"] else "FAIL"
    lines.append(
        f"  permissions: {status} ({permissions['cases_checked']} checked, "
        f"{permissions['violations']} leaked, {permissions['errors']} unverifiable)"
    )
    for case in report["cases"]:
        if case["must_not_return"] and case["must_not_return"]["violations"]:
            lines.append(f"  LEAK {case['id']}: {', '.join(case['must_not_return']['violations'])}")
        elif case["error"] and case["must_not_return"] is not None:
            lines.append(f"  UNVERIFIED {case['id']}: {case['error']}")
    return "\n".join(lines)


def compare_command(args: argparse.Namespace) -> int:
    reports = []
    for path in (args.before, args.after):
        try:
            report = json.loads(path.read_text())
            check_report(report, str(path))
        except (OSError, json.JSONDecodeError, ReportError) as exc:
            print(f"Cannot read report {path}: {exc}", file=sys.stderr)
            return EXIT_USAGE
        reports.append(report)

    diff = compare_reports(*reports)
    print(json.dumps(diff, indent=2, sort_keys=True) if args.json else format_comparison(diff))
    return EXIT_OK  # a comparison is information; quality never fails a run


def main(argv: Optional[list] = None, context: Optional[EvalContext] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run a Golden set against an Application")
    run.add_argument("golden", type=Path, help="The Golden set YAML file")
    run.add_argument("--application", help="Application slug (default: the Golden set's `application`)")
    run.add_argument("--k", type=int, help="How many passages count (default: the Golden set's `k`, then the Application's top-k)")
    run.add_argument("--out", type=Path, help="Write the JSON report here instead of stdout")
    run.add_argument("--retrieval-only", action="store_true", help="Skip answer generation: score retrieval and permissions only")
    run.add_argument(
        "--groundedness",
        action="store_true",
        help="Also score whether each answer stays within its passages, judged by the configured chat model",
    )
    run.add_argument("--judge-model", help="Model that judges groundedness (default: the configured LLM__MODEL)")
    compare = sub.add_parser("compare", help="Show what changed between two reports")
    compare.add_argument("before", type=Path)
    compare.add_argument("after", type=Path)
    compare.add_argument("--json", action="store_true", help="Print the differences as JSON")
    args = parser.parse_args(argv)
    if args.command == "compare":
        return compare_command(args)

    try:
        golden = load_golden_set(args.golden)
    except GoldenSetError as exc:
        print(f"Golden set error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    slug = args.application or golden.application
    if not slug:
        print("No application given: pass --application or set `application:` in the Golden set.", file=sys.stderr)
        return EXIT_USAGE
    if args.k is not None and args.k < 1:
        print("--k must be at least 1", file=sys.stderr)
        return EXIT_USAGE

    if context is None and any(case.principal for case in golden.cases):
        from src.config import get_settings

        if get_settings().auth.mode == "disabled":
            print(
                "warning: AUTH__MODE is disabled, so every caller is unrestricted and cases naming a principal "
                "do not test grants. Run permission suites against a deployment with authentication enabled.",
                file=sys.stderr,
            )
    try:
        context = context or build_context()
        if args.groundedness and context.judge is None:
            context.judge = LLMGroundednessJudge(context.llm, args.judge_model or context.settings.default_llm_model)
        report = asyncio.run(
            run_golden_set(
                golden,
                slug,
                context,
                k=args.k,
                answer=not args.retrieval_only,
                source_path=args.golden,
            )
        )
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n")
    else:
        print(text)
    print(_summary(report), file=sys.stderr)
    return EXIT_OK if report["permissions"]["passed"] else EXIT_PERMISSION_FAILURE


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
