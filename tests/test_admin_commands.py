import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from api import reconcile_storage
from worker import replay_dlq


def test_reconcile_dry_run_and_already_queued_cleanup():
    storage = MagicMock()
    storage.client.list_objects.return_value = [
        SimpleNamespace(object_name="orphan", last_modified=datetime.now(UTC) - timedelta(days=3))
    ]
    db = MagicMock()
    db.__enter__.return_value = db
    db.query.return_value.filter.return_value.first.side_effect = [None, object()]
    assert (
        reconcile_storage.reconcile(
            storage, datetime.now(UTC), execute=True, session_factory=lambda: db
        )
        == []
    )
    db.add.assert_not_called()
    db.query.return_value.filter.return_value.first.side_effect = [None, None]
    assert reconcile_storage.reconcile(
        storage, datetime.now(UTC), limit=1, execute=False, session_factory=lambda: db
    ) == ["orphan"]
    db.add.assert_not_called()


def test_reconcile_cli_defaults_to_inspect_only(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["reconcile_storage", "--min-age-hours", "48", "--limit", "3"])
    monkeypatch.setattr(reconcile_storage, "MinioClient", MagicMock())
    command = MagicMock(return_value=["old-object"])
    monkeypatch.setattr(reconcile_storage, "reconcile", command)
    reconcile_storage.main()
    assert command.call_args.args[2:] == (3, False)
    assert "Unreferenced: old-object" in capsys.readouterr().out


def test_reconcile_cli_invalid_age_never_touches_storage(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["reconcile_storage", "--min-age-hours", "0"])
    storage = MagicMock()
    monkeypatch.setattr(reconcile_storage, "MinioClient", storage)
    with pytest.raises(SystemExit):
        reconcile_storage.main()
    storage.assert_not_called()


def test_dlq_cli_defaults_to_inspect_and_closes_connection(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["replay_dlq", "--queue", "document.upload.dlq"])
    connection = MagicMock()
    channel = connection.channel.return_value
    channel.basic_get.return_value = (
        SimpleNamespace(delivery_tag=1),
        SimpleNamespace(message_id="event"),
        b"{}",
    )
    monkeypatch.setattr(replay_dlq.pika, "BlockingConnection", lambda _: connection)
    replay_dlq.main()
    channel.basic_publish.assert_not_called()
    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)
    connection.close.assert_called_once()


def test_dlq_cli_invalid_limit_never_connects(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["replay_dlq", "--queue", "document.upload.dlq", "--limit", "0"]
    )
    connection = MagicMock()
    monkeypatch.setattr(replay_dlq.pika, "BlockingConnection", connection)
    with pytest.raises(SystemExit):
        replay_dlq.main()
    connection.assert_not_called()
