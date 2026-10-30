"""
FutBot HTTP API.

This service owns FutBot game data and rules. It deliberately does not import
bot.py, create a Discord client, or require a Discord bot token.
"""

import hmac
import logging
import asyncio
import base64
import binascii
import os
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv

from futbot_api import services
from futbot_api.database import init_database
from futbot_api.images import generate_minted_card

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
            "card_drops": True,
            "secret_rewards": True,
            "admin_management": True,
            "leaderboards": True,
            "battles": True,
            "trading": True,
            "trade_up": True,
            "marketplace": True,
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


def resolve_asset_path(image_path):
    if not image_path:
        raise services.GameError("This card does not have an image path.", 404)
    asset_root = Path(os.getenv("FUTBOT_ASSET_DIR", ".")).expanduser().resolve()
    candidate = Path(image_path)
    if not candidate.is_absolute():
        candidate = asset_root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(asset_root):
        raise services.GameError("Card image path is outside the configured asset directory.", 403)
    if resolved.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif") or not resolved.is_file():
        raise services.GameError("Card image file was not found on the API service.", 404)
    return resolved


async def card_image(request):
    card = services.get_card(int(request.match_info["card_id"]))
    if card is None:
        raise services.GameError("Card not found.", 404)
    return web.FileResponse(resolve_asset_path(card.get("image_path")))


async def card_weight(request):
    search = request.query.get("search", "")
    return success(services.get_card_weight(search))


async def owned_card_copies(request):
    user_id = int(request.match_info["user_id"])
    card_id = int(request.match_info["card_id"])
    copies = services.get_owned_card_copies(user_id, card_id)
    if not copies:
        raise services.GameError("This player does not own that card.", 404)
    return success({"copies": copies, "count": len(copies)})


async def minted_card_image(request):
    user_id = int(request.match_info["user_id"])
    card_id = int(request.match_info["card_id"])
    payload = await read_json(request)
    try:
        edition = int(payload["edition"])
        username = payload["username"]
    except (KeyError, TypeError, ValueError):
        raise services.GameError("edition and username are required.")
    if not isinstance(username, str) or not username.strip():
        raise services.GameError("username must be a non-empty string.")
    avatar_bytes = None
    encoded_avatar = payload.get("avatar_base64")
    if encoded_avatar:
        if not isinstance(encoded_avatar, str) or len(encoded_avatar) > 2_500_000:
            raise services.GameError("avatar_base64 is invalid or too large.")
        try:
            avatar_bytes = base64.b64decode(encoded_avatar, validate=True)
        except (ValueError, binascii.Error):
            raise services.GameError("avatar_base64 is not valid base64.")
    copies = services.get_owned_card_copies(user_id, card_id)
    selected = next((copy for copy in copies if int(copy["edition"]) == edition), None)
    if selected is None:
        raise services.GameError("The selected card edition is not owned by this player.", 404)
    path = resolve_asset_path(selected.get("image_path"))
    total_copies = max(1, int(selected.get("copies") or 1))
    edition_text = f"#{edition}/{total_copies}"
    try:
        rendered = await asyncio.to_thread(
            generate_minted_card, str(path), avatar_bytes, username[:80], edition_text
        )
    except ValueError as exc:
        raise services.GameError(str(exc), 500)
    return web.Response(body=rendered, content_type="image/png")


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
    try:
        limit = int(request.query.get("limit", "10"))
        raw_ids = request.query.get("user_ids")
        user_ids = [int(value) for value in raw_ids.split(",") if value] if raw_ids is not None else None
    except ValueError:
        raise services.GameError("limit and user_ids must contain valid integers.")
    return success(services.leaderboard_entries(stat, limit, user_ids))


async def player_stats(request):
    return success(services.get_player_stats(int(request.match_info["user_id"])))


async def player_wishlist(request):
    user_id = int(request.match_info["user_id"])
    return success({"cards": services.get_player_wishlist(user_id)})


async def player_achievements(request):
    user_id = int(request.match_info["user_id"])
    return success({"achievements": services.get_achievements(user_id)})


async def equip_title(request):
    user_id = int(request.match_info["user_id"])
    payload = await read_json(request)
    try:
        achievement_id = int(payload["achievement_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("achievement_id is required.")
    return success(services.equip_title(user_id, achievement_id))


async def friend_list(request):
    user_id = int(request.match_info["user_id"])
    return success({"friends": services.list_friends(user_id)})


async def friend_requests(request):
    user_id = int(request.match_info["user_id"])
    return success({"requests": services.list_friend_requests(user_id)})


async def friend_request_create(request):
    payload = await read_json(request)
    try:
        requester_id = int(payload["requester_id"])
        recipient_id = int(payload["recipient_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("requester_id and recipient_id are required.")
    return success(services.add_friend_request(requester_id, recipient_id), 201)


async def friend_request_resolve(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id is required.")
    accept = request.match_info["decision"] == "accept"
    return success(services.resolve_friend_request(int(request.match_info["request_id"]), user_id, accept))


async def friend_remove(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
        friend_id = int(payload["friend_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id and friend_id are required.")
    return success(services.remove_friend(user_id, friend_id))


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


def require_admin(admin_id: int):
    configured = os.getenv("FUTBOT_ADMIN_IDS", os.getenv("ADMIN_IDS", ""))
    admin_ids = {int(value.strip()) for value in configured.split(",") if value.strip().isdigit()}
    if admin_id not in admin_ids:
        raise services.GameError("You are not authorized to use this admin action.", 403)


async def admin_give_coins(request):
    payload = await read_json(request)
    try:
        admin_id = int(payload["admin_id"])
        user_id = int(payload["user_id"])
        amount = int(payload["amount"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("admin_id, user_id and amount are required integers.")
    require_admin(admin_id)
    return success(services.admin_give_coins(user_id, amount))


async def admin_give_card(request):
    payload = await read_json(request)
    try:
        admin_id = int(payload["admin_id"])
        user_id = int(payload["user_id"])
        card_id = int(payload["card_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("admin_id, user_id and card_id are required integers.")
    require_admin(admin_id)
    return success(services.admin_give_card(user_id, card_id))


async def admin_remove_card(request):
    payload = await read_json(request)
    try:
        admin_id = int(payload["admin_id"])
        user_id = int(payload["user_id"])
        card_id = int(payload["card_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("admin_id, user_id and card_id are required integers.")
    require_admin(admin_id)
    return success(services.admin_remove_card(user_id, card_id))


async def secret_reward(request):
    user_id = int(request.match_info["user_id"])
    secret_name = request.match_info["secret"]
    return success(services.claim_secret_reward(user_id, secret_name))


async def trade_up_eligible(request):
    user_id = int(request.match_info["user_id"])
    tier = request.match_info["tier"]
    cards = services.eligible_trade_up_cards(user_id, tier)
    return success({"tier": tier, "cards": cards, "count": len(cards)})


async def trade_up_execute(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
        tier = payload["tier"]
        row_ids = payload["inventory_row_ids"]
        if not isinstance(row_ids, list):
            raise TypeError()
        row_ids = [int(value) for value in row_ids]
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id, tier and inventory_row_ids are required.")
    if not isinstance(tier, str):
        raise services.GameError("tier must be a string.")
    return success(services.execute_trade_up(user_id, tier, row_ids))


async def market_browse(request):
    search = request.query.get("search", "").strip()[:100]
    try:
        page = int(request.query.get("page", "0"))
        page_size = int(request.query.get("page_size", "5"))
    except ValueError:
        raise services.GameError("page and page_size must be integers.")
    return success(services.browse_market(search, page, page_size))


async def market_create(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
        card_id = int(payload["card_id"])
        edition = int(payload["edition"])
        price = int(payload["price"])
        duration_hours = int(payload["duration_hours"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id, card_id, edition, price and duration_hours are required integers.")
    return success(services.create_market_listing(user_id, card_id, edition, price, duration_hours), 201)


async def market_buy(request):
    payload = await read_json(request)
    try:
        buyer_id = int(payload["buyer_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("buyer_id is required.")
    try:
        result = services.buy_market_listing(int(request.match_info["listing_id"]), buyer_id)
    except ValueError:
        raise services.GameError("listing_id must be an integer.")
    return success(result)


async def market_cancel(request):
    payload = await read_json(request)
    try:
        seller_id = int(payload["seller_id"])
        listing_id = int(request.match_info["listing_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("seller_id and a valid listing_id are required.")
    return success(services.cancel_market_listing(listing_id, seller_id))


async def market_listed(request):
    try:
        user_id = int(request.match_info["user_id"])
    except ValueError:
        raise services.GameError("user_id must be an integer.")
    return success({"listings": services.get_player_listings(user_id)})


async def market_expire(request):
    return success(services.expire_market_listings())


async def create_drop(request):
    payload = await read_json(request)
    owner_id = payload.get("owner_id")
    if owner_id is not None:
        try:
            owner_id = int(owner_id)
        except (TypeError, ValueError):
            raise services.GameError("owner_id must be an integer or null.")
    return success(services.create_card_drop(owner_id), 201)


async def drop_detail(request):
    return success(services.get_card_drop(request.match_info["drop_id"]))


async def drop_collect(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id is required.")
    username = payload.get("username", str(user_id))
    if not isinstance(username, str):
        raise services.GameError("username must be a string.")
    return success(services.collect_card_drop(request.match_info["drop_id"], user_id, username))


async def create_trade(request):
    payload = await read_json(request)
    try:
        values = [int(payload[key]) for key in ("sender_id", "recipient_id", "sender_card_id", "recipient_card_id")]
    except (KeyError, TypeError, ValueError):
        raise services.GameError("sender_id, recipient_id, sender_card_id and recipient_card_id are required.")
    return success(services.create_trade_offer(*values), 201)


async def trade_detail(request):
    return success(services.get_trade_offer(request.match_info["trade_id"]))


async def trade_accept(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id is required.")
    return success(services.resolve_trade_offer(request.match_info["trade_id"], user_id, True))


async def trade_decline(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id is required.")
    return success(services.resolve_trade_offer(request.match_info["trade_id"], user_id, False))


async def create_exchange(request):
    payload = await read_json(request)
    try:
        p1 = int(payload["player1_id"])
        p2 = int(payload["player2_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("player1_id and player2_id are required.")
    return success(services.create_exchange(p1, p2), 201)


async def exchange_detail(request):
    return success(services.get_exchange(request.match_info["session_id"]))


async def exchange_offer(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
        card_ids = payload.get("card_ids", [])
        coins = int(payload.get("coins", 0))
        if not isinstance(card_ids, list):
            raise TypeError()
        card_ids = [int(card_id) for card_id in card_ids]
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id, a card_ids array, and an integer coins value are required.")
    return success(services.update_exchange_offer(request.match_info["session_id"], user_id, card_ids, coins))


async def exchange_action(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id is required.")
    action = request.match_info["action"]
    if action == "lock":
        result = services.lock_exchange(request.match_info["session_id"], user_id)
    elif action == "confirm":
        result = services.confirm_exchange(request.match_info["session_id"], user_id)
    elif action == "cancel":
        result = services.cancel_exchange(request.match_info["session_id"], user_id)
    else:
        raise services.GameError("Unknown exchange action.")
    return success(result)


async def delete_player_deck(request):
    user_id = int(request.match_info["user_id"])
    payload = await read_json(request)
    name = payload.get("name")
    if not isinstance(name, str):
        raise services.GameError("name must be a string.")
    return success(services.delete_deck(user_id, name))


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
        client_max_size=4 * 1024 * 1024,
    )
    app["api_key"] = api_key

    app.router.add_get("/health", health)
    app.router.add_get("/api/v1/status", status)
    app.router.add_get("/api/v1/cards", card_list)
    app.router.add_get("/api/v1/cards/weight", card_weight)
    app.router.add_get("/api/v1/cards/{card_id:\\d+}", card_detail)
    app.router.add_get("/api/v1/cards/{card_id:\\d+}/image", card_image)
    app.router.add_post("/api/v1/players/{user_id:\\d+}", create_or_update_player)
    app.router.add_get("/api/v1/players/{user_id:\\d+}", player_detail)
    app.router.add_get("/api/v1/players/{user_id:\\d+}/stats", player_stats)
    app.router.add_get("/api/v1/players/{user_id:\\d+}/wishlist", player_wishlist)
    app.router.add_get("/api/v1/players/{user_id:\\d+}/achievements", player_achievements)
    app.router.add_post("/api/v1/players/{user_id:\\d+}/title", equip_title)
    app.router.add_get("/api/v1/players/{user_id:\\d+}/friends", friend_list)
    app.router.add_get("/api/v1/players/{user_id:\\d+}/friend-requests", friend_requests)
    app.router.add_post("/api/v1/friends/requests", friend_request_create)
    app.router.add_post("/api/v1/friends/requests/{request_id:\\d+}/{decision:accept|decline}", friend_request_resolve)
    app.router.add_post("/api/v1/friends/remove", friend_remove)
    app.router.add_get("/api/v1/players/{user_id:\\d+}/inventory", player_inventory)
    app.router.add_get("/api/v1/players/{user_id:\\d+}/cards/{card_id:\\d+}/copies", owned_card_copies)
    app.router.add_post("/api/v1/players/{user_id:\\d+}/cards/{card_id:\\d+}/minted", minted_card_image)
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
    app.router.add_post("/api/v1/admin/coins", admin_give_coins)
    app.router.add_post("/api/v1/admin/cards/give", admin_give_card)
    app.router.add_post("/api/v1/admin/cards/remove", admin_remove_card)
    app.router.add_post("/api/v1/players/{user_id:\\d+}/secrets/{secret}", secret_reward)
    app.router.add_get("/api/v1/players/{user_id:\\d+}/trade-up/{tier}", trade_up_eligible)
    app.router.add_post("/api/v1/trade-up", trade_up_execute)
    app.router.add_get("/api/v1/market/listings", market_browse)
    app.router.add_post("/api/v1/market/listings", market_create)
    app.router.add_post("/api/v1/market/listings/{listing_id}/buy", market_buy)
    app.router.add_post("/api/v1/market/listings/{listing_id}/cancel", market_cancel)
    app.router.add_get("/api/v1/players/{user_id:\\d+}/market/listings", market_listed)
    app.router.add_post("/api/v1/market/expire", market_expire)
    app.router.add_post("/api/v1/drops", create_drop)
    app.router.add_get("/api/v1/drops/{drop_id}", drop_detail)
    app.router.add_post("/api/v1/drops/{drop_id}/collect", drop_collect)
    app.router.add_post("/api/v1/trades", create_trade)
    app.router.add_get("/api/v1/trades/{trade_id}", trade_detail)
    app.router.add_post("/api/v1/trades/{trade_id}/accept", trade_accept)
    app.router.add_post("/api/v1/trades/{trade_id}/decline", trade_decline)
    app.router.add_post("/api/v1/exchanges", create_exchange)
    app.router.add_get("/api/v1/exchanges/{session_id}", exchange_detail)
    app.router.add_put("/api/v1/exchanges/{session_id}/offer", exchange_offer)
    app.router.add_post("/api/v1/exchanges/{session_id}/{action:lock|confirm|cancel}", exchange_action)
    app.router.add_post("/api/v1/players/{user_id:\\d+}/decks", save_player_deck)
    app.router.add_delete("/api/v1/players/{user_id:\\d+}/decks", delete_player_deck)
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
