"""Command line: run the server, manage agent tokens, import files, back up."""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from . import auth, jobs
from .config import DEFAULT_PORT, from_env
from .db import log_change


def _conn():
    from .app import bootstrap
    return bootstrap(from_env())


def cmd_serve(args) -> None:
    import uvicorn

    from .app import create_app
    uvicorn.run(create_app(), host=args.host, port=args.port, proxy_headers=True,
                log_level="info", access_log=False)


def cmd_token(args) -> None:
    conn = _conn()
    if args.action == "create":
        token = auth.create(conn, args.name, args.scope)
        log_change(conn, "cli", "create", "token", args.name, after={"scope": args.scope})
        print(token)
        print(f"# token for {args.name!r} (scope {args.scope}); it is shown only once",
              file=sys.stderr)
    elif args.action == "revoke":
        if not auth.revoke(conn, args.name):
            sys.exit(f"no active token named {args.name!r}")
        log_change(conn, "cli", "revoke", "token", args.name)
        print(f"revoked {args.name}")
    else:
        for t in auth.listing(conn):
            state = "revoked" if t["revoked_at"] else "active"
            print(f"{t['name']:<24} {t['scope']:<6} {state:<8} last used {t['last_used_at']}")


def cmd_import(args) -> None:
    settings = from_env()
    conn = _conn()
    src = Path(args.path).expanduser().resolve()
    if not src.is_file():
        sys.exit(f"{src} is not a file")
    if args.kind == "apple-health":
        from .importers import apple_health
        path = src
        if not args.no_copy:
            dest = settings.raw_dir / "apple-health"
            dest.mkdir(parents=True, exist_ok=True)
            path = dest / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{src.name}"
            shutil.copy2(src, path)
            print(f"original kept at {path}", file=sys.stderr)

        def progress(stats):
            print(f"\r{stats['samples_read']:>10} samples read, {stats['samples_added']:>10} new,"
                  f" {stats['workouts']} workouts", end="", file=sys.stderr, flush=True)
        stats = apple_health.run(conn, path, "cli", settings.files_dir, progress)
        print(file=sys.stderr)
    elif args.kind == "health-auto-export-csv":
        from .importers import hae_csv
        stats = hae_csv.run(conn, src, "cli")
    else:
        from .importers import health_auto_export
        stats = health_auto_export.run(conn, json.loads(src.read_text()), "cli",
                                       settings.files_dir)
    job_id = jobs.record(conn, args.kind.replace("-", "_"), str(src), "cli", "done", stats)
    log_change(conn, "cli", "import", "import", job_id, after=stats)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def cmd_backup(args) -> None:
    """Consistent online copy of the database (attachments and raw files are plain files)."""
    settings = from_env()
    conn = _conn()
    dest = settings.backups_dir / f"lifeos-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.db"
    target = sqlite3.connect(dest)
    with target:
        conn.backup(target)
    target.close()
    dest.chmod(0o600)
    old = sorted(settings.backups_dir.glob("lifeos-*.db"))[:-args.keep]
    for f in old:
        f.unlink()
    print(f"{dest} ({dest.stat().st_size / 1e6:.1f} MB); removed {len(old)} old backups")


def cmd_status(_args) -> None:
    settings = from_env()
    conn = _conn()
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    print(f"data      {settings.home}")
    print(f"records   {q('SELECT COUNT(*) FROM records WHERE deleted_at IS NULL')}")
    for r in conn.execute("SELECT category, COUNT(*) n FROM records WHERE deleted_at IS NULL"
                          " GROUP BY category ORDER BY n DESC"):
        print(f"  {r['category']:<18} {r['n']}")
    print(f"samples   {q('SELECT COUNT(*) FROM samples')}")
    print(f"foods     {q('SELECT COUNT(*) FROM foods WHERE deleted_at IS NULL')}")
    print(f"tokens    {q('SELECT COUNT(*) FROM tokens WHERE revoked_at IS NULL')} active")
    for j in jobs.listing(conn, 3):
        print(f"import    {j['created_at']} {j['kind']} {j['status']}")


def cmd_api(args) -> None:
    """Call the running API as an agent: the token comes from a file, never the command line,
    so it stays out of shell history and agent transcripts."""
    base = os.environ.get("LIFEOS_URL", f"http://127.0.0.1:{DEFAULT_PORT}/api/v1").rstrip("/")
    token = os.environ.get("LIFEOS_TOKEN")
    if not token:
        token_file = Path(os.environ.get("LIFEOS_TOKEN_FILE", "/etc/lifeos/agent.token"))
        try:
            token = token_file.read_text().strip()
        except OSError as e:
            sys.exit(f"no token: set LIFEOS_TOKEN or LIFEOS_TOKEN_FILE ({e})")
    path = "/" + args.path.lstrip("/").removeprefix("api/v1/")
    body = args.body
    if body == "-" or (body is None and args.method in ("POST", "PATCH") and not sys.stdin.isatty()):
        body = sys.stdin.read()
    data = None
    if body:
        try:
            data = json.dumps(json.loads(body), ensure_ascii=False).encode()
        except ValueError as e:
            sys.exit(f"body is not valid JSON: {e}")
    req = urllib.request.Request(base + path, data=data, method=args.method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach {base}: {e.reason}")
    try:
        print(json.dumps(json.loads(raw), ensure_ascii=False, indent=1))
    except ValueError:
        print(raw.decode(errors="replace"))
    if status >= 400:
        sys.exit(1)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="lifeos", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the HTTP API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=DEFAULT_PORT)
    s.set_defaults(func=cmd_serve)

    t = sub.add_parser("token", help="manage agent tokens")
    t.add_argument("action", choices=["create", "list", "revoke"])
    t.add_argument("name", nargs="?")
    t.add_argument("--scope", default="write", choices=auth.SCOPES)
    t.set_defaults(func=cmd_token)

    i = sub.add_parser("import", help="import a file from this machine")
    i.add_argument("kind", choices=["apple-health", "health-auto-export", "health-auto-export-csv"],
                   help="health-auto-export-csv reads the medication log from the app's CSV zip")
    i.add_argument("path")
    i.add_argument("--no-copy", action="store_true",
                   help="do not keep a copy of the original under the data directory")
    i.set_defaults(func=cmd_import)

    b = sub.add_parser("backup", help="snapshot the database into the backups directory")
    b.add_argument("--keep", type=int, default=30)
    b.set_defaults(func=cmd_backup)

    st = sub.add_parser("status", help="what is stored")
    st.set_defaults(func=cmd_status)

    a = sub.add_parser("api", help="call the API, e.g. `lifeos api GET context`")
    a.add_argument("method", type=str.upper, choices=["GET", "POST", "PATCH", "DELETE"])
    a.add_argument("path", help="path under the API root ($LIFEOS_URL), with query string")
    a.add_argument("body", nargs="?", help="JSON body, or - to read stdin")
    a.set_defaults(func=cmd_api)

    args = ap.parse_args(argv)
    if args.cmd == "token" and args.action != "list" and not args.name:
        ap.error("token create/revoke needs a name")
    try:
        args.func(args)
    except ValueError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
