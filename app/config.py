"""Environment-backed settings. Every secret lives here and nowhere else."""

import os
from functools import lru_cache

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class BrandSettings(BaseModel):
    """Per-brand logo and publishing credentials, read from nested env vars.

    Each brand in `app.brands` has one of these, filled from variables prefixed
    with its id and a double underscore — `NBT__LINKEDIN_AUTHOR_URN`,
    `PAYPRONEXT__META_PAGE_TOKEN` and so on. Anything left unset means that
    brand cannot publish there, and the frontend greys the checkbox out.
    """

    # Overrides for the brand's logo files; unset uses the default in app.brands.
    logo_path: str | None = None
    logo_path_on_dark: str | None = None

    # LinkedIn. The token falls back to the shared LINKEDIN_ACCESS_TOKEN, which
    # covers every company page its member is an admin of.
    linkedin_author_urn: str | None = None   # urn:li:organization:... for a page
    linkedin_access_token: str | None = None

    # Meta. One Page access token covers both the Facebook Page and the
    # Instagram professional account linked to it.
    facebook_page_id: str | None = None
    instagram_user_id: str | None = None
    meta_page_token: str | None = None


def env_files() -> tuple[str, ...]:
    """`.env`, then the overlay named by `ENV_FILE`, if any.

    `ENV_FILE=.env.test` layers the test file over `.env`: anything it sets
    wins, anything it leaves empty or out falls through to `.env`. That keeps
    the API keys in one file and lets the overlay hold only what differs — the
    personal test accounts.
    """
    overlay = os.environ.get("ENV_FILE", "").strip()
    return (".env", overlay) if overlay and overlay != ".env" else (".env",)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=env_files(), env_file_encoding="utf-8", extra="ignore",
        env_nested_delimiter="__",
        # An empty `KEY=` in the overlay must not blank out the value in .env.
        env_ignore_empty=True,
    )

    # LLM — everything runs through OpenRouter, so the model is a plain
    # OpenRouter slug ("vendor/model"). OpenRouter is itself the router across
    # vendors, which is what keeps the choice an env change and never a code
    # change. See https://openrouter.ai/models for what is currently served.
    llm_model: str = "openai/gpt-oss-120b"
    llm_temperature: float = 0.7

    # OpenRouter credentials and attribution. One key covers both the chat
    # models and the image models — there is nothing else to configure.
    openrouter_api_key: str | None = None
    openrouter_api_base: str | None = None   # unset means OpenRouter's own URL
    # Shown against this app's usage on the OpenRouter dashboard.
    openrouter_app_title: str = "linkedin-post-automation"
    openrouter_app_url: str | None = None
    # Transient failures are retried by the OpenRouter client itself. Its own
    # default keeps trying for an hour; a human is waiting on the other end of
    # every request here, so the budget is capped instead.
    openrouter_max_retries: int = 2          # chat models
    openrouter_retry_budget_seconds: float = 30.0
    openrouter_retry_initial_seconds: float = 0.5
    openrouter_retry_max_interval_seconds: float = 8.0
    openrouter_retry_exponent: float = 1.5

    # Source gathering.
    tavily_api_key: str | None = None
    search_max_results: int = 5
    search_topic: str = "news"   # general, news or finance
    # How far back results may come from, enforced by the search API itself
    # rather than left to whatever the model happens to type into a query.
    # Recency is the whole point of the search step, so this is deliberately
    # tight — widen it for niches that move slowly. day, week, month or year.
    search_time_range: str = "month"

    # LinkedIn. OAuth is handled separately by the org; we consume the token.
    # Which page a post goes out as is per brand (`<BRAND>__LINKEDIN_AUTHOR_URN`);
    # this is the shared token those pages fall back to.
    linkedin_access_token: str | None = None
    linkedin_api_base: str = "https://api.linkedin.com"
    linkedin_api_version: str = "202608"
    linkedin_max_chars: int = 3000        # LinkedIn's own cap on post length

    # Meta (Facebook Pages and Instagram), through the Graph API. The per-page
    # ids and tokens live on each brand.
    meta_graph_base: str = "https://graph.facebook.com"
    meta_graph_version: str = "v23.0"
    meta_max_chars: int = 2200            # Instagram's cap; Facebook's is far higher

    # Scheduled publishing. Each is optional: with DATABASE_URL unset the
    # /schedule and /cron routes answer 503 and publish-now works as before.
    # Neon's pooled URL (the "-pooler" host); app/db.py forces sslmode=require.
    database_url: str | None = None
    # cloudinary://<key>:<secret>@<cloud>. Required for images: /image stores
    # every branded draft here and the frontend shows them by URL. Also where
    # scheduled images wait and where Instagram fetches from (it takes a URL,
    # never bytes). Unset means /image answers 503 and posts are text only.
    cloudinary_url: str | None = None
    # How long an unpublished draft image is kept before the daily cron's
    # sweep deletes it. The cron runs once a day, so a draft can outlive this
    # by up to a day. Scheduled posts keep their own copy and are unaffected.
    draft_ttl_hours: int = 24
    # Vercel Cron sends this as "Authorization: Bearer <secret>". Unset means
    # /cron/dispatch rejects every caller.
    cron_secret: str | None = None
    # Whether /schedule hands due posts to Vercel Queues. Unset means "only when
    # running on Vercel" — locally there is no queue, so rows wait for
    # scripts/run_due.py instead.
    queue_enabled: bool | None = None

    # Optional shared key for every route except /cron (which has its own
    # secret). When set, callers must send it as the X-API-Key header.
    api_key: str | None = None

    # One block per brand; see BrandSettings. The ids match app.brands.
    nbt: BrandSettings = BrandSettings()
    paypronext: BrandSettings = BrandSettings()
    taxpronext: BrandSettings = BrandSettings()

    # Image generation, through the same OpenRouter key. Swapping the image
    # vendor is a change to this slug and nothing else; the list of models that
    # serve images is at https://openrouter.ai/api/v1/images/models.
    image_model: str = "google/gemini-2.5-flash-image"
    image_output_format: str = "png"  # png, jpeg or webp
    # Asked of the model as a ratio rather than exact pixels: vendors serve
    # their own fixed sizes and reject dimensions they do not have. The exact
    # frame below is settled afterwards by app.branding, which cover-crops.
    image_aspect_ratio: str = "16:9"
    image_width: int = 1200          # LinkedIn renders single images at 1200x627
    image_height: int = 627
    image_timeout_seconds: float = 120.0
    # Image providers cap the prompt they accept, and the cap is small: Grok's
    # is 8000 characters, and the art direction plus a full-length post can
    # cross it. `build_prompt` keeps the assembled prompt under this, so a long
    # post comes back as a picture rather than a 400 from the provider.
    image_prompt_max_chars: int = 8000

    # How each brand's logo is placed. The logo files themselves are per brand
    # (app.brands, overridable with `<BRAND>__LOGO_PATH`); relative paths are
    # resolved against the project root.
    logo_position: str = "bottom-right"   # any corner, see app/branding.py
    logo_width_ratio: float = 0.11        # logo width as a share of image width
    logo_margin_ratio: float = 0.03       # inset from the edges, same units
    # Nothing is drawn behind the mark — it sits directly on the photograph.
    # `logo_shadow` is the only legibility aid: a soft dark shadow shaped from
    # the mark's own outline, which is not a panel and does not box it in.
    logo_shadow: bool = True
    # A logo supplied as JPEG has no transparency, so its white background
    # arrives as an opaque rectangle. Keying it out lets the mark sit on the
    # photograph instead of in a box. Off for logos that use white as ink.
    logo_key_white: bool = True
    logo_key_threshold: int = 238         # 0-255; brighter than this is dropped

    # Browser origins allowed to call this API. The React dev server runs on
    # 5173 (Vite's default); add the deployed origin when there is one.
    # Comma-separated in the env, e.g. "http://localhost:5173,https://app.example".
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Terminal logging. DEBUG adds the search results and the full post text.
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        """`cors_origins` split into the list Starlette's middleware wants."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def queue_active(self) -> bool:
        """`queue_enabled`, defaulting to on only inside a Vercel deployment."""
        if self.queue_enabled is not None:
            return self.queue_enabled
        return bool(os.environ.get("VERCEL"))


@lru_cache
def get_settings() -> Settings:
    return Settings()