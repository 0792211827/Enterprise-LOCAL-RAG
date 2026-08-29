from abc import ABC, abstractmethod
from typing import Any, Dict


class VLMProvider(ABC):
    """Abstract interface for Vision-Language Model (VLM) providers.

    VLM support is optional and capability-driven: it is only used when a
    document (or ingestion step) requires multimodal understanding such as
    scanned pages, diagrams, or tables rendered as images. Implementations
    target local inference servers only.
    """

    default_model: str = ""

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """Return provider/endpoint health information."""
        raise NotImplementedError

    @abstractmethod
    async def describe_image(self, image_base64: str, prompt: str, model: str = "") -> str:
        """Describe or extract text/structure from an image.

        :param image_base64: Base64-encoded image bytes (no data-URI prefix).
        :param prompt: Instruction describing what to extract.
        :param model: Optional model override.
        :returns: The model's textual response.
        """
        raise NotImplementedError
