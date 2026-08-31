import logging

from langchain_core.documents import Document
from langchain_core.tools import tool
from src.services.opensearch.client import OpenSearchClient
from src.services.providers.embeddings.base import EmbeddingProvider

logger = logging.getLogger(__name__)


def create_retriever_tool(
    opensearch_client: OpenSearchClient,
    embeddings_client: EmbeddingProvider,
    top_k: int = 3,
    use_hybrid: bool = True,
):
    """Create a retriever tool that wraps OpenSearch service.

    :param opensearch_client: Existing OpenSearch service
    :param embeddings_client: Existing Jina embeddings service
    :param top_k: Number of chunks to retrieve
    :param use_hybrid: Use hybrid search (BM25 + vector)
    :returns: LangChain tool for retrieving document excerpts
    """

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

        # Generate query embedding
        logger.debug("Generating query embedding")
        query_embedding = await embeddings_client.embed_query(query)
        logger.debug(f"Generated embedding with {len(query_embedding)} dimensions")

        # Search using OpenSearch
        logger.debug("Searching OpenSearch")
        search_results = opensearch_client.search_unified(
            query=query,
            query_embedding=query_embedding,
            size=top_k,
            use_hybrid=use_hybrid,
        )

        # Convert SearchHit to LangChain Document
        documents = []
        hits = search_results.get("hits", [])
        logger.info(f"Found {len(hits)} documents from OpenSearch")

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
