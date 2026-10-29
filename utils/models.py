"""
Data models for FutBot
Card and Player classes, plus card loading and weight utilities.
"""
import random
from utils.database import get_connection

def determine_card_rarity(overall):
    """Determine rarity based on overall rating."""
    if overall is None:
        return 'Common'
    if overall > 85:
        return 'Rare'
    elif overall > 75:
        return 'Uncommon'
    else:
        return 'Common'


class Card:
    """Represents a player card with stats and attributes."""
    def __init__(self, card_id, player_id, name, attack, defense, speed, height, club, position, overall, image_path, card_rarity=None, card_type='standard', league=None, nation=None, copies=0, wishlist_count=0, *args):
        self.card_id = card_id
        self.player_id = player_id
        self.name = name
        self.attack = attack
        self.defense = defense
        self.speed = speed
        self.height = height
        self.club = club
        self.position = position
        self.overall = overall if overall is not None else 0
        self.image_path = image_path
        self.card_rarity = card_rarity if card_rarity else determine_card_rarity(overall)
        self.card_type = card_type
        self.league = league
        self.nation = nation
        self.copies = copies
        self.wishlist_count = wishlist_count


class Player:
    """Represents a bot user/player."""
    def __init__(self, user_id, name, battles_played=0, battles_won=0, battles_lost=0, has_claimed_starter_pack=False):
        self.user_id = user_id
        self.name = name
        self.battles_played = battles_played
        self.battles_won = battles_won
        self.battles_lost = battles_lost
        self.has_claimed_starter_pack = has_claimed_starter_pack
        self.selected_deck = None
        self.decks = {}


# --- Card Loading and Utilities ---

def fetch_all_cards():
    """Fetch all cards from the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM cards')
    rows = cursor.fetchall()
    conn.close()
    return [Card(*row) for row in rows]

def get_card_by_id(card_id):
    """Get a card by its ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM cards WHERE card_id = ?', (card_id,))
    row = cursor.fetchone()
    conn.close()
    return Card(*row) if row else None

def get_card_by_name(card_name):
    """Get a card by fuzzy name matching."""
    from rapidfuzz import process as rapidfuzz_process
    cards = fetch_all_cards()
    card_names = [card.name.lower() for card in cards]
    best_match = rapidfuzz_process.extractOne(card_name.lower(), card_names)
    if best_match:
        best_match_index = card_names.index(best_match[0])
        return cards[best_match_index]
    return None

def add_card(card):
    """Add or update a card in the database."""
    conn = get_connection()
    cursor = conn.cursor()
    card_rarity = determine_card_rarity(card.overall)
    cursor.execute('''
    INSERT INTO cards (card_id, player_id, name, attack, defense, speed, height, club, position, overall, image_path, card_rarity, card_type, league, nation, copies)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(card_id) DO UPDATE SET copies = copies + 1
    ''', (card.card_id, card.player_id, card.name, card.attack, card.defense, card.speed, card.height, card.club, card.position, card.overall, card.image_path, card_rarity, card.card_type, card.league, card.nation, card.copies))
    conn.commit()
    conn.close()


# --- Card Weights for Pack Drops ---

# Weight values
WEIGHT_70_79 = 70
WEIGHT_80_85 = 20
WEIGHT_86_90 = 7
WEIGHT_90_PLUS = 3
WEIGHT_HERO = 2
WEIGHT_ICON_80 = 2
WEIGHT_ICON_90 = 1
WEIGHT_TOTT = 1

def get_cards_with_weights():
    """Get all cards with their drop weights for pack opening."""
    all_cards = fetch_all_cards()
    
    cards_with_weights = []
    
    for card in all_cards:
        if card.card_type == 'Standard':
            if 70 <= card.overall <= 79:
                cards_with_weights.append((card, WEIGHT_70_79))
            elif 80 <= card.overall <= 85:
                cards_with_weights.append((card, WEIGHT_80_85))
            elif 86 <= card.overall <= 90:
                cards_with_weights.append((card, WEIGHT_86_90))
            elif card.overall > 90:
                cards_with_weights.append((card, WEIGHT_90_PLUS))
        elif card.card_type == 'Hero':
            cards_with_weights.append((card, WEIGHT_HERO))
        elif card.card_type == 'Icon':
            if 80 <= card.overall <= 89:
                cards_with_weights.append((card, WEIGHT_ICON_80))
            elif card.overall >= 90:
                cards_with_weights.append((card, WEIGHT_ICON_90))
        elif card.card_type in ['Euro TOTT', 'Copa America TOTT']:
            cards_with_weights.append((card, WEIGHT_TOTT))
    
    return cards_with_weights

def weighted_choice(cards_with_weights):
    """Select a random card based on weights."""
    total = sum(weight for card, weight in cards_with_weights)
    r = random.uniform(0, total)
    upto = 0
    for card, weight in cards_with_weights:
        if upto + weight >= r:
            return card
        upto += weight
    return cards_with_weights[0][0] if cards_with_weights else None


def get_card_weight_by_name(card_name):
    """Get the pack weight for a specific card by name."""
    card = get_card_by_name(card_name)
    if not card:
        return None, None

    conn = get_connection()
    cursor = conn.cursor()

    if card.card_type == 'Standard':
        cursor.execute('SELECT COUNT(*) FROM cards WHERE card_type = "Standard"')
        total_standard_cards = cursor.fetchone()[0]
        conn.close()

        if 70 <= card.overall <= 79:
            return WEIGHT_70_79 / total_standard_cards, card.name
        elif 80 <= card.overall <= 85:
            return WEIGHT_80_85 / total_standard_cards, card.name
        elif 86 <= card.overall <= 90:
            return WEIGHT_86_90 / total_standard_cards, card.name
        elif card.overall > 90:
            return WEIGHT_90_PLUS / total_standard_cards, card.name
        
    else:
        cursor.execute('SELECT COUNT(*) FROM cards WHERE card_type != "Standard"')
        total_non_standard_cards = cursor.fetchone()[0]
        conn.close()
        return 3 / total_non_standard_cards, card.name
    
    conn.close()
    return None, None

