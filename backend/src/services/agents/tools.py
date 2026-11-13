import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document
from langchain_core.tools import tool
from src.services.access import RetrievalScope
from src.services.opensearch.client import OpenSearchClient
from src.services.providers.embeddings.base import EmbeddingProvider
from src.services.rerank.client import Reranker
from src.services.retrieval import retrieve

logger = logging.getLogger(__name__)

@dataclass
class RetrievalLog:
    """What the retriever tool returned during one ``AgenticRAGService.ask``.

    The graph only hands the model text, so without this the caller could not say
    what was retrieved (for the audit log and citations) or whether reranking was
    skipped. ``ask`` installs a fresh log; the tool fills in that same object from
    whichever task runs it.
    """

    hits: List[Dict[str, Any]] = field(default_factory=list)
    rerank: Optional[str] = None  # "applied", or "skipped" if any attempt's rerank failed

    def note_rerank(self, outcome: Optional[str]) -> None:
        if outcome == "skipped" or (outcome == "applied" and self.rerank is None):
            self.rerank = outcome


retrieval_log: ContextVar[Optional[RetrievalLog]] = ContextVar("agentic_retrieval_log", default=None)


def create_retriever_tool(
    opensearch_client: OpenSearchClient,
    embeddings_client: EmbeddingProvider,
    top_k: int = 3,
    use_hybrid: bool = True,
    knowledge_base_ids: Optional[List[str]] = None,
    reranker: Optional[Reranker] = None,
):
    """Create a retriever tool that wraps OpenSearch service.

    :param opensearch_client: Existing OpenSearch service
    :param embeddings_client: Existing Jina embeddings service
    :param top_k: Number of chunks to retrieve
    :param use_hybrid: Use hybrid search (BM25 + vector)
    :param knowledge_base_ids: Restrict retrieval to these knowledge bases (the
        caller's scope); None is unscoped, [] retrieves nothing
    :param reranker: optional reranker applied to the candidates the scoped search returns
    :returns: LangChain tool for retrieving document excerpts
    """

    agent_scope = (
        RetrievalScope.unrestricted("agent retriever, unscoped")
        if knowledge_base_ids is None
        else RetrievalScope(kb_ids=frozenset(knowledge_base_ids), reason="agent retriever scope")
    )

    @tool
    async def retrieve_documents(query: str) -> list[Document]:
        """Search and return relevant excerpts from the indexed documents.

        Use this tool when the user asks about anything that could be covered by
        the organisation's own documents -- policies, procedures, products,
        records or other internal knowledge.

        :param query: The search query describing what to find
        :returns: List of relevant document excerpts with metadata
        """
        logger.info(f"Retrieving documents for query: {query[:100]}...")
        logger.debug(f"Search mode: {'hybrid' if use_hybrid else 'bm25'}, top_k: {top_k}")

        # The graph's retriever holds an already-decided id list (the caller's
        # scope, narrowed to the Application); the seam applies it as a scope.
        result = await retrieve(
            opensearch_client,
            embeddings_client,
            query,
            scope=agent_scope,
            mode="hybrid" if use_hybrid else "bm25",
            size=top_k,
            reranker=reranker,
        )

        # Convert SearchHit to LangChain Document
        documents = []
        hits = result.hits
        logger.info(f"Found {len(hits)} documents from OpenSearch")
        log = retrieval_log.get()
        if log is not None:
            log.hits.extend(hits)
            log.note_rerank(result.rerank)

        for hit in hits:
            doc = Document(
                page_content=hit["chunk_text"],
                metadata={
                    "document_id": hit.get("document_id", ""),
                    "title": hit.get("title", ""),
                    "score": hit.get("score", 0.0),
                    "section": hit.get("section_title", ""),
                    "search_mode": "hybrid" if use_hybrid else "bm25",
                    "top_k": top_k,
                },
            )
            documents.append(doc)

        logger.debug(f"Converted {len(documents)} hits to LangChain Documents")
        logger.info(f"✓ Retrieved {len(documents)} document excerpts successfully")

        return documents

    return retrieve_documents
