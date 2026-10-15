import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from api import search
from api.config import settings
from shared.telemetry import new_trace


def test_concurrent_cold_search_loads_only_one_pinned_model(monkeypatch):
    calls = []
    model = object()

    def load(name, **kwargs):
        calls.append((name, kwargs))
        time.sleep(0.02)
        return model

    monkeypatch.setattr(search, "_embedding_model", None)
    monkeypatch.setattr(search, "SentenceTransformer", load)
    with ThreadPoolExecutor(max_workers=4) as pool:
        models = list(pool.map(lambda _: search.get_embedding_model(), range(4)))
    assert all(item is model for item in models)
    assert calls == [
        (settings.EMBEDDING_MODEL_NAME, {"revision": settings.EMBEDDING_MODEL_REVISION})
    ]


def test_trace_child_keeps_trace_id_but_changes_span():
    parent = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
    child = new_trace(parent)
    assert child.split("-")[1] == "a" * 32
    assert child.split("-")[2] != "b" * 16
    assert new_trace("00-" + "0" * 32 + "-" + "b" * 16 + "-01").split("-")[1] != "0" * 32


def test_retention_disabled_never_deletes_documents(monkeypatch):
    from api.models import OutboxEvent
    from api.retention import apply_retention

    monkeypatch.setattr(settings, "RETENTION_DAYS", 0)
    db = MagicMock()
    assert apply_retention(db) == 0
    assert db.query.call_args.args == (OutboxEvent,)
    db.delete.assert_not_called()
