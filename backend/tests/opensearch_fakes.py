"""An in-memory stand-in for the slice of an OpenSearch cluster the reindex command drives: indices, aliases, scroll,
mget and bulk. Offline; the real cluster is exercised by the integration suite."""

import fnmatch
import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional


class FakeEmbeddings:
    model_name = "new-model"

    def __init__(self, dimension: int):
        self.dimension = dimension
        self.embedded: List[str] = []

    async def embed_passages(self, texts: List[str], batch_size: int = 100):
        self.embedded.extend(texts)
        return [[float(len(t))] * self.dimension for t in texts]

    async def embed_query(self, query: str):
        return [float(len(query))] * self.dimension


class FakeCluster:
    """Indices hold documents by ``_id``; an alias maps to the indices it points at."""

    def __init__(self):
        self.indices_data: Dict[str, Dict[str, Any]] = {}
        self.aliases: Dict[str, List[str]] = {}
        self.bulk_calls = 0
        self.crash_on_bulk: Optional[int] = None  # raise on this (1-based) bulk call, as if the process died
        self.reject_ids: set = set()  # bulk items for these ids come back as errors
        self.scrolls: Dict[str, List[Dict[str, Any]]] = {}
        self.indices = SimpleNamespace(
            exists=self._exists,
            exists_alias=lambda name: name in self.aliases,
            get_alias=self._get_alias,
            get=self._get,
            get_mapping=self._get_mapping,
            create=self._create,
            refresh=lambda index: None,
            update_aliases=self._update_aliases,
            delete=lambda index: self.indices_data.pop(index, None),
        )

    # -- test setup ------------------------------------------------------------------------------------------ #
    def add_index(self, name: str, dimension: int, engine: str, docs: Dict[str, Dict[str, Any]], alias: Optional[str] = None):
        self.indices_data[name] = {"dimension": dimension, "engine": engine, "docs": dict(docs)}
        if alias:
            self.aliases.setdefault(alias, []).append(name)

    def resolve(self, name: str) -> str:
        return self.aliases[name][0] if name in self.aliases else name

    def docs(self, name: str) -> Dict[str, Dict[str, Any]]:
        return self.indices_data[self.resolve(name)]["docs"]

    # -- the API surface ------------------------------------------------------------------------------------- #
    def _exists(self, index):
        return index in self.indices_data or index in self.aliases

    def _get_alias(self, name):
        return {index: {"aliases": {name: {}}} for index in self.aliases[name]}

    def _get(self, index):
        return {name: {} for name in self.indices_data if fnmatch.fnmatch(name, index)}

    def _get_mapping(self, index):
        data = self.indices_data[self.resolve(index)]
        return {
            self.resolve(index): {
                "mappings": {
                    "properties": {
                        "embedding": {"type": "knn_vector", "dimension": data["dimension"], "method": {"engine": data["engine"]}}
                    }
                }
            }
        }

    def _create(self, index, body):
        embedding = body["mappings"]["properties"]["embedding"]
        self.indices_data[index] = {
            "dimension": embedding["dimension"],
            "engine": embedding["method"]["engine"],
            "docs": {},
        }

    def _update_aliases(self, body):
        for action in body["actions"]:
            if "remove_index" in action:
                name = action["remove_index"]["index"]
                self.indices_data.pop(name)
            elif "remove" in action:
                spec = action["remove"]
                self.aliases[spec["alias"]].remove(spec["index"])
                if not self.aliases[spec["alias"]]:
                    del self.aliases[spec["alias"]]
            elif "add" in action:
                spec = action["add"]
                assert spec["alias"] not in self.indices_data, "an alias cannot share a name with an index"
                self.aliases.setdefault(spec["alias"], []).append(spec["index"])

    def count(self, index, body=None):
        return {"count": len(self.docs(index))}

    def search(self, index, body=None, scroll=None, size=10, **_):
        hits = [{"_id": _id, "_source": dict(src)} for _id, src in self.docs(index).items()]
        token = uuid.uuid4().hex
        self.scrolls[token] = hits[size:]
        return {"_scroll_id": token, "hits": {"hits": hits[:size]}}

    def scroll(self, scroll_id, scroll=None):
        remaining = self.scrolls[scroll_id]
        page, self.scrolls[scroll_id] = remaining[:100], remaining[100:]
        return {"_scroll_id": scroll_id, "hits": {"hits": page}}

    def clear_scroll(self, scroll_id=None, **_):
        self.scrolls.pop(scroll_id, None)

    def mget(self, index, body, **_):
        present = self.docs(index)
        return {"docs": [{"_id": _id, "found": _id in present} for _id in body["ids"]]}

    def bulk(self, body, **_):
        self.bulk_calls += 1
        if self.crash_on_bulk == self.bulk_calls:
            raise ConnectionError("the process was killed")
        items = []
        for action, source in zip(body[::2], body[1::2]):
            spec = action["index"]
            if spec["_id"] in self.reject_ids:
                items.append({"index": {"_id": spec["_id"], "status": 400, "error": {"type": "mapper_parsing_exception"}}})
                continue
            self.indices_data[self.resolve(spec["_index"])]["docs"][spec["_id"]] = source
            items.append({"index": {"_id": spec["_id"], "status": 201}})
        return {"errors": any("error" in item["index"] for item in items), "items": items}


def chunk(n: int, dimension: int, **extra) -> Dict[str, Any]:
    return {
        "chunk_text": f"passage number {n}",
        "knowledge_base_id": "kb-1",
        "document_id": f"doc-{n}",
        "embedding": [float(n)] * dimension,
        "embedding_model": "old-model",
        **extra,
    }
