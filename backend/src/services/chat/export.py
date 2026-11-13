"""Shared exchanges as Golden set candidates (the eval harness's YAML format)."""

from typing import List, Optional

import yaml

GOLDEN_VERSION = 1


def shared_sources(citations: Optional[list]) -> List[str]:
    """The distinct Document titles an answer cited, in citation order."""
    titles: List[str] = []
    for citation in citations or []:
        title = (citation.get("title") or "").strip()
        if title and title not in titles:
            titles.append(title)
    return titles


def golden_candidates_yaml(application, exchanges: List[dict]) -> str:
    """One case per shared exchange: the question, and -- only for answers rated 👍 -- the Documents it cited as the
    expected ones. A 👎 answer's citations are not known to be right, so it is exported as a bare question for an
    admin to complete. No caller identity is included.
    """
    cases = []
    for row in exchanges:
        case = {"id": f"shared-{str(row['feedback'].message_id)[:8]}", "question": row["question"]}
        sources = shared_sources(row["answer"].citations) if row["feedback"].rating == "up" else []
        if sources:
            case["expected"] = {"documents": sources}
        cases.append(case)
    document = {
        "version": GOLDEN_VERSION,
        "name": f"{application.slug}-shared-exchanges",
        "application": application.slug,
        "cases": cases,
    }
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
