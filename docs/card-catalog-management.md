# FutBot card catalog management

Card catalog administration is available through the Discord bot's `/futbotadmin` command group.

## Discord commands

- `/futbotadmin create-card`: create a catalog card and upload its image.
- `/futbotadmin replace-card`: update any supplied fields and optionally replace the image.
- `/futbotadmin remove-card`: delete a catalog card. Deletion is blocked while any player owns a copy.
- `/futbotadmin remove-player-card`: remove a particular card from a player's inventory.
- `/futbotadmin import-json`: import 1-50 cards from an attached JSON file.

All card catalog operations require the invoking Discord user's ID to be included in the Python service's `FUTBOT_ADMIN_IDS` Railway variable. Separate Discord Administrator permissions are also required by the slash command.

## Card fields

Required when creating a card:

- `card_id`: unique positive integer
- `name`
- `attack`, `defense`, `speed`, `overall`: integers from 0 to 99
- `card_rarity`
- `card_type`
- `image`: PNG, JPG/JPEG, WEBP, or GIF, up to 3 MB

Optional fields: `player_id`, `height`, `club`, `position`, `league`, and `nation`.

The overall rating is entered manually; it is not calculated from the other three stats.

## JSON import format

Attach a JSON file containing either an array of cards or an object with a `cards` array. Every card must include the required fields above and an `image_base64` string containing the base64-encoded image bytes. Set `replace_existing` to `true` on an entry to update a card with the same ID; otherwise duplicate IDs are rejected.

Example entry:

```json
{
  "card_id": 10001,
  "name": "Example Player",
  "attack": 90,
  "defense": 75,
  "speed": 88,
  "overall": 89,
  "card_rarity": "Rare",
  "card_type": "Standard",
  "club": "Example FC",
  "position": "ST",
  "league": "Example League",
  "nation": "Example Nation",
  "image_base64": "BASE64_ENCODED_IMAGE_BYTES"
}
```

On Windows PowerShell, generate the base64 value for an image with:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\path\to\card.png"))
```

Paste the output into the JSON string. The API accepts images up to 3 MB each and requests up to 16 MB. The imported image files are stored under the configured `FUTBOT_ASSET_DIR`, so use the persistent Railway volume for that directory.
