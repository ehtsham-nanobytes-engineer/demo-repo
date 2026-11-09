"""Publish every scheduled post that is due now — the local stand-in for the queue.

Vercel Queues only exists on Vercel. Locally, /schedule saves the rows but
sends nothing (QUEUE_ENABLED is off outside Vercel), so run this once the
scheduled time has passed:

    .venv/Scripts/python.exe scripts/run_due.py            # PowerShell: same path
    ENV_FILE=.env.test .venv/Scripts/python.exe scripts/run_due.py

It goes through exactly the consumer's path, `scheduler.run(id, version)`:
claim, publish the stored copy, record the result. A row that is not due yet
is left alone, and running it twice never posts twice — the claim sees to that.

THIS PUBLISHES FOR REAL to whatever accounts the env points at.
"""

import sys
from pathlib import Path

# Run from the project root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, scheduler  # noqa: E402
from app.config import env_files  # noqa: E402
from app.logging_setup import configure_logging  # noqa: E402


def main() -> int:
    configure_logging()
    if not db.configured():
        print("DATABASE_URL is not set.")
        return 1
    print(f"env: {' + '.join(env_files())}")
    outcomes = scheduler.run_due()
    if not outcomes:
        print("Nothing is due.")
        return 0
    for outcome in outcomes:
        print(f"  {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
