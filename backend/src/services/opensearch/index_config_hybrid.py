"""OpenSearch index configuration for hybrid search (BM25 + Vector).

This configuration supports both keyword search (BM25) and vector similarity search
using HNSW algorithm for approximate nearest neighbor search.
"""


# lucene filters before it searches, so a narrow scope (one small Knowledge Base, one Conversation) still gets a full
# set of nearest neighbours. Indices built before ADR-0001 use nmslib, which only post-filters; the reindex
# command moves them (``python -m src.cli.reindex``).
KNN_ENGINE = "lucene"


# Fields added after the first release of the mapping. They are additive, so an existing index gains them in place
# (``OpenSearchClient.ensure_additive_fields``) without a reindex.
#   conversation_id / owner_subject / attachment_id: the private partition of a Conversation's large Attachments.
#   A chunk with ``conversation_id`` belongs to a partition; Knowledge Base searches exclude every such chunk.
#   restriction_principals: the Principals a Document is restricted to (``group:finance``); absent means unrestricted.
#   locator: where the chunk came from in its source (page, slide, sheet and rows, heading path); absent on chunks
#   indexed before locators existed or cut from plain text, which a Citation then shows without a position.
#   revision / staged: which revision of a replaced Document a chunk belongs to, and whether that revision is still
#   being built (``staged: true`` chunks are hidden from search until it becomes current).
ADDITIVE_FIELDS = {
    "locator": {
        "type": "object",
        "dynamic": "strict",
        "properties": {
            "kind": {"type": "keyword"},
            "page": {"type": "integer"},
            "slide": {"type": "integer"},
            "sheet": {"type": "keyword"},
            "row_start": {"type": "integer"},
            "row_end": {"type": "integer"},
            "heading_path": {"type": "keyword"},
        },
    },
    "restriction_principals": {"type": "keyword"},
    "revision": {"type": "keyword"},
    "staged": {"type": "boolean"},
    "conversation_id": {"type": "keyword"},
    "owner_subject": {"type": "keyword"},
    "attachment_id": {"type": "keyword"},
}


def build_chunks_mapping(dimension: int) -> dict:
    """Index mapping for document chunks with vector embeddings of ``dimension``."""
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "index.knn": True,
            "index.knn.space_type": "cosinesimil",
            "analysis": {
                "analyzer": {
                    "standard_analyzer": {"type": "standard", "stopwords": "_english_"},
                    "text_analyzer": {"type": "custom", "tokenizer": "standard", "filter": ["lowercase", "stop", "snowball"]},
                }
            },
        },
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "chunk_id": {"type": "keyword"},
                # Document identifiers written by IngestionService. The mapping is
                # strict, so omitting these rejects every bulk index request with
                # strict_dynamic_mapping_exception.
                "document_id": {"type": "keyword"},
                "knowledge_base_id": {"type": "keyword"},
                "chunk_index": {"type": "integer"},
                "chunk_text": {
                    "type": "text",
                    "analyzer": "text_analyzer",
                    "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
                },
                "chunk_word_count": {"type": "integer"},
                "start_char": {"type": "integer"},
                "end_char": {"type": "integer"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": dimension,
                    "method": {
                        "name": "hnsw",  # Hierarchical Navigable Small World
                        "space_type": "cosinesimil",  # Cosine similarity
                        "engine": KNN_ENGINE,
                        "parameters": {
                            "ef_construction": 512,  # Higher value = better recall, slower indexing
                            "m": 16,  # Number of bi-directional links
                        },
                    },
                },
                "title": {
                    "type": "text",
                    "analyzer": "text_analyzer",
                    "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
                },
                "authors": {
                    "type": "text",
                    "analyzer": "standard_analyzer",
                    "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
                },
                "abstract": {"type": "text", "analyzer": "text_analyzer"},
                "categories": {"type": "keyword"},
                "published_date": {"type": "date"},
                "section_title": {"type": "keyword"},
                "embedding_model": {"type": "keyword"},
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
                **ADDITIVE_FIELDS,
            },
        },
    }


# The default (1024) mapping, kept for callers that need a fixed one.
DOCUMENT_CHUNKS_MAPPING = build_chunks_mapping(1024)


HYBRID_RRF_PIPELINE = {
    "id": "hybrid-rrf-pipeline",
    "description": "Post processor for hybrid RRF search",
    "phase_results_processors": [
        {
            "score-ranker-processor": {
                "combination": {
                    "technique": "rrf",  # Reciprocal Rank Fusion
                    "rank_constant": 60,  # Default k=60 for RRF formula: 1/(k+rank)
                }
            }
        }
    ],
}

# Alternative: Weighted average pipeline (commented out - not used by default)
# This could be used if you need explicit control over BM25 vs vector weights
# However, RRF generally provides better results without manual weight tuning
"""
HYBRID_SEARCH_PIPELINE = {
    "id": "hybrid-ranking-pipeline",
    "description": "Hybrid search pipeline using weighted average for BM25 and vector similarity",
    "phase_results_processors": [
        {
            "normalization-processor": {
                "normalization": {
                    "technique": "l2"  # L2 normalization for better score distribution
                },
                "combination": {
                    "technique": "harmonic_mean",  # Harmonic mean often works better than arithmetic
                    "parameters": {
                        "weights": [0.3, 0.7]  # 30% BM25, 70% vector similarity
                    }
                }
            }
        }
    ]
}
"""
