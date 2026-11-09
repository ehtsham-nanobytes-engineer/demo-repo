"""publish_target: the one path both publish-now and the scheduler use."""

import pytest

from app import linkedin, meta, publishing, storage
from app.publish_errors import FailureKind, PublishError

PNG = b"\x89PNG\r\n\x1a\n" + b"approved-bytes"  # the branded draft, as stored
DRAFT = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def calls(monkeypatch, fake_env):
    """Record every platform and storage call instead of making it."""
    log = []

    def record(name, result=""):
        def fn(*args, **kwargs):
            log.append((name, args, kwargs))
            return result
        return fn

    monkeypatch.setattr(linkedin, "publish_post", record("linkedin", "https://li.test/1"))
    monkeypatch.setattr(meta, "publish_facebook", record("facebook", "https://fb.test/1"))
    monkeypatch.setattr(meta, "publish_instagram", record("instagram", "https://ig.test/1"))
    monkeypatch.setattr(storage, "original_bytes", record("original_bytes", PNG))
    monkeypatch.setattr(storage, "require", record("require"))
    monkeypatch.setattr(storage, "instagram_url",
                        lambda pid: f"https://res.test/{pid}.jpg")
    return log


def names(log):
    return [name for name, *_ in log]


def missing(public_id):
    raise storage.ImageMissing("The image is no longer stored: drafts expire.")


def test_linkedin_gets_the_stored_bytes_untouched(calls):
    url = publishing.publish_target("nbt", "linkedin", "post", image_public_id="social/nbt/a")
    assert url == "https://li.test/1"
    assert names(calls) == ["original_bytes", "linkedin"]
    assert calls[1][2]["image"] == PNG  # no re-branding, no re-encode


def test_instagram_fetches_the_stored_image_by_url(calls):
    publishing.publish_target("nbt", "instagram", "caption", image_public_id="social/nbt/a")
    assert names(calls) == ["require", "instagram"]
    assert calls[1][1] == ("caption", "https://res.test/social/nbt/a.jpg")


def test_text_only_posts_touch_no_storage(calls):
    publishing.publish_target("nbt", "facebook", "post")
    assert names(calls) == ["facebook"]
    assert calls[0][1] == ("post", None)


@pytest.mark.parametrize("platform", ["linkedin", "facebook", "instagram"])
def test_an_expired_draft_publishes_nothing(calls, monkeypatch, platform):
    monkeypatch.setattr(storage, "original_bytes", missing)
    monkeypatch.setattr(storage, "require", missing)
    with pytest.raises(PublishError) as caught:
        publishing.publish_target("nbt", platform, "post",
                                  image_public_id=storage.draft_public_id(DRAFT, "nbt"))
    assert caught.value.kind is FailureKind.PERMANENT  # a retry will not bring it back
    assert "expire" in str(caught.value)
    assert calls == []


def test_credentials_are_rechecked_at_publish_time(calls, fake_env, monkeypatch):
    monkeypatch.setattr(fake_env.nbt, "meta_page_token", None)
    with pytest.raises(PublishError) as caught:
        publishing.publish_target("nbt", "facebook", "post")
    assert caught.value.kind is FailureKind.PERMANENT
    assert calls == []


def test_storage_outage_is_transient(calls, monkeypatch):
    def down(_):
        raise storage.StorageError("cloudinary is down")
    monkeypatch.setattr(storage, "original_bytes", down)
    with pytest.raises(PublishError) as caught:
        publishing.publish_target("nbt", "facebook", "post", image_public_id="social/nbt/a")
    assert caught.value.kind is FailureKind.TRANSIENT
    assert "facebook" not in names(calls)


def test_publish_route_posts_the_brands_draft(client, calls):
    body = {"brand": "paypronext", "platform": "linkedin", "post": "hello", "draft_id": DRAFT}
    ok = client.post("/publish", json=body)
    assert ok.status_code == 200 and ok.json() == {"url": "https://li.test/1"}
    assert calls[0][1] == (f"social/drafts/{DRAFT}/paypronext",)
    assert calls[-1][2]["image"] == PNG


@pytest.mark.parametrize(("body", "message"), [
    ({"platform": "instagram"}, "Instagram posts need an image"),
    ({"draft_id": "../../social/nbt/other"}, "draft_id"),
    ({"draft_id": DRAFT.upper()}, "draft_id"),
])
def test_publish_route_rejects(client, calls, body, message):
    rejected = client.post("/publish", json={"brand": "nbt", "platform": "linkedin",
                                             "post": "hello", **body})
    assert rejected.status_code == 422 and message in rejected.text
    assert calls == []


def test_publish_route_reports_an_expired_draft(client, calls, monkeypatch):
    monkeypatch.setattr(storage, "original_bytes", missing)
    failed = client.post("/publish", json={"brand": "nbt", "platform": "linkedin",
                                           "post": "x", "draft_id": DRAFT})
    assert failed.status_code == 502 and "expire" in failed.json()["detail"]


def test_publish_route_reports_platform_errors(client, calls, monkeypatch):
    def refuse(*_, **__):
        raise linkedin.LinkedInError("LinkedIn returned 401")
    monkeypatch.setattr(linkedin, "publish_post", refuse)
    failed = client.post("/publish", json={"brand": "nbt", "platform": "linkedin", "post": "x"})
    assert failed.status_code == 502
    assert "401" in failed.json()["detail"]
