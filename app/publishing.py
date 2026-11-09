"""Publish one approved post for one brand on one platform.

The single place that turns (brand, platform, text, image) into a live post.
Both publish-now (`POST /publish`) and the scheduler call `publish_target`, so
a scheduled post goes out exactly the way an immediate one does.

The image is always a Cloudinary `public_id`, already branded for this brand:

- publish-now: the brand's draft from `/image`, the file the reviewer saw;
- scheduled: the copy `/schedule` made of that draft.

LinkedIn and Facebook get its original bytes; Instagram is given a signed URL
to fetch it from, since it takes no bytes.

Every failure is a `PublishError` with a `FailureKind` (app.publish_errors).
"""

from app import linkedin, meta, storage
from app.brands import get_brand
from app.logging_setup import get_logger
from app.publish_errors import FailureKind, PublishError

log = get_logger("publish")


def publish_target(brand_id: str, platform: str, post: str, *,
                   image_public_id: str | None = None,
                   alt: str | None = None) -> str:
    """Publish `post` as `brand_id` on `platform`; return the post's URL.

    Credentials are checked here, at publish time, not only when the post was
    approved: a token can be revoked between scheduling and publishing.
    """
    brand = get_brand(brand_id)
    if not brand:
        raise PublishError(f"Unknown brand {brand_id!r}.")
    reason = brand.missing(platform)
    if reason:
        raise PublishError(f"{brand.name} cannot publish to {platform}: {reason}")

    s = brand.settings
    if platform == "instagram":
        if not image_public_id:
            raise PublishError("Instagram posts need an image.")
        _guard(storage.require, image_public_id)
        return meta.publish_instagram(post, _instagram_url(image_public_id),
                                      ig_user_id=s.instagram_user_id, token=s.meta_page_token)

    image = _guard(storage.original_bytes, image_public_id) if image_public_id else None
    if platform == "linkedin":
        return linkedin.publish_post(post, image=image, alt_text=alt,
                                     author_urn=s.linkedin_author_urn,
                                     token=brand.linkedin_token)
    if platform == "facebook":
        return meta.publish_facebook(post, image, page_id=s.facebook_page_id,
                                     token=s.meta_page_token)
    raise PublishError(f"Unknown platform {platform!r}.")


def _guard(call, public_id: str):
    """Run a storage call before anything is posted, as a PublishError.

    A missing image is permanent: an expired draft or a deleted copy will not
    come back on a retry. Anything else (Cloudinary down) is transient.
    """
    try:
        return call(public_id)
    except storage.ImageMissing as exc:
        raise PublishError(str(exc)) from exc
    except storage.StorageError as exc:
        raise PublishError(str(exc), FailureKind.TRANSIENT) from exc


def _instagram_url(public_id: str) -> str:
    try:
        return storage.instagram_url(public_id)
    except storage.StorageError as exc:  # only a malformed CLOUDINARY_URL
        raise PublishError(str(exc)) from exc
