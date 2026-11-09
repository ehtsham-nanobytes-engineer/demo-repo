"""Text-to-image generation, routed through OpenRouter.

Two functions: `build_prompt(title, post)` assembles what the image model is
asked for, and `render(prompt) -> image bytes` sends it. The art direction in
`prompts/image_brief.md` goes to the image model verbatim, together with the
finished post — there is no intermediate LLM turning the post into a one-line
brief. One post, one model call.

OpenRouter is itself the router across image vendors, so choosing a vendor is
an `IMAGE_MODEL` env change and never a code change — the same arrangement the
LLM already uses, on the same API key.

Requesting a size is left to `IMAGE_ASPECT_RATIO`: vendors serve their own
fixed dimensions and reject pixel sizes they do not have. The exact frame is
settled afterwards by `app.branding`, which cover-crops to `IMAGE_WIDTH` x
`IMAGE_HEIGHT` before stamping the logo.
"""

import base64
import binascii
from functools import lru_cache

import httpx
from openrouter import OpenRouter
from openrouter.errors import OpenRouterError
from openrouter.utils import BackoffStrategy, RetryConfig

from app.config import get_settings
from app.logging_setup import get_logger, preview
from app.prompts.composer import compose

log = get_logger("image")

# Read once at import: a missing fragment is a startup failure, not a 502 on
# somebody's request.
IMAGE_BRIEF = compose("image_brief")


class ImageGenerationError(RuntimeError):
    """Raised when the image provider fails or is not configured."""


def build_prompt(title: str, post: str) -> str:
    """The art direction plus the post it is for, as one prompt string.

    This is the whole of what used to be a separate LLM call. The image model
    reads the post itself and decides the picture; nothing in between gets to
    paraphrase the post into a sentence and lose it.

    Image providers cap the prompt they accept, and the cap is small — 8000
    characters at Grok, against LinkedIn's own 3000-character post. The art
    direction is fixed, so it is the post that gives way when the two together
    do not fit: a shortened post still describes the subject, whereas a
    truncated brief silently drops whichever rules sit at the end of the file.

    The return value is exactly what `render` sends, and it is what `/image`
    hands back as `image_prompt` — send it again and the same prompt is
    re-rendered with no assembly at all.
    """
    heading = f"Title: {title.strip()}\n\n" if title.strip() else ""
    frame = f"{IMAGE_BRIEF}\n\n---\n\n{heading}Post:\n"
    budget = get_settings().image_prompt_max_chars - len(frame)
    return frame + _fit(post.strip(), budget)


def _fit(text: str, budget: int) -> str:
    """`text` shortened to `budget` characters, cut at a word boundary."""
    limit = get_settings().image_prompt_max_chars

    if budget <= 0:
        # The brief alone has eaten the provider's whole allowance, so no
        # amount of trimming the post will help. Name the knob that is wrong.
        raise ImageGenerationError(
            f"The image brief is {len(IMAGE_BRIEF)} characters, which leaves no "
            f"room for the post within the {limit}-character prompt limit. "
            f"Shorten app/prompts/image_brief.md."
        )

    if len(text) <= budget:
        return text

    cut = text[:budget].rsplit(" ", 1)[0].rstrip() or text[:budget]
    log.warning(f"Post trimmed from {len(text)} to {len(cut)} characters to keep "
                f"the image prompt within {limit}")
    return cut


@lru_cache
def _client() -> OpenRouter:
    """Build the OpenRouter client once and reuse it across requests."""
    settings = get_settings()
    if not settings.openrouter_api_key:
        raise ImageGenerationError("OPENROUTER_API_KEY is not configured.")

    return OpenRouter(
        api_key=settings.openrouter_api_key,
        server_url=settings.openrouter_api_base,
        http_referer=settings.openrouter_app_url,
        x_open_router_title=settings.openrouter_app_title,
        timeout_ms=round(settings.image_timeout_seconds * 1000),
        # The client retries transient 5xx and connection failures on its own.
        # Its default gives up only after an hour, which is no use to a request
        # someone is waiting on, so the budget comes from config instead.
        retry_config=RetryConfig(
            "backoff",
            BackoffStrategy(
                round(settings.openrouter_retry_initial_seconds * 1000),
                round(settings.openrouter_retry_max_interval_seconds * 1000),
                settings.openrouter_retry_exponent,
                round(settings.openrouter_retry_budget_seconds * 1000),
            ),
            retry_connection_errors=True,
        ),
    )


def render(prompt: str) -> bytes:
    """Generate one image from `prompt` and return the raw encoded bytes."""
    settings = get_settings()

    # `build_prompt` already fits what it assembles, so this only catches a
    # hand-written brief sent back as `image_prompt`. Saying so here beats a
    # provider 400 that reads as a server fault.
    if len(prompt) > settings.image_prompt_max_chars:
        raise ImageGenerationError(
            f"The image prompt is {len(prompt)} characters; the provider "
            f"accepts at most {settings.image_prompt_max_chars}."
        )

    log.info(f"Rendering an image with {settings.image_model} "
             f"at {settings.image_aspect_ratio} ({settings.image_output_format})")
    log.debug(f"Image prompt: {prompt}")

    try:
        response = _client().images.generate(
            model=settings.image_model,
            prompt=prompt,
            n=1,
            aspect_ratio=settings.image_aspect_ratio,
            output_format=settings.image_output_format,
        )
    except OpenRouterError as exc:
        # `body` is the provider's own error text; the key is never in it.
        detail = preview(exc.body, 300)
        log.error(f"OpenRouter returned HTTP {exc.status_code}: {detail}")
        raise ImageGenerationError(
            f"Image generation failed ({exc.status_code}): {detail}"
        ) from exc
    except httpx.HTTPError as exc:
        log.error(f"Could not reach OpenRouter: {exc}")
        raise ImageGenerationError(f"Could not reach OpenRouter: {exc}") from exc

    images = getattr(response, "data", None)
    if not images:
        raise ImageGenerationError("OpenRouter returned no image.")

    try:
        data = base64.b64decode(images[0].b64_json, validate=True)
    except (binascii.Error, ValueError) as exc:
        log.error(f"The image OpenRouter returned could not be decoded: {exc}")
        raise ImageGenerationError(
            f"OpenRouter returned an image that could not be decoded: {exc}"
        ) from exc

    if not data:
        raise ImageGenerationError("OpenRouter returned an empty image.")

    log.info(f"Provider returned {len(data)} bytes "
             f"({images[0].media_type or 'unknown media type'})")
    return data
