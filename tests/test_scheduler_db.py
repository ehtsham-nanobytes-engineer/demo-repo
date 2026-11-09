"""The claim rules, against a real Postgres (a Neon dev branch).

Skipped unless TEST_DATABASE_URL is set, in the environment or in .env.test.
Point it at a Neon *dev* branch, never production. The table is created from
migrations/001_publications.sql if it is missing; each test deletes the rows
it inserted. Nothing here touches the queue, Cloudinary or any platform.
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

from app import scheduler

ROOT = Path(__file__).resolve().parent.parent
TEST_URL = os.environ.get("TEST_DATABASE_URL") or dotenv_values(
    ROOT / ".env.test").get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL is not set")


@pytest.fixture
def database(fake_env, monkeypatch):
    monkeypatch.setattr(fake_env, "database_url", TEST_URL)
    with psycopg.connect(TEST_URL, sslmode="require", autocommit=True) as conn:
        exists = conn.execute("SELECT to_regclass('publications')").fetchone()[0]
        if not exists:
            conn.execute((ROOT / "migrations/001_publications.sql").read_text("utf-8"))
    created = []
    yield created
    with psycopg.connect(TEST_URL, sslmode="require", autocommit=True) as conn:
        conn.execute("DELETE FROM publications WHERE id = ANY(%s)", (created,))


def schedule(database, *, minutes=90, platform="linkedin") -> dict:
    """One text-only row via the real create(); the queue is off in fake_env."""
    at = datetime.now(UTC) + timedelta(minutes=minutes)
    row = scheduler.create("nbt", at, [(platform, "pytest post")], None, None)[0]
    database.append(row["id"])
    return row


def test_create_stores_utc_and_waits_for_the_queue(database):
    row = schedule(database)
    assert row["status"] == "scheduled" and row["version"] == 1
    assert row["scheduled_at"].utcoffset() == timedelta(0)
    assert row["enqueued_at"] is None  # queue disabled: nothing was sent


def test_claim_twice_second_is_a_no_op(database):
    row = schedule(database)
    first = scheduler.claim(str(row["id"]), 1)
    assert first["status"] == "publishing" and first["attempts"] == 1
    assert scheduler.claim(str(row["id"]), 1) is None


def test_reschedule_makes_the_old_message_stale(database):
    row = schedule(database)
    moved = scheduler.update(row["id"],
                             scheduled_at=datetime.now(UTC) + timedelta(hours=3))
    assert moved["version"] == 2 and moved["enqueued_at"] is None
    assert scheduler.claim(str(row["id"]), 1) is None      # the old message
    assert scheduler.claim(str(row["id"]), 2) is not None  # the new one


def test_cancelled_rows_are_never_claimed(database):
    row = schedule(database)
    assert scheduler.cancel(row["id"])["status"] == "cancelled"
    assert scheduler.claim(str(row["id"]), 1) is None
    with pytest.raises(scheduler.Conflict):
        scheduler.cancel(row["id"])


def test_edit_only_while_scheduled(database):
    row = schedule(database)
    edited = scheduler.update(row["id"], post="edited text")
    assert edited["post"] == "edited text" and edited["version"] == 1
    with pytest.raises(ValueError):
        scheduler.update(row["id"], post="x" * 3001)
    scheduler.claim(str(row["id"]), 1)
    with pytest.raises(scheduler.Conflict):
        scheduler.update(row["id"], post="too late")


def test_resolve_only_needs_review(database):
    row = schedule(database)
    with pytest.raises(scheduler.Conflict):
        scheduler.resolve(row["id"], published=True)
    claimed = scheduler.claim(str(row["id"]), 1)
    scheduler._finish(claimed, "needs_review", error="timed out")
    done = scheduler.resolve(row["id"], published=True, external_url="https://li.test/9")
    assert done["status"] == "published" and done["external_url"] == "https://li.test/9"


def test_instagram_rows_require_an_image(database):
    with pytest.raises(psycopg.errors.CheckViolation):
        schedule(database, platform="instagram")
