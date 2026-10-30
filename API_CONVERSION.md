# FutBot API Conversion

This branch is converting FutBot into a Python game backend for the existing
Node.js Discord bot.

## Repository separation

- `miniature-parakeet`: Python game backend (this repository).
- `iloveballsdex1234`: existing Node.js Discord bot; it is not changed by this work.
- `main`: original Python Discord bot; leave it as the fallback until the migration is tested.
- `python-api-conversion`: all conversion work happens here.

The API process does not import `bot.py`, start a Discord client, or require
`DISCORD_TOKEN`.

## Current API capabilities

Authenticated routes use the `X-API-Key` header.

| Method | Route | Purpose |
|---|---|---|
| GET | `/health` | Public process health check |
| GET | `/api/v1/status` | API status and implemented feature list |
| GET | `/api/v1/cards` | Search/list the card catalog |
| GET | `/api/v1/cards/{card_id}` | Card details |
| POST | `/api/v1/players/{user_id}` | Create/update a player name |
| GET | `/api/v1/players/{user_id}` | Player profile |
| GET | `/api/v1/players/{user_id}/inventory` | Player inventory |
| GET | `/api/v1/players/{user_id}/packs` | Unopened pack counts |
| GET | `/api/v1/players/{user_id}/decks` | Saved decks |
| POST | `/api/v1/players/{user_id}/decks` | Save a five-card deck |
| POST | `/api/v1/players/{user_id}/wishlist/{card_id}` | Toggle wishlist entry |
| GET | `/api/v1/packs/catalog` | Pack names and prices |
| POST | `/api/v1/packs/buy` | Buy a pack with coins |
| POST | `/api/v1/packs/open` | Open a pack and receive cards |
| POST | `/api/v1/inventory/sell` | Sell selected cards |

Game mutations use SQLite transactions. The API validates ownership and
balances before changing player data. Battles, player-to-player trading, and
the marketplace are not yet exposed; they must be implemented and tested
before the Node.js bot switches to the API.

## Local setup

Use Python 3.10+ for the new API process.

```bash
python -m venv .venv
# Windows:
.venv\\Scripts\\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements-api.txt
```

Create a local `.env` file (never commit it):

```ini
FUTBOT_API_KEY=put_a_long_random_secret_here
FUTBOT_DB_PATH=./futbot_api.db
API_HOST=127.0.0.1
API_PORT=8000
```

Start the API:

```bash
python api_server.py
```

Check process health:

```bash
curl http://127.0.0.1:8000/health
```

Check authenticated status:

```bash
curl -H "X-API-Key: put_a_long_random_secret_here" http://127.0.0.1:8000/api/v1/status
```

Run service tests:

```bash
python -m unittest discover -s tests -v
```

## Import the existing card catalog

The old `cards_game.db` is not stored in GitHub. Once the local API has been
tested, copy only the card catalog into a separate API database:

```bash
python scripts/import_card_catalog.py --source ./cards_game.db --target ./futbot_api.db
```

This script copies rows from the `cards` table only. It does not copy players,
inventories, coins, packs, decks, trades, or match history. It refuses to run
if source and target are the same file.

Keep a backup of the original database. Do not overwrite it.

## Database and Railway

- Set `FUTBOT_DB_PATH` to a path on a Railway persistent volume.
- Set `FUTBOT_API_KEY` as a Railway secret variable.
- Set the service start command to `python api_server.py`.
- Do not set or expose the Discord bot token in the Python API service.
- Do not deploy the API against a temporary filesystem for production data.
- Do not connect the Node.js bot until all required game routes are complete
  and tested.

## Conversion phases

1. Standalone API and database layer.
2. Port economy, inventory, card catalog, pack and deck behavior.
3. Port battles, trading, marketplace, daily rewards, drops, achievements,
   friends, leaderboards and admin actions.
4. Test all API behavior against a fresh development database.
5. Deploy the Python API as a separate Railway service.
6. Connect the existing Node.js bot to the API.
7. Retire the old Python Discord gateway process only after the new flow works.
