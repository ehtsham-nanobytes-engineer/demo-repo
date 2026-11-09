"""FastAPI app: wiring only.

Flow: POST /generate returns text only → the frontend shows it with an Edit
button → on approve, the frontend calls POST /publish (now) or POST /schedule
(later). Nothing auto-publishes: a scheduled post is an approval with a time
attached. The endpoints themselves live in app/routes/.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db, storage
from app.brands import BRANDS, PLATFORMS
from app.config import env_files, get_settings
from app.logging_setup import configure_logging, get_logger
from app.routes import api_router
from app.scheduler import Conflict, NotFound

configure_logging()
log = get_logger("api")
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Print what is configured on startup, so a missing key is obvious."""
    log.info("Post Generator and Scheduler")
    log.info("  env:     %s", " + ".join(env_files()))
    log.info("  model:   %s", settings.llm_model)
    log.info("  key:     %s",
             "OpenRouter" if settings.openrouter_api_key else "NOT CONFIGURED")
    log.info("  search:  %s", "Tavily" if settings.tavily_api_key else "NOT CONFIGURED")
    log.info("  image:   %s", settings.image_model)
    log.info("  storage: %s", "Cloudinary" if storage.configured() else "NOT CONFIGURED")
    log.info("  schedule: %s", (
        f"on (queue {'Vercel' if settings.queue_active else 'off — use scripts/run_due.py'})"
        if db.configured() else "off (DATABASE_URL not set)"))
    log.info("  api key: %s", "required" if settings.api_key else "off")
    for brand in BRANDS:
        logo = brand.logo_file()
        ready = [p for p in PLATFORMS if brand.missing(p) is None]
        log.info("  %-10s logo %s; publishes to %s", brand.name,
                 logo.name if logo.exists() else f"MISSING ({logo})",
                 ", ".join(ready) or "nothing yet")
    yield


app = FastAPI(title="LinkedIn Post Generator", lifespan=lifespan)

# The React frontend is served from its own origin, so the browser preflights
# every request. Origins come from config, never a wildcard.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-API-Key"],
)

app.include_router(api_router)


@app.exception_handler(NotFound)
async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(Conflict)
async def _conflict(_: Request, exc: Conflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})
