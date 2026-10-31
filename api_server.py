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


async def player_lineup_image(request):
    user_id = int(request.match_info["user_id"])
    lineup = services.get_lineup(user_id)
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFont

    # Arrange the saved formation as a vertical football pitch:
    # attackers at the top, then midfield, defenders, and the goalkeeper.
    card_width, card_height = 420, 588
    label_height = 460
    gap_x, padding_x = 56, 100
    row_gap, top_padding = 48, 92
    row_order = ("att", "mid", "def", "gk")
    position_rows = {
        "GK": "gk",
        "LB": "def", "LCB": "def", "CB": "def", "RCB": "def", "RB": "def",
        "LWB": "def", "RWB": "def",
        "CDM": "mid", "CM": "mid", "CAM": "mid", "LM": "mid", "RM": "mid",
        "LW": "att", "ST": "att", "CF": "att", "RW": "att",
    }
    left_to_right = {
        "LB": 0, "LWB": 1, "LCB": 2, "CB": 3, "RCB": 4, "RWB": 5, "RB": 6,
        "LM": 0, "CDM": 1, "CM": 2, "CAM": 3, "RM": 4,
        "LW": 0, "ST": 1, "CF": 2, "RW": 3,
        "GK": 0,
    }
    rows = {name: [] for name in row_order}
    for slot in lineup["slots"]:
        position = str(slot.get("position") or "").upper()
        row_name = position_rows.get(position, "mid")
        rows[row_name].append(slot)
    for row_name in row_order:
        rows[row_name].sort(
            key=lambda item: (left_to_right.get(str(item.get("position") or "").upper(), 99), int(item["slot"]))
        )

    max_players = max(1, *(len(row) for row in rows.values()))
    canvas_width = max(1600, padding_x * 2 + max_players * card_width + (max_players - 1) * gap_x)
    row_height = card_height + label_height
    canvas_height = top_padding + len(row_order) * row_height + (len(row_order) - 1) * row_gap + 38
    canvas = Image.new("RGB", (canvas_width, canvas_height), (30, 105, 66))
    draw = ImageDraw.Draw(canvas)

    try:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        position_font = ImageFont.truetype(font_path, 200)
        name_font = ImageFont.truetype(font_path, 80)
        small_font = ImageFont.truetype(font_path, 72)
        badge_font = ImageFont.truetype(font_path, 32)
        title_font = ImageFont.truetype(font_path, 42)
    except OSError:
        position_font = ImageFont.load_default(size=200)
        name_font = ImageFont.load_default(size=80)
        small_font = ImageFont.load_default(size=72)
        badge_font = ImageFont.load_default(size=32)
        title_font = ImageFont.load_default(size=42)

    # Pitch markings.
    draw.rectangle((14, 14, canvas_width - 14, canvas_height - 14), outline=(220, 242, 225), width=3)
    draw.line((14, canvas_height // 2, canvas_width - 14, canvas_height // 2), fill=(220, 242, 225), width=2)
    draw.ellipse((canvas_width // 2 - 75, canvas_height // 2 - 75,
                  canvas_width // 2 + 75, canvas_height // 2 + 75), outline=(220, 242, 225), width=2)
    draw.rectangle((canvas_width // 2 - 180, 14, canvas_width // 2 + 180, 115),
                   outline=(220, 242, 225), width=2)
    draw.rectangle((canvas_width // 2 - 180, canvas_height - 115,
                    canvas_width // 2 + 180, canvas_height - 14), outline=(220, 242, 225), width=2)
    title = f"{lineup.get('formation', '2-2-3')} FORMATION"
    title_box = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((canvas_width - (title_box[2] - title_box[0])) // 2, 30),
              title, font=title_font, fill=(255, 255, 255))

    for row_index, row_name in enumerate(row_order):
        row = rows[row_name]
        y = top_padding + row_index * (row_height + row_gap)
        count = len(row)
        total_width = count * card_width + max(0, count - 1) * gap_x
        # Center even a single player/card on the pitch.
        start_x = (canvas_width - total_width) // 2 - 12
        for index, slot in enumerate(row):
            x = start_x + index * (card_width + gap_x)
            card = slot.get("card")
            if card and card.get("image_path"):
                try:
                    with Image.open(resolve_asset_path(card["image_path"])) as source:
                        artwork = source.convert("RGB")
                    artwork.thumbnail((card_width, card_height), Image.Resampling.LANCZOS)
                    card_image = Image.new("RGB", (card_width, card_height), (38, 41, 50))
                    card_image.paste(artwork, ((card_width - artwork.width) // 2,
                                               (card_height - artwork.height) // 2))
                    canvas.paste(card_image, (x, y))
                    penalty = int(card.get("position_penalty") or 0)
                    if penalty > 0:
                        badge_text = f"-{penalty}"
                        bounds = draw.textbbox((0, 0), badge_text, font=badge_font)
                        badge_w = bounds[2] - bounds[0] + 20
                        badge_h = bounds[3] - bounds[1] + 14
                        bx, by = x + card_width - badge_w - 6, y + 6
                        draw.rounded_rectangle((bx, by, bx + badge_w, by + badge_h),
                                               radius=9, fill=(210, 35, 45),
                                               outline=(255, 255, 255), width=2)
                        draw.text((bx + 10, by + 5), badge_text,
                                  font=badge_font, fill=(255, 255, 255))
                except Exception:
                    card = None
            else:
                card = None

            position_label = f"{slot['position']} ({slot['slot']})"
            if card:
                name_label = str(card.get("name") or "Unknown")[:23]
                rating_label = f"{card.get('effective_overall', card.get('overall', '—'))} OVR"
            else:
                name_label = "Empty"
                rating_label = " "

            # Align labels with the card itself; remove the previous rightward offset.
            label_center_x = x + card_width // 2
            position_box = draw.textbbox((0, 0), position_label, font=position_font)
            name_box = draw.textbbox((0, 0), name_label, font=name_font)
            rating_box = draw.textbbox((0, 0), rating_label, font=small_font)
            draw.text((label_center_x - (position_box[2] - position_box[0]) // 2,
                       y + card_height + 8), position_label,
                      font=position_font, fill=(255, 255, 255),
                      stroke_width=1, stroke_fill=(20, 45, 30))
            draw.text((label_center_x - (name_box[2] - name_box[0]) // 2,
                       y + card_height + 230), name_label,
                      font=name_font, fill=(255, 255, 255))
            draw.text((label_center_x - (rating_box[2] - rating_box[0]) // 2,
                       y + card_height + 320), rating_label,
                      font=small_font, fill=(235, 245, 235))

    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return web.Response(body=output.getvalue(), content_type="image/png")


async def card_compare_image(request):
    raw_ids = request.query.get("ids", "")
    try:
        card_ids = [int(value.strip()) for value in raw_ids.split(",") if value.strip()]
    except ValueError:
        raise services.GameError("ids must be comma-separated card IDs.")
    if len(card_ids) != 2 or card_ids[0] == card_ids[1]:
        raise services.GameError("Provide exactly two different card IDs.")
    from io import BytesIO
    from PIL import Image
    cards = [services.get_card(card_id) for card_id in card_ids]
    if any(card is None for card in cards):
        raise services.GameError("One or both cards were not found.", 404)
    images = []
    for card in cards:
        try:
            with Image.open(resolve_asset_path(card.get("image_path"))) as source:
                images.append(source.convert("RGB").copy())
        except services.GameError:
            raise
        except Exception as exc:
            raise services.GameError("A card image could not be opened.", 404) from exc
    target_width, target_height, gap = 600, 840, 16
    canvas = Image.new("RGB", (target_width * 2 + gap, target_height), (22, 25, 35))
    for index, image in enumerate(images):
        image.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
        x = index * (target_width + gap) + (target_width - image.width) // 2
        y = (target_height - image.height) // 2
        canvas.paste(image, (x, y))
    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return web.Response(body=output.getvalue(), content_type="image/png")


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


async def battle_select_lineup(request):
    payload = await read_json(request)
    try:
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("user_id is required.")
    return success(services.select_battle_lineup(request.match_info["battle_id"], user_id))


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


async def admin_wipe_cards(request):
    payload = await read_json(request)
    try:
        admin_id = int(payload["admin_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("admin_id is required.")
    require_admin(admin_id)
    if payload.get("confirmation") != "WIPE ALL CARDS":
        raise services.GameError('Type the exact confirmation "WIPE ALL CARDS" to continue.', 400)
    result = services.wipe_all_catalog_cards()
    asset_root = Path(os.getenv("FUTBOT_ASSET_DIR", ".")).expanduser().resolve()
    card_dir = asset_root / "cards"
    if card_dir.exists():
        import shutil
        shutil.rmtree(card_dir)
    return success(result)


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



def _store_admin_card_image(payload: dict, card_id: int):
    encoded = payload.get("image_base64")
    if not encoded:
        return None
    if not isinstance(encoded, str) or len(encoded) > 4_200_000:
        raise services.GameError("Image is missing, invalid, or larger than 3 MB.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise services.GameError("image_base64 must be valid base64.")
    if not raw or len(raw) > 3 * 1024 * 1024:
        raise services.GameError("Image must be between 1 byte and 3 MB.")
    try:
        from PIL import Image
        from io import BytesIO
        image = Image.open(BytesIO(raw))
        image.verify()
        image_format = (image.format or "").upper()
    except Exception:
        raise services.GameError("The uploaded file is not a valid image.")
    extensions = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "GIF": ".gif"}
    extension = extensions.get(image_format)
    if not extension:
        raise services.GameError("Use a PNG, JPG, WEBP, or GIF image.")
    asset_root = Path(os.getenv("FUTBOT_ASSET_DIR", ".")).expanduser().resolve()
    card_dir = asset_root / "cards"
    card_dir.mkdir(parents=True, exist_ok=True)
    destination = card_dir / f"{card_id}{extension}"
    destination.write_bytes(raw)
    return f"cards/{card_id}{extension}"


async def admin_create_catalog_card(request):
    payload = await read_json(request)
    try:
        admin_id = int(payload["admin_id"])
        card_id = int(payload["card_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("admin_id and card_id are required integers.")
    require_admin(admin_id)
    payload.pop("image_path", None)
    image_path = _store_admin_card_image(payload, card_id)
    if not image_path:
        raise services.GameError("Attach a card image to create this card.")
    payload["image_path"] = image_path
    return success(services.create_catalog_card(payload), 201)


async def admin_replace_catalog_card(request):
    payload = await read_json(request)
    try:
        admin_id = int(payload["admin_id"])
        card_id = int(request.match_info["card_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("admin_id and a valid card ID are required.")
    require_admin(admin_id)
    payload.pop("image_path", None)
    image_path = _store_admin_card_image(payload, card_id)
    if image_path:
        payload["image_path"] = image_path
    payload.pop("image_base64", None)
    return success(services.update_catalog_card(card_id, payload))


async def admin_delete_catalog_card(request):
    payload = await read_json(request)
    try:
        admin_id = int(payload["admin_id"])
        card_id = int(request.match_info["card_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("admin_id and a valid card ID are required.")
    require_admin(admin_id)
    return success(services.delete_catalog_card(card_id))


async def admin_import_catalog_cards(request):
    payload = await read_json(request)
    try:
        admin_id = int(payload["admin_id"])
    except (KeyError, TypeError, ValueError):
        raise services.GameError("admin_id is required.")
    require_admin(admin_id)
    cards = payload.get("cards")
    if not isinstance(cards, list) or not cards or len(cards) > 50:
        raise services.GameError("Provide between 1 and 50 cards in the cards array.")
    results = []
    for item in cards:
        if not isinstance(item, dict):
            raise services.GameError("Every cards entry must be an object.")
        try:
            card_id = int(item["card_id"])
        except (KeyError, TypeError, ValueError):
            raise services.GameError("Every card needs an integer card_id.")
        item.pop("image_path", None)
        image_path = _store_admin_card_image(item, card_id)
        if not image_path:
            raise services.GameError(f"Card {card_id}: include an image_base64 value.")
        item["image_path"] = image_path
        item.pop("image_base64", None)
        try:
            if item.pop("replace_existing", False):
                result = services.update_catalog_card(card_id, item)
                action = "replaced"
            else:
                result = services.create_catalog_card(item)
                action = "created"
        except services.GameError as exc:
            raise services.GameError(f"Card {card_id}: {exc}", exc.status)
        results.append({"card_id": card_id, "name": result.get("name"), "action": action})
    return success({"imported": len(results), "cards": results}, 201)

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



async def player_lineup(request):
    return success(services.get_lineup(int(request.match_info["user_id"])))

async def add_player_lineup(request):
    payload = await read_json(request)
    return success(services.add_lineup_player(int(request.match_info["user_id"]), payload.get("player", ""), int(payload.get("slot", 0))))

async def remove_player_lineup(request):
    payload = await read_json(request)
    return success(services.remove_lineup_player(int(request.match_info["user_id"]), payload.get("player", ""), int(payload.get("slot", 0))))

async def set_player_formation(request):
    payload = await read_json(request)
    return success(services.set_lineup_formation(int(request.match_info["user_id"]), payload.get("formation", "")))

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
        client_max_size=16 * 1024 * 1024,
    )
    app["api_key"] = api_key

    app.router.add_get("/health", health)
    app.router.add_get("/api/v1/status", status)
    app.router.add_get("/api/v1/cards", card_list)
    app.router.add_get("/api/v1/cards/compare-image", card_compare_image)
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
    app.router.add_get("/api/v1/players/{user_id:\\d+}/lineup", player_lineup)
    app.router.add_get("/api/v1/players/{user_id:\\d+}/lineup/image", player_lineup_image)
    app.router.add_post("/api/v1/players/{user_id:\\d+}/lineup/add", add_player_lineup)
    app.router.add_post("/api/v1/players/{user_id:\\d+}/lineup/remove", remove_player_lineup)
    app.router.add_put("/api/v1/players/{user_id:\\d+}/lineup/formation", set_player_formation)
    app.router.add_get("/api/v1/players/{user_id:\\d+}/decks", player_decks)
    app.router.add_post("/api/v1/players/{user_id:\\d+}/daily/claim", daily_claim)
    app.router.add_post("/api/v1/players/{user_id:\\d+}/daily/collect", daily_collect)
    app.router.add_post("/api/v1/players/{user_id:\\d+}/starter-pack", starter_pack)
    app.router.add_get("/api/v1/leaderboards/{stat}", leaderboard)
    app.router.add_post("/api/v1/battles", create_battle)
    app.router.add_get("/api/v1/battles/{battle_id}", battle_detail)
    app.router.add_post("/api/v1/battles/{battle_id}/accept", battle_accept)
    app.router.add_post("/api/v1/battles/{battle_id}/decline", battle_decline)
    app.router.add_post("/api/v1/battles/{battle_id}/lineup", battle_select_lineup)
    app.router.add_post("/api/v1/battles/{battle_id}/action", battle_action)
    app.router.add_post("/api/v1/battles/{battle_id}/card", battle_card)
    app.router.add_post("/api/v1/battles/{battle_id}/next", battle_next_round)
    app.router.add_post("/api/v1/battles/{battle_id}/draw", battle_draw)
    app.router.add_post("/api/v1/battles/{battle_id}/surrender", battle_surrender)
    app.router.add_post("/api/v1/admin/cards/wipe", admin_wipe_cards)
    app.router.add_post("/api/v1/admin/coins", admin_give_coins)
    app.router.add_post("/api/v1/admin/cards/give", admin_give_card)
    app.router.add_post("/api/v1/admin/cards/remove", admin_remove_card)
    app.router.add_post("/api/v1/admin/cards/catalog", admin_create_catalog_card)
    app.router.add_put("/api/v1/admin/cards/catalog/{card_id:\\d+}", admin_replace_catalog_card)
    app.router.add_delete("/api/v1/admin/cards/catalog/{card_id:\\d+}", admin_delete_catalog_card)
    app.router.add_post("/api/v1/admin/cards/import", admin_import_catalog_cards)
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


def seed_test_cards():
    """Optionally add test cards when FUTBOT_SEED_TEST_CARDS=true."""
    if os.getenv("FUTBOT_SEED_TEST_CARDS", "").lower() != "true":
        return
    from PIL import Image, ImageDraw
    from PIL import ImageFont

    test_cards = [
        {"card_id": 10002, "name": "Kylian Mbappe", "attack": 98, "defense": 40, "speed": 96, "overall": 96, "card_rarity": "Rare", "card_type": "Standard", "height": "178", "club": "Real Madrid", "position": "ST", "league": "La Liga", "nation": "France"},
        {"card_id": 10003, "name": "Vinicius Junior", "attack": 97, "defense": 36, "speed": 95, "overall": 94, "card_rarity": "Rare", "card_type": "Standard", "height": "176", "club": "Real Madrid", "position": "LW", "league": "La Liga", "nation": "Brazil"},
        {"card_id": 10004, "name": "Jude Bellingham", "attack": 90, "defense": 87, "speed": 84, "overall": 95, "card_rarity": "Rare", "card_type": "Standard", "height": "186", "club": "Real Madrid", "position": "CM", "league": "La Liga", "nation": "England"},
        {"card_id": 10005, "name": "Ruben Dias", "attack": 50, "defense": 95, "speed": 76, "overall": 93, "card_rarity": "Rare", "card_type": "Standard", "height": "187", "club": "Manchester City", "position": "CB", "league": "Premier League", "nation": "Portugal"},
        {"card_id": 10006, "name": "Mohamed Salah", "attack": 96, "defense": 45, "speed": 91, "overall": 93, "card_rarity": "Rare", "card_type": "Standard", "height": "175", "club": "Liverpool", "position": "RW", "league": "Premier League", "nation": "Egypt"},
    ]

    # A broad sample catalog for testing every pack tier. Ratings are custom game ratings.
    extra_players = [
        # Standard: low (70-79), mid (80-85), and high (86-95).
        ("Jordan Pickford", 79, 20, 78, 70, "GK", "Everton", "Premier League", "England", "185"),
        ("Harry Maguire", 77, 48, 79, 55, "CB", "Manchester United", "Premier League", "England", "194"),
        ("Mason Mount", 78, 76, 65, 78, "CM", "Manchester United", "Premier League", "England", "181"),
        ("Christian Pulisic", 79, 78, 45, 86, "LW", "AC Milan", "Serie A", "USA", "177"),
        ("Kai Havertz", 78, 76, 62, 73, "ST", "Arsenal", "Premier League", "Germany", "193"),
        ("Antony", 74, 72, 35, 82, "RW", "Real Betis", "La Liga", "Brazil", "174"),
        ("Richarlison", 77, 78, 48, 75, "ST", "Tottenham", "Premier League", "Brazil", "184"),
        ("Mason Greenwood", 79, 80, 38, 82, "RW", "Marseille", "Ligue 1", "England", "181"),
        ("Cody Gakpo", 79, 80, 48, 79, "LW", "Liverpool", "Premier League", "Netherlands", "193"),
        ("Nicolas Jackson", 76, 77, 35, 83, "ST", "Chelsea", "Premier League", "Senegal", "187"),
        ("Jadon Sancho", 78, 78, 35, 85, "LW", "Chelsea", "Premier League", "England", "180"),
        ("Joao Felix", 79, 80, 42, 82, "CF", "Chelsea", "Premier League", "Portugal", "181"),
        ("Alexis Mac Allister", 79, 75, 72, 72, "CM", "Liverpool", "Premier League", "Argentina", "176"),
        ("Rasmus Hojlund", 77, 78, 40, 82, "ST", "Manchester United", "Premier League", "Denmark", "191"),
        ("Dominik Szoboszlai", 79, 78, 68, 83, "CM", "Liverpool", "Premier League", "Hungary", "186"),
        ("Ollie Watkins", 84, 86, 45, 84, "ST", "Aston Villa", "Premier League", "England", "180"),
        ("Declan Rice", 85, 70, 85, 78, "CDM", "Arsenal", "Premier League", "England", "188"),
        ("Bruno Fernandes", 85, 88, 70, 75, "CAM", "Manchester United", "Premier League", "Portugal", "179"),
        ("Lautaro Martinez", 85, 88, 48, 83, "ST", "Inter Milan", "Serie A", "Argentina", "174"),
        ("Federico Valverde", 85, 82, 82, 90, "CM", "Real Madrid", "La Liga", "Uruguay", "182"),
        ("Son Heung-min", 85, 88, 42, 86, "LW", "Tottenham", "Premier League", "South Korea", "183"),
        ("Bernardo Silva", 85, 82, 70, 78, "CAM", "Manchester City", "Premier League", "Portugal", "173"),
        ("Rodri", 85, 70, 88, 65, "CDM", "Manchester City", "Premier League", "Spain", "191"),
        ("Rafael Leao", 84, 86, 40, 91, "LW", "AC Milan", "Serie A", "Portugal", "188"),
        ("Victor Osimhen", 84, 88, 42, 87, "ST", "Galatasaray", "Super Lig", "Nigeria", "186"),
        ("Bukayo Saka", 88, 90, 62, 88, "RW", "Arsenal", "Premier League", "England", "178"),
        ("Erling Haaland", 91, 96, 45, 89, "ST", "Manchester City", "Premier League", "Norway", "195"),
        ("Kevin De Bruyne", 91, 94, 70, 75, "CM", "Manchester City", "Premier League", "Belgium", "181"),
        ("Harry Kane", 90, 95, 50, 72, "ST", "Bayern Munich", "Bundesliga", "England", "188"),
        ("Robert Lewandowski", 89, 94, 45, 78, "ST", "Barcelona", "La Liga", "Poland", "185"),
        ("Virgil van Dijk", 90, 65, 94, 78, "CB", "Liverpool", "Premier League", "Netherlands", "193"),
        ("Alisson Becker", 89, 20, 90, 82, "GK", "Liverpool", "Premier League", "Brazil", "193"),
        ("Thibaut Courtois", 90, 18, 92, 75, "GK", "Real Madrid", "La Liga", "Belgium", "200"),
        ("Rodrygo", 87, 88, 40, 90, "RW", "Real Madrid", "La Liga", "Brazil", "174"),
        ("Phil Foden", 88, 89, 55, 87, "CAM", "Manchester City", "Premier League", "England", "171"),
        ("Lamine Yamal", 88, 90, 35, 94, "RW", "Barcelona", "La Liga", "Spain", "180"),
        ("Pedri", 87, 82, 70, 78, "CM", "Barcelona", "La Liga", "Spain", "174"),
        ("Jamal Musiala", 89, 91, 55, 90, "CAM", "Bayern Munich", "Bundesliga", "Germany", "184"),
        ("Florian Wirtz", 88, 90, 58, 85, "CAM", "Bayer Leverkusen", "Bundesliga", "Germany", "177"),
        ("Antoine Griezmann", 87, 89, 62, 80, "CF", "Atletico Madrid", "La Liga", "France", "176"),
        ("Jan Oblak", 89, 15, 93, 74, "GK", "Atletico Madrid", "La Liga", "Slovenia", "188"),
        ("William Saliba", 88, 45, 91, 84, "CB", "Arsenal", "Premier League", "France", "192"),
        ("Achraf Hakimi", 88, 78, 82, 94, "RB", "PSG", "Ligue 1", "Morocco", "181"),
        ("Niclas Fullkrug", 86, 75, 70, 80, "CM", "Borussia Dortmund", "Bundesliga", "Germany", "180"),
        ("Khvicha Kvaratskhelia", 87, 88, 45, 90, "LW", "PSG", "Ligue 1", "Georgia", "183"),
        # Icons and Heroes make their dedicated packs testable.
        ("Pele", 96, 98, 55, 94, "ST", "Icons", "Icon", "Brazil", "173", "Icon"),
        ("Diego Maradona", 95, 97, 42, 91, "CAM", "Icons", "Icon", "Argentina", "165", "Icon"),
        ("Zinedine Zidane", 95, 94, 78, 82, "CAM", "Icons", "Icon", "France", "185", "Icon"),
        ("Ronaldinho", 94, 96, 48, 92, "LW", "Icons", "Icon", "Brazil", "181", "Icon"),
        ("Ronaldo Nazario", 96, 98, 45, 95, "ST", "Icons", "Icon", "Brazil", "183", "Icon"),
        ("Johan Cruyff", 94, 94, 55, 90, "CF", "Icons", "Icon", "Netherlands", "180", "Icon"),
        ("Didier Drogba", 91, 93, 60, 82, "ST", "Heroes", "Hero", "Ivory Coast", "189", "Hero"),
        ("David Ginola", 90, 89, 48, 88, "LW", "Heroes", "Hero", "France", "185", "Hero"),
        ("Yaya Toure", 91, 88, 82, 83, "CM", "Heroes", "Hero", "Ivory Coast", "188", "Hero"),
        ("Sonia Bompastor", 89, 78, 82, 87, "LB", "Heroes", "Hero", "France", "162", "Hero"),
        ("Javier Mascherano", 90, 65, 91, 78, "CDM", "Heroes", "Hero", "Argentina", "174", "Hero"),
        ("Landon Donovan", 89, 87, 55, 89, "CF", "Heroes", "Hero", "USA", "173", "Hero"),
    ]
    for offset, entry in enumerate(extra_players):
        name, overall, attack, defense, speed, position, club, league, nation, height, *kind = entry
        card_type = kind[0] if kind else "Standard"
        card_id = 10100 + offset
        test_cards.append({
            "card_id": card_id, "name": name, "attack": attack, "defense": defense,
            "speed": speed, "overall": overall, "card_rarity": "Legendary" if card_type == "Icon" else "Hero" if card_type == "Hero" else "Common" if overall < 80 else "Uncommon" if overall < 86 else "Rare",
            "card_type": card_type, "height": height, "club": club, "position": position,
            "league": league, "nation": nation,
        })


    asset_root = Path(os.getenv("FUTBOT_ASSET_DIR", ".")).expanduser().resolve()
    card_dir = asset_root / "cards"
    card_dir.mkdir(parents=True, exist_ok=True)
    colors = [(26, 74, 150), (93, 40, 160), (28, 105, 78), (45, 55, 72), (135, 75, 25), (110, 40, 55), (30, 95, 110)]
    for index, card in enumerate(test_cards):
        if services.get_card(card["card_id"]) is not None:
            continue
        image_path = card_dir / f'{card["card_id"]}.png'
        image = Image.new("RGB", (600, 840), colors[index % len(colors)])
        draw = ImageDraw.Draw(image)
        try:
            title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 42)
            rating_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 110)
            body_font = ImageFont.truetype("DejaVuSans.ttf", 30)
        except OSError:
            title_font = rating_font = body_font = ImageFont.load_default()
        draw.rounded_rectangle((18, 18, 582, 822), radius=34, outline=(235, 210, 145), width=9)
        draw.text((48, 55), str(card["overall"]), font=rating_font, fill=(255, 255, 235))
        draw.text((50, 190), card["position"], font=title_font, fill=(255, 230, 150))
        draw.text((50, 430), card["name"], font=title_font, fill="white")
        draw.text((50, 510), card["club"], font=body_font, fill=(235, 235, 235))
        draw.text((50, 590), f'ATK {card["attack"]}   DEF {card["defense"]}', font=body_font, fill="white")
        draw.text((50, 645), f'SPD {card["speed"]}   {card["nation"]}', font=body_font, fill="white")
        image.save(image_path, format="PNG")
        card["image_path"] = f'cards/{card["card_id"]}.png'
        services.create_catalog_card(card)
        logger.info("Seeded test card %s (%s)", card["card_id"], card["name"])


def main():
    api_key = os.getenv("FUTBOT_API_KEY")
    if not api_key:
        raise RuntimeError("FUTBOT_API_KEY is required. Set it in the environment.")

    init_database()
    seed_test_cards()
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", os.getenv("API_PORT", "8000")))
    logger.info("Starting FutBot API on %s:%s", host, port)
    web.run_app(create_app(api_key), host=host, port=port)


if __name__ == "__main__":
    main()
