"""Inspect or replay bounded batches from a DLQ, preserving event identity."""

import argparse
import json
import os

import pika


def replay(channel, queue, limit=10, execute=False):
    destination = queue.removesuffix(".dlq") + ".queue"
    replayed = 0
    for _ in range(limit):
        method, properties, body = channel.basic_get(queue=queue, auto_ack=False)
        if method is None:
            break
        if not execute:
            print(
                json.dumps(
                    {"queue": queue, "message_id": properties.message_id, "bytes": len(body)}
                )
            )
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            # Inspect one message without disturbing queue ordering or contents.
            break
        headers = {
            key: value
            for key, value in (properties.headers or {}).items()
            if key
            not in (
                "x-death",
                "x-first-death-exchange",
                "x-first-death-queue",
                "x-first-death-reason",
            )
        }
        headers["x-retry-count"] = 0
        try:
            channel.basic_publish(
                exchange="",
                routing_key=destination,
                body=body,
                mandatory=True,
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    headers=headers,
                    message_id=properties.message_id,
                    content_type="application/json",
                ),
            )
        except Exception:
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            raise
        channel.basic_ack(delivery_tag=method.delivery_tag)
        replayed += 1
    return replayed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queue", required=True, choices=["document.upload.dlq", "document.reindex.dlq"]
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--execute", action="store_true", help="Publish and acknowledge; default only inspects"
    )
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit must be positive")
    connection = pika.BlockingConnection(
        pika.URLParameters(os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672"))
    )
    try:
        channel = connection.channel()
        channel.confirm_delivery()
        print(f"Replayed: {replay(channel, args.queue, args.limit, args.execute)}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
