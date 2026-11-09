"""The brands a post can go out under, and where each one can publish.

The post and the picture are shared; the brand is what differs. Each brand has
its own logo, composited onto the one generated image, and its own LinkedIn
page, Facebook Page and Instagram account. Identity (id, name, default logo)
lives here in code; credentials come from env via `config.BrandSettings`.

Adding a brand is two lines: an entry in `BRANDS` below and a matching
`BrandSettings` field in `config.Settings`.
"""

from dataclasses import dataclass
from pathlib import Path

from app.config import BrandSettings, get_settings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

PLATFORMS = ("linkedin", "facebook", "instagram")


@dataclass(frozen=True)
class Brand:
    id: str                       # also the env prefix: NBT__..., PAYPRONEXT__...
    name: str
    logo_path: str                # default; `<ID>__LOGO_PATH` overrides it
    logo_path_on_dark: str | None = None

    @property
    def settings(self) -> BrandSettings:
        return getattr(get_settings(), self.id)

    def logo_file(self) -> Path:
        """Absolute path to this brand's logo. It may not exist yet."""
        return _resolve(self.settings.logo_path or self.logo_path)

    def logo_file_on_dark(self) -> Path | None:
        """The light-ink variant, if one is configured and present on disk."""
        setting = self.settings.logo_path_on_dark or self.logo_path_on_dark
        if not setting:
            return None
        path = _resolve(setting)
        return path if path.exists() else None

    @property
    def linkedin_token(self) -> str | None:
        return self.settings.linkedin_access_token or get_settings().linkedin_access_token

    def missing(self, platform: str) -> str | None:
        """Why this brand cannot publish to `platform`, or None when it can."""
        s = self.settings
        prefix = self.id.upper()
        if platform == "linkedin":
            if not s.linkedin_author_urn:
                return f"{prefix}__LINKEDIN_AUTHOR_URN is not set"
            if not self.linkedin_token:
                return "LINKEDIN_ACCESS_TOKEN is not set"
        elif platform == "facebook":
            if not s.facebook_page_id or not s.meta_page_token:
                return f"{prefix}__FACEBOOK_PAGE_ID and {prefix}__META_PAGE_TOKEN are not set"
        elif platform == "instagram":
            if not s.instagram_user_id or not s.meta_page_token:
                return f"{prefix}__INSTAGRAM_USER_ID and {prefix}__META_PAGE_TOKEN are not set"
            if not get_settings().cloudinary_url:
                return "CLOUDINARY_URL is not set, so Instagram cannot fetch the image"
        else:
            return f"unknown platform {platform!r}"
        return None


BRANDS: tuple[Brand, ...] = (
    Brand("nbt", "NBT", "assets/nbt_logo.jfif", "assets/nbt_logo-on-dark.png"),
    Brand("paypronext", "PayProNext", "assets/paypronext.jfif"),
    Brand("taxpronext", "TaxProNext", "assets/paytaxpronext.png"),
)

_BY_ID = {brand.id: brand for brand in BRANDS}


def get_brand(brand_id: str) -> Brand | None:
    return _BY_ID.get(brand_id)


def _resolve(setting: str) -> Path:
    path = Path(setting)
    return path if path.is_absolute() else _PROJECT_ROOT / path
