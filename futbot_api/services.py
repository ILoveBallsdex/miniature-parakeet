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
        edition = copies if copies else 1
        db.execute("INSERT INTO inventories (user_id, card_id, edition) VALUES (?, ?, ?)", (user_id, card_id, edition))
        db.execute("DELETE FROM pending_daily_rewards WHERE user_id = ?", (user_id,))
    return {"card": dict(card), "edition": edition}


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
                excluded_sql = f" AND card_id NOT IN ({','.join('?' for _ in excluded)})" if excluded else ""
                row = db.execute(
                    f"SELECT * FROM cards WHERE {condition} AND card_id NOT IN (SELECT card_id FROM inventories WHERE user_id = ?){excluded_sql} ORDER BY RANDOM() LIMIT 1",
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



# ------------------------------ Battle engine ------------------------------


def _battle_row(db, battle_id):
    row = db.execute("SELECT * FROM battles WHERE battle_id = ?", (battle_id,)).fetchone()
    if row is None:
        raise GameError("Battle not found.", 404)
    return row


def _battle_payload(row):
    payload = dict(row)
    payload["state"] = json.loads(payload.pop("state_json"))
    return payload


def create_battle(player1_id: int, player2_id: int):
    import uuid
    from datetime import datetime
    if player1_id == player2_id:
        raise GameError("You cannot battle yourself.")
    with transaction() as db:
        for user_id in (player1_id, player2_id):
            if not db.execute("SELECT 1 FROM players WHERE user_id = ?", (user_id,)).fetchone():
                raise GameError(f"Player {user_id} does not exist.", 404)
        battle_id = uuid.uuid4().hex
        now = datetime.utcnow().isoformat()
        state = {
            "phase": "INVITE", "p1_deck": [], "p2_deck": [],
            "p1_used": [], "p2_used": [], "p1_wins": 0, "p2_wins": 0,
            "draws": 0, "round": 1, "turn_player": player1_id,
            "p1_action": None, "p2_action": None, "p1_card": None,
            "p2_card": None, "draw_offers": [], "ready_players": [],
            "last_result": None, "winner_id": None,
        }
        db.execute(
            "INSERT INTO battles (battle_id, player1_id, player2_id, status, state_json, created_at, updated_at) VALUES (?, ?, ?, 'pending', ?, ?, ?)",
            (battle_id, player1_id, player2_id, json.dumps(state), now, now),
        )
        row = _battle_row(db, battle_id)
    return _battle_payload(row)


def get_battle(battle_id: str):
    with read_connection() as db:
        row = db.execute("SELECT * FROM battles WHERE battle_id = ?", (battle_id,)).fetchone()
    if row is None:
        raise GameError("Battle not found.", 404)
    return _battle_payload(row)


def accept_battle(battle_id: str, user_id: int):
    from datetime import datetime
    with transaction() as db:
        row = _battle_row(db, battle_id)
        if user_id != row["player2_id"]:
            raise GameError("Only the challenged player can accept this battle.", 403)
        if row["status"] != "pending":
            raise GameError("This battle is no longer pending.", 409)
        state = json.loads(row["state_json"])
        state["phase"] = "SETUP"
        db.execute("UPDATE battles SET status = 'active', state_json = ?, updated_at = ? WHERE battle_id = ?", (json.dumps(state), datetime.utcnow().isoformat(), battle_id))
        row = _battle_row(db, battle_id)
    return _battle_payload(row)


def decline_battle(battle_id: str, user_id: int):
    from datetime import datetime
    with transaction() as db:
        row = _battle_row(db, battle_id)
        if user_id != row["player2_id"]:
            raise GameError("Only the challenged player can decline this battle.", 403)
        if row["status"] != "pending":
            raise GameError("This battle is no longer pending.", 409)
        state = json.loads(row["state_json"])
        state["phase"] = "DECLINED"
        db.execute("UPDATE battles SET status = 'declined', state_json = ?, updated_at = ? WHERE battle_id = ?", (json.dumps(state), datetime.utcnow().isoformat(), battle_id))
        row = _battle_row(db, battle_id)
    return _battle_payload(row)


def select_battle_deck(battle_id: str, user_id: int, deck_name: str):
    from datetime import datetime
    with transaction() as db:
        row = _battle_row(db, battle_id)
        if row["status"] != "active":
            raise GameError("Battle is not active.", 409)
        if user_id not in (row["player1_id"], row["player2_id"]):
            raise GameError("You are not part of this battle.", 403)
        state = json.loads(row["state_json"])
        if state["phase"] != "SETUP":
            raise GameError("Deck selection is already complete.", 409)
        deck = db.execute("SELECT cards FROM decks WHERE user_id = ? AND deck_name = ?", (user_id, deck_name)).fetchone()
        if deck is None:
            raise GameError("Deck not found.", 404)
        card_ids = [int(value) for value in (deck["cards"] or "").split(",") if value.strip().isdigit()]
        if len(card_ids) != 5 or len(set(card_ids)) != 5:
            raise GameError("Battle decks must contain exactly 5 different cards.", 409)
        owned = db.execute(
            f"SELECT COUNT(DISTINCT card_id) AS n FROM inventories WHERE user_id = ? AND card_id IN ({','.join('?' for _ in card_ids)})",
            (user_id, *card_ids),
        ).fetchone()["n"]
        if owned != 5:
            raise GameError("The selected deck contains cards the player no longer owns.", 409)
        key = "p1_deck" if user_id == row["player1_id"] else "p2_deck"
        state[key] = card_ids
        if state["p1_deck"] and state["p2_deck"]:
            state["phase"] = "ACTION"
        db.execute("UPDATE battles SET state_json = ?, updated_at = ? WHERE battle_id = ?", (json.dumps(state), datetime.utcnow().isoformat(), battle_id))
        row = _battle_row(db, battle_id)
    return _battle_payload(row)


def choose_battle_action(battle_id: str, user_id: int, action: str):
    from datetime import datetime
    action = action.lower()
    if action not in ("attack", "defense", "speed"):
        raise GameError("Action must be attack, defense, or speed.")
    with transaction() as db:
        row = _battle_row(db, battle_id)
        if row["status"] != "active":
            raise GameError("Battle is not active.", 409)
        state = json.loads(row["state_json"])
        if user_id not in (row["player1_id"], row["player2_id"]):
            raise GameError("You are not part of this battle.", 403)
        if state["phase"] != "ACTION":
            raise GameError("Battle is not waiting for an action.", 409)
        if user_id != state["turn_player"]:
            raise GameError("It is not your turn.", 403)
        if user_id == row["player1_id"]:
            state["p1_action"] = action
            state["p2_action"] = {"attack": "defense", "defense": "attack", "speed": "speed"}[action]
        else:
            state["p2_action"] = action
            state["p1_action"] = {"attack": "defense", "defense": "attack", "speed": "speed"}[action]
        state["phase"] = "CARD_SELECT"
        db.execute("UPDATE battles SET state_json = ?, updated_at = ? WHERE battle_id = ?", (json.dumps(state), datetime.utcnow().isoformat(), battle_id))
        row = _battle_row(db, battle_id)
    return _battle_payload(row)


def _resolve_battle_round(db, row, state):
    p1_id, p2_id = row["player1_id"], row["player2_id"]
    p1_card = db.execute("SELECT * FROM cards WHERE card_id = ?", (state["p1_card"],)).fetchone()
    p2_card = db.execute("SELECT * FROM cards WHERE card_id = ?", (state["p2_card"],)).fetchone()
    if p1_card is None or p2_card is None:
        raise GameError("A selected battle card no longer exists.", 409)

    if state["p1_action"] == "attack" and state["p2_action"] == "defense":
        p1_value, p2_value = p1_card["attack"], p2_card["defense"]
        stat = "Attack vs Defense"
    elif state["p1_action"] == "defense" and state["p2_action"] == "attack":
        p1_value, p2_value = p1_card["defense"], p2_card["attack"]
        stat = "Defense vs Attack"
    else:
        p1_value, p2_value = p1_card["speed"], p2_card["speed"]
        stat = "Speed vs Speed"

    winner_id = None
    if (p1_value or 0) > (p2_value or 0):
        winner_id = p1_id
        state["p1_wins"] += 1
        result = f"Player {p1_id} wins with {stat}."
    elif (p2_value or 0) > (p1_value or 0):
        winner_id = p2_id
        state["p2_wins"] += 1
        result = f"Player {p2_id} wins with {stat}."
    elif (p1_card["overall"] or 0) > (p2_card["overall"] or 0):
        winner_id = p1_id
        state["p1_wins"] += 1
        result = f"Stats tied; player {p1_id} wins on overall."
    elif (p2_card["overall"] or 0) > (p1_card["overall"] or 0):
        winner_id = p2_id
        state["p2_wins"] += 1
        result = f"Stats tied; player {p2_id} wins on overall."
    else:
        state["draws"] += 1
        result = "The round is a draw."

    for uid, cid in ((p1_id, p1_card["card_id"]), (p2_id, p2_card["card_id"])):
        db.execute("UPDATE players SET rounds_played = rounds_played + 1 WHERE user_id = ?", (uid,))
        db.execute("UPDATE inventories SET rounds_played = rounds_played + 1 WHERE user_id = ? AND card_id = ?", (uid, cid))
        db.execute("UPDATE cards SET total_rounds_played = COALESCE(total_rounds_played, 0) + 1 WHERE card_id = ?", (cid,))
    if winner_id:
        loser_id = p2_id if winner_id == p1_id else p1_id
        winner_card_id = p1_card["card_id"] if winner_id == p1_id else p2_card["card_id"]
        db.execute("UPDATE players SET rounds_won = rounds_won + 1 WHERE user_id = ?", (winner_id,))
        db.execute("UPDATE players SET rounds_lost = rounds_lost + 1 WHERE user_id = ?", (loser_id,))
        db.execute("UPDATE inventories SET rounds_won = rounds_won + 1 WHERE user_id = ? AND card_id = ?", (winner_id, winner_card_id))
        db.execute("UPDATE cards SET total_rounds_won = COALESCE(total_rounds_won, 0) + 1 WHERE card_id = ?", (winner_card_id,))
    else:
        db.execute("UPDATE players SET rounds_drawn = COALESCE(rounds_drawn, 0) + 1 WHERE user_id IN (?, ?)", (p1_id, p2_id))

    state["p1_used"].append(p1_card["card_id"])
    state["p2_used"].append(p2_card["card_id"])
    state["last_result"] = {
        "text": result, "winner_id": winner_id,
        "player1_card": dict(p1_card), "player2_card": dict(p2_card),
        "player1_action": state["p1_action"], "player2_action": state["p2_action"],
        "stat": stat,
    }
    state["phase"] = "GAME_OVER" if state["p1_wins"] >= 3 or state["p2_wins"] >= 3 or state["round"] == 5 else "RESULT"
    if state["phase"] == "GAME_OVER":
        _finish_battle(db, row, state, winner_id=(p1_id if state["p1_wins"] > state["p2_wins"] else p2_id if state["p2_wins"] > state["p1_wins"] else None), draw=(state["p1_wins"] == state["p2_wins"]))
    state["p1_card"] = None
    state["p2_card"] = None
    return state


def _finish_battle(db, row, state, winner_id=None, draw=False, surrendered_by=None):
    p1_id, p2_id = row["player1_id"], row["player2_id"]
    if state.get("settled"):
        return
    db.execute("UPDATE players SET battles_played = battles_played + 1 WHERE user_id IN (?, ?)", (p1_id, p2_id))
    if draw:
        db.execute("UPDATE players SET battles_drawn = battles_drawn + 1, coins = COALESCE(coins, 0) + 100 WHERE user_id IN (?, ?)", (p1_id, p2_id))
    elif winner_id:
        loser_id = p2_id if winner_id == p1_id else p1_id
        db.execute("UPDATE players SET battles_won = battles_won + 1, coins = COALESCE(coins, 0) + 200 WHERE user_id = ?", (winner_id,))
        db.execute("UPDATE players SET battles_lost = battles_lost + 1, coins = COALESCE(coins, 0) + 100 WHERE user_id = ?", (loser_id,))
    if not surrendered_by:
        for uid, deck_key in ((p1_id, "p1_deck"), (p2_id, "p2_deck")):
            won = not draw and uid == winner_id
            for cid in state[deck_key]:
                db.execute("UPDATE inventories SET battles_played = battles_played + 1 WHERE user_id = ? AND card_id = ?", (uid, cid))
                db.execute("UPDATE cards SET total_battles_played = COALESCE(total_battles_played, 0) + 1 WHERE card_id = ?", (cid,))
                if won:
                    db.execute("UPDATE inventories SET battles_won = battles_won + 1 WHERE user_id = ? AND card_id = ?", (uid, cid))
                    db.execute("UPDATE cards SET total_battles_won = COALESCE(total_battles_won, 0) + 1 WHERE card_id = ?", (cid,))
    state["settled"] = True
    state["winner_id"] = winner_id
    state["phase"] = "GAME_OVER"


def choose_battle_card(battle_id: str, user_id: int, card_id: int):
    from datetime import datetime
    with transaction() as db:
        row = _battle_row(db, battle_id)
        if row["status"] != "active":
            raise GameError("Battle is not active.", 409)
        if user_id not in (row["player1_id"], row["player2_id"]):
            raise GameError("You are not part of this battle.", 403)
        state = json.loads(row["state_json"])
        if state["phase"] != "CARD_SELECT":
            raise GameError("Battle is not waiting for card selection.", 409)
        deck_key = "p1_deck" if user_id == row["player1_id"] else "p2_deck"
        used_key = "p1_used" if user_id == row["player1_id"] else "p2_used"
        card_key = "p1_card" if user_id == row["player1_id"] else "p2_card"
        if card_id not in state[deck_key] or card_id in state[used_key]:
            raise GameError("Choose an unused card from your selected battle deck.", 403)
        if state[card_key] is not None:
            raise GameError("You have already selected a card this round.", 409)
        if not db.execute("SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?", (user_id, card_id)).fetchone():
            raise GameError("You no longer own this card.", 409)
        state[card_key] = card_id
        if state["p1_card"] is not None and state["p2_card"] is not None:
            state = _resolve_battle_round(db, row, state)
        new_status = "completed" if state["phase"] == "GAME_OVER" else "active"
        db.execute("UPDATE battles SET status = ?, state_json = ?, updated_at = ? WHERE battle_id = ?", (new_status, json.dumps(state), datetime.utcnow().isoformat(), battle_id))
        row = _battle_row(db, battle_id)
    return _battle_payload(row)


def ready_next_round(battle_id: str, user_id: int):
    from datetime import datetime
    with transaction() as db:
        row = _battle_row(db, battle_id)
        if row["status"] != "active":
            raise GameError("Battle is not active.", 409)
        if user_id not in (row["player1_id"], row["player2_id"]):
            raise GameError("You are not part of this battle.", 403)
        state = json.loads(row["state_json"])
        if state["phase"] != "RESULT":
            raise GameError("The battle is not waiting for the next round.", 409)
        if user_id in state["ready_players"]:
            raise GameError("You are already ready.", 409)
        state["ready_players"].append(user_id)
        if len(state["ready_players"]) == 2:
            state["round"] += 1
            state["p1_action"] = state["p2_action"] = None
            state["p1_card"] = state["p2_card"] = None
            state["ready_players"] = []
            state["turn_player"] = row["player2_id"] if state["turn_player"] == row["player1_id"] else row["player1_id"]
            state["phase"] = "ACTION"
        db.execute("UPDATE battles SET state_json = ?, updated_at = ? WHERE battle_id = ?", (json.dumps(state), datetime.utcnow().isoformat(), battle_id))
        row = _battle_row(db, battle_id)
    return _battle_payload(row)


def offer_battle_draw(battle_id: str, user_id: int):
    from datetime import datetime
    with transaction() as db:
        row = _battle_row(db, battle_id)
        if row["status"] != "active":
            raise GameError("Battle is not active.", 409)
        if user_id not in (row["player1_id"], row["player2_id"]):
            raise GameError("You are not part of this battle.", 403)
        state = json.loads(row["state_json"])
        if user_id in state["draw_offers"]:
            raise GameError("You already offered a draw.", 409)
        state["draw_offers"].append(user_id)
        if len(state["draw_offers"]) == 2:
            _finish_battle(db, row, state, draw=True)
            db.execute("UPDATE battles SET status = 'completed' WHERE battle_id = ?", (battle_id,))
        db.execute("UPDATE battles SET state_json = ?, updated_at = ? WHERE battle_id = ?", (json.dumps(state), datetime.utcnow().isoformat(), battle_id))
        row = _battle_row(db, battle_id)
    return _battle_payload(row)


def surrender_battle(battle_id: str, user_id: int):
    from datetime import datetime
    with transaction() as db:
        row = _battle_row(db, battle_id)
        if row["status"] != "active":
            raise GameError("Battle is not active.", 409)
        if user_id not in (row["player1_id"], row["player2_id"]):
            raise GameError("You are not part of this battle.", 403)
        state = json.loads(row["state_json"])
        winner_id = row["player2_id"] if user_id == row["player1_id"] else row["player1_id"]
        _finish_battle(db, row, state, winner_id=winner_id, surrendered_by=user_id)
        db.execute("UPDATE battles SET status = 'completed', state_json = ?, updated_at = ? WHERE battle_id = ?", (json.dumps(state), datetime.utcnow().isoformat(), battle_id))
        row = _battle_row(db, battle_id)
    return _battle_payload(row)



# --------------------------- Trading and exchange ---------------------------


def create_trade_offer(sender_id: int, recipient_id: int, sender_card_id: int, recipient_card_id: int):
    import uuid
    from datetime import datetime
    if sender_id == recipient_id:
        raise GameError("You cannot trade with yourself.")
    with transaction() as db:
        for user_id in (sender_id, recipient_id):
            if not db.execute("SELECT 1 FROM players WHERE user_id = ?", (user_id,)).fetchone():
                raise GameError(f"Player {user_id} does not exist.", 404)
        for user_id, card_id in ((sender_id, sender_card_id), (recipient_id, recipient_card_id)):
            if not db.execute("SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?", (user_id, card_id)).fetchone():
                raise GameError(f"Player {user_id} does not own card {card_id}.", 409)
        if db.execute("SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?", (recipient_id, sender_card_id)).fetchone():
            raise GameError("The recipient already owns your offered card.", 409)
        if db.execute("SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?", (sender_id, recipient_card_id)).fetchone():
            raise GameError("You already own the recipient's offered card.", 409)
        trade_id = uuid.uuid4().hex
        now = datetime.utcnow().isoformat()
        db.execute(
            "INSERT INTO trade_offers (trade_id, sender_id, recipient_id, sender_card_id, recipient_card_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (trade_id, sender_id, recipient_id, sender_card_id, recipient_card_id, now, now),
        )
        row = db.execute("SELECT * FROM trade_offers WHERE trade_id = ?", (trade_id,)).fetchone()
    return dict(row)


def resolve_trade_offer(trade_id: str, user_id: int, accept: bool):
    from datetime import datetime
    with transaction() as db:
        offer = db.execute("SELECT * FROM trade_offers WHERE trade_id = ?", (trade_id,)).fetchone()
        if offer is None:
            raise GameError("Trade offer not found.", 404)
        if user_id != offer["recipient_id"] and not (not accept and user_id == offer["sender_id"]):
            raise GameError("You cannot resolve this trade offer.", 403)
        if offer["status"] != "pending":
            raise GameError("This trade offer is no longer pending.", 409)
        now = datetime.utcnow().isoformat()
        if not accept:
            db.execute("UPDATE trade_offers SET status = 'declined', updated_at = ? WHERE trade_id = ?", (now, trade_id))
        else:
            pairs = (
                (offer["sender_id"], offer["sender_card_id"], offer["recipient_id"]),
                (offer["recipient_id"], offer["recipient_card_id"], offer["sender_id"]),
            )
            for owner_id, card_id, new_owner in pairs:
                if not db.execute("SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?", (owner_id, card_id)).fetchone():
                    raise GameError("A player no longer owns the offered card.", 409)
                if db.execute("SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?", (new_owner, card_id)).fetchone():
                    raise GameError("A player already owns the card they would receive.", 409)
            db.execute("UPDATE inventories SET user_id = ?, trade_count = COALESCE(trade_count, 0) + 1 WHERE user_id = ? AND card_id = ?", (offer["recipient_id"], offer["sender_id"], offer["sender_card_id"]))
            db.execute("UPDATE inventories SET user_id = ?, trade_count = COALESCE(trade_count, 0) + 1 WHERE user_id = ? AND card_id = ?", (offer["sender_id"], offer["recipient_id"], offer["recipient_card_id"]))
            db.execute("UPDATE trade_offers SET status = 'accepted', updated_at = ? WHERE trade_id = ?", (now, trade_id))
        result = db.execute("SELECT * FROM trade_offers WHERE trade_id = ?", (trade_id,)).fetchone()
    return dict(result)


def create_exchange(player1_id: int, player2_id: int):
    import uuid
    from datetime import datetime
    if player1_id == player2_id:
        raise GameError("You cannot exchange with yourself.")
    with transaction() as db:
        for user_id in (player1_id, player2_id):
            if not db.execute("SELECT 1 FROM players WHERE user_id = ?", (user_id,)).fetchone():
                raise GameError(f"Player {user_id} does not exist.", 404)
        session_id = uuid.uuid4().hex
        now = datetime.utcnow().isoformat()
        db.execute(
            """INSERT INTO exchange_sessions
            (session_id, player1_id, player2_id, p1_cards, p2_cards, status, created_at, updated_at)
            VALUES (?, ?, ?, '[]', '[]', 'open', ?, ?)""",
            (session_id, player1_id, player2_id, now, now),
        )
        row = db.execute("SELECT * FROM exchange_sessions WHERE session_id = ?", (session_id,)).fetchone()
    return dict(row)


def _exchange_row(db, session_id):
    row = db.execute("SELECT * FROM exchange_sessions WHERE session_id = ?", (session_id,)).fetchone()
    if row is None:
        raise GameError("Exchange session not found.", 404)
    return row


def update_exchange_offer(session_id: str, user_id: int, card_ids: list[int], coins: int):
    from datetime import datetime
    card_ids = list(dict.fromkeys(int(card_id) for card_id in card_ids))
    if len(card_ids) > 25:
        raise GameError("You can offer at most 25 cards.")
    if coins < 0:
        raise GameError("Coin offer cannot be negative.")
    with transaction() as db:
        row = _exchange_row(db, session_id)
        if row["status"] != "open":
            raise GameError("This exchange is no longer open.", 409)
        if user_id not in (row["player1_id"], row["player2_id"]):
            raise GameError("You are not part of this exchange.", 403)
        balance = db.execute("SELECT coins FROM players WHERE user_id = ?", (user_id,)).fetchone()["coins"] or 0
        if coins > balance:
            raise GameError("Your coin offer exceeds your current balance.", 409)
        if card_ids:
            owned = db.execute(
                f"SELECT COUNT(DISTINCT card_id) AS n FROM inventories WHERE user_id = ? AND card_id IN ({','.join('?' for _ in card_ids)})",
                (user_id, *card_ids),
            ).fetchone()["n"]
            if owned != len(card_ids):
                raise GameError("You can only offer cards you currently own.", 409)
        key_cards = "p1_cards" if user_id == row["player1_id"] else "p2_cards"
        key_coins = "p1_coins" if user_id == row["player1_id"] else "p2_coins"
        db.execute(
            f"UPDATE exchange_sessions SET {key_cards} = ?, {key_coins} = ?, p1_locked = 0, p2_locked = 0, p1_confirmed = 0, p2_confirmed = 0, updated_at = ? WHERE session_id = ?",
            (json.dumps(card_ids), coins, datetime.utcnow().isoformat(), session_id),
        )
        row = _exchange_row(db, session_id)
    return dict(row)


def lock_exchange(session_id: str, user_id: int):
    from datetime import datetime
    with transaction() as db:
        row = _exchange_row(db, session_id)
        if row["status"] != "open":
            raise GameError("This exchange is no longer open.", 409)
        if user_id == row["player1_id"]:
            key = "p1_locked"
        elif user_id == row["player2_id"]:
            key = "p2_locked"
        else:
            raise GameError("You are not part of this exchange.", 403)
        db.execute(f"UPDATE exchange_sessions SET {key} = 1, updated_at = ? WHERE session_id = ?", (datetime.utcnow().isoformat(), session_id))
        row = _exchange_row(db, session_id)
    return dict(row)


def confirm_exchange(session_id: str, user_id: int):
    from datetime import datetime
    with transaction() as db:
        row = _exchange_row(db, session_id)
        if row["status"] != "open":
            raise GameError("This exchange is no longer open.", 409)
        if not row["p1_locked"] or not row["p2_locked"]:
            raise GameError("Both players must lock their offers before confirming.", 409)
        if user_id == row["player1_id"]:
            key = "p1_confirmed"
        elif user_id == row["player2_id"]:
            key = "p2_confirmed"
        else:
            raise GameError("You are not part of this exchange.", 403)
        db.execute(f"UPDATE exchange_sessions SET {key} = 1, updated_at = ? WHERE session_id = ?", (datetime.utcnow().isoformat(), session_id))
        row = _exchange_row(db, session_id)
        if row["p1_confirmed"] and row["p2_confirmed"]:
            p1_cards, p2_cards = json.loads(row["p1_cards"]), json.loads(row["p2_cards"])
            p1_id, p2_id = row["player1_id"], row["player2_id"]
            p1_coins, p2_coins = row["p1_coins"], row["p2_coins"]
            for owner_id, card_ids, other_id in ((p1_id, p1_cards, p2_id), (p2_id, p2_cards, p1_id)):
                for card_id in card_ids:
                    if not db.execute("SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?", (owner_id, card_id)).fetchone():
                        raise GameError("An offered card is no longer owned by its player.", 409)
                    if db.execute("SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?", (other_id, card_id)).fetchone():
                        raise GameError("A player already owns a card they would receive.", 409)
            balances = {
                p1_id: db.execute("SELECT coins FROM players WHERE user_id = ?", (p1_id,)).fetchone()["coins"] or 0,
                p2_id: db.execute("SELECT coins FROM players WHERE user_id = ?", (p2_id,)).fetchone()["coins"] or 0,
            }
            if p1_coins > balances[p1_id] or p2_coins > balances[p2_id]:
                raise GameError("An offer exceeds a player's current coin balance.", 409)
            for card_id in p1_cards:
                db.execute("UPDATE inventories SET user_id = ?, trade_count = COALESCE(trade_count, 0) + 1 WHERE user_id = ? AND card_id = ?", (p2_id, p1_id, card_id))
            for card_id in p2_cards:
                db.execute("UPDATE inventories SET user_id = ?, trade_count = COALESCE(trade_count, 0) + 1 WHERE user_id = ? AND card_id = ?", (p1_id, p2_id, card_id))
            db.execute("UPDATE players SET coins = coins - ? + ? WHERE user_id = ?", (p1_coins, p2_coins, p1_id))
            db.execute("UPDATE players SET coins = coins - ? + ? WHERE user_id = ?", (p2_coins, p1_coins, p2_id))
            db.execute("UPDATE exchange_sessions SET status = 'completed', updated_at = ? WHERE session_id = ?", (datetime.utcnow().isoformat(), session_id))
        row = _exchange_row(db, session_id)
    return dict(row)


def cancel_exchange(session_id: str, user_id: int):
    from datetime import datetime
    with transaction() as db:
        row = _exchange_row(db, session_id)
        if user_id not in (row["player1_id"], row["player2_id"]):
            raise GameError("You are not part of this exchange.", 403)
        if row["status"] != "open":
            raise GameError("This exchange is no longer open.", 409)
        db.execute("UPDATE exchange_sessions SET status = 'cancelled', updated_at = ? WHERE session_id = ?", (datetime.utcnow().isoformat(), session_id))
        row = _exchange_row(db, session_id)
    return dict(row)



def get_trade_offer(trade_id: str):
    with read_connection() as db:
        row = db.execute("SELECT * FROM trade_offers WHERE trade_id = ?", (trade_id,)).fetchone()
    if row is None:
        raise GameError("Trade offer not found.", 404)
    return dict(row)


def get_exchange(session_id: str):
    with read_connection() as db:
        row = db.execute("SELECT * FROM exchange_sessions WHERE session_id = ?", (session_id,)).fetchone()
    if row is None:
        raise GameError("Exchange session not found.", 404)
    result = dict(row)
    result["p1_cards"] = json.loads(result["p1_cards"])
    result["p2_cards"] = json.loads(result["p2_cards"])
    return result



# -------------------------------- Card drops --------------------------------


def create_card_drop(owner_id: int | None = None):
    import uuid
    from datetime import datetime, timedelta
    with transaction() as db:
        if owner_id is not None and not db.execute("SELECT 1 FROM players WHERE user_id = ?", (owner_id,)).fetchone():
            raise GameError("Drop owner is not registered.", 404)
        weighted = _weighted_card_rows(db)
        card = _weighted_choice(weighted)
        if card is None:
            raise GameError("No eligible cards are available to drop.", 409)
        now = datetime.utcnow()
        drop_id = uuid.uuid4().hex
        expires = now + timedelta(seconds=120)
        db.execute("UPDATE cards SET copies = COALESCE(copies, 0) + 1 WHERE card_id = ?", (card["card_id"],))
        if owner_id is not None:
            db.execute("UPDATE players SET cards_dropped = COALESCE(cards_dropped, 0) + 1 WHERE user_id = ?", (owner_id,))
        db.execute(
            "INSERT INTO drop_sessions (drop_id, card_id, owner_id, created_at, expires_at, status) VALUES (?, ?, ?, ?, ?, 'active')",
            (drop_id, card["card_id"], owner_id, now.isoformat(), expires.isoformat()),
        )
    return {"drop_id": drop_id, "owner_id": owner_id, "card": dict(card), "owner_priority_seconds": 10 if owner_id is not None else 0, "expires_at": expires.isoformat()}


def get_card_drop(drop_id: str):
    with read_connection() as db:
        row = db.execute(
            "SELECT d.*, c.name, c.overall, c.attack, c.defense, c.speed, c.card_type, c.card_rarity, c.image_path FROM drop_sessions d JOIN cards c ON c.card_id = d.card_id WHERE d.drop_id = ?",
            (drop_id,),
        ).fetchone()
    if row is None:
        raise GameError("Drop not found.", 404)
    return dict(row)


def collect_card_drop(drop_id: str, user_id: int, username: str):
    from datetime import datetime
    with transaction() as db:
        drop = db.execute("SELECT * FROM drop_sessions WHERE drop_id = ?", (drop_id,)).fetchone()
        if drop is None:
            raise GameError("Drop not found.", 404)
        if drop["status"] != "active":
            raise GameError("This drop has already been collected or expired.", 409)
        now = datetime.utcnow()
        expires = datetime.fromisoformat(drop["expires_at"])
        if now >= expires:
            db.execute("UPDATE drop_sessions SET status = 'expired' WHERE drop_id = ?", (drop_id,))
            expired = True
        else:
            expired = False
        if expired:
            # Return from the transaction normally so the expired status is committed.
            pass
        if expired:
            result = None
        else:
            result = True
        if expired:
            # No inventory or player changes are made for expired drops.
            pass
        created = datetime.fromisoformat(drop["created_at"])
        if not expired and drop["owner_id"] is not None and user_id != drop["owner_id"] and (now - created).total_seconds() < 10:
            raise GameError("The drop is still reserved for its owner.", 403)
        if not expired:
            db.execute(
                "INSERT INTO players (user_id, name) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET name = excluded.name",
                (user_id, username.strip()[:100] or str(user_id)),
            )
        if not expired and db.execute("SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?", (user_id, drop["card_id"])).fetchone():
            raise GameError("You already own this card.", 409)
        card = db.execute("SELECT * FROM cards WHERE card_id = ?", (drop["card_id"],)).fetchone() if not expired else None
        if not expired:
            db.execute(
                "INSERT INTO inventories (user_id, card_id, edition) VALUES (?, ?, COALESCE((SELECT copies FROM cards WHERE card_id = ?), 1))",
                (user_id, drop["card_id"], drop["card_id"]),
            )
            db.execute("UPDATE drop_sessions SET status = 'collected' WHERE drop_id = ?", (drop_id,))
    if expired:
        raise GameError("This drop has expired.", 410)
    return {"drop_id": drop_id, "collected_by": user_id, "card": dict(card)}
