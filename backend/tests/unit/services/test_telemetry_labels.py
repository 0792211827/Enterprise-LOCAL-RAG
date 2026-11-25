import pytest
from src.services.telemetry import InMemoryTelemetry, TelemetryLabelError


def test_a_label_outside_the_allow_list_is_rejected_in_tests():
    telemetry = InMemoryTelemetry()

    with pytest.raises(TelemetryLabelError):
        telemetry.stage("generate_answer", question="what is the salary of the CEO?")


def test_a_label_outside_the_allow_list_is_dropped_in_production():
    telemetry = InMemoryTelemetry(strict=False)

    with telemetry.stage("generate_answer", question="what is the salary of the CEO?", model="llama"):
        pass

    attributes = telemetry.span_named("generate_answer").attributes
    assert attributes["model"] == "llama" and "question" not in attributes
