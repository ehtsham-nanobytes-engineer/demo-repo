"""Terminal logging setup.

One place decides what the terminal looks like. Called once on app startup and
also safe to call from a script or test. Lines are meant to be read by a human
watching a single request go by, so they stay short and say what happened, not
which internal function produced them.

Never log secrets: API keys, access tokens or Authorization headers.
"""

import logging
import sys

from app.config import get_settings

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-12s | %(message)s"
TIME_FORMAT = "%H:%M:%S"

# Third-party loggers that are noisy at DEBUG and say little we need.
_QUIET_LOGGERS = (
    "httpx",
    "httpcore",
    "urllib3",
    "asyncio",
    "openrouter",
)

_configured = False


def configure_logging() -> None:
    """Install the terminal handler. Idempotent — repeat calls do nothing."""
    global _configured
    if _configured:
        return

    level = getattr(logging, get_settings().log_level.upper(), logging.INFO)

    # Windows consoles default to cp1252, which cannot encode the em dashes,
    # non-breaking hyphens and Unicode glyphs that run through these logs — the
    # handler raises instead of printing the line. UTF-8 with a replacement
    # character keeps logging lossy at worst rather than broken.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=TIME_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()  # uvicorn installs its own; ours is the single source
    root.addHandler(handler)
    root.setLevel(level)

    # Match by prefix: some packages register versioned logger names ("httpx2").
    quiet_level = max(level, logging.WARNING)
    known = set(_QUIET_LOGGERS) | {
        name
        for name in logging.root.manager.loggerDict
        if name.startswith(_QUIET_LOGGERS)
    }
    for name in known:
        logging.getLogger(name).setLevel(quiet_level)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Logger for one part of the flow, e.g. get_logger("agent")."""
    return logging.getLogger(name)


def preview(text: str, limit: int = 90) -> str:
    """Collapse text to one readable line for a log message."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
