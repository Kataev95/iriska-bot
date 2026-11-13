"""Consistent SQLite backups (including WAL), before migrations and periodically.

Backups live alongside the persistent DB, in <DB directory>/backups.
Keep an off-server copy too: a disk/mount loss also destroys local backups.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)


async def create_consistent_backup(path: str, *, label: str = "daily", keep: int = 14) -> Path | None:
    """SQLite online backup to a temporary file, integrity check, then atomic rename.

    Uses a *separate* source connection, so a running aiosqlite connection can
    keep processing messages during a periodic backup.
    """
    if path == ":memory:" or not Path(path).is_file():
        return None
    source = Path(path)
    folder = source.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = folder / f"{source.name}.{stamp}.{label}.sqlite3"
    temp = folder / f".{target.name}.partial"
    src = await aiosqlite.connect(path, timeout=30)
    try:
        dest = sqlite3.connect(str(temp))
        try:
            await src.backup(dest, pages=128, sleep=0.1)
            check = dest.execute("PRAGMA quick_check").fetchone()
            if not check or check[0] != "ok":
                raise RuntimeError(f"Backup integrity check failed: {check}")
        finally:
            dest.close()
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    finally:
        await src.close()
    # Retention is count-based and applies to startup + periodic backups.
    previous = sorted(folder.glob(f"{source.name}.*.sqlite3"))
    for old in previous[:-max(keep, 1)]:
        try:
            old.unlink()
        except OSError:
            logger.warning("Cannot remove old SQLite backup %s", old)
    logger.info("SQLite backup created: %s", target)
    return target


async def backup_keeper(db, hours: float = 24.0) -> None:
    """Daily online backups; never bring down the bot if one fails."""
    if db.path == ":memory:":
        return
    while True:
        await asyncio.sleep(max(hours, 1.0) * 3600)
        try:
            await create_consistent_backup(db.path)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to create scheduled SQLite backup")
