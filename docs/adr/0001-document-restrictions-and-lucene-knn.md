# Document restrictions narrow Knowledge Base Grants; kNN moves to lucene

Status: accepted. Resolves the open engine decision in `docs/decisions/0004-opensearch-and-knn-engine.md` and un-defers roadmap Phase 12.

An admin can attach a **Document restriction** (a list of Principals, same vocabulary as Grants) to a Document by hand. A caller reads a restricted Document only if they hold a Grant on its Knowledge Base **and** match at least one restriction Principal; restrictions can never widen access, and unrestricted Documents inherit the Grant. Connectors do not populate restrictions for now.

Pre-filtering by restriction needs the kNN field on an engine that filters before ANN search, so the chunk index moves from `nmslib` (post-filter, under-returns under narrow scopes) to `lucene`, via a new index and an alias-swap reindex CLI. The same CLI serves later embedding-model changes. `faiss` was the alternative; lucene was chosen for operational simplicity and because it can be revisited by reindexing.
