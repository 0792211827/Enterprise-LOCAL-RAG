# Evaluating an Application

The eval command scores an Application against a **Golden set**: a versioned YAML file of questions and the passages that must, or must not, come back. It runs each question through the same scope resolver and answer path production uses, so a result is what a real caller would get.

It does three jobs:

- **Quality**: recall@k and mean reciprocal rank (MRR) over the cases that name expected content, and optionally a groundedness score.
- **Permissions**: cases that name a Principal run as that caller, and `must_not_return` content makes the command fail. The Golden set doubles as a permissions regression suite for CI.
- **Comparison**: two reports can be diffed, for example before and after enabling the reranker.

## The Golden set

```yaml
version: 1
name: hr-handbook
application: hr-assistant        # optional default for --application
k: 5                             # optional default for --k

cases:
  - id: annual-leave             # optional; defaults to case-N. Must be unique
    question: How many days of annual leave do employees get?
    expected:                    # optional; scored for recall@k and MRR
      documents: ["Leave Policy"]  # a Document title or id
      passages: ["25 days"]        # text that must appear in a retrieved chunk
    must_not_return:             # optional; any match fails the run
      documents: ["Salary Bands"]
      passages: ["salary band"]
    principal:                   # optional; run as this caller
      subject: alice
      groups: ["staff"]
      roles: ["rag-user"]
```

See [`evals/example-golden-set.yaml`](../evals/example-golden-set.yaml). Matching is case-insensitive; a passage matches by substring of the chunk text. Unknown keys are rejected so a typo such as `must_not_retun` cannot silently turn a permission check into nothing. Keep Golden sets in the repo (or a mounted path) and review changes to them like code.

### Principals

A case with a `principal` is resolved by the production scope resolver: Knowledge Base Grants on its `subject`, `groups` and `roles` decide what it may search (`roles` are IdP role values, like a token's role claim). A case **without** a principal runs as the Application itself, as when it is called with its own API key: limited only by the Knowledge Bases attached to it.

The resolver follows your deployment's settings. With `AUTH__MODE=disabled` every caller is unrestricted, so a `must_not_return` case for a named Principal will report a leak: that is the truth about such a deployment. Run permission suites against a deployment with authentication enabled.

## Running it

```bash
# from backend/, with the stack's services reachable
python -m src.cli.eval run ../evals/example-golden-set.yaml --application hr-assistant --out report.json

# or against the running stack (the file is copied into the container)
make eval GOLDEN=evals/example-golden-set.yaml APP=hr-assistant
make eval GOLDEN=... APP=... K=5 RETRIEVAL_ONLY=1 GROUNDEDNESS=1 REPORT=before.json
```

| Option | Meaning |
|---|---|
| `--application SLUG` | The Application to score (default: the file's `application`) |
| `--k N` | How many passages count. Default: the file's `k`, then the Application's top-k |
| `--out FILE` | Write the JSON report here instead of stdout |
| `--retrieval-only` | Skip answer generation. Fast: scores retrieval and permissions only |
| `--groundedness` | Also score each answer (see below) |
| `--judge-model NAME` | The model that judges groundedness (default `LLM__MODEL`) |

A summary goes to stderr. If reranking is enabled (`RERANK__ENABLED`), the run uses it, and the report says so.

### Exit status

| Code | Meaning |
|---|---|
| 0 | Every `must_not_return` check passed. **Quality scores never change this** |
| 1 | A `must_not_return` case leaked content, or could not be verified (for example the index was down) |
| 2 | The Golden set or arguments are unusable (invalid file, unknown Application) |

Gate CI on this code. Do not gate on recall, MRR or groundedness: they depend on the model and are reported, not enforced.

## The report

The report is JSON with a stable shape (`report_version`), keys sorted, so two runs diff cleanly. Top level: `golden_set` (name, version, content hash), `application`, `k`, `reranking`, `metrics`, `permissions` and `cases`.

```json
{
  "metrics": {"recall_at_k": 0.667, "mrr": 0.667, "groundedness": null,
              "cases_total": 3, "cases_scored": 3},
  "permissions": {"cases_checked": 1, "violations": 0, "errors": 0, "passed": true},
  "cases": [{
    "id": "annual-leave",
    "scope": {"reason": "grants", "permitted_kb_ids": ["..."], "dropped_kb_ids": []},
    "retrieved": [{"rank": 1, "document_id": "...", "title": "Leave Policy", "score": 0.9}],
    "expected": {"found": ["document: Leave Policy"], "missing": []},
    "recall_at_k": 1.0, "reciprocal_rank": 1.0,
    "must_not_return": null, "answer": "...", "error": null
  }]
}
```

- **recall@k** per case is the fraction of its expectations (documents and passages) found in the top k hits; the metric is the mean over cases that have expectations.
- **MRR** per case is 1 / the rank of the first hit matching any expectation (0 if none); the metric is the mean.
- A case that errors scores 0 for quality and is recorded under `error`.
- `must_not_return` is checked against the retrieved chunks, which is where restricted content would leak from.

## Groundedness

With `--groundedness`, the configured chat model judges whether each answer stays within the passages it was given, on a 1 to 5 scale reported as 0.0 to 1.0. A case with no answer, no retrieved passages or a judge that fails or answers unusably is recorded as `"status": "unscored"` with a reason; it is never counted as a pass and never stops the run, and the mean covers scored cases only.

**Small models give noisy scores.** A 1B model is a poor judge: expect scores that vary between runs and disagree with a human reader. Treat them as a smoke signal, and judge with a larger model (`--judge-model`) before relying on the number or comparing two runs with it.

## Comparing two reports

```bash
make eval-compare BEFORE=before.json AFTER=after.json
python -m src.cli.eval compare before.json after.json [--json]
```

It prints each metric before and after with the difference, then per-case differences (changed scores and rankings, cases only in one report). It warns if the Golden set's content changed between the reports. Use it to show whether enabling the reranker helps: run once with `RERANK__ENABLED=false` and once with `true`. The command always exits 0 for a readable pair; quality differences are information, not failures.
