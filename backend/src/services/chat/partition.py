"""Large Attachments: a document over the prompt budget is chunked and embedded into a private partition of the chunk
index, and each question gets its best passages instead of the whole file.

The partition is the same index the Knowledge Bases use, kept apart by ``conversation_id`` / ``owner_subject`` fields:
no Knowledge Base search can return these chunks, and a partition search needs both keys (see ``OpenSearchClient``).
Deleting an Attachment, its Conversation, or the retention purge removes the chunks.
"""

import logging
from typing import Any, Dict, List

from src.services.indexing.text_chunker import TextChunker

from .attachments import AttachmentError

logger = logging.getLogger(__name__)


async def index_partition(
    opensearch: Any,
    embeddings: Any,
    chunking: Any,
    owner_subject: str,
    conversation_id: str,
    attachment_id: str,
    filename: str,
    text: str,
) -> int:
    """Chunk ``text``, embed it and write it to the Conversation's partition. Returns the number of chunks.

    :raises AttachmentError: 503 if the chunks could not be written (nothing is left behind), 422 if there is no text.
    """
    chunker = TextChunker(chunking.chunk_size, chunking.overlap_size, chunking.min_chunk_size)
    chunks = chunker.chunk_text(text, attachment_id)
    if not chunks:
        raise AttachmentError(422, "no_text", "No text could be extracted from the file (a scanned document, perhaps).")
    try:
        vectors = await embeddings.embed_passages([chunk.text for chunk in chunks])
        model_name = getattr(embeddings, "model_name", None)
        payload = [
            {
                "chunk_data": {
                    "attachment_id": attachment_id,
                    "conversation_id": conversation_id,
                    "owner_subject": owner_subject,
                    "chunk_index": chunk.metadata.chunk_index,
                    "chunk_text": chunk.text,
                    "chunk_word_count": chunk.metadata.word_count,
                    "start_char": chunk.metadata.start_char,
                    "end_char": chunk.metadata.end_char,
                    "title": filename,
                    "embedding_model": model_name if isinstance(model_name, str) else None,
                },
                "embedding": vector,
            }
            for chunk, vector in zip(chunks, vectors)
        ]
        opensearch.bulk_index_chunks(payload)
    except Exception as exc:  # noqa: BLE001 - the user gets one clear message; the cause goes to the log by type only
        logger.error("Indexing an Attachment failed: %s", type(exc).__name__)
        try:
            opensearch.delete_attachment_chunks(conversation_id, attachment_id)
        except Exception:  # noqa: BLE001 - nothing more can be done here; the Conversation's own delete will retry
            logger.warning("Could not clean up the partial partition of a failed Attachment")
        raise AttachmentError(
            503,
            "index_failed",
            "This document is too long to include in full and could not be indexed for searching right now. "
            "Please try again in a moment.",
        )
    return len(chunks)


async def find_passages(
    opensearch: Any, embeddings: Any, query: str, owner_subject: str, conversation_id: str, size: int
) -> List[Dict[str, Any]]:
    """The best passages of the Conversation's large Attachments for ``query``, only ever from the owner's own partition."""
    vector = await embeddings.embed_query(query)
    result = opensearch.search_attachment_passages(query, vector, owner_subject, conversation_id, size)
    return result.get("hits", [])
