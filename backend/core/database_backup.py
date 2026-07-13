"""PostgreSQL backup service shared by CLI and scheduled task."""
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from core.db import get_db_engine


def create_database_backup(output_dir: str | None = None, retention: int = 14) -> Dict[str, Any]:
    target_dir = Path(output_dir).resolve() if output_dir else Path(__file__).resolve().parents[1] / "backups"
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
    return {"path": str(output), "size": output.stat().st_size, "retained": min(len(backups), max(1, int(retention)))}
