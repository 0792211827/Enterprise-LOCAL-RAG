"""The system health view reports whether telemetry export works, without letting it make the platform unhealthy."""

import os

import pytest

reads_telemetry = pytest.mark.skipif(
    os.environ.get("RAG_TEST_TELEMETRY") == "off", reason="reads the in-memory telemetry, which is off in this run"
)


async def telemetry_entry(client) -> dict:
    body = (await client.get("/api/v1/system/health")).json()
    return next(c for c in body["components"] if c["name"] == "Telemetry")


@reads_telemetry
class TestTelemetryHealth:
    async def test_working_export_is_reported_as_exporting(self, client):
        assert (await telemetry_entry(client))["status"] == "exporting"

    async def test_an_unreachable_collector_is_reported_as_failing_but_not_as_unhealthy(self, client, telemetry):
        healthy_overall = (await client.get("/api/v1/system/health")).json()["status"]
        telemetry.health.failed("traces", "connection refused")

        entry = await telemetry_entry(client)
        overall = (await client.get("/api/v1/system/health")).json()["status"]

        assert entry["status"] == "failing"
        assert entry["detail"]
        assert overall == healthy_overall

    async def test_recovery_is_reported(self, client, telemetry):
        telemetry.health.failed("metrics", "connection refused")
        telemetry.health.succeeded()
        assert (await telemetry_entry(client))["status"] == "exporting"


class TestTelemetryOff:
    @pytest.fixture
    def telemetry(self):
        from src.services.telemetry import NOOP_TELEMETRY

        return NOOP_TELEMETRY

    async def test_is_reported_as_disabled(self, client):
        assert (await telemetry_entry(client))["status"] == "disabled"
