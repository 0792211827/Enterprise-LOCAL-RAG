"""The shipped dashboards and alert rules, checked offline: valid, provisioned from files, and built only on metrics the
platform defines and documents, so a renamed metric cannot silently break one of them."""

import json
import re
from pathlib import Path

import pytest
import yaml
from src.services.telemetry.catalogue import CATALOGUE

ROOT = Path(__file__).resolve().parents[3]
GRAFANA = ROOT / "telemetry" / "grafana"
CATALOGUE_DOC = ROOT / "docs" / "telemetry-metrics.md"
SERIES = re.compile(r"\brag_[a-z0-9_]+\b")
DOC_ROW = re.compile(
    r"^\| `(?P<name>[^`]+)` \| `(?P<series>[^`]+)` \| (?P<kind>\w+) \| `(?P<unit>[^`]+)` \| (?P<labels>[^|]+) \|", re.MULTILINE
)


def dashboards() -> dict:
    return {path.stem: json.loads(path.read_text()) for path in sorted((GRAFANA / "dashboards").glob("*.json"))}


def queries(dashboard: dict) -> list:
    return [target["expr"] for panel in dashboard["panels"] for target in panel.get("targets", [])]


def alert_rules() -> list:
    document = yaml.safe_load((GRAFANA / "provisioning" / "alerting" / "rules.yml").read_text())
    return [rule for group in document["groups"] for rule in group["rules"]]


def documented() -> dict:
    """The catalogue in docs/telemetry-metrics.md: OpenTelemetry name to its row."""
    return {m["name"]: m.groupdict() for m in DOC_ROW.finditer(CATALOGUE_DOC.read_text())}


def defined_series() -> set:
    return {series for spec in CATALOGUE for series in spec.prometheus_series}


def documented_series() -> set:
    names = {row["series"] for row in documented().values()}
    return {series for spec in CATALOGUE if spec.prometheus_name in names for series in spec.prometheus_series}


class TestMetricsCatalogueDocument:
    def test_it_lists_exactly_the_metrics_the_platform_defines(self):
        assert set(documented()) == {spec.name for spec in CATALOGUE}

    @pytest.mark.parametrize("spec", CATALOGUE, ids=lambda spec: spec.name)
    def test_each_row_matches_the_definition(self, spec):
        row = documented()[spec.name]
        assert row["series"] == spec.prometheus_name
        assert row["kind"] == spec.kind
        assert row["unit"] == spec.unit
        assert re.findall(r"`(\w+)`", row["labels"]) == list(spec.labels)


class TestDashboards:
    def test_there_are_three_each_with_a_stable_uid(self):
        boards = dashboards()
        assert set(boards) == {"rag-health", "rag-capacity", "rag-usage"}
        for name, board in boards.items():
            assert board["uid"] == name and board["title"].startswith("RAG platform: ")
            assert board["editable"] is False

    def test_the_health_dashboard_shows_request_error_latency_and_dependency_health(self):
        titles = " | ".join(panel["title"] for panel in dashboards()["rag-health"]["panels"]).lower()
        for wanted in ("request rate", "error rate", "p95 latency per route", "dependency success ratio"):
            assert wanted in titles

    def test_the_capacity_dashboard_shows_tokens_first_token_dependencies_ingestion_and_connectors(self):
        titles = " | ".join(panel["title"] for panel in dashboards()["rag-capacity"]["panels"]).lower()
        for wanted in ("tokens", "time to first token", "dependency latency", "ingestion stage duration", "connector runs"):
            assert wanted in titles

    def test_the_usage_dashboard_shows_questions_retrieval_empty_results_refusals_and_cache(self):
        titles = " | ".join(panel["title"] for panel in dashboards()["rag-usage"]["panels"]).lower()
        for wanted in ("questions", "searches", "empty results", "out-of-scope", "cache hit rate"):
            assert wanted in titles

    def test_every_panel_queries_the_provisioned_prometheus(self):
        for board in dashboards().values():
            for panel in board["panels"]:
                for target in panel.get("targets", []):
                    assert panel["datasource"]["uid"] == target["datasource"]["uid"] == "prometheus"

    def test_panels_do_not_overlap(self):
        for name, board in dashboards().items():
            cells = set()
            for panel in board["panels"]:
                grid = panel["gridPos"]
                for x in range(grid["x"], grid["x"] + grid["w"]):
                    for y in range(grid["y"], grid["y"] + grid["h"]):
                        assert (x, y) not in cells, f"{name}: {panel['title']} overlaps another panel"
                        cells.add((x, y))

    def test_dashboards_are_provisioned_from_files(self):
        provider = yaml.safe_load((GRAFANA / "provisioning" / "dashboards" / "dashboards.yml").read_text())["providers"][0]
        assert provider["type"] == "file"


class TestEveryReferencedMetricIsDefinedAndDocumented:
    def test_dashboards(self):
        for name, board in dashboards().items():
            used = {series for expr in queries(board) for series in SERIES.findall(expr)}
            assert used, f"{name} queries no platform metric"
            assert used <= defined_series(), f"{name} queries undefined metrics: {sorted(used - defined_series())}"
            assert used <= documented_series(), f"{name} queries undocumented metrics: {sorted(used - documented_series())}"

    def test_alert_rules(self):
        for rule in alert_rules():
            used = {series for expr in _rule_queries(rule) for series in SERIES.findall(expr)}
            assert used, f"{rule['title']} queries no platform metric"
            assert used <= defined_series(), f"{rule['title']}: undefined metrics {sorted(used - defined_series())}"
            assert used <= documented_series(), f"{rule['title']}: undocumented metrics {sorted(used - documented_series())}"


def _rule_queries(rule: dict) -> list:
    return [step["model"]["expr"] for step in rule["data"] if step["datasourceUid"] == "prometheus"]


class TestAlertRules:
    def test_the_four_rules_exist(self):
        assert {rule["uid"] for rule in alert_rules()} == {
            "rag-api-error-rate",
            "rag-api-p95-latency",
            "rag-dependency-failing",
            "rag-ingestion-failing",
        }

    def test_each_rule_is_a_query_a_reduction_and_a_threshold_that_fires_it(self):
        for rule in alert_rules():
            steps = {step["refId"]: step for step in rule["data"]}
            assert rule["condition"] == "C" and set(steps) == {"A", "B", "C"}, rule["title"]
            assert steps["A"]["datasourceUid"] == "prometheus"
            assert steps["B"]["model"]["type"] == "reduce" and steps["B"]["model"]["expression"] == "A"
            assert steps["C"]["model"]["type"] == "threshold" and steps["C"]["model"]["expression"] == "B"

    def test_thresholds_use_only_evaluators_grafana_accepts(self):
        # Grafana rejects the whole rule (and logs it) for any other evaluator, such as gte.
        for rule in alert_rules():
            for condition in rule["data"][2]["model"]["conditions"]:
                assert condition["evaluator"]["type"] in {"gt", "lt", "within_range", "outside_range"}, rule["title"]

    def test_no_data_is_normal_and_a_failing_query_is_an_error(self):
        for rule in alert_rules():
            assert rule["noDataState"] == "OK" and rule["execErrState"] == "Error", rule["title"]

    def test_every_rule_says_what_happened(self):
        for rule in alert_rules():
            assert rule["annotations"]["summary"] and rule["annotations"]["description"], rule["title"]

    def test_no_contact_point_notification_policy_or_mute_timing_is_shipped(self):
        shipped = list((GRAFANA / "provisioning" / "alerting").iterdir())
        assert [path.name for path in shipped] == ["rules.yml"]
        document = yaml.safe_load(shipped[0].read_text())
        assert not {"contactPoints", "policies", "muteTimes", "templates"} & set(document)
        for rule in alert_rules():
            assert "notification_settings" not in rule, "a rule must not assume a destination"

    def test_the_dependency_rule_names_the_failing_dependency(self):
        rule = next(r for r in alert_rules() if r["uid"] == "rag-dependency-failing")
        assert "sum by (dependency)" in _rule_queries(rule)[0]
        assert ">= 5" in _rule_queries(rule)[0], "a handful of calls must not be enough to fire"
        assert "$labels.dependency" in rule["annotations"]["summary"]
