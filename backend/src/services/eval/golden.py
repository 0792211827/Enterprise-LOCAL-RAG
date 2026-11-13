"""The Golden set file: versioned YAML of questions and the passages that must (or must not) come back.

    version: 1
    name: hr-handbook
    application: hr-assistant        # optional default for --application
    k: 5                             # optional default for --k
    cases:
      - id: leave-days               # optional; defaults to case-N
        question: How many days of annual leave do I get?
        expected:                    # optional; scores recall@k and MRR
          documents: [Leave Policy]  # a Document title or id
          passages: ["25 days"]      # text that must appear in a retrieved chunk
        must_not_return:             # optional; any hit fails the run
          documents: [Salary Bands]
          passages: ["salary band"]
        principal:                   # optional; run as this caller
          subject: alice
          groups: [finance]
          roles: [rag-user]

Unknown keys are rejected: a typo in ``must_not_return`` would otherwise quietly
turn a permission check into nothing.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


class GoldenSetError(ValueError):
    """The Golden set file is unreadable or invalid."""


@dataclass(frozen=True)
class PrincipalSpec:
    subject: str
    groups: tuple = ()
    roles: tuple = ()


@dataclass(frozen=True)
class GoldenCase:
    id: str
    question: str
    expected_documents: tuple = ()
    expected_passages: tuple = ()
    forbidden_documents: tuple = ()
    forbidden_passages: tuple = ()
    principal: Optional[PrincipalSpec] = None

    @property
    def has_expectations(self) -> bool:
        return bool(self.expected_documents or self.expected_passages)

    @property
    def has_permission_check(self) -> bool:
        return bool(self.forbidden_documents or self.forbidden_passages)


@dataclass(frozen=True)
class GoldenSet:
    name: str
    version: str
    cases: List[GoldenCase] = field(default_factory=list)
    application: Optional[str] = None
    k: Optional[int] = None


def _only(mapping: Any, allowed: set, where: str) -> Dict[str, Any]:
    if not isinstance(mapping, dict):
        raise GoldenSetError(f"{where} must be a mapping")
    unknown = set(mapping) - allowed
    if unknown:
        raise GoldenSetError(f"{where} has unknown key(s) {sorted(unknown)}; allowed: {sorted(allowed)}")
    return mapping


def _strings(value: Any, where: str) -> tuple:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, (str, int)) and str(item).strip() for item in value):
        raise GoldenSetError(f"{where} must be a list of non-empty strings")
    return tuple(str(item).strip() for item in value)


def _parse_case(raw: Any, index: int) -> GoldenCase:
    where = f"cases[{index}]"
    raw = _only(raw, {"id", "question", "expected", "must_not_return", "principal"}, where)
    question = raw.get("question")
    if not isinstance(question, str) or not question.strip():
        raise GoldenSetError(f"{where}.question is required")

    expected = _only(raw.get("expected") or {}, {"documents", "passages"}, f"{where}.expected")
    forbidden = _only(raw.get("must_not_return") or {}, {"documents", "passages"}, f"{where}.must_not_return")

    principal = None
    if raw.get("principal") is not None:
        spec = _only(raw["principal"], {"subject", "groups", "roles"}, f"{where}.principal")
        subject = spec.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            raise GoldenSetError(f"{where}.principal.subject is required")
        principal = PrincipalSpec(
            subject.strip(),
            _strings(spec.get("groups"), f"{where}.principal.groups"),
            _strings(spec.get("roles"), f"{where}.principal.roles"),
        )

    return GoldenCase(
        id=str(raw.get("id") or f"case-{index + 1}"),
        question=question.strip(),
        expected_documents=_strings(expected.get("documents"), f"{where}.expected.documents"),
        expected_passages=_strings(expected.get("passages"), f"{where}.expected.passages"),
        forbidden_documents=_strings(forbidden.get("documents"), f"{where}.must_not_return.documents"),
        forbidden_passages=_strings(forbidden.get("passages"), f"{where}.must_not_return.passages"),
        principal=principal,
    )


def load_golden_set(path: Path) -> GoldenSet:
    """Read and validate a Golden set; raises ``GoldenSetError`` with what to fix."""
    try:
        raw = yaml.safe_load(Path(path).read_text())
    except OSError as exc:
        raise GoldenSetError(f"Cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise GoldenSetError(f"{path} is not valid YAML: {exc}") from exc

    raw = _only(raw, {"version", "name", "application", "k", "cases"}, "the Golden set")
    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        raise GoldenSetError("cases must be a non-empty list")
    cases = [_parse_case(case, index) for index, case in enumerate(cases_raw)]

    ids = [case.id for case in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise GoldenSetError(f"case ids must be unique; repeated: {duplicates}")

    k = raw.get("k")
    if k is not None and (not isinstance(k, int) or isinstance(k, bool) or k < 1):
        raise GoldenSetError("k must be a positive integer")
    return GoldenSet(
        name=str(raw.get("name") or Path(path).stem),
        version=str(raw.get("version", 1)),
        cases=cases,
        application=raw.get("application"),
        k=k,
    )
