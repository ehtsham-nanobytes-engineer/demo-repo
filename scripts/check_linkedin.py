"""Read-only preflight for the LinkedIn credentials. Publishes nothing.

Run this before the first publish, and any time `/publish` answers 401 or 403:

    python scripts/check_linkedin.py [brand]     # nbt (default), paypronext, taxpronext

It calls identity endpoints only — no POST to /rest/posts, no image upload, so
it cannot put anything on a feed. What it reports:

- whether the token is accepted at all;
- which member the token actually belongs to;
- whether the brand's <BRAND>__LINKEDIN_AUTHOR_URN matches that member (a mismatch is the usual
  cause of an empty-bodied 403 from the Images API);
- whether LINKEDIN_API_VERSION is still supported.

The token is never printed.
"""

import sys
from pathlib import Path

import httpx

# Run as `python scripts/check_linkedin.py` from the project root without
# needing the package installed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brands import BRANDS, get_brand  # noqa: E402
from app.config import get_settings  # noqa: E402

OK, BAD, MEH = "PASS", "FAIL", "??"


def main() -> int:
    settings = get_settings()
    brand = get_brand(sys.argv[1] if len(sys.argv) > 1 else "nbt")
    if not brand:
        print(f"Unknown brand. Pick one of: {', '.join(b.id for b in BRANDS)}")
        return 1
    prefix = brand.id.upper()
    token = brand.linkedin_token
    urn = brand.settings.linkedin_author_urn

    print(f"LinkedIn preflight for {brand.name} — read-only, nothing is published\n")

    if not token or not urn:
        print(f"[{BAD}] LINKEDIN_ACCESS_TOKEN and {prefix}__LINKEDIN_AUTHOR_URN must both be set.")
        return 1

    print(f"  author urn : {urn}")
    print(f"  api version: {settings.linkedin_api_version}")
    print(f"  token      : {len(token)} characters\n")

    failures = 0
    member_id = None

    # 1. Identity. This needs the openid/profile scopes; without them the token
    #    cannot tell us who it is, which is itself the finding.
    identity = _get("https://api.linkedin.com/v2/userinfo", token)
    if identity.get("status") == 200:
        member_id = identity["body"].get("sub")
        name = identity["body"].get("name", "")
        print(f"[{OK}] token identity: {member_id} ({name})")
    else:
        failures += 1
        print(f"[{BAD}] cannot read the token's identity "
              f"(HTTP {identity.get('status')}): {identity.get('text', '')[:120]}")
        print("      The token is missing the 'openid' and 'profile' scopes, so")
        print("      the author URN below cannot be checked against it.")

    # 2. Does the configured URN belong to this token?
    if member_id:
        expected = f"urn:li:person:{member_id}"
        if urn == expected:
            print(f"[{OK}] {prefix}__LINKEDIN_AUTHOR_URN matches the token's member")
        elif urn.startswith("urn:li:organization:"):
            print(f"[{MEH}] posting as a company page; the page must have this app")
            print("      verified and the token needs w_organization_social")
        else:
            failures += 1
            print(f"[{BAD}] {prefix}__LINKEDIN_AUTHOR_URN does not match the token's member")
            print(f"      configured: {urn}")
            print(f"      should be : {expected}")
    elif urn.startswith("urn:li:person:"):
        suffix = urn.split(":")[-1]
        if suffix.isdigit():
            failures += 1
            print(f"[{BAD}] {prefix}__LINKEDIN_AUTHOR_URN is all digits ({suffix})")
            print("      OAuth member ids are opaque alphanumeric strings. A plain")
            print("      number is usually the internal id from a profile page,")
            print("      which the Posts and Images APIs reject with a 403.")

    # 3. Is the version header still accepted? A retired version answers 426.
    version = _get("https://api.linkedin.com/rest/me", token,
                   version=settings.linkedin_api_version)
    if version.get("status") == 426:
        failures += 1
        print(f"[{BAD}] LINKEDIN_API_VERSION {settings.linkedin_api_version} "
              f"is no longer supported")
    elif version.get("status") in (200, 403):
        # 403 here means the version was accepted and only the scope was short.
        print(f"[{OK}] LINKEDIN_API_VERSION {settings.linkedin_api_version} "
              f"is accepted")
    else:
        print(f"[{MEH}] could not confirm the API version "
              f"(HTTP {version.get('status')})")

    print()
    if failures:
        print(f"{failures} problem(s) found. Fix these before publishing:\n")
        print("  1. Re-issue the token with ALL of these scopes:")
        print("       openid profile w_member_social")
        print("  2. Read your real member id:")
        print("       curl -H 'Authorization: Bearer <TOKEN>' \\")
        print("            https://api.linkedin.com/v2/userinfo")
        print(f"  3. Set {prefix}__LINKEDIN_AUTHOR_URN=urn:li:person:<the sub value>")
        return 1

    print("All checks passed. Publishing should work.")
    return 0


def _get(url: str, token: str, version: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    if version:
        headers["LinkedIn-Version"] = version
        headers["X-Restli-Protocol-Version"] = "2.0.0"
    try:
        response = httpx.get(url, headers=headers, timeout=20)
    except httpx.HTTPError as exc:
        return {"error": str(exc)}

    out = {"status": response.status_code, "text": response.text}
    if response.status_code == 200:
        try:
            out["body"] = response.json()
        except ValueError:
            out["body"] = {}
    return out


if __name__ == "__main__":
    raise SystemExit(main())
