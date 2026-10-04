"""Validate migration on a consistent COPY of the old SQLite database, never the original."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from niulai_player.library import Library


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path(os.environ["LOCALAPPDATA"]) / "NiuLaiPlayerPython" / "library.sqlite3")
    args = parser.parse_args()
    if not args.source.is_file():
        raise FileNotFoundError(args.source)
    root = Path(__file__).resolve().parents[1]
    target = root / "artifacts" / "upgrade-verification" / uuid.uuid4().hex[:8]
    target.mkdir(parents=True)
    with sqlite3.connect(args.source.absolute().as_uri() + "?mode=ro", uri=True) as source:
        columns = [row[1] for row in source.execute("PRAGMA table_info(items)")]
        projection = ",".join('"' + column.replace('"', '""') + '"' for column in columns)
        settings = list(source.execute("SELECT key,value FROM settings ORDER BY key"))
        items = list(source.execute(f"SELECT {projection} FROM items ORDER BY path"))
        old_version = source.execute("PRAGMA user_version").fetchone()[0]
        with sqlite3.connect(target / "library.sqlite3") as copy:
            source.backup(copy)
    migrated = Library(target)
    try:
        assert list(map(tuple, migrated._db.execute("SELECT key,value FROM settings ORDER BY key"))) == settings
        assert list(map(tuple, migrated._db.execute(f"SELECT {projection} FROM items ORDER BY path"))) == items
        assert migrated._db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert migrated._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        migrated.close()
    report = target / "results.json"
    report.write_text(json.dumps({"source_opened_readonly": True, "source_modified": False,
                                  "copied_old_schema": old_version, "new_schema": 3,
                                  "preserved_settings": len(settings), "preserved_items": len(items),
                                  "backup_created": bool(list((target / "backups").glob("*.sqlite3")))}, indent=2), encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
