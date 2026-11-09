"""Shared fixtures. Nothing in the test suite may reach a real platform.

`no_network` (autouse) makes any real HTTP request through httpx, or any
Cloudinary upload/delete/Admin API call, fail loudly, so a mock that is missing shows up as
an error instead of a post on a real feed. TestClient is unaffected: it uses
its own in-process transport.

`fake_env` swaps the real settings (loaded from .env, with real tokens) for
fake brand credentials, so every platform reads as configured.
"""

import httpx
import pytest

from app.config import BrandSettings, get_settings


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise RuntimeError("Real network access is blocked in tests — mock it.")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
    monkeypatch.setattr("cloudinary.uploader.upload", blocked)
    monkeypatch.setattr("cloudinary.uploader.destroy", blocked)
    for name in ("resources", "resource", "delete_resources", "delete_resources_by_prefix"):
        monkeypatch.setattr(f"cloudinary.api.{name}", blocked)


@pytest.fixture
def fake_env(monkeypatch):
    settings = get_settings()
    for brand in ("nbt", "paypronext", "taxpronext"):
        monkeypatch.setattr(settings, brand, BrandSettings(
            linkedin_author_urn="urn:li:organization:123",
            facebook_page_id="page-1", instagram_user_id="ig-1",
            meta_page_token="meta-token",
        ))
    monkeypatch.setattr(settings, "linkedin_access_token", "li-token")
    monkeypatch.setattr(settings, "cloudinary_url", "cloudinary://key:secret@demo")
    monkeypatch.setattr(settings, "database_url", "postgresql://fake/db")
    monkeypatch.setattr(settings, "queue_enabled", False)
    monkeypatch.setattr(settings, "cron_secret", "cron-test-secret")
    monkeypatch.setattr(settings, "api_key", None)
    return settings


@pytest.fixture
def client(fake_env):
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)

