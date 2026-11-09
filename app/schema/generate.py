from pydantic import BaseModel, Field, model_validator


class GenerateRequest(BaseModel):
    """A niche, and which platforms to write for.

    `linkedin` asks for the full LinkedIn post, `meta` for the one-to-two line
    Facebook/Instagram caption. At least one must be true.
    """

    niche: str = Field(min_length=2, max_length=200)
    linkedin: bool = True
    meta: bool = False

    @model_validator(mode="after")
    def _at_least_one(self) -> "GenerateRequest":
        if not (self.linkedin or self.meta):
            raise ValueError("Pick at least one platform: linkedin or meta.")
        return self


class GenerateResponse(BaseModel):
    """One field per platform. A platform that was not asked for is null.

    Both are already formatted for their feed and ready to publish.
    """

    linkedin: str | None = None
    meta: str | None = None
