"""Import custom FutBot cards from a JSON file into the API database.

Images must already exist inside the configured asset directory. Paths in the
JSON file are relative to that directory, for example "cards/player_1.png".
"""
import argparse
import json
import os
from pathlib import Path

from futbot_api.database import init_database, transaction

REQUIRED = ("card_id", "name", "attack", "defense", "speed", "overall", "image_path", "card_rarity", "card_type")
OPTIONAL = ("player_id", "height", "club", "position", "league", "nation")
INTEGER_FIELDS = ("card_id", "attack", "defense", "speed", "overall")


def load_cards(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read valid JSON: {exc}") from exc
    cards = payload.get("cards") if isinstance(payload, dict) else payload
    if not isinstance(cards, list) or not cards:
        raise ValueError("JSON must contain a non-empty array of cards (or an object with a 'cards' array).")
    return cards


def validate_card(card, asset_root):
    if not isinstance(card, dict):
        raise ValueError("Each card must be a JSON object.")
    missing = [field for field in REQUIRED if field not in card]
    if missing:
        raise ValueError("Missing required fields: " + ", ".join(missing))

    result = {}
    for field in INTEGER_FIELDS:
        value = card[field]
        if isinstance(value, bool):
            raise ValueError(f"{field} must be an integer.")
        try:
            result[field] = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field} must be an integer.")
    if result["card_id"] <= 0:
        raise ValueError("card_id must be greater than zero.")
    if not 0 <= result["overall"] <= 99:
        raise ValueError("overall must be between 0 and 99.")

    for field in ("name", "image_path", "card_rarity", "card_type"):
        if not isinstance(card[field], str) or not card[field].strip():
            raise ValueError(f"{field} must be a non-empty string.")
        result[field] = card[field].strip()

    image_rel = Path(result["image_path"])
    if image_rel.is_absolute() or ".." in image_rel.parts:
        raise ValueError("image_path must stay inside the asset directory.")
    image_file = (asset_root / image_rel).resolve()
    if not image_file.is_relative_to(asset_root) or not image_file.is_file():
        raise ValueError(f"Image file does not exist inside the asset directory: {result['image_path']}")
    if image_file.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise ValueError("image_path must use PNG, JPG, JPEG, WEBP, or GIF.")

    result["player_id"] = str(card.get("player_id") or result["card_id"])
    for field in OPTIONAL[1:]:
        value = card.get(field)
        result[field] = str(value).strip() if value is not None else None
    return result


def main():
    parser = argparse.ArgumentParser(description="Import custom cards into FutBot.")
    parser.add_argument("--file", required=True, help="Path to the cards JSON file.")
    parser.add_argument("--database", default=os.getenv("FUTBOT_DB_PATH", "./futbot_api.db"))
    parser.add_argument("--asset-root", default=os.getenv("FUTBOT_ASSET_DIR", "./assets"))
    parser.add_argument("--update", action="store_true", help="Allow existing card IDs to be updated.")
    args = parser.parse_args()

    os.environ["FUTBOT_DB_PATH"] = args.database
    asset_root = Path(args.asset_root).expanduser().resolve()
    if not asset_root.is_dir():
        parser.error(f"Asset directory does not exist: {asset_root}")

    try:
        raw_cards = load_cards(args.file)
        cards = [validate_card(card, asset_root) for card in raw_cards]
        ids = [card["card_id"] for card in cards]
        if len(ids) != len(set(ids)):
            raise ValueError("The JSON file contains duplicate card_id values.")

        columns = ("card_id", "player_id", "name", "attack", "defense", "speed", "height", "club", "position", "overall", "image_path", "card_rarity", "card_type", "league", "nation")
        with transaction() as db:
            existing = {row[0] for row in db.execute(
                f"SELECT card_id FROM cards WHERE card_id IN ({','.join('?' for _ in ids)})", ids
            )}
            if existing and not args.update:
                raise ValueError("These card IDs already exist: " + ", ".join(map(str, sorted(existing))) + ". Use --update to replace them.")
            placeholders = ",".join("?" for _ in columns)
            updates = ",".join(f"{column}=excluded.{column}" for column in columns if column != "card_id")
            sql = f"INSERT INTO cards ({','.join(columns)}) VALUES ({placeholders}) ON CONFLICT(card_id) DO UPDATE SET {updates}"
            db.executemany(sql, [tuple(card.get(column) for column in columns) for card in cards])
        print(f"Successfully imported {len(cards)} card(s) into {args.database}.")
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
