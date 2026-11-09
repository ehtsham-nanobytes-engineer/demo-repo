"""Facebook Page and Instagram publishing, through the Meta Graph API.

Both run on one Page access token per brand (`<BRAND>__META_PAGE_TOKEN`), which
needs `pages_manage_posts` and `pages_read_engagement` for the Page, and
`instagram_basic` plus `instagram_content_publish` for the Instagram
professional account linked to it. OAuth is handled separately by the org — we
only consume the token.

Facebook takes the image bytes directly: one multipart `POST /{page}/photos`.

Instagram does not. It fetches the picture from a public URL, so a post is
two calls plus a wait:

1. `POST /{ig-user}/media` makes a container from `image_url` and the caption;
2. once the container reports FINISHED, `POST /{ig-user}/media_publish`
   publishes it.

`image_url` is a Cloudinary derived URL from `app.storage.instagram_url`: the
approved image as JPEG, inside Instagram's accepted aspect ratios. Cloudinary
rather than this API serves it because on serverless the instance Meta's fetch
reaches is not the one holding the image.

Every error is a `MetaError` carrying a `FailureKind` (see app.publish_errors),
so the scheduler knows whether a retry could post twice.
"""

import time

import httpx

from app.config import get_settings
from app.logging_setup import get_logger, preview
from app.publish_errors import FailureKind, PublishError, network_failure, status_failure

log = get_logger("meta")

# How long to wait for Instagram to finish fetching and processing the image.
_IG_READY_TIMEOUT_SECONDS = 60.0
_IG_POLL_SECONDS = 2.0

# Graph error codes that mean "slow down" rather than "no": rate limits on the
# app, the user, the Page and Instagram, plus Meta's own temporary failures.
_TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 613, 80001, 80002}

_PERMISSION_HINT = (
    "check that the brand's META_PAGE_TOKEN is a Page access token (not a user "
    "token) with pages_manage_posts, pages_read_engagement, instagram_basic and "
    "instagram_content_publish, and that the page and Instagram ids belong to it."
)


class MetaError(PublishError):
    """Raised when Meta rejects a request or config is missing."""


def publish_facebook(post: str, image: bytes | None, *, page_id: str | None,
                     token: str | None) -> str:
    """Publish `post` to a Facebook Page, optionally with one image; return its URL."""
    if not page_id or not token:
        raise MetaError("Facebook is not configured for this brand: set "
                        "<BRAND>__FACEBOOK_PAGE_ID and <BRAND>__META_PAGE_TOKEN.")

    log.info(f"Publishing {len(post)} characters to Facebook Page {page_id}"
             f"{' with an image' if image else ''}")

    if image:
        body = _call("POST", f"/{page_id}/photos", token, what="the Facebook photo",
                     final=True, data={"message": post},
                     files={"source": ("post.png", image, "image/png")})
        post_id = body.get("post_id") or body.get("id")
    else:
        body = _call("POST", f"/{page_id}/feed", token, what="the Facebook post",
                     final=True, data={"message": post})
        post_id = body.get("id")

    if not post_id:
        log.warning("Facebook accepted the post but returned no id — no URL to show")
        return ""
    url = f"https://www.facebook.com/{post_id}"
    log.info(f"Published: {url}")
    return url


def publish_instagram(caption: str, image_url: str, *, ig_user_id: str | None,
                      token: str | None) -> str:
    """Publish the image at `image_url` with `caption`; return its permalink."""
    if not ig_user_id or not token:
        raise MetaError("Instagram is not configured for this brand: set "
                        "<BRAND>__INSTAGRAM_USER_ID and <BRAND>__META_PAGE_TOKEN.")

    log.info(f"Publishing {len(caption)} characters with an image to Instagram "
             f"account {ig_user_id}")
    container = _call("POST", f"/{ig_user_id}/media", token,
                      what="the Instagram media container",
                      data={"image_url": image_url, "caption": caption}).get("id")
    if not container:
        raise MetaError("Instagram did not return a media container id.",
                        FailureKind.TRANSIENT)

    _wait_until_ready(container, token)

    media_id = _call("POST", f"/{ig_user_id}/media_publish", token,
                     what="the Instagram publish", final=True,
                     data={"creation_id": container}).get("id")

    if not media_id:
        log.warning("Instagram accepted the post but returned no id — no URL to show")
        return ""

    # The permalink is a separate read; a failure here must not report an
    # already-published post as failed.
    try:
        permalink = _call("GET", f"/{media_id}", token, what="the permalink",
                          params={"fields": "permalink"}).get("permalink", "")
    except MetaError as exc:
        log.warning(f"Published, but the permalink could not be read: {exc}")
        permalink = ""
    log.info(f"Published: {permalink or media_id}")
    return permalink


def _wait_until_ready(container: str, token: str) -> None:
    """Poll the container until Instagram has fetched and processed the image."""
    deadline = time.monotonic() + _IG_READY_TIMEOUT_SECONDS
    while True:
        status = _call("GET", f"/{container}", token, what="the container status",
                       params={"fields": "status_code,status"})
        code = status.get("status_code")
        if code in ("FINISHED", "PUBLISHED"):
            return
        if code in ("ERROR", "EXPIRED"):
            raise MetaError(f"Instagram could not process the image ({code}): "
                            f"{status.get('status', 'no detail')}. It must be able "
                            f"to fetch the Cloudinary image URL from the internet.")
        if time.monotonic() > deadline:
            # Nothing is published until media_publish, so this is safe to retry.
            raise MetaError("Instagram is still processing the image; try again shortly.",
                            FailureKind.TRANSIENT)
        time.sleep(_IG_POLL_SECONDS)


def _call(method: str, path: str, token: str, *, what: str, final: bool = False,
          **kwargs) -> dict:
    """One authenticated Graph API call, errors surfaced without the token.

    `final` marks the call that creates the post, so a timeout on it is an
    unknown outcome rather than a safe retry (see app.publish_errors).
    """
    settings = get_settings()
    url = f"{settings.meta_graph_base}/{settings.meta_graph_version}{path}"
    try:
        response = httpx.request(method, url,
                                 headers={"Authorization": f"Bearer {token}"},
                                 timeout=60.0, **kwargs)
    except httpx.HTTPError as exc:  # network-level failure
        log.error(f"Could not reach Meta for {what}: {type(exc).__name__}")
        raise MetaError(f"Could not reach Meta for {what}: {type(exc).__name__}",
                        network_failure(exc, final=final)) from exc

    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code >= 400 or "error" in body:
        error = body.get("error") or {}
        detail = error.get("message") or preview(response.text or "<empty body>", 300)
        log.error(f"Meta rejected {what} (HTTP {response.status_code}, "
                  f"code {error.get('code')}): {detail}")
        message = f"Meta returned {response.status_code} for {what}: {detail}"
        if response.status_code in (401, 403) or error.get("code") in (10, 190, 200):
            message += f" — {_PERMISSION_HINT}"
        if error.get("is_transient") or error.get("code") in _TRANSIENT_CODES:
            kind = FailureKind.TRANSIENT
        else:
            kind = status_failure(response.status_code, final=final)
        raise MetaError(message, kind)

    return body
