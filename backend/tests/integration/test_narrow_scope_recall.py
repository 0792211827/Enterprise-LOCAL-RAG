"""Narrow-scope behaviour of the chunk index against a live OpenSearch, and the reindex that moves it to lucene.

Seeded and deterministic. Recall is measured against an exact brute-force nearest-neighbour search inside the scope,
for a small Knowledge Base whose chunks are a tiny fraction of the index -- the case where a post-filtering engine
(nmslib) under-returns. It was measured on nmslib before the switch (ADR-0001) and must hold on lucene.

Needs a live cluster: OPENSEARCH__HOST (default http://localhost:9200); skipped when none answers.
"""

import asyncio
import math
import os
import random
import uuid
from typing import Dict, List

import pytest
from opensearchpy import OpenSearch, helpers
from src.config import Settings
from src.services.opensearch.client import OpenSearchClient
from src.services.opensearch.index_config_hybrid import build_chunks_mapping
from src.services.opensearch.reindex import run_reindex

DIMENSION = 16
BACKGROUND_CHUNKS = 4000  # in many Knowledge Bases the caller cannot read
NARROW_CHUNKS = 40  # the caller's own, small Knowledge Base
QUERIES = 25
TOP_K = 10
HOST = os.environ.get("OPENSEARCH__HOST", "http://localhost:9200")


def _unit(rng: random.Random) -> List[float]:
    vector = [rng.gauss(0, 1) for _ in range(DIMENSION)]
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector]


def _cosine(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@pytest.fixture(scope="module")
def raw():
    cluster = OpenSearch(hosts=[HOST], use_ssl=False, verify_certs=False, ssl_show_warn=False, timeout=60)
    try:
        cluster.info()
    except Exception:
        pytest.skip(f"no OpenSearch at {HOST}")
    return cluster


@pytest.fixture
def names(raw):
    prefix = f"recall-{uuid.uuid4().hex[:8]}"
    yield prefix
    for index in raw.indices.get(index=f"{prefix}*"):
        raw.indices.delete(index=index, ignore_unavailable=True)


def _client(raw, name: str, monkeypatch) -> OpenSearchClient:
    monkeypatch.setenv("EMBEDDING__DIMENSION", str(DIMENSION))
    settings = Settings()
    client = OpenSearchClient(host=HOST, settings=settings)
    client.client = raw
    client.index_name = name
    client._engine_read_at = None
    return client


def _mapping(engine: str) -> dict:
    mapping = build_chunks_mapping(DIMENSION)
    mapping["mappings"]["properties"]["embedding"]["method"]["engine"] = engine
    if engine == "nmslib":
        mapping["mappings"]["properties"]["embedding"]["method"]["parameters"] = {"ef_construction": 512, "m": 16}
    return mapping


def _seed(raw, index: str, seed: int = 7) -> Dict[str, List[float]]:
    """4000 background chunks across other Knowledge Bases and 40 in the narrow one; returns the narrow ones' vectors."""
    rng = random.Random(seed)
    narrow: Dict[str, List[float]] = {}
    actions = []
    for n in range(BACKGROUND_CHUNKS + NARROW_CHUNKS):
        in_narrow = n % ((BACKGROUND_CHUNKS + NARROW_CHUNKS) // NARROW_CHUNKS) == 0 and len(narrow) < NARROW_CHUNKS
        vector = _unit(rng)
        chunk_id = f"chunk-{n}"
        if in_narrow:
            narrow[chunk_id] = vector
        actions.append(
            {
                "_index": index,
                "_id": chunk_id,
                "_source": {
                    "chunk_id": chunk_id,
                    "document_id": f"doc-{n}",
                    "knowledge_base_id": "kb-narrow" if in_narrow else f"kb-other-{n % 50}",
                    "chunk_text": f"passage {n}",
                    "embedding": vector,
                },
            }
        )
    helpers.bulk(raw, actions, refresh=True)
    return narrow


def _recall(client: OpenSearchClient, narrow: Dict[str, List[float]]) -> float:
    rng = random.Random(99)
    found = 0
    for _ in range(QUERIES):
        query = _unit(rng)
        exact = {i for i, _ in sorted(narrow.items(), key=lambda kv: -_cosine(query, kv[1]))[:TOP_K]}
        hits = client.search_chunks_vector(query, size=TOP_K, knowledge_base_ids=["kb-narrow"])["hits"]
        assert all(hit["knowledge_base_id"] == "kb-narrow" for hit in hits)
        found += len(exact & {hit["chunk_id"] for hit in hits})
    return found / (QUERIES * TOP_K)


def test_narrow_scope_recall_before_and_after_the_switch(raw, names, monkeypatch, record_property):
    results = {}
    for engine in ("nmslib", "lucene"):
        index = f"{names}-{engine}"
        raw.indices.create(index=index, body=_mapping(engine))
        narrow = _seed(raw, index)
        client = _client(raw, index, monkeypatch)
        assert client.knn_engine() == engine
        results[engine] = _recall(client, narrow)
        record_property(f"recall_at_{TOP_K}_{engine}", results[engine])
    print(f"narrow-scope recall@{TOP_K}: {results}")

    assert results["lucene"] >= 0.95
    assert results["lucene"] >= results["nmslib"]


def test_reindex_moves_a_live_nmslib_index_to_lucene_behind_the_alias(raw, names, monkeypatch):
    alias = f"{names}-chunks"
    raw.indices.create(index=alias, body=_mapping("nmslib"))
    narrow = _seed(raw, alias)
    client = _client(raw, alias, monkeypatch)
    assert client.knn_engine() == "nmslib"

    result = asyncio.run(run_reindex(client, dimension=DIMENSION, batch_size=500, say=lambda _: None))

    assert result.swapped and set(raw.indices.get_alias(name=alias)) == {result.target}
    assert raw.count(index=alias)["count"] == BACKGROUND_CHUNKS + NARROW_CHUNKS
    client._engine_read_at = None
    assert client.knn_engine() == "lucene"
    assert client.get_embedding_dimension() == DIMENSION
    assert client.get_index_stats()["document_count"] == BACKGROUND_CHUNKS + NARROW_CHUNKS
    assert _recall(client, narrow) >= 0.95  # search, through the alias, on the new engine
    assert client.index_chunk({"chunk_text": "written through the alias", "knowledge_base_id": "kb-narrow"}, [0.1] * DIMENSION)
