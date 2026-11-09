"""POST /generate — research a niche and write the post for each platform asked for.

One agent invocation per platform: the LLM picks its own search queries, reads
the sources, then writes. When both are asked for, the LinkedIn post is written
first and the Meta caption is distilled from it, so the two tell the same story.
Nothing is published here — the response is the draft the human reviews on the
frontend.
"""

import logging

from fastapi import APIRouter, HTTPException

from app.agent import generate_post
from app.config import get_settings
from app.formatting import to_linkedin_unicode
from app.logging_setup import get_logger
from app.schema.generate import GenerateRequest, GenerateResponse

router = APIRouter(tags=["generate"])

log = get_logger("api")
settings = get_settings()


@router.post("/generate", response_model=GenerateResponse)
def generate(request: GenerateRequest) -> GenerateResponse:
    """Research the niche and return the ready-to-post text for each platform."""
    log.info("POST /generate — niche=%r linkedin=%s meta=%s",
             request.niche, request.linkedin, request.meta)

    linkedin = _write(request.niche, "linkedin", settings.linkedin_max_chars) \
        if request.linkedin else None
    meta = _write(request.niche, "meta", settings.meta_max_chars, source_post=linkedin) \
        if request.meta else None

    log.info("POST /generate — returning the text for review (nothing published yet)")

    return GenerateResponse(linkedin=linkedin, meta=meta)


def _write(niche: str, platform: str, max_chars: int,
           source_post: str | None = None) -> str:
    """One agent run for `platform`, formatted; any failure becomes a 502."""
    try:
        text = generate_post(niche, platform, source_post=source_post)
    except Exception as exc:  # agent or provider failure
        # The traceback is only useful when debugging; LOG_LEVEL=DEBUG shows it.
        log.error("POST /generate (%s) failed: %s", platform, exc,
                  exc_info=log.isEnabledFor(logging.DEBUG))
        raise HTTPException(status_code=502,
                            detail=f"{platform} generation failed: {exc}") from exc

    if not text:
        log.error("POST /generate — the agent returned an empty %s post", platform)
        raise HTTPException(status_code=502,
                            detail=f"The agent returned an empty {platform} post.")

    # The model writes markdown; the feeds render plain text. Swap the markup for
    # the Unicode glyphs that survive the feed before anyone sees the draft, so
    # what the reviewer approves is exactly what gets published. The Meta caption
    # is asked for in plain text; this is the same clean-up either way.
    text = to_linkedin_unicode(text)

    if len(text) > max_chars:
        log.warning("POST /generate — %s post is %d characters, over the %d limit",
                    platform, len(text), max_chars)
    return text
