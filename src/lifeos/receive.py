"""`lifeos receive`: take an export from the phone over LocalSend and import it.

Runs the LocalSend CLI (the `LocalSend-CLI-*-linux-*.tar.gz` asset of a LocalSend release) on a
pseudo-terminal, because it asks for a key press on every incoming request. Each request is
shown with its sender and files and accepted only after confirmation: typed by the person at
the terminal, or, when nobody is, only from the device named by --from. Arrived files are moved
under the data directory's raw/ folder and imported by what they contain.
"""
import os
import pty
import re
import select
import shutil
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[@-_]")
SENDER = re.compile(r"^R (?P<sender>.+?)\s*$")
FILES = re.compile(r"^\s*Files \((?P<count>\d+), (?P<size>[^)]*)\):\s*$")
FILE = re.compile(r"^\s{3,}(?P<name>.+) \((?P<size>[^()]*)\)\s*$")
RECEIVED = re.compile(r"Received (?P<count>\d+) files?")
ACCEPTABLE = (".zip", ".xml", ".json", ".csv")


@dataclass
class Request:
    sender: str
    size: str = ""
    files: list[tuple[str, str]] = field(default_factory=list)


def parse_request(lines: list[str]) -> Request | None:
    """The request shown just before an "Accept?" prompt, from cleaned terminal lines."""
    for i in range(len(lines) - 1, -1, -1):
        m = SENDER.match(lines[i])
        if m and ":" not in m["sender"]:
            req = Request(m["sender"])
            for line in lines[i + 1:]:
                if fm := FILES.match(line):
                    req.size = fm["size"]
                elif fl := FILE.match(line):
                    req.files.append((fl["name"], fl["size"]))
            return req
    return None


def acceptable(req: Request, trusted: str | None) -> bool:
    """Unattended rule: the named device, and only export-like files."""
    return (trusted is not None and req.sender == trusted and bool(req.files)
            and all(name.lower().endswith(ACCEPTABLE) for name, _ in req.files))


def _ask(req: Request) -> bool:
    print(f"\n来自 {req.sender} 的 {len(req.files)} 个文件（{req.size}）：")
    for name, size in req.files:
        print(f"  {name}  {size}")
    try:
        with open("/dev/tty") as tty:
            print("接收？[y/N] ", end="", flush=True)
            return tty.readline().strip().lower() in ("y", "yes", "是")
    except OSError:
        return False


def receive(settings: Settings, *, alias: str, port: int, trusted: str | None, timeout: float,
            localsend: str, interactive: bool) -> list[Path]:
    """Run LocalSend until one request has been received (or `timeout`); return new files."""
    exe = shutil.which(localsend)
    if exe is None:
        raise SystemExit(f"{localsend} not found; install the LocalSend CLI from "
                         "https://github.com/localsend/localsend/releases")
    inbox = settings.home / "inbox"
    inbox.mkdir(parents=True, exist_ok=True, mode=0o700)
    before = set(inbox.iterdir())
    pid, fd = pty.fork()
    if pid == 0:
        os.execv(exe, [exe, "--alias", alias, "--port", str(port), "--destination", str(inbox)])
    lines: list[str] = []
    seen: list[str] = []  # everything LocalSend said, for the error report
    pending, done, listening = "", False, False
    deadline = time.monotonic() + timeout
    try:
        while not done and time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 1.0)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 65536).decode("utf-8", "replace")
            except OSError:
                break  # LocalSend exited
            *complete, pending = re.split(r"[\r\n]", pending + ANSI.sub("", chunk))
            for line in complete:
                if not line.strip():
                    continue
                lines = (lines + [line])[-40:]
                seen = (seen + [line])[-20:]
                if "Ready to accept requests" in line:
                    listening = True
                    print(f"等待手机发送：在 LocalSend 里选“{alias}”。", flush=True)
                elif line.startswith(" - http"):
                    print(f"  地址 {line.strip()[2:]}", flush=True)
                elif m := RECEIVED.search(line):
                    print(f"已收到 {m['count']} 个文件。", flush=True)
                    done = True
                elif "Declined" in line or "Cancelled" in line or "Canceled" in line:
                    print(line.strip(), flush=True)
            if "Accept?" in pending or any("Accept?" in x for x in complete):
                req = parse_request(lines)
                yes = req is not None and (_ask(req) if interactive else acceptable(req, trusted))
                if req and not interactive:
                    print(f"{'接收' if yes else '拒绝'}：{req.sender}，{len(req.files)} 个文件"
                          f"（{req.size}）", flush=True)
                os.write(fd, b"Y" if yes else b"N")
                pending, lines = "", []
    finally:
        try:
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        os.close(fd)
    if not listening:
        detail = "\n".join(x.strip() for x in seen[-5:] if x.strip())
        print(f"LocalSend 没能开始接收（端口 {port} 被占用？）：\n{detail}", file=sys.stderr)
    elif not done:
        print("没有收到文件。", file=sys.stderr)
    return sorted(p for p in inbox.iterdir() if p not in before and p.is_file())
