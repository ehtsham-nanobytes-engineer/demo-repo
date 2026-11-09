"""GET /brands — the brands a post can go out under, for the publish checkboxes.

Read-only and credential-free: each brand's name, a small copy of its logo, and
whether each platform is configured for it. The frontend greys out any
checkbox this says is not ready, so a missing token shows up before publishing
rather than as a failure halfway through.
"""

import base64

from fastapi import APIRouter

from app.brands import BRANDS, PLATFORMS
from app.branding import logo_thumbnail
from app.schema.brands import BrandInfo, PlatformStatus

router = APIRouter(tags=["brands"])


@router.get("/brands", response_model=list[BrandInfo])
def brands() -> list[BrandInfo]:
    out = []
    for brand in BRANDS:
        logo = logo_thumbnail(brand)
        platforms = {}
        for platform in PLATFORMS:
            reason = brand.missing(platform)
            platforms[platform] = PlatformStatus(ready=reason is None, reason=reason)
        out.append(BrandInfo(
            id=brand.id,
            name=brand.name,
            logo_base64=base64.b64encode(logo).decode("ascii") if logo else None,
            platforms=platforms,
        ))
    return out
