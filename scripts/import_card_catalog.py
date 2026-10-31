"""Copy only the card catalog from an existing FutBot SQLite DB.

Player accounts, inventories, coins, packs, decks, trades and match data are
intentionally not copied. The destination database is created/updated using
the API schema, making this suitable for the planned player reset.

Usage:
  python scripts/import_card_catalog.py --source ./cards_game.db --target ./futbot_api.db
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from futbot_api.database import init_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Path to the existing FutBot database")
    parser.add_argument("--target", required=True, help="Path to the new API database")
    args = parser.parse_args()

    source_path = Path(args.source).expanduser().resolve()
    target_path = Path(args.target).expanduser().resolve()
    if not source_path.is_file():
        raise SystemExit(f"Source database does not exist: {source_path}")
    if source_path == target_path:
        raise SystemExit("Source and target must be different files; refusing an in-place reset.")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["FUTBOT_DB_PATH"] = str(target_path)
    init_database()

    source = sqlite3.connect(str(source_path))
    source.row_factory = sqlite3.Row
    target = sqlite3.connect(str(target_path))
    try:
        exists = source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cards'").fetchone()
        if not exists:
            raise SystemExit("The source database does not contain a cards table.")
        rows = source.execute("SELECT * FROM cards ORDER BY card_id").fetchall()
        if not rows:
            raise SystemExit("The source cards table is empty; nothing was imported.")

        source_columns = {item[1] for item in source.execute("PRAGMA table_info(cards)")}
        target_columns = {item[1] for item in target.execute("PRAGMA table_info(cards)")}
        # Only immutable card catalog fields are imported. Ownership/copy counts,
        # wishlists, and global battle statistics start fresh for the new player base.
        catalog_columns = [
            "card_id", "player_id", "name", "attack", "defense", "speed",
            "shooting", "passing", "dribbling", "goalkeeping",
            "height", "club", "position", "overall", "image_path",
            "card_rarity", "card_type", "league", "nation",
        ]
        stat_columns = {"shooting", "passing", "dribbling", "goalkeeping"}
        columns = [
            column for column in catalog_columns
            if column in target_columns and (column in source_columns or column in stat_columns)
        ]
        if "card_id" not in columns or "name" not in columns:
            raise SystemExit("The source cards table is missing required catalog columns.")

        def imported_value(row, column):
            if column not in stat_columns:
                return row[column] if column in source_columns else None

            position = str(row["position"] or "").upper() if "position" in source_columns else ""
            defense = int(row["defense"] or 0) if "defense" in source_columns else 0
            attack = int(row["attack"] or 0) if "attack" in source_columns else 0
            speed = int(row["speed"] or 0) if "speed" in source_columns else 0

            if column == "shooting":
                return (row["shooting"] if "shooting" in source_columns and row["shooting"] else attack)
            if column == "passing":
                existing = row["passing"] if "passing" in source_columns else 0
                return existing or (round((attack + defense) / 2) if position != "GK" else 0)
            if column == "dribbling":
                return (row["dribbling"] if "dribbling" in source_columns and row["dribbling"] else speed)
            existing = row["goalkeeping"] if "goalkeeping" in source_columns else 0
            return existing or (defense if position == "GK" else 0)

        quoted = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        values = [[imported_value(row, column) for column in columns] for row in rows]

        target.execute("BEGIN IMMEDIATE")
        # The destination is the new game database. Clear any previous player
        # state there, but never modify the source database.
        reset_tables = [
            "pending_daily_rewards", "drop_sessions", "trade_offers",
            "exchange_sessions", "battles", "market_listings",
            "friend_requests", "friendships", "user_achievements",
            "wishlists", "decks", "packs", "inventories", "players",
        ]
        target_tables = {
            row[0] for row in target.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table in reset_tables:
            if table in target_tables:
                target.execute(f'DELETE FROM "{table}"')
        target.execute("DELETE FROM cards")
        target.executemany(f"INSERT INTO cards ({quoted}) VALUES ({placeholders})", values)

        # Achievement definitions are shared catalog data. Earned achievements
        # remain player progress and are intentionally not copied.
        achievement_exists = source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='achievements'"
        ).fetchone()
        achievement_count = 0
        if achievement_exists:
            source_achievements = source.execute(
                "SELECT achievement_id, title, description FROM achievements"
            ).fetchall()
            target.execute("DELETE FROM achievements")
            target.executemany(
                "INSERT INTO achievements (achievement_id, title, description) VALUES (?, ?, ?)",
                [tuple(row) for row in source_achievements],
            )
            achievement_count = len(source_achievements)

        target.commit()
        print(f"Imported {len(values)} card catalog entries into {target_path}")
        print(f"Imported {achievement_count} achievement definitions.")
        print("Player data and all per-card copy, wishlist, and battle counters were reset.")
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    main()
