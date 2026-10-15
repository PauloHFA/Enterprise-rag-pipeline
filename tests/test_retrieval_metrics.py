import math
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from api import search
from evaluation.common import load_dataset
from evaluation.metrics import percentile, score_ranking


def test_metrics_known_ranking_and_duplicate_chunks():
    relevance = {"a": 3, "b": 1}
    score = score_ranking(["wrong", "a", "a", "b"], relevance)
    assert score["recall@1"] == 0 and score["recall@5"] == 1
    assert score["mrr@10"] == 0.5
    assert score["ndcg@10"] == pytest.approx(
        (7 / math.log2(3) + 1 / math.log2(4)) / (7 + 1 / math.log2(3))
    )
    assert score_ranking([], relevance)["ndcg@10"] == 0
    assert score_ranking(["a", "b"], relevance)["ndcg@10"] == 1


def test_metric_invalid_judgments_and_percentiles():
    for relevance in ({}, {"a": 0}):
        with pytest.raises(ValueError):
            score_ranking([], relevance)
    assert percentile(list(range(1, 101)), 0.95) == 95
    assert percentile([1, 100], 0.95) == 100
    for values, quantile in (([], 0.95), ([1], 0), ([1], 2)):
        with pytest.raises(ValueError):
            percentile(values, quantile)


def test_curated_fixture_references_and_unique_queries():
    dataset = load_dataset("evaluation/dataset.json")
    assert len(dataset["queries"]) == 60
    assert len({item["query"] for item in dataset["queries"]}) == 60
    assert len(dataset["documents"]) == 20


def row(content="Pagamento " * 30):
    return SimpleNamespace(
        id=1,
        document_id="document",
        page=2,
        section="Financeiro",
        section_idx=0,
        offset_start=10,
        offset_end=10 + len(content),
        filename="policy.pdf",
        title="Policy",
        content=content,
        score=0.5,
        lexical_rank=1,
        vector_rank=2,
    )


@pytest.mark.parametrize("mode", ["lexical", "vector", "hybrid"])
def test_search_bound_parameters_filter_scope_and_citations(monkeypatch, mode):
    model = MagicMock()
    model.encode.return_value = np.array([0.1] * 384)
    monkeypatch.setattr(search, "_embedding_model", model)
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = [row()]
    result = search.hybrid_search(
        db,
        "contrato",
        top_k=30,
        mode=mode,
        filters={
            "tenant_id": "acme",
            "tags": ["legal"],
            "created_after": "2026-01-01",
            "created_before": "2027-01-01",
        },
        weights={"lexical": 2, "vector": 1},
    )
    statement, params = db.execute.call_args.args
    assert params["tenant_id"] == "acme" and params["tags"] == ["legal"]
    assert params["candidate_limit"] == 90
    assert "d.tenant_id = :tenant_id" in str(statement)
    assert result[0]["citation"]["offset_end"] == 210
    assert len(result[0]["citation"]["excerpt"]) == 200 and result[0]["snippet"].endswith("...")
    assert "acme" not in str(statement)  # The value is a bind, not interpolated SQL.
    if mode == "lexical":
        model.encode.assert_not_called()
    else:
        model.encode.assert_called_once_with("contrato")


@pytest.mark.parametrize("bad", [np.zeros(3), np.full(384, np.nan)])
def test_invalid_query_vector_never_reaches_database(monkeypatch, bad):
    monkeypatch.setattr(search, "_embedding_model", SimpleNamespace(encode=lambda _: bad))
    with pytest.raises(ValueError):
        search.hybrid_search(MagicMock(), "test")


def test_legacy_citation_has_no_invented_offsets():
    value = row("Short")
    value.offset_start = None
    citation = search._format_result(value, {})["citation"]
    assert citation["offset_start"] is None and citation["offset_end"] is None
