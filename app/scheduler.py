"""Scheduled publishing: keep an approved post, hand it to the queue, publish it once.

A scheduled row is a human approval with a time attached. Nothing here writes,
rewrites or re-renders anything: `run` publishes the stored text and the stored
(already branded) image through `publishing.publish_target` and never calls
the agent or the image model.

One row per brand × platform, in the `publications` table:

    scheduled ──claim──▶ publishing ──▶ published | failed | needs_review
        │                    │
        └──▶ cancelled       └── nothing reached the feed ──▶ scheduled (retry)

Three rules keep a post from going out twice:

1. The claim is the only way to publish. It is one UPDATE that succeeds for
   exactly one caller, and only while the row is `scheduled` at the version
   the message carries. A duplicate delivery, a message from before a
   reschedule, a cancelled post: the claim returns nothing and `run` stops.
2. Only failures where nothing reached the feed are retried
   (`FailureKind.TRANSIENT`), with backoff, a bounded number of times.
3. An unknown outcome goes to `needs_review`. A human checks the feed and
   resolves it; nothing here ever retries it.

Queue messages carry only `{id, version}`. Vercel Queues drops a send whose
idempotency key it has seen within min(retention, 24h), so each intended send
gets its own key: the first `"{id}:{version}"`, a retry `"...:retry<n>"`, a
cron recovery `"...:recover<date>"`. Reusing the first key would make every
retry a silent no-op.
"""

import time
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import TypedDict

import psycopg
from vercel.queue import Topic
from vercel.queue.sync import QueueClient

from app import db, publishing, storage
from app.config import get_settings
from app.logging_setup import get_logger
from app.publish_errors import FailureKind, PublishError
from app.schema.common import check_length

log = get_logger("scheduler")

# /schedule and PATCH hand a post to the queue at once when it is due within
# this; anything later waits for the daily cron.
ENQUEUE_WINDOW = timedelta(hours=24)
# The cron's look-ahead. Hobby cron fires once a day and only roughly on time,
# so the window overlaps the next run instead of leaving a gap.
CRON_WINDOW = timedelta(hours=26)
# A post must be at least this far out, so its rows are committed long before
# the queue can deliver the message.
MIN_LEAD = timedelta(minutes=1)
# Past this, a scheduled row's message counts as lost and a publishing row as
# crashed. Far longer than the function's maxDuration or any retry backoff.
STALE_AFTER = timedelta(minutes=15)
# One retry per step after the first attempt; failed once they are used up.
RETRY_BACKOFF = (timedelta(minutes=2), timedelta(minutes=4), timedelta(minutes=8))
MAX_ATTEMPTS = len(RETRY_BACKOFF) + 1
# Images are deleted once every row using them has finished this long ago. The
# lookback is bounded so a deleted image is not asked about forever.
KEEP_IMAGES_FOR = timedelta(days=7)
_CLEANUP_LOOKBACK = timedelta(days=7)

# A message must outlive its delay (the queue's default TTL is 24h, shorter
# than the cron window) plus room for redeliveries. The queue caps it at 7 days.
_RETENTION_SLACK = timedelta(hours=6)
_MAX_RETENTION = timedelta(days=7)

_ERROR_MAX = 2000
_WRITE_RETRY_SECONDS = (1, 3)


class PublishJob(TypedDict):
    id: str
    version: int


TOPIC = Topic[PublishJob]("publish")


class NotFound(LookupError):
    """No row with that id."""


class Conflict(RuntimeError):
    """The row is not in a state that allows the change."""


class ClaimUnavailable(RuntimeError):
    """The database could not be asked, so nothing was claimed or published.

    The one error the consumer lets the queue retry: redelivery is harmless
    while the claim has not happened.
    """


# ---- Scheduling (called from the /schedule routes) ------------------------

def create(brand: str, scheduled_at: datetime, targets: list[tuple[str, str]],
           draft_image: str | None, image_alt: str | None) -> list[dict]:
    """Store one row per (platform, post) for `brand`, all at `scheduled_at`.

    `draft_image` is the brand's branded draft from /image. It is copied once
    out of the drafts folder (which the daily sweep empties) and the copy is
    shared by the rows. Rows due within ENQUEUE_WINDOW are sent to the queue
    inside the same transaction, so a failed send leaves nothing behind: no
    rows, and the copy is deleted. The draft itself is left alone; it may
    still be published elsewhere.
    """
    at = scheduled_at.astimezone(UTC)
    public_id = storage.copy(draft_image, brand) if draft_image else None
    try:
        with db.connect() as conn:
            rows = [
                conn.execute(
                    """INSERT INTO publications
                         (brand, platform, post, image_public_id, image_alt, scheduled_at)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       RETURNING *""",
                    (brand, platform, post, public_id, image_alt, at),
                ).fetchone()
                for platform, post in targets
            ]
            _send_due(conn, rows)
    except Exception:
        if public_id:
            _forget_image(public_id)
        raise
    log.info(f"Scheduled {brand} on {', '.join(p for p, _ in targets)} for {at.isoformat()}")
    return rows


def list_rows(status: str | None = None, limit: int = 200) -> list[dict]:
    columns = "id, brand, platform, scheduled_at, status, external_url, error"
    with db.connect() as conn:
        if status:
            return conn.execute(
                f"SELECT {columns} FROM publications WHERE status = %s "
                f"ORDER BY scheduled_at DESC LIMIT %s", (status, limit)).fetchall()
        return conn.execute(
            f"SELECT {columns} FROM publications ORDER BY scheduled_at DESC LIMIT %s",
            (limit,)).fetchall()


def update(publication_id: uuid.UUID, *, scheduled_at: datetime | None = None,
           post: str | None = None) -> dict:
    """Edit the text and/or move the time of a row that is still `scheduled`.

    A new time bumps `version`, so the message already in the queue for the old
    time no longer matches the claim and is ignored when it arrives.
    """
    with db.connect() as conn:
        row = _locked(conn, publication_id)
        if post is not None:
            check_length(row["platform"], post)  # ValueError -> 422
            conn.execute("UPDATE publications SET post = %s WHERE id = %s",
                         (post, publication_id))
        if scheduled_at is not None:
            conn.execute(
                """UPDATE publications
                   SET scheduled_at = %s, version = version + 1, enqueued_at = NULL
                   WHERE id = %s""",
                (scheduled_at.astimezone(UTC), publication_id))
        row = conn.execute("SELECT * FROM publications WHERE id = %s",
                           (publication_id,)).fetchone()
        if scheduled_at is not None:
            _send_due(conn, [row])
    return row


def cancel(publication_id: uuid.UUID) -> dict:
    with db.connect() as conn:
        _locked(conn, publication_id)
        return conn.execute(
            "UPDATE publications SET status = 'cancelled' WHERE id = %s RETURNING *",
            (publication_id,)).fetchone()


def resolve(publication_id: uuid.UUID, *, published: bool,
            external_url: str | None = None) -> dict:
    """Record what a human found on the feed for a `needs_review` row."""
    with db.connect() as conn:
        row = _locked(conn, publication_id, status="needs_review")
        if published:
            return conn.execute(
                """UPDATE publications
                   SET status = 'published', error = NULL, published_at = now(),
                       external_url = COALESCE(%s, external_url)
                   WHERE id = %s RETURNING *""",
                (external_url, publication_id)).fetchone()
        error = _clip(f"Checked by a reviewer: not on the feed. {row['error'] or ''}")
        return conn.execute(
            "UPDATE publications SET status = 'failed', error = %s WHERE id = %s RETURNING *",
            (error, publication_id)).fetchone()


# ---- Publishing (called from the queue consumer) --------------------------

def claim(publication_id: str, version: int) -> dict | None:
    """Take the row for publishing, or None if someone else should not.

    None means cancelled, rescheduled (another version), already handled, or a
    duplicate delivery of a message that was already claimed.
    """
    try:
        with db.connect() as conn:
            return conn.execute(
                """UPDATE publications SET status = 'publishing', attempts = attempts + 1
                   WHERE id = %s AND version = %s AND status = 'scheduled'
                   RETURNING *""",
                (publication_id, version)).fetchone()
    except (psycopg.Error, db.DatabaseNotConfigured) as exc:
        raise ClaimUnavailable(f"Could not claim {publication_id}: {exc}") from exc


def run(publication_id: str, version: int) -> str:
    """Claim, publish and record one row. Returns what happened, for the log.

    Raises only `ClaimUnavailable`, and only before anything was claimed.
    Every failure after the claim is recorded on the row instead, because
    raising would make the queue redeliver the message.
    """
    try:
        uuid.UUID(publication_id)
    except ValueError:
        log.error(f"Ignoring a message with a malformed id {publication_id!r}")
        return "skipped"

    row = claim(publication_id, version)
    if row is None:
        log.info(f"{publication_id} v{version}: nothing to do (cancelled, "
                 f"rescheduled, already handled or a duplicate delivery)")
        return "skipped"

    label = f"{row['brand']} on {row['platform']} ({row['id']}, attempt {row['attempts']})"
    log.info(f"Publishing {label}")
    try:
        url = publishing.publish_target(
            row["brand"], row["platform"], row["post"],
            image_public_id=row["image_public_id"], alt=row["image_alt"])
    except PublishError as exc:
        return _after_failure(row, exc.kind, str(exc))
    except Exception as exc:  # a crash mid-publish: the post may or may not be live
        log.exception(f"Unexpected error publishing {label}")
        return _after_failure(row, FailureKind.UNKNOWN,
                              f"Unexpected {type(exc).__name__} while publishing: {exc}")

    _finish(row, "published", external_url=url or None)
    log.info(f"Published {label}: {url or '(no URL returned)'}")
    return "published"


def run_due() -> list[str]:
    """Publish every row that is due now, without the queue. For local runs."""
    with db.connect() as conn:
        due = conn.execute(
            """SELECT id, version FROM publications
               WHERE status = 'scheduled' AND scheduled_at <= now()
               ORDER BY scheduled_at""").fetchall()
    return [run(str(r["id"]), r["version"]) for r in due]


# ---- The daily cron --------------------------------------------------------

def dispatch() -> dict[str, int]:
    """Catch up on everything the queue cannot do by itself.

    - enqueue rows due within CRON_WINDOW that were never sent (scheduled more
      than 24h ahead, or a send that failed);
    - re-send rows whose message was lost (STALE_AFTER past due, still
      scheduled). These go out late, up to a day;
    - move rows stuck in `publishing` to `needs_review` (the function died
      mid-publish, so whether it posted is unknown);
    - delete images whose rows all finished more than KEEP_IMAGES_FOR ago.
    """
    with db.connect() as conn:
        fresh = conn.execute(
            """SELECT * FROM publications
               WHERE status = 'scheduled' AND enqueued_at IS NULL
                 AND scheduled_at <= now() + %s
               ORDER BY scheduled_at""", (CRON_WINDOW,)).fetchall()
        lost = conn.execute(
            """SELECT * FROM publications
               WHERE status = 'scheduled' AND enqueued_at IS NOT NULL
                 AND scheduled_at <= now() - %(stale)s AND enqueued_at <= now() - %(stale)s
               ORDER BY scheduled_at""", {"stale": STALE_AFTER}).fetchall()
        # The schema has no claim timestamp, so "stuck" is measured from when
        # the row was due or last sent. A legitimately late claim caught by
        # this is still recorded correctly: _finish accepts needs_review rows.
        stuck = conn.execute(
            """UPDATE publications SET status = 'needs_review', error = %(error)s
               WHERE status = 'publishing' AND scheduled_at <= now() - %(stale)s
                 AND (enqueued_at IS NULL OR enqueued_at <= now() - %(stale)s)
               RETURNING id""",
            {"stale": STALE_AFTER,
             "error": "Still publishing long after it was due: the function stopped "
                      "mid-publish. Check the feed, then resolve."}).fetchall()
        images = conn.execute(
            """SELECT image_public_id FROM publications
               WHERE image_public_id IS NOT NULL
               GROUP BY image_public_id
               HAVING bool_and(status IN ('published', 'failed', 'cancelled'))
                  AND max(COALESCE(published_at, scheduled_at)) < now() - %(keep)s
                  AND max(COALESCE(published_at, scheduled_at)) >= now() - %(keep)s - %(back)s""",
            {"keep": KEEP_IMAGES_FOR, "back": _CLEANUP_LOOKBACK}).fetchall()

    stamp = _now().strftime("%Y%m%d%H%M")
    summary = {
        "enqueued": sum(_enqueue(r, key=_key(r)) for r in fresh),
        "recovered": sum(_enqueue(r, key=_key(r, f"recover{stamp}")) for r in lost),
        "needs_review": len(stuck),
        "images_deleted": sum(_forget_image(r["image_public_id"]) for r in images),
    }
    log.info(f"Cron dispatch: {summary}")
    return summary


# ---- Internals -------------------------------------------------------------

def _after_failure(row: dict, kind: FailureKind, error: str) -> str:
    if kind is FailureKind.TRANSIENT and row["attempts"] < MAX_ATTEMPTS:
        _retry_later(row, error)
        return "retrying"
    if kind is FailureKind.TRANSIENT:
        status, error = "failed", f"Gave up after {row['attempts']} attempts: {error}"
    elif kind is FailureKind.PERMANENT:
        status = "failed"
    else:
        status = "needs_review"
        error = f"Outcome unknown — check the feed before resolving: {error}"
    log.warning(f"{row['brand']} on {row['platform']} ({row['id']}) -> {status}: {error}")
    _finish(row, status, error=error)
    return status


def _retry_later(row: dict, error: str) -> None:
    """Put the row back to `scheduled` and send it again after the backoff."""
    attempt = row["attempts"]
    delay = RETRY_BACKOFF[attempt - 1]
    back = _write(
        """UPDATE publications SET status = 'scheduled', error = %(error)s, enqueued_at = NULL
           WHERE id = %(id)s AND version = %(version)s
             AND status IN ('publishing', 'needs_review')
           RETURNING *""",
        {"error": _clip(f"Retrying in {delay.seconds // 60} min: {error}"),
         "id": row["id"], "version": row["version"]})
    if back is None:
        return
    log.warning(f"{row['brand']} on {row['platform']} ({row['id']}): nothing was "
                f"posted; retrying in {delay} ({error})")
    if not _enqueue(back, key=_key(back, f"retry{attempt}"), delay=delay):
        log.warning(f"{row['id']} could not be re-sent now; the daily cron will send it")


def _finish(row: dict, status: str, *, external_url: str | None = None,
            error: str | None = None) -> None:
    """Record the outcome. A cron may have moved the row to needs_review while
    this attempt was still running; the attempt knows better, so it wins."""
    _write(
        """UPDATE publications
           SET status = %(status)s, external_url = %(url)s, error = %(error)s,
               published_at = %(published_at)s
           WHERE id = %(id)s AND version = %(version)s
             AND status IN ('publishing', 'needs_review')
           RETURNING id""",
        {"status": status, "url": external_url, "error": _clip(error),
         "published_at": _now() if status == "published" else None,
         "id": row["id"], "version": row["version"]})


def _write(sql: str, params: dict) -> dict | None:
    """Run one UPDATE, retrying briefly. Never raises.

    If every try fails the row stays `publishing`, and the daily cron moves it
    to needs_review — the safe default, since a human then checks the feed.
    """
    for wait in (*_WRITE_RETRY_SECONDS, None):
        try:
            with db.connect() as conn:
                return conn.execute(sql, params).fetchone()
        except (psycopg.Error, db.DatabaseNotConfigured) as exc:
            if wait is None:
                log.critical(f"Could not record the outcome for {params.get('id')}: "
                             f"{exc}. The daily cron will flag it for review.")
                return None
            log.warning(f"Database write failed ({type(exc).__name__}); retrying in {wait}s")
            time.sleep(wait)
    return None


def _send_due(conn: psycopg.Connection, rows: list[dict]) -> None:
    """Send the rows due within ENQUEUE_WINDOW and mark them, in `conn`'s
    transaction. A failed send raises, rolling the transaction back."""
    horizon = _now() + ENQUEUE_WINDOW
    sent = []
    for row in rows:
        if row["scheduled_at"] <= horizon and _send(row, key=_key(row)):
            sent.append(row["id"])
    if sent:
        conn.execute("UPDATE publications SET enqueued_at = now() WHERE id = ANY(%s)", (sent,))


def _enqueue(row: dict, *, key: str, delay: timedelta | None = None) -> bool:
    """Send one row outside any transaction and mark it. Never raises."""
    try:
        if not _send(row, key=key, delay=delay):
            return False
        with db.connect() as conn:
            conn.execute("UPDATE publications SET enqueued_at = now() "
                         "WHERE id = %s AND version = %s", (row["id"], row["version"]))
    except Exception as exc:
        log.error(f"Could not enqueue {row['id']}: {type(exc).__name__}: {exc}")
        return False
    return True


def _send(row: dict, *, key: str, delay: timedelta | None = None) -> bool:
    """Hand `{id, version}` to the queue, due at the row's time (or after `delay`).

    False when the queue is switched off (local runs), leaving `enqueued_at`
    null so scripts/run_due.py or the cron picks the row up.
    """
    if not get_settings().queue_active:
        log.info(f"Queue disabled; {row['id']} waits for scripts/run_due.py or the cron")
        return False
    wait = delay if delay is not None else max(timedelta(0), row["scheduled_at"] - _now())
    _queue().send(TOPIC, {"id": str(row["id"]), "version": row["version"]},
                  delay=wait, retention=min(wait + _RETENTION_SLACK, _MAX_RETENTION),
                  idempotency_key=key)
    log.info(f"Enqueued {row['id']} v{row['version']} to run in {wait} (key {key})")
    return True


@lru_cache
def _queue() -> QueueClient:
    # Holds no connections; region and deployment come from Vercel's env.
    return QueueClient()


def _locked(conn: psycopg.Connection, publication_id: uuid.UUID,
            status: str = "scheduled") -> dict:
    """The row, locked for this transaction, if it is in `status`."""
    row = conn.execute("SELECT * FROM publications WHERE id = %s FOR UPDATE",
                       (publication_id,)).fetchone()
    if row is None:
        raise NotFound(f"No scheduled post {publication_id}.")
    if row["status"] != status:
        raise Conflict(f"This post is {row['status']}; only a {status} post can be changed.")
    return row


def _forget_image(public_id: str) -> bool:
    try:
        storage.delete(public_id)
    except storage.StorageError as exc:
        log.warning(f"Could not delete image {public_id}: {exc}")
        return False
    return True


def _key(row: dict, suffix: str = "") -> str:
    base = f"{row['id']}:{row['version']}"
    return f"{base}:{suffix}" if suffix else base


def _clip(text: str | None) -> str | None:
    return text[:_ERROR_MAX] if text else text


def _now() -> datetime:
    return datetime.now(UTC)
