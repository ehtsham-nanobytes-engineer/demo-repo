"""POST /publish — publish a post the user has already approved, right now.

Called only after the human review gate on the frontend, once per ticked
brand-and-platform checkbox. Nothing here decides to publish on its own: the
approved text arrives in the request body, and the approved image by its draft
id. Neither is regenerated, re-rendered or re-branded — this brand's stored
draft is exactly what the reviewer saw, and it is what goes out. The work
itself is `publishing.publish_target`, the same function scheduled posts go
through.
"""

from fastapi import APIRouter, HTTPException

from app.brands import get_brand
from app.logging_setup import get_logger
from app.publish_errors import PublishError
from app.publishing import publish_target
from app.schema.publish import PublishRequest, PublishResponse

router = APIRouter(tags=["publish"])

log = get_logger("api")


@router.post("/publish", response_model=PublishResponse)
def publish(request: PublishRequest) -> PublishResponse:
    """Publish an approved post. Called only after human review on the frontend."""
    brand = get_brand(request.brand)
    image = request.image_public_id
    log.info(f"POST /publish — {brand.name} on {request.platform}: approved post of "
             f"{len(request.post)} characters"
             f"{f' with draft {request.draft_id}' if image else ' (text only)'}")

    try:
        url = publish_target(request.brand, request.platform, request.post,
                             image_public_id=image, alt=request.image_alt)
    except PublishError as exc:
        log.error(f"POST /publish ({brand.name} on {request.platform}) failed "
                  f"({exc.kind}): {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return PublishResponse(url=url)
