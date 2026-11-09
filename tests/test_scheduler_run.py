"""scheduler.run: claim → publish → record, with the database faked.

The claim itself (that a second claim is a no-op, that an old version or a
cancelled row is ignored) is real SQL and is tested against Postgres in
test_scheduler_db.py. Here the question is what `run` does with each outcome.
"""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app import linkedin, publishing, scheduler, storage
from app.publish_errors import FailureKind, PublishError
from tests.fakes import png, response

ROW_ID = uuid.uuid4()


def make_row(**overrides):
    row = {"id": ROW_ID, "brand": "nbt", "platform": "linkedin", "post": "hello",
           "image_public_id": "social/nbt/a", "image_alt": "alt", "version": 1,
           "attempts": 1, "status": "publishing",
           "scheduled_at": datetime.now(UTC) - timedelta(seconds=5)}
    row.update(overrides)
    return row


@pytest.fixture
def db(monkeypatch, fake_env):
    """A fake database: one claimable row, and a log of every write."""
    state = {"row": make_row(), "writes": [], "enqueued": [], "claims": 0}

    def claim(publication_id, version):
        state["claims"] += 1
        return state["row"]

    def write(sql, params):
        state["writes"].append(params)
        return {**state["row"], "status": params.get("status", "scheduled")}

    def enqueue(row, *, key, delay=None):
        state["enqueued"].append({"key": key, "delay": delay})
        return True

    monkeypatch.setattr(scheduler, "claim", claim)
    monkeypatch.setattr(scheduler, "_write", write)
    monkeypatch.setattr(scheduler, "_enqueue", enqueue)
    monkeypatch.setattr(scheduler.time, "sleep", lambda _: None)
    return state


def publishes(monkeypatch, result):
    """Make publish_target return `result`, or raise it if it is an exception."""
    seen = []

    def fake(*args, **kwargs):
        seen.append((args, kwargs))
        if isinstance(result, BaseException):
            raise result
        return result
    monkeypatch.setattr(publishing, "publish_target", fake)
    return seen


def run():
    return scheduler.run(str(ROW_ID), 1)


def test_nothing_to_claim_publishes_nothing(db, monkeypatch):
    db["row"] = None
    seen = publishes(monkeypatch, "https://li.test/1")
    assert run() == "skipped"
    assert seen == [] and db["writes"] == []


def test_success_records_the_url(db, monkeypatch):
    seen = publishes(monkeypatch, "https://li.test/1")
    assert run() == "published"
    args, kwargs = seen[0]
    assert args == ("nbt", "linkedin", "hello")
    assert kwargs["image_public_id"] == "social/nbt/a"  # the stored copy, nothing new
    assert db["writes"][-1]["status"] == "published"
    assert db["writes"][-1]["url"] == "https://li.test/1"


def test_transient_failure_is_retried_with_backoff(db, monkeypatch):
    publishes(monkeypatch, PublishError("429", FailureKind.TRANSIENT))
    assert run() == "retrying"
    assert db["writes"][-1]["error"].startswith("Retrying in 2 min")
    assert db["enqueued"] == [{"key": f"{ROW_ID}:1:retry1", "delay": timedelta(minutes=2)}]


def test_backoff_grows_then_gives_up(db, monkeypatch):
    publishes(monkeypatch, PublishError("503", FailureKind.TRANSIENT))
    db["row"] = make_row(attempts=3)
    assert run() == "retrying"
    assert db["enqueued"][-1]["delay"] == timedelta(minutes=8)

    db["row"] = make_row(attempts=scheduler.MAX_ATTEMPTS)
    assert run() == "failed"
    assert db["writes"][-1]["error"].startswith(f"Gave up after {scheduler.MAX_ATTEMPTS}")


def test_permanent_failure_fails_without_retry(db, monkeypatch):
    publishes(monkeypatch, PublishError("401 bad token", FailureKind.PERMANENT))
    assert run() == "failed"
    assert db["enqueued"] == []
    assert "401" in db["writes"][-1]["error"]


def test_unknown_outcome_goes_to_review_and_is_never_retried(db, monkeypatch):
    publishes(monkeypatch, PublishError("timed out", FailureKind.UNKNOWN))
    assert run() == "needs_review"
    assert db["enqueued"] == []


def test_a_crash_mid_publish_goes_to_review_and_does_not_raise(db, monkeypatch):
    publishes(monkeypatch, KeyError("boom"))
    assert run() == "needs_review"
    assert db["enqueued"] == []


def test_malformed_id_is_ignored(db, monkeypatch):
    seen = publishes(monkeypatch, "x")
    assert scheduler.run("not-a-uuid", 1) == "skipped"
    assert db["claims"] == 0 and seen == []


# ---- Through the real clients: only the HTTP layer is faked ----------------

@pytest.fixture
def real_linkedin(monkeypatch, db):
    monkeypatch.setattr(storage, "original_bytes", lambda _: png())
    monkeypatch.setattr(linkedin.httpx, "put", lambda *a, **k: response(201))

    def with_post(on_post):
        def fake_post(url, **_):
            if "initializeUpload" in url:
                return response(200, json={"value": {"uploadUrl": "https://u.test",
                                                     "image": "urn:li:image:1"}})
            return on_post()
        monkeypatch.setattr(linkedin.httpx, "post", fake_post)
    return with_post


def test_timeout_on_the_post_goes_to_review(db, real_linkedin):
    def timeout():
        raise httpx.ReadTimeout("no answer")
    real_linkedin(timeout)
    assert run() == "needs_review"
    assert db["enqueued"] == []


def test_429_is_retried(db, real_linkedin):
    real_linkedin(lambda: response(429, text="slow down"))
    assert run() == "retrying"
    assert db["enqueued"][0]["key"] == f"{ROW_ID}:1:retry1"


def test_claim_errors_propagate_so_the_queue_retries(monkeypatch, fake_env):
    def down(*_):
        raise scheduler.ClaimUnavailable("db down")
    monkeypatch.setattr(scheduler, "claim", down)
    with pytest.raises(scheduler.ClaimUnavailable):
        run()
