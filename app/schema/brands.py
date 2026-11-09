from pydantic import BaseModel


class PlatformStatus(BaseModel):
    """Whether a brand can publish to one platform, and why not if it cannot."""

    ready: bool
    reason: str | None = None


class BrandInfo(BaseModel):
    """One brand as the publish checkboxes need it. No credentials, ever.

    `logo_base64` is a small PNG of the brand's logo, or null when the file is
    missing — in which case that brand's image cannot be branded either.
    """

    id: str
    name: str
    logo_base64: str | None = None
    platforms: dict[str, PlatformStatus]
