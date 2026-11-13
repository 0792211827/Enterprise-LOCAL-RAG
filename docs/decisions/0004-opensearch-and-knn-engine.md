# ADR-0004: Keep OpenSearch; the nmslib kNN engine is a known trade-off

- **Status:** OpenSearch retention accepted. Engine change **open**, gating document-level ACLs.
- **Context:** All chunks live in one hybrid index (BM25 + kNN, fused by an RRF search pipeline), tagged `knowledge_base_id`. The kNN field uses the `nmslib` engine, which applies filters as **post-filters**: ANN returns the global top-k, then the KB filter discards the rest, so a narrowly scoped search can under-return. `KNN_FILTER_OVERSAMPLE=10` and `KNN_MAX_CANDIDATES=1000` mitigate this (`services/opensearch/client.py`). It cannot leak data across KBs.
- **Decision:** Keep OpenSearch (no vector-database catalogue). Keep nmslib for now.
- **Open decision:** moving to `lucene` or `faiss` (efficient pre-filtering) requires a new index and a reindex with an alias swap. It should happen before document-level ACL tags (roadmap Phase 12), where filter selectivity is higher. Tied to the embedding-dimension work (roadmap Phase 2), since both change the mapping.
- **Consequences:** retrieval quality under narrow scopes is not benchmarked yet. Add a seeded recall test before changing the engine.
