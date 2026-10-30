"""
FutBot HTTP API.

This service owns FutBot game data and rules. It deliberately does not import
bot.py, create a Discord client, or require a Discord bot token.
"""

import hmac
import logging
import os

from aiohttp import web
from dotenv import load_dotenv

from futbot_api import services
from futbot_api.database import init_database

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("futbot.api")


@web.middleware
async def api_key_middleware(request: web.Request, handler):
    if request.path == "/health":
        return await handler(request)

    expected_key = request.app["api_key"]
    supplied_key = request.headers.get("X-API-Key", "")
    if not supplied_key or not hmac.compare_digest(supplied_key, expected_key):
        raise web.HTTPUnauthorized(
            text='{"error":"Missing or invalid API key"}',
            content_type="application/json",
        )
    return await handler(request)


def success(data, status=200):
    return web.json_response({"ok": True, "data": data}, status=status)


def error(message, status=400):
    return web.json_response({"ok": False, "error": message}, status=status)


async def read_json(request):
    try:
        payload = await request.json()
    except (ValueError, web.HTTPException):
        raise services.GameError("Request body must be valid JSON.")
    if not isinstance(payload, dict):
        raise services.GameError("Request body must be a JSON object.")
    return payload


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except services.GameError as exc:
        return error(str(exc), exc.status)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("Unhandled API error on %s %s", request.method, request.path)
        return error("Internal server error.", 500)


async def health(request):
    return success({"service": "futbot-api"})


async def status(request):
    return success({
        "service": "futbot-api",
        "api_version": 1,
        "discord_gateway_connected": False,
        "database": "initialized",
        "features": {
            "cards": True,
            "players": True,
            "inventory": True,
            "economy": True,
            "packs": True,
            "wishlists": True,
            "decks": True,
            "daily_rewards": True,
            "starter_pack": True,
            "leaderboards": True,
            "battles": True,
            "trading": False,
            "marketplace": False,
        },
    })


async def card_list(request):
    search = request.query.get("search", "").strip()[:100]
    try:
        limit = int(request.query.get("limit", "50"))
        offset = int(request.query.get("offset", "0"))
    except ValueError:
        raise services.GameError("limit and offset must be integers.")
    cards = services.search_cards(search, limit, offset)
    return success({"cards": cards, "count": len(cards), "limit": max(1, min(limit, 100)), "offset": max(0, offset)})


async def card_detail(request):
    card_id = int(request.match_info["card_id"])
    card = services.get_card(card_id)
    if card is None:
        raise services.GameError("Card not found.", 404)
    return success(card)


async def create_or_update_player(request):
    user_id = int(request.match_info["user_id"])
    payload = await read_json(request)
    name = payload.get("name")
    if not isinstance(name, str):
        raise services.GameError("name must be a string.")
    return success(services.ensure_player(user_id, name))


async def player_detail(request):
    user_id = int(request.match_info["user_id"])
    player = services.get_player(user_id)
    if player is None:
        raise services.GameError("Player not found.", 404)
    return success(player)


async def player_inventory(request):
    user_id = int(request.match_info["user_id"])
    player = services.get_player(user_id)
    if player is None:
        raise services.GameError("Player not found.", 404)
    cards = services.get_inventory(user_id)
    return success({"user_id": user_id, "cards": cards, "count": len(cards)})


async def player_packs(request):
    user_id = int(request.match_info["user_id"])
    if services.get_player(user_id) is None:
        raise services.GameError("Player not found.", 404)
    return success(services.get_packs(user_id))


async def pack_catalog(request):
    return success({
        "packs": [
            {"id": pack_id, **pack}
            for pack_id, pack in services.PACKS.items()
        ]
    })


async def buy_pack(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
        pack_id = int(payload["pack_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id and pack_id must be integers.")
    return success(services.buy_pack(user_id, pack_id))


async def open_pack(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
        pack_id = int(payload["pack_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id and pack_id must be integers.")
    return success(services.open_pack(user_id, pack_id))


async def sell_cards(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
        card_ids = [int(card_id) for card_id in payload["card_ids"]]
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id and card_ids are required.")
    return success(services.sell_cards(user_id, card_ids))


async def toggle_wishlist(request):
    user_id = int(request.match_info["user_id"])
    card_id = int(request.match_info["card_id"])
    if services.get_player(user_id) is None:
        raise services.GameError("Player not found.", 404)
    return success(services.toggle_wishlist(user_id, card_id))


async def daily_claim(request):
    user_id = int(request.match_info["user_id"])
    if services.get_player(user_id) is None:
        raise services.GameError("Player not found.", 404)
    return success(services.claim_daily(user_id))


async def daily_collect(request):
    user_id = int(request.match_info["user_id"])
    payload = await read_json(request)
    try:
        card_id = int(payload["card_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("card_id must be an integer.")
    return success(services.collect_daily(user_id, card_id))


async def starter_pack(request):
    user_id = int(request.match_info["user_id"])
    return success(services.claim_starter_pack(user_id))


async def leaderboard(request):
    stat = request.match_info["stat"]
    allowed = {
        "battles_won", "battles_played", "battles_lost", "rounds_won",
        "rounds_played", "rounds_lost", "coins", "cards_dropped", "cards_sold",
    }
    if stat not in allowed:
        raise services.GameError("Unsupported leaderboard statistic.")
    try:
        limit = max(1, min(int(request.query.get("limit", "10")), 100))
    except ValueError:
        raise services.GameError("limit must be an integer.")
    from futbot_api.database import read_connection
    with read_connection() as db:
        rows = db.execute(
            f"SELECT user_id, name, {stat} AS value FROM players ORDER BY {stat} DESC, name ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return success({"stat": stat, "entries": [
        {"rank": index + 1, **dict(row)} for index, row in enumerate(rows)
    ]})


async def create_battle(request):
    payload = await read_json(request)
    try:
        player1_id = int(payload["player1_id"])
        player2_id = int(payload["player2_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("player1_id and player2_id are required.")
    return success(services.create_battle(player1_id, player2_id), 201)


async def battle_detail(request):
    return success(services.get_battle(request.match_info["battle_id"]))


async def battle_accept(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id is required.")
    return success(services.accept_battle(request.match_info["battle_id"], user_id))


async def battle_decline(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id is required.")
    return success(services.decline_battle(request.match_info["battle_id"], user_id))


async def battle_select_deck(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
        deck_name = payload["deck_name"]
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id and deck_name are required.")
    if not isinstance(deck_name, str):
        raise services.GameError("deck_name must be a string.")
    return success(services.select_battle_deck(request.match_info["battle_id"], user_id, deck_name))


async def battle_action(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
        action = payload["action"]
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id and action are required.")
    if not isinstance(action, str):
        raise services.GameError("action must be a string.")
    return success(services.choose_battle_action(request.match_info["battle_id"], user_id, action))


async def battle_card(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
        card_id = int(payload["card_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id and card_id are required.")
    return success(services.choose_battle_card(request.match_info["battle_id"], user_id, card_id))


async def battle_next_round(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id is required.")
    return success(services.ready_next_round(request.match_info["battle_id"], user_id))


async def battle_draw(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id is required.")
    return success(services.offer_battle_draw(request.match_info["battle_id"], user_id))


async def battle_surrender(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id is required.")
    return success(services.surrender_battle(request.match_info["battle_id"], user_id))


async def player_decks(request):
    user_id = int(request.match_info["user_id"])
    if services.get_player(user_id) is None:
        raise services.GameError("Player not found.", 404)
    return success({"decks": services.get_decks(user_id)})


async def save_player_deck(request):
    user_id = int(request.match_info["user_id"])
    payload = await read_json(request)
    if services.get_player(user_id) is None:
        raise services.GameError("Player not found.", 404)
    try:
        name = payload["name"]
        card_ids = [int(card_id) for card_id in payload["card_ids"]]
    except (KeyError, TypeError, ValueError):
        raise services.GameError("name and card_ids are required.")
    if not isinstance(name, str):
        raise services.GameError("name must be a string.")
    return success(services.save_deck(user_id, name, card_ids))


def create_app(api_key: str) -> web.Application:
    app = web.Application(
        middlewares=[error_middleware, api_key_middleware],
        client_max_size=1024 * 1024,
    )
    app["api_key"] = api_key

    app.router.add_get("/health", health)
    app.router.add_get("/api/v1/status", status)
    app.router.add_get("/api/v1/cards", card_list)
    app.router.add_get("/api/v1/cards/{card_id:\\d+}", card_detail)
    app.router.add_post("/api/v1/players/{user_id:\\d+}", create_or_update_player)
    app.router.add_get("/api/v1/players/{user_id:\\d+}", player_detail)
    app.router.add_get("/api/v1/players/{user_id:\\d+}/inventory", player_inventory)
    app.router.add_get("/api/v1/players/{user_id:\\d+}/packs", player_packs)
    app.router.add_get("/api/v1/players/{user_id:\\d+}/decks", player_decks)
    app.router.add_post("/api/v1/players/{user_id:\\d+}/daily/claim", daily_claim)
    app.router.add_post("/api/v1/players/{user_id:\\d+}/daily/collect", daily_collect)
    app.router.add_post("/api/v1/players/{user_id:\\d+}/starter-pack", starter_pack)
    app.router.add_get("/api/v1/leaderboards/{stat}", leaderboard)
    app.router.add_post("/api/v1/battles", create_battle)
    app.router.add_get("/api/v1/battles/{battle_id}", battle_detail)
    app.router.add_post("/api/v1/battles/{battle_id}/accept", battle_accept)
    app.router.add_post("/api/v1/battles/{battle_id}/decline", battle_decline)
    app.router.add_post("/api/v1/battles/{battle_id}/decks", battle_select_deck)
    app.router.add_post("/api/v1/battles/{battle_id}/action", battle_action)
    app.router.add_post("/api/v1/battles/{battle_id}/card", battle_card)
    app.router.add_post("/api/v1/battles/{battle_id}/next", battle_next_round)
    app.router.add_post("/api/v1/battles/{battle_id}/draw", battle_draw)
    app.router.add_post("/api/v1/battles/{battle_id}/surrender", battle_surrender)
    app.router.add_post("/api/v1/players/{user_id:\\d+}/decks", save_player_deck)
    app.router.add_post("/api/v1/players/{user_id:\\d+}/wishlist/{card_id:\\d+}", toggle_wishlist)
    app.router.add_get("/api/v1/packs/catalog", pack_catalog)
    app.router.add_post("/api/v1/packs/buy", buy_pack)
    app.router.add_post("/api/v1/packs/open", open_pack)
    app.router.add_post("/api/v1/inventory/sell", sell_cards)
    return app


def main():
    api_key = os.getenv("FUTBOT_API_KEY")
    if not api_key:
        raise RuntimeError("FUTBOT_API_KEY is required. Set it in the environment.")

    init_database()
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", os.getenv("API_PORT", "8000")))
    logger.info("Starting FutBot API on %s:%s", host, port)
    web.run_app(create_app(api_key), host=host, port=port)


if __name__ == "__main__":
    main()
