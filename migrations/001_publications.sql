-- Scheduled publishing (SCHEDULER.md). Run once against Neon's DIRECT url:
--   python scripts/migrate.py
-- One row per ticked brand x platform. No credentials are stored here.

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
