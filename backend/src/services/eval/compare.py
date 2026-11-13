"""Compare two eval reports: what changed between runs (say, before and after enabling the reranker)."""

from typing import Any, Dict, List, Optional

METRICS = ("recall_at_k", "mrr", "groundedness")
CASE_METRICS = ("recall_at_k", "reciprocal_rank")


class ReportError(ValueError):
    """A file is not an eval report this tool can read."""


def _delta(before: Optional[float], after: Optional[float]) -> Dict[str, Any]:
    delta = round(after - before, 6) if before is not None and after is not None else None
    return {"before": before, "after": after, "delta": delta}


def _titles(case: Dict[str, Any]) -> List[str]:
    return [hit.get("title") or hit.get("document_id") or "?" for hit in case.get("retrieved") or []]


def _groundedness(case: Dict[str, Any]) -> Optional[float]:
    return (case.get("groundedness") or {}).get("score")


def check_report(report: Any, name: str) -> None:
    if not isinstance(report, dict) or "metrics" not in report or not isinstance(report.get("cases"), list):
        raise ReportError(f"{name} is not an eval report (no metrics/cases)")


def compare_reports(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """The differences between two reports of the same Golden set. Cases are matched by id."""
    warnings: List[str] = []
    if before.get("report_version") != after.get("report_version"):
        warnings.append("the reports have different report_version values; some fields may not compare")
    if (before.get("golden_set") or {}).get("sha256") != (after.get("golden_set") or {}).get("sha256"):
        warnings.append("the reports were made from a different Golden set (its content changed); per-case results may not be comparable")

    before_cases = {c["id"]: c for c in before["cases"]}
    after_cases = {c["id"]: c for c in after["cases"]}
    changed, unchanged = [], 0
    for case_id in before_cases.keys() & after_cases.keys():
        old, new = before_cases[case_id], after_cases[case_id]
        entry: Dict[str, Any] = {"id": case_id}
        differs = False
        for metric in CASE_METRICS:
            if old.get(metric) != new.get(metric):
                entry[metric], differs = _delta(old.get(metric), new.get(metric)), True
        if _groundedness(old) != _groundedness(new):
            entry["groundedness"], differs = _delta(_groundedness(old), _groundedness(new)), True
        if _titles(old) != _titles(new):
            entry["ranking"], differs = {"before": _titles(old), "after": _titles(new)}, True
        if differs:
            changed.append(entry)
        else:
            unchanged += 1
    changed.sort(key=lambda e: [c["id"] for c in before["cases"]].index(e["id"]))

    return {
        "warnings": warnings,
        "context": {
            key: {"before": before.get(key), "after": after.get(key)}
            for key in ("k", "reranking")
            if before.get(key) != after.get(key)
        },
        "metrics": {m: _delta(before["metrics"].get(m), after["metrics"].get(m)) for m in METRICS},
        "permissions": {
            "violations": {
                "before": (before.get("permissions") or {}).get("violations"),
                "after": (after.get("permissions") or {}).get("violations"),
            }
        },
        "cases": {
            "changed": changed,
            "unchanged": unchanged,
            "only_in_before": [c["id"] for c in before["cases"] if c["id"] not in after_cases],
            "only_in_after": [c["id"] for c in after["cases"] if c["id"] not in before_cases],
        },
    }


def _fmt(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _signed(delta: Optional[float]) -> str:
    return "" if delta is None else f" ({delta:+.3f})"


def format_comparison(diff: Dict[str, Any]) -> str:
    lines = [f"warning: {w}" for w in diff["warnings"]]
    for key, values in diff["context"].items():
        lines.append(f"{key}: {values['before']} -> {values['after']}")
    for name, values in diff["metrics"].items():
        lines.append(f"{name}: {_fmt(values['before'])} -> {_fmt(values['after'])}{_signed(values['delta'])}")
    violations = diff["permissions"]["violations"]
    if violations["before"] != violations["after"]:
        lines.append(f"permission violations: {violations['before']} -> {violations['after']}")
    cases = diff["cases"]
    lines.append(f"cases: {len(cases['changed'])} changed, {cases['unchanged']} unchanged")
    for case in cases["changed"]:
        parts = [
            f"{metric} {_fmt(case[metric]['before'])} -> {_fmt(case[metric]['after'])}{_signed(case[metric]['delta'])}"
            for metric in (*CASE_METRICS, "groundedness")
            if metric in case
        ]
        if "ranking" in case:
            parts.append(f"ranking {case['ranking']['before']} -> {case['ranking']['after']}")
        lines.append(f"  {case['id']}: " + "; ".join(parts))
    if cases["only_in_before"]:
        lines.append(f"only in the first report: {', '.join(cases['only_in_before'])}")
    if cases["only_in_after"]:
        lines.append(f"only in the second report: {', '.join(cases['only_in_after'])}")
    return "\n".join(lines)
