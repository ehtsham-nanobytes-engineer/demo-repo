# Scheduled publishing — design

Built (October 2026). This file stays the design reference; what was built,
file by file, and where it differs from this design is in
[docs/changes/scheduled-publishing.md](docs/changes/scheduled-publishing.md).

## The idea

Generate → image → review stays the same. On approval the reviewer picks
**Publish now** or **Schedule for <time>**. Scheduling saves exactly what was
approved: the text in Neon and the brand's branded image, copied in Cloudinary
from its draft. At that time, Vercel Queues wakes a private function that
publishes them with the existing `linkedin.py` / `meta.py` clients. Nothing is
generated, rewritten or re-branded at publish time.

**One scheduler for all three platforms.** LinkedIn, Facebook and Instagram all
go through the same table, queue and consumer. Meta's native scheduling
(Facebook's `scheduled_publish_time`) is not used, so cancel, reschedule,
status and retries work the same way everywhere and live in one place.

Nothing else is stored: no niche, no prompts, no chat, no image prompt.

```
React ─▶ FastAPI on Vercel ──rows──▶ Neon
              │  └──images──▶ Cloudinary
              │ send(delay)
              ▼
        Vercel Queues ──at scheduled time──▶ private consumer ──▶ LinkedIn / FB / IG
              ▲
   Vercel Cron (daily) ── enqueues posts more than 24h out, catches missed ones
```

| Piece | Job |
| --- | --- |
| Neon | One table, `publications`. The source of truth. |
| Cloudinary | One copy of the brand's draft image per schedule |
| Vercel Queues | Delivers `{id, version}` at the scheduled time |
| Consumer function | Private, not a public endpoint. Claims the row, publishes, records the result. |
| Vercel Cron (daily) | Enqueues posts more than 24h out, re-sends missed ones, cleans up |

Why not the alternatives:
- **Celery** needs a long-running worker and beat, which Vercel doesn't have.
  Its eta/countdown isn't deferred on Vercel.
- **cron-job.org** polls every minute and gives up on a request after a short
  timeout, while an Instagram publish can take about a minute. It also needs a
  public endpoint. It is kept only as the fallback if Queues can't be enabled.
- **Meta native scheduling** covers Facebook only. LinkedIn has none, so it
  would still need its own scheduler, which means two systems to keep in step.

## Schema (Neon)

```sql
CREATE TABLE publications (                 -- one row per ticked brand × platform
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  brand           text NOT NULL,            -- nbt | paypronext | taxpronext
  platform        text NOT NULL CHECK (platform IN ('linkedin','facebook','instagram')),
  post            text NOT NULL,            -- exactly what goes out
  image_public_id text,                     -- Cloudinary; null = text only
  image_alt       text,
  scheduled_at    timestamptz NOT NULL,     -- UTC
  status          text NOT NULL DEFAULT 'scheduled' CHECK (status IN
                  ('scheduled','publishing','published','failed','needs_review','cancelled')),
  version         int  NOT NULL DEFAULT 1,  -- +1 on reschedule; old messages are ignored
  attempts        int  NOT NULL DEFAULT 0,
  enqueued_at     timestamptz,              -- null = not yet handed to the queue
  external_url    text,
  error           text,
  published_at    timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (platform <> 'instagram' OR image_public_id IS NOT NULL)
);
CREATE INDEX publications_due ON publications (scheduled_at) WHERE status = 'scheduled';
```

Status flow: `scheduled → publishing → published | failed | needs_review`,
and `scheduled → cancelled`. Edit, reschedule and cancel are allowed only while
the row is `scheduled`.

No tokens in the DB; they stay in env.

## API

**`POST /schedule`**: one brand per call, the same pattern as `/publish`. The
image is named by `draft_id`, never sent as bytes.
```json
{ "brand": "nbt", "scheduled_at": "2026-10-07T09:00:00+05:00",
  "draft_id": "...", "image_alt": "...",
  "targets": [ { "platform": "linkedin", "post": "..." },
               { "platform": "instagram", "post": "..." } ] }
```
→ `{ "items": [ { "id": "...", "platform": "linkedin", "status": "scheduled", "scheduled_at": "..." } ] }`

This call rejects a time in the past and any platform the brand isn't
configured for (`brand.missing()`), copies the brand's draft image once (an
expired draft is a 410), and inserts all rows in one transaction.

| Endpoint | Does |
| --- | --- |
| `GET /schedule?status=` | Lists rows: `id, brand, platform, scheduled_at, status, external_url, error` |
| `PATCH /schedule/{id}` | `{ scheduled_at?, post? }`. A new time bumps `version` and clears `enqueued_at`. |
| `DELETE /schedule/{id}` | Sets `cancelled` |
| `POST /schedule/{id}/resolve` | `{ published: bool, external_url? }` for a `needs_review` row, after a human checks the feed |
| `GET /cron/dispatch` | Vercel Cron only (`Authorization: Bearer $CRON_SECRET`) |

## How a post goes out

1. **Enqueue.** If `scheduled_at` is within 24h, `/schedule` (or `PATCH`) sends
   right away: `send("publish", {id, version}, delay=seconds_until)`, with the
   idempotency key `"{id}:{version}"`, and sets `enqueued_at`. Later posts are
   picked up by the daily cron.
2. **Claim** (the only way to publish):
   ```sql
   UPDATE publications SET status='publishing', attempts=attempts+1
   WHERE id=:id AND version=:version AND status='scheduled' RETURNING *;
   ```
   If no row comes back, the post was cancelled, rescheduled, already done or
   is a duplicate delivery, so the consumer stops.
3. **Publish** through `publishing.publish_target()`, the same function
   `/publish` uses. Credentials are checked again at this point, because tokens
   can expire after scheduling.
4. **Record** the result:

   | Outcome | Row |
   | --- | --- |
   | Success | `published`, `external_url`, `published_at` |
   | Connection failed before sending, 429, 5xx | Back to `scheduled`, re-send with backoff (2, 4, 8 min); `failed` after 3 attempts |
   | 4xx (token, permission, bad media) | `failed` + `error` |
   | Timeout after sending, or a crash mid-publish | `needs_review`. **Never auto-retried.** A human checks the feed. |

   The consumer catches its own errors. If it raised, the queue would redeliver
   the message, and that could post twice.

**Daily cron** (`/cron/dispatch`), with three jobs:
- Enqueue `scheduled` rows that are due within 26h and have `enqueued_at IS NULL`.
- Re-send `scheduled` rows that are 15 min or more past their time with
  `enqueued_at` set, because their message was lost. These go out late, up to a
  day.
- Move `publishing` rows older than 15 min to `needs_review`, and delete
  Cloudinary images whose rows have all finished more than 7 days ago.
- Sweep `social/drafts/` of drafts older than `DRAFT_TTL_HOURS` (24). This one
  needs only `CLOUDINARY_URL`, so it runs even with scheduling off.

## Cloudinary

- `/image` stores each brand's branded copy as a draft,
  `social/drafts/<draft_id>/<brand>`. Drafts are deleted when replaced, on New
  post, or by the daily cron after `DRAFT_TTL_HOURS` (24).
- At schedule time the brand's draft is copied to `social/<brand>/<uuid>`
  (downloaded and re-uploaded, so byte-identical) and only that `public_id` is
  stored. The draft sweep never touches it. Config is `CLOUDINARY_URL`.
- LinkedIn and Facebook: download the **original** PNG and pass the bytes to the
  existing clients. Never use `f_auto`/`q_auto`, because re-encoding breaks the
  "logo as-is" rule.
- Instagram: a derived URL, a 1.91:1 crop as JPEG, sent as `image_url`. This
  also replaces `media.py` + `PUBLIC_BASE_URL`, because in-memory staging does
  not work on serverless (Meta's fetch reaches a different instance). Publish-now
  to Instagram uses the draft's URL directly.

## Vercel gotchas

- **Queues is Beta.** Confirm it can be enabled on the project before building
  on it, and check the exact Python SDK names (`send`, `@subscribe`,
  `[[tool.vercel.subscribers]]`) against the current docs.
- **Messages belong to the deployment that sent them.** That deployment's code
  and env vars process them, so a token rotated in env does not reach messages
  already queued. Don't delete old deployments that have pending messages.
  The 24h enqueue window limits this.
- **maxDuration:** an Instagram publish can take ~60s. Set `maxDuration` for the
  consumer and `/publish`, and confirm what the plan allows.
- **Hobby cron** runs once a day and only roughly on time (within the hour),
  which is why the window is 26h, not 24h.
- **Body limit 4.5 MB:** check the size of the `/image` response (three
  base64 PNGs). If it is too large, return JPEG or compress.
- **Bundling:** make sure `assets/` (the logos) and `app/prompts/` are included
  in the deployment.
- **Auth:** the API has none. On a public Vercel URL, anyone could schedule or
  publish for every brand. Add auth (at least Vercel Deployment Protection or an
  API key header) before going live.

## Neon

- Use the pooled URL (`-pooler` host) at runtime, with `sslmode=require`. With
  psycopg 3 behind the pooler, set `prepare_threshold=None`.
- Open one short connection per request; serverless has no long-lived pool.
- Migrations: one SQL file, `migrations/001_publications.sql`, run once with
  the direct URL. No Alembic needed.
- Use a Neon `dev` branch for `.env.test`.

## Fallback: if Queues can't be enabled

Keep everything above and change only the trigger. cron-job.org calls
`/cron/dispatch` every 5 min (with the `CRON_SECRET` header), and the cron
publishes rows that are due, using the same claim. Posts go out up to 5 min
late, and the per-request timeout becomes the limit.

## Fit into the repo

**New:** `app/db.py` (psycopg, raw SQL), `app/storage.py` (Cloudinary),
`app/publishing.py` (`publish_target`), `app/scheduler.py` (enqueue + claim +
run), `app/queue_consumer.py` (`@subscribe`), `app/routes/schedule.py`,
`app/routes/cron.py`, `app/schema/schedule.py`, `migrations/001_publications.sql`,
`vercel.json` (cron + maxDuration).

**Changed:** `routes/publish.py` → calls `publishing.py`.
`meta.publish_instagram` → takes an `image_url`; `media.py` and the
`/media` route are removed. `config.py` gains `DATABASE_URL`, `CLOUDINARY_URL`
and `CRON_SECRET`. The frontend gains a Publish now / Schedule toggle, a
date-time picker and a Scheduled list with Cancel.

**Unchanged:** `/generate`, `/image`, the agent, image generation, branding.

## CLAUDE.md updates (when built)

- Remove "no scheduling, no database".
- Never auto-publish → *a scheduled post is an approval with a time attached;
  the consumer publishes only the stored approved copy and never calls the agent
  or the image model.*
- *Unknown outcomes go to `needs_review` and are never auto-retried.*
- *No credentials in the DB.*
- *One scheduler for all platforms; Meta native scheduling is not used.*
