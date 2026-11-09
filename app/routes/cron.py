"""GET /cron/dispatch — the daily catch-up, called by Vercel Cron only.

Vercel Cron sends `Authorization: Bearer $CRON_SECRET`; anything else is a 401,
and so is every call while CRON_SECRET is unset. Two jobs, each run only when
its backing service is configured:

- `scheduler.dispatch` (needs DATABASE_URL): enqueue posts that are now within
  a day, re-send lost messages, flag stuck publishes for review and delete
  scheduled images a week after their posts finish;
- `storage.sweep_drafts` (needs CLOUDINARY_URL): delete draft images older
  than DRAFT_TTL_HOURS — the ones nobody published, replaced or discarded.
"""

import secrets
from datetime import timedelta

from fastapi import APIRouter, Header, HTTPException

from app import db, scheduler, storage
from app.config import get_settings
from app.logging_setup import get_logger

router = APIRouter(tags=["cron"])

log = get_logger("api")


@router.get("/cron/dispatch", include_in_schema=False)
def dispatch(authorization: str | None = Header(default=None)) -> dict[str, int]:
    settings = get_settings()
    secret = settings.cron_secret
    expected = f"Bearer {secret}"
    if not secret or not secrets.compare_digest((authorization or "").encode(),
                                                expected.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    if not db.configured() and not storage.configured():
        raise HTTPException(status_code=503,
                            detail="Neither DATABASE_URL nor CLOUDINARY_URL is set.")

    summary = scheduler.dispatch() if db.configured() else {}
    if storage.configured():
        try:
            summary["drafts_deleted"] = storage.sweep_drafts(
                timedelta(hours=settings.draft_ttl_hours))
        except storage.StorageError as exc:
            # Tomorrow's run picks up the same drafts; nothing is lost by waiting.
            log.error(f"{exc} — retrying on the next daily run")
            summary["drafts_deleted"] = 0
    return summary
