"""/schedule — approve a post for a later time, then list, move, cancel or resolve it.

Scheduling is the same approval as Publish now, with a time attached. The text
arrives exactly as the reviewer saw it and goes to Neon; the brand's branded
draft is copied within Cloudinary out of the drafts folder; at the scheduled
time the queue consumer publishes that stored copy. Nothing is generated or
rewritten later.

    POST   /schedule                one brand, one or more platforms
    GET    /schedule?status=        the Scheduled list
    PATCH  /schedule/{id}           new time and/or text, while still scheduled
    DELETE /schedule/{id}           cancel, while still scheduled
    POST   /schedule/{id}/resolve   a human's verdict on a needs_review post

All of them answer 503 while DATABASE_URL is unset.
"""

import uuid

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from vercel.queue import QueueError

from app import db, scheduler, storage
from app.brands import get_brand
from app.logging_setup import get_logger
from app.schema.schedule import (
    ResolveRequest, ScheduledItem, ScheduledPost, ScheduleRequest, ScheduleResponse,
    ScheduleUpdate, Status,
)

log = get_logger("api")


def _enabled() -> None:
    if not db.configured():
        raise HTTPException(status_code=503,
                            detail="Scheduling is off: DATABASE_URL is not set.")


router = APIRouter(tags=["schedule"], dependencies=[Depends(_enabled)])


@router.post("/schedule", response_model=ScheduleResponse)
def schedule(request: ScheduleRequest) -> ScheduleResponse:
    image = request.image_public_id
    if image and not storage.configured():
        raise HTTPException(status_code=503,
                            detail="Scheduling an image needs CLOUDINARY_URL.")
    log.info(f"POST /schedule — {get_brand(request.brand).name} on "
             f"{', '.join(t.platform for t in request.targets)} at "
             f"{request.scheduled_at.isoformat()}"
             f"{f' with draft {request.draft_id}' if image else ' (text only)'}")
    try:
        rows = scheduler.create(request.brand, request.scheduled_at,
                                [(t.platform, t.post) for t in request.targets],
                                image, request.image_alt)
    except storage.ImageMissing as exc:  # the draft expired or was replaced
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except storage.StorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (QueueError, psycopg.Error) as exc:
        log.error(f"POST /schedule failed: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=503, detail="Could not save the schedule; "
                            "nothing was scheduled. Try again.") from exc
    return ScheduleResponse(items=[ScheduledItem(**r) for r in rows])


@router.get("/schedule", response_model=list[ScheduledPost])
def list_schedule(status: Status | None = None) -> list[ScheduledPost]:
    return [ScheduledPost(**r) for r in scheduler.list_rows(status)]


@router.patch("/schedule/{publication_id}", response_model=ScheduledPost)
def update(publication_id: uuid.UUID, request: ScheduleUpdate) -> ScheduledPost:
    try:
        row = scheduler.update(publication_id, scheduled_at=request.scheduled_at,
                               post=request.post)
    except ValueError as exc:  # the edited text is over the platform's limit
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except QueueError as exc:
        raise HTTPException(status_code=503, detail="Could not re-queue the post; "
                            "nothing was changed. Try again.") from exc
    return ScheduledPost(**row)


@router.delete("/schedule/{publication_id}", response_model=ScheduledPost)
def cancel(publication_id: uuid.UUID) -> ScheduledPost:
    return ScheduledPost(**scheduler.cancel(publication_id))


@router.post("/schedule/{publication_id}/resolve", response_model=ScheduledPost)
def resolve(publication_id: uuid.UUID, request: ResolveRequest) -> ScheduledPost:
    return ScheduledPost(**scheduler.resolve(
        publication_id, published=request.published, external_url=request.external_url))
