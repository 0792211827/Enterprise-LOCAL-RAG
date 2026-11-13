"""The reindex command: builds a new lucene index, copies (and re-embeds) the chunks, swaps the alias. Offline, against a
fake cluster; the live behaviour is covered by tests/integration/test_narrow_scope_recall.py."""

import pytest
from src.cli import reindex
from src.config import Settings
from src.services.opensearch.client import OpenSearchClient

from tests.opensearch_fakes import FakeCluster, FakeEmbeddings, chunk

ALIAS = "rag-documents-chunks"


def docs(count, dimension):
    return {f"c{n}": chunk(n, dimension) for n in range(count)}


def run(cluster, monkeypatch, *argv, dimension=3, embeddings=None):
    monkeypatch.setenv("EMBEDDING__DIMENSION", str(dimension))
    settings = Settings()
    opensearch = OpenSearchClient(host="http://localhost:9200", settings=settings)
    opensearch.client = cluster
    embeddings = embeddings or FakeEmbeddings(dimension)
    code = reindex.main(["--batch-size", "2", *argv], opensearch=opensearch, embeddings=embeddings, settings=settings)
    return code, embeddings


@pytest.fixture
def legacy(monkeypatch):
    """What an existing deployment has: a concrete nmslib index named like the alias will be."""
    cluster = FakeCluster()
    cluster.add_index(ALIAS, 3, "nmslib", docs(5, 3))
    return cluster


def test_moves_a_concrete_nmslib_index_behind_an_alias_on_lucene(legacy, monkeypatch, capsys):
    code, embeddings = run(legacy, monkeypatch)

    assert code == 0
    live = legacy.resolve(ALIAS)
    assert live != ALIAS and live.startswith(f"{ALIAS}-v")
    assert legacy.indices_data[live]["engine"] == "lucene"
    assert set(legacy.docs(ALIAS)) == {f"c{n}" for n in range(5)}
    assert legacy.docs(ALIAS)["c3"]["embedding"] == [3.0, 3.0, 3.0]  # same dimension: vectors are copied, not recomputed
    assert embeddings.embedded == []
    assert ALIAS not in legacy.indices_data  # the alias took the old index's name
    assert "swapped" in capsys.readouterr().out.lower()


def test_a_changed_embedding_dimension_re_embeds_every_chunk(legacy, monkeypatch):
    code, embeddings = run(legacy, monkeypatch, dimension=2)

    assert code == 0
    live = legacy.indices_data[legacy.resolve(ALIAS)]
    assert live["dimension"] == 2
    assert sorted(embeddings.embedded) == sorted(f"passage number {n}" for n in range(5))
    migrated = legacy.docs(ALIAS)["c1"]
    assert len(migrated["embedding"]) == 2 and migrated["embedding_model"] == "new-model"


def test_re_embed_flag_recomputes_vectors_of_the_same_dimension(legacy, monkeypatch):
    code, embeddings = run(legacy, monkeypatch, "--re-embed")

    assert code == 0 and len(embeddings.embedded) == 5


def test_an_interrupted_run_resumes_without_redoing_finished_work(legacy, monkeypatch, capsys):
    legacy.crash_on_bulk = 2
    code, _ = run(legacy, monkeypatch, "--re-embed")

    assert code != 0
    assert legacy.resolve(ALIAS) == ALIAS  # nothing is swapped half-way: search still reads the old index
    assert len(legacy.docs(f"{ALIAS}-v1")) == 2  # the first batch of two made it across

    legacy.crash_on_bulk = None
    code, second = run(legacy, monkeypatch, "--re-embed")

    assert code == 0
    assert len(second.embedded) == 3  # only what was missing
    assert set(legacy.docs(ALIAS)) == {f"c{n}" for n in range(5)}
    assert "resum" in capsys.readouterr().out.lower()


def test_rejected_chunks_stop_the_swap_and_name_the_failure(legacy, monkeypatch, capsys):
    legacy.reject_ids = {"c1"}
    code, _ = run(legacy, monkeypatch)

    assert code == 1
    assert legacy.resolve(ALIAS) == ALIAS
    assert set(legacy.docs(ALIAS)) == {f"c{n}" for n in range(5)}  # the live index is untouched
    assert "1 chunk" in capsys.readouterr().err

    legacy.reject_ids = set()
    code, _ = run(legacy, monkeypatch)  # the same command picks up the missing chunk
    assert code == 0 and legacy.resolve(ALIAS) != ALIAS


def test_a_second_run_builds_the_next_version_and_keeps_the_previous_for_rollback(legacy, monkeypatch):
    run(legacy, monkeypatch)
    first = legacy.resolve(ALIAS)

    code, _ = run(legacy, monkeypatch, "--re-embed")

    assert code == 0
    second = legacy.resolve(ALIAS)
    assert second != first and first in legacy.indices_data
    assert legacy.aliases[ALIAS] == [second]

    code, _ = run(legacy, monkeypatch, "--re-embed", "--delete-old")
    assert code == 0 and second not in legacy.indices_data


def test_dry_run_reports_the_plan_and_changes_nothing(legacy, monkeypatch, capsys):
    code, _ = run(legacy, monkeypatch, "--dry-run")

    assert code == 0
    assert set(legacy.indices_data) == {ALIAS}
    out = capsys.readouterr().out
    assert "5" in out and "lucene" in out


def test_nothing_to_reindex_is_reported(monkeypatch, capsys):
    code, _ = run(FakeCluster(), monkeypatch)

    assert code == 2
    assert "no chunk index" in capsys.readouterr().err.lower()


def test_a_resumed_build_with_a_different_dimension_is_refused(legacy, monkeypatch, capsys):
    legacy.crash_on_bulk = 2
    run(legacy, monkeypatch, dimension=2)
    legacy.crash_on_bulk = None

    code, _ = run(legacy, monkeypatch, dimension=4)

    assert code == 1
    assert "delete" in capsys.readouterr().err.lower()
    assert legacy.resolve(ALIAS) == ALIAS
