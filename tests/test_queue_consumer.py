"""The queue consumer: never raises after a claim, and is wired to the topic
the scheduler sends to."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from vercel.queue import Message, MessageMetadata, RetryAfter
from vercel.queue.embedded import embedded_queue_service

from app import queue_consumer, scheduler


def message(payload) -> Message:
    return Message(payload=payload, metadata=MessageMetadata(
        message_id="m1", delivery_count=1, created_at=datetime.now(UTC),
        topic="publish", consumer_group="test"))


def deliver(payload):
    asyncio.run(queue_consumer.publish_due(message(payload)))


def test_runs_the_scheduler(monkeypatch):
    seen = []
    monkeypatch.setattr(scheduler, "run", lambda i, v: seen.append((i, v)) or "published")
    deliver({"id": "abc", "version": 2})
    assert seen == [("abc", 2)]


def test_swallows_errors_so_the_queue_does_not_redeliver(monkeypatch):
    def boom(*_):
        raise RuntimeError("mid-publish crash")
    monkeypatch.setattr(scheduler, "run", boom)
    deliver({"id": "abc", "version": 1})  # must not raise


def test_asks_for_a_retry_only_when_nothing_was_claimed(monkeypatch):
    def down(*_):
        raise scheduler.ClaimUnavailable("db down")
    monkeypatch.setattr(scheduler, "run", down)
    with pytest.raises(RetryAfter):
        deliver({"id": "abc", "version": 1})


def test_drops_malformed_messages(monkeypatch):
    seen = []
    monkeypatch.setattr(scheduler, "run", lambda *a: seen.append(a))
    deliver({"version": 1})
    deliver({"id": "abc", "version": "not-a-number"})
    assert seen == []


def test_what_the_scheduler_sends_reaches_the_consumer(monkeypatch, fake_env):
    """scheduler._send → Vercel Queues (embedded, in-process) → publish_due."""
    monkeypatch.setattr(fake_env, "queue_enabled", True)

    sent = []

    class Recorder:
        def send(self, topic, payload, **options):
            sent.append((topic, payload, options))

    monkeypatch.setattr(scheduler, "_queue", lambda: Recorder())
    row = {"id": uuid.uuid4(), "version": 3,
           "scheduled_at": datetime.now(UTC) + timedelta(hours=2)}
    assert scheduler._send(row, key="k")

    topic, payload, options = sent[0]
    assert payload == {"id": str(row["id"]), "version": 3}
    assert timedelta(hours=1, minutes=59) < options["delay"] <= timedelta(hours=2)
    assert options["retention"] > options["delay"]  # outlives its own delay
    assert options["idempotency_key"] == "k"

    ran = []
    monkeypatch.setattr(scheduler, "run", lambda i, v: ran.append((i, v)) or "published")

    async def replay():
        async with embedded_queue_service() as service:
            await service.get_async_client().send(topic, payload)
            for _ in range(50):
                if ran:
                    return
                await asyncio.sleep(0.1)

    asyncio.run(replay())
    assert ran == [(str(row["id"]), 3)]
