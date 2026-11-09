"""LinkedIn publishing client.

Uses the Posts API (`POST /rest/posts`). Posts need the `w_member_social`
scope for a member author, or `w_organization_social` for a company page; each
brand's `<BRAND>__LINKEDIN_AUTHOR_URN` decides which. OAuth is handled
separately by the org — we only consume the access token.

An image post is three calls, not one, because LinkedIn does not take image
bytes inline:

1. `POST /rest/images?action=initializeUpload` reserves an image URN and hands
   back a one-shot upload URL;
2. the bytes go to that URL with a plain `PUT`;
3. the URN is referenced in the post body.

Read paths (feed, profiles, search) do not exist here on purpose: LinkedIn
exposes no such API, and scraping it violates their ToS.
"""

from typing import NamedTuple

import httpx

from app.config import get_settings
from app.logging_setup import get_logger, preview
from app.publish_errors import FailureKind, PublishError, network_failure, status_failure

log = get_logger("linkedin")

# LinkedIn's own cap on the alt text of an image.
_ALT_TEXT_MAX = 4096

# The only two author URNs the Posts API accepts. A near miss — "urn:li:personal"
# for "urn:li:person" — is easy to typo and comes back from LinkedIn as an
# opaque 400 at publish time, so it is caught here instead.
_AUTHOR_PREFIXES = ("urn:li:person:", "urn:li:organization:")

# A 401/403 from the Posts or Images API is nearly always one of three things,
# and LinkedIn's body is usually empty, so the guesswork is written down here
# rather than left to whoever reads the log next.
_PERMISSION_HINT = (
    "check that (1) the token carries w_member_social (or "
    "w_organization_social for a page), (2) the brand's "
    "<BRAND>__LINKEDIN_AUTHOR_URN is a page the token's member administers, or "
    "that member's own `sub` from /v2/userinfo, and (3) LINKEDIN_API_VERSION is "
    "a version LinkedIn still supports. Run scripts/check_linkedin.py to see which."
)


class LinkedInError(PublishError):
    """Raised when LinkedIn rejects a request or config is missing."""


def publish_post(post: str, image: bytes | None = None,
                 alt_text: str | None = None, *,
                 author_urn: str | None, token: str | None) -> str:
    """Publish `post` as `author_urn`, optionally with one image; return its URL.

    `image` is the finished PNG a human approved — the same bytes `/image`
    returned for this brand. Passing None publishes text only.
    """
    account = _require_config(author_urn, token)

    content = None
    if image:
        # Upload first: a failure here must not leave a text-only post behind
        # that the user did not approve.
        content = {"media": _upload_image(image, alt_text, account)}

    payload = {
        "author": account.author_urn,
        "commentary": post,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if content:
        payload["content"] = content

    log.info(f"Publishing {len(post)} characters"
             f"{' with an image' if image else ''} as "
             f"{account.author_urn}")

    # The only call that puts something on the feed; see app.publish_errors.
    response = _post("/rest/posts", json=payload, account=account,
                     what="the post", final=True)

    # The post id comes back in a header, not the body.
    post_id = response.headers.get("x-restli-id") or response.headers.get(
        "x-linkedin-id", ""
    )
    if not post_id:
        log.warning("LinkedIn accepted the post but returned no id — no URL to show")
        return ""

    url = f"https://www.linkedin.com/feed/update/{post_id}"
    log.info(f"Published: {url}")
    return url


def _upload_image(image: bytes, alt_text: str | None, account: "_Account") -> dict:
    """Run the two-step image upload and return the post's `media` object."""
    log.info(f"Registering an image upload ({len(image)} bytes)")

    registration = _post(
        "/rest/images?action=initializeUpload",
        json={"initializeUploadRequest": {
            "owner": account.author_urn,
        }},
        account=account,
        what="the image upload registration",
    ).json()

    value = registration.get("value") or {}
    upload_url = value.get("uploadUrl")
    image_urn = value.get("image")
    if not upload_url or not image_urn:
        log.error(f"Unexpected registration response: {preview(str(registration), 300)}")
        raise LinkedInError(
            "LinkedIn did not return an upload URL for the image.",
            FailureKind.TRANSIENT,
        )

    log.info(f"Uploading the image as {image_urn}")
    try:
        # The upload URL is pre-signed but still wants the bearer token. No
        # Content-Type: LinkedIn infers it and rejects some explicit values.
        upload = httpx.put(
            upload_url,
            content=image,
            headers={"Authorization": f"Bearer {account.token}"},
            timeout=120.0,
        )
    except httpx.HTTPError as exc:
        log.error(f"Could not reach the image upload URL: {exc}")
        raise LinkedInError(f"Could not upload the image: {exc}",
                            network_failure(exc, final=False)) from exc

    if upload.status_code >= 400:
        log.error(f"Image upload failed (HTTP {upload.status_code}): {upload.text}")
        raise LinkedInError(
            f"LinkedIn rejected the image upload ({upload.status_code}): "
            f"{upload.text}",
            status_failure(upload.status_code, final=False),
        )

    media = {"id": image_urn}
    if alt_text:
        media["altText"] = alt_text[:_ALT_TEXT_MAX]
    log.info("Image uploaded")
    return media


class _Account(NamedTuple):
    """Who a post goes out as, and the token allowed to post for them."""

    author_urn: str
    token: str


def _require_config(author_urn: str | None, token: str | None) -> _Account:
    """The account, or a clear error naming what is missing."""
    if not token or not author_urn:
        log.error("Publish blocked — LinkedIn credentials are missing")
        raise LinkedInError(
            "LinkedIn is not configured for this brand: set LINKEDIN_ACCESS_TOKEN "
            "and <BRAND>__LINKEDIN_AUTHOR_URN."
        )

    if not author_urn.startswith(_AUTHOR_PREFIXES):
        log.error(f"Publish blocked — the author URN is {author_urn!r}")
        raise LinkedInError(
            f"The LinkedIn author URN must start with 'urn:li:person:' (your own "
            f"profile) or 'urn:li:organization:' (a company page). Got "
            f"{author_urn!r}."
        )

    return _Account(author_urn, token)


def _post(path: str, *, json: dict, account: _Account, what: str,
          final: bool = False) -> httpx.Response:
    """One authenticated POST to the versioned API, errors surfaced verbatim."""
    settings = get_settings()
    try:
        response = httpx.post(
            f"{settings.linkedin_api_base}{path}",
            headers={
                "Authorization": f"Bearer {account.token}",
                "LinkedIn-Version": settings.linkedin_api_version,
                "X-Restli-Protocol-Version": "2.0.0",
                "Content-Type": "application/json",
            },
            json=json,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:  # network-level failure
        log.error(f"Could not reach LinkedIn: {exc}")
        raise LinkedInError(f"Could not reach LinkedIn: {exc}",
                            network_failure(exc, final=final)) from exc

    if response.status_code >= 400:
        # Never echo the token; the response body is safe to surface. LinkedIn
        # often sends an empty body and puts the real reason in a header, so
        # those are logged too.
        log.error(f"LinkedIn rejected {what} (HTTP {response.status_code}): "
                  f"{response.text or '<empty body>'}")
        for header in ("x-li-error-response", "x-restli-error-response",
                       "x-li-uuid", "www-authenticate"):
            if header in response.headers:
                log.error(f"  {header}: {preview(response.headers[header], 200)}")

        detail = (f"LinkedIn returned {response.status_code} for {what}: "
                  f"{response.text or '<empty body>'}")
        if response.status_code in (401, 403):
            detail += f" — {_PERMISSION_HINT}"
        raise LinkedInError(detail, status_failure(response.status_code, final=final))

    return response
