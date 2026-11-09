"""POST /image and DELETE /image/{id}. The model and Cloudinary are faked."""

import pytest

from app import storage
from app.imagegen import ImageGenerationError
from app.routes import image as image_route
from tests.fakes import png, size_of

OLD = "f" * 32


@pytest.fixture
def cloud(monkeypatch, fake_env):
    """A fake Cloudinary: what was uploaded, and which drafts were deleted."""
    state = {"uploads": {}, "deleted": [], "renders": []}

    def upload_as(image, public_id):
        state["uploads"][public_id] = image
        return public_id

    def render(prompt):
        state["renders"].append(prompt)
        return png((1024, 1024))  # providers ignore the requested size

    monkeypatch.setattr(storage, "upload_as", upload_as)
    monkeypatch.setattr(storage, "view_url", lambda pid: f"https://res.test/{pid}.png")
    monkeypatch.setattr(storage, "download_url", lambda pid, name: f"https://dl.test/{name}")
    monkeypatch.setattr(storage, "delete_draft", state["deleted"].append)
    monkeypatch.setattr(image_route, "render", render)
    return state


def body(**overrides):
    return {"linkedin": "A post about lab automation.", "brands": ["nbt", "taxpronext"],
            **overrides}


def test_brands_and_stores_only_the_selected_brands(client, cloud):
    ok = client.post("/image", json=body())
    assert ok.status_code == 200, ok.text
    data = ok.json()
    draft = data["draft_id"]
    assert storage.valid_draft_id(draft)
    assert [i["brand"] for i in data["images"]] == ["nbt", "taxpronext"]
    assert sorted(cloud["uploads"]) == [f"social/drafts/{draft}/nbt",
                                        f"social/drafts/{draft}/taxpronext"]
    assert data["images"][0]["url"] == f"https://res.test/social/drafts/{draft}/nbt.png"
    assert data["images"][0]["download_url"] == "https://dl.test/nbt-post"
    assert data["image_prompt"] == cloud["renders"][0] and len(cloud["renders"]) == 1
    assert data["expires_at"]
    # One render, two logos: same frame, different pixels.
    first, second = cloud["uploads"].values()
    assert size_of(first) == size_of(second) == (1200, 627) and first != second
    assert cloud["deleted"] == []


def test_regenerating_deletes_the_previous_draft_after_the_new_one_exists(client, cloud):
    ok = client.post("/image", json=body(previous_draft_id=OLD))
    assert ok.status_code == 200
    assert cloud["deleted"] == [OLD] and cloud["uploads"]


def test_a_failed_render_keeps_the_previous_draft(client, cloud, monkeypatch):
    def fail(_):
        raise ImageGenerationError("rate limited")
    monkeypatch.setattr(image_route, "render", fail)
    assert client.post("/image", json=body(previous_draft_id=OLD)).status_code == 502
    assert cloud["deleted"] == [] and cloud["uploads"] == {}


def test_one_missing_logo_does_not_cost_the_other_brands(client, cloud, fake_env,
                                                         monkeypatch, tmp_path):
    monkeypatch.setattr(fake_env.nbt, "logo_path", str(tmp_path / "gone.png"))
    data = client.post("/image", json=body()).json()
    nbt, tax = data["images"]
    assert nbt["url"] is None and "logo not found" in nbt["error"]
    assert tax["url"] and tax["error"] is None
    assert len(cloud["uploads"]) == 1


def test_nothing_usable_is_a_502_and_leaves_no_draft(client, cloud, monkeypatch):
    monkeypatch.setattr(image_route, "render", lambda _: b"not an image")
    failed = client.post("/image", json=body(previous_draft_id=OLD))
    assert failed.status_code == 502
    assert len(cloud["deleted"]) == 1 and cloud["deleted"][0] != OLD  # its own, not the old


def test_upload_failures_stay_in_the_brands_slot(client, cloud, monkeypatch):
    def flaky(image, public_id):
        if public_id.endswith("/nbt"):
            raise storage.StorageError("Cloudinary rejected the upload")
        cloud["uploads"][public_id] = image
        return public_id
    monkeypatch.setattr(storage, "upload_as", flaky)
    nbt, tax = client.post("/image", json=body()).json()["images"]
    assert "rejected" in nbt["error"] and tax["url"]


def test_no_storage_means_no_render(client, cloud, fake_env, monkeypatch):
    monkeypatch.setattr(fake_env, "cloudinary_url", None)
    assert client.post("/image", json=body()).status_code == 503
    assert cloud["renders"] == []


def test_image_prompt_is_rendered_verbatim(client, cloud):
    client.post("/image", json=body(image_prompt="a crate"))
    assert cloud["renders"] == ["a crate"]


@pytest.mark.parametrize(("overrides", "message"), [
    ({"linkedin": " "}, "Content comes first"),
    ({"brands": []}, "at least 1"),
    ({"brands": ["nbt", "nbt"]}, "only once"),
    ({"brands": ["nope"]}, "Unknown brand"),
    ({"previous_draft_id": "../social/nbt"}, "draft_id"),
])
def test_rejects(client, cloud, overrides, message):
    rejected = client.post("/image", json=body(**overrides))
    assert rejected.status_code == 422 and message in rejected.text
    assert cloud["renders"] == []


def test_discard_deletes_the_draft(client, cloud):
    assert client.delete(f"/image/{OLD}").status_code == 204
    assert cloud["deleted"] == [OLD]
    assert client.delete("/image/not-a-draft").status_code == 422
