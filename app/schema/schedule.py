import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app import storage
from app.brands import get_brand
from app.scheduler import MIN_LEAD
from app.schema.common import (
    ALT_MAX, LONGEST_POST, Platform, check_length, known_brand, valid_draft,
)

Status = Literal["scheduled", "publishing", "published", "failed", "needs_review",
                 "cancelled"]


def _future(value: datetime | None) -> datetime | None:
    """A time with an explicit offset, at least MIN_LEAD from now, in UTC."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduled_at needs a timezone, e.g. 2026-10-07T09:00:00+05:00.")
    value = value.astimezone(UTC)
    if value < datetime.now(UTC) + MIN_LEAD:
        raise ValueError("scheduled_at must be at least a minute in the future.")
    return value


class ScheduleTarget(BaseModel):
    platform: Platform
    post: str = Field(min_length=1, max_length=LONGEST_POST)

    @model_validator(mode="after")
    def _fits_platform(self) -> "ScheduleTarget":
        check_length(self.platform, self.post)
        return self


class ScheduleRequest(BaseModel):
    """One brand's approved posts, to go out together at `scheduled_at`.

    The same shape as `/publish`, one brand per call. `targets` holds one
    entry per ticked platform: the LinkedIn post for `linkedin`, the Meta
    caption for `facebook` and `instagram`. `draft_id` is what `/image`
    returned; this brand's branded copy in it is copied out of the drafts
    folder, so the sweep that clears drafts can never take a scheduled image.
    """

    brand: str
    scheduled_at: datetime
    draft_id: str | None = None
    image_alt: str | None = Field(default=None, max_length=ALT_MAX)
    targets: list[ScheduleTarget] = Field(min_length=1, max_length=3)

    _known_brand = field_validator("brand")(known_brand)
    _draft = field_validator("draft_id")(valid_draft)
    _in_future = field_validator("scheduled_at")(_future)

    @model_validator(mode="after")
    def _publishable(self) -> "ScheduleRequest":
        platforms = [t.platform for t in self.targets]
        if len(set(platforms)) != len(platforms):
            raise ValueError("Each platform can appear only once per brand.")
        brand = get_brand(self.brand)
        for platform in platforms:
            reason = brand.missing(platform)
            if reason:
                raise ValueError(f"{brand.name} cannot publish to {platform}: {reason}")
        if "instagram" in platforms and not self.draft_id:
            raise ValueError("Instagram posts need an image.")
        return self

    @property
    def image_public_id(self) -> str | None:
        """This brand's copy in the draft, or None when the posts are text only."""
        return storage.draft_public_id(self.draft_id, self.brand) if self.draft_id else None


class ScheduledItem(BaseModel):
    id: uuid.UUID
    platform: Platform
    status: Status
    scheduled_at: datetime


class ScheduleResponse(BaseModel):
    items: list[ScheduledItem]


class ScheduledPost(BaseModel):
    """One row as the Scheduled list shows it. No post text, no credentials."""

    id: uuid.UUID
    brand: str
    platform: Platform
    scheduled_at: datetime
    status: Status
    external_url: str | None = None
    error: str | None = None


class ScheduleUpdate(BaseModel):
    """Move a still-scheduled post, edit its text, or both."""

    scheduled_at: datetime | None = None
    post: str | None = Field(default=None, min_length=1, max_length=LONGEST_POST)

    _in_future = field_validator("scheduled_at")(_future)

    @model_validator(mode="after")
    def _something(self) -> "ScheduleUpdate":
        if self.scheduled_at is None and self.post is None:
            raise ValueError("Send scheduled_at, post, or both.")
        return self


class ResolveRequest(BaseModel):
    """What a human found on the feed for a needs_review post."""

    published: bool
    external_url: str | None = Field(default=None, max_length=2000)
