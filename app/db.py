"""Neon Postgres: one short connection per use, raw SQL.

Serverless has no long-lived process to hold a pool, so each request opens a
connection to Neon's pooler (the `-pooler` host in DATABASE_URL), does its
work in one transaction and closes it. Two settings matter behind the pooler:

- `sslmode=require` — Neon refuses plain connections. Forced unless the URL
  names its own sslmode (a local test database may say `disable`).
- `prepare_threshold=None` — PgBouncer in transaction mode cannot keep
  server-side prepared statements across transactions, so psycopg must not
  create any.

The only table is `publications` (migrations/001_publications.sql). No tokens
or other credentials are ever stored in it.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from app.config import get_settings

_CONNECT_TIMEOUT_SECONDS = 10


class DatabaseNotConfigured(RuntimeError):
    """DATABASE_URL is unset, so scheduling is switched off."""


def configured() -> bool:
    return bool(get_settings().database_url)


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """One transaction: committed when the block exits cleanly, else rolled back."""
    url = get_settings().database_url
    if not url:
        raise DatabaseNotConfigured("DATABASE_URL is not set, so scheduling is disabled.")

    options = {} if "sslmode" in conninfo_to_dict(url) else {"sslmode": "require"}
    with psycopg.connect(url, prepare_threshold=None, row_factory=dict_row,
                         connect_timeout=_CONNECT_TIMEOUT_SECONDS, **options) as conn:
        yield conn
