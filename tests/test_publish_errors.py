"""Every way a platform call can fail lands in the right FailureKind.

This is what keeps a scheduled retry from posting twice: only TRANSIENT is
retried, and a timeout on the call that creates the post is UNKNOWN.
"""

import httpx
import pytest

from app import linkedin, meta
from app.publish_errors import FailureKind, network_failure, status_failure
from tests.fakes import response

T, P, U = FailureKind.TRANSIENT, FailureKind.PERMANENT, FailureKind.UNKNOWN


@pytest.mark.parametrize(("status", "final", "kind"), [
    (429, True, T), (500, True, T), (503, True, T), (504, True, U), (504, False, T),
    (400, True, P), (401, True, P), (403, False, P), (422, True, P),
])
def test_status_failure(status, final, kind):
    assert status_failure(status, final=final) is kind


@pytest.mark.parametrize(("exc", "final", "kind"), [
    (httpx.ConnectError("x"), True, T),
    (httpx.ConnectTimeout("x"), True, T),
    (httpx.ReadTimeout("x"), True, U),
    (httpx.RemoteProtocolError("x"), True, U),
    (httpx.ReadTimeout("x"), False, T),
])
def test_network_failure(exc, final, kind):
    assert network_failure(exc, final=final) is kind


# ---- LinkedIn ---------------------------------------------------------------

def _linkedin(monkeypatch, *, on_post, on_register=None):
    """Route httpx.post by path: the image registration or the post itself."""
    def fake_post(url, **_):
        if "initializeUpload" in url:
            return (on_register or (lambda: response(200, json={"value": {
                "uploadUrl": "https://upload.test", "image": "urn:li:image:1"}})))()
        return on_post()
    monkeypatch.setattr(linkedin.httpx, "post", fake_post)
    monkeypatch.setattr(linkedin.httpx, "put", lambda *a, **k: response(201))


def _raise(exc):
    def go():
        raise exc
    return go


def _publish_linkedin(image=None):
    return linkedin.publish_post("hello", image=image,
                                 author_urn="urn:li:organization:1", token="t")


@pytest.mark.parametrize(("on_post", "kind"), [
    (_raise(httpx.ReadTimeout("slow")), U),
    (_raise(httpx.ConnectError("down")), T),
    (lambda: response(429), T),
    (lambda: response(503), T),
    (lambda: response(401), P),
])
def test_linkedin_post_failures(monkeypatch, on_post, kind):
    _linkedin(monkeypatch, on_post=on_post)
    with pytest.raises(linkedin.LinkedInError) as caught:
        _publish_linkedin(image=b"png")
    assert caught.value.kind is kind


def test_linkedin_timeout_before_the_post_is_safe_to_retry(monkeypatch):
    called = []
    _linkedin(monkeypatch, on_post=lambda: called.append("post"),
              on_register=_raise(httpx.ReadTimeout("slow")))
    with pytest.raises(linkedin.LinkedInError) as caught:
        _publish_linkedin(image=b"png")
    assert caught.value.kind is T
    assert called == []  # the post itself was never attempted


def test_linkedin_missing_config_is_permanent():
    with pytest.raises(linkedin.LinkedInError) as caught:
        linkedin.publish_post("hello", author_urn=None, token=None)
    assert caught.value.kind is P


def test_linkedin_success_returns_url(monkeypatch):
    _linkedin(monkeypatch, on_post=lambda: response(201, headers={"x-restli-id": "urn:li:share:9"}))
    assert _publish_linkedin() == "https://www.linkedin.com/feed/update/urn:li:share:9"


# ---- Meta -------------------------------------------------------------------

def _meta(monkeypatch, handlers):
    """handlers: path suffix -> callable returning a response (or raising)."""
    def fake_request(method, url, **_):
        for suffix, handler in handlers.items():
            if url.split("?")[0].endswith(suffix):
                return handler()
        raise AssertionError(f"unexpected Meta call {method} {url}")
    monkeypatch.setattr(meta.httpx, "request", fake_request)


_CONTAINER = {"/ig-1/media": lambda: response(200, json={"id": "c1"}),
              "/c1": lambda: response(200, json={"status_code": "FINISHED"})}


def _publish_instagram():
    return meta.publish_instagram("caption", "https://img.test/x.jpg",
                                  ig_user_id="ig-1", token="t")


def test_instagram_timeout_on_publish_is_unknown(monkeypatch):
    _meta(monkeypatch, {**_CONTAINER, "/media_publish": _raise(httpx.ReadTimeout("slow"))})
    with pytest.raises(meta.MetaError) as caught:
        _publish_instagram()
    assert caught.value.kind is U


def test_instagram_timeout_making_the_container_is_transient(monkeypatch):
    _meta(monkeypatch, {"/ig-1/media": _raise(httpx.ReadTimeout("slow"))})
    with pytest.raises(meta.MetaError) as caught:
        _publish_instagram()
    assert caught.value.kind is T


@pytest.mark.parametrize(("error", "status", "kind"), [
    ({"code": 190, "message": "token expired"}, 400, P),
    ({"code": 4, "message": "rate limited"}, 400, T),
    ({"code": 100, "message": "odd", "is_transient": True}, 400, T),
    ({"code": 1, "message": "unknown"}, 500, T),
])
def test_facebook_graph_errors(monkeypatch, error, status, kind):
    _meta(monkeypatch, {"/page-1/feed": lambda: response(status, json={"error": error})})
    with pytest.raises(meta.MetaError) as caught:
        meta.publish_facebook("hello", None, page_id="page-1", token="t")
    assert caught.value.kind is kind


def test_instagram_success_reads_permalink(monkeypatch):
    _meta(monkeypatch, {**_CONTAINER,
                        "/media_publish": lambda: response(200, json={"id": "m1"}),
                        "/m1": lambda: response(200, json={"permalink": "https://ig.test/p/1"})})
    assert _publish_instagram() == "https://ig.test/p/1"
