"""Client for a reranking Inference endpoint."""

import logging
from typing import Any, Dict, List, Optional, Protocol

import httpx

logger = logging.getLogger(__name__)


class RerankError(RuntimeError):
    """The rerank endpoint failed or answered something unusable."""


class Reranker(Protocol):
    """Reorders already-retrieved hits by relevance to a query.

    :ivar candidates: how many hits the retrieval seam should fetch for it to reorder
    """

    candidates: int

    async def rerank(self, query: str, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """``hits`` reordered, best first. Never adds, drops or alters a hit; raises ``RerankError`` on failure."""
        ...


class HTTPReranker:
    """Reranks through an endpoint speaking the common ``/v1/rerank`` shape."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: Optional[str] = None,
        candidates: int = 30,
        timeout: float = 5.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.endpoint = endpoint
        self.model = model
        self.candidates = candidates
        self.timeout = timeout
        self._transport = transport
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    async def rerank(self, query: str, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        documents = [hit.get("chunk_text", "") for hit in hits]
        payload: Dict[str, Any] = {"query": query, "documents": documents}
        if self.model:
            payload["model"] = self.model
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers, transport=self._transport) as client:
                response = await client.post(self.endpoint, json=payload)
                response.raise_for_status()
                results = response.json()["results"]
            scored = [(int(item["index"]), float(item["relevance_score"])) for item in results]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise RerankError(f"{type(exc).__name__}: {exc}") from exc

        # Only indexes of the hits that were sent: the endpoint can reorder, never widen.
        if any(not 0 <= index < len(hits) for index, _ in scored):
            raise RerankError("the endpoint returned an index that was not in the request")

        order: List[int] = []
        for index, _ in sorted(scored, key=lambda pair: pair[1], reverse=True):
            if index not in order:
                order.append(index)
        # Anything the endpoint did not score keeps its place behind the scored ones.
        order += [index for index in range(len(hits)) if index not in order]
        return [hits[index] for index in order]
