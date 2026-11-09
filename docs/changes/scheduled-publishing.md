# Change log: scheduled publishing (Vercel Queues + Neon + Cloudinary)

October 2026. Implements [SCHEDULER.md](../../SCHEDULER.md). Nothing here is
committed yet. Review the diff, then commit.

## In one paragraph

A reviewer can now pick **Schedule** instead of **Publish now**. The approved
text goes into a Neon table (`publications`, one row per brand × platform) and
that brand's image goes to Cloudinary, byte for byte. At the scheduled time,
Vercel Queues invokes a private consumer. It claims the row, publishes the
stored copy through the same code path as publish-now, and records the result.
Nothing is generated or rewritten at publish time. A failure where nothing
reached the feed is retried after 2, 4 and 8 minutes. A failure the platform
refused is marked `failed`. If the outcome is unknown (a timeout after the
request went out), the row goes to `needs_review` and is never retried
automatically. A daily cron catches up on the rest. Instagram now always
fetches its image from Cloudinary, because the old in-memory staging can't work
on serverless.

---

## Step 0: pre-check results

| Check | Result |
| --- | --- |
| Vercel Queues availability | **Public beta, on all plans** (Vercel docs, Sep 2026). Not checked on *your* project: no Vercel project is linked in this repo, so enabling it is a dashboard step for you. |
| Python SDK names | Checked against the docs **and** the installed package (`vercel-queue 0.9.0`): `send(topic, payload, *, idempotency_key, retention, delay)`, `@subscribe(topic=, max_attempts=)`, `[[tool.vercel.subscribers]] entrypoint = "module.path"`, `RetryAfter(delay)`. A bare `dict` payload annotation is **rejected** by `@subscribe`, so the topic is typed (`Topic[PublishJob]`, a TypedDict). |
| Queue limits that changed the design | Default message TTL is **24h**, and a delay can't exceed the TTL. The cron's 26h window would create messages that expire undelivered, so every send sets `retention` explicitly. Idempotency keys dedupe for **min(retention, 24h)**, so a retry reusing `"{id}:{version}"` would be silently dropped. Retries and recoveries get their own keys. |
| `maxDuration` | Hobby: default and max **300s**. Pro: 800s. Instagram's ~60s publish fits on any plan. Set to 300 for `app/main.py` in `vercel.json`. The generated consumer function has **no documented `maxDuration` setting**. It runs on the platform default (300s), which is enough. |
| `/image` response size | **Measured: 2.92 MB** for 3 branded PNGs (~765 KB each, one real render). Under the 4.5 MB limit with ~35% headroom, so `/image` stays PNG and unchanged. A busier picture could grow toward the limit. If that happens, switching branding output to JPEG is a change to `/image` and needs your approval. |
| `assets/` and `app/prompts/` bundled | Vercel's Python build bundles the project directory. Neither folder is excluded. A new `.vercelignore` keeps `.env`/`.env.*` out of the bundle (otherwise production would read your local secrets at runtime). **Not verified on a real deploy.** |
| Fallback (cron-job.org) | Not needed, since Queues is on every plan. `scheduler.run_due()` (used by `scripts/run_due.py`) is the same "publish what's due" logic the fallback would call. |
| Uncommitted brands/meta work | Already committed before this started (`4d4afc8 Backend for multiple platforms`). |

---

## Where this differs from SCHEDULER.md, and why

1. **Idempotency keys per send.** First send `"{id}:{version}"`, a retry
   `"{id}:{version}:retry<n>"`, a cron recovery `"{id}:{version}:recover<stamp>"`.
   With one key per row, every retry inside 24h would be dropped as a duplicate.
2. **`retention` = delay + 6h (max 7 days)** on every message, for the TTL
   reason above.
3. **Retry count.** The design says "backoff 2/4/8 min, failed after 3
   attempts". Those two can't both hold: three attempts only use two backoffs.
   I went with **one first try + three retries (2, 4, 8 min) = 4 attempts**,
   then `failed`. To get the literal "3 attempts", drop `8` from
   `RETRY_BACKOFF` in `app/scheduler.py`. **Please confirm which you meant.**
4. **504 on the create call is `unknown`, not transient.** A gateway timeout
   says nothing about whether the platform created the post. Other 5xx and 429
   stay transient, as designed.
5. **Meta rate-limit codes are transient** (`4, 17, 32, 613, 80001, 80002`,
   plus `is_transient: true`). Meta returns rate limits as HTTP 400, which
   would otherwise read as permanent.
6. **Failures before the create call are always transient.** That covers the
   LinkedIn image upload, the Instagram container and the Cloudinary download,
   even on timeout, because nothing can be on the feed yet.
7. **Scheduling is atomic.** Rows are inserted and messages sent in one
   transaction. A failed send rolls everything back and deletes the uploaded
   image. `scheduled_at` must be ≥ 1 minute ahead so the commit always lands
   before the message.
8. **"Stuck in publishing" has no claim timestamp to use.** I kept the table
   exactly as designed, so the cron measures from `scheduled_at`/`enqueued_at`.
   To make a false flag harmless, recording an outcome also accepts
   `needs_review` rows. A slow attempt that finishes after the cron flagged it
   still records the real result.
9. **Image cleanup looks back 7–14 days only.** An Instagram row can't have
   its `image_public_id` nulled (the CHECK constraint), so there's no way to
   mark an image as deleted. The bounded window stops the cron asking about
   the same deleted image forever.
10. **Cloudinary assets are `authenticated`**, not public. Every URL is
    signed. LinkedIn and Facebook get the original bytes through the download
    API, not a delivery URL.
11. **`/schedule` only accepts PNG**, which is what `/image` returns. The
    original-bytes download asks for PNG.
12. **Auth was added (Step 9):** an optional `API_KEY` / `X-API-Key` header.
    It's off until you set it.

---

## Files: new

### Backend (`app/`)

**`app/publish_errors.py`**: the failure classification the scheduler depends on.
- `FailureKind`: `TRANSIENT` (nothing posted, safe to retry), `PERMANENT`
  (the platform refused), `UNKNOWN` (may have posted; a human checks).
- `PublishError(message, kind)`: base class of `LinkedInError` and `MetaError`.
- `network_failure(exc, final=)`: connect errors are transient. Any error
  before the create call is transient. A timeout or dropped connection *on*
  the create call is unknown.
- `status_failure(status, final=)`: 429 and 5xx are transient, 504 on the
  create call is unknown, other 4xx are permanent.

**`app/publishing.py`**: `publish_target(brand, platform, post, *, image_bytes | image_public_id, alt) -> url`.
The single publish path, used by `/publish` and by the scheduler. It re-checks
`brand.missing(platform)` at publish time, because tokens can expire after
scheduling. For a scheduled LinkedIn or Facebook post it downloads the original
bytes from Cloudinary. Instagram gets the signed derived URL. Publish-now to
Instagram uploads to Cloudinary, publishes, then deletes the copy (in `finally`).

**`app/storage.py`**: Cloudinary, configured from `CLOUDINARY_URL` and passed
per call (no global state).
- `upload(png, brand) -> public_id`: `social/<brand>/<uuid>`, type
  `authenticated`, original bytes.
- `original_bytes(public_id)`: the file exactly as uploaded (download API).
  Never `f_auto`/`q_auto`.
- `instagram_url(public_id)`: signed URL, JPEG q_95, cropped only when outside
  4:5–1.91:1.
- `delete(public_id)`: "not found" counts as deleted.

**`app/db.py`**: `connect()`: one short psycopg 3 connection per use, one
transaction (commit on success, rollback on error). Forces `sslmode=require`
unless the URL sets its own sslmode. Sets `prepare_threshold=None` for Neon's
PgBouncer pooler and uses `dict_row` rows. Also provides `configured()` and
`DatabaseNotConfigured`.

**`app/scheduler.py`**: all scheduling logic. The constants at the top are the
knobs: `ENQUEUE_WINDOW` 24h, `CRON_WINDOW` 26h, `MIN_LEAD` 1 min,
`STALE_AFTER` 15 min, `RETRY_BACKOFF` 2/4/8 min, `KEEP_IMAGES_FOR` 7 days.
- `TOPIC` / `PublishJob`: the typed queue topic `"publish"` carrying `{id, version}`.
- `create()`: upload the image once, insert all rows and enqueue those due
  within 24h, in one transaction.
- `list_rows()`, `update()` (a new time bumps `version`, clears
  `enqueued_at`, re-enqueues), `cancel()`, `resolve()`.
- `claim()`: the atomic `UPDATE … status='scheduled' → 'publishing' … RETURNING *`.
- `run(id, version)`: claim → `publish_target` → record. Never raises after
  the claim. It raises only `ClaimUnavailable` (DB unreachable before claiming).
- `run_due()`: publishes due rows without the queue (local).
- `dispatch()`: the daily cron's four jobs.
- Internals: `_send` (queue send with delay, retention, key; skipped when the
  queue is off), `_enqueue`, `_retry_later`, `_finish`, and `_write` (retries a
  DB write briefly and never raises; if it gives up, the cron flags the row).

**`app/queue_consumer.py`**: the private subscriber.
`@subscribe(topic=scheduler.TOPIC, max_attempts=10)` on `publish_due`. It runs
`scheduler.run` in a thread, swallows every error, and converts only
`ClaimUnavailable` into `RetryAfter(60)`. Malformed messages are logged and
dropped.

**`app/auth.py`**: `require_api_key`. When `API_KEY` is set, every route
except the cron needs `X-API-Key`. Off when unset.

**`app/routes/schedule.py`**: `POST /schedule`, `GET /schedule?status=`,
`PATCH /schedule/{id}`, `DELETE /schedule/{id}`, `POST /schedule/{id}/resolve`.
All answer 503 while `DATABASE_URL` is unset. Queue or DB failures while
scheduling answer 503 "nothing was scheduled".

**`app/routes/cron.py`**: `GET /cron/dispatch`. Needs
`Authorization: Bearer $CRON_SECRET` (constant-time compare). Returns 401
otherwise, and 401 for every call while the secret is unset.

**`app/schema/common.py`**: rules shared by `/publish` and `/schedule`:
`Platform`, `MAX_CHARS` per platform, `check_length`, `clean_base64`,
`decode`, `known_brand`, `ALT_MAX`.

**`app/schema/schedule.py`**: `ScheduleRequest` (timezone-aware, ≥ 1 min
ahead, stored as UTC; unique platforms; each platform configured for the brand;
Instagram needs the image; PNG only), `ScheduleTarget`, `ScheduleResponse`,
`ScheduledPost` (the list row: no post text, no credentials), `ScheduleUpdate`,
and `ResolveRequest`.

### Database and deployment

**`migrations/001_publications.sql`**: exactly the table and partial index
from SCHEDULER.md.

**`vercel.json`**: `maxDuration: 300` for `app/main.py`, plus a daily cron
`0 3 * * *` (03:00 UTC = 08:00 PKT) on `/cron/dispatch`.

**`.vercelignore`**: keeps `.env` and `.env.*` (but not `.env.example`) out of
the deployment, along with tests, docs, scripts, migrations and frontend
build output. `assets/` and `app/prompts/` are deliberately **not** listed.

### Scripts

**`scripts/migrate.py`**: applies `migrations/*.sql`, using
`DIRECT_DATABASE_URL` if set, otherwise `DATABASE_URL`. Reports "already
applied" if the table exists and never prints the URL. It exists because this
machine has no `psql`.

**`scripts/run_due.py`**: the local stand-in for the queue. It publishes
every due row through `scheduler.run`. **It publishes for real.**

### Tests (`tests/`, 80 total)

| File | What it proves |
| --- | --- |
| `conftest.py` | `no_network` (autouse): any real httpx request or Cloudinary call fails the test. `fake_env`: fake brand credentials. `client`: TestClient. |
| `fakes.py` | `response()`: canned platform HTTP responses. |
| `test_publish_errors.py` | Every failure mode of the LinkedIn and Meta clients lands in the right `FailureKind`: timeout on the post → unknown, timeout on the upload/container → transient, 429/5xx → transient, 401 → permanent, Graph rate-limit codes → transient. |
| `test_publishing.py` | `publish_target`: original bytes for scheduled LinkedIn, derived URL for Instagram, publish-now Instagram stages and **always** deletes, credentials re-checked, storage failure → transient. Also the `/publish` route (200 and 502). |
| `test_scheduler_run.py` | `run()` outcomes: no claim → nothing published; success; transient → retry with key `…:retry1` and 2-min delay; backoff 8 min, then gives up; permanent → failed; unknown → needs_review, never retried; a crash mid-publish → needs_review without raising. Includes **timeout → needs_review** and **429 → retried** through the real LinkedIn client. |
| `test_queue_consumer.py` | The consumer never raises (except `RetryAfter` when nothing was claimed) and drops malformed messages. End to end, what `scheduler._send` sends is delivered by the SDK's embedded queue to `publish_due`, and retention outlives the delay. |
| `test_schedule_api.py` | `/schedule` validation (naive time, past time, Instagram without image, non-PNG, duplicate platform, over-length, unknown brand, unconfigured platform), UTC storage, 503 without a DB, 404/409 mapping, cron auth (no header, wrong secret, unset secret), and the API key guarding all routes except the cron. |
| `test_scheduler_db.py` | **Real Postgres; skipped until `TEST_DATABASE_URL` is set.** Claim twice → second is a no-op, reschedule → old version ignored, cancel → never claimed, edits only while scheduled, resolve only `needs_review`, and the Instagram-needs-image constraint. |

### Docs

**`docs/changes/scheduled-publishing.md`**: this file.

---

## Files: changed

| File | What changed |
| --- | --- |
| `app/config.py` | Added `database_url`, `cloudinary_url`, `cron_secret`, `queue_enabled` (unset = on only inside Vercel) and `api_key`, all optional. Added the `queue_active` property. Removed `public_base_url` and `media_ttl_seconds`. |
| `app/linkedin.py` | `LinkedInError` is now a `PublishError` with a kind. `_post(..., final=)` classifies network and status errors. Only `POST /rest/posts` is `final=True`. The image registration and PUT are classified as pre-post steps. No behaviour change for publish-now. |
| `app/meta.py` | `MetaError` is now a `PublishError`. `publish_instagram(caption, image_url, ...)` takes a URL instead of bytes and no longer stages anything. Removed `_instagram_jpeg` (Cloudinary does the crop and JPEG). `_call(..., final=)` classifies errors, including Graph rate-limit codes. `/photos`, `/feed` and `/media_publish` are the final calls. |
| `app/brands.py` | Instagram readiness now needs `CLOUDINARY_URL` instead of `PUBLIC_BASE_URL`. |
| `app/routes/publish.py` | Calls `publishing.publish_target`. The platform `if/elif` moved there. Still maps failures to 502. |
| `app/routes/__init__.py` | Adds the schedule and cron routers. Every router except the cron sits behind `require_api_key`. Media router removed. |
| `app/schema/publish.py` | Uses the shared rules from `schema/common.py`. Same validation as before. |
| `app/main.py` | Maps `NotFound` → 404 and `Conflict` → 409. CORS allows PATCH/DELETE and the `X-API-Key` header. Startup log shows the storage, schedule/queue and API-key state. |
| `pyproject.toml` | Adds `psycopg[binary]`, `cloudinary` and `vercel-queue`, `pytest` (dev), the `[[tool.vercel.subscribers]]` entry for `app.queue_consumer`, and the pytest config. |
| `uv.lock` | Regenerated by `uv add`. `uv sync --locked` passes. |
| `.env.example` | Removed `PUBLIC_BASE_URL`. Added empty `CLOUDINARY_URL`, `DATABASE_URL`, `CRON_SECRET` and `API_KEY`. No existing values touched. |
| `frontend/src/api/client.js` | `call()` takes a method and sends `X-API-Key` when `VITE_API_KEY` is set. New `schedulePosts`, `listScheduled`, `cancelScheduled` and `resolveScheduled`. |
| `frontend/src/hooks/usePostStudio.js` | `mode` (now / schedule), `scheduledAt` (defaults to an hour ahead), the `schedule()` action (one `POST /schedule` per brand with its ticked platforms and its image), the Scheduled list, `cancel` and `resolve`. Scheduled targets are locked like published ones. |
| `frontend/src/components/PublishPanel.jsx` | Publish now / Schedule radio buttons, a `datetime-local` picker, a "⏱ Scheduled" status, and a button label that follows the mode. Schedule is greyed out when the backend has no DB. |
| `frontend/src/App.jsx` | Renders `ScheduledList` under the publish panel. |
| `frontend/src/styles/app.css` | Minimal functional styles for the toggle and list. The design is yours to do. |
| `frontend/.env.example` | Commented `VITE_API_KEY`. |
| `frontend/README.md` | Flow table and file list. |
| `CLAUDE.md` | The updates listed in SCHEDULER.md: removed "no scheduling, no database", reworded never-auto-publish, added needs_review, no DB credentials, one scheduler, no f_auto/q_auto. Also the schedule section, the new structure and the Instagram-via-Cloudinary text. |
| `README.md`, `ARCHITECTURE.md` | Endpoints, layout, how to turn scheduling on, and a new "Request flow: scheduled publishing" section with the design reasoning. |
| `SCHEDULER.md` | The header now says it's built and points here. The design text is unchanged. |
| `.env.test` (local, git-ignored) | Appended empty `DATABASE_URL`, `TEST_DATABASE_URL` and `CLOUDINARY_URL`. Existing values untouched. Its old `PUBLIC_BASE_URL` line is now unused and harmless. |

## Files: deleted

| File | Why |
| --- | --- |
| `app/media.py` | In-memory image staging for Instagram. On serverless, Meta's fetch hits a different instance than the one holding the image. Replaced by Cloudinary. |
| `app/routes/media.py` | The `GET /media/{token}` route that served it. |

`/generate`, `/image`, the agent, image generation and branding are **unchanged**.

---

## What was verified, and what wasn't

Verified here:
- `pytest`: **73 passed, 7 skipped** (the skips are the real-Postgres tests).
  A deliberate break (timeouts on the create call classified as transient)
  fails 5 tests, so the suite catches the double-post case.
- The app imports, all routes register, and `/cron/dispatch` without auth → 401.
- `uv sync --locked` passes.
- The queue SDK's send → subscriber path, via its embedded service.
- Cloudinary signed URLs are generated in the expected shape (offline).

Not verified (needs your accounts or tools this machine lacks):
- **No real Postgres run.** This machine has no Postgres or Docker, and
  executables under AppData are blocked by group policy. Set
  `TEST_DATABASE_URL` to a Neon dev branch and run `pytest`.
- **No real Cloudinary, Neon, Queues or Vercel deploy.** Not touched.
- **Frontend not built**, because Node isn't installed. Run `npm run build` in
  `frontend/`.
- **Publish-now on all three platforms after the refactor** (Step 4 asks for
  this) was tested with mocks only. Before relying on it, do one real publish
  per platform with `ENV_FILE=.env.test`, one brand at a time. Instagram now
  also needs `CLOUDINARY_URL`.
- Whether Vercel Deployment Protection lets Vercel Cron through. Check this
  in Vercel's docs before turning protection on.

## To turn it on

1. Create a Neon project with a `dev` branch. Run
   `DIRECT_DATABASE_URL=<direct url> .venv/Scripts/python.exe scripts/migrate.py`
   once per branch.
2. Fill `DATABASE_URL` (pooled), `CLOUDINARY_URL`, `CRON_SECRET` and `API_KEY`
   in the Vercel project env. Locally, fill `.env.test` with the dev branch.
3. Enable Queues (Beta) on the Vercel project and deploy.
4. Real test: schedule one brand 3–5 minutes out with personal accounts. On
   Vercel the queue publishes it. Locally, run `scripts/run_due.py` after the
   time passes.

## Known risks (unchanged from the plan)

- Queues is in beta.
- Messages run on the deployment that sent them. A rotated token doesn't reach
  messages already queued. Don't delete deployments that still have pending
  messages.
- Hobby cron runs once a day and only roughly on time. A lost message is
  recovered up to a day late.
- Remaining double-post exposure: a 500/502/503 from the create call is
  retried, as designed. If a platform ever returns one *after* creating the
  post, the retry would double-post. Make those `unknown` in
  `publish_errors.status_failure` if you'd rather be strict.
