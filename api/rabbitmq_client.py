import os
import json
import asyncio
import aio_pika
from aio_pika import ExchangeType
from typing import Callable, Awaitable

class RabbitMQClient:
    def __init__(self):
        self.url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672")
        self.connection = None
        self.channel = None

    async def connect(self):
        self.connection = await aio_pika.connect_robust(self.url)
        self.channel = await self.connection.channel()

    async def close(self):
        if self.connection and not self.connection.is_closed:
            await self.connection.close()

    async def setup_queues(self):
        # Declare dead letter exchange
        dlx = await self.channel.declare_exchange(
            "document.dlx", ExchangeType.DIRECT, durable=True
        )
        
        # Declare DLQ queues
        upload_dlq = await self.channel.declare_queue(
            "document.upload.dlq", durable=True
        )
        reindex_dlq = await self.channel.declare_queue(
            "document.reindex.dlq", durable=True
        )
        
        # Bind DLQs to DLX
        await upload_dlq.bind(dlx, routing_key="document.upload.dlq")
        await reindex_dlq.bind(dlx, routing_key="document.reindex.dlq")
        
        # Declare main exchanges
        upload_exchange = await self.channel.declare_exchange(
            "document.upload", ExchangeType.DIRECT, durable=True
        )
        reindex_exchange = await self.channel.declare_exchange(
            "document.reindex", ExchangeType.DIRECT, durable=True
        )
        
        # Declare main queues with DLX configuration
        upload_queue = await self.channel.declare_queue(
            "document.upload.queue",
            durable=True,
            arguments={
                "x-dead-letter-exchange": "document.dlx",
                "x-dead-letter-routing-key": "document.upload.dlq",
                "x-message-ttl": 86400000  # 24h TTL
            }
        )
        reindex_queue = await self.channel.declare_queue(
            "document.reindex.queue",
            durable=True,
            arguments={
                "x-dead-letter-exchange": "document.dlx",
                "x-dead-letter-routing-key": "document.reindex.dlq",
                "x-message-ttl": 86400000  # 24h TTL
            }
        )
        
        # Bind queues to exchanges
        await upload_queue.bind(upload_exchange, routing_key="document.upload")
        await reindex_queue.bind(reindex_exchange, routing_key="document.reindex")

    async def publish_message(self, routing_key: str, message: dict):
        if not self.channel:
            await self.connect()
        # Determine exchange based on routing_key
        if routing_key == "document.upload":
            exchange_name = "document.upload"
        elif routing_key == "document.reindex":
            exchange_name = "document.reindex"
        else:
            raise ValueError(f"Unknown routing key: {routing_key}")
        
        exchange = await self.channel.get_exchange(exchange_name)
        message_body = json.dumps(message).encode()
        await exchange.publish(
            aio_pika.Message(body=message_body),
            routing_key=routing_key
        )

    async def consume_messages(self, routing_key: str, callback: Callable[[dict], Awaitable[None]]):
        if not self.channel:
            await self.connect()
        # Determine queue based on routing_key
        if routing_key == "document.upload":
            queue_name = "document.upload.queue"
        elif routing_key == "document.reindex":
            queue_name = "document.reindex.queue"
        else:
            raise ValueError(f"Unknown routing key: {routing_key}")
        
        queue = await self.channel.get_queue(queue_name)
        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    try:
                        payload = json.loads(message.body.decode())
                        await callback(payload)
                    except Exception as e:
                        print(f"Error processing message: {e}")
                        # Depending on the policy, we might want to reject or requeue
                        # For now, we acknowledge and let the DLQ handle it if needed.
                        # But note: we are not setting up a DLQ in this example.
                        # We'll just acknowledge and log the error.
                        pass