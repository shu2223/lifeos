"""Bearer tokens. Each agent gets its own named token, so every write is attributed.

Scopes are ordered: read < write < admin. Only a hash of each token is stored.
"""
import hashlib
import secrets
from dataclasses import dataclass

from .db import new_id, tx
from .timeutil import utc_now

SCOPES = ("read", "write", "admin")
PREFIX = "lifeos_"


@dataclass(frozen=True)
class Actor:
    name: str
    scope: str

    def can(self, scope: str) -> bool:
        return SCOPES.index(self.scope) >= SCOPES.index(scope)


SYSTEM = Actor("system", "admin")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(conn, name: str, scope: str) -> str:
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}")
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise ValueError("name may contain letters, digits, - and _")
    token = PREFIX + secrets.token_urlsafe(32)
    with tx(conn):
        if conn.execute("SELECT 1 FROM tokens WHERE name = ? AND revoked_at IS NULL",
                        (name,)).fetchone():
            raise ValueError(f"an active token named {name!r} exists; revoke it first")
        conn.execute("DELETE FROM tokens WHERE name = ?", (name,))
        conn.execute(
            "INSERT INTO tokens(id, name, token_hash, scope, created_at) VALUES (?, ?, ?, ?, ?)",
            (new_id("tok"), name, _hash(token), scope, utc_now()),
        )
    return token


def revoke(conn, name: str) -> bool:
    with tx(conn):
        cur = conn.execute("UPDATE tokens SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL",
                           (utc_now(), name))
    return cur.rowcount > 0


def verify(conn, token: str) -> Actor | None:
    if not token.startswith(PREFIX):
        return None
    row = conn.execute(
        "SELECT id, name, scope, last_used_at FROM tokens"
        " WHERE token_hash = ? AND revoked_at IS NULL", (_hash(token),)
    ).fetchone()
    if row is None:
        return None
    now = utc_now()
    if (row["last_used_at"] or "")[:16] != now[:16]:  # at most one write per minute per token
        conn.execute("UPDATE tokens SET last_used_at = ? WHERE id = ?", (now, row["id"]))
    return Actor(row["name"], row["scope"])


def listing(conn) -> list[dict]:
    rows = conn.execute("SELECT name, scope, created_at, last_used_at, revoked_at FROM tokens"
                        " ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]
