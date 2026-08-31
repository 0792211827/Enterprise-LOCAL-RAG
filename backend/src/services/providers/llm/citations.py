"""Generic citation/source helpers shared across LLM providers.

These helpers avoid any product-specific assumptions and derive citations from
generic document metadata attached to retrieved chunks.
"""
from typing import Any, Dict, List


def _chunk_source_ref(chunk: Dict[str, Any]) -> str:
    """Return the best available human-facing reference for a chunk."""
    for key in ("source_url", "url", "document_title", "title", "document_id"):
        value = chunk.get(key)
        if value:
            return str(value)
    return ""


def build_sources(chunks: List[Dict[str, Any]], limit: int = 10) -> List[str]:
    """Build a de-duplicated, ordered list of source references."""
    sources: List[str] = []
    seen = set()
    for chunk in chunks:
        ref = _chunk_source_ref(chunk)
        if ref and ref not in seen:
            seen.add(ref)
            sources.append(ref)
        if len(sources) >= limit:
            break
    return sources


def build_citations(chunks: List[Dict[str, Any]], limit: int = 5) -> List[str]:
    """Build a de-duplicated list of citation identifiers."""
    citations: List[str] = []
    seen = set()
    for chunk in chunks:
        ref = _chunk_source_ref(chunk)
        if ref and ref not in seen:
            seen.add(ref)
            citations.append(ref)
        if len(citations) >= limit:
            break
    return citations
