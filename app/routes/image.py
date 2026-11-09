"""POST /image — generate the visual for text that already exists.
DELETE /image/{draft_id} — discard a draft nobody is going to publish.

Content first, image second. The route takes the finished LinkedIn post and/or
Meta caption (and a title) plus the brands the user selected, and returns one
branded copy per selected brand. It cannot run before generation: a request
with no text is a 422 from Pydantic before any provider is called.

The picture is rendered **once** and the same one goes to every brand and every
platform; only the logo differs. Each copy is stored in Cloudinary as a draft
(`social/drafts/<draft_id>/<brand>`) and returned as a signed URL, so the
response stays tiny however many brands there are, and `/publish` and
`/schedule` refer to the draft by id instead of sending the image back.

The endpoint is deliberately self-contained so the frontend can drive all three
buttons off the existing routes:

    regenerate content  -> POST /generate
    regenerate image    -> POST /image   (same body + previous_draft_id)
    regenerate the post -> POST /generate then POST /image

Drafts never pile up: a regenerate deletes the draft it replaces, "New post"
deletes the current one, and the daily cron sweeps whatever is left after
DRAFT_TTL_HOURS (a closed tab, say).

There is no brief-writing LLM call: the art direction, the title and the text
go to the image model as one prompt. Sending `image_prompt` back re-renders
that exact prompt.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Response

from app import storage
from app.branding import BrandingError, apply_logo
from app.brands import get_brand
from app.config import get_settings
from app.imagegen import ImageGenerationError, build_prompt, render
from app.logging_setup import get_logger, preview
from app.schema.image import BrandImage, ImageRequest, ImageResponse

router = APIRouter(tags=["image"])

log = get_logger("api")


@router.post("/image", response_model=ImageResponse)
def image(request: ImageRequest) -> ImageResponse:
    """Render one image, brand it per selected brand, store each as a draft."""
    # Checked before the render: a picture that cannot be stored cannot be
    # shown or published, so it is not worth paying for.
    if not storage.configured():
        raise HTTPException(status_code=503,
                            detail="Images need CLOUDINARY_URL: that is where they are stored.")

    post = request.combined_post
    log.info(f"POST /image — {len(post)} characters of text for "
             f"{', '.join(request.brands)}, title={preview(request.title, 60)!r}")

    # Assembled here, not written by a model: nothing can fail and nothing can
    # come back empty, because the text is already non-empty by schema.
    prompt = (request.image_prompt or "").strip() or build_prompt(request.title, post)

    try:
        picture = render(prompt)
    except ImageGenerationError as exc:
        log.error(f"POST /image failed: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    draft_id = storage.new_draft_id()
    # Uploads are network-bound and independent, so they run side by side.
    with ThreadPoolExecutor(max_workers=len(request.brands)) as pool:
        images = list(pool.map(lambda b: _brand_and_store(picture, b, draft_id),
                                request.brands))

    if not any(i.url for i in images):
        _discard(draft_id)  # whatever did upload is of no use on its own
        raise HTTPException(status_code=502,
                            detail="No brand's image could be made: " +
                                   "; ".join(i.error or "" for i in images))

    # Only now that the new draft exists: a failed render above leaves the
    # user's current images untouched.
    if request.previous_draft_id and request.previous_draft_id != draft_id:
        _discard(request.previous_draft_id)

    log.info(f"POST /image — draft {draft_id}: {sum(1 for i in images if i.url)} "
             f"branded image(s) stored for review (nothing published yet)")

    expires = datetime.now(UTC) + timedelta(hours=get_settings().draft_ttl_hours)
    return ImageResponse(draft_id=draft_id, images=images, image_prompt=prompt,
                         expires_at=expires)


@router.delete("/image/{draft_id}", status_code=204)
def discard(draft_id: str) -> Response:
    """Delete a draft: the frontend's "New post". Best effort — the sweep is
    the backstop, so a failure here is logged, never shown to the user."""
    if not storage.valid_draft_id(draft_id):
        raise HTTPException(status_code=422, detail="Malformed draft id.")
    if storage.configured():
        _discard(draft_id)
    return Response(status_code=204)


def _brand_and_store(picture: bytes, brand_id: str, draft_id: str) -> BrandImage:
    """One brand's copy: logo on, stored, signed URLs out. Errors stay in the
    brand's slot so one missing logo does not throw away everyone's image."""
    brand = get_brand(brand_id)
    try:
        branded = apply_logo(picture, brand)
        public_id = storage.upload_as(branded, storage.draft_public_id(draft_id, brand.id))
    except (BrandingError, storage.StorageError) as exc:
        log.error(f"POST /image — {brand.name} failed: {exc}")
        return BrandImage(brand=brand.id, name=brand.name, error=str(exc))
    return BrandImage(
        brand=brand.id, name=brand.name,
        url=storage.view_url(public_id),
        download_url=storage.download_url(public_id, f"{brand.id}-post"),
    )


def _discard(draft_id: str) -> None:
    try:
        storage.delete_draft(draft_id)
    except storage.StorageError as exc:
        log.warning(f"{exc} — the daily sweep will remove it")
