from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional


class LLMProvider(ABC):
    """Abstract interface for text-generation (LLM) providers.

    Providers expose a uniform async surface so the RAG orchestration and
    agentic engines never depend on how a model is hosted. Implementations may
    target a local Ollama server, any OpenAI-compatible endpoint (vLLM, NIM,
    SGLang, LocalAI, ...), or future local inference servers.
    """

    #: Default model identifier used when a caller does not specify one.
    default_model: str = ""

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """Return provider/endpoint health information."""
        raise NotImplementedError

    @abstractmethod
    async def list_models(self) -> List[Dict[str, Any]]:
        """List models available at the endpoint."""
        raise NotImplementedError

    @abstractmethod
    async def generate(self, model: str, prompt: str, stream: bool = False, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """Generate a completion for a raw prompt."""
        raise NotImplementedError

    @abstractmethod
    def generate_stream(self, model: str, prompt: str, **kwargs: Any) -> AsyncIterator[Dict[str, Any]]:
        """Stream completion chunks for a raw prompt."""
        raise NotImplementedError

    @abstractmethod
    async def generate_rag_answer(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        model: str,
        use_structured_output: bool = False,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a grounded RAG answer from retrieved chunks."""
        raise NotImplementedError

    @abstractmethod
    def generate_rag_answer_stream(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        model: str,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream a grounded RAG answer from retrieved chunks."""
        raise NotImplementedError
