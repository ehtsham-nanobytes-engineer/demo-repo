# CLAUDE.md — social post generator

A small FastAPI service that turns a niche into a ready-to-post LinkedIn post
and/or a short Facebook + Instagram ("Meta") caption, with one image branded
for each brand the user picks (NBT, PayProNext, TaxProNext).
The LLM uses tool calling to pull fresh sources, then writes and formats the post.
Approval and manual editing happen on the frontend; publishing is a separate call,
either now (`/publish`) or at a chosen time (`/schedule`).

## Tech stack
- Python 3.11+, FastAPI
- LangChain tool-calling agent (LangGraph not needed — this is a single-shot flow, no human gate inside the request)
- A web search tool (Tavily by default) as the agent's source-gathering tool
- `httpx` for the LinkedIn API, `pydantic-settings` for config

## Core endpoint (the one that matters)

`POST /generate`

Request — the niche and which platforms to write for (at least one true):
```json
{ "niche": "AI in diagnostic labs", "linkedin": true, "meta": true }
```

Response — one field per platform, null for one not asked for:
```json
{ "linkedin": "...", "meta": "..." }
```

No other fields. Each string is already formatted for its feed and ready to publish.
`linkedin` uses `system_role` + `research_process` + `output_roles`; `meta` uses
`meta_system_role` + `research_process` + `meta_output_rules` (a 1–2 line caption).

### How it works internally
1. A tool-calling LLM receives the niche and a `web_search` tool.
2. The LLM decides its own queries, calls the tool to fetch the latest sources,
   and reads the returned content. (This is the "scraping via tool calling" step —
   use a search API, never scrape LinkedIn.)
3. The LLM writes the post grounded in those sources, then applies LinkedIn formatting.
4. Return `{ "linkedin": "<text>", "meta": "<text>" }`.

One agent invocation per platform. When both are asked for, the Meta caption is
distilled from the LinkedIn post (no second research pass) so both tell the same
story. Ground claims in the retrieved sources to limit
hallucination.

## LinkedIn formatting rules
LinkedIn renders plain text only — no markdown.
- Strong first line as the hook (keep it within ~210 chars, before the "see more" fold)
- Short, line-broken paragraphs; blank lines between them
- One soft call to action
- 3–5 relevant hashtags on the last line
- Emoji allowed but sparse
- Stay under the ~3000 character limit

## Image endpoint (runs after generation)

`POST /image`
```json
{ "linkedin": "<post or null>", "meta": "<caption or null>", "title": "optional headline",
  "brands": ["nbt", "taxpronext"], "previous_draft_id": "<draft this replaces, or null>" }
```
Response — one branded copy per selected brand, stored as a draft:
```json
{ "draft_id": "<32 hex>",
  "images": [{ "brand": "nbt", "name": "NBT", "url": "<signed Cloudinary URL>",
               "download_url": "...", "error": null }],
  "media_type": "image/png", "image_prompt": "...", "expires_at": "<ISO time>" }
```

Content first, image second — always. At least one of `linkedin` / `meta` is
required and non-empty, so there is no way to ask for an image before the text
exists. When both are sent the image model reads both. One image serves every
platform; `brands` (at least one) decides who gets a copy.

### How it works internally
1. The art direction (`app/prompts/image_brief.md`), the title and the finished
   post are assembled into one prompt by `imagegen.build_prompt`. No second LLM
   call: the image model reads the post itself.
2. OpenRouter renders it (`POST /images`). OpenRouter is itself the router
   across image vendors, so `IMAGE_MODEL` is the only switch — swappable by
   env var, never hardcoded.
3. Rendered **once**; each selected brand's logo (`app/brands.py`) is then
   composited onto its own copy **as-is** (scaled and placed only, never
   recoloured or cropped) by `app/branding.py`. Branding is not optional: a
   brand whose logo is missing gets an `error` in its slot rather than an
   unbranded image.
4. Each copy is uploaded to Cloudinary as `social/drafts/<draft_id>/<brand>`
   (`authenticated`) and returned as a signed URL. No bytes in the response, so
   Vercel's 4.5 MB limit never applies however many brands are selected.
5. Only once the new draft is stored is `previous_draft_id` deleted, so a
   failed render never costs the user the images on screen.

`CLOUDINARY_URL` is required: without it `/image` is a 503 before rendering.
Pass `image_prompt` back in the request to send that exact prompt again and skip
the assembly step.

### Draft lifecycle
A draft is deleted when it is replaced (the next `/image` with
`previous_draft_id`), discarded (`DELETE /image/{draft_id}`, the frontend's
"New post"), or by the daily cron once it is `DRAFT_TTL_HOURS` (24) old.
Publishing does **not** delete it — the same draft may still go to another
brand or platform. Publishing an expired draft fails with a "regenerate" message
(permanent; nothing posted).

### The three frontend regenerate buttons
The endpoints compose; nothing new is needed for any of them.

| Button | Calls |
| --- | --- |
| Regenerate content only | `POST /generate` |
| Regenerate image only | `POST /image` with the same body + `previous_draft_id` |
| Regenerate the whole post | "New post" (`DELETE /image/{id}`), then `POST /generate`, then `POST /image` |

The brand and platform picks lock once images exist; "New post" unlocks them.

## Publish endpoint (used after approval)

`POST /publish`
```json
{
  "brand": "nbt | paypronext | taxpronext",
  "platform": "linkedin | facebook | instagram",
  "post": "<final text, possibly edited by the user>",
  "draft_id": "<from /image, or omitted for text only>",
  "image_alt": "<alt text for accessibility>"
}
```
One brand on one platform per call; the frontend calls it once per ticked
checkbox. `linkedin` sends the LinkedIn post, `facebook` / `instagram` the Meta
caption. Returns the post URL. Frontend flow: pick brands → `/generate` →
`/image` → show the text and each selected brand's image with an Edit button →
tick brand × platform boxes (only the selected brands) → `/publish` per box
with the (possibly edited) text and the `draft_id`. The brand's copy in the
draft is published as stored — exactly what was shown, never re-rendered or
re-branded. `GET /brands` feeds the checkboxes and the brand picker (names,
small logos, which platforms are configured — never credentials).

Per-brand credentials come from nested env vars: `<BRAND>__LINKEDIN_AUTHOR_URN`,
`<BRAND>__FACEBOOK_PAGE_ID`, `<BRAND>__INSTAGRAM_USER_ID`,
`<BRAND>__META_PAGE_TOKEN`, `<BRAND>__LOGO_PATH`.

LinkedIn: Posts API (`POST /rest/posts`). `w_member_social` for a
`urn:li:person:` author, `w_organization_social` for `urn:li:organization:`.

Facebook: `POST /{page}/photos` (multipart) or `/{page}/feed` for text only.
LinkedIn and Facebook get the stored image's original bytes (download API).
Instagram takes no bytes: it is given a signed derived URL of the stored image
(JPEG, cropped into 4:5–1.91:1), fetched by `POST /{ig-user}/media`, then
published with `/media_publish`.

All three go through `app/publishing.py` → `publish_target()`, which re-checks
the brand's credentials and raises `PublishError` with a `FailureKind`
(`app/publish_errors.py`): transient (nothing posted, safe to retry), permanent
(the platform refused, or the image is gone), or unknown (the create call may
have landed).

LinkedIn: an image post is three calls, not one: `POST /rest/images?action=initializeUpload`
reserves an image URN and an upload URL, the bytes go there with a `PUT`, then
the URN is referenced as `content.media.id` in the post body. LinkedIn does not
accept image bytes inline.

## Scheduled publishing (see SCHEDULER.md)

`POST /schedule` — one brand per call, the same pattern as `/publish`:
```json
{ "brand": "nbt", "scheduled_at": "2026-10-07T09:00:00+05:00",
  "draft_id": "...", "image_alt": "...",
  "targets": [ { "platform": "linkedin", "post": "..." } ] }
```
The text goes to Neon (`publications`, one row per brand × platform). The
brand's draft image is **copied** to `social/<brand>/<uuid>` (downloaded and
re-uploaded byte for byte), so the draft sweep can never take an image a
scheduled row needs; an expired draft is a 410. Rows due within 24h are sent to
Vercel Queues at once (`{id, version}`, delayed to the time); the daily cron
(`GET /cron/dispatch`, `Authorization: Bearer $CRON_SECRET`) enqueues later ones,
re-sends lost messages, flags stuck publishes, deletes scheduled images a week
after their rows finish, and sweeps old drafts. The private consumer
(`app/queue_consumer.py`) calls `scheduler.run(id, version)`: an atomic claim
(`scheduled → publishing`), `publish_target()` with the stored copy, then the
outcome on the row. Also: `GET /schedule?status=`, `PATCH /schedule/{id}` (new
time bumps `version`), `DELETE /schedule/{id}`, `POST /schedule/{id}/resolve`
for `needs_review`.

Outcomes: success → `published`; transient → back to `scheduled`, retried after
2/4/8 min, then `failed`; permanent → `failed`; unknown → `needs_review`.

Config: `DATABASE_URL` (Neon pooled), `CLOUDINARY_URL`, `CRON_SECRET`,
`DRAFT_TTL_HOURS` (default 24), optional `API_KEY` (X-API-Key header on every
route but the cron). Scheduling is off while `DATABASE_URL` is unset; the cron
still sweeps drafts. Locally there is no queue: `scripts/run_due.py`
publishes due rows. Migration: `migrations/001_publications.sql` via
`scripts/migrate.py`. Tests: `pytest` (DB tests need `TEST_DATABASE_URL`).

## Suggested structure (keep it flat)
```
app/
  main.py        # FastAPI app wiring
  routes/        # one module per endpoint: generate, image, publish
  schema/        # the request/response models for each
  agent.py       # the tool-calling agent
  tools.py       # web_search tool
  imagegen.py    # build_prompt(title, post) + OpenRouter render(prompt) -> bytes
  brands.py      # the brand registry: names, default logos, per-brand config
  branding.py    # composite a brand's logo onto a generated image
  formatting.py  # markdown -> LinkedIn plain text
  linkedin.py    # LinkedIn client (OAuth handled separately by the org)
  meta.py        # Facebook Page + Instagram client
  publishing.py  # publish_target(): the one publish path, now or scheduled
  publish_errors.py # PublishError + FailureKind (transient/permanent/unknown)
  storage.py     # Cloudinary: drafts, scheduled copies, the draft sweep
  db.py          # Neon connection (psycopg, raw SQL)
  scheduler.py   # create/claim/run/dispatch for scheduled posts
  queue_consumer.py # the private Vercel Queues subscriber
  auth.py        # optional X-API-Key guard
  prompts/       # prompt fragments as .md, joined by composer.compose()
  config.py      # env settings
assets/          # the logo stamped on every image
migrations/      # SQL, run once by scripts/migrate.py
tests/           # pytest; no test reaches a real platform
vercel.json      # maxDuration + the daily cron
```

## Conventions & guardrails
- One OpenRouter key covers both the text and the images. Model slugs are
  configurable via env; don't hardcode.
- All secrets from env via `config.py`. Never log or commit tokens.
- Never scrape LinkedIn (no feed/profile/search API exists; it violates ToS).
  Source material comes from web/news search only.
- Never auto-publish. `/generate` and `/image` only return content; publishing
  requires the explicit `/publish` or `/schedule` call from an approved action.
  A scheduled post is an approval with a time attached: the consumer publishes
  only the stored approved copy and never calls the agent or the image model.
- Unknown publish outcomes (timeout after sending, crash mid-publish) go to
  `needs_review` and are never auto-retried. A human checks the feed.
- No credentials in the database. Tokens stay in env.
- One scheduler for all platforms; Meta's native scheduling is not used.
- Never use Cloudinary `f_auto`/`q_auto` on the LinkedIn/Facebook image: they
  get the original bytes, so the logo stays exactly as approved.
- Never generate an image before the content. `/image` takes the finished post
  as input; it cannot run first.
- Every generated image carries its brand's logo, composited as-is in `/image`.
  What is published is that stored file; it is never re-branded or re-rendered.
- Images travel as Cloudinary ids and signed URLs, never as base64 in a request
  or response (Vercel's 4.5 MB body limit).
- Every draft has something that deletes it (replace, New post, or the daily
  sweep). Scheduled posts copy their image out of `social/drafts/` first.
- A human reviews every post before it goes out — the Edit + approve step is that gate.

## Do not build (out of scope for now)
- No duplication check. The `publications` table is the only database.
- No human-in-the-loop state machine inside the request — generation is stateless.
- Don't add response fields beyond `linkedin` and `meta` on `/generate`.
