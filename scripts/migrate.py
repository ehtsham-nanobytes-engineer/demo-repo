"""Apply the SQL files in migrations/ to Neon, in order. Run once per database.

Neon recommends its DIRECT url (no "-pooler" in the host) for schema changes,
so that is used when DIRECT_DATABASE_URL is set; otherwise DATABASE_URL.

    $env:DIRECT_DATABASE_URL="postgresql://..."     # PowerShell
    .venv/Scripts/python.exe scripts/migrate.py

There is no migration table: each file is plain DDL, and a file whose table
already exists is reported and skipped. The URL is never printed.
"""

import os
import sys
from pathlib import Path

import psycopg
from psycopg import errors

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402


def main() -> int:
    url = os.environ.get("DIRECT_DATABASE_URL") or get_settings().database_url
    if not url:
        print("Set DIRECT_DATABASE_URL (or DATABASE_URL) first.")
        return 1

    files = sorted((ROOT / "migrations").glob("*.sql"))
    with psycopg.connect(url, sslmode="require", autocommit=True) as conn:
        for path in files:
            try:
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
            except errors.DuplicateTable:
                print(f"  {path.name}: already applied")
                continue
            print(f"  {path.name}: applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
