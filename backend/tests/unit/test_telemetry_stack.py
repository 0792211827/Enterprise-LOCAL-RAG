"""The shipped telemetry stack, checked offline: Compose profile, hardened overlay, Collector, Prometheus, Tempo, Grafana.

Nothing here needs Docker, a running Collector, Prometheus, Tempo or Grafana.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
STACK = ("otel-collector", "prometheus", "tempo", "grafana")
LOOPBACK = "127.0.0.1"


class ComposeLoader(yaml.SafeLoader):
    """Compose's ``!reset`` and ``!override`` tags, kept visible as ``{"!reset": ...}``."""


for _tag in ("!reset", "!override"):
    ComposeLoader.add_constructor(
        _tag,
        lambda loader, node, tag=_tag: {tag: loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode) else None},
    )


def load(relative: str) -> dict:
    return yaml.load((ROOT / relative).read_text(), Loader=ComposeLoader)


def environment(service: dict) -> dict:
    """A service's ``environment`` as a dict, whether Compose's list or map form."""
    env = service.get("environment", [])
    if isinstance(env, dict):
        return {k: str(v) for k, v in env.items()}
    return dict(item.split("=", 1) for item in env)


@pytest.fixture(scope="module")
def compose() -> dict:
    return load("compose.yml")["services"]


@pytest.fixture(scope="module")
def hardened() -> dict:
    return load("compose.hardened.yml")["services"]


class TestComposeProfile:
    def test_the_four_services_exist_only_in_the_telemetry_profile(self, compose):
        for name in STACK:
            assert compose[name]["profiles"] == ["telemetry"], name

    def test_nothing_outside_the_profile_depends_on_the_stack(self, compose):
        for name, service in compose.items():
            if name in STACK:
                continue
            depends = service.get("depends_on", {})
            assert not set(STACK) & set(depends), f"{name} would start the telemetry stack"

    def test_every_published_port_binds_to_localhost(self, compose):
        for name in STACK:
            for port in compose[name].get("ports", []):
                assert str(port).startswith(f"{LOOPBACK}:"), f"{name} publishes {port} beyond localhost"

    def test_images_are_pinned(self, compose):
        for name in STACK:
            image = compose[name]["image"]
            assert ":" in image and not image.endswith(":latest"), f"{name} uses {image}"

    def test_the_api_and_the_connector_send_to_the_collector(self, compose):
        for name in ("api", "connector"):
            assert environment(compose[name])["OTEL__ENDPOINT"] == "${OTEL_COLLECTOR_ENDPOINT:-http://otel-collector:4318}", name

    def test_the_connector_reports_as_its_own_service_and_is_off_by_default(self, compose):
        env = environment(compose["connector"])
        assert env["OTEL__SERVICE_NAME"] == "rag-connector"
        assert env["OTEL__ENABLED"].endswith(":-false}")


class TestGrafanaIsLockedDown:
    def test_no_anonymous_access_and_no_sign_up(self, compose):
        env = environment(compose["grafana"])
        assert env["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
        assert env["GF_USERS_ALLOW_SIGN_UP"] == "false"

    def test_the_password_comes_from_env_with_no_default(self, compose):
        env = environment(compose["grafana"])
        assert env["GF_SECURITY_ADMIN_PASSWORD"] == "${GRAFANA_ADMIN_PASSWORD:-}"

    def test_grafana_refuses_to_start_without_a_password(self, compose):
        script = " ".join(compose["grafana"]["entrypoint"])
        assert '[ -z "$$GF_SECURITY_ADMIN_PASSWORD" ]' in script
        assert "exit 1" in script and script.rstrip().endswith("exec /run.sh")

    def test_the_password_is_documented_and_empty_in_env_example(self):
        env = (ROOT / ".env.example").read_text()
        assert re.search(r"^GRAFANA_ADMIN_PASSWORD=$", env, re.MULTILINE)

    def test_a_required_variable_is_not_used_because_it_would_break_plain_compose_up(self, compose):
        # Compose interpolates every service, profile or not, so `${VAR:?}` would fail a plain `docker compose up`.
        assert "${GRAFANA_ADMIN_PASSWORD:?" not in (ROOT / "compose.yml").read_text()


class TestRetention:
    def test_metrics_are_kept_15_days_by_default_and_it_is_settable(self, compose):
        assert "--storage.tsdb.retention.time=${PROMETHEUS_RETENTION:-15d}" in compose["prometheus"]["command"]

    def test_traces_are_kept_3_days_by_default_and_it_is_settable(self, compose):
        assert environment(compose["tempo"])["TEMPO_RETENTION"] == "${TEMPO_RETENTION:-72h}"
        assert "-config.expand-env=true" in compose["tempo"]["command"]
        tempo = load("telemetry/tempo.yml")
        assert tempo["compactor"]["compaction"]["block_retention"] == "${TEMPO_RETENTION:-72h}"

    def test_both_are_documented_in_env_example(self):
        env = (ROOT / ".env.example").read_text()
        assert re.search(r"^PROMETHEUS_RETENTION=15d$", env, re.MULTILINE)
        assert re.search(r"^TEMPO_RETENTION=72h$", env, re.MULTILINE)


class TestHardenedOverlayParity:
    def test_the_collector_prometheus_and_tempo_are_not_published(self, hardened):
        for name in ("otel-collector", "prometheus", "tempo"):
            assert hardened[name]["ports"] == {"!reset": []}, name

    def test_grafana_is_published_on_the_bind_address_only(self, hardened):
        (port,) = hardened["grafana"]["ports"]["!override"]
        assert port.startswith("${RAG_BIND_ADDRESS:-127.0.0.1}:")

    def test_grafana_keeps_every_security_setting_of_the_base_file(self, compose, hardened):
        base, overlay = environment(compose["grafana"]), environment(hardened["grafana"])
        for key in ("GF_SECURITY_ADMIN_PASSWORD", "GF_AUTH_ANONYMOUS_ENABLED", "GF_USERS_ALLOW_SIGN_UP"):
            assert overlay[key] == base[key], key

    def test_every_service_of_the_stack_is_covered(self, hardened):
        assert set(STACK) <= set(hardened)

    def test_the_overlay_does_not_widen_the_profile(self, hardened):
        for name in STACK:
            assert "profiles" not in hardened[name]


class TestCollectorConfig:
    @pytest.fixture(scope="class")
    def collector(self) -> dict:
        return load("telemetry/otel-collector.yml")

    def test_it_receives_otlp_http_on_the_published_port(self, collector, compose):
        assert collector["receivers"]["otlp"]["protocols"]["http"]["endpoint"] == "0.0.0.0:4318"
        assert compose["otel-collector"]["ports"] == ["127.0.0.1:4318:4318"]

    def test_every_pipeline_uses_only_what_is_defined(self, collector):
        pipelines = collector["service"]["pipelines"]
        assert set(pipelines) == {"metrics", "traces"}
        for name, pipeline in pipelines.items():
            for receiver in pipeline["receivers"]:
                assert receiver in collector["receivers"], (name, receiver)
            for processor in pipeline["processors"]:
                assert processor in collector["processors"], (name, processor)
            for exporter in pipeline["exporters"]:
                assert exporter in collector["exporters"], (name, exporter)

    def test_metrics_go_to_prometheus_and_traces_to_tempo(self, collector):
        pipelines = collector["service"]["pipelines"]
        assert pipelines["metrics"]["exporters"] == ["prometheus"]
        assert pipelines["traces"]["exporters"] == ["otlp/tempo"]

    def test_the_memory_limiter_runs_first(self, collector):
        for pipeline in collector["service"]["pipelines"].values():
            assert pipeline["processors"][0] == "memory_limiter"

    def test_nothing_is_sampled_or_dropped_in_the_collector(self, collector):
        assert not {"probabilistic_sampler", "tail_sampling", "filter"} & set(collector["processors"])

    def test_the_collector_mounts_its_own_config(self, compose):
        service = compose["otel-collector"]
        assert "./telemetry/otel-collector.yml:/etc/otelcol/config.yaml:ro" in service["volumes"]
        assert "--config=/etc/otelcol/config.yaml" in service["command"]

    def test_the_sdk_endpoint_the_platform_appends_paths_to_matches_the_receiver(self):
        # The API posts to {OTEL__ENDPOINT}/v1/traces and /v1/metrics, which the OTLP/HTTP receiver serves.
        source = (ROOT / "backend/src/services/telemetry/factory.py").read_text()
        assert "/v1/traces" in source and "/v1/metrics" in source


class TestPrometheusConfig:
    def test_it_scrapes_the_collectors_exporter(self):
        prometheus = load("telemetry/prometheus.yml")
        collector = load("telemetry/otel-collector.yml")
        port = collector["exporters"]["prometheus"]["endpoint"].rsplit(":", 1)[1]
        targets = [t for job in prometheus["scrape_configs"] for c in job["static_configs"] for t in c["targets"]]
        assert targets == [f"otel-collector:{port}"]

    def test_the_resource_name_stays_a_label_so_the_api_and_connector_are_apart(self):
        collector = load("telemetry/otel-collector.yml")
        assert collector["exporters"]["prometheus"]["resource_to_telemetry_conversion"]["enabled"] is True

    def test_its_config_is_mounted(self, compose):
        assert "./telemetry/prometheus.yml:/etc/prometheus/prometheus.yml:ro" in compose["prometheus"]["volumes"]


class TestTempoConfig:
    def test_it_receives_otlp_grpc_where_the_collector_sends(self):
        tempo = load("telemetry/tempo.yml")
        collector = load("telemetry/otel-collector.yml")
        listen = tempo["distributor"]["receivers"]["otlp"]["protocols"]["grpc"]["endpoint"].rsplit(":", 1)[1]
        assert collector["exporters"]["otlp/tempo"]["endpoint"] == f"tempo:{listen}"

    def test_traces_are_stored_on_the_mounted_volume(self, compose):
        tempo = load("telemetry/tempo.yml")
        assert any(volume.endswith(":/var/tempo") for volume in compose["tempo"]["volumes"])
        trace = tempo["storage"]["trace"]
        assert trace["wal"]["path"].startswith("/var/tempo/") and trace["local"]["path"].startswith("/var/tempo/")


class TestGrafanaProvisioning:
    def test_prometheus_and_tempo_are_provisioned_at_the_addresses_of_the_stack(self):
        sources = {s["uid"]: s for s in load("telemetry/grafana/provisioning/datasources/datasources.yml")["datasources"]}
        assert sources["prometheus"]["type"] == "prometheus" and sources["prometheus"]["url"] == "http://prometheus:9090"
        assert sources["tempo"]["type"] == "tempo" and sources["tempo"]["url"] == "http://tempo:3200"
        assert sources["prometheus"]["isDefault"] is True

    def test_the_tempo_url_matches_its_http_port(self):
        assert load("telemetry/tempo.yml")["server"]["http_listen_port"] == 3200

    def test_the_provisioning_and_dashboard_directories_are_mounted_where_grafana_reads_them(self, compose):
        volumes = compose["grafana"]["volumes"]
        assert "./telemetry/grafana/provisioning:/etc/grafana/provisioning:ro" in volumes
        assert "./telemetry/grafana/dashboards:/var/lib/grafana/dashboards:ro" in volumes
        provider = load("telemetry/grafana/provisioning/dashboards/dashboards.yml")["providers"][0]
        assert provider["options"]["path"] == "/var/lib/grafana/dashboards"
        assert provider["allowUiUpdates"] is False
