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
    """Return a smoothly scaling player value, starting at 1,000 coins for 60 OVR."""
    overall = int(overall or 0)
    return max(1, round(1000 * (1.10 ** (overall - 60))))


def quicksell_value(overall: int) -> int:
    """Return half of the card's overall-based value, rounded down to whole coins."""
    return card_value(overall) // 2


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



# --------------------------- Admin card catalog ------------------------------

CARD_FIELDS = (
    "player_id", "name", "attack", "defense", "speed", "height", "club",
    "position", "overall", "image_path", "card_rarity", "card_type", "league", "nation",
)
CARD_INTEGER_FIELDS = {"attack", "defense", "speed", "overall"}


def _clean_catalog_card(payload: dict, creating: bool):
    if not isinstance(payload, dict):
        raise GameError("Card data must be an object.")
    required = ("card_id", "name", "attack", "defense", "speed", "overall", "card_rarity", "card_type")
    if creating:
        missing = [field for field in required if field not in payload or payload[field] is None]
        if missing:
            raise GameError("Missing card fields: " + ", ".join(missing))
    result = {}
    for field in ("card_id", *CARD_INTEGER_FIELDS):
        if field not in payload or payload[field] is None:
            continue
        value = payload[field]
        if isinstance(value, bool):
            raise GameError(f"{field} must be an integer.")
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise GameError(f"{field} must be an integer.")
        if field == "card_id" and value <= 0:
            raise GameError("card_id must be greater than zero.")
        if field != "card_id" and not 0 <= value <= 99:
            raise GameError(f"{field} must be between 0 and 99.")
        result[field] = value
    for field in ("name", "card_rarity", "card_type", "player_id", "height", "club", "position", "league", "nation", "image_path"):
        if field not in payload:
            continue
        value = payload[field]
        if value is None and field not in ("name", "card_rarity", "card_type"):
            result[field] = None
            continue
        if not isinstance(value, str) or not value.strip():
            raise GameError(f"{field} must be a non-empty string.")
        result[field] = value.strip()[:100]
    if creating:
        result.setdefault("player_id", str(result["card_id"]))
    return result


def create_catalog_card(payload: dict):
    values = _clean_catalog_card(payload, True)
    columns = ("card_id", *CARD_FIELDS)
    values.setdefault("image_path", payload.get("image_path"))
    if not values.get("image_path"):
        raise GameError("A card image is required.")
    with transaction() as db:
        if db.execute("SELECT 1 FROM cards WHERE card_id = ?", (values["card_id"],)).fetchone():
            raise GameError("A card with that ID already exists. Use replace-card instead.", 409)
        db.execute(
            f"INSERT INTO cards ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            tuple(values.get(column) for column in columns),
        )
        row = db.execute("SELECT * FROM cards WHERE card_id = ?", (values["card_id"],)).fetchone()
    return dict(row)


def update_catalog_card(card_id: int, payload: dict):
    values = _clean_catalog_card(payload, False)
    values.pop("card_id", None)
    if not values:
        raise GameError("Provide at least one card field to replace.")
    with transaction() as db:
        if not db.execute("SELECT 1 FROM cards WHERE card_id = ?", (card_id,)).fetchone():
            raise GameError("Card not found.", 404)
        assignments = ", ".join(f"{field} = ?" for field in values)
        db.execute(f"UPDATE cards SET {assignments} WHERE card_id = ?", (*values.values(), card_id))
        row = db.execute("SELECT * FROM cards WHERE card_id = ?", (card_id,)).fetchone()
    return dict(row)


def delete_catalog_card(card_id: int):
    """Permanently remove a catalog card and clean up every saved reference to it."""
    with transaction() as db:
        card = db.execute("SELECT card_id FROM cards WHERE card_id = ?", (card_id,)).fetchone()
        if card is None:
            raise GameError("Card not found.", 404)

        # Remove every owned copy, then remove the card from all saved lineups.
        inventory_result = db.execute("DELETE FROM inventories WHERE card_id = ?", (card_id,))
        lineup_count = 0
        decks = db.execute("SELECT rowid, cards FROM decks").fetchall()
        for deck in decks:
            card_ids = [value.strip() for value in (deck["cards"] or "").split(",") if value.strip()]
            filtered = [value for value in card_ids if value != str(card_id)]
            if len(filtered) != len(card_ids):
                db.execute("UPDATE decks SET cards = ? WHERE rowid = ?", (",".join(filtered), deck["rowid"]))
                lineup_count += 1

        # Clear other persisted references so deleted cards cannot appear in
        # wishlists, pending rewards, marketplace listings, drops, or trades.
        wishlist_result = db.execute("DELETE FROM wishlists WHERE card_id = ?", (card_id,))
        market_result = db.execute("DELETE FROM market_listings WHERE card_id = ?", (card_id,))
        drop_result = db.execute("DELETE FROM drop_sessions WHERE card_id = ?", (card_id,))
        trade_result = db.execute(
            "DELETE FROM trade_offers WHERE sender_card_id = ? OR recipient_card_id = ?",
            (card_id, card_id),
        )

        pending_removed = 0
        pending_rows = db.execute("SELECT user_id, card_ids FROM pending_daily_rewards").fetchall()
        for pending in pending_rows:
            try:
                choices = json.loads(pending["card_ids"])
            except (TypeError, ValueError):
                continue
            filtered = [value for value in choices if value != card_id]
            if len(filtered) != len(choices):
                pending_removed += 1
                if filtered:
                    db.execute(
                        "UPDATE pending_daily_rewards SET card_ids = ? WHERE user_id = ?",
                        (json.dumps(filtered), pending["user_id"]),
                    )
                else:
                    db.execute("DELETE FROM pending_daily_rewards WHERE user_id = ?", (pending["user_id"],))

        db.execute("DELETE FROM cards WHERE card_id = ?", (card_id,))

    return {
        "card_id": card_id,
        "deleted": True,
        "removed_inventory_copies": inventory_result.rowcount,
        "updated_lineups": lineup_count,
        "removed_wishlists": wishlist_result.rowcount,
        "removed_market_listings": market_result.rowcount,
        "removed_drops": drop_result.rowcount,
        "removed_trade_offers": trade_result.rowcount,
        "updated_pending_rewards": pending_removed,
    }

def wipe_all_catalog_cards():
    """Delete every catalog card and every saved reference to cards, preserving player accounts."""
    tables = (
        "inventories", "decks", "wishlists", "pending_daily_rewards",
        "market_listings", "drop_sessions", "trade_offers", "exchange_sessions", "battles",
    )
    with transaction() as db:
        counts = {}
        for table in tables:
            counts[table] = db.execute(f"DELETE FROM {table}").rowcount
        counts["catalog_cards"] = db.execute("DELETE FROM cards").rowcount
    return {
        "wiped": True,
        "catalog_cards_deleted": counts["catalog_cards"],
        "player_accounts_preserved": True,
        "player_profiles_preserved": True,
        "deleted_references": {key: value for key, value in counts.items() if key != "catalog_cards"},
    }


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
        rows = db.execute("""SELECT c.*, i.rowid AS inventory_row_id, i.edition, i.battles_played AS owner_battles_played,
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
    return {"pack_id": pack_id, "pack": pack["display_name"], "cards": [{**dict(card), "value": card_value(card["overall"] or 0), "quicksell_value": quicksell_value(card["overall"] or 0)} for card in selected], "remaining": remaining}


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


def quicksell_card(user_id: int, card_id: int):
    """Sell one owned copy of a card for half its overall-based value."""
    with transaction() as db:
        row = db.execute(
            """SELECT i.rowid AS inventory_row_id, c.overall
            FROM inventories i JOIN cards c ON c.card_id = i.card_id
            WHERE i.user_id = ? AND i.card_id = ?
            ORDER BY i.rowid LIMIT 1""",
            (user_id, card_id),
        ).fetchone()
        if row is None:
            raise GameError("You do not own this player card.", 409)

        coins_earned = quicksell_value(row["overall"] or 0)
        db.execute("DELETE FROM inventories WHERE rowid = ?", (row["inventory_row_id"],))
        db.execute(
            "UPDATE players SET coins = COALESCE(coins, 0) + ?, cards_sold = COALESCE(cards_sold, 0) + 1 WHERE user_id = ?",
            (coins_earned, user_id),
        )
        balance = db.execute(
            "SELECT coins FROM players WHERE user_id = ?", (user_id,)
        ).fetchone()["coins"]

    return {
        "card_id": card_id,
        "coins_earned": coins_earned,
        "balance": balance,
        "sold": True,
    }



def _match_card_name(rows, player_name):
    """Match a player name exactly, by a clear partial name, or with a small typo."""
    import re

    query = " ".join(str(player_name or "").lower().split())
    if len(query) < 2:
        raise GameError("Enter at least two letters of the player's name.")
    normalize = lambda value: re.sub(r"[^a-z0-9 ]", "", " ".join(str(value or "").lower().split()))
    normalized_query = normalize(query)
    exact = [row for row in rows if normalize(row["name"]) == normalized_query]
    if exact:
        return exact[0]

    partial = [row for row in rows if normalized_query in normalize(row["name"])]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        names = list(dict.fromkeys(str(row["name"]) for row in partial))
        if len(names) == 1:
            return partial[0]
        raise GameError("That name matches multiple players: " + ", ".join(names[:8]) + ". Type more of the name.")

    # Allow up to two character edits, but never guess from a distant match.
    def edit_distance(first, second):
        previous = list(range(len(second) + 1))
        for index, char in enumerate(first, 1):
            current = [index]
            for column, other in enumerate(second, 1):
                current.append(min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (char != other),
                ))
            previous = current
        return previous[-1]

    scored = []
    for row in rows:
        candidate = normalize(row["name"])
        choices = [candidate, *candidate.split()]
        distance = min(
            (edit_distance(normalized_query, part) for part in choices
             if abs(len(normalized_query) - len(part)) <= 2),
            default=99,
        )
        if distance <= 2:
            scored.append((distance, row))
    scored.sort(key=lambda item: item[0])
    if not scored:
        raise GameError("I couldn't find a player matching that name. Check the spelling and try again.", 404)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        names = list(dict.fromkeys(str(item[1]["name"]) for item in scored if item[0] == scored[0][0]))
        if len(names) > 1:
            raise GameError("That could match multiple players: " + ", ".join(names[:5]) + ". Type more of the name.")
    return scored[0][1]


def find_catalog_card(player_name: str):
    with read_connection() as db:
        rows = db.execute("SELECT * FROM cards ORDER BY overall DESC, name ASC").fetchall()
    if not rows:
        raise GameError("The player catalog is empty.", 404)
    card = _match_card_name(rows, player_name)
    return {**dict(card), "value": card_value(card["overall"] or 0),
            "quicksell_value": quicksell_value(card["overall"] or 0)}


def find_owned_card(user_id: int, player_name: str):
    with read_connection() as db:
        rows = db.execute(
            """SELECT c.*, i.rowid AS inventory_row_id, i.edition
            FROM inventories i JOIN cards c ON c.card_id = i.card_id
            WHERE i.user_id = ? ORDER BY c.overall DESC, c.name ASC""",
            (user_id,),
        ).fetchall()
    if not rows:
        raise GameError("You don't own any players yet.", 404)
    return dict(_match_card_name(rows, player_name))


def buy_player_card(user_id: int, card_id: int):
    """Buy one catalog card for its full value and add it to the player's inventory."""
    with transaction() as db:
        player = db.execute("SELECT coins FROM players WHERE user_id = ?", (user_id,)).fetchone()
        if player is None:
            raise GameError("Player not found. Create the player first.", 404)
        card = db.execute("SELECT * FROM cards WHERE card_id = ?", (card_id,)).fetchone()
        if card is None:
            raise GameError("Player card not found.", 404)
        cost = card_value(card["overall"] or 0)
        if int(player["coins"] or 0) < cost:
            raise GameError("You don't have enough coins to buy this player.", 409)
        edition_row = db.execute(
            "SELECT COALESCE(copies, 0) + 1 AS edition FROM cards WHERE card_id = ?",
            (card_id,),
        ).fetchone()
        edition = int(edition_row["edition"])
        db.execute("UPDATE cards SET copies = COALESCE(copies, 0) + 1 WHERE card_id = ?", (card_id,))
        db.execute("INSERT INTO inventories (user_id, card_id, edition) VALUES (?, ?, ?)",
                   (user_id, card_id, edition))
        db.execute("UPDATE players SET coins = COALESCE(coins, 0) - ? WHERE user_id = ?", (cost, user_id))
        balance = db.execute("SELECT coins FROM players WHERE user_id = ?", (user_id,)).fetchone()["coins"]
    return {**dict(card), "value": cost, "quicksell_value": quicksell_value(card["overall"] or 0),
            "coins_spent": cost, "balance": balance, "edition": edition}

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




FORMATIONS = {
 "2-2-3":["GK","LCB","RCB","CM","CM","LW","ST","RW"],
 "3-2-2":["GK","LCB","CB","RCB","CM","CM","ST","CF"],
 "2-3-2":["GK","LCB","RCB","CM","CM","CAM","ST","CF"],
 "4-2-1":["GK","LB","LCB","RCB","RB","CM","CM","ST"],
 "3-3-1":["GK","LCB","CB","RCB","CM","CM","CAM","ST"],
}
POSITION_AREAS={"GK":"gk","CB":"def","LCB":"def","RCB":"def","LB":"def","RB":"def","LWB":"def","RWB":"def","CDM":"mid","CM":"mid","CAM":"mid","LM":"mid","RM":"mid","LW":"att","RW":"att","ST":"att","CF":"att"}
POSITION_EQUIVALENTS={"CB":"CB","LCB":"CB","RCB":"CB","ST":"ST","CF":"ST"}

def position_penalty(natural_position, slot_position):
 n=(natural_position or "").upper().strip(); p=(slot_position or "").upper().strip()
 if POSITION_EQUIVALENTS.get(n,n)==POSITION_EQUIVALENTS.get(p,p): return 0
 a,b=POSITION_AREAS.get(n),POSITION_AREAS.get(p)
 if a and a==b: return 3
 if {a,b} in ({"def","mid"},{"mid","att"}): return 10
 return 30

def _lineup_data(db,user_id):
 row=db.execute("SELECT formation,slots FROM lineups WHERE user_id=?",(user_id,)).fetchone()
 formation=row["formation"] if row else "2-2-3"; slots=json.loads(row["slots"]) if row else {}
 result=[]
 for number,pos in enumerate(FORMATIONS[formation],1):
  cid=slots.get(str(number)); rowcard=db.execute("SELECT * FROM cards WHERE card_id=?",(cid,)).fetchone() if cid else None
  card=dict(rowcard) if rowcard else None
  if card:
   penalty=position_penalty(card.get("position"),pos)
   card.update({"slot":number,"slot_position":pos,"position_penalty":penalty,"effective_overall":max(0,int(card.get("overall") or 0)-penalty)})
  result.append({"slot":number,"position":pos,"card":card})
 return {"user_id":user_id,"formation":formation,"slots":result,"filled":sum(bool(x["card"]) for x in result)}

def get_lineup(user_id):
 with read_connection() as db:
  if not db.execute("SELECT 1 FROM players WHERE user_id=?",(user_id,)).fetchone(): raise GameError("Player not found. Register first.",404)
  return _lineup_data(db,user_id)

def _match_owned_lineup_player(db, user_id, player_name):
 import difflib
 query = " ".join(str(player_name or "").lower().split())
 if not query: raise GameError("Enter at least part of the player's name.")
 rows = db.execute(
  "SELECT c.card_id,c.name,c.overall FROM cards c JOIN inventories i ON i.card_id=c.card_id WHERE i.user_id=? ORDER BY c.overall DESC",
  (user_id,)
 ).fetchall()
 if not rows: raise GameError("You don't own any players yet.",404)
 exact = [r for r in rows if str(r["name"]).lower() == query]
 if exact: return exact[0]
 partial = [r for r in rows if query in str(r["name"]).lower()]
 if len(partial) == 1: return partial[0]
 if len(partial) > 1:
  raise GameError("That name matches multiple players: " + ", ".join(r["name"] for r in partial[:8]) + ". Type a little more of the name.")
 scored = []
 for r in rows:
  name = str(r["name"]).lower()
  score = difflib.SequenceMatcher(None, query, name).ratio()
  score = max(score, max((difflib.SequenceMatcher(None, query, part).ratio() for part in name.split()), default=0))
  if score >= 0.55: scored.append((score, r))
 scored.sort(key=lambda item: item[0], reverse=True)
 if not scored: raise GameError("I couldn't find a player matching that name. Try another part of the name.",404)
 if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.08:
  raise GameError("That could match multiple players: " + ", ".join(item[1]["name"] for item in scored[:5]) + ". Type a little more of the name.")
 return scored[0][1]


def add_lineup_player(user_id,player_name,slot):
 slot=int(slot)
 if not 1<=slot<=8: raise GameError("Slot must be from 1 to 8.")
 with transaction() as db:
  row=db.execute("SELECT formation,slots FROM lineups WHERE user_id=?",(user_id,)).fetchone()
  formation=row["formation"] if row else "2-2-3"; slots=json.loads(row["slots"]) if row else {}
  card=_match_owned_lineup_player(db,user_id,player_name)
  cid=int(card["card_id"])
  if str(slot) in slots and int(slots[str(slot)])!=cid: raise GameError("That slot is occupied. Remove its player first.",409)
  if str(cid) in [str(v) for k,v in slots.items() if k!=str(slot)]: raise GameError("That player is already in your lineup.",409)
  slots[str(slot)]=cid
  db.execute("INSERT INTO lineups(user_id,formation,slots) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET slots=excluded.slots",(user_id,formation,json.dumps(slots)))
  return _lineup_data(db,user_id)


def remove_lineup_player(user_id,slot):
 slot=int(slot)
 if not 1<=slot<=8: raise GameError("Slot must be from 1 to 8.")
 with transaction() as db:
  row=db.execute("SELECT slots FROM lineups WHERE user_id=?",(user_id,)).fetchone()
  if not row: raise GameError("You don't have a lineup yet.",404)
  slots=json.loads(row["slots"])
  if slots.get(str(slot)) is None: raise GameError("That slot is empty.",404)
  del slots[str(slot)]
  db.execute("UPDATE lineups SET slots=? WHERE user_id=?",(json.dumps(slots),user_id))
  return _lineup_data(db,user_id)


def swap_lineup_players(user_id,slot1,slot2):
 slot1,slot2=int(slot1),int(slot2)
 if not 1<=slot1<=8 or not 1<=slot2<=8: raise GameError("Both slots must be from 1 to 8.")
 if slot1==slot2: raise GameError("Choose two different slots to swap.")
 with transaction() as db:
  row=db.execute("SELECT slots FROM lineups WHERE user_id=?",(user_id,)).fetchone()
  if not row: raise GameError("You don't have a lineup yet.",404)
  slots=json.loads(row["slots"])
  first,second=slots.get(str(slot1)),slots.get(str(slot2))
  if first is None or second is None: raise GameError("Both slots must contain a player before you can swap them.",404)
  slots[str(slot1)],slots[str(slot2)]=second,first
  db.execute("UPDATE lineups SET slots=? WHERE user_id=?",(json.dumps(slots),user_id))
  return _lineup_data(db,user_id)


def set_lineup_formation(user_id,formation):
 formation=str(formation or "").strip()
 if formation not in FORMATIONS: raise GameError("Choose one of: "+", ".join(FORMATIONS))
 with transaction() as db:
  row=db.execute("SELECT slots FROM lineups WHERE user_id=?",(user_id,)).fetchone()
  slots=json.loads(row["slots"]) if row else {}
  db.execute("INSERT INTO lineups(user_id,formation,slots) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET formation=excluded.formation",(user_id,formation,json.dumps(slots)))
  return _lineup_data(db,user_id)


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



def _grant_random_reward(user_id: int, amount: int, cooldown_column: str, cooldown_seconds: int, coins: int = 0):
    """Grant weighted random cards and optional coins, enforcing cooldown atomically."""
    if cooldown_column not in ("last_daily_reward_claim", "last_quick_claim"):
        raise GameError("Invalid reward cooldown.")
    now = datetime.now()
    with transaction() as db:
        player = db.execute(
            f"SELECT coins, {cooldown_column} AS last_claim FROM players WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if player is None:
            raise GameError("Player not found. Create the player first.", 404)

        last_claim = player["last_claim"]
        if last_claim:
            try:
                last_time = datetime.fromisoformat(last_claim)
            except (TypeError, ValueError):
                last_time = None
            if last_time is not None:
                elapsed = (now - last_time).total_seconds()
                if elapsed < cooldown_seconds:
                    remaining = int(cooldown_seconds - elapsed)
                    minutes, seconds = divmod(remaining, 60)
                    hours, minutes = divmod(minutes, 60)
                    if hours:
                        wait = f"{hours}h {minutes}m"
                    elif minutes:
                        wait = f"{minutes}m {seconds}s"
                    else:
                        wait = f"{seconds}s"
                    raise GameError(f"You can claim this reward again in {wait}.", 409)

        weighted = _weighted_card_rows(db)
        selected = []
        excluded = set()
        for _ in range(amount):
            card = _weighted_choice([item for item in weighted if item[0]["card_id"] not in excluded])
            if card is None:
                raise GameError(f"The card catalog needs at least {amount} eligible different cards for this reward.", 409)
            selected.append(card)
            excluded.add(card["card_id"])

        for card in selected:
            edition_row = db.execute(
                "SELECT COALESCE(copies, 0) + 1 AS edition FROM cards WHERE card_id = ?",
                (card["card_id"],),
            ).fetchone()
            edition = int(edition_row["edition"])
            db.execute("UPDATE cards SET copies = COALESCE(copies, 0) + 1 WHERE card_id = ?", (card["card_id"],))
            db.execute(
                "INSERT INTO inventories (user_id, card_id, edition) VALUES (?, ?, ?)",
                (user_id, card["card_id"], edition),
            )

        db.execute(
            f"UPDATE players SET {cooldown_column} = ?, coins = COALESCE(coins, 0) + ?, cards_dropped = COALESCE(cards_dropped, 0) + ? WHERE user_id = ?",
            (now.isoformat(), coins, len(selected), user_id),
        )
        balance = db.execute("SELECT coins FROM players WHERE user_id = ?", (user_id,)).fetchone()["coins"]

    return {
        "cards": [{**dict(card), "value": card_value(card["overall"] or 0), "quicksell_value": quicksell_value(card["overall"] or 0)} for card in selected],
        "cards_received": len(selected),
        "coins_earned": coins,
        "balance": balance,
        "next_claim": (now + timedelta(seconds=cooldown_seconds)).isoformat(),
    }


def claim_daily_reward(user_id: int):
    return _grant_random_reward(user_id, 3, "last_daily_reward_claim", 0, coins=1000)


def claim_quick_reward(user_id: int):
    return _grant_random_reward(user_id, 1, "last_quick_claim", 0)



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
    from datetime import datetime
    expired = False
    with transaction() as db:
        pending = db.execute(
            "SELECT card_ids, created_at FROM pending_daily_rewards WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if pending is None:
            raise GameError("There is no active daily reward to collect.", 409)
        created = datetime.fromisoformat(pending["created_at"])
        if (datetime.now() - created).total_seconds() > 120:
            db.execute("DELETE FROM pending_daily_rewards WHERE user_id = ?", (user_id,))
            expired = True
        else:
            choices = json.loads(pending["card_ids"])
            if card_id not in choices:
                raise GameError("That card was not one of your daily reward choices.", 403)
            if db.execute("SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?", (user_id, card_id)).fetchone():
                raise GameError("You already own this card.", 409)
            card = db.execute("SELECT * FROM cards WHERE card_id = ?", (card_id,)).fetchone()
            if card is None:
                raise GameError("Card not found.", 404)
            copies = int(card["copies"] or 0)
            edition = copies + 1
            db.execute("UPDATE cards SET copies = COALESCE(copies, 0) + 1 WHERE card_id = ?", (card_id,))
            db.execute(
                "INSERT INTO inventories (user_id, card_id, edition) VALUES (?, ?, ?)",
                (user_id, card_id, edition),
            )
            db.execute("DELETE FROM pending_daily_rewards WHERE user_id = ?", (user_id,))
    if expired:
        raise GameError("Your daily card selection has expired. Claim again tomorrow.", 410)
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
            ("overall BETWEEN 70 AND 79 AND card_type != 'Unique'", 6),
            ("overall BETWEEN 80 AND 85 AND card_type != 'Unique'", 3),
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


def select_battle_lineup(battle_id: str, user_id: int):
    from datetime import datetime
    with transaction() as db:
        row = _battle_row(db, battle_id)
        if row["status"] != "active":
            raise GameError("Battle is not active.", 409)
        if user_id not in (row["player1_id"], row["player2_id"]):
            raise GameError("You are not part of this battle.", 403)
        state = json.loads(row["state_json"])
        if state["phase"] != "SETUP":
            raise GameError("Lineup selection is already complete.", 409)

        lineup_row = db.execute(
            "SELECT formation, slots FROM lineups WHERE user_id = ?", (user_id,)
        ).fetchone()
        if lineup_row is None:
            raise GameError("You don't have a lineup yet. Fill all 8 slots with /8 add first.", 409)
        formation = lineup_row["formation"]
        assigned = json.loads(lineup_row["slots"] or "{}")
        if len(assigned) != 8 or any(str(slot) not in assigned for slot in range(1, 9)):
            raise GameError("Your lineup must have all 8 slots filled before battling.", 409)

        lineup_snapshot = []
        card_ids = []
        for slot_number, position in enumerate(FORMATIONS[formation], 1):
            card_id = int(assigned[str(slot_number)])
            card_row = db.execute(
                "SELECT c.* FROM cards c JOIN inventories i ON i.card_id = c.card_id "
                "WHERE i.user_id = ? AND c.card_id = ?",
                (user_id, card_id),
            ).fetchone()
            if card_row is None:
                raise GameError("A player in your lineup is no longer owned. Update your lineup before battling.", 409)
            card = dict(card_row)
            penalty = position_penalty(card.get("position"), position)
            card.update({
                "slot": slot_number,
                "slot_position": position,
                "position_penalty": penalty,
                "effective_overall": max(0, int(card.get("overall") or 0) - penalty),
            })
            lineup_snapshot.append({"slot": slot_number, "position": position, "card": card})
            card_ids.append(card_id)

        prefix = "p1" if user_id == row["player1_id"] else "p2"
        state[prefix + "_deck"] = card_ids
        state[prefix + "_lineup"] = {
            "formation": formation,
            "slots": lineup_snapshot,
        }
        state[prefix + "_effective_overall"] = {
            str(item["card"]["card_id"]): item["card"]["effective_overall"]
            for item in lineup_snapshot
        }
        state[prefix + "_position_penalties"] = {
            str(item["card"]["card_id"]): item["card"]["position_penalty"]
            for item in lineup_snapshot
        }
        if state["p1_deck"] and state["p2_deck"]:
            state["phase"] = "ACTION"
        db.execute(
            "UPDATE battles SET state_json = ?, updated_at = ? WHERE battle_id = ?",
            (json.dumps(state), datetime.utcnow().isoformat(), battle_id),
        )
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





def _unlock_stat_achievements(db, user_id: int, stat_type: str):
    thresholds = {
        "rounds_won": {10: 1, 50: 2, 100: 8},
        "battles_won": {1: 3, 10: 4, 25: 5, 50: 6, 100: 8},
    }
    if stat_type not in thresholds:
        return
    row = db.execute(f"SELECT {stat_type} FROM players WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return
    value = int(row[stat_type] or 0)
    for threshold, achievement_id in thresholds[stat_type].items():
        if value >= threshold and db.execute(
            "SELECT 1 FROM achievements WHERE achievement_id = ?", (achievement_id,)
        ).fetchone():
            db.execute(
                "INSERT OR IGNORE INTO user_achievements (user_id, achievement_id) VALUES (?, ?)",
                (user_id, achievement_id),
            )


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

    p1_effective = int(state.get("p1_effective_overall", {}).get(str(p1_card["card_id"]), p1_card["overall"] or 0))
    p2_effective = int(state.get("p2_effective_overall", {}).get(str(p2_card["card_id"]), p2_card["overall"] or 0))
    winner_id = None
    if (p1_value or 0) > (p2_value or 0):
        winner_id = p1_id
        state["p1_wins"] += 1
        result = f"Player {p1_id} wins with {stat}."
    elif (p2_value or 0) > (p1_value or 0):
        winner_id = p2_id
        state["p2_wins"] += 1
        result = f"Player {p2_id} wins with {stat}."
    elif p1_effective > p2_effective:
        winner_id = p1_id
        state["p1_wins"] += 1
        result = f"Stats tied; player {p1_id} wins on effective overall ({p1_effective} vs {p2_effective})."
    elif p2_effective > p1_effective:
        winner_id = p2_id
        state["p2_wins"] += 1
        result = f"Stats tied; player {p2_id} wins on effective overall ({p2_effective} vs {p1_effective})."
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
        _unlock_stat_achievements(db, winner_id, "rounds_won")
        db.execute("UPDATE inventories SET rounds_won = rounds_won + 1 WHERE user_id = ? AND card_id = ?", (winner_id, winner_card_id))
        db.execute("UPDATE cards SET total_rounds_won = COALESCE(total_rounds_won, 0) + 1 WHERE card_id = ?", (winner_card_id,))
    else:
        db.execute("UPDATE players SET rounds_drawn = COALESCE(rounds_drawn, 0) + 1 WHERE user_id IN (?, ?)", (p1_id, p2_id))

    state["p1_used"].append(p1_card["card_id"])
    state["p2_used"].append(p2_card["card_id"])
    p1_result_card = dict(p1_card)
    p2_result_card = dict(p2_card)
    p1_result_card["position_penalty"] = int(state.get("p1_position_penalties", {}).get(str(p1_card["card_id"]), 0))
    p2_result_card["position_penalty"] = int(state.get("p2_position_penalties", {}).get(str(p2_card["card_id"]), 0))
    p1_result_card["effective_overall"] = p1_effective
    p2_result_card["effective_overall"] = p2_effective
    state["last_result"] = {
        "text": result, "winner_id": winner_id,
        "player1_card": p1_result_card, "player2_card": p2_result_card,
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
        _unlock_stat_achievements(db, winner_id, "battles_won")
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
            raise GameError("Choose an unused card from your saved eight-player lineup.", 403)
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
        if owner_id is not None:
            db.execute("UPDATE players SET cards_dropped = COALESCE(cards_dropped, 0) + 1 WHERE user_id = ?", (owner_id,))
        db.execute(
            "INSERT INTO drop_sessions (drop_id, card_id, owner_id, created_at, expires_at, status) VALUES (?, ?, ?, ?, ?, 'active')",
            (drop_id, card["card_id"], owner_id, now.isoformat(), expires.isoformat()),
        )
    card_data = dict(card)
    card_data["copies"] = int(card_data.get("copies") or 0) + 1
    return {"drop_id": drop_id, "owner_id": owner_id, "card": card_data, "owner_priority_seconds": 10 if owner_id is not None else 0, "expires_at": expires.isoformat()}


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
            edition = int(card["copies"] or 0) + 1
            db.execute("UPDATE cards SET copies = COALESCE(copies, 0) + 1 WHERE card_id = ?", (drop["card_id"],))
            db.execute(
                "INSERT INTO inventories (user_id, card_id, edition) VALUES (?, ?, ?)",
                (user_id, drop["card_id"], edition),
            )
            db.execute("UPDATE drop_sessions SET status = 'collected' WHERE drop_id = ?", (drop_id,))
    if expired:
        raise GameError("This drop has expired.", 410)
    return {"drop_id": drop_id, "collected_by": user_id, "card": dict(card), "edition": edition}



# ------------------------------- Transfer market -----------------------------


def expire_market_listings():
    from datetime import datetime
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with transaction() as db:
        rows = db.execute(
            "SELECT listing_id, seller_id, card_id, edition, trade_count FROM market_listings WHERE status = 'active' AND expires_at <= ?",
            (now,),
        ).fetchall()
        for row in rows:
            db.execute(
                "INSERT INTO inventories (user_id, card_id, edition, trade_count) VALUES (?, ?, ?, ?)",
                (row["seller_id"], row["card_id"], row["edition"], row["trade_count"]),
            )
            db.execute(
                "UPDATE market_listings SET status = 'expired', resolved_at = ? WHERE listing_id = ?",
                (now, row["listing_id"]),
            )
    return {"expired": len(rows)}


def create_market_listing(user_id: int, card_id: int, edition: int, price: int, duration_hours: int):
    from datetime import datetime, timedelta
    if duration_hours not in (1, 3, 6, 12, 24, 48):
        raise GameError("Listing duration must be 1, 3, 6, 12, 24, or 48 hours.")
    if price <= 0:
        raise GameError("Listing price must be positive.")
    with transaction() as db:
        card = db.execute("SELECT * FROM cards WHERE card_id = ?", (card_id,)).fetchone()
        if card is None:
            raise GameError("Card not found.", 404)
        minimum = card_value(card["overall"] or 0)
        if price < minimum:
            raise GameError(f"Minimum listing price is {minimum} coins.")
        inventory = db.execute(
            "SELECT rowid, trade_count FROM inventories WHERE user_id = ? AND card_id = ? AND edition = ? ORDER BY rowid LIMIT 1",
            (user_id, card_id, edition),
        ).fetchone()
        if inventory is None:
            raise GameError("You do not own that card edition.", 409)
        if db.execute(
            "SELECT 1 FROM market_listings WHERE seller_id = ? AND card_id = ? AND edition = ? AND status = 'active'",
            (user_id, card_id, edition),
        ).fetchone():
            raise GameError("That card edition is already listed.", 409)
        now = datetime.utcnow()
        expires = now + timedelta(hours=duration_hours)
        db.execute("DELETE FROM inventories WHERE rowid = ?", (inventory["rowid"],))
        cur = db.execute(
            """INSERT INTO market_listings
            (seller_id, card_id, edition, trade_count, price, listed_at, expires_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active')""",
            (user_id, card_id, edition, inventory["trade_count"] or 0, price,
             now.strftime("%Y-%m-%d %H:%M:%S"), expires.strftime("%Y-%m-%d %H:%M:%S")),
        )
        listing_id = cur.lastrowid
    return {"listing_id": listing_id, "seller_id": user_id, "card": dict(card), "edition": edition, "price": price, "expires_at": expires.strftime("%Y-%m-%d %H:%M:%S")}


def browse_market(search: str = "", page: int = 0, page_size: int = 5):
    expire_market_listings()
    page = max(0, int(page))
    page_size = max(1, min(int(page_size), 25))
    with read_connection() as db:
        where = "ml.status = 'active'"
        params = []
        if search:
            where += " AND c.name LIKE ?"
            params.append(f"%{search[:100]}%")
        total = db.execute(
            f"SELECT COUNT(*) AS n FROM market_listings ml JOIN cards c ON c.card_id = ml.card_id WHERE {where}",
            params,
        ).fetchone()["n"]
        rows = db.execute(
            f"""SELECT ml.listing_id, ml.seller_id, ml.card_id, ml.edition,
                ml.price, ml.expires_at, c.name, c.overall, c.card_rarity,
                p.name AS seller_name
                FROM market_listings ml
                JOIN cards c ON c.card_id = ml.card_id
                LEFT JOIN players p ON p.user_id = ml.seller_id
                WHERE {where}
                ORDER BY ml.listed_at DESC LIMIT ? OFFSET ?""",
            (*params, page_size, page * page_size),
        ).fetchall()
    return {"listings": [dict(row) for row in rows], "total": total, "page": page, "page_size": page_size}


def buy_market_listing(listing_id: int, buyer_id: int):
    from datetime import datetime
    expired = False
    with transaction() as db:
        listing = db.execute("SELECT * FROM market_listings WHERE listing_id = ?", (listing_id,)).fetchone()
        if listing is None or listing["status"] != "active":
            raise GameError("This listing is no longer available.", 404)
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        if listing["expires_at"] <= now:
            db.execute(
                "INSERT INTO inventories (user_id, card_id, edition, trade_count) VALUES (?, ?, ?, ?)",
                (listing["seller_id"], listing["card_id"], listing["edition"], listing["trade_count"]),
            )
            db.execute("UPDATE market_listings SET status = 'expired', resolved_at = ? WHERE listing_id = ?", (now, listing_id))
            expired = True
        else:
            if buyer_id == listing["seller_id"]:
                raise GameError("You cannot buy your own listing.", 403)
            buyer = db.execute("SELECT coins FROM players WHERE user_id = ?", (buyer_id,)).fetchone()
            if buyer is None:
                raise GameError("Buyer player not found.", 404)
            if (buyer["coins"] or 0) < listing["price"]:
                raise GameError("You do not have enough coins.", 409)
            card = db.execute("SELECT * FROM cards WHERE card_id = ?", (listing["card_id"],)).fetchone()
            db.execute("UPDATE players SET coins = coins - ? WHERE user_id = ?", (listing["price"], buyer_id))
            db.execute("UPDATE players SET coins = COALESCE(coins, 0) + ? WHERE user_id = ?", (listing["price"], listing["seller_id"]))
            db.execute(
                "INSERT INTO inventories (user_id, card_id, edition, trade_count) VALUES (?, ?, ?, ?)",
                (buyer_id, listing["card_id"], listing["edition"], (listing["trade_count"] or 0) + 1),
            )
            db.execute(
                "UPDATE market_listings SET status = 'sold', resolved_at = ?, buyer_id = ? WHERE listing_id = ?",
                (now, buyer_id, listing_id),
            )
    if expired:
        raise GameError("This listing has expired and the card was returned to its seller.", 410)
    return {"listing_id": listing_id, "buyer_id": buyer_id, "seller_id": listing["seller_id"], "card": dict(card), "edition": listing["edition"], "price": listing["price"]}


def cancel_market_listing(listing_id: int, seller_id: int):
    from datetime import datetime
    with transaction() as db:
        listing = db.execute(
            "SELECT * FROM market_listings WHERE listing_id = ? AND seller_id = ? AND status = 'active'",
            (listing_id, seller_id),
        ).fetchone()
        if listing is None:
            raise GameError("Listing not found, already sold, or it is not yours.", 404)
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "INSERT INTO inventories (user_id, card_id, edition, trade_count) VALUES (?, ?, ?, ?)",
            (seller_id, listing["card_id"], listing["edition"], listing["trade_count"]),
        )
        db.execute(
            "UPDATE market_listings SET status = 'cancelled', resolved_at = ? WHERE listing_id = ?",
            (now, listing_id),
        )
    return {"listing_id": listing_id, "status": "cancelled", "card_id": listing["card_id"], "edition": listing["edition"]}


def get_player_listings(user_id: int):
    expire_market_listings()
    with read_connection() as db:
        rows = db.execute(
            """SELECT ml.listing_id, ml.card_id, ml.edition, ml.price, ml.expires_at,
                c.name, c.overall, c.card_rarity
                FROM market_listings ml JOIN cards c ON c.card_id = ml.card_id
                WHERE ml.seller_id = ? AND ml.status = 'active'
                ORDER BY ml.listed_at DESC""",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]



# ------------------------------- Friends --------------------------------------


def add_friend_request(requester_id: int, recipient_id: int):
    from datetime import datetime
    if requester_id == recipient_id:
        raise GameError("You cannot friend yourself.")
    with transaction() as db:
        for user_id in (requester_id, recipient_id):
            if not db.execute("SELECT 1 FROM players WHERE user_id = ?", (user_id,)).fetchone():
                raise GameError(f"Player {user_id} does not exist.", 404)
        if db.execute("SELECT 1 FROM friendships WHERE user_id = ? AND friend_id = ?", (requester_id, recipient_id)).fetchone():
            raise GameError("You are already friends.", 409)
        if db.execute("SELECT 1 FROM friend_requests WHERE requester_id = ? AND recipient_id = ?", (requester_id, recipient_id)).fetchone():
            raise GameError("You have already sent a friend request.", 409)
        incoming = db.execute("SELECT request_id FROM friend_requests WHERE requester_id = ? AND recipient_id = ?", (recipient_id, requester_id)).fetchone()
        if incoming:
            db.execute("INSERT OR IGNORE INTO friendships (user_id, friend_id) VALUES (?, ?)", (requester_id, recipient_id))
            db.execute("INSERT OR IGNORE INTO friendships (user_id, friend_id) VALUES (?, ?)", (recipient_id, requester_id))
            db.execute("DELETE FROM friend_requests WHERE request_id = ?", (incoming["request_id"],))
            return {"status": "accepted", "friend_id": recipient_id}
        cur = db.execute(
            "INSERT INTO friend_requests (requester_id, recipient_id, requested_at) VALUES (?, ?, ?)",
            (requester_id, recipient_id, datetime.utcnow().isoformat()),
        )
        return {"status": "pending", "request_id": cur.lastrowid, "recipient_id": recipient_id}


def list_friends(user_id: int):
    with read_connection() as db:
        rows = db.execute(
            """SELECT f.friend_id, p.name, f.added_at
            FROM friendships f LEFT JOIN players p ON p.user_id = f.friend_id
            WHERE f.user_id = ? ORDER BY p.name""",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_friend_requests(user_id: int):
    with read_connection() as db:
        rows = db.execute(
            """SELECT fr.request_id, fr.requester_id, p.name AS requester_name,
                fr.requested_at, fr.notified
            FROM friend_requests fr LEFT JOIN players p ON p.user_id = fr.requester_id
            WHERE fr.recipient_id = ? ORDER BY fr.requested_at""",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def resolve_friend_request(request_id: int, recipient_id: int, accept: bool):
    with transaction() as db:
        row = db.execute("SELECT * FROM friend_requests WHERE request_id = ?", (request_id,)).fetchone()
        if row is None:
            raise GameError("Friend request not found.", 404)
        if row["recipient_id"] != recipient_id:
            raise GameError("Only the recipient can resolve this request.", 403)
        if accept:
            db.execute("INSERT OR IGNORE INTO friendships (user_id, friend_id) VALUES (?, ?)", (row["requester_id"], row["recipient_id"]))
            db.execute("INSERT OR IGNORE INTO friendships (user_id, friend_id) VALUES (?, ?)", (row["recipient_id"], row["requester_id"]))
        db.execute("DELETE FROM friend_requests WHERE request_id = ?", (request_id,))
    return {"request_id": request_id, "status": "accepted" if accept else "declined"}


def remove_friend(user_id: int, friend_id: int):
    with transaction() as db:
        cur = db.execute("DELETE FROM friendships WHERE user_id = ? AND friend_id = ?", (user_id, friend_id))
        db.execute("DELETE FROM friendships WHERE user_id = ? AND friend_id = ?", (friend_id, user_id))
        if cur.rowcount == 0:
            raise GameError("You are not friends with this player.", 404)
    return {"friend_id": friend_id, "status": "removed"}


# -------------------------- Profiles and achievements -------------------------


def get_player_stats(user_id: int):
    player = get_player(user_id)
    if player is None:
        raise GameError("Player not found.", 404)
    inventory = get_inventory(user_id)
    rarity_counts = {}
    for card in inventory:
        rarity = card.get("card_rarity") or "Unknown"
        rarity_counts[rarity] = rarity_counts.get(rarity, 0) + 1
    player["inventory_count"] = len(inventory)
    player["rarity_counts"] = rarity_counts
    return player


def get_player_wishlist(user_id: int):
    with read_connection() as db:
        rows = db.execute(
            """SELECT c.* FROM wishlists w JOIN cards c ON c.card_id = w.card_id
            WHERE w.user_id = ? ORDER BY c.overall DESC""",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_achievements(user_id: int):
    with read_connection() as db:
        rows = db.execute(
            """SELECT a.achievement_id, a.title, a.description,
                CASE WHEN ua.user_id IS NULL THEN 0 ELSE 1 END AS unlocked,
                ua.date_earned
            FROM achievements a LEFT JOIN user_achievements ua
                ON ua.achievement_id = a.achievement_id AND ua.user_id = ?
            ORDER BY a.achievement_id""",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def equip_title(user_id: int, achievement_id: int):
    with transaction() as db:
        row = db.execute(
            """SELECT a.title FROM achievements a JOIN user_achievements ua
            ON ua.achievement_id = a.achievement_id
            WHERE ua.user_id = ? AND a.achievement_id = ?""",
            (user_id, achievement_id),
        ).fetchone()
        if row is None:
            raise GameError("You have not unlocked that title.", 403)
        db.execute("UPDATE players SET display_title = ? WHERE user_id = ?", (row["title"], user_id))
    return {"display_title": row["title"]}


def leaderboard_entries(stat: str, limit: int = 10, user_ids=None):
    allowed = {
        "battles_won", "battles_played", "battles_lost", "battles_drawn",
        "rounds_won", "rounds_played", "rounds_lost", "rounds_drawn",
        "coins", "cards_dropped", "cards_sold",
    }
    if stat not in allowed:
        raise GameError("Unsupported leaderboard statistic.")
    limit = max(1, min(int(limit), 100))
    with read_connection() as db:
        params = []
        where = ""
        if user_ids is not None:
            ids = list(dict.fromkeys(int(uid) for uid in user_ids))
            if not ids:
                return {"stat": stat, "entries": []}
            where = f" WHERE user_id IN ({','.join('?' for _ in ids)})"
            params.extend(ids)
        rows = db.execute(
            f"SELECT user_id, name, {stat} AS value FROM players{where} ORDER BY {stat} DESC, name ASC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return {"stat": stat, "entries": [{"rank": i + 1, **dict(row)} for i, row in enumerate(rows)]}



# --------------------------------- Trade up -----------------------------------


TRADE_UP_TIERS = {
    "common": {"rarity": "Common", "reward": "Uncommon"},
    "uncommon": {"rarity": "Uncommon", "reward": "Rare"},
    "rare": {"rarity": "Rare Standard", "reward": "Hero/Icon (93 OVR or lower)"},
}


def eligible_trade_up_cards(user_id: int, tier: str):
    if tier not in TRADE_UP_TIERS:
        raise GameError("Tier must be common, uncommon, or rare.")
    if tier == "common":
        rarity_filter = "c.card_rarity = 'Common'"
    elif tier == "uncommon":
        rarity_filter = "c.card_rarity = 'Uncommon'"
    else:
        rarity_filter = "c.card_rarity = 'Rare' AND c.card_type = 'Standard'"
    with read_connection() as db:
        rows = db.execute(
            f"""SELECT i.rowid AS inventory_row_id, c.card_id, c.name, c.overall,
                i.edition, c.card_rarity, c.card_type, i.trade_count
                FROM inventories i JOIN cards c ON c.card_id = i.card_id
                WHERE i.user_id = ? AND {rarity_filter}
                ORDER BY c.overall ASC""",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def execute_trade_up(user_id: int, tier: str, inventory_row_ids: list[int]):
    if tier not in TRADE_UP_TIERS:
        raise GameError("Tier must be common, uncommon, or rare.")
    row_ids = list(dict.fromkeys(int(value) for value in inventory_row_ids))
    if len(row_ids) != 5:
        raise GameError("Select exactly five cards to trade up.")
    with transaction() as db:
        if tier == "common":
            rarity_filter = "c.card_rarity = 'Common'"
        elif tier == "uncommon":
            rarity_filter = "c.card_rarity = 'Uncommon'"
        else:
            rarity_filter = "c.card_rarity = 'Rare' AND c.card_type = 'Standard'"
        owned = db.execute(
            f"""SELECT i.rowid, i.card_id FROM inventories i
                JOIN cards c ON c.card_id = i.card_id
                WHERE i.user_id = ? AND {rarity_filter}
                AND i.rowid IN ({','.join('?' for _ in row_ids)})""",
            (user_id, *row_ids),
        ).fetchall()
        if len(owned) != 5:
            raise GameError("One or more selected cards are no longer eligible or owned.", 409)

        reward = None
        if tier == "common":
            reward = db.execute(
                "SELECT * FROM cards WHERE card_rarity = 'Uncommon' AND card_type != 'Unique' ORDER BY RANDOM() LIMIT 1"
            ).fetchone()
        elif tier == "uncommon":
            categories = [("std_mid", 55), ("std_hi", 20), ("hero", 5), ("icon", 2)]
            chosen = random.choices([item[0] for item in categories], weights=[item[1] for item in categories], k=1)[0]
            queries = {
                "std_mid": "card_type = 'Standard' AND card_rarity = 'Rare' AND overall BETWEEN 86 AND 89",
                "std_hi": "card_type = 'Standard' AND card_rarity = 'Rare' AND overall >= 90",
                "hero": "card_type = 'Hero'",
                "icon": "card_type = 'Icon'",
            }
            reward = db.execute(f"SELECT * FROM cards WHERE {queries[chosen]} ORDER BY RANDOM() LIMIT 1").fetchone()
            if reward is None:
                reward = db.execute(
                    "SELECT * FROM cards WHERE card_type = 'Standard' AND card_rarity = 'Rare' AND overall >= 86 ORDER BY RANDOM() LIMIT 1"
                ).fetchone()
        else:
            reward = db.execute(
                "SELECT * FROM cards WHERE card_type IN ('Hero', 'Icon') AND overall <= 93 ORDER BY RANDOM() LIMIT 1"
            ).fetchone()
        if reward is None:
            raise GameError("No eligible reward cards are available.", 409)

        db.executemany("DELETE FROM inventories WHERE rowid = ?", [(row_id,) for row_id in row_ids])
        current_copies = int(reward["copies"] or 0)
        edition = current_copies + 1
        db.execute("UPDATE cards SET copies = COALESCE(copies, 0) + 1 WHERE card_id = ?", (reward["card_id"],))
        db.execute("INSERT INTO inventories (user_id, card_id, edition) VALUES (?, ?, ?)", (user_id, reward["card_id"], edition))
    return {"tier": tier, "sacrificed_inventory_row_ids": row_ids, "reward": dict(reward), "edition": edition}



# -------------------------------- Secret rewards ------------------------------


SECRET_REWARDS = {
    "itscominghome": {
        "flag": "itscominghome",
        "card_ids": [10392, 10397, 10406, 10407, 10408, 10411, 10412, 10418, 10428, 10443, 10451, 10453, 10457],
    },
    "jogabonito": {
        "flag": "jogabonito",
        "card_ids": [10394, 10395, 10399, 10405, 10446, 10462, 10465, 10469],
    },
    "pineappleonpizza": {
        "flag": "pineappleonpizza",
        "card_ids": [10391, 10393, 10414, 10415, 10430, 10455, 10459, 10460],
    },
    "fubball": {
        "flag": "mannschaft",
        "card_ids": [10417, 10447, 10449, 10452, 10463],
    },
    "theflyingdutchmen": {
        "flag": "theflyingdutchmen",
        "card_ids": [10420, 10422, 10424, 10432, 10433, 10448, 10456],
    },
    "mayonnaise": {
        "flag": "blues",
        "card_ids": [10398, 10410, 10419, 10421, 10426, 10439, 10467],
    },
}


def claim_secret_reward(user_id: int, secret_name: str):
    secret = SECRET_REWARDS.get(secret_name)
    if secret is None:
        raise GameError("Unknown secret reward.", 404)
    flag = secret["flag"]
    with transaction() as db:
        player = db.execute(f"SELECT {flag} FROM players WHERE user_id = ?", (user_id,)).fetchone()
        if player is None:
            raise GameError("Player not found. Create the player first.", 404)
        if player[flag]:
            raise GameError("You have already claimed this secret reward.", 409)
        available = db.execute(
            f"""SELECT * FROM cards WHERE card_id IN ({','.join('?' for _ in secret['card_ids'])})
            AND card_id NOT IN (SELECT card_id FROM inventories WHERE user_id = ?)
            ORDER BY RANDOM()""",
            (*secret["card_ids"], user_id),
        ).fetchall()
        if not available:
            raise GameError("No unowned cards remain in this secret reward pool.", 409)
        card = available[0]
        edition = int(card["copies"] or 0) + 1
        db.execute("UPDATE cards SET copies = COALESCE(copies, 0) + 1 WHERE card_id = ?", (card["card_id"],))
        db.execute("INSERT INTO inventories (user_id, card_id, edition) VALUES (?, ?, ?)", (user_id, card["card_id"], edition))
        db.execute(f"UPDATE players SET {flag} = 1 WHERE user_id = ?", (user_id,))
    return {"secret": secret_name, "card": dict(card), "edition": edition}



# -------------------------------- Admin actions -------------------------------


def admin_give_coins(user_id: int, amount: int):
    if amount <= 0:
        raise GameError("Amount must be positive.")
    with transaction() as db:
        player = db.execute("SELECT coins FROM players WHERE user_id = ?", (user_id,)).fetchone()
        if player is None:
            raise GameError("Player not found.", 404)
        db.execute("UPDATE players SET coins = COALESCE(coins, 0) + ? WHERE user_id = ?", (amount, user_id))
        balance = db.execute("SELECT coins FROM players WHERE user_id = ?", (user_id,)).fetchone()["coins"]
    return {"user_id": user_id, "coins_added": amount, "balance": balance}


def admin_give_card(user_id: int, card_id: int):
    with transaction() as db:
        if not db.execute("SELECT 1 FROM players WHERE user_id = ?", (user_id,)).fetchone():
            raise GameError("Player not found.", 404)
        card = db.execute("SELECT * FROM cards WHERE card_id = ?", (card_id,)).fetchone()
        if card is None:
            raise GameError("Card not found.", 404)
        if db.execute("SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?", (user_id, card_id)).fetchone():
            raise GameError("Player already owns this card.", 409)
        copies = int(card["copies"] or 0)
        edition = copies + 1
        db.execute("UPDATE cards SET copies = COALESCE(copies, 0) + 1 WHERE card_id = ?", (card_id,))
        db.execute("INSERT INTO inventories (user_id, card_id, edition) VALUES (?, ?, ?)", (user_id, card_id, edition))
    return {"user_id": user_id, "card": dict(card), "edition": edition}


def admin_remove_card(user_id: int, card_id: int):
    with transaction() as db:
        rows = db.execute(
            "SELECT rowid FROM inventories WHERE user_id = ? AND card_id = ?",
            (user_id, card_id),
        ).fetchall()
        if not rows:
            raise GameError("Player does not own this card.", 404)
        db.execute("DELETE FROM inventories WHERE user_id = ? AND card_id = ?", (user_id, card_id))
    return {"user_id": user_id, "card_id": card_id, "removed_copies": len(rows)}



def delete_deck(user_id: int, deck_name: str):
    with transaction() as db:
        cur = db.execute("DELETE FROM decks WHERE user_id = ? AND deck_name = ?", (user_id, deck_name))
        if cur.rowcount == 0:
            raise GameError("Deck not found.", 404)
    return {"name": deck_name, "deleted": True}



def get_owned_card_copies(user_id: int, card_id: int):
    with read_connection() as db:
        rows = db.execute(
            """SELECT c.*, i.rowid AS inventory_row_id, i.edition,
                i.battles_played AS owner_battles_played,
                i.battles_won AS owner_battles_won,
                i.rounds_played AS owner_rounds_played,
                i.rounds_won AS owner_rounds_won,
                i.trade_count
            FROM inventories i JOIN cards c ON c.card_id = i.card_id
            WHERE i.user_id = ? AND i.card_id = ? ORDER BY i.edition""",
            (user_id, card_id),
        ).fetchall()
    return [dict(row) for row in rows]


def get_card_weight(search: str):
    from difflib import get_close_matches
    query = search.strip()
    if not query:
        raise GameError("Enter a card name.")
    with read_connection() as db:
        rows = db.execute("SELECT card_id, name, overall, card_type FROM cards").fetchall()
        if query.isdigit():
            card = next((row for row in rows if row["card_id"] == int(query)), None)
        else:
            matches = [row for row in rows if query.lower() in row["name"].lower()]
            if matches:
                card = matches[0]
            else:
                names = [row["name"] for row in rows]
                close = get_close_matches(query, names, n=1, cutoff=0.45)
                card = next((row for row in rows if close and row["name"] == close[0]), None)
        if card is None:
            raise GameError("Card not found or it has no defined pack weight.", 404)
        if card["card_type"] == "Standard":
            total = db.execute("SELECT COUNT(*) AS n FROM cards WHERE card_type = 'Standard'").fetchone()["n"]
            if not total:
                raise GameError("The card catalog is empty.", 409)
            overall = card["overall"] or 0
            if 70 <= overall <= 79: weight = 70
            elif 80 <= overall <= 85: weight = 20
            elif 86 <= overall <= 90: weight = 7
            elif overall > 90: weight = 3
            else: raise GameError("This card has no defined pack weight.", 404)
            value = weight / total
        else:
            total = db.execute("SELECT COUNT(*) AS n FROM cards WHERE card_type != 'Standard'").fetchone()["n"]
            if not total:
                raise GameError("The non-standard card catalog is empty.", 409)
            value = 3 / total
    return {"card_id": card["card_id"], "name": card["name"], "weight": value}
