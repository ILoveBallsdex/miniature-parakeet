"""Game services used by the HTTP API. No Discord objects are used here."""

import random
import sqlite3
import json
from datetime import datetime, timedelta
from .database import read_connection, transaction, row_dict

PACKS = {
    1: {"name": "rare_player_pack", "display_name": "Rare Player Pack", "buyable": True, "cost": 1000},
    2: {"name": "icon_pack", "display_name": "Icon Pack", "buyable": True, "cost": 2500},
    3: {"name": "hero_pack", "display_name": "Hero Pack", "buyable": True, "cost": 1750},
    4: {"name": "tester_pack", "display_name": "Tester Pack", "buyable": False, "cost": 0},
}


class GameError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def card_value(overall: int) -> int:
    if overall >= 90: return 200
    if overall >= 85: return 150
    if overall >= 80: return 100
    if overall >= 75: return 50
    return 25


def get_card(card_id: int):
    with read_connection() as db:
        row = db.execute("SELECT * FROM cards WHERE card_id = ?", (card_id,)).fetchone()
    return row_dict(row)


def search_cards(search: str = "", limit: int = 50, offset: int = 0):
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    with read_connection() as db:
        if search:
            rows = db.execute(
                "SELECT * FROM cards WHERE name LIKE ? OR CAST(card_id AS TEXT) = ? ORDER BY overall DESC LIMIT ? OFFSET ?",
                (f"%{search}%", search, limit, offset),
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM cards ORDER BY overall DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [dict(row) for row in rows]


def ensure_player(user_id: int, name: str):
    if user_id <= 0 or not name.strip():
        raise GameError("A valid user ID and display name are required.")
    with transaction() as db:
        db.execute("INSERT INTO players (user_id, name) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET name = excluded.name", (user_id, name.strip()[:100]))
        row = db.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row)


def get_player(user_id: int):
    with read_connection() as db:
        row = db.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)).fetchone()
    return row_dict(row)


def get_inventory(user_id: int):
    with read_connection() as db:
        rows = db.execute("""SELECT c.*, i.edition, i.battles_played AS owner_battles_played,
            i.battles_won AS owner_battles_won, i.rounds_played AS owner_rounds_played,
            i.rounds_won AS owner_rounds_won, i.trade_count
            FROM inventories i JOIN cards c ON c.card_id = i.card_id
            WHERE i.user_id = ? ORDER BY c.overall DESC, c.name ASC""", (user_id,)).fetchall()
    return [dict(row) for row in rows]


def get_packs(user_id: int):
    with read_connection() as db:
        row = db.execute("SELECT * FROM packs WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return {"user_id": user_id, "rare_player_pack": 0, "icon_pack": 0, "hero_pack": 0, "tester_pack": 0}
    return dict(row)


def buy_pack(user_id: int, pack_id: int):
    pack = PACKS.get(pack_id)
    if not pack: raise GameError("Invalid pack ID.")
    if not pack["buyable"]: raise GameError("This pack cannot be purchased.", 403)
    with transaction() as db:
        player = db.execute("SELECT coins FROM players WHERE user_id = ?", (user_id,)).fetchone()
        if player is None: raise GameError("Player not found. Create the player first.", 404)
        if (player["coins"] or 0) < pack["cost"]: raise GameError("Not enough coins.", 409)
        db.execute("UPDATE players SET coins = coins - ? WHERE user_id = ?", (pack["cost"], user_id))
        db.execute("INSERT INTO packs (user_id) VALUES (?) ON CONFLICT(user_id) DO NOTHING", (user_id,))
        db.execute(f"UPDATE packs SET {pack['name']} = {pack['name']} + 1 WHERE user_id = ?", (user_id,))
        balance = db.execute("SELECT coins FROM players WHERE user_id = ?", (user_id,)).fetchone()["coins"]
        quantity = db.execute(f"SELECT {pack['name']} AS quantity FROM packs WHERE user_id = ?", (user_id,)).fetchone()["quantity"]
    return {"pack_id": pack_id, "pack": pack["display_name"], "spent": pack["cost"], "coins": balance, "quantity": quantity}


def _choose_unowned(db, user_id: int, where_sql: str, params=()):
    return db.execute(
        f"""SELECT * FROM cards WHERE {where_sql}
        AND card_id NOT IN (SELECT card_id FROM inventories WHERE user_id = ?)
        ORDER BY RANDOM() LIMIT 1""", (*params, user_id)
    ).fetchone()


def open_pack(user_id: int, pack_id: int):
    pack = PACKS.get(pack_id)
    if not pack: raise GameError("Invalid pack ID.")
    with transaction() as db:
        pack_row = db.execute(f"SELECT {pack['name']} AS quantity FROM packs WHERE user_id = ?", (user_id,)).fetchone()
        if pack_row is None or pack_row["quantity"] <= 0: raise GameError("You do not own this pack.", 409)

        selected = []
        if pack_id == 1:
            for _ in range(100):
                if random.random() < 0.8:
                    card = _choose_unowned(db, user_id, "card_type = 'Standard' AND overall > 85")
                else:
                    card = _choose_unowned(db, user_id, "card_type != 'Standard' AND overall > 85")
                if card:
                    selected = [card]
                    break
        elif pack_id == 2:
            card = _choose_unowned(db, user_id, "card_type = 'Icon'")
            if card: selected = [card]
        elif pack_id == 3:
            card = _choose_unowned(db, user_id, "card_type = 'Hero'")
            if card: selected = [card]
        elif pack_id == 4:
            icon = _choose_unowned(db, user_id, "card_type = 'Icon'")
            if icon: selected.append(icon)
            for _ in range(4):
                card = _choose_unowned(db, user_id, "overall > 85")
                if not card: break
                selected.append(card)

        if not selected:
            raise GameError("No eligible unowned cards are available for this pack.", 409)
        if pack_id == 4 and len(selected) != 5:
            raise GameError("Not enough eligible unowned cards to open the tester pack.", 409)

        db.execute(f"UPDATE packs SET {pack['name']} = {pack['name']} - 1 WHERE user_id = ?", (user_id,))
        for card in selected:
            db.execute("INSERT INTO inventories (user_id, card_id, edition) VALUES (?, ?, COALESCE((SELECT copies + 1 FROM cards WHERE card_id = ?), 1))", (user_id, card["card_id"], card["card_id"]))
            db.execute("UPDATE cards SET copies = COALESCE(copies, 0) + 1 WHERE card_id = ?", (card["card_id"],))
        remaining = db.execute(f"SELECT {pack['name']} AS quantity FROM packs WHERE user_id = ?", (user_id,)).fetchone()["quantity"]
    return {"pack_id": pack_id, "pack": pack["display_name"], "cards": [dict(card) for card in selected], "remaining": remaining}


def sell_cards(user_id: int, card_ids: list[int]):
    if not card_ids: raise GameError("Select at least one card.")
    if len(card_ids) > 100: raise GameError("You can sell at most 100 cards at once.")
    unique_ids = list(dict.fromkeys(int(card_id) for card_id in card_ids))
    with transaction() as db:
        rows = db.execute(
            f"SELECT c.card_id, c.overall FROM cards c JOIN inventories i ON i.card_id = c.card_id WHERE i.user_id = ? AND c.card_id IN ({','.join('?' for _ in unique_ids)})",
            (user_id, *unique_ids),
        ).fetchall()
        if len(rows) != len(unique_ids): raise GameError("One or more selected cards are not in this player's inventory.", 409)
        total = sum(card_value(row["overall"] or 0) for row in rows)
        db.executemany("DELETE FROM inventories WHERE user_id = ? AND card_id = ?", [(user_id, row["card_id"]) for row in rows])
        db.execute("UPDATE players SET coins = COALESCE(coins, 0) + ?, cards_sold = COALESCE(cards_sold, 0) + ? WHERE user_id = ?", (total, len(rows), user_id))
        balance = db.execute("SELECT coins FROM players WHERE user_id = ?", (user_id,)).fetchone()["coins"]
    return {"sold": len(rows), "coins_earned": total, "balance": balance}


def toggle_wishlist(user_id: int, card_id: int):
    with transaction() as db:
        if not db.execute("SELECT 1 FROM cards WHERE card_id = ?", (card_id,)).fetchone():
            raise GameError("Card not found.", 404)
        exists = db.execute("SELECT 1 FROM wishlists WHERE user_id = ? AND card_id = ?", (user_id, card_id)).fetchone()
        if exists:
            db.execute("DELETE FROM wishlists WHERE user_id = ? AND card_id = ?", (user_id, card_id))
            db.execute("UPDATE cards SET wishlist_count = MAX(0, COALESCE(wishlist_count, 0) - 1) WHERE card_id = ?", (card_id,))
            wishlisted = False
        else:
            db.execute("INSERT INTO wishlists (user_id, card_id) VALUES (?, ?)", (user_id, card_id))
            db.execute("UPDATE cards SET wishlist_count = COALESCE(wishlist_count, 0) + 1 WHERE card_id = ?", (card_id,))
            wishlisted = True
    return {"card_id": card_id, "wishlisted": wishlisted}


def get_decks(user_id: int):
    with read_connection() as db:
        rows = db.execute("SELECT deck_name, cards FROM decks WHERE user_id = ? ORDER BY deck_name", (user_id,)).fetchall()
    return [{"name": row["deck_name"], "card_ids": [int(x) for x in (row["cards"] or "").split(",") if x.strip().isdigit()]} for row in rows]


def save_deck(user_id: int, deck_name: str, card_ids: list[int]):
    name = deck_name.strip()
    if not name or len(name) > 40: raise GameError("Deck name must be between 1 and 40 characters.")
    if len(card_ids) != 5 or len(set(card_ids)) != 5: raise GameError("A deck must contain exactly 5 different cards.")
    with transaction() as db:
        owned = db.execute(
            f"SELECT COUNT(DISTINCT card_id) AS n FROM inventories WHERE user_id = ? AND card_id IN ({','.join('?' for _ in card_ids)})",
            (user_id, *card_ids),
        ).fetchone()["n"]
        if owned != 5: raise GameError("Every card in the deck must be owned by the player.", 409)
        existing = db.execute("SELECT rowid FROM decks WHERE user_id = ? AND deck_name = ? ORDER BY rowid LIMIT 1", (user_id, name)).fetchone()
        if existing:
            db.execute("UPDATE decks SET cards = ? WHERE rowid = ?", (",".join(map(str, card_ids)), existing["rowid"]))
        else:
            db.execute("INSERT INTO decks (user_id, deck_name, cards) VALUES (?, ?, ?)", (user_id, name, ",".join(map(str, card_ids))))
    return {"name": name, "card_ids": card_ids}



def _weighted_card_rows(db, excluded_ids=()):
    rows = db.execute("SELECT * FROM cards ORDER BY card_id").fetchall()
    weighted = []
    excluded = set(excluded_ids)
    for card in rows:
        if card["card_id"] in excluded:
            continue
        overall = card["overall"] or 0
        card_type = card["card_type"] or ""
        weight = 0
        if card_type == "Standard":
            if 70 <= overall <= 79: weight = 70
            elif 80 <= overall <= 85: weight = 20
            elif 86 <= overall <= 90: weight = 7
            elif overall > 90: weight = 3
        elif card_type == "Hero":
            weight = 2
        elif card_type == "Icon":
            if 80 <= overall <= 89: weight = 2
            elif overall >= 90: weight = 1
        elif card_type in ("Euro TOTT", "Copa America TOTT"):
            weight = 1
        if weight:
            weighted.append((card, weight))
    return weighted


def _weighted_choice(weighted):
    if not weighted:
        return None
    return random.choices([item[0] for item in weighted], weights=[item[1] for item in weighted], k=1)[0]


def claim_daily(user_id: int):
    today = datetime.now().date()
    now = datetime.now()
    with transaction() as db:
        player = db.execute("SELECT daily_streak, last_daily_claim FROM players WHERE user_id = ?", (user_id,)).fetchone()
        if player is None:
            raise GameError("Player not found. Create the player first.", 404)
        last_claim = player["last_daily_claim"]
        streak = int(player["daily_streak"] or 0)
        if last_claim:
            try:
                last_date = datetime.fromisoformat(last_claim).date()
            except ValueError:
                last_date = None
            if last_date == today:
                next_claim = datetime.combine(today + timedelta(days=1), datetime.min.time())
                raise GameError(f"Daily reward already claimed. Try again after {next_claim.isoformat()}.", 409)
            if last_date == today - timedelta(days=1):
                streak += 1
            else:
                streak = 1
        else:
            streak = 1

        if streak >= 14:
            bonus = 300
            tier = "Legendary"
        elif streak >= 7:
            bonus = 200
            tier = "Diamond"
        elif streak >= 4:
            bonus = 150
            tier = "Silver"
        else:
            bonus = 100
            tier = "Bronze"

        weighted = _weighted_card_rows(db)
        first = _weighted_choice(weighted)
        if first is None:
            raise GameError("The card catalog has no cards eligible for daily rewards.", 409)
        second = _weighted_choice(_weighted_card_rows(db, [first["card_id"]]))
        if second is None:
            raise GameError("At least two eligible cards are required for daily rewards.", 409)

        db.execute(
            "UPDATE players SET daily_streak = ?, last_daily_claim = ?, coins = COALESCE(coins, 0) + ?, cards_dropped = COALESCE(cards_dropped, 0) + 1 WHERE user_id = ?",
            (streak, now.isoformat(), bonus, user_id),
        )
        milestone_pack = streak in (7, 14)
        if milestone_pack:
            db.execute("INSERT INTO packs (user_id) VALUES (?) ON CONFLICT(user_id) DO NOTHING", (user_id,))
            db.execute("UPDATE packs SET rare_player_pack = rare_player_pack + 1 WHERE user_id = ?", (user_id,))
        db.execute(
            "INSERT INTO pending_daily_rewards (user_id, card_ids, created_at) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET card_ids = excluded.card_ids, created_at = excluded.created_at",
            (user_id, json.dumps([first["card_id"], second["card_id"]]), now.isoformat()),
        )
        balance = db.execute("SELECT coins FROM players WHERE user_id = ?", (user_id,)).fetchone()["coins"]

    return {
        "streak": streak,
        "tier": tier,
        "coins_earned": bonus,
        "balance": balance,
        "milestone_pack_awarded": milestone_pack,
        "choices": [dict(first), dict(second)],
        "expires_in_seconds": 120,
    }


def collect_daily(user_id: int, card_id: int):
    with transaction() as db:
        pending = db.execute("SELECT card_ids FROM pending_daily_rewards WHERE user_id = ?", (user_id,)).fetchone()
        if pending is None:
            raise GameError("There is no active daily reward to collect.", 409)
        choices = json.loads(pending["card_ids"])
        if card_id not in choices:
            raise GameError("That card was not one of your daily reward choices.", 403)
        if db.execute("SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?", (user_id, card_id)).fetchone():
            raise GameError("You already own this card.", 409)
        card = db.execute("SELECT * FROM cards WHERE card_id = ?", (card_id,)).fetchone()
        if card is None:
            raise GameError("Card not found.", 404)
        copies = int(card["copies"] or 0)
        db.execute("INSERT INTO inventories (user_id, card_id, edition) VALUES (?, ?, ?)", (user_id, card_id, copies + 1))
        db.execute("UPDATE cards SET copies = COALESCE(copies, 0) + 1 WHERE card_id = ?", (card_id,))
        db.execute("DELETE FROM pending_daily_rewards WHERE user_id = ?", (user_id,))
    return {"card": dict(card), "edition": copies + 1}


def claim_starter_pack(user_id: int):
    with transaction() as db:
        player = db.execute("SELECT has_claimed_starter_pack FROM players WHERE user_id = ?", (user_id,)).fetchone()
        if player is None:
            raise GameError("Player not found. Create the player first.", 404)
        if player["has_claimed_starter_pack"]:
            raise GameError("You have already claimed your starter pack.", 409)

        selections = []
        groups = [
            ("overall BETWEEN 70 AND 79", 6),
            ("overall BETWEEN 80 AND 85", 3),
            ("overall > 85 AND card_type = 'Standard'", 1),
        ]
        excluded = []
        for condition, amount in groups:
            for _ in range(amount):
                row = db.execute(
                    f"SELECT * FROM cards WHERE {condition} AND card_id NOT IN (SELECT card_id FROM inventories WHERE user_id = ?) AND card_id NOT IN ({','.join('?' for _ in excluded) if excluded else 'NULL'}) ORDER BY RANDOM() LIMIT 1",
                    (user_id, *excluded),
                ).fetchone()
                if row is None:
                    raise GameError("There are not enough eligible unowned cards for the starter pack.", 409)
                selections.append(row)
                excluded.append(row["card_id"])

        for card in selections:
            copies = int(card["copies"] or 0)
            db.execute("INSERT INTO inventories (user_id, card_id, edition) VALUES (?, ?, ?)", (user_id, card["card_id"], copies + 1))
            db.execute("UPDATE cards SET copies = COALESCE(copies, 0) + 1 WHERE card_id = ?", (card["card_id"],))
        db.execute("UPDATE players SET has_claimed_starter_pack = 1 WHERE user_id = ?", (user_id,))
    return {"cards": [dict(card) for card in selections], "count": len(selections)}
