"""
FutBot HTTP API entrypoint.

This process intentionally does not import bot.py or connect to Discord.
It is the first step toward moving FutBot's game logic behind an API that
the existing Node.js Discord bot can call.
"""

import hmac
import logging
import os

from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("futbot.api")


@web.middleware
async def api_key_middleware(request: web.Request, handler):
    # Railway health checks should not need an API key.
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


async def health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": True,
            "service": "futbot-api",
        }
    )


async def status(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": True,
            "service": "futbot-api",
            "api_version": 1,
            "discord_gateway_connected": False,
            "game_routes_ready": False,
        }
    )


def create_app(api_key: str) -> web.Application:
    app = web.Application(
        middlewares=[api_key_middleware],
        client_max_size=1024 * 1024,
    )
    app["api_key"] = api_key
    app.router.add_get("/health", health)
    app.router.add_get("/api/v1/status", status)
    return app


def main() -> None:
    api_key = os.getenv("FUTBOT_API_KEY")
    if not api_key:
        raise RuntimeError(
            "FUTBOT_API_KEY is required. Set it in your local .env file "
            "or your deployment environment."
        )

    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", os.getenv("API_PORT", "8000")))

    logger.info("Starting FutBot API on %s:%s", host, port)
    web.run_app(create_app(api_key), host=host, port=port)


if __name__ == "__main__":
    main()
