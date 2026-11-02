"""Where lifeos keeps its data. Everything lives under one directory on this machine."""
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOME = "/var/lib/lifeos"
DEFAULT_PORT = 8773


@dataclass(frozen=True)
class Settings:
    home: Path
    prefix: str = ""  # URL prefix when served behind a path, e.g. "/life"

    @property
    def db_path(self) -> Path:
        return self.home / "lifeos.db"

    @property
    def files_dir(self) -> Path:
        """Attachments: meal photos, workout routes."""
        return self.home / "files"

    @property
    def raw_dir(self) -> Path:
        """Original import payloads, kept so nothing is lost if a parser improves later."""
        return self.home / "raw"

    @property
    def backups_dir(self) -> Path:
        return self.home / "backups"

    def ensure_dirs(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        for d in (self.files_dir, self.raw_dir, self.backups_dir):
            d.mkdir(exist_ok=True)


def from_env() -> Settings:
    prefix = os.environ.get("LIFEOS_PREFIX", "").rstrip("/")
    if prefix and not prefix.startswith("/"):
        prefix = "/" + prefix
    return Settings(home=Path(os.environ.get("LIFEOS_HOME", DEFAULT_HOME)), prefix=prefix)
