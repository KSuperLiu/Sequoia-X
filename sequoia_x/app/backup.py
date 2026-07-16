"""SQLite 一致性备份与保留策略。"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


def backup_sqlite(source: str, target_dir: str, prefix: str, retain: int) -> Path:
    directory = Path(target_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = directory / f"{prefix}_{stamp}.db"
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    backups = sorted(directory.glob(f"{prefix}_*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old in backups[retain:]:
        old.unlink(missing_ok=True)
    return target
