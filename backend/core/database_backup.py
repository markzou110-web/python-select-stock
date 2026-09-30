"""PostgreSQL backup service shared by CLI and scheduled task."""
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from core.db import get_db_engine


def _backup_dir(output_dir: str | None = None) -> Path:
    return Path(output_dir).resolve() if output_dir else Path(__file__).resolve().parents[1] / "backups"


def today_backup_exists(output_dir: str | None = None) -> bool:
    """今天是否已有备份文件（按文件名前缀 stock_db_YYYYMMDD_ 判断）。"""
    prefix = f"stock_db_{datetime.now():%Y%m%d}_"
    try:
        return any(_backup_dir(output_dir).glob(f"{prefix}*.dump"))
    except OSError:
        return False


def create_database_backup(output_dir: str | None = None, retention: int = 14) -> Dict[str, Any]:
    target_dir = _backup_dir(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    output = target_dir / f"stock_db_{datetime.now():%Y%m%d_%H%M%S}.dump"
    engine = get_db_engine()
    if engine is None or engine.dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL engine is required for pg_dump backup")
    url = engine.url
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = str(url.password)
    command = [
        "pg_dump", "--format=custom", "--no-owner", "--no-acl",
        "--host", str(url.host or "localhost"), "--port", str(url.port or 5432),
        "--username", str(url.username or ""), "--file", str(output), str(url.database or ""),
    ]
    try:
        subprocess.run(command, env=env, check=True, timeout=1800)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    backups = sorted(target_dir.glob("*.dump"), key=lambda path: path.stat().st_mtime, reverse=True)
    for expired in backups[max(1, int(retention)):]:
        expired.unlink(missing_ok=True)
    # 第二副本（可选）：BACKUP_SECONDARY_DIR 指向另一块盘/目录，best-effort 复制，
    # 失败只告警不失败——主备份已完成，勿因副本目录不可写丢掉主备份结果。
    secondary = os.getenv("BACKUP_SECONDARY_DIR", "").strip()
    secondary_copied = False
    if secondary:
        try:
            secondary_path = Path(secondary)
            secondary_path.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output, secondary_path / output.name)
            secondary_copied = True
        except Exception as exc:
            from core.logging_config import logger

            logger.warning(f"Secondary backup copy failed (non-blocking): {exc}")
    return {
        "path": str(output),
        "size": output.stat().st_size,
        "retained": min(len(backups), max(1, int(retention))),
        "secondary_copied": secondary_copied,
    }
