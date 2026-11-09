"""The tool-calling agent: niche in, finished post out, one per platform.

Two platforms, one agent each, differing only in their prompts:

- `linkedin` — the full post (system_role + research_process + output_roles);
- `meta` — a one-to-two line caption for Facebook and Instagram
  (meta_system_role + research_process + meta_output_rules).

The LLM chooses its own search queries, reads the returned sources, then writes
and formats the text. When the LinkedIn post has already been written in the
same request, the Meta caption is distilled from it instead of researched
again, so both platforms tell the same story and the second run is cheap.
Generation is stateless — no human gate inside the request (that lives on the
frontend).
"""

import time
from datetime import datetime
from functools import lru_cache

from langchain.agents import create_agent
from langchain_openrouter import ChatOpenRouter

from app.config import get_settings
from app.logging_setup import get_logger, preview
from app.prompts.composer import compose
from app.tools import web_search

log = get_logger("agent")

SYSTEM_PROMPTS = {
    "linkedin": compose("system_role", "research_process", "output_roles"),
    "meta": compose("meta_system_role", "research_process", "meta_output_rules"),
}


def _chat_model(temperature: float) -> ChatOpenRouter:
    """One OpenRouter chat model, configured from config.py and nothing else.

    The key is passed in explicitly rather than left to the client's own
    environment lookup, so `.env` and the real environment behave the same way.
    """
    settings = get_settings()
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured.")

    log.info(f"Loading model {settings.llm_model} (temperature {temperature})")

    return ChatOpenRouter(
        model=settings.llm_model,
        temperature=temperature,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_api_base,
        max_retries=settings.openrouter_max_retries,
        app_title=settings.openrouter_app_title,
        app_url=settings.openrouter_app_url,
    )


@lru_cache
def _build_agent(platform: str):
    """Build one agent per platform, once, and reuse it across requests."""
    settings = get_settings()
    return create_agent(
        _chat_model(settings.llm_temperature), [web_search],
        system_prompt=SYSTEM_PROMPTS[platform],
    )


def today() -> str:
    """Today's date, written out.

    The model has no clock and a training cutoff somewhere in the past, so left
    to itself it reaches for the last year it remembers — which is how a post
    about "the latest" ends up citing old material. Read per request rather
    than baked into the cached prompt, so a long-running server never drifts.
    """
    return datetime.now().astimezone().strftime("%A, %d %B %Y")


def generate_post(niche: str, platform: str = "linkedin",
                  source_post: str | None = None) -> str:
    """Run one agent invocation and return the finished text for `platform`.

    `source_post` is only used for `meta`: the LinkedIn post from the same
    request, which the caption is distilled from instead of re-researching.
    """
    log.info("Generating the %s text about: %s (today is %s)%s", platform, niche,
             today(), " from the LinkedIn post" if source_post else "")
    started = time.perf_counter()

    if platform == "meta" and source_post:
        request = (f"Today's date is {today()}. Write a Facebook and Instagram "
                   f"image caption about this niche: {niche}\n\n"
                   f"Base it only on this LinkedIn post:\n\n{source_post}")
    elif platform == "meta":
        request = (f"Today's date is {today()}. Write a Facebook and Instagram "
                   f"image caption about this niche: {niche}")
    else:
        request = (f"Today's date is {today()}. "
                   f"Write a LinkedIn post about this niche: {niche}")

    # Streaming the run (instead of a plain invoke) is what makes the terminal
    # useful: each search and each draft is logged as it happens rather than
    # after the whole thing finishes. The result is identical either way.
    final_text = ""
    for update in _build_agent(platform).stream(
        {"messages": [("human", request)]},
        stream_mode="updates",
    ):
        for messages in (step.get("messages", []) for step in update.values() if step):
            for message in messages:
                text = _log_step(message)
                if text:
                    final_text = text

    # Returned as the model wrote it, markdown and all. The /generate route runs
    # it through app.formatting, which turns that markup into LinkedIn's Unicode
    # equivalents instead of throwing it away.
    post = final_text.strip()
    log.info("Done in %.1fs — %d characters, %d line(s)",
             time.perf_counter() - started, len(post), len(post.splitlines()))
    log.debug("Final post:\n%s", post)
    return post


def _log_step(message) -> str:
    """Log one message from the agent run; return its text if it is the answer."""
    calls = getattr(message, "tool_calls", None)
    if calls:
        for call in calls:
            log.info("Model wants to call %s(%s)",
                     call.get("name", "?"),
                     ", ".join(f"{k}={v!r}" for k, v in (call.get("args") or {}).items()))
        return ""

    if getattr(message, "type", "") == "tool":
        log.debug("Tool %s returned %d characters",
                  getattr(message, "name", "?"), len(str(message.content)))
        return ""

    if getattr(message, "type", "") == "ai":
        text = str(message.text)
        log.info("Model wrote a draft: %s", preview(text))
        return text

    return ""
