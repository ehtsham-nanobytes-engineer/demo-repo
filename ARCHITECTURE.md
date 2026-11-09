# Codebase guide

A walkthrough of what each file does, how a request flows through the service,
and which rules are deliberate rather than incidental. Read `CLAUDE.md` first
for the spec; this document explains the implementation of it.

## What the service is

You give it a niche. It researches that niche on the live web, writes a
LinkedIn post grounded in what it found, formats it for LinkedIn's plain-text
renderer, and hands the text back. A human reviews and edits that text in the
frontend. Only when they approve does a second call publish it — now, or at a
time they pick.

Generation is stateless: one agent, no state between requests. The only state
in the service is the `publications` table that holds approved, scheduled posts.

## File map

| File | Responsibility |
| --- | --- |
| [app/main.py](app/main.py) | FastAPI app and the two routes. Request/response models, HTTP error mapping. Nothing else. |
| [app/agent.py](app/agent.py) | Builds the tool-calling agent, holds the system prompt, exposes `generate_post(niche) -> str`. |
| [app/tools.py](app/tools.py) | The single `web_search` tool the agent calls to gather sources. |
| [app/imagegen.py](app/imagegen.py) | OpenRouter image generation. `render(prompt) -> bytes`, model chosen by env. |
| [app/branding.py](app/branding.py) | Composites a brand's logo onto a generated image, as-is. |
| [app/linkedin.py](app/linkedin.py) | `publish_post(post) -> url` against the LinkedIn Posts API. |
| [app/meta.py](app/meta.py) | Facebook Page and Instagram publishing through the Graph API. |
| [app/publishing.py](app/publishing.py) | `publish_target()`: the single publish path for publish-now and the scheduler. |
| [app/publish_errors.py](app/publish_errors.py) | `PublishError` and `FailureKind`: transient, permanent or unknown. |
| [app/storage.py](app/storage.py) | Cloudinary: upload, original bytes, Instagram's derived URL, delete. |
| [app/db.py](app/db.py) | One short Neon connection per use (psycopg 3, pooler-safe). |
| [app/scheduler.py](app/scheduler.py) | Scheduling: create, claim, run, retry, the daily dispatch. |
| [app/queue_consumer.py](app/queue_consumer.py) | The private Vercel Queues subscriber that calls `scheduler.run`. |
| [app/routes/schedule.py](app/routes/schedule.py), [app/routes/cron.py](app/routes/cron.py) | `/schedule` and `/cron/dispatch`. |
| [app/auth.py](app/auth.py) | Optional `X-API-Key` guard on every route but the cron. |
| [migrations/001_publications.sql](migrations/001_publications.sql) | The one table. |
| [vercel.json](vercel.json) | `maxDuration` and the daily cron. |
| [app/config.py](app/config.py) | All env-backed settings, including every secret. |
| [requirements.txt](requirements.txt) | Pinned floors for the dependency set. |
| [.env.example](.env.example) | The variables you need to fill in to run it. |

## Request flow: `POST /generate`

```
client                main.py            agent.py            tools.py         web
  │                      │                   │                   │             │
  ├─ {"niche": "..."} ──▶│                   │                   │             │
  │                      ├─ generate_post ──▶│                   │             │
  │                      │                   ├─ LLM: pick query  │             │
  │                      │                   ├─ web_search ─────▶│             │
  │                      │                   │                   ├─ Tavily ───▶│
  │                      │                   │◀── sources ───────┤             │
  │                      │                   ├─ (loops if thin)  │             │
  │                      │                   ├─ write + format   │             │
  │                      │◀── post text ─────┤                   │             │
  │◀─ {"post": "..."} ───┤                   │                   │             │
```

Step by step:

1. [main.py:35](app/main.py#L35) validates the body against `GenerateRequest`
   — a niche string, 2–200 chars. Anything else is a 422 from Pydantic before
   any LLM cost is incurred.
2. [`generate_post`](app/agent.py#L76) runs **one** `AgentExecutor.invoke`. The
   executor's internal loop may call `web_search` several times — that is the
   agent deciding it needs more material, not multiple invocations.
3. Inside that loop, the LLM writes its own queries. It is not given a query
   template. [`web_search`](app/tools.py#L14) returns numbered blocks of
   `title / URL / excerpt`, which is what the model reads.
4. The system prompt ([agent.py:19](app/agent.py#L19)) carries both the
   grounding rule ("if you cannot support a claim from what you retrieved,
   leave it out") and the full LinkedIn formatting spec. Formatting is not a
   post-processing step — the model is asked to produce final text.
5. [`_strip_markdown`](app/agent.py#L82) is a safety net for the times the model
   emits markdown anyway. It unwraps `**bold**`, `__bold__`, backtick code,
   markdown headings and `-`/`*` bullets.
6. The route returns `{"post": "..."}` and nothing else.

## Request flow: `POST /image`

```
client              routes/image.py                    imagegen.py    branding.py
  │                      │                                      │              │
  ├─ {post, title} ─────▶│                                      │              │
  │                      ├─ build_prompt ──────────────────────▶│              │
  │                      │◀── brief + title + post ─────────────┤              │
  │                      ├─ render ────────────────────────────▶│              │
  │                      │◀── image bytes ──────────────────────┤              │
  │                      ├─ apply_logo, per selected brand ───────────────────▶│
  │                      │◀── branded PNG ────────────────────────────────────-┤
  │                      ├─ upload to social/drafts/<id>/<brand> (Cloudinary)
  │                      ├─ delete previous_draft_id, if any
  │◀─ {draft_id, urls} ──┤                                      │              │
```

The order is enforced by the schema, not by convention: `ImageRequest.post` has
`min_length=1`, so a call with no content is a 422 before any provider is
touched. There is no code path that produces an image without a post.

Three deliberate choices here:

- **There is no brief-writing LLM.** `image_brief.md` is art direction addressed
  to the image model, and it travels with the title and the whole post. An
  intermediate model compressing a 2000-character post into one sentence was a
  lossy step that cost a round trip; the image model reads the post itself.
  "Regenerate the image only" is still one endpoint call and no research loop.
- **The logo is composited, never prompted.** Diffusion models cannot render a
  specific mark; asking one to "include the logo" produces a smeared imitation.
  The brief keeps the lower-right of the frame plain and Pillow stamps the real
  file there afterwards. The corner is described as bare surface rather than as
  space reserved for something, so nothing is invited into it.
- **The mark is keyed and plated, not pasted.** A JPEG logo has no alpha, so its
  white paper would arrive as an opaque rectangle; `_key_white` drops it. What
  goes behind the mark is a rounded plate whose opacity is *measured* from how
  dark the photograph is underneath — invisible on a pale corner, strong enough
  to carry dark ink on a black one. A halo shaped from the mark's own alpha was
  tried first and does not work: a wordmark fills its bounding box, so dilating
  and blurring it just reproduces the box.
- **Images are stored, not returned.** Each branded copy goes to Cloudinary as a
  draft and the response carries signed URLs. Three base64 PNGs in one response
  exceeded Vercel's 4.5 MB limit; URLs keep every request and response tiny, and
  the reviewer sees the exact file that is published.
- **Every draft is deleted by something.** Regenerate deletes the draft it
  replaces (only after the new one is stored), New post deletes the current
  one, and the daily cron sweeps drafts older than `DRAFT_TTL_HOURS`. Scheduling
  copies the image out of the drafts folder so the sweep cannot take it.
- **A missing logo is a per-brand error, not a silent fallback.** Every image
  carries the mark; an unbranded image leaving the service would be the wrong
  default.

**On choosing a model.** The models that serve images are listed at
`https://openrouter.ai/api/v1/images/models`, and asking for a name not on it
does not fail. It quietly routes to a weak, heavily rate-limited community model,
which looks like "the images got worse for no reason". Keep `IMAGE_MODEL` in step
with that list. Its rate limits also arrive as a 429 nested inside its own 500,
so `_get_bytes` retries on the body as well as the status.

`_fit` settles the output size. Providers treat the requested dimensions as a
hint — the free tier answered 1062x555 for a 1200x627 request — so the image is
cover-cropped to exactly `IMAGE_WIDTH` x `IMAGE_HEIGHT` before branding.

## Request flow: `POST /publish`

The frontend sends back the text the human approved — possibly edited, possibly
rewritten entirely. The service does not re-check, re-generate, or compare it
against what `/generate` produced. It publishes exactly what it is given, then
returns the post URL.

`publish_post` builds the Posts API payload, sends it with the bearer token, and
reads the new post id from the `x-restli-id` **response header** — the id is not
in the body, which is a common first-time surprise with this API.

**An image post is three calls.** LinkedIn takes no image bytes inline, so
`_upload_image` registers the upload (`POST /rest/images?action=initializeUpload`),
`PUT`s the binary to the one-shot URL that comes back, and returns the image URN
for `content.media.id`. The upload runs *before* the post is created: if it
fails, nothing is published, rather than a text-only post appearing that the
reviewer never approved.

The image is the same base64 the frontend received from `/image` — sent back
untouched, not re-rendered — so what goes out is the picture the human looked at.

**Diagnosing a 401 or 403.** LinkedIn answers a permissions failure with an
empty body (`{"message":"","status":403}`), which says nothing about which of
the three possible causes it is. `scripts/check_linkedin.py` is the read-only
preflight that tells them apart — it calls identity endpoints only and cannot
publish. The three causes:

1. the token is missing `w_member_social` (or `w_organization_social`);
2. `LINKEDIN_AUTHOR_URN` is not the member the token belongs to. The id must be
   the `sub` from `/v2/userinfo` — an opaque alphanumeric string. The all-digit
   number visible in a profile page's source is a *different* internal id and
   the Posts and Images APIs reject it;
3. `LINKEDIN_API_VERSION` has aged out of the supported window (that one
   answers 426, not 403).

`_post` logs LinkedIn's error headers as well as the body, because the body is
so often empty, and appends the same three-way hint to the raised error.

`LINKEDIN_AUTHOR_URN` is validated against the two prefixes the Posts API
accepts. `urn:li:personal:` for `urn:li:person:` is an easy typo and LinkedIn
answers it with an opaque 400, so it is rejected here with a message that says
what is wrong.

## Brands and platforms

Three brands (NBT, PayProNext, TaxProNext) share one post and one picture; only
the logo and the accounts differ. `app/brands.py` holds identity (id, name,
default logo); `config.BrandSettings` holds each brand's credentials, read from
nested env vars (`NBT__META_PAGE_TOKEN`, …, via `env_nested_delimiter="__"`).

- **Text.** `/generate` writes the LinkedIn post and/or the Meta caption. When
  both are ticked the caption is distilled from the LinkedIn post rather than
  researched again — same story on every feed, and the second run is cheap.
- **Image.** Rendered once, branded for each selected brand, stored as a draft.
  A brand with no logo gets an error in its slot; the others still come back.
- **Publish.** One `/publish` call per ticked brand × platform box, so each
  succeeds or fails on its own and a retry never double-posts what already
  went out (the frontend locks finished targets).
- **Instagram** is the odd one out: its API downloads the image from a public
  URL instead of taking bytes, and only accepts JPEG between 4:5 and 1.91:1.
  The image is already in Cloudinary as an `authenticated` asset, and Meta is given a
  signed derived URL: JPEG at q_95, cropped only if outside the accepted range
  (the 1200x627 frame loses a pixel or two of width). In-memory staging was
  dropped because on serverless the instance Meta's fetch reaches is not the
  one holding the image.

## Request flow: scheduled publishing

```
React ─▶ POST /schedule ──rows──▶ Neon (publications)
              │  └─image (original bytes)──▶ Cloudinary
              │ send({id, version}, delay=until due)   ← only if due within 24h
              ▼
        Vercel Queues ──at the time──▶ queue_consumer.publish_due
              ▲                              │ scheduler.run(id, version)
   Vercel Cron (daily) /cron/dispatch        ├─ claim: scheduled → publishing
   enqueues the rest, re-sends lost,         ├─ publishing.publish_target(stored copy)
   flags stuck, deletes old images           └─ record: published | retry | failed | needs_review
```

**The claim is the only way to publish.** It is one `UPDATE … WHERE id AND
version AND status='scheduled' RETURNING *`, so exactly one caller wins. A
duplicate delivery, a message from before a reschedule (old `version`) or a
cancelled row all get nothing back, and the consumer stops.

**Failures are sorted by whether anything could have been posted.** Every
platform client raises `PublishError` with a `FailureKind`. A failure before
the create call — the LinkedIn image upload, the Instagram container — leaves
nothing on the feed however it fails, so it is `transient`. On the create call
itself only a refused connection, a 429 or a 5xx (bar 504) is transient; a 4xx
is `permanent`; a timeout or dropped connection after sending is `unknown`.
Transient rows go back to `scheduled` and are re-sent after 2, 4 and 8 minutes,
then `failed`. Unknown rows go to `needs_review` and are never retried: a
person checks the feed and resolves them.

**The consumer never raises after the claim.** A raised subscriber makes the
queue redeliver, which after a half-finished publish is how a post goes out
twice. The one exception is `ClaimUnavailable` — the database could not be
reached *before* anything was claimed — which becomes a `RetryAfter(60)`.

**Every intended send has its own idempotency key.** Vercel Queues drops a
send whose key it saw within min(retention, 24h). The first send is
`"{id}:{version}"`, a retry `"...:retry<n>"`, a cron recovery
`"...:recover<stamp>"`; reusing the first key would make each retry a silent
no-op. Each message's `retention` is set to its delay plus six hours, because
the queue's default 24h TTL is shorter than the cron's 26h window, and a
message whose delay outlives its TTL is never delivered.

**Scheduling a post is atomic.** The rows are inserted and the messages sent
in one transaction. If a send fails, the transaction rolls back and the
uploaded image is deleted, so a schedule either fully exists or does not. A
message sent for a transaction that then fails to commit finds no row and is
ignored. `scheduled_at` must be at least a minute out, so the commit always
lands before the message can.

**Messages belong to the deployment that sent them** (Vercel partitions topics
by deployment). Its code and env publish them, so a token rotated in env does
not reach messages already queued, and deleting that deployment strands them.
The 24h enqueue window bounds this; the daily cron re-sends from production.

**No claim timestamp.** The table is exactly as designed, so "stuck in
publishing" is measured from when the row was due or last sent. If the cron
flags a row that a slow attempt then finishes, the attempt's result still
wins: recording accepts `needs_review` rows as well as `publishing` ones.

## Design decisions worth knowing

**Why no LangGraph.** LangGraph earns its complexity when a graph needs to
pause, branch on external state, or resume. Here the human gate lives *outside*
the request: `/generate` finishes, the frontend holds the text, `/publish` is a
fresh call. Nothing inside the request waits on a human, so a plain
`AgentExecutor` is the whole requirement.

**Why the LLM chooses its own queries.** A fixed query template would produce
the same three articles for every niche. Letting the model search, read, and
search again with a different angle is what makes the post specific instead of
generic. `max_iterations=8` bounds the loop.

**Why the agent is cached.** [`_build_agent`](app/agent.py#L48) is
`@lru_cache`'d, so the model client and prompt are constructed once per process
rather than per request. `get_settings` is cached the same way.

**Why everything goes through OpenRouter.** OpenRouter is already a router
across vendors, so `LLM_MODEL=openai/gpt-oss-120b` switches vendor and model
with one env var, no code change and no extra package per provider. The same
key serves the image models, so there is one credential in the whole service.

**Why the key is passed explicitly.** `ChatOpenRouter` and the `OpenRouter`
client both fall back to reading `OPENROUTER_API_KEY` from the process
environment. `config.py` is the single place credentials are resolved, so the
key is handed to them directly instead — a key from `.env` and a key from a
real env var then behave identically.

## Guardrails — these are requirements, not preferences

- **Never scrape LinkedIn.** There is no feed/profile/search API, and scraping
  violates their ToS. Source material comes from web/news search only. This is
  why `tools.py` contains exactly one tool and why `linkedin.py` has no read
  path.
- **Never auto-publish.** `/generate` returns text. Publishing requires the
  explicit `/publish` or `/schedule` call from an approved frontend action.
  There is no code path from generation to publication. A scheduled post is an
  approval with a time attached: the consumer publishes the stored copy and
  never calls the agent or the image model.
- **Unknown outcomes are never retried.** They go to `needs_review`.
- **No credentials in the database.** Tokens stay in env; the table holds
  only the post, the image's Cloudinary id and the outcome.
- **The logo stays as approved.** LinkedIn and Facebook get the original
  bytes back from Cloudinary; `f_auto`/`q_auto` are never used.
- **A human reviews every post.** The Edit + approve step in the frontend is
  that gate. Treat generated text as a draft for review — not as approved
  outbound content — and keep that review in place for anything customer-facing.
- **Secrets only from `config.py`.** No key is read anywhere else, and no key is
  ever logged. `linkedin.py` surfaces LinkedIn's error body on failure but never
  the token.
- **`/generate` returns only `post`.** No sources array, no metadata, no token
  counts. Adding fields is a spec change, not an improvement.

## Deliberately out of scope

No duplication check and no in-request human-in-the-loop state machine. Meta's
native scheduling is deliberately not used: one scheduler covers all three
platforms. If any of this is needed later, it is a spec change first — start
in `CLAUDE.md`.

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in the keys
uvicorn app.main:app --reload
```

```bash
curl -X POST localhost:8000/generate \
  -H 'Content-Type: application/json' \
  -d '{"niche": "AI in diagnostic labs"}'
```

## Where to make a given change

| You want to… | Touch |
| --- | --- |
| Change the post's voice, structure or formatting | `SYSTEM_PROMPT` in `agent.py` |
| Swap the LLM or provider | `LLM_MODEL` env var — no code |
| Swap the search backend | `web_search` body in `tools.py`; keep the signature and docstring |
| Give the agent a second research tool | `tools.py`, then the tool lists in `_build_agent` |
| Change what the API accepts or returns | the Pydantic models in `app/schema/` |
| Change the art direction of images | `app/prompts/image_brief.md` |
| Move or restyle the logo lockup | `LOGO_*` env vars, then `_anchor` / `_shadow` in `branding.py` |
| Swap the image model or vendor | `IMAGE_MODEL` env var — no code |
| Use a different image vendor | `IMAGE_MODEL` — OpenRouter routes to it |
| Move or resize the logo | `LOGO_*` env vars — no code |
| Change publish visibility or targeting | the `payload` dict in `linkedin.py` |
| Change retry backoff, windows or attempts | the constants at the top of `scheduler.py` |
| Treat a platform error differently on retry | `publish_errors.py`, or `_TRANSIENT_CODES` in `meta.py` |
| Change the cron's time | `crons` in `vercel.json` (Hobby: once a day) |
| Change the table | a new `migrations/00N_*.sql`, then `scripts/migrate.py` |
