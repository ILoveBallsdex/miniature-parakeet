"""SQLite schema and connection helpers for the standalone FutBot API."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def database_path() -> str:
    configured = os.getenv("FUTBOT_DB_PATH", "cards_game.db")
    path = Path(configured).expanduser()
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(database_path(), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


@contextmanager
def read_connection():
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def transaction():
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _add_columns(connection: sqlite3.Connection, table: str, definitions: dict[str, str]) -> None:
    present = _columns(connection, table)
    for name, definition in definitions.items():
        if name not in present:
            connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


def init_database() -> None:
    """Create missing FutBot tables and safely add columns to older databases."""
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS cards (
            card_id INTEGER PRIMARY KEY, player_id TEXT, name TEXT, attack INTEGER,
            defense INTEGER, speed INTEGER, height TEXT, club TEXT, position TEXT,
            overall INTEGER, image_path TEXT, card_rarity TEXT, card_type TEXT,
            league TEXT, nation TEXT
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS players (
            user_id INTEGER PRIMARY KEY, name TEXT, battles_played INTEGER DEFAULT 0,
            battles_won INTEGER DEFAULT 0, battles_lost INTEGER DEFAULT 0,
            has_claimed_starter_pack INTEGER DEFAULT 0, rounds_played INTEGER DEFAULT 0,
            rounds_won INTEGER DEFAULT 0, rounds_lost INTEGER DEFAULT 0
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS inventories (
            user_id INTEGER, card_id INTEGER, edition INTEGER,
            FOREIGN KEY(user_id) REFERENCES players(user_id),
            FOREIGN KEY(card_id) REFERENCES cards(card_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS decks (
            user_id INTEGER, deck_name TEXT, cards TEXT,
            FOREIGN KEY(user_id) REFERENCES players(user_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS achievements (
            achievement_id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS user_achievements (
            user_id INTEGER, achievement_id INTEGER,
            date_earned DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id, achievement_id),
            FOREIGN KEY(user_id) REFERENCES players(user_id),
            FOREIGN KEY(achievement_id) REFERENCES achievements(achievement_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS wishlists (
            user_id INTEGER, card_id INTEGER, PRIMARY KEY(user_id, card_id),
            FOREIGN KEY(user_id) REFERENCES players(user_id),
            FOREIGN KEY(card_id) REFERENCES cards(card_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS packs (
            user_id INTEGER PRIMARY KEY, rare_player_pack INTEGER DEFAULT 0,
            icon_pack INTEGER DEFAULT 0, hero_pack INTEGER DEFAULT 0,
            tester_pack INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES players(user_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS pending_daily_rewards (
            user_id INTEGER PRIMARY KEY, card_ids TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES players(user_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS battles (
            battle_id TEXT PRIMARY KEY, player1_id INTEGER NOT NULL,
            player2_id INTEGER NOT NULL, status TEXT NOT NULL,
            state_json TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS trade_offers (
            trade_id TEXT PRIMARY KEY, sender_id INTEGER NOT NULL,
            recipient_id INTEGER NOT NULL, sender_card_id INTEGER NOT NULL,
            recipient_card_id INTEGER NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS exchange_sessions (
            session_id TEXT PRIMARY KEY, player1_id INTEGER NOT NULL,
            player2_id INTEGER NOT NULL, p1_cards TEXT NOT NULL,
            p2_cards TEXT NOT NULL, p1_coins INTEGER NOT NULL DEFAULT 0,
            p2_coins INTEGER NOT NULL DEFAULT 0, p1_locked INTEGER NOT NULL DEFAULT 0,
            p2_locked INTEGER NOT NULL DEFAULT 0, p1_confirmed INTEGER NOT NULL DEFAULT 0,
            p2_confirmed INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS market_listings (
            listing_id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER,
            card_id INTEGER, edition INTEGER DEFAULT 1, trade_count INTEGER DEFAULT 0,
            price INTEGER, listed_at TEXT DEFAULT CURRENT_TIMESTAMP, expires_at TEXT,
            status TEXT DEFAULT 'active', resolved_at TEXT, buyer_id INTEGER
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS friendships (
            user_id INTEGER, friend_id INTEGER, added_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id, friend_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS friend_requests (
            request_id INTEGER PRIMARY KEY AUTOINCREMENT, requester_id INTEGER,
            recipient_id INTEGER, requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
            notified INTEGER DEFAULT 0, UNIQUE(requester_id, recipient_id)
        )""")

        _add_columns(db, "cards", {
            "total_battles_played": "INTEGER DEFAULT 0",
            "total_battles_won": "INTEGER DEFAULT 0",
            "total_rounds_played": "INTEGER DEFAULT 0",
            "total_rounds_won": "INTEGER DEFAULT 0",
            "wishlist_count": "INTEGER DEFAULT 0",
            "copies": "INTEGER DEFAULT 0",
        })
        _add_columns(db, "players", {
            "coins": "INTEGER DEFAULT 0", "cards_dropped": "INTEGER DEFAULT 0",
            "cards_sold": "INTEGER DEFAULT 0", "display_title": "TEXT DEFAULT NULL",
            "battles_drawn": "INTEGER DEFAULT 0", "rounds_drawn": "INTEGER DEFAULT 0",
            "daily_streak": "INTEGER DEFAULT 0",
            "last_daily_claim": "TEXT DEFAULT NULL", "itscominghome": "INTEGER DEFAULT 0",
            "jogabonito": "INTEGER DEFAULT 0", "pineappleonpizza": "INTEGER DEFAULT 0",
            "mannschaft": "INTEGER DEFAULT 0", "theflyingdutchmen": "INTEGER DEFAULT 0",
            "blues": "INTEGER DEFAULT 0",
        })
        _add_columns(db, "inventories", {
            "battles_played": "INTEGER DEFAULT 0", "battles_won": "INTEGER DEFAULT 0",
            "rounds_played": "INTEGER DEFAULT 0", "rounds_won": "INTEGER DEFAULT 0",
            "trade_count": "INTEGER DEFAULT 0",
        })
        _add_columns(db, "market_listings", {
            "status": "TEXT DEFAULT 'active'", "resolved_at": "TEXT", "buyer_id": "INTEGER",
        })
        db.execute("CREATE INDEX IF NOT EXISTS idx_inventory_user ON inventories(user_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_inventory_card ON inventories(card_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(name)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_market_status ON market_listings(status)")


def row_dict(row):
    return dict(row) if row is not None else None
