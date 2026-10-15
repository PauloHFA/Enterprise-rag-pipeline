import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api import rate_limiter
from api.minio_client import MinioClient
from api.rabbitmq_client import RabbitMQClient
from worker.replay_dlq import replay


def test_token_bucket_burst_refill_and_client_isolation(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(rate_limiter.time, "time", lambda: clock[0])
    limiter = rate_limiter.TokenBucketRateLimiter(capacity=2, refill_rate=1)
    assert limiter.consume("a") and limiter.consume("a")
    assert not limiter.consume("a")
    assert limiter.consume("b")
    clock[0] += 0.5
    assert limiter.get_remaining("a") == 0
    clock[0] += 1
    assert limiter.consume("a")
    clock[0] += 100
    assert limiter.get_remaining("a") == 2


@pytest.mark.asyncio
async def test_sliding_window_rejects_and_expires(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(rate_limiter.time, "time", lambda: clock[0])
    middleware = rate_limiter.RateLimitMiddleware(MagicMock(), requests_per_minute=1)
    request = SimpleNamespace(
        url=SimpleNamespace(path="/v1/search"), client=SimpleNamespace(host="peer"), headers={}
    )
    call = AsyncMock(side_effect=lambda request: SimpleNamespace(headers={}))
    assert (await middleware.dispatch(request, call)).headers["X-RateLimit-Remaining"] == "0"
    assert (await middleware.dispatch(request, call)).status_code == 429
    clock[0] += 61
    assert (await middleware.dispatch(request, call)).headers["X-RateLimit-Remaining"] == "0"
    request.url.path = "/metrics"
    await middleware.dispatch(request, call)
    assert call.await_count == 3


@pytest.mark.asyncio
async def test_token_bucket_middleware_contract():
    limiter = rate_limiter.TokenBucketRateLimiter(1, 0)
    middleware = rate_limiter.TokenBucketMiddleware(MagicMock(), limiter)
    request = SimpleNamespace(
        url=SimpleNamespace(path="/v1/search"), client=SimpleNamespace(host="peer"), headers={}
    )
    call = AsyncMock(side_effect=lambda request: SimpleNamespace(headers={}))
    assert (await middleware.dispatch(request, call)).headers["X-RateLimit-Remaining"] == "0"
    assert (await middleware.dispatch(request, call)).status_code == 429
    request.url.path = "/readyz"
    await middleware.dispatch(request, call)
    assert call.await_count == 2


def test_object_storage_stream_lifecycle():
    adapter = MinioClient()
    adapter.client = MagicMock()
    adapter.client.bucket_exists.return_value = False
    adapter._ensure_bucket_exists()
    adapter.client.make_bucket.assert_called_once()
    adapter.client.get_object.return_value.read.return_value = b"policy"
    assert adapter.get_file("documents", "key") == b"policy"
    adapter.client.get_object.return_value.close.assert_called_once()
    adapter.client.get_object.return_value.release_conn.assert_called_once()
    adapter.delete_file("documents", "key")
    adapter.client.remove_object.assert_called_once_with("documents", "key")


@pytest.mark.parametrize("operation", ["bucket", "upload", "delete", "read"])
def test_object_adapter_propagates_storage_errors(operation):
    from minio.error import S3Error

    adapter = MinioClient()
    adapter.client = MagicMock()
    error = S3Error("AccessDenied", "denied", "resource", "request", "host", MagicMock())
    if operation == "bucket":
        adapter.client.bucket_exists.side_effect = error
        call = adapter._ensure_bucket_exists
    elif operation == "upload":
        adapter.client.put_object.side_effect = error

        def call():
            return adapter.upload_file("documents", "key", b"file", 4, "text/plain")
    elif operation == "delete":
        adapter.client.remove_object.side_effect = error

        def call():
            return adapter.delete_file("documents", "key")
    else:
        adapter.client.get_object.side_effect = error

        def call():
            return adapter.get_file("documents", "key")

    with pytest.raises(S3Error):
        call()


@pytest.mark.asyncio
async def test_broker_topology_persistent_confirmed_message_and_reconnect(monkeypatch):
    import aio_pika

    adapter = RabbitMQClient()
    channel = AsyncMock()
    channel.is_closed = False
    connection = AsyncMock(is_closed=False)
    connection.channel.return_value = channel
    monkeypatch.setattr(aio_pika, "connect_robust", AsyncMock(return_value=connection))
    await adapter.publish_message("document.upload", {"event_id": "stable-id"})
    connection.channel.assert_awaited_once_with(publisher_confirms=True, on_return_raises=True)
    assert channel.declare_queue.await_count == 10
    exchange = channel.get_exchange.return_value
    published = exchange.publish.call_args.args[0]
    assert published.delivery_mode == 2 and published.message_id == "stable-id"
    assert json.loads(published.body) == {"event_id": "stable-id"}
    assert exchange.publish.call_args.kwargs["mandatory"] is True
    await adapter.publish_message("document.reindex", {})
    with pytest.raises(ValueError):
        await adapter.publish_message("unknown", {})
    channel.is_closed = True
    await adapter.publish_message("document.upload", {})
    connection.close.assert_awaited_once()
    await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("routing", ["document.upload", "document.reindex"])
@pytest.mark.parametrize("failure", [None, "callback", "json"])
async def test_broker_consumer_surfaces_failures_to_message_rejection(routing, failure):
    adapter = RabbitMQClient()
    channel = AsyncMock()
    queue = channel.get_queue.return_value
    iterator = MagicMock()
    iterator.__aenter__ = AsyncMock(return_value=iterator)
    iterator.__aexit__ = AsyncMock(return_value=False)
    queue.iterator = MagicMock(return_value=iterator)
    message = MagicMock(body=b"not-json" if failure == "json" else b'{"event_id":"stable"}')
    processing = AsyncMock()
    processing.__aexit__.return_value = False
    message.process.return_value = processing
    iterator.__aiter__.return_value = [message]
    callback = AsyncMock(side_effect=RuntimeError("failed") if failure == "callback" else None)

    async def connect():
        adapter.channel = channel

    adapter.connect = AsyncMock(side_effect=connect)
    if failure:
        error = RuntimeError if failure == "callback" else json.JSONDecodeError
        with pytest.raises(error):
            await adapter.consume_messages(routing, callback)
        # The exception reaches aio-pika's process context, which rejects it;
        # catching it inside that context would instead acknowledge failed work.
        assert processing.__aexit__.call_args.args[0] is error
    else:
        await adapter.consume_messages(routing, callback)
        assert processing.__aexit__.call_args.args[0] is None
    adapter.connect.assert_awaited_once()
    channel.get_queue.assert_awaited_once_with(routing + ".queue")
    if failure == "json":
        callback.assert_not_awaited()
    else:
        callback.assert_awaited_once_with({"event_id": "stable"})


@pytest.mark.asyncio
async def test_broker_rejects_unknown_consumer_route():
    adapter = RabbitMQClient()
    adapter.channel = AsyncMock()
    with pytest.raises(ValueError, match="Unknown routing key"):
        await adapter.consume_messages("storage.delete", AsyncMock())
    adapter.channel.get_queue.assert_not_awaited()


def test_dlq_inspect_and_replay_preserve_message_until_confirm():
    method = SimpleNamespace(delivery_tag=1)
    properties = SimpleNamespace(
        message_id="stable", headers={"x-retry-count": 3, "x-death": "history", "custom": "keep"}
    )
    channel = MagicMock()
    channel.basic_get.side_effect = [(method, properties, b"{}"), (None, None, None)]
    assert replay(channel, "document.upload.dlq", execute=False) == 0
    channel.basic_publish.assert_not_called()
    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)
    channel.reset_mock()
    channel.basic_get.side_effect = [(method, properties, b"{}"), (None, None, None)]
    assert replay(channel, "document.upload.dlq", execute=True) == 1
    sent = channel.basic_publish.call_args.kwargs
    assert sent["routing_key"] == "document.upload.queue"
    assert sent["properties"].message_id == "stable"
    assert sent["properties"].headers == {"x-retry-count": 0, "custom": "keep"}
    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    channel.reset_mock()
    channel.basic_get.side_effect = [(method, properties, b"{}")]
    channel.basic_publish.side_effect = ConnectionError()
    with pytest.raises(ConnectionError):
        replay(channel, "document.upload.dlq", execute=True)
    channel.basic_ack.assert_not_called()
    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)
