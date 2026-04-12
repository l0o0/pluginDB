from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    root: Path
    docs_dir: Path
    data_dir: Path
    json_dir: Path
    xpi_dir: Path
    db_path: Path
    cache_dir: Path
    logs_dir: Path
    lock_path: Path

    @classmethod
    def from_root(cls, root: Path) -> "AppPaths":
        data_dir = root / "data"
        return cls(
            root=root,
            docs_dir=root / "docs",
            data_dir=data_dir,
            json_dir=data_dir / "json",
            xpi_dir=data_dir / "xpi",
            db_path=data_dir / "db" / "plugins.sqlite3",
            cache_dir=data_dir / "cache",
            logs_dir=root / "logs",
            lock_path=data_dir / ".sync.lock",
        )

    def ensure_directories(self) -> None:
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.json_dir.mkdir(parents=True, exist_ok=True)
        self.xpi_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def default_database_url(self) -> str:
        return f"sqlite+pysqlite:///{self.db_path}"
