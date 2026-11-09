"""The private queue consumer: Vercel invokes it when a scheduled post is due.

Declared under `[[tool.vercel.subscribers]]` in pyproject.toml. At build time
Vercel imports this module, reads the `@subscribe` below and generates a
queue-triggered function for it. That function has no public URL; only Vercel
Queues can invoke it, so it needs no auth of its own.

It does one thing: `scheduler.run(id, version)`, which claims the row,
publishes the stored copy and records the outcome.

It must not raise. When a subscriber raises, the queue redelivers the message,
and a redelivery after a partly finished publish is how a post goes out twice.
The single exception is `ClaimUnavailable`: the database could not be reached
before anything was claimed, so asking the queue to try again in a minute is
safe — the claim still guards the row.
"""

import asyncio

from vercel.queue import Message, RetryAfter, subscribe

from app import scheduler
from app.logging_setup import configure_logging, get_logger

configure_logging()
log = get_logger("consumer")

# Redeliveries only happen for ClaimUnavailable or a crashed invocation; this
# caps what a broken deployment can cost.
_MAX_DELIVERIES = 10
_CLAIM_RETRY_SECONDS = 60


@subscribe(topic=scheduler.TOPIC, max_attempts=_MAX_DELIVERIES)
async def publish_due(message: Message[scheduler.PublishJob]) -> None:
    job = message.payload
    try:
        publication_id, version = str(job["id"]), int(job["version"])
    except (KeyError, TypeError, ValueError):
        log.error(f"Dropping a malformed message {message.message_id}: {job!r}")
        return

    try:
        # The publish path is synchronous (httpx, psycopg); keep it off the loop.
        outcome = await asyncio.to_thread(scheduler.run, publication_id, version)
    except scheduler.ClaimUnavailable as exc:
        log.warning(f"{exc}; asking the queue to retry in {_CLAIM_RETRY_SECONDS}s")
        raise RetryAfter(_CLAIM_RETRY_SECONDS, reason=str(exc)) from exc
    except Exception:
        log.exception(f"Consumer error for {publication_id} v{version}; not retrying")
        return
    log.info(f"{publication_id} v{version}: {outcome}")
