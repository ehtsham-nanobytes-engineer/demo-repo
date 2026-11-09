"""Every route the app serves, gathered into one router.

main.py includes `api_router` and nothing else, so adding an endpoint is a
change inside this package: write the module, include it below.

Every route sits behind the optional API key except the cron, which Vercel
Cron calls with its own secret.
"""

from fastapi import APIRouter, Depends

from app.auth import require_api_key
from app.routes import brands, cron, generate, image, publish, schedule

_protected = APIRouter(dependencies=[Depends(require_api_key)])
_protected.include_router(generate.router)
_protected.include_router(image.router)
_protected.include_router(publish.router)
_protected.include_router(schedule.router)
_protected.include_router(brands.router)

api_router = APIRouter()
api_router.include_router(_protected)
api_router.include_router(cron.router)

__all__ = ["api_router"]
