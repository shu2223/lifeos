"""SQLite storage: a single file in WAL mode, migrated by PRAGMA user_version."""
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
  name        TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  schema      TEXT NOT NULL DEFAULT '{"type": "object"}',
  example     TEXT,
  builtin     INTEGER NOT NULL DEFAULT 0,
  created_by  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS records (
  id          TEXT PRIMARY KEY,
  category    TEXT NOT NULL REFERENCES categories(name),
  start_at    TEXT NOT NULL,
  end_at      TEXT,
  start_ts    REAL NOT NULL,
  end_ts      REAL,
  local_date  TEXT NOT NULL,
  title       TEXT NOT NULL DEFAULT '',
  data        TEXT NOT NULL DEFAULT '{}',
  tags        TEXT NOT NULL DEFAULT '[]',
  note        TEXT NOT NULL DEFAULT '',
  source      TEXT NOT NULL,
  external_id TEXT,
  created_by  TEXT NOT NULL,
  updated_by  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS records_by_time ON records(start_ts);
CREATE INDEX IF NOT EXISTS records_by_category ON records(category, start_ts);
CREATE INDEX IF NOT EXISTS records_by_date ON records(local_date);
CREATE UNIQUE INDEX IF NOT EXISTS records_external
  ON records(source, category, external_id) WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS metrics (
  name        TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  unit        TEXT NOT NULL,
  aggregation TEXT NOT NULL CHECK (aggregation IN ('sum', 'avg', 'last')),
  date_by     TEXT NOT NULL DEFAULT 'start' CHECK (date_by IN ('start', 'end')),
  description TEXT NOT NULL DEFAULT '',
  builtin     INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS samples (
  id          INTEGER PRIMARY KEY,
  metric      TEXT NOT NULL REFERENCES metrics(name),
  start_at    TEXT NOT NULL,
  end_at      TEXT NOT NULL,
  start_ts    REAL NOT NULL,
  end_ts      REAL NOT NULL,
  local_date  TEXT NOT NULL,
  value       REAL NOT NULL,
  unit        TEXT NOT NULL,
  source      TEXT NOT NULL,
  meta        TEXT,
  created_by  TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS samples_dedup
  ON samples(metric, source, start_ts, end_ts, value);
CREATE INDEX IF NOT EXISTS samples_by_time ON samples(metric, start_ts);
CREATE INDEX IF NOT EXISTS samples_by_date ON samples(metric, local_date);

CREATE TABLE IF NOT EXISTS foods (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  aliases      TEXT NOT NULL DEFAULT '[]',
  brand        TEXT NOT NULL DEFAULT '',
  serving      TEXT,
  per_serving  TEXT,
  per_100g     TEXT,
  note         TEXT NOT NULL DEFAULT '',
  source       TEXT NOT NULL DEFAULT 'estimate',
  use_count    INTEGER NOT NULL DEFAULT 0,
  last_used_at TEXT,
  created_by   TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  deleted_at   TEXT
);
CREATE INDEX IF NOT EXISTS foods_by_name ON foods(name);

CREATE TABLE IF NOT EXISTS profile (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_by TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
  id         TEXT PRIMARY KEY,
  record_id  TEXT NOT NULL REFERENCES records(id),
  filename   TEXT NOT NULL,
  mime       TEXT NOT NULL,
  size       INTEGER NOT NULL,
  sha256     TEXT NOT NULL,
  path       TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS attachments_by_record ON attachments(record_id);

CREATE TABLE IF NOT EXISTS tokens (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL UNIQUE,
  token_hash   TEXT NOT NULL UNIQUE,
  scope        TEXT NOT NULL CHECK (scope IN ('read', 'write', 'admin')),
  created_at   TEXT NOT NULL,
  last_used_at TEXT,
  revoked_at   TEXT
);

CREATE TABLE IF NOT EXISTS changes (
  id        INTEGER PRIMARY KEY,
  at        TEXT NOT NULL,
  actor     TEXT NOT NULL,
  action    TEXT NOT NULL,
  entity    TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  before    TEXT,
  after     TEXT
);
CREATE INDEX IF NOT EXISTS changes_by_entity ON changes(entity, entity_id);

CREATE TABLE IF NOT EXISTS imports (
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,
  status      TEXT NOT NULL,
  file        TEXT,
  stats       TEXT NOT NULL DEFAULT '{}',
  error       TEXT,
  created_by  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  finished_at TEXT
);
"""
SCHEMA_VERSION = 1

_B32 = "0123456789abcdefghjkmnpqrstvwxyz"


def new_id(prefix: str) -> str:
    """Time-sortable id (ULID layout, lowercase Crockford base32): rec_01jbx...."""
    n = (time.time_ns() // 1_000_000) << 80 | secrets.randbits(80)
    return prefix + "_" + "".join(_B32[(n >> s) & 31] for s in range(125, -1, -5))


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection):
    """Write transaction. IMMEDIATE takes the write lock up front, so concurrent writers wait
    on busy_timeout instead of failing when a read snapshot turns into a write."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(text: str | None):
    return None if text is None else json.loads(text)


def log_change(conn, actor: str, action: str, entity: str, entity_id: str,
               before=None, after=None, at: str | None = None) -> None:
    from .timeutil import utc_now
    conn.execute(
        "INSERT INTO changes(at, actor, action, entity, entity_id, before, after)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (at or utc_now(), actor, action, entity, entity_id,
         None if before is None else dumps(before), None if after is None else dumps(after)),
    )
