"""Check whether image files referenced by a FutBot card catalog exist locally.

Example:
  python scripts/verify_card_assets.py --database ./cards_game.db --asset-root .
"""

import argparse
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--asset-root", default=".")
    args = parser.parse_args()

    db_path = Path(args.database).expanduser().resolve()
    root = Path(args.asset_root).expanduser().resolve()
    if not db_path.is_file():
        raise SystemExit(f"Database not found: {db_path}")

    db = sqlite3.connect(str(db_path))
    try:
        columns = {row[1] for row in db.execute("PRAGMA table_info(cards)")}
        if "image_path" not in columns:
            raise SystemExit("The cards table has no image_path column.")
        rows = db.execute("SELECT card_id, name, image_path FROM cards ORDER BY card_id").fetchall()
    finally:
        db.close()

    missing = []
    absolute = []
    for card_id, name, image_path in rows:
        if not image_path:
            missing.append((card_id, name, "(empty image_path)"))
            continue
        value = Path(image_path).expanduser()
        if value.is_absolute():
            absolute.append((card_id, name, str(value)))
            candidate = value
        else:
            candidate = root / value
        if not candidate.resolve().is_file():
            missing.append((card_id, name, str(candidate)))

    print(f"Cards checked: {len(rows)}")
    print(f"Missing images: {len(missing)}")
    print(f"Absolute image paths: {len(absolute)}")
    if missing:
        print("\nMissing files:")
        for card_id, name, path in missing:
            print(f"  #{card_id} {name}: {path}")
    if absolute:
        print("\nAbsolute paths need to be copied into the API asset directory and")
        print("the card image_path values normalized before Railway deployment.")
    if missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
