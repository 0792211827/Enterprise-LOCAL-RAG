"""Legacy endpoints and health checks follow the configured LLM provider."""

from unittest.mock import AsyncMock

import pytest
import src.main as main
from src import dependencies
from src.config import LLMSettings, Settings
from src.schemas.api.ask import AskRequest


@pytest.fixture
def separate_provider():
    """An LLM provider that is *not* the Ollama client, as with LLM__PROVIDER=vllm."""
    provider = AsyncMock()
    provider.default_model = "served-model"
    provider.health_check = AsyncMock(return_value={"status": "healthy", "message": "backend ok"})
    provider.generate_rag_answer = AsyncMock(
        return_value={"answer": "From the backend", "sources": [], "confidence": "medium", "citations": []}
    )

    async def stream(query, chunks, model, system_prompt=None):
        yield {"response": "From ", "done": False}
        yield {"response": "the backend", "done": False}

    provider.generate_rag_answer_stream = stream
    original = main.app.state.llm_provider
    main.app.state.llm_provider = provider
    yield provider
    main.app.state.llm_provider = original


@pytest.fixture
def settings_for(client):
    def _use(settings: Settings):
        main.app.dependency_overrides[dependencies.get_settings] = lambda: settings

    yield _use
    main.app.dependency_overrides.pop(dependencies.get_settings, None)


class TestAnswerPathsUseTheProvider:
    async def test_ask_generates_with_the_provider_not_ollama(self, client, separate_provider):
        response = await client.post("/api/v1/ask", json={"query": "q", "model": "served-model"})

        assert response.status_code == 200
        assert response.json()["answer"] == "From the backend"
        assert separate_provider.generate_rag_answer.call_args.kwargs["model"] == "served-model"
        main.app.state.ollama_client.generate_rag_answer.assert_not_called()

    async def test_stream_generates_with_the_provider(self, client, separate_provider):
        response = await client.post("/api/v1/stream", json={"query": "q", "model": "served-model"})

        assert response.status_code == 200
        assert "From " in response.text and "the backend" in response.text

    async def test_the_agentic_service_receives_the_provider(self, client, separate_provider):
        service = dependencies.get_agentic_rag_service(
            main.app.state.opensearch_client,
            main.app.state.ollama_client,
            main.app.state.embeddings_service,
            main.app.state.langfuse_tracer,
            Settings(),
            None,
            separate_provider,
        )
        assert service.llm_provider is separate_provider


class TestDefaultModel:
    def test_requests_without_a_model_get_the_active_providers_default(self, monkeypatch):
        assert AskRequest(query="q").model == Settings().ollama_model

        monkeypatch.setenv("LLM__PROVIDER", "vllm")
        monkeypatch.setenv("LLM__MODEL", "qwen")
        assert AskRequest(query="q").model == "qwen"

    def test_an_explicit_model_always_wins(self, monkeypatch):
        monkeypatch.setenv("LLM__PROVIDER", "vllm")
        assert AskRequest(query="q", model="mine").model == "mine"


class TestHealth:
    async def test_health_reports_the_configured_llm_backend(self, client, separate_provider):
        body = (await client.get("/api/v1/health")).json()
        assert body["services"]["llm"] == {"status": "healthy", "message": "backend ok"}

    async def test_an_unhealthy_backend_degrades_the_service(self, client, separate_provider):
        separate_provider.health_check.return_value = {"status": "unhealthy", "message": "endpoint down"}
        body = (await client.get("/api/v1/health")).json()
        assert body["status"] == "degraded"
        assert body["services"]["llm"]["status"] == "unhealthy"

    async def test_ollama_absence_is_not_fatal_when_nothing_uses_it(self, client, separate_provider, settings_for):
        # The backend is vLLM and embeddings are in-process: Ollama is optional.
        from src.config import EmbeddingSettings

        settings_for(
            Settings(
                llm=LLMSettings(provider="vllm", endpoint="http://x/v1"),
                embedding=EmbeddingSettings(provider="huggingface"),
                ollama_host="http://127.0.0.1:9",
            )
        )
        body = (await client.get("/api/v1/health")).json()
        assert body["services"]["ollama"]["status"] != "healthy"
        assert body["services"]["llm"]["status"] == "healthy"
        assert body["status"] == "ok"

    async def test_ollama_absence_degrades_when_ollama_is_the_provider(self, client, settings_for):
        settings_for(Settings(ollama_host="http://127.0.0.1:9"))
        body = (await client.get("/api/v1/health")).json()
        assert body["services"]["ollama"]["status"] != "healthy"
        assert body["status"] == "degraded"

    async def test_admin_health_does_not_call_an_unhealthy_backend_healthy(self, client, separate_provider):
        # Providers report failure as a status dict, not an exception.
        separate_provider.health_check.return_value = {"status": "unhealthy", "message": "endpoint down"}
        components = (await client.get("/api/v1/system/health")).json()["components"]
        inference = next(c for c in components if c["name"] == "Inference")
        assert inference["status"] == "unhealthy"
        assert inference["detail"] == "endpoint down"

    async def test_admin_health_reports_a_healthy_backend(self, client, separate_provider):
        components = (await client.get("/api/v1/system/health")).json()["components"]
        assert next(c for c in components if c["name"] == "Inference")["status"] == "healthy"
