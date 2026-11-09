from pydantic import BaseModel, Field, field_validator, model_validator

from app import storage
from app.schema.common import (
    ALT_MAX, LONGEST_POST, Platform, check_length, known_brand, valid_draft,
)


class PublishRequest(BaseModel):
    """One approved post for one brand on one platform.

    The frontend calls this once per ticked checkbox, so each target succeeds
    or fails on its own. `post` is the LinkedIn post for `linkedin` and the
    Meta caption for `facebook` and `instagram`. `draft_id` is what `/image`
    returned: this brand's branded copy in it is published as stored, so what
    goes out is the image the human actually looked at. Omit it for text only.
    """

    brand: str
    platform: Platform
    post: str = Field(min_length=1, max_length=LONGEST_POST)
    draft_id: str | None = None
    image_alt: str | None = Field(default=None, max_length=ALT_MAX)

    _known_brand = field_validator("brand")(known_brand)
    _draft = field_validator("draft_id")(valid_draft)

    @model_validator(mode="after")
    def _fits_platform(self) -> "PublishRequest":
        check_length(self.platform, self.post)
        if self.platform == "instagram" and not self.draft_id:
            raise ValueError("Instagram posts need an image.")
        return self

    @property
    def image_public_id(self) -> str | None:
        """This brand's copy in the draft, or None when the post is text only."""
        return storage.draft_public_id(self.draft_id, self.brand) if self.draft_id else None


class PublishResponse(BaseModel):
    url: str
