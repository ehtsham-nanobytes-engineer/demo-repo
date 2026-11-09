"""Stamp a brand's logo onto a generated image.

One picture is generated per post and each brand the user selected gets its
own copy with its own mark, in `/image`. Those copies are stored as drafts and
are exactly what is published. The logo is composited **as-is**: it is only scaled
(aspect ratio preserved) and placed, never recoloured, cropped, rotated or
restyled. Transparency in the source PNG is respected, so a logo on a
transparent background keeps its shape.

A generated background can be any colour, so two steps make the mark sit *in*
the picture rather than on top of it:

- a logo with no alpha channel (a JPEG) has its white paper keyed out, so the
  mark is the only thing composited (`LOGO_KEY_WHITE=false` to skip);
- where a light-ink variant of the mark is supplied (`<BRAND>__LOGO_PATH_ON_DARK`),
  the one that reads against this particular corner is chosen. Both are brand
  assets composited as-is; nothing is recoloured here;
- nothing is drawn behind it — no plate, no panel. The only legibility aid is a
  soft drop shadow in the shape of the mark itself, at a strength measured from
  how light the photograph is underneath (`LOGO_SHADOW=false` to skip).
"""

import io
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from app.brands import Brand
from app.config import get_settings
from app.logging_setup import get_logger

log = get_logger("branding")

# Where each named position anchors the logo box, as (x, y) fractions of the
# free space left over once the logo and its margins are accounted for.
_POSITIONS = {
    "bottom-right": (1.0, 1.0),
    "bottom-left": (0.0, 1.0),
    "top-right": (1.0, 0.0),
    "top-left": (0.0, 0.0),
    "bottom-center": (0.5, 1.0),
    "top-center": (0.5, 0.0),
}


class BrandingError(RuntimeError):
    """Raised when the logo is missing or the generated image is unreadable."""


def apply_logo(image_bytes: bytes, brand: Brand) -> bytes:
    """Composite `brand`'s logo onto `image_bytes`; return PNG bytes.

    Raises `BrandingError` if the logo file is missing — every generated image
    carries the mark, so a silent unbranded fallback would be the wrong answer.
    """
    settings = get_settings()

    base = _fit(_open(image_bytes), (settings.image_width, settings.image_height))
    target_width = max(1, round(base.width * settings.logo_width_ratio))
    margin = round(base.width * settings.logo_margin_ratio)

    logo = _load_logo(_require_logo(brand), settings)
    logo = _scale(logo, target_width=target_width)
    left, top = _anchor(base.size, logo.size, margin=margin,
                        position=settings.logo_position)

    # Which variant reads against this particular photograph, measured under
    # the box the standard mark would cover. Nothing is recoloured here: both
    # files are supplied brand assets and whichever is chosen is composited
    # exactly as it is on disk.
    on_dark = brand.logo_file_on_dark()
    if on_dark and _is_dark(base, (left, top), logo.size):
        log.debug(f"{brand.name}: dark corner — using the light-ink logo at {on_dark.name}")
        logo = _scale(_load_logo(on_dark, settings), target_width=target_width)
        left, top = _anchor(base.size, logo.size, margin=margin,
                            position=settings.logo_position)

    canvas = base.copy()
    if settings.logo_shadow:
        # How dark the photograph already is decides how much shadow helps. On a
        # pale corner a shadow is what separates the mark from the background;
        # on a dark one there is nothing to gain, so it fades out.
        strength = _shadow_strength(base, (left, top), logo.size)
        canvas.alpha_composite(_shadow(base.size, (left, top), logo, strength))
    canvas.alpha_composite(logo, dest=(left, top))

    log.info(f"{brand.name} logo composited at {settings.logo_position} "
             f"({logo.width}x{logo.height} on {base.width}x{base.height})")

    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


def logo_thumbnail(brand: Brand, height: int = 64) -> bytes | None:
    """`brand`'s logo, scaled to `height`, as PNG — or None if it is missing.

    The same keyed mark that goes onto the images, shrunk for the frontend's
    publish checkboxes, so the UI never needs its own copy of every logo.
    """
    path = brand.logo_file()
    if not path.exists():
        return None
    try:
        logo = _load_logo(path, get_settings())
    except BrandingError:
        return None
    width = max(1, round(logo.width * height / logo.height))
    out = io.BytesIO()
    logo.resize((width, height), Image.LANCZOS).save(out, format="PNG", optimize=True)
    return out.getvalue()


def missing_logo(brand: Brand) -> str | None:
    """Why `brand`'s image cannot be branded, or None when it can."""
    path = brand.logo_file()
    if path.exists():
        return None
    return (f"{brand.name} logo not found at {path}. Add it there or set "
            f"{brand.id.upper()}__LOGO_PATH (PNG with transparency works best).")


def _require_logo(brand: Brand) -> Path:
    reason = missing_logo(brand)
    if reason:
        log.error(reason)
        raise BrandingError(reason)
    return brand.logo_file()


def _open(image_bytes: bytes) -> Image.Image:
    try:
        return Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    except OSError as exc:
        log.error(f"The image could not be decoded: {exc}")
        raise BrandingError(f"The image could not be decoded: {exc}") from exc


def _load_logo(path: Path, settings) -> Image.Image:
    """Open a logo file as RGBA, with a JPEG's white paper keyed out."""
    try:
        logo = Image.open(path).convert("RGBA")
    except OSError as exc:
        log.error(f"The logo at {path} could not be decoded: {exc}")
        raise BrandingError(f"The logo at {path} could not be read: {exc}") from exc

    if settings.logo_key_white:
        logo = _key_white(logo, settings.logo_key_threshold)
    return logo


def _fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Cover-crop to exactly `size`.

    Providers treat the requested dimensions as a hint — the free tier came back
    1062x555 for a 1200x627 request — so the size is settled here instead. Cover
    rather than stretch: the subject keeps its proportions, the edges give.
    """
    if image.size == size:
        return image

    scale = max(size[0] / image.width, size[1] / image.height)
    scaled = image.resize(
        (max(size[0], round(image.width * scale)),
         max(size[1], round(image.height * scale))),
        Image.LANCZOS,
    )
    left = (scaled.width - size[0]) // 2
    top = (scaled.height - size[1]) // 2
    return scaled.crop((left, top, left + size[0], top + size[1]))


def _scale(logo: Image.Image, *, target_width: int) -> Image.Image:
    """Resize to `target_width`, keeping the aspect ratio. No cropping."""
    if logo.width == target_width:
        return logo
    height = max(1, round(logo.height * target_width / logo.width))
    return logo.resize((target_width, height), Image.LANCZOS)


def _anchor(base_size, logo_size, *, margin: int, position: str) -> tuple[int, int]:
    """Top-left pixel for the logo under the named position."""
    fx, fy = _POSITIONS.get(position, _POSITIONS["bottom-right"])
    if position not in _POSITIONS:
        log.warning(f"Unknown LOGO_POSITION {position!r}; using bottom-right")

    free_x = max(0, base_size[0] - logo_size[0] - 2 * margin)
    free_y = max(0, base_size[1] - logo_size[1] - 2 * margin)
    return margin + round(free_x * fx), margin + round(free_y * fy)


def _key_white(logo: Image.Image, threshold: int) -> Image.Image:
    """Make a logo's white background transparent.

    A JPEG logo has no alpha channel, so its white paper arrives as opaque
    pixels and the mark can only ever sit in a white rectangle. Pixels brighter
    than `threshold` on all three channels become fully transparent; the ones
    just below it fade out proportionally, which keeps anti-aliased edges soft
    instead of leaving a jagged halo.

    Off (`LOGO_KEY_WHITE=false`) for a logo that uses white as ink — keying that
    one would erase the mark itself.
    """
    if logo.getchannel("A").getextrema()[0] < 255:
        return logo  # already has real transparency; leave it alone

    rgb = logo.convert("RGB")
    # The brightest channel decides: a pixel is background only when it is pale
    # in all three, so the orange of the mark survives even where it is light.
    darkest = ImageChops.darker(
        ImageChops.darker(rgb.getchannel("R"), rgb.getchannel("G")),
        rgb.getchannel("B"),
    )
    # Map [threshold-12 .. threshold] onto [opaque .. transparent].
    span = 12
    floor = max(0, threshold - span)
    alpha = darkest.point(
        lambda v: 255 if v <= floor else (0 if v >= threshold
                                          else round(255 * (threshold - v) / span))
    )
    keyed = logo.copy()
    keyed.putalpha(alpha)

    covered = sum(alpha.point(lambda v: 1 if v > 8 else 0).getdata())
    log.debug(f"Keyed the logo background out; {covered} pixel(s) of mark remain")
    return keyed


def _is_dark(base: Image.Image, dest, size) -> bool:
    """Is the box the logo lands in too dark for the standard mark?

    Measured from the exact box the scaled mark will cover, so the decision is
    about those pixels rather than the image as a whole — and it holds for any
    brand's lockup, whatever its proportions.
    """
    left, top = dest
    width, height = size
    patch = base.crop((left, top, left + width, top + height)).convert("L")
    mean = sum(patch.getdata()) / max(1, patch.width * patch.height)
    log.debug(f"Corner brightness under the logo: {mean:.0f}/255")
    return mean < 128


def _shadow_strength(base: Image.Image, dest, logo_size) -> float:
    """How much shadow the corner wants, from 0.0 to 1.0.

    Read from the photograph directly under the mark. A shadow lifts a logo off
    a light or busy background; against an already-dark one it does nothing, so
    it is faded out rather than stacking murk on murk.
    """
    pad = max(6, round(min(logo_size) * 0.25))
    box = (max(0, dest[0] - pad), max(0, dest[1] - pad),
           min(base.width, dest[0] + logo_size[0] + pad),
           min(base.height, dest[1] + logo_size[1] + pad))

    patch = base.crop(box).convert("L")
    mean = sum(patch.getdata()) / max(1, patch.width * patch.height)

    if mean >= 150:
        return 1.0
    if mean <= 60:
        return 0.25
    return 0.25 + 0.75 * (mean - 60) / 90


def _shadow(base_size, dest, logo: Image.Image, strength: float) -> Image.Image:
    """A soft drop shadow in the shape of the mark itself.

    Deliberately not a plate: it is built from the logo's own alpha, offset a
    little and blurred, so there is no panel, no rectangle and no edge — just
    enough separation for the mark to read against whatever is behind it.
    """
    if strength <= 0:
        return Image.new("RGBA", base_size, (0, 0, 0, 0))

    blur = max(2.0, logo.height * 0.10)
    offset = max(1, round(logo.height * 0.06))

    mask = Image.new("L", base_size, 0)
    mask.paste(logo.getchannel("A"), (dest[0], dest[1] + offset))
    mask = mask.filter(ImageFilter.GaussianBlur(blur))
    mask = mask.point(lambda v: round(v * 0.55 * strength))

    shadow = Image.new("RGBA", base_size, (8, 14, 18, 0))
    shadow.putalpha(mask)
    return shadow
