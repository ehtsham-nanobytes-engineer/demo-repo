"""Validation rules shared by publish-now and scheduled publishing.

A post is checked the same way whether it goes out now or later, so the rules
live here once and both `/publish` and `/schedule` apply them.
"""

from typing import Literal

from app import storage
from app.brands import get_brand
from app.config import get_settings

Platform = Literal["linkedin", "facebook", "instagram"]

settings = get_settings()

# The longest post each platform accepts. Facebook's cap is far above anything
# this service writes; it is here so every platform is checked the same way.
MAX_CHARS = {
    "linkedin": settings.linkedin_max_chars,
    "facebook": 63206,
    "instagram": settings.meta_max_chars,
}
LONGEST_POST = max(MAX_CHARS.values())

# Alt text for the image. LinkedIn caps it and the client truncates to that cap.
ALT_MAX = 4096


def known_brand(value: str) -> str:
    if not get_brand(value):
        raise ValueError(f"Unknown brand {value!r}.")
    return value


def valid_draft(value: str | None) -> str | None:
    """The `draft_id` /image returned, or None for a text-only post."""
    if value is None or not value.strip():
        return None
    value = value.strip()
    if not storage.valid_draft_id(value):
        raise ValueError("draft_id must be the id /image returned.")
    return value


def check_length(platform: str, post: str) -> None:
    limit = MAX_CHARS[platform]
    if len(post) > limit:
        raise ValueError(f"The post is {len(post)} characters; "
                         f"{platform} accepts at most {limit}.")
