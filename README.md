# LinkedIn Post Generator

A FastAPI service that turns a niche into a ready-to-post LinkedIn post and/or
a Meta caption, with one image branded for each brand the user picks. A human reviews and approves
everything before it is published, now or at a scheduled time — nothing
auto-publishes.

## Running it

```bash
cp .env.example .env        # fill in OPENROUTER_API_KEY and TAVILY_API_KEY
uv sync
uvicorn app.main:app --reload
```

The React UI lives in [frontend/](frontend/) and has its own README.

Tests (no test reaches a real platform):

```bash
.venv/Scripts/python.exe -m pytest
```

### Scheduled publishing

Off until `DATABASE_URL` is set. To turn it on (details in
[SCHEDULER.md](SCHEDULER.md)):

1. Create the table once, with Neon's **direct** URL:
   `DIRECT_DATABASE_URL=... .venv/Scripts/python.exe scripts/migrate.py`
2. Set `DATABASE_URL` (Neon's **pooled** URL), `CLOUDINARY_URL` and `CRON_SECRET`.
3. On Vercel, enable Queues (Beta) for the project. The consumer is declared in
   `pyproject.toml` (`[[tool.vercel.subscribers]]`), the cron in `vercel.json`.

Locally there is no queue: schedule a post, wait for its time, then run
`.venv/Scripts/python.exe scripts/run_due.py` — it publishes for real.

## The endpoints

| Endpoint | Does |
| --- | --- |
| `POST /generate` | `{"niche": "...", "linkedin": true, "meta": true}` → `{"linkedin": "...", "meta": "..."}`. One tool-calling agent run per platform; the Meta caption is distilled from the LinkedIn post when both are asked for. |
| `POST /image` | `{"linkedin": "...", "meta": "...", "title": "...", "brands": ["nbt"], "previous_draft_id": null}` → one render, branded for each listed brand and stored in Cloudinary as a draft. Returns `draft_id` and a signed URL per brand, never bytes. Deletes `previous_draft_id` once the new draft exists. Content first — it needs the finished text as input. Pass `image_prompt` back to send the same prompt again. |
| `DELETE /image/{draft_id}` | Discard a draft (the frontend's New post). Drafts nobody publishes or discards are swept by the daily cron after `DRAFT_TTL_HOURS` (24). |
| `GET /brands` | The brands, a small logo each, and which platforms each can publish to. No credentials. |
| `POST /publish` | One approved post for one brand on one platform (`linkedin`, `facebook` or `instagram`), with the `draft_id` from `/image`. Posts that brand's stored copy as-is. Returns the URL. |
| `POST /schedule` | The same approval for later: one brand, its ticked platforms, the `draft_id`, a timezone-aware `scheduled_at`. The brand's draft image is copied out of the drafts folder; both are published as-is at that time. |
| `GET /schedule?status=` | The scheduled rows: time, status, link, error. |
| `PATCH /schedule/{id}` | New time and/or text, while still `scheduled`. |
| `DELETE /schedule/{id}` | Cancel, while still `scheduled`. |
| `POST /schedule/{id}/resolve` | For `needs_review`: a person says whether the post is on the feed. |
| `GET /cron/dispatch` | Vercel Cron only (`Authorization: Bearer $CRON_SECRET`). |

Brands, their logos and their per-platform credentials are configured in `.env`
(`NBT__...`, `PAYPRONEXT__...`, `TAXPRONEXT__...`) — see `.env.example`. Instagram
also needs `CLOUDINARY_URL`, because it fetches the image from a URL.

Set `API_KEY` before deploying anywhere public: without it, anyone with the URL
can publish and schedule for every brand.

## Layout

```
app/
  main.py          FastAPI wiring
  routes/          one module per endpoint: generate, image, publish, schedule, cron, brands
  schema/          the request/response models for each (common.py: shared rules)
  agent.py         the tool-calling agent
  tools.py         the web_search tool (Tavily)
  imagegen.py      OpenRouter image generation, render(prompt) -> bytes
  brands.py        the brand registry: names, default logos, per-brand config
  branding.py      composites a brand's logo onto a generated image
  formatting.py    markdown -> LinkedIn plain text
  linkedin.py      LinkedIn publishing client
  meta.py          Facebook Page + Instagram publishing client
  publishing.py    publish_target(): the one publish path, now or scheduled
  publish_errors.py PublishError + FailureKind: transient / permanent / unknown
  storage.py       Cloudinary: drafts, scheduled copies, the draft sweep
  db.py            Neon connection (psycopg 3, raw SQL)
  scheduler.py     create, claim, run and the daily dispatch
  queue_consumer.py the private Vercel Queues subscriber
  auth.py          optional X-API-Key guard
  prompts/         prompt fragments as .md, joined by composer.compose()
  config.py        env settings
assets/            each brand's logo, stamped on its copy of the image
frontend/          the React review UI
migrations/        001_publications.sql
scripts/           check_linkedin.py (credential preflight), migrate.py, run_due.py
tests/             pytest suite
docs/changes/      what each change touched, file by file
vercel.json        maxDuration and the daily cron
```

[ARCHITECTURE.md](ARCHITECTURE.md) explains why each piece is the way it is.

## Ground rules

- LinkedIn is never scraped. Source material comes from web/news search only.
- Nothing auto-publishes: `/generate` and `/image` only return content. A
  scheduled post is an approval with a time attached; at that time the stored
  copy goes out unchanged.
- An unknown publish outcome goes to `needs_review` and is never retried
  automatically.
- No image before the content — `/image` takes the finished post as input.
- Every generated image carries its brand's logo, composited as-is.
- Secrets come from the environment via `config.py` and are never logged.
