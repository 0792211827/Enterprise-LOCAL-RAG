import os

import pytest
from src.config import Settings


def test_settings_initialization():
    """Test settings can be initialized."""
    settings = Settings()

    assert settings.app_version == "0.1.0"
    assert settings.debug is True
    assert settings.environment == "development"
    assert settings.service_name == "rag-api"


def test_settings_postgres_defaults():
    """Test PostgreSQL default configuration."""
    settings = Settings()

    assert "postgresql://" in settings.postgres_database_url
    assert settings.postgres_echo_sql is False
    assert settings.postgres_pool_size == 20
    assert settings.postgres_max_overflow == 0


def test_settings_opensearch_defaults():
    """Test OpenSearch default configuration."""
    settings = Settings()

    assert settings.opensearch.host == "http://localhost:9200"
    assert settings.opensearch.index_name == "rag-documents"


def test_settings_embedding_defaults():
    """Test local-first embedding provider defaults."""
    settings = Settings()

    assert settings.embedding.provider == "huggingface"
    assert settings.embedding.model == "BAAI/bge-m3"
    assert settings.embedding.dimension == 1024


def test_settings_llm_defaults():
    """Test LLM provider defaults."""
    settings = Settings()

    assert settings.llm.provider == "ollama"
    assert settings.vlm.enabled is False


def test_settings_ollama_defaults():
    """Test Ollama default configuration."""
    settings = Settings()

    # In Docker environment, this should be ollama service host
    expected_host = "http://ollama:11434" if "OLLAMA_HOST" not in os.environ else settings.ollama_host
    assert settings.ollama_host in ["http://localhost:11434", "http://ollama:11434"]


def test_telemetry_is_off_by_default_and_keeps_every_trace():
    settings = Settings()

    assert settings.otel.enabled is False
    assert settings.otel.trace_sample_ratio == 1.0
    assert settings.otel.service_name == "rag-api"


def test_telemetry_settings_come_from_otel_variables(monkeypatch):
    monkeypatch.setenv("OTEL__ENABLED", "true")
    monkeypatch.setenv("OTEL__ENDPOINT", "http://collector:4318")
    monkeypatch.setenv("OTEL__SERVICE_NAME", "rag-api-eu")
    monkeypatch.setenv("OTEL__TRACE_SAMPLE_RATIO", "0.25")
    monkeypatch.setenv("OTEL__EXPORT_TIMEOUT", "2.5")
    monkeypatch.setenv("OTEL__QUEUE_SIZE", "512")

    otel = Settings().otel

    assert (otel.enabled, otel.endpoint, otel.service_name) == (True, "http://collector:4318", "rag-api-eu")
    assert (otel.trace_sample_ratio, otel.export_timeout, otel.queue_size) == (0.25, 2.5, 512)


@pytest.mark.parametrize("ratio", ["-0.1", "1.5"])
def test_a_trace_sample_ratio_outside_zero_to_one_is_refused(monkeypatch, ratio):
    monkeypatch.setenv("OTEL__TRACE_SAMPLE_RATIO", ratio)

    with pytest.raises(ValueError):
        Settings()


def test_every_telemetry_setting_is_documented_in_the_env_example():
    from pathlib import Path

    from src.config import OtelSettings

    documented = (Path(__file__).parents[3] / ".env.example").read_text()

    missing = [name for name in OtelSettings.model_fields if f"OTEL__{name.upper()}=" not in documented]
    assert missing == []
