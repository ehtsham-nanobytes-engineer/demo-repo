"""/schedule, /cron/dispatch and the API key, with the scheduler faked."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app import scheduler, storage

DRAFT = "0123456789abcdef0123456789abcdef"
CRON = {"Authorization": "Bearer cron-test-secret"}


def later(**delta) -> str:
    return (datetime.now(UTC) + timedelta(**(delta or {"hours": 2}))).isoformat()


def body(**overrides):
    request = {"brand": "nbt", "scheduled_at": later(), "draft_id": DRAFT,
               "image_alt": "alt", "targets": [
                   {"platform": "linkedin", "post": "the linkedin post"},
                   {"platform": "instagram", "post": "the caption"}]}
    request.update(overrides)
    return request


@pytest.fixture
def created(monkeypatch):
    calls = []

    def create(brand, scheduled_at, targets, image, image_alt):
        calls.append({"brand": brand, "at": scheduled_at, "targets": targets,
                      "image": image, "alt": image_alt})
        return [{"id": uuid.uuid4(), "platform": p, "status": "scheduled",
                 "scheduled_at": scheduled_at} for p, _ in targets]
    monkeypatch.setattr(scheduler, "create", create)
    return calls


@pytest.fixture
def swept(monkeypatch):
    calls = []

    def sweep(older_than):
        calls.append(older_than)
        return 4
    monkeypatch.setattr(storage, "sweep_drafts", sweep)
    return calls


def test_schedule_saves_every_target_in_utc(client, created):
    at = (datetime.now(UTC) + timedelta(hours=3)).astimezone().replace(microsecond=0)
    ok = client.post("/schedule", json=body(
        scheduled_at=at.isoformat(timespec="seconds")))
    assert ok.status_code == 200, ok.text
    assert [i["platform"] for i in ok.json()["items"]] == ["linkedin", "instagram"]
    call = created[0]
    assert call["at"].utcoffset() == timedelta(0) and call["at"] == at
    assert call["targets"] == [("linkedin", "the linkedin post"),
                               ("instagram", "the caption")]
    assert call["image"] == f"social/drafts/{DRAFT}/nbt"  # the brand's own draft


@pytest.mark.parametrize(("overrides", "message"), [
    ({"scheduled_at": "2030-01-01T09:00:00"}, "timezone"),
    ({"scheduled_at": "2020-01-01T09:00:00+00:00"}, "future"),
    ({"scheduled_at": later(seconds=20)}, "future"),
    ({"draft_id": None}, "Instagram posts need an image"),
    ({"draft_id": "social/nbt/x"}, "draft_id"),
    ({"targets": []}, "at least 1"),
    ({"targets": [{"platform": "linkedin", "post": "a"},
                  {"platform": "linkedin", "post": "b"}]}, "only once"),
    ({"targets": [{"platform": "instagram", "post": "x" * 2201}]}, "at most 2200"),
    ({"brand": "nope"}, "Unknown brand"),
])
def test_schedule_rejects(client, created, overrides, message):
    rejected = client.post("/schedule", json=body(**overrides))
    assert rejected.status_code == 422
    assert message in rejected.text
    assert created == []


def test_schedule_rejects_an_unconfigured_platform(client, created, fake_env, monkeypatch):
    monkeypatch.setattr(fake_env.nbt, "linkedin_author_urn", None)
    rejected = client.post("/schedule", json=body())
    assert rejected.status_code == 422
    assert "NBT__LINKEDIN_AUTHOR_URN" in rejected.text


def test_schedule_reports_an_expired_draft(client, monkeypatch):
    def create(*_):
        raise storage.ImageMissing("The image is no longer stored: drafts expire.")
    monkeypatch.setattr(scheduler, "create", create)
    gone = client.post("/schedule", json=body())
    assert gone.status_code == 410 and "expire" in gone.json()["detail"]


def test_text_only_scheduling_needs_no_draft(client, created):
    ok = client.post("/schedule", json=body(
        draft_id=None, targets=[{"platform": "linkedin", "post": "text"}]))
    assert ok.status_code == 200 and created[0]["image"] is None


def test_scheduling_is_off_without_a_database(client, created, fake_env, monkeypatch):
    monkeypatch.setattr(fake_env, "database_url", None)
    assert client.post("/schedule", json=body()).status_code == 503
    assert client.get("/schedule").status_code == 503


def test_patch_cancel_and_resolve_map_state_errors(client, monkeypatch):
    def not_found(*_, **__):
        raise scheduler.NotFound("No scheduled post.")

    def conflict(*_, **__):
        raise scheduler.Conflict("This post is published.")

    monkeypatch.setattr(scheduler, "update", not_found)
    monkeypatch.setattr(scheduler, "cancel", conflict)
    monkeypatch.setattr(scheduler, "resolve", conflict)
    some_id = uuid.uuid4()
    assert client.patch(f"/schedule/{some_id}", json={"post": "x"}).status_code == 404
    assert client.delete(f"/schedule/{some_id}").status_code == 409
    assert client.post(f"/schedule/{some_id}/resolve",
                       json={"published": True}).status_code == 409


def test_patch_needs_a_change_and_a_future_time(client):
    some_id = uuid.uuid4()
    assert client.patch(f"/schedule/{some_id}", json={}).status_code == 422
    assert client.patch(f"/schedule/{some_id}",
                        json={"scheduled_at": "2030-01-01T09:00:00"}).status_code == 422


@pytest.mark.parametrize("header", [None, "Bearer wrong", "cron-test-secret"])
def test_cron_rejects_anything_but_the_secret(client, monkeypatch, swept, header):
    monkeypatch.setattr(scheduler, "dispatch", lambda: pytest.fail("ran without auth"))
    headers = {"Authorization": header} if header else {}
    assert client.get("/cron/dispatch", headers=headers).status_code == 401
    assert swept == []


def test_cron_is_closed_while_the_secret_is_unset(client, fake_env, monkeypatch, swept):
    monkeypatch.setattr(fake_env, "cron_secret", None)
    monkeypatch.setattr(scheduler, "dispatch", lambda: pytest.fail("ran without auth"))
    assert client.get("/cron/dispatch",
                      headers={"Authorization": "Bearer None"}).status_code == 401


def test_cron_dispatches_and_sweeps_drafts(client, monkeypatch, swept):
    monkeypatch.setattr(scheduler, "dispatch", lambda: {"enqueued": 2})
    ok = client.get("/cron/dispatch", headers=CRON)
    assert ok.status_code == 200 and ok.json() == {"enqueued": 2, "drafts_deleted": 4}
    assert swept == [timedelta(hours=24)]


def test_cron_sweeps_drafts_even_without_a_database(client, fake_env, monkeypatch, swept):
    monkeypatch.setattr(fake_env, "database_url", None)
    monkeypatch.setattr(scheduler, "dispatch", lambda: pytest.fail("needs the database"))
    ok = client.get("/cron/dispatch", headers=CRON)
    assert ok.status_code == 200 and ok.json() == {"drafts_deleted": 4}


def test_a_failed_sweep_does_not_fail_the_cron(client, monkeypatch):
    def down(_):
        raise storage.StorageError("Draft sweep failed: 420 rate limited")
    monkeypatch.setattr(storage, "sweep_drafts", down)
    monkeypatch.setattr(scheduler, "dispatch", lambda: {"enqueued": 0})
    ok = client.get("/cron/dispatch", headers=CRON)
    assert ok.status_code == 200 and ok.json()["drafts_deleted"] == 0


def test_api_key_guards_everything_but_the_cron(client, fake_env, monkeypatch, swept):
    monkeypatch.setattr(fake_env, "api_key", "k-123")
    monkeypatch.setattr(scheduler, "dispatch", lambda: {})
    assert client.get("/brands").status_code == 401
    assert client.get("/brands", headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/brands", headers={"X-API-Key": "k-123"}).status_code == 200
    assert client.get("/cron/dispatch", headers=CRON).status_code == 200
