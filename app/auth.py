"""Optional shared API key for every route except the cron.

Off while API_KEY is unset, so local development is unchanged. When set, a
request must carry it as `X-API-Key`. Without this, anyone who finds the
deployed URL can publish and schedule for every brand.

The frontend sends its own copy (VITE_API_KEY), which ends up readable in the
built JavaScript — so this keeps out strangers who find the API URL, not
people who can load the frontend. Put the frontend itself behind Vercel
Deployment Protection (or SSO) for that.
"""

import secrets

from fastapi import Header, HTTPException

from app.config import get_settings


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().api_key
    if not expected:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Missing or wrong X-API-Key.")
