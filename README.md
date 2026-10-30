# ⚽ FutBot - Football Card Battle Bot

**FutBot** is a football card collecting and battle game. Collect cards, build your dream team, trade with friends, and compete in tactical 5-round battles.

> **API conversion branch:** This branch is being converted into the Python game backend for the Node.js Discord bot. It does not connect to Discord. Run `python api_server.py` to start the API. The original standalone Discord bot remains on the `main` branch. See [API_CONVERSION.md](API_CONVERSION.md) for setup, routes and migration steps.

---

## 📜 License (IMPORTANT)

This project is licensed under the **PolyForm Noncommercial License 1.0.0**.

### ✅ You ARE allowed to:
* Download, install, and host this bot on your own servers.
* Modify the code for your personal use or community.
* Share this code with friends, provided this license stays attached.

### ❌ You are STRICTLY FORBIDDEN from:
* **Selling** this bot or any part of its source code.
* **Charging money** for features, in-game items, or access to the bot.
* Using this code in any commercial product or service.

*By using this software, you agree to these terms. See the `LICENSE` file for the full legal text.*

---

## ✨ Features

### 🎒 Collection & Economy
* **Daily Rewards:** Claim free cards every 18 hours.
* **Packs System:** Buy and open packs (Rare, Icon, Hero) using in-game coins.
* **Marketplace:** Sell duplicate cards to the system for coins.
* **Trading:** Securely trade cards with other players.
* **Advanced Exchange:** Negotiate complex deals involving multiple cards and coins.

### ⚔️ Battle Arena
* **Tactical Combat:** 5-round battles using Attack, Defense, and Speed stats.
* **Deck Builder:** Visual, interactive UI to build and save multiple squads.
* **Matchmaking:** Challenge any user on the server.
* **Ranked Mode:** Win battles to climb the server and global leaderboards.

### 📊 Stats & Tools
* **Visual Inspection:** Generate "Minted" slab images of your cards with ownership details.
* **Leaderboards:** Track who has the most wins, coins, or drops.
* **Wishlists:** Keep track of the cards you are hunting for.

---

## 🛠️ Legacy Standalone Bot Setup

The instructions below describe the original Discord bot on the `main` branch. They are not the startup instructions for the API conversion branch.

### Prerequisites
* Python 3.8 or higher
* A Discord Bot Token (from the [Discord Developer Portal](https://discord.com/developers/applications))

### 1. Clone the Repository
```bash
git clone [https://github.com/yourusername/futbot.git](https://github.com/yourusername/futbot.git)
cd futbot
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```
*(Make sure you have a `requirements.txt` with: `discord.py`, `python-dotenv`, `Pillow`, `rapidfuzz`, `fuzzywuzzy`)*

### 3. Configure Environment
Create a file named `.env` in the main folder and add your keys:
```ini
DISCORD_TOKEN=your_bot_token_here
ADMIN_IDS=123456789,987654321
DROP_CHANNEL_IDS=111111111,222222222
ALLOWED_CHANNELS=333333333,444444444
SUGGESTION_CHANNEL_ID=555555555
```

### 4. Run the Bot
```bash
python bot.py
```
The bot will automatically create the `cards_game.db` database file on the first run.

---

## 🎮 Key Commands

### Basics
* `/help` - Show the interactive help menu.
* `/get_starter_pack` - Start your journey with a free pack.
* `/daily` - Claim your daily reward.

### Management
* `/inventory` - View your cards (supports sorting & filtering).
* `/build_deck [name]` - Open the visual deck builder.
* `/shop` & `/buy` - Purchase new packs.

### Social
* `/battle @user` - Challenge someone to a match.
* `/trade @user` - Start a trade offer.
* `/exchange @user` - Start an advanced negotiation (Cards + Coins).
* `/lookup [id]` - Generate a visual slab for a specific card.

---

## 👤 Credits

**Creator:** Rams.
*Developed with a passion for football and trading card games.*
