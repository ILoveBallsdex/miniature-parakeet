# FutBot API Conversion

This branch is converting FutBot into a Python game backend for the existing
Node.js Discord bot.

## Repository separation

- `miniature-parakeet`: Python game backend (this repository).
- `iloveballsdex1234`: existing Node.js Discord bot; it is not changed by this work.
- `main`: original Python Discord bot; keep it as the fallback until migration is tested.
- `python-api-conversion`: all conversion work happens here.

The API process does not import `bot.py`, start a Discord client, or require
`DISCORD_TOKEN`.

## Implemented API capabilities

All routes except `GET /health` require the `X-API-Key` header. JSON responses
use `{"ok": true, "data": ...}` on success and `{"ok": false, "error": ...}`
on failure.

### Health and cards

| Method | Route | Purpose |
|---|---|---|
| GET | `/health` | Public process health check |
| GET | `/api/v1/status` | API status and feature flags |
| GET | `/api/v1/cards` | Search/list the card catalog |
| GET | `/api/v1/cards/weight?search={name}` | Calculate a card's pack weight |
| GET | `/api/v1/cards/{card_id}` | Card details |
| GET | `/api/v1/cards/{card_id}/image` | Safely serve a card image from the configured asset folder |

### Players and collection

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/v1/players/{user_id}` | Create/update a player's display name |
| GET | `/api/v1/players/{user_id}` | Player profile and wallet |
| GET | `/api/v1/players/{user_id}/stats` | Stats and inventory rarity counts |
| GET | `/api/v1/players/{user_id}/inventory` | Inventory, editions and card stats |
| GET | `/api/v1/players/{user_id}/cards/{card_id}/copies` | Inspect editions and stats for owned copies |
| POST | `/api/v1/players/{user_id}/cards/{card_id}/minted` | Generate the minted inspection image |
| GET | `/api/v1/players/{user_id}/wishlist` | Wishlist |
| POST | `/api/v1/players/{user_id}/wishlist/{card_id}` | Toggle a wishlist entry |
| GET | `/api/v1/players/{user_id}/packs` | Unopened pack counts |
| GET | `/api/v1/players/{user_id}/decks` | Saved decks |
| POST | `/api/v1/players/{user_id}/decks` | Create/update a five-card deck |
| DELETE | `/api/v1/players/{user_id}/decks` | Delete a deck (JSON body contains name) |
| DELETE | `/api/v1/players/{user_id}/decks` | Delete a deck (JSON body contains name) |
| GET | `/api/v1/players/{user_id}/achievements` | Achievement definitions and unlock status |
| POST | `/api/v1/players/{user_id}/title` | Equip an unlocked title |
| POST | `/api/v1/players/{user_id}/secrets/{secret}` | Claim one of the six one-time secret card rewards |
| POST | `/api/v1/players/{user_id}/starter-pack` | Claim the 10-card starter pack |
| POST | `/api/v1/players/{user_id}/daily/claim` | Claim daily coins/streak and receive two card choices |
| POST | `/api/v1/players/{user_id}/daily/collect` | Collect one of the pending daily cards |

### Economy, packs and drops

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/v1/packs/catalog` | Pack names and prices |
| POST | `/api/v1/packs/buy` | Buy a pack with coins |
| POST | `/api/v1/packs/open` | Open a pack and receive card(s) |
| POST | `/api/v1/inventory/sell` | Sell selected cards for coins |
| POST | `/api/v1/trade-up` | Sacrifice five cards for a higher-rarity reward |
| GET | `/api/v1/players/{user_id}/trade-up/{tier}` | List eligible trade-up copies |
| POST | `/api/v1/drops` | Create a timed card drop |
| GET | `/api/v1/drops/{drop_id}` | Read a drop |
| POST | `/api/v1/drops/{drop_id}/collect` | Collect a drop with owner-priority checks |

### Battles and trading

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/v1/battles` | Create a battle challenge |
| GET | `/api/v1/battles/{battle_id}` | Read battle state |
| POST | `/api/v1/battles/{battle_id}/accept` | Accept a challenge |
| POST | `/api/v1/battles/{battle_id}/decline` | Decline a challenge |
| POST | `/api/v1/battles/{battle_id}/decks` | Select a saved five-card deck |
| POST | `/api/v1/battles/{battle_id}/action` | Select attack, defense or speed |
| POST | `/api/v1/battles/{battle_id}/card` | Select a card for the round |
| POST | `/api/v1/battles/{battle_id}/next` | Ready up for the next round |
| POST | `/api/v1/battles/{battle_id}/draw` | Offer/accept a draw |
| POST | `/api/v1/battles/{battle_id}/surrender` | Surrender |
| POST | `/api/v1/trades` | Create a one-for-one trade offer |
| GET | `/api/v1/trades/{trade_id}` | Read a trade offer |
| POST | `/api/v1/trades/{trade_id}/accept` | Accept a trade |
| POST | `/api/v1/trades/{trade_id}/decline` | Decline a trade |
| POST | `/api/v1/exchanges` | Create an advanced card/coin exchange |
| GET | `/api/v1/exchanges/{session_id}` | Read an exchange |
| PUT | `/api/v1/exchanges/{session_id}/offer` | Replace your offer |
| POST | `/api/v1/exchanges/{session_id}/lock` | Lock your offer |
| POST | `/api/v1/exchanges/{session_id}/confirm` | Confirm a mutually locked exchange |
| POST | `/api/v1/exchanges/{session_id}/cancel` | Cancel an exchange |

### Market, friends and rankings

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/v1/market/listings` | Browse active listings |
| POST | `/api/v1/market/listings` | List a card copy in escrow |
| POST | `/api/v1/market/listings/{listing_id}/buy` | Buy a listing |
| POST | `/api/v1/market/listings/{listing_id}/cancel` | Unlist and return a card |
| GET | `/api/v1/players/{user_id}/market/listings` | View your active listings |
| POST | `/api/v1/market/expire` | Expire due listings and return escrowed cards |
| POST | `/api/v1/friends/requests` | Send a friend request |
| GET | `/api/v1/players/{user_id}/friends` | List friends |
| GET | `/api/v1/players/{user_id}/friend-requests` | List incoming requests |
| POST | `/api/v1/friends/requests/{request_id}/accept` | Accept a friend request |
| POST | `/api/v1/friends/requests/{request_id}/decline` | Decline a friend request |
| POST | `/api/v1/friends/remove` | Remove a friend |
| GET | `/api/v1/leaderboards/{stat}` | Global or selected-user leaderboard |
| POST | `/api/v1/admin/coins` | Admin-only coin grant |
| POST | `/api/v1/admin/cards/give` | Admin-only card grant |
| POST | `/api/v1/admin/cards/remove` | Admin-only card removal |

The existing Node.js bot will own Discord commands, embeds, buttons, menus,
timers and scheduling. Python owns game rules, validation, and database writes.
The API is not connected to the Node.js bot yet.

## Local setup

Use Python 3.10+ for the API process.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements-api.txt
```

Create a local `.env` file (never commit it):

```ini
FUTBOT_API_KEY=put_a_long_random_secret_here
FUTBOT_DB_PATH=./futbot_api.db
FUTBOT_ASSET_DIR=./assets
API_HOST=127.0.0.1
API_PORT=8000
```

Start the API:

```bash
python api_server.py
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Authenticated status check:

```bash
curl -H "X-API-Key: put_a_long_random_secret_here" http://127.0.0.1:8000/api/v1/status
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

GitHub Actions also runs these tests on pushes to this branch.

## Add your custom cards

The API starts with an empty card catalog. Add your own card images and define
the cards in JSON; no old database or old card files are imported.

1. Put each card image inside the folder configured by `FUTBOT_ASSET_DIR`.
   For local testing, this is normally the project's `assets` folder.
2. Copy `examples/custom_cards.example.json` to a file such as
   `my_cards.json` and replace the example details with your cards.
3. Run the importer from the project folder with the virtual environment active:

```bash
python scripts/import_custom_cards.py --file ./my_cards.json
```

The importer uses `FUTBOT_DB_PATH` and `FUTBOT_ASSET_DIR` from `.env`.
You can also pass `--database` and `--asset-root` explicitly.

Each card needs these fields:

| Field | Meaning |
|---|---|
| `card_id` | Unique numeric ID for the card |
| `name` | Player name |
| `attack`, `defense`, `speed` | Card stats |
| `overall` | Overall rating (0–99) |
| `image_path` | Image path relative to the asset folder |
| `card_rarity` | Rarity label |
| `card_type` | Type, such as `Standard`, `Icon` or `Hero` |

Optional fields: `player_id`, `height`, `club`, `position`, `league`,
and `nation`. The importer checks image paths, rejects duplicate IDs in the
same file, and will not overwrite an existing card unless you add `--update`.

Example image path: `cards/example_player.png` means the file should be at
`assets/cards/example_player.png` when `FUTBOT_ASSET_DIR=./assets`.

After importing, restart the API if needed and check:

```bash
curl -H "X-API-Key: YOUR_API_KEY" http://127.0.0.1:8000/api/v1/cards
```

## Railway deployment (later)

- Deploy this repository as a **separate** Railway service.
- Set the start command to `python api_server.py`.
- Attach a persistent volume and point `FUTBOT_DB_PATH` and `FUTBOT_ASSET_DIR`
  to paths on that volume.
- Set `FUTBOT_API_KEY` as a Railway secret variable.
- Set `FUTBOT_ADMIN_IDS` to the comma-separated Discord IDs allowed to use
  the admin endpoints.
- Do not set or expose the Discord bot token in the Python API service.
- Do not connect the Node.js bot until the API and asset migration are tested.

## Remaining work before connecting Node.js

- Add end-to-end API tests and reconcile each original command against the new routes.
- Port remaining presentation-only commands (help, about, changelog, facts and suggestions) to the Node.js bot.
- Verify card images and any other local assets.
- Build the Node.js HTTP client and replace FutBot Discord presentation with
  Node.js embeds/buttons/menus.
- Test both services together before switching the live bot.
