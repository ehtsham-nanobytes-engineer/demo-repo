from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import get_settings
from app.schema.common import known_brand, valid_draft

settings = get_settings()


class ImageRequest(BaseModel):
    """The finished text the image is for, and the brands to brand it for.

    At least one of `linkedin` and `meta` must be non-empty, which is what
    enforces the order of the flow: there is no way to ask for an image before
    the content exists. One picture serves every platform; when both texts are
    sent the model reads both. The frontend's "regenerate image" button re-sends
    this same body, plus the draft it replaces.
    """

    linkedin: str | None = Field(default=None, max_length=settings.linkedin_max_chars)
    meta: str | None = Field(default=None, max_length=settings.meta_max_chars)
    title: str = Field(default="", max_length=300)

    # Only these brands get a branded copy, and only they can publish it.
    brands: list[str] = Field(min_length=1, max_length=10)

    # The draft this render replaces. Deleted once the new one is stored, so a
    # failed render never costs the user the images they already had.
    previous_draft_id: str | None = None

    # Escape hatch for a second pass: send the prompt back (edited or not) and
    # it goes to the image model verbatim, so only the picture is re-rolled.
    # Left empty on a normal call, in which case the prompt is assembled from
    # the art direction plus the text. The limit has room for both, since that
    # assembled prompt is what a caller sends back.
    image_prompt: str | None = Field(default=None, max_length=12000)

    _previous = field_validator("previous_draft_id")(valid_draft)

    @field_validator("brands")
    @classmethod
    def _known_unique(cls, value: list[str]) -> list[str]:
        for brand in value:
            known_brand(brand)
        if len(set(value)) != len(value):
            raise ValueError("Each brand can appear only once.")
        return value

    @model_validator(mode="after")
    def _has_content(self) -> "ImageRequest":
        if not (self.linkedin or "").strip() and not (self.meta or "").strip():
            raise ValueError("Content comes first: send the linkedin post, "
                             "the meta caption, or both.")
        return self

    @property
    def combined_post(self) -> str:
        """Everything the image should illustrate, as one block of text."""
        linkedin = (self.linkedin or "").strip()
        meta = (self.meta or "").strip()
        if linkedin and meta:
            return f"{linkedin}\n\nShort caption for the same image: {meta}"
        return linkedin or meta


class BrandImage(BaseModel):
    """One selected brand's copy of the picture, stored as a draft.

    `url` is a signed Cloudinary URL for the frontend to show; `download_url`
    serves the same file as an attachment. `error` is set instead when that
    brand could not be branded or stored. No unbranded image is ever returned.
    """

    brand: str
    name: str
    url: str | None = None
    download_url: str | None = None
    error: str | None = None


class ImageResponse(BaseModel):
    """One picture, branded once per selected brand, stored as one draft.

    `draft_id` is what `/publish` and `/schedule` take instead of image bytes,
    and what the next `/image` call sends as `previous_draft_id`. The draft is
    deleted by the daily sweep after `expires_at` if it is never replaced.

    `image_prompt` is the exact string the image model was given: the art
    direction, the title and the text. Send it back to re-render it unchanged.
    """

    draft_id: str
    images: list[BrandImage]
    media_type: str = "image/png"
    image_prompt: str
    expires_at: datetime
