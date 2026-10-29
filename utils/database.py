"""
Database utilities for FutBot
Handles database connection, table creation, migrations, and common DB operations.
"""
import sqlite3
import logging
import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Load configuration
def load_id_list(env_key):
    val = os.getenv(env_key)
    if not val: return []
    return [int(x.strip()) for x in val.split(',') if x.strip().isdigit()]

# Configuration variables (shared across cogs)
ADMIN_IDS = load_id_list('ADMIN_IDS')
DROP_CHANNEL_IDS = load_id_list('DROP_CHANNEL_IDS')
ALLOWED_CHANNELS = load_id_list('ALLOWED_CHANNELS')
SUGGESTION_CHANNEL_ID = int(os.getenv('SUGGESTION_CHANNEL_ID', '0'))

# Database path
DB_PATH = 'cards_game.db'

def get_connection():
    """Get a new database connection."""
    return sqlite3.connect(DB_PATH)

def init_tables():
    """Create all required tables if they don't exist."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS cards (
        card_id INTEGER PRIMARY KEY,
        player_id TEXT,
        name TEXT,
        attack INTEGER,
        defense INTEGER,
        speed INTEGER,
        height TEXT,
        club TEXT,
        position TEXT,
        overall INTEGER,
        image_path TEXT,
        card_rarity TEXT,
        card_type TEXT,
        league TEXT,
        nation TEXT
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS decks (
        user_id INTEGER,
        deck_name TEXT,
        cards TEXT,
        FOREIGN KEY(user_id) REFERENCES players(user_id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS players (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        battles_played INTEGER DEFAULT 0,
        battles_won INTEGER DEFAULT 0,
        battles_lost INTEGER DEFAULT 0,
        has_claimed_starter_pack BOOLEAN DEFAULT 0,
        rounds_played INTEGER DEFAULT 0,
        rounds_won INTEGER DEFAULT 0,
        rounds_lost INTEGER DEFAULT 0
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS inventories (
        user_id INTEGER,
        card_id INTEGER,
        edition INTEGER,
        FOREIGN KEY(user_id) REFERENCES players(user_id),
        FOREIGN KEY(card_id) REFERENCES cards(card_id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS achievements (
        achievement_id INTEGER,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        PRIMARY KEY(achievement_id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_achievements (
        user_id INTEGER,
        achievement_id INTEGER,
        date_earned DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id, achievement_id),
        FOREIGN KEY(user_id) REFERENCES players(user_id),
        FOREIGN KEY(achievement_id) REFERENCES achievements(achievement_id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS wishlists (
        user_id INTEGER,
        card_id INTEGER,
        PRIMARY KEY(user_id, card_id),
        FOREIGN KEY(user_id) REFERENCES players(user_id),
        FOREIGN KEY(card_id) REFERENCES cards(card_id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS packs (
        user_id INTEGER PRIMARY KEY,
        rare_player_pack INTEGER DEFAULT 0,
        icon_pack INTEGER DEFAULT 0,
        hero_pack INTEGER DEFAULT 0,
        tester_pack INTEGER DEFAULT 0,
        FOREIGN KEY(user_id) REFERENCES players(user_id)
    )
    ''')

    conn.commit()
    conn.close()

def migrate_db():
    """Run database migrations to add new columns."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Inventory stats columns
        cursor.execute("PRAGMA table_info(inventories)")
        inv_columns = [info[1] for info in cursor.fetchall()]
        
        inv_cols = ['battles_played', 'battles_won', 'rounds_played', 'rounds_won', 'trade_count']
        for col in inv_cols:
            if col not in inv_columns:
                print(f"Migrating DB: Adding {col} to inventories...")
                cursor.execute(f"ALTER TABLE inventories ADD COLUMN {col} INTEGER DEFAULT 0")

        # Card global stats columns
        cursor.execute("PRAGMA table_info(cards)")
        card_columns = [info[1] for info in cursor.fetchall()]
        
        card_cols = ['total_battles_played', 'total_battles_won', 'total_rounds_played', 'total_rounds_won', 'wishlist_count', 'copies']
        for col in card_cols:
            if col not in card_columns:
                print(f"Migrating DB: Adding {col} to cards...")
                cursor.execute(f"ALTER TABLE cards ADD COLUMN {col} INTEGER DEFAULT 0")

        # Player columns
        cursor.execute("PRAGMA table_info(players)")
        player_columns = [info[1] for info in cursor.fetchall()]
        
        player_cols = {
            'coins': 'INTEGER DEFAULT 0',
            'cards_dropped': 'INTEGER DEFAULT 0',
            'cards_sold': 'INTEGER DEFAULT 0',
            'display_title': 'TEXT DEFAULT NULL',
            'battles_drawn': 'INTEGER DEFAULT 0',
            'daily_streak': 'INTEGER DEFAULT 0',
            'last_daily_claim': 'TEXT DEFAULT NULL'
        }
        for col, col_type in player_cols.items():
            if col not in player_columns:
                print(f"Migrating DB: Adding {col} to players...")
                cursor.execute(f"ALTER TABLE players ADD COLUMN {col} {col_type}")

        # Secret command flags
        secret_cols = ['itscominghome', 'jogabonito', 'pineappleonpizza', 'mannschaft', 'theflyingdutchmen', 'blues']
        for col in secret_cols:
            if col not in player_columns:
                cursor.execute(f"ALTER TABLE players ADD COLUMN {col} INTEGER DEFAULT 0")

        conn.commit()
        conn.close()
        print("Database migration complete.")
    except Exception as e:
        print(f"Migration Error: {e}")

# --- Common Helper Functions ---

def ensure_player_exists(user_id, user_name):
    """Create player record if it doesn't exist."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM players WHERE user_id = ?', (user_id,))
    if cursor.fetchone() is None:
        cursor.execute('INSERT INTO players (user_id, name) VALUES (?, ?)', (user_id, user_name))
        conn.commit()
    conn.close()

def add_card_to_inventory(user_id, card_id):
    """Add a card to user's inventory. Raises ValueError if duplicate."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM inventories WHERE user_id = ? AND card_id = ?', (user_id, card_id))
    if cursor.fetchone() is not None:
        conn.close()
        raise ValueError("Card already in inventory")

    cursor.execute('SELECT copies FROM cards WHERE card_id = ?', (card_id,))
    result = cursor.fetchone()
    edition = result[0] if result else 1

    cursor.execute('INSERT INTO inventories (user_id, card_id, edition) VALUES (?, ?, ?)', (user_id, card_id, edition))
    conn.commit()
    conn.close()

def remove_card_from_inventory(user_id, card_id):
    """Remove a card from user's inventory."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM inventories WHERE user_id = ? AND card_id = ?', (user_id, card_id))
    conn.commit()
    conn.close()

def get_player_inventory(user_id):
    """Get all cards in a user's inventory."""
    from utils.models import Card
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
    SELECT cards.*, inventories.edition FROM cards
    JOIN inventories ON cards.card_id = inventories.card_id
    WHERE inventories.user_id = ?
    ORDER BY cards.overall DESC
    ''', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [Card(*row[:-1]) for row in rows], [row[-1] for row in rows]

def add_coins(user_id, coins):
    """Add coins to a user's balance."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE players SET coins = coins + ? WHERE user_id = ?', (coins, user_id))
    conn.commit()
    conn.close()

def deduct_coins(user_id, amount):
    """Deduct coins from a user's balance."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE players SET coins = coins - ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()

def get_user_coins(user_id):
    """Get a user's coin balance."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT coins FROM players WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def has_sufficient_coins(user_id, cost):
    """Check if user has enough coins."""
    return get_user_coins(user_id) >= cost

def check_card_ownership(user_id, card_id):
    """Check if user owns a specific card."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM inventories WHERE user_id = ? AND card_id = ?', (user_id, card_id))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def increment_cards_dropped(user_id):
    """Increment the cards_dropped counter for a user."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE players SET cards_dropped = cards_dropped + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error incrementing cards_dropped: {e}")
        return False

def increment_cards_sold(user_id):
    """Increment the cards_sold counter for a user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE players SET cards_sold = cards_sold + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# Initialize on import
init_tables()
migrate_db()
