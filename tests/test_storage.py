"""app.storage against a faked Cloudinary: drafts, the sweep, missing images."""

from datetime import UTC, datetime, timedelta

import cloudinary.api
import cloudinary.exceptions
import pytest

from app import storage
from tests.fakes import response

DRAFT = "0123456789abcdef0123456789abcdef"


def stamp(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.parametrize("bad", ["", "abc", DRAFT.upper(), "../" + DRAFT[3:], DRAFT + "0"])
def test_only_our_own_draft_ids_become_public_ids(bad):
    assert not storage.valid_draft_id(bad)
    with pytest.raises(ValueError):
        storage.draft_public_id(bad, "nbt")
    assert storage.draft_public_id(DRAFT, "nbt") == f"social/drafts/{DRAFT}/nbt"


def test_delete_draft_removes_every_brand_by_prefix(fake_env, monkeypatch):
    seen = []
    monkeypatch.setattr(cloudinary.api, "delete_resources_by_prefix",
                        lambda prefix, **kw: seen.append((prefix, kw["type"])))
    storage.delete_draft(DRAFT)
    # The trailing slash keeps one draft's prefix from matching another's.
    assert seen == [(f"social/drafts/{DRAFT}/", "authenticated")]


def test_sweep_pages_through_and_deletes_only_old_drafts(fake_env, monkeypatch):
    pages = {
        None: {"resources": [{"public_id": "social/drafts/a/nbt", "created_at": stamp(30)},
                             {"public_id": "social/drafts/b/nbt", "created_at": stamp(2)}],
               "next_cursor": "p2"},
        "p2": {"resources": [{"public_id": f"social/drafts/c{i}/nbt", "created_at": stamp(25)}
                             for i in range(150)]},
    }
    listed, deleted = [], []

    def resources(**kw):
        listed.append((kw["prefix"], kw.get("next_cursor")))
        return pages[kw.get("next_cursor")]

    monkeypatch.setattr(cloudinary.api, "resources", resources)
    monkeypatch.setattr(cloudinary.api, "delete_resources",
                        lambda ids, **kw: deleted.append(list(ids)))

    assert storage.sweep_drafts(timedelta(hours=24)) == 151
    assert listed == [("social/drafts/", None), ("social/drafts/", "p2")]
    assert [len(batch) for batch in deleted] == [100, 51]  # the Admin API's cap
    assert "social/drafts/b/nbt" not in sum(deleted, [])   # 2h old: kept


def test_sweep_failure_is_a_storage_error(fake_env, monkeypatch):
    def limited(**_):
        raise cloudinary.exceptions.RateLimited("420")
    monkeypatch.setattr(cloudinary.api, "resources", limited)
    with pytest.raises(storage.StorageError):
        storage.sweep_drafts(timedelta(hours=24))


def test_a_missing_draft_says_to_regenerate(fake_env, monkeypatch):
    monkeypatch.setattr(storage.httpx, "get", lambda *a, **k: response(404))
    with pytest.raises(storage.ImageMissing) as caught:
        storage.original_bytes(storage.draft_public_id(DRAFT, "nbt"))
    assert "Regenerate" in str(caught.value)


def test_other_download_failures_are_not_missing(fake_env, monkeypatch):
    monkeypatch.setattr(storage.httpx, "get", lambda *a, **k: response(500))
    with pytest.raises(storage.StorageError) as caught:
        storage.original_bytes("social/nbt/a")
    assert not isinstance(caught.value, storage.ImageMissing)


def test_require_maps_not_found(fake_env, monkeypatch):
    def gone(*_, **__):
        raise cloudinary.exceptions.NotFound("no such resource")
    monkeypatch.setattr(cloudinary.api, "resource", gone)
    with pytest.raises(storage.ImageMissing):
        storage.require("social/nbt/a")


def test_copy_reuploads_the_exact_bytes_outside_the_drafts(fake_env, monkeypatch):
    original = b"\x89PNG\r\n\x1a\n" + b"approved"
    stored = {}
    monkeypatch.setattr(storage, "original_bytes", lambda pid: original)
    monkeypatch.setattr(storage, "upload_as",
                        lambda image, pid: stored.setdefault(pid, image) and pid)
    copied = storage.copy(storage.draft_public_id(DRAFT, "nbt"), "nbt")
    assert copied.startswith("social/nbt/") and not copied.startswith("social/drafts/")
    assert stored[copied] == original
