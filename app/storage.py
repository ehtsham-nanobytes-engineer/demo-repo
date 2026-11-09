"""Cloudinary: where every generated image lives until it is published.

Three kinds of image, all `authenticated` assets (nothing is reachable without
a URL signed with the account secret):

- **drafts**, `social/drafts/<draft_id>/<brand>`: written by `/image`, one per
  selected brand, already branded. The frontend shows them by signed URL and
  `/publish` posts straight from them. A draft is deleted when it is replaced
  (regenerate, New post) or by the daily sweep once it is `DRAFT_TTL_HOURS` old;
- **scheduled copies**, `social/<brand>/<uuid>`: `/schedule` copies the brand's
  draft here, so the sweep can never take an image a scheduled row needs. The
  cron deletes these a week after their rows finish;
- Instagram fetches from either, because it takes a URL rather than bytes.

LinkedIn and Facebook get the **original bytes** back through the download
API — never a transformed delivery URL and never `f_auto`/`q_auto`, because a
re-encode would change the logo the reviewer approved. Instagram alone gets a
derived JPEG, cropped only if the frame is outside the 4:5–1.91:1 range it
accepts. The shared 1200x627 frame is a hair wider than 1.91:1, so a pixel or
two comes off each side — far less than the logo's inset.

Config is one `CLOUDINARY_URL` (cloudinary://<key>:<secret>@<cloud>), passed to
each call rather than set globally. Signed URLs carry the API key, so they are
never logged.
"""

import io
import re
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse

import cloudinary.api
import cloudinary.exceptions
import cloudinary.uploader
import cloudinary.utils
import httpx

from app.config import get_settings
from app.logging_setup import get_logger

log = get_logger("storage")

_TYPE = "authenticated"
_TIMEOUT_SECONDS = 60
_DRAFTS = "social/drafts"
_DRAFT_ID = re.compile(r"^[0-9a-f]{32}$")
_DELETE_BATCH = 100  # the Admin API's cap per delete_resources call

# Cropped to Instagram's accepted aspect ratios, then JPEG at a fixed quality
# (explicitly not q_auto). Conditions make the crops no-ops for frames already
# inside the range.
_INSTAGRAM_TRANSFORMATION = [
    {"if": "ar_gt_1.91"}, {"aspect_ratio": "1.91", "crop": "crop", "gravity": "center"},
    {"if": "end"},
    {"if": "ar_lt_0.8"}, {"aspect_ratio": "0.8", "crop": "crop", "gravity": "center"},
    {"if": "end"},
    {"quality": 95},
]


class StorageError(RuntimeError):
    """Raised when Cloudinary is unconfigured, unreachable or refuses a call."""


class ImageMissing(StorageError):
    """The image is not in Cloudinary: an expired or discarded draft, usually."""


def configured() -> bool:
    return bool(get_settings().cloudinary_url)


# ---- Drafts ----------------------------------------------------------------

def new_draft_id() -> str:
    return uuid.uuid4().hex


def valid_draft_id(draft_id: str) -> bool:
    """Draft ids become part of a public_id, so only our own shape is accepted."""
    return bool(_DRAFT_ID.match(draft_id or ""))


def draft_public_id(draft_id: str, brand: str) -> str:
    if not valid_draft_id(draft_id):
        raise ValueError(f"Malformed draft id {draft_id!r}.")
    return f"{_DRAFTS}/{draft_id}/{brand}"


def delete_draft(draft_id: str) -> None:
    """Remove every brand's image in a draft. A draft already gone is fine."""
    prefix = f"{_DRAFTS}/{draft_id}/"
    if not valid_draft_id(draft_id):
        raise ValueError(f"Malformed draft id {draft_id!r}.")
    try:
        cloudinary.api.delete_resources_by_prefix(
            prefix, type=_TYPE, resource_type="image", timeout=_TIMEOUT_SECONDS,
            **_credentials())
    except (cloudinary.exceptions.Error, OSError) as exc:
        raise StorageError(f"Could not delete draft {draft_id}: {exc}") from exc
    log.info(f"Deleted draft {draft_id}")


def sweep_drafts(older_than: timedelta) -> int:
    """Delete drafts created more than `older_than` ago; return how many images.

    The safety net behind the explicit deletes: a tab closed mid-review, a
    publish-now draft nobody replaced, a delete that failed. Scheduled posts
    are unaffected — they hold their own copy outside the drafts folder.
    """
    cutoff = datetime.now().astimezone() - older_than
    stale, cursor = [], None
    try:
        while True:
            page = cloudinary.api.resources(
                type=_TYPE, resource_type="image", prefix=f"{_DRAFTS}/",
                max_results=500, timeout=_TIMEOUT_SECONDS,
                **({"next_cursor": cursor} if cursor else {}), **_credentials())
            stale += [r["public_id"] for r in page.get("resources", [])
                      if _created(r) < cutoff]
            cursor = page.get("next_cursor")
            if not cursor:
                break
        for start in range(0, len(stale), _DELETE_BATCH):
            cloudinary.api.delete_resources(
                stale[start:start + _DELETE_BATCH], type=_TYPE, resource_type="image",
                timeout=_TIMEOUT_SECONDS, **_credentials())
    except (cloudinary.exceptions.Error, OSError) as exc:
        raise StorageError(f"Draft sweep failed: {exc}") from exc
    log.info(f"Swept {len(stale)} draft image(s) older than {older_than}")
    return len(stale)


# ---- Any stored image -------------------------------------------------------

def upload(image: bytes, brand: str) -> str:
    """Store `image` under `social/<brand>/`; return its public_id."""
    return upload_as(image, f"social/{brand}/{uuid.uuid4().hex}")


def upload_as(image: bytes, public_id: str) -> str:
    """Store `image` byte for byte as `public_id`; return the public_id."""
    try:
        result = cloudinary.uploader.upload(
            io.BytesIO(image), public_id=public_id, type=_TYPE,
            resource_type="image", overwrite=False, timeout=_TIMEOUT_SECONDS,
            **_credentials(),
        )
    except cloudinary.exceptions.Error as exc:
        raise StorageError(f"Cloudinary rejected the upload: {exc}") from exc
    except OSError as exc:  # urllib3 network failures surface as OSError
        raise StorageError(f"Could not reach Cloudinary: {type(exc).__name__}") from exc

    log.info(f"Stored a {len(image)} byte image as {result['public_id']}")
    return result["public_id"]


def copy(public_id: str, brand: str) -> str:
    """A new copy of `public_id` under `social/<brand>/`; return its public_id.

    Downloaded and re-uploaded rather than fetched by Cloudinary from a URL:
    the original bytes go back in untouched, so the copy is byte-identical to
    the approved image by construction. Server to server, so no request body
    limit applies.
    """
    return upload(original_bytes(public_id), brand)


def original_bytes(public_id: str) -> bytes:
    """The stored image exactly as uploaded, for LinkedIn and Facebook."""
    url = cloudinary.utils.private_download_url(
        public_id, "png", type=_TYPE, resource_type="image", **_credentials())
    try:
        response = httpx.get(url, timeout=_TIMEOUT_SECONDS, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise StorageError(f"Could not download {public_id}: {type(exc).__name__}") from exc
    if response.status_code == 404:
        raise ImageMissing(_missing(public_id))
    if response.status_code != 200:
        raise StorageError(f"Cloudinary returned {response.status_code} for {public_id}")
    return response.content


def require(public_id: str) -> None:
    """Raise `ImageMissing` unless `public_id` exists. For Instagram, whose
    fetch of a missing image would otherwise fail on Meta's side, vaguely."""
    try:
        cloudinary.api.resource(public_id, type=_TYPE, resource_type="image",
                                timeout=_TIMEOUT_SECONDS, **_credentials())
    except cloudinary.exceptions.NotFound as exc:
        raise ImageMissing(_missing(public_id)) from exc
    except (cloudinary.exceptions.Error, OSError) as exc:
        raise StorageError(f"Could not look up {public_id}: {exc}") from exc


def view_url(public_id: str) -> str:
    """A signed URL for showing the stored image as it is: no transformation."""
    url, _ = cloudinary.utils.cloudinary_url(
        public_id, type=_TYPE, sign_url=True, secure=True, resource_type="image",
        format="png", **_credentials())
    return url


def download_url(public_id: str, filename: str) -> str:
    """The same image served as an attachment named `filename`."""
    url, _ = cloudinary.utils.cloudinary_url(
        public_id, type=_TYPE, sign_url=True, secure=True, resource_type="image",
        format="png", flags=f"attachment:{filename}", **_credentials())
    return url


def instagram_url(public_id: str) -> str:
    """A signed URL Meta can fetch: the image as an Instagram-shaped JPEG."""
    url, _ = cloudinary.utils.cloudinary_url(
        public_id, type=_TYPE, sign_url=True, secure=True, resource_type="image",
        format="jpg", transformation=_INSTAGRAM_TRANSFORMATION, **_credentials())
    return url


def delete(public_id: str) -> None:
    """Remove the image. An image that is already gone counts as deleted."""
    try:
        result = cloudinary.uploader.destroy(
            public_id, type=_TYPE, resource_type="image", invalidate=True,
            timeout=_TIMEOUT_SECONDS, **_credentials())
    except (cloudinary.exceptions.Error, OSError) as exc:
        raise StorageError(f"Could not delete {public_id}: {exc}") from exc
    if result.get("result") not in ("ok", "not found"):
        raise StorageError(f"Cloudinary did not delete {public_id}: {result}")
    log.info(f"Deleted {public_id}")


def _missing(public_id: str) -> str:
    if public_id.startswith(f"{_DRAFTS}/"):
        return ("The image is no longer stored: drafts expire after "
                f"{get_settings().draft_ttl_hours} hours or when replaced. "
                "Regenerate the image, then approve again.")
    return f"The image {public_id} is no longer stored."


def _created(resource: dict) -> datetime:
    return datetime.fromisoformat(resource["created_at"].replace("Z", "+00:00"))


def _credentials() -> dict:
    url = get_settings().cloudinary_url
    if not url:
        raise StorageError("CLOUDINARY_URL is not set.")
    parsed = urlparse(url)
    # The netloc, not .hostname, which would lowercase the cloud name.
    cloud_name = parsed.netloc.rpartition("@")[2]
    if parsed.scheme != "cloudinary" or not (
            cloud_name and parsed.username and parsed.password):
        raise StorageError("CLOUDINARY_URL must look like "
                           "cloudinary://<api_key>:<api_secret>@<cloud_name>.")
    return {"cloud_name": cloud_name, "api_key": parsed.username,
            "api_secret": parsed.password}
