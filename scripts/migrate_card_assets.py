"""Copy card artwork into a clean asset folder and normalize API DB paths.

Example (run on the PC that has the old card images):
  python scripts/migrate_card_assets.py ^
    --source-db .\\cards_game.db ^
    --source-root . ^
    --target-db .\\futbot_api.db ^
    --target-assets .\\assets

The source database is read-only. The target database image_path values are
changed to safe filenames relative to FUTBOT_ASSET_DIR.
"""

import argparse
import shutil
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", required=True)
    parser.add_argument("--source-root", default=".")
    parser.add_argument("--target-db", required=True)
    parser.add_argument("--target-assets", required=True)
    args = parser.parse_args()

    source_db = Path(args.source_db).expanduser().resolve()
    target_db = Path(args.target_db).expanduser().resolve()
    source_root = Path(args.source_root).expanduser().resolve()
    target_assets = Path(args.target_assets).expanduser().resolve()

    if not source_db.is_file():
        raise SystemExit(f"Source database not found: {source_db}")
    if not target_db.is_file():
        raise SystemExit(f"Target API database not found: {target_db}")
    if source_db == target_db:
        raise SystemExit("Source and target databases must be different.")
    target_assets.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(str(source_db))
    target = sqlite3.connect(str(target_db))
    try:
        rows = source.execute("SELECT card_id, image_path FROM cards ORDER BY card_id").fetchall()
        target.execute("BEGIN IMMEDIATE")
        copied = 0
        missing = []
        for card_id, image_path in rows:
            if not image_path:
                missing.append((card_id, "(empty image_path)"))
                continue
            source_path = Path(image_path).expanduser()
            if not source_path.is_absolute():
                source_path = source_root / source_path
            source_path = source_path.resolve()
            if not source_path.is_file():
                missing.append((card_id, str(source_path)))
                continue

            suffix = source_path.suffix.lower()
            if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                missing.append((card_id, f"unsupported image type: {source_path}"))
                continue
            filename = f"card_{card_id}{suffix}"
            destination = target_assets / filename
            shutil.copy2(source_path, destination)
            target.execute("UPDATE cards SET image_path = ? WHERE card_id = ?", (filename, card_id))
            copied += 1

        target.commit()
        print(f"Copied and linked {copied} card images into {target_assets}")
        print(f"Missing or unsupported images: {len(missing)}")
        for card_id, path in missing:
            print(f"  Card #{card_id}: {path}")
        if missing:
            raise SystemExit(1)
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    main()
