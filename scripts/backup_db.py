"""Write a consistent snapshot of the database, safe to take while the app runs.

Copying advisor.db with `cp` is not safe. The database runs in write-ahead-log
mode, where recently committed writes sit in advisor.db-wal until they are
folded back into the main file, so a plain copy can miss them - or catch the
file halfway through being written. SQLite's online backup API copies a
committed, consistent state instead, and the snapshot is integrity-checked
before this script reports success.

It opens the database directly rather than through policy_advisor.db.connect,
so taking a backup can never apply a migration or otherwise change the file
being backed up.

The snapshot holds client chats and uploaded document text: encrypt it before
it leaves the machine (docs/16 uses an rclone crypt remote).

Usage:
  uv run python -m scripts.backup_db --out data/backups
"""

import argparse
import sqlite3
import time
from pathlib import Path


def backup(source: Path, out_dir: Path) -> Path:
    if not source.exists():
        raise FileNotFoundError(f"No database at {source} - nothing to back up.")
    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / f"advisor-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.db"

    # Read-only URI: the backup must never write to the live database.
    src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        verdict = dst.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dst.close()
        src.close()

    if verdict != "ok":
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"Snapshot failed its integrity check ({verdict}); not keeping it.")
    return destination


def main() -> None:
    from policy_advisor.db import database_path

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("data/backups"))
    args = parser.parse_args()

    print(backup(database_path(), args.out))


if __name__ == "__main__":
    main()
