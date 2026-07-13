"""Create a PostgreSQL custom-format backup without printing credentials."""
import argparse
from pathlib import Path

from core.database_backup import create_database_backup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parents[1] / "backups"))
    args = parser.parse_args()
    result = create_database_backup(str(Path(args.output_dir).resolve()))
    print(f"backup_created={result['path']} size={result['size']} retained={result['retained']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
