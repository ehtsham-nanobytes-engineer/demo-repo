# assets

Each brand's logo, stamped onto that brand's copy of every generated image by
[app/branding.py](../app/branding.py). It is composited **as-is** — only scaled
and placed, never recoloured or cropped. The defaults live in
[app/brands.py](../app/brands.py); `<BRAND>__LOGO_PATH` overrides them.

| File | Brand | Used when |
| --- | --- | --- |
| `nbt_logo.jfif` | NBT | the default mark |
| `nbt_logo-on-dark.png` | NBT | the light-ink variant, picked automatically when the corner the logo lands in is too dark for the default |
| `paypronext.jfif` | PayProNext | the default mark. JPEG on a **black** background — only white is keyed out, so replace it with a transparent PNG |
| `paytaxpronext.png` | TaxProNext | the default mark (transparent PNG; 214px wide — a larger source will look crisper) |

A brand whose logo file is missing gets no image (an error in its slot instead),
and its publish checkboxes are disabled — an unbranded image never goes out.
Add `<brand>_logo-on-dark.png` and set `<BRAND>__LOGO_PATH_ON_DARK` for a
light-ink variant.

A JPEG has no alpha channel, so its white paper is keyed out at composite time
(`LOGO_KEY_WHITE=true`). A PNG with real transparency, at least ~600px wide and
with no baked-in padding, is the better source — the keying step skips itself.

## The frontend copies

`frontend/public/logo.png` and `logo-dark.png` are the NBT header logo, generated
from the same mark. The small logos on the publish checkboxes are not copied —
they come from `GET /brands`, straight from these files.
