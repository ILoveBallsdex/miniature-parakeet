# FutBot API Conversion

This branch contains the in-progress conversion of FutBot into a Python backend
for the existing Node.js Discord bot.

## Important separation

- `bot.py` remains the original standalone Discord bot for now.
- `api_server.py` is a separate HTTP process and does **not** import `bot.py`
  or connect to Discord.
- The Node.js bot in the separate `iloveballsdex1234` repository remains
  untouched during this phase.
- Do not run two Discord clients with the same bot token during migration.

## First API scaffold

Install the existing project dependencies:

```bash
pip install -r requirements.txt
```

Create a local `.env` file (do not commit it) with a strong random API key:

```ini
FUTBOT_API_KEY=replace_with_a_long_random_secret
API_HOST=127.0.0.1
API_PORT=8000
```

Start the API:

```bash
python api_server.py
```

Check that the process is alive:

- `GET http://127.0.0.1:8000/health` (no key required)
- `GET http://127.0.0.1:8000/api/v1/status` (send the `X-API-Key` header)

Example:

```bash
curl -H "X-API-Key: replace_with_a_long_random_secret" \
  http://127.0.0.1:8000/api/v1/status
```

The status response deliberately reports that game routes are not ready yet.
No player or game data is changed by this scaffold.

## Conversion phases

1. Add and locally test the standalone API process.
2. Separate database access and game rules from Discord UI code.
3. Add authenticated, domain-specific routes for cards, packs, inventory,
   economy, battles, trading, and other existing FutBot features.
4. Test those routes against a fresh development database.
5. Deploy the API as a separate Railway service with persistent storage.
6. Connect the existing Node.js bot to the API, then retire the old Python
   Discord gateway process once the migration is verified.

## Database reset

The current player population can be replaced during the migration. That does
not mean the card catalog, game configuration, or schema should be discarded.
The reset should be deliberate and performed against the new database only
after the replacement schema and game routes have been tested.
