import discord
from discord.ext import commands, tasks
from rapidfuzz import process
import sqlite3
import random
import asyncio
import logging
import json
from fuzzywuzzy import process
from PIL import Image, ImageDraw, ImageFont, ImageOps
import io
import time
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
from typing import Literal
from discord import app_commands
from typing import List
try:
    from aiohttp import web as aiohttp_web
    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False



#----------------------------ENVIRONMENT SETUP---------------------------------------------------------------------------------

# 1. Load the .env file
load_dotenv()

# 2. Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 3. Helper function to load lists from .env
def load_id_list(env_key):
    val = os.getenv(env_key)
    if not val: return []
    return [int(x.strip()) for x in val.split(',') if x.strip().isdigit()]

# 4. Load Configuration Variables
TOKEN = os.getenv('DISCORD_TOKEN')
ADMIN_IDS = load_id_list('ADMIN_IDS')
DROP_CHANNEL_IDS = load_id_list('DROP_CHANNEL_IDS')
ALLOWED_CHANNELS = load_id_list('ALLOWED_CHANNELS')
SUGGESTION_CHANNEL_ID = int(os.getenv('SUGGESTION_CHANNEL_ID'))
API_PORT = int(os.getenv('API_PORT', '25535'))
DASHBOARD_SECRET = os.getenv('DASHBOARD_SECRET', '')

# Connect to SQLite database (or create it if it doesn't exist)
conn = sqlite3.connect('cards_game.db')
cursor = conn.cursor()







#---------------------------------------------------------TABLES-------------------------------------------------------------------------------------
# Create tables (if not already created)
cursor.execute('''
CREATE TABLE IF NOT EXISTS cards (
    card_id INTEGER PRIMARY KEY,
    player_id TEXT,
    name TEXT,
    attack INTEGER,
    defense INTEGER,
    speed INTEGER,
    height TEXT,
    club TEXT,
    position TEXT,
    overall INTEGER,
    image_path TEXT,
    card_rarity TEXT,
    card_type TEXT,
    league TEXT,
    nation TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS decks (
    user_id INTEGER,
    deck_name TEXT,
    cards TEXT,
    FOREIGN KEY(user_id) REFERENCES players(user_id)
)
''')


cursor.execute('''
CREATE TABLE IF NOT EXISTS players (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    battles_played INTEGER DEFAULT 0,
    battles_won INTEGER DEFAULT 0,
    battles_lost INTEGER DEFAULT 0,
    has_claimed_starter_pack BOOLEAN DEFAULT 0,
    rounds_played INTEGER DEFAULT 0,
    rounds_won INTEGER DEFAULT 0,
    rounds_lost INTEGER DEFAULT 0
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS inventories (
    user_id INTEGER,
    card_id INTEGER,
    edition INTEGER,
    FOREIGN KEY(user_id) REFERENCES players(user_id),
    FOREIGN KEY(card_id) REFERENCES cards(card_id)
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS achievements (
   	achievement_id	INTEGER,
	title	TEXT NOT NULL,
	description	TEXT NOT NULL,
	PRIMARY KEY(achievement_id)
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS user_achievements (
    	user_id	INTEGER,
	achievement_id	INTEGER,
	date_earned 	DATETIME DEFAULT CURRENT_TIMESTAMP,
	PRIMARY KEY(user_id,achievement_id),
	FOREIGN KEY(user_id) REFERENCES players(user_id),
	FOREIGN KEY(achievement_id) REFERENCES achievements(achievement_id)
)
''')

def migrate_db():
    try:
        conn = sqlite3.connect('cards_game.db')
        cursor = conn.cursor()
        
        # ... (Keep previous Player/Wishlist checks) ...

        # --- NEW: Card Stats (Individual Copies in Inventory) ---
        cursor.execute("PRAGMA table_info(inventories)")
        inv_columns = [info[1] for info in cursor.fetchall()]
        
        stats_cols = ['battles_played', 'battles_won', 'rounds_played', 'rounds_won']
        for col in stats_cols:
            if col not in inv_columns:
                print(f"Migrating DB: Adding {col} to inventories...")
                cursor.execute(f"ALTER TABLE inventories ADD COLUMN {col} INTEGER DEFAULT 0")

        # --- NEW: Card Stats (Global Totals in Cards table) ---
        cursor.execute("PRAGMA table_info(cards)")
        card_columns = [info[1] for info in cursor.fetchall()]
        
        global_cols = ['total_battles_played', 'total_battles_won', 'total_rounds_played', 'total_rounds_won']
        for col in global_cols:
            if col not in card_columns:
                print(f"Migrating DB: Adding {col} to cards...")
                cursor.execute(f"ALTER TABLE cards ADD COLUMN {col} INTEGER DEFAULT 0")

        # --- NEW: Daily Streak System ---
        cursor.execute("PRAGMA table_info(players)")
        player_columns = [info[1] for info in cursor.fetchall()]
        
        streak_cols = ['daily_streak', 'last_daily_claim']
        for col in streak_cols:
            if col not in player_columns:
                print(f"Migrating DB: Adding {col} to players...")
                if col == 'daily_streak':
                    cursor.execute(f"ALTER TABLE players ADD COLUMN {col} INTEGER DEFAULT 0")
                else:
                    cursor.execute(f"ALTER TABLE players ADD COLUMN {col} TEXT DEFAULT NULL")

        # --- Transfer Market ---
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_listings (
                listing_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id   INTEGER,
                card_id     INTEGER,
                edition     INTEGER DEFAULT 1,
                trade_count INTEGER DEFAULT 0,
                price       INTEGER,
                listed_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                expires_at  TEXT,
                status      TEXT DEFAULT 'active',
                resolved_at TEXT,
                buyer_id    INTEGER
            )
        ''')
        cursor.execute("PRAGMA table_info(market_listings)")
        ml_cols = [r[1] for r in cursor.fetchall()]
        for col, defn in [('status', "TEXT DEFAULT 'active'"),
                          ('resolved_at', 'TEXT'),
                          ('buyer_id', 'INTEGER')]:
            if col not in ml_cols:
                cursor.execute(f'ALTER TABLE market_listings ADD COLUMN {col} {defn}')

        # --- Friends System ---
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS friendships (
                user_id   INTEGER,
                friend_id INTEGER,
                added_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, friend_id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS friend_requests (
                request_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                requester_id INTEGER,
                recipient_id INTEGER,
                requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
                notified     INTEGER DEFAULT 0,
                UNIQUE(requester_id, recipient_id)
            )
        ''')

        conn.commit()
        conn.close()
        print("Database migration complete.")
    except Exception as e:
        print(f"Migration Error: {e}")

# IMPORTANT: Call this function once when the bot starts
migrate_db()

#---------------------------------------------------------SETUP-------------------------------------------------------------------------------------

conn.commit()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # <--- ADD THIS LINE
bot = commands.Bot(command_prefix=['f','F'], intents=intents)

@bot.check
async def global_channel_check(ctx):
    # 1. Allow DMs if needed (Optional)
    if ctx.guild is None: return False

    # 2. Check Allowed Channels (Applies to EVERYONE, including Admins)
    if ctx.channel.id in ALLOWED_CHANNELS:
        return True
    
    # If we are here, the channel is wrong.
    raise commands.CheckFailure("Channel not allowed.")

@bot.event
async def on_message(message):
    # 1. Ignore itself
    if message.author == bot.user:
        return

    # 2. OPTIMIZATION: Ignore non-allowed channels immediately
    # We removed the Admin check here, so it blocks everyone
    if message.guild is not None and message.channel.id not in ALLOWED_CHANNELS:
        return

    # 3. Process
    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx, error):
    # 1. Handle Channel Restriction Errors
    if isinstance(error, commands.CheckFailure):
        # IF SLASH COMMAND: Send a hidden warning (only user sees it)
        if ctx.interaction:
            await ctx.send(f"⛔ Commands are only allowed in <#{ALLOWED_CHANNELS[0]}>.", ephemeral=True)
        
        # IF TEXT COMMAND (!daily): Do nothing (Stay silent)
        else:
            return 

    # 2. Handle Cooldowns (e.g., !daily)
    elif isinstance(error, commands.CommandOnCooldown):
        retry_after = int(time.time() + error.retry_after)
        # Use send for text commands, interaction for slash
        if ctx.interaction:
            await ctx.send(f"⏳ Cooldown! Try again <t:{retry_after}:R>.", ephemeral=True)
        else:
            await ctx.send(f"⏳ Cooldown! Try again <t:{retry_after}:R>.", delete_after=5)

    # 3. Print other errors to console for debugging
    else:
        logger.error(f"Command Error: {error}")


#---------------------------------------------------------HELP-------------------------------------------------------------------------------------

#---------------------------------------------------------HELP-------------------------------------------------------------------------------------

bot.remove_command('help')

class HelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Home", description="Back to main menu", emoji="🏠", value="home"),
            discord.SelectOption(label="Battle Arena", description="Combat, Decks, and Tactics", emoji="⚔️", value="battle"),
            discord.SelectOption(label="Collection", description="Inventory, Catalog, Inspection", emoji="🎒", value="collection"),
            discord.SelectOption(label="Economy & Market", description="Coins, Shop, Trading", emoji="💰", value="economy"),
            discord.SelectOption(label="Stats & Rankings", description="Leaderboards and Achievements", emoji="🏆", value="stats"),
            discord.SelectOption(label="Bot Info", description="Version, Changelog, Extras", emoji="ℹ️", value="info")
        ]
        super().__init__(placeholder="Select a category...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        
        if value == "home":
            embed = discord.Embed(title="⚽ FutBot Help Center", description="Welcome to the ultimate football card battle bot!", color=discord.Color.gold())
            embed.add_field(name="Getting Started", value="Use the dropdown menu below to browse specific command categories.", inline=False)
            embed.add_field(name="Quick Start", value="`/get_starter_pack` - Get your first cards\n`/daily` - Claim free rewards\n`/build_deck` - Create a team visually\n`/battle` - Fight players", inline=False)
            embed.set_footer(text="Select a category for detailed command usage.")
        
        elif value == "battle":
            embed = discord.Embed(title="⚔️ Battle Arena", color=discord.Color.red())
            embed.add_field(name="Matchmaking", value="`/battle @user` - Challenge a player to a 5-round match.", inline=False)
            embed.add_field(name="Deck Management", value="`/build_deck [name]` - **NEW!** Visual interactive deck builder.\n`/create_deck` - Manual creation (Requires IDs).\n`/edit_deck` - Modify an existing deck.\n`/decks` - View your list of decks.\n`/view_deck` - Visualize your lineup.", inline=False)
            embed.add_field(name="Info", value="`/battle_logic` - Learn the rules of combat.", inline=False)

        elif value == "collection":
            embed = discord.Embed(title="🎒 Collection & Items", color=discord.Color.blue())
            embed.add_field(name="Viewing", value="`/inventory` - View your cards (Sort/Filter available).\n`/catalog` - Browse ALL cards in the game database.\n`/view [name]` - See card stats and global popularity.", inline=False)
            embed.add_field(name="Inspection", value="`/lookup [id]` - Generate a custom 'Minted' slab for a card you own.", inline=False)
            embed.add_field(name="Packs", value="`/packs` - See your unopened card packs.\n`/open [id]` - Open a pack.\n`/weight` - Check drop chances.", inline=False)
            embed.add_field(name="Wishlist", value="`/wishlist [id]` - Add/Remove a card from your wishlist.\n`/wishlists [@user]` - View your (or a friend's) wishlist.", inline=False)

        elif value == "economy":
            embed = discord.Embed(title="💰 Economy & Market", color=discord.Color.green())
            embed.add_field(name="Earning", value="`/daily` - Claim free cards (18h Cooldown).\n`/drop` - Drop a card in chat (30m Cooldown).", inline=False)
            embed.add_field(name="Trading", value="`/trade` - Quick 1-for-1 card swap.\n`/exchange` - Advanced table for Cards + Coins trading.", inline=False)
            embed.add_field(name="Market", value="`/shop` - Buy packs with coins.\n`/buy` - Purchase a pack.\n`/sell` - Sell a card for quick coins.", inline=False)
            embed.add_field(name="Wallet", value="`/coins` - Check your balance.", inline=False)

        elif value == "stats":
            embed = discord.Embed(title="🏆 Stats & Rankings", color=discord.Color.purple())
            embed.add_field(name="Profile", value="`/stats [@user]` - View battle records and win rates.\n`/titles` - View unlocked achievements.\n`/set_title` - Equip a profile title.", inline=False)
            embed.add_field(name="Leaderboards", value="`/lb` - View Server Rankings.\n`/lb [bp/rw/rp/coins]` - View sub-leaderboards (Played, Rounds, Wealth).", inline=False)

        elif value == "info":
            embed = discord.Embed(title="ℹ️ Bot Information", color=discord.Color.light_grey())
            embed.add_field(name="General", value="`/about` - Bot Info.\n`/changelog` - Latest Updates.\n`/facts` - Football Trivia.\n`/suggest` - Send feedback.", inline=False)
            embed.add_field(name="Secrets", value="There are hidden commands based on countries... can you find them?", inline=False)

        await interaction.response.edit_message(embed=embed, view=self.view)

class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(HelpSelect())

@bot.hybrid_command(name='help', description="Show the help menu")
async def help_command(ctx):
    embed = discord.Embed(title="⚽ FutBot Help Center", description="Welcome to the ultimate football card battle bot!", color=discord.Color.gold())
    embed.add_field(name="Getting Started", value="Use the dropdown menu below to browse specific command categories.", inline=False)
    embed.add_field(name="Quick Start", value="`/get_starter_pack` - Get your first cards\n`/daily` - Claim free rewards\n`/build_deck` - Create a team\n`/battle` - Fight players", inline=False)
    
    view = HelpView()
    await ctx.send(embed=embed, view=view)

class ChangelogView(discord.ui.View):
    def __init__(self, data):
        super().__init__(timeout=120)
        self.data = data
        self.current_page = 0
        self.items_per_page = 10 # Adjust this number to fit more/less per page
        self.total_pages = max(1, (len(data) - 1) // self.items_per_page + 1)
        self.update_buttons()

    def get_embed(self):
        start = self.current_page * self.items_per_page
        end = start + self.items_per_page
        page_items = self.data[start:end]
        
        # Format the list into a code block string
        text_content = "\n".join(page_items)
        
        embed = discord.Embed(title="📜 Bot Changelog", color=discord.Color.blue())
        embed.description = f"```\n{text_content}\n```"
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} | Version {BOT_VERSION}")
        return embed

    def update_buttons(self):
        # Enable/Disable buttons based on current page
        self.children[0].disabled = (self.current_page == 0) # Prev button
        self.children[1].disabled = (self.current_page == self.total_pages - 1) # Next button

    @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.primary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)
#---------------------------------------------------------ABOUT-------------------------------------------------------------------------------------


# Bot version and creator information
BOT_VERSION = "1.7.1"
CREATOR = "noobmaster"
DESCRIPTION = "This bot is designed to give maximum resemblance to Match Attax card games. With this bot, you can collect football player cards and battle with your friends using your favourite players."
# Sorted Newest to Oldest for better UX
CHANGELOG_DATA = [
    "1.7.1 - Leaderboards now have a Friends scope — toggle Server / Global / Friends to rank only among your friends.",
    "1.7.1 - Friend request notifications now fire reliably after prefix commands as well as slash commands.",
    "1.7.0 - Friends System: send/accept friend requests with /addfriend, /removefriend, /friends. Pending requests show on next command with accept/decline buttons.",
    "1.6.6 - Transfer market now keeps full history of all listings (sold, cancelled, expired) with status tracking.",
    "1.6.6 - Trade up reward card image now displays inside the embed instead of as a separate message.",
    "1.6.6 - Trade up rare pool now uses weighted category selection — Rare Standard cards are far more common than Hero/Icon.",
    "1.6.5 - Trade Up: sacrifice 5 cards of a rarity for 1 of the next tier via /tradeup.",
    "1.6.4 - Transfer Market: list, browse, buy, and unlist cards with /list, /market, /listed, /unlist.",
    "1.6.4 - Fixed edition numbering — copies no longer double-increments when a card is given or dropped.",
    "1.6.3 - Duplicate cards: players can now hold multiple copies of the same card, each with its own edition number.",
    "1.6.2 - /view and /lookup now show all owned editions for duplicate cards. /lookup shows an edition picker when you own multiple copies.",
    "1.6.1 - Unique-type cards are now hidden from /view and autocomplete for players who don't own them.",
    "1.6.0 - Battle draw reward increased from 100 to 150 coins.",
    "1.5.2 - Added /delete_deck command for easier deck management.",
    "1.5.1 - Wishlists are now fully interactive buttons on Card Details.",
    "1.5.0 - Overhauled Daily Rewards: Calendar-day streaks with milestone packs!",
    "1.4.7 - Added multiple sales and sale menu in sell command.",
    "1.4.6 - Added autocomplete for view, lookup and sell commands.",
    "1.4.5 - Fixed changelog spanning multiple pages",
    "1.4.4 - Better leaderboard commands",
    "1.4.3 - Fixed last round not showing in battles",
    "1.4.2 - Added build_deck command",
    "1.4.1 - More Card Stats Tracking",
    "1.4.0 - Wishlist System Added",
    "1.3.10 - Lookup Mint Card Image Generation",
    "1.3.9 - Lookup Command Added",
    "1.3.8 - Global & Server Leaderboards",
    "1.3.7 - Beauty Enhancements",
    "1.3.6 - Catalog Command Added",
    "1.3.5 - More Filters Added",
    "1.3.4 - Help Menu Upgrade",
    "1.3.3 - Exchange command added",
    "1.3.2 - Drop command fixes",
    "1.3.1 - Inventory Sort & Filter",
    "1.3.0 - Inventory Control Fixes",
    "1.2.6 - Fixed slash command bugs",
    "1.2.5 - Added Slash Commands",
    "1.2.4 - Added Draws",
    "1.2.3 - fdrop updates",
    "1.2.2 - 30 min card drop logic fix",
    "1.2.1 - Deck Lineup UI",
    "1.2.0 - Battle UI overhaul",
    "1.1.1 - Fixed minor bugs and added hero cards",
    "1.1.0 - Added Shop and Sell functions. Multiple minor patches.",
    "1.0.0 - Initial release"
]

# Existing commands like !daily, !drop, !view, etc.

@bot.hybrid_command(name='about', description="About this bot")
async def about(ctx):
    embed = discord.Embed(title="About This Bot", color=discord.Color.blue())
    embed.add_field(name="Version", value=f"```{BOT_VERSION}```", inline=True)
    embed.add_field(name="Creator", value=CREATOR, inline=True)
    embed.add_field(name="Description", value=DESCRIPTION, inline=False)
    embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
    
    await ctx.send(embed=embed)


@bot.hybrid_command(name='version', description="Check bot version")
async def version(ctx):
    embed = discord.Embed(title="Bot Version")
    embed.add_field(name="Version", value=f"```{BOT_VERSION}```", inline=False)
    await ctx.send(embed=embed)

@bot.hybrid_command(name='changelog', description="Check recent changes")
async def changelog(ctx):
    view = ChangelogView(CHANGELOG_DATA)
    await ctx.send(embed=view.get_embed(), view=view)


#---------------------------------------------------------SUGGESTIONS-------------------------------------------------------------------------------------


@bot.hybrid_command(name='suggest', description="Submit a suggestion")
async def suggest(ctx, *, suggestion: str):
    # Use the variable loaded from env
    suggestion_channel = bot.get_channel(SUGGESTION_CHANNEL_ID) 
    
    if suggestion_channel:
        embed = discord.Embed(title="New Suggestion", description=suggestion, color=0x0000ff)
        embed.add_field(name="Suggested by", value=ctx.author.mention, inline=False)
        await suggestion_channel.send(embed=embed)
        await ctx.send("Thank you for your suggestion! It has been forwarded to the team.")
    else:
        await ctx.send("Sorry, I couldn't find the suggestion channel. Please try again later.")



#---------------------------------------------------------ACHIEVEMENTS-------------------------------------------------------------------------------------

@bot.hybrid_command(name='titles', description="View achievements")
async def display_achievements(ctx, member: discord.Member = None):
    if member is None:
        cursor.execute('SELECT title, description FROM achievements')
        achievements = cursor.fetchall()
        embed = discord.Embed(title="All Achievements", description="List of all possible achievements.")
        for title, description in achievements:
            embed.add_field(name=title, value=description, inline=False)
    else:
        cursor.execute('''
        SELECT a.title, a.description FROM achievements a
        JOIN user_achievements ua ON a.achievement_id = ua.achievement_id
        WHERE ua.user_id = ?
        ''', (member.id,))
        user_achievements = cursor.fetchall()

        if not user_achievements:
            embed = discord.Embed(title=f"{member.name}'s Achievements", description="This user doesn't have any achievements.")
        else:
            embed = discord.Embed(title=f"{member.name}'s Achievements", description="List of achievements earned by the user.")
            for title, description in user_achievements:
                embed.add_field(name=title, value=description, inline=False)
    
    await ctx.send(embed=embed)

        


def determine_card_rarity(overall):
    if overall is None:
        return 'Common'
    if overall > 85:
        return 'Rare'
    elif overall > 75:
        return 'Uncommon'
    else:
        return 'Common'

from discord.ext import commands
from discord.ui import View

def increment_cards_dropped(user_id):
    try:
        conn = sqlite3.connect('cards_game.db')
        cursor = conn.cursor()
        cursor.execute("UPDATE players SET cards_dropped = cards_dropped + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
    except Exception as e:
        logger.error(f"Error incrementing cards_dropped: {e}")
    finally:
        conn.close()



#---------------------------------------------------------SECRET COMMANDS-------------------------------------------------------------------------------------

def secret_command():
    def decorator(func):
        async def wrapper(ctx, *args, **kwargs):
            await func(ctx, *args, **kwargs)
            await ctx.message.delete()
        return wrapper
    return decorator


def get_card_by_id(card_id):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM cards WHERE card_id = ?', (card_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return Card(*row)
    return None


#-----------------ENGLAND

itscominghome_card_ids = [10392, 10397, 10406, 10407, 10408, 10411, 10412, 10418, 10428, 10443, 10451, 10453, 10457]

@bot.command(name='itscominghome')
@secret_command()
async def itscominghome(ctx):
    ensure_player_exists(ctx.author.id, ctx.author.name)
    
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute('SELECT itscominghome FROM players WHERE user_id = ?', (ctx.author.id,))
    used_command = cursor.fetchone()[0]

    if used_command:
        await ctx.author.send("You have already used this command.")
        return
    
    card_id = None
    attempts = 0
    max_attempts = len(itscominghome_card_ids) * 2  # To prevent potential infinite loops
    
    while attempts < max_attempts:
        attempts += 1
        card_id = random.choice(itscominghome_card_ids)
        cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (ctx.author.id, card_id))
        if cursor.fetchone() is None:
            break

    if card_id is None:
        await ctx.author.send("No available cards to add to your inventory.")
        conn.close()
        return

    card = get_card_by_id(card_id)
    if card:
        card.copies += 1
        add_card(card)
        add_card_to_inventory(ctx.author.id, card.card_id)

        cursor.execute('UPDATE players SET itscominghome = 1 WHERE user_id = ?', (ctx.author.id,))
        conn.commit()
        
        embed = discord.Embed(title="Special Drop", description="You have received a special card drop! Shh, don't tell anyone about this command.")
        embed.add_field(name="Name", value=card.name, inline=True)
        embed.add_field(name="ID", value=card.card_id, inline=True)
        embed.add_field(name="Rarity", value=card.card_rarity, inline=True)
        embed.add_field(name="Type", value=card.card_type, inline=True)
        embed.add_field(name="Attack", value=card.attack, inline=True)
        embed.add_field(name="Defense", value=card.defense, inline=True)
        embed.add_field(name="Speed", value=card.speed, inline=True)
        embed.add_field(name="Overall", value=card.overall, inline=True)
        embed.add_field(name="League", value=card.league, inline=True)
        embed.add_field(name="Nation", value=card.nation, inline=True)
        embed.add_field(name="Copies", value=card.copies, inline=True)
        embed.set_image(url=f"attachment://{card.image_path.split('/')[-1]}")
        embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)

        await ctx.author.send(embed=embed, file=discord.File(card.image_path))
        logger.info(f'{ctx.author.name} received a special card {card.name} (ID: {card.card_id}) using !itscominghome')
    else:
        await ctx.author.send("An error occurred while processing your request.")
    
    conn.close()


#----------------------BRAZIL

jogabonito_card_ids = [10394, 10395, 10399, 10405, 10446, 10462, 10465, 10469]

@bot.command(name='jogabonito')
@secret_command()
async def jogabonito(ctx):
    ensure_player_exists(ctx.author.id, ctx.author.name)
    
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute('SELECT jogabonito FROM players WHERE user_id = ?', (ctx.author.id,))
    used_command = cursor.fetchone()[0]

    if used_command:
        await ctx.author.send("You have already used this command.")
        return
    
    card_id = None
    attempts = 0
    max_attempts = len(jogabonito_card_ids) * 2  # To prevent potential infinite loops
    
    while attempts < max_attempts:
        attempts += 1
        card_id = random.choice(jogabonito_card_ids)
        cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (ctx.author.id, card_id))
        if cursor.fetchone() is None:
            break

    if card_id is None:
        await ctx.author.send("No available cards to add to your inventory.")
        conn.close()
        return

    card = get_card_by_id(card_id)
    if card:
        card.copies += 1
        add_card(card)
        add_card_to_inventory(ctx.author.id, card.card_id)

        cursor.execute('UPDATE players SET jogabonito = 1 WHERE user_id = ?', (ctx.author.id,))
        conn.commit()
        
        embed = discord.Embed(title="Special Drop", description="You have received a special card drop! Shh, don't tell anyone about this command.")
        embed.add_field(name="Name", value=card.name, inline=True)
        embed.add_field(name="ID", value=card.card_id, inline=True)
        embed.add_field(name="Rarity", value=card.card_rarity, inline=True)
        embed.add_field(name="Type", value=card.card_type, inline=True)
        embed.add_field(name="Attack", value=card.attack, inline=True)
        embed.add_field(name="Defense", value=card.defense, inline=True)
        embed.add_field(name="Speed", value=card.speed, inline=True)
        embed.add_field(name="Overall", value=card.overall, inline=True)
        embed.add_field(name="League", value=card.league, inline=True)
        embed.add_field(name="Nation", value=card.nation, inline=True)
        embed.add_field(name="Copies", value=card.copies, inline=True)
        embed.set_image(url=f"attachment://{card.image_path.split('/')[-1]}")
        embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)

        await ctx.author.send(embed=embed, file=discord.File(card.image_path))
        logger.info(f'{ctx.author.name} received a special card {card.name} (ID: {card.card_id}) using !jogabonito')
    else:
        await ctx.author.send("An error occurred while processing your request.")
    
    conn.close()


#--------------------ITALY

pineappleonpizza_card_ids = [10391, 10393, 10414, 10415, 10430, 10455, 10459, 10460]

@bot.command(name='pineappleonpizza')
@secret_command()
async def pineappleonpizza(ctx):
    ensure_player_exists(ctx.author.id, ctx.author.name)
    
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute('SELECT pineappleonpizza FROM players WHERE user_id = ?', (ctx.author.id,))
    used_command = cursor.fetchone()[0]

    if used_command:
        await ctx.author.send("You have already used this command.")
        return
    
    card_id = None
    attempts = 0
    max_attempts = len(pineappleonpizza_card_ids) * 2  # To prevent potential infinite loops
    
    while attempts < max_attempts:
        attempts += 1
        card_id = random.choice(pineappleonpizza_card_ids)
        cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (ctx.author.id, card_id))
        if cursor.fetchone() is None:
            break

    if card_id is None:
        await ctx.author.send("No available cards to add to your inventory.")
        conn.close()
        return

    card = get_card_by_id(card_id)
    if card:
        card.copies += 1
        add_card(card)
        add_card_to_inventory(ctx.author.id, card.card_id)

        cursor.execute('UPDATE players SET pineappleonpizza = 1 WHERE user_id = ?', (ctx.author.id,))
        conn.commit()
        
        embed = discord.Embed(title="Special Drop", description="You have received a special card drop! Shh, don't tell anyone about this command.")
        embed.add_field(name="Name", value=card.name, inline=True)
        embed.add_field(name="ID", value=card.card_id, inline=True)
        embed.add_field(name="Rarity", value=card.card_rarity, inline=True)
        embed.add_field(name="Type", value=card.card_type, inline=True)
        embed.add_field(name="Attack", value=card.attack, inline=True)
        embed.add_field(name="Defense", value=card.defense, inline=True)
        embed.add_field(name="Speed", value=card.speed, inline=True)
        embed.add_field(name="Overall", value=card.overall, inline=True)
        embed.add_field(name="League", value=card.league, inline=True)
        embed.add_field(name="Nation", value=card.nation, inline=True)
        embed.add_field(name="Copies", value=card.copies, inline=True)
        embed.set_image(url=f"attachment://{card.image_path.split('/')[-1]}")
        embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)

        await ctx.author.send(embed=embed, file=discord.File(card.image_path))
        logger.info(f'{ctx.author.name} received a special card {card.name} (ID: {card.card_id}) using !pineappleonpizza')
    else:
        await ctx.author.send("An error occurred while processing your request.")
    
    conn.close()



#--------------------GERMANY

mannschaft_card_ids = [10417, 10447, 10449, 10452, 10463]

@bot.command(name='fubball')
@secret_command()
async def mannschaft(ctx):
    ensure_player_exists(ctx.author.id, ctx.author.name)
    
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute('SELECT mannschaft FROM players WHERE user_id = ?', (ctx.author.id,))
    used_command = cursor.fetchone()[0]

    if used_command:
        await ctx.author.send("You have already used this command.")
        return
    
    card_id = None
    attempts = 0
    max_attempts = len(mannschaft_card_ids) * 2  # To prevent potential infinite loops
    
    while attempts < max_attempts:
        attempts += 1
        card_id = random.choice(mannschaft_card_ids)
        cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (ctx.author.id, card_id))
        if cursor.fetchone() is None:
            break

    if card_id is None:
        await ctx.author.send("No available cards to add to your inventory.")
        conn.close()
        return

    card = get_card_by_id(card_id)
    if card:
        card.copies += 1
        add_card(card)
        add_card_to_inventory(ctx.author.id, card.card_id)

        cursor.execute('UPDATE players SET mannschaft = 1 WHERE user_id = ?', (ctx.author.id,))
        conn.commit()
        
        embed = discord.Embed(title="Special Drop", description="You have received a special card drop! Shh, don't tell anyone about this command.")
        embed.add_field(name="Name", value=card.name, inline=True)
        embed.add_field(name="ID", value=card.card_id, inline=True)
        embed.add_field(name="Rarity", value=card.card_rarity, inline=True)
        embed.add_field(name="Type", value=card.card_type, inline=True)
        embed.add_field(name="Attack", value=card.attack, inline=True)
        embed.add_field(name="Defense", value=card.defense, inline=True)
        embed.add_field(name="Speed", value=card.speed, inline=True)
        embed.add_field(name="Overall", value=card.overall, inline=True)
        embed.add_field(name="League", value=card.league, inline=True)
        embed.add_field(name="Nation", value=card.nation, inline=True)
        embed.add_field(name="Copies", value=card.copies, inline=True)
        embed.set_image(url=f"attachment://{card.image_path.split('/')[-1]}")
        embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)

        await ctx.author.send(embed=embed, file=discord.File(card.image_path))
        logger.info(f'{ctx.author.name} received a special card {card.name} (ID: {card.card_id}) using !mannschaft')
    else:
        await ctx.author.send("An error occurred while processing your request.")
    
    conn.close()


#--------------------NETHERLANDS

theflyingdutchmen_card_ids = [10420, 10422, 10424, 10432, 10433, 10448, 10456]

@bot.command(name='theflyingdutchmen')
@secret_command()
async def theflyingdutchmen(ctx):
    ensure_player_exists(ctx.author.id, ctx.author.name)
    
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute('SELECT theflyingdutchmen FROM players WHERE user_id = ?', (ctx.author.id,))
    used_command = cursor.fetchone()[0]

    if used_command:
        await ctx.author.send("You have already used this command.")
        return
    
    card_id = None
    attempts = 0
    max_attempts = len(theflyingdutchmen_card_ids) * 2  # To prevent potential infinite loops
    
    while attempts < max_attempts:
        attempts += 1
        card_id = random.choice(theflyingdutchmen_card_ids)
        cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (ctx.author.id, card_id))
        if cursor.fetchone() is None:
            break

    if card_id is None:
        await ctx.author.send("No available cards to add to your inventory.")
        conn.close()
        return

    card = get_card_by_id(card_id)
    if card:
        card.copies += 1
        add_card(card)
        add_card_to_inventory(ctx.author.id, card.card_id)

        cursor.execute('UPDATE players SET theflyingdutchmen = 1 WHERE user_id = ?', (ctx.author.id,))
        conn.commit()
        
        embed = discord.Embed(title="Special Drop", description="You have received a special card drop! Shh, don't tell anyone about this command.")
        embed.add_field(name="Name", value=card.name, inline=True)
        embed.add_field(name="ID", value=card.card_id, inline=True)
        embed.add_field(name="Rarity", value=card.card_rarity, inline=True)
        embed.add_field(name="Type", value=card.card_type, inline=True)
        embed.add_field(name="Attack", value=card.attack, inline=True)
        embed.add_field(name="Defense", value=card.defense, inline=True)
        embed.add_field(name="Speed", value=card.speed, inline=True)
        embed.add_field(name="Overall", value=card.overall, inline=True)
        embed.add_field(name="League", value=card.league, inline=True)
        embed.add_field(name="Nation", value=card.nation, inline=True)
        embed.add_field(name="Copies", value=card.copies, inline=True)
        embed.set_image(url=f"attachment://{card.image_path.split('/')[-1]}")
        embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)

        await ctx.author.send(embed=embed, file=discord.File(card.image_path))
        logger.info(f'{ctx.author.name} received a special card {card.name} (ID: {card.card_id}) using !theflyingdutchmen')
    else:
        await ctx.author.send("An error occurred while processing your request.")
    
    conn.close()

#--------------------FRANCE


blues_card_ids = [10398, 10410, 10419, 10421, 10426, 10439, 10467]

@bot.command(name='mayonnaise')
@secret_command()
async def blues(ctx):
    ensure_player_exists(ctx.author.id, ctx.author.name)
    
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute('SELECT blues FROM players WHERE user_id = ?', (ctx.author.id,))
    used_command = cursor.fetchone()[0]

    if used_command:
        await ctx.author.send("You have already used this command.")
        return
    
    card_id = None
    attempts = 0
    max_attempts = len(blues_card_ids) * 2  # To prevent potential infinite loops
    
    while attempts < max_attempts:
        attempts += 1
        card_id = random.choice(blues_card_ids)
        cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (ctx.author.id, card_id))
        if cursor.fetchone() is None:
            break

    if card_id is None:
        await ctx.author.send("No available cards to add to your inventory.")
        conn.close()
        return

    card = get_card_by_id(card_id)
    if card:
        card.copies += 1
        add_card(card)
        add_card_to_inventory(ctx.author.id, card.card_id)

        cursor.execute('UPDATE players SET blues = 1 WHERE user_id = ?', (ctx.author.id,))
        conn.commit()
        
        embed = discord.Embed(title="Special Drop", description="You have received a special card drop! Shh, don't tell anyone about this command.")
        embed.add_field(name="Name", value=card.name, inline=True)
        embed.add_field(name="ID", value=card.card_id, inline=True)
        embed.add_field(name="Rarity", value=card.card_rarity, inline=True)
        embed.add_field(name="Type", value=card.card_type, inline=True)
        embed.add_field(name="Attack", value=card.attack, inline=True)
        embed.add_field(name="Defense", value=card.defense, inline=True)
        embed.add_field(name="Speed", value=card.speed, inline=True)
        embed.add_field(name="Overall", value=card.overall, inline=True)
        embed.add_field(name="League", value=card.league, inline=True)
        embed.add_field(name="Nation", value=card.nation, inline=True)
        embed.add_field(name="Copies", value=card.copies, inline=True)
        embed.set_image(url=f"attachment://{card.image_path.split('/')[-1]}")
        embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)

        await ctx.author.send(embed=embed, file=discord.File(card.image_path))
        logger.info(f'{ctx.author.name} received a special card {card.name} (ID: {card.card_id}) using !blues')
    else:
        await ctx.author.send("An error occurred while processing your request.")
    
    conn.close()

#---------------------------------------------------------FACTS-------------------------------------------------------------------------------------


facts_list = [
    "England's last FIFA World Cup victory was in 1966, when they triumphed at Wembley Stadium, a moment the fans nostalgically reminisce about, since when they began their famous chant.",
    "Football is one of the best things to come out of France after their food, with their iconic 1998 World Cup victory on home soil showcasing the team's flair and elegance.",
    "Brazil holds the record for the most FIFA World Cup wins, with a total of five championships, known for their beautiful style of play that dazzles spectators.",
    "The Netherlands, known for their 'Total Football' style, reached the FIFA World Cup final three times but have yet to win the tournament, earning the nickname for their graceful and flying playstyle.",
    "Italy is renowned for its strong defensive tactics and has won the FIFA World Cup four times, with their latest victory in 2006, a victory almost as controversial as certain tropical toppings on pizza.",
    "Germany has a storied football history, having won the FIFA World Cup four times, and is known for their consistent performance in international tournaments, embodying the spirit of their beloved 'Fußball'."
]

@bot.hybrid_command(name='facts', description="Get a random football fact")
async def facts(ctx):
    fact = random.choice(facts_list)
    embed = discord.Embed(title="Football Fact", description=fact, color=discord.Color.blue())
    await ctx.send(embed=embed)
    logger.info(f'{ctx.author.name} used the facts command and received: {fact}')
#---------------------------------------------------------CARDS AND PLAYERS CLASS-------------------------------------------------------------------------------------


class Card:
    # Added 'wishlist_count' and '*args' to the end of the argument list
    def __init__(self, card_id, player_id, name, attack, defense, speed, height, club, position, overall, image_path, card_rarity=None, card_type='standard', league=None, nation=None, copies=0, wishlist_count=0, *args):
        self.card_id = card_id
        self.player_id = player_id
        self.name = name
        self.attack = attack
        self.defense = defense
        self.speed = speed
        self.height = height
        self.club = club
        self.position = position
        self.overall = overall if overall is not None else 0
        self.image_path = image_path
        self.card_rarity = card_rarity if card_rarity else determine_card_rarity(overall)
        self.card_type = card_type
        self.league = league
        self.nation = nation
        self.copies = copies
        self.wishlist_count = wishlist_count


class Player:
    def __init__(self, user_id, name, battles_played=0, battles_won=0, battles_lost=0, has_claimed_starter_pack=False):
        self.user_id = user_id
        self.name = name
        self.battles_played = battles_played
        self.battles_won = battles_won
        self.battles_lost = battles_lost
        self.has_claimed_starter_pack = has_claimed_starter_pack
        self.selected_deck = None
        self.decks = {}

def ensure_player_exists(user_id, user_name):
    cursor.execute('SELECT * FROM players WHERE user_id = ?', (user_id,))
    if cursor.fetchone() is None:
        cursor.execute('INSERT INTO players (user_id, name) VALUES (?, ?)', (user_id, user_name))
        conn.commit()

def add_card(card):
    card_rarity = determine_card_rarity(card.overall)
    cursor.execute('''
    INSERT INTO cards (card_id, player_id, name, attack, defense, speed, height, club, position, overall, image_path, card_rarity, card_type, league, nation, copies)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(card_id) DO UPDATE SET copies = copies + 1
    ''', (card.card_id, card.player_id, card.name, card.attack, card.defense, card.speed, card.height, card.club, card.position, card.overall, card.image_path, card_rarity, card.card_type, card.league, card.nation, card.copies))
    conn.commit()


def get_card_by_name(card_name):
    cursor.execute('SELECT * FROM cards')
    rows = cursor.fetchall()
    cards = [Card(*row) for row in rows]
    card_names = [card.name.lower() for card in cards]
    best_match = process.extractOne(card_name.lower(), card_names)
    if best_match:
        best_match_index = card_names.index(best_match[0])
        return cards[best_match_index]
    return None


def add_card_to_inventory(user_id, card_id):
    cursor.execute('SELECT copies FROM cards WHERE card_id = ?', (card_id,))
    current_copies = cursor.fetchone()[0]
    edition = current_copies + 1
    cursor.execute('UPDATE cards SET copies = copies + 1 WHERE card_id = ?', (card_id,))
    cursor.execute('INSERT INTO inventories (user_id, card_id, edition) VALUES (?, ?, ?)', (user_id, card_id, edition))
    conn.commit()




def get_player_inventory(user_id):
    cursor.execute('''
    SELECT cards.*, inventories.edition FROM cards
    JOIN inventories ON cards.card_id = inventories.card_id
    WHERE inventories.user_id = ?
    ORDER BY cards.overall DESC
    ''', (user_id,))
    rows = cursor.fetchall()
    return [Card(*row[:-1]) for row in rows], [row[-1] for row in rows]

def fetch_all_cards():
    cursor.execute('SELECT * FROM cards')
    rows = cursor.fetchall()
    return [Card(*row) for row in rows]


all_cards = fetch_all_cards()

# Card types excluded from all pack drops and random card drops
NON_DROPPABLE_TYPES = ('Unique',)
_ndt_sql = ','.join('?' * len(NON_DROPPABLE_TYPES))  # placeholder string for SQL NOT IN

# Define weights for each overall rating range
weight_70_79 = 70
weight_80_85 = 20
weight_86_90 = 7
weight_90_plus = 3
weight_hero = 2
weight_icon_80 = 2
weight_icon_90 = 1
weight_euro_tott = 1
weight_copa_tott = 1

cards_with_weights = [(card, weight_70_79) for card in all_cards if 70 <= card.overall <= 79 and card.card_type == 'Standard'] + \
                     [(card, weight_80_85) for card in all_cards if 80 <= card.overall <= 85 and card.card_type == 'Standard'] + \
                     [(card, weight_86_90) for card in all_cards if 86 <= card.overall <= 90 and card.card_type == 'Standard'] + \
                     [(card, weight_90_plus) for card in all_cards if card.overall > 90 and card.card_type == 'Standard'] + \
                     [(card, weight_hero) for card in all_cards if card.card_type == 'Hero'] + \
                     [(card, weight_icon_80) for card in all_cards if 80 <= card.overall <= 89 and card.card_type == 'Icon'] + \
                     [(card, weight_icon_90) for card in all_cards if card.overall >= 90 and card.card_type == 'Icon']  + \
                     [(card, weight_euro_tott) for card in all_cards if card.card_type == 'Euro TOTT']  + \
                     [(card, weight_euro_tott) for card in all_cards if card.card_type == 'Copa America TOTT']

def get_card_weight_by_name(card_name):
    card = get_card_by_name(card_name)
    if not card:
        return None, None

    cursor = conn.cursor()

    if card.card_type == 'Standard':
        cursor.execute('SELECT COUNT(*) FROM cards WHERE card_type = "Standard"')
        total_standard_cards = cursor.fetchone()[0]

        if 70 <= card.overall <= 79:
            return weight_70_79 / total_standard_cards, card.name
        elif 80 <= card.overall <= 85:
            return weight_80_85 / total_standard_cards, card.name
        elif 86 <= card.overall <= 90:
            return weight_86_90 / total_standard_cards, card.name
        elif card.overall > 90:
            return weight_90_plus / total_standard_cards, card.name
        
    else:
        cursor.execute('SELECT COUNT(*) FROM cards WHERE card_type != "Standard"')
        total_non_standard_cards = cursor.fetchone()[0]
        return 3 / total_non_standard_cards, card.name



@bot.hybrid_command(name='weight', description="Check the pack weight of a card")
async def weight(ctx, *, card_name: str):
    weight, actual_card_name = get_card_weight_by_name(card_name)
    if weight:
        embed = discord.Embed(title=f"Card Weight: {actual_card_name}", color=0x00ff00)
        embed.add_field(name="Pack Weight", value=f"{weight:.6f}", inline=False)
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(title="Card Not Found", color=0xff0000)
        embed.add_field(name="Error", value=f"Card '{card_name}' not found or does not have a defined weight.", inline=False)
        await ctx.send(embed=embed)


class CollectButton(discord.ui.Button):
    def __init__(self, card):
        super().__init__(style=discord.ButtonStyle.green, label="Collect", custom_id="collect_card")
        self.card = card

    async def callback(self, interaction: discord.Interaction):
        # 1. Add to database
        ensure_player_exists(interaction.user.id, interaction.user.name)
        try:
            add_card_to_inventory(interaction.user.id, self.card.card_id)
        except ValueError:
            return await interaction.response.send_message("You already have this card!", ephemeral=True)

        # 2. Create the "Collected" Embed
        embed = discord.Embed(
            title="✅ Card Collected!",
            description=f"**{self.card.name}** has been collected by {interaction.user.mention}!",
            color=discord.Color.gold()
        )
        embed.set_image(url=f"attachment://{self.card.image_path.split('/')[-1]}")
        
        # ROW 1: Stats (Overall added to start, Speed icon changed to Lightning)
        embed.add_field(
            name="Stats", 
            value=f"⭐ {self.card.overall} | ⚔️ {self.card.attack} | 🛡️ {self.card.defense} | ⚡ {self.card.speed}", 
            inline=False
        )

        # ROW 2: Card Details (ID, Rarity, Copies)
        # Note: We add +1 to copies because the drop itself generated a new copy
        embed.add_field(
            name="Card Details",
            value=f"ID: {self.card.card_id} | Rarity: {self.card.card_rarity} | Total Copies: {self.card.copies + 1}",
            inline=False
        )

        embed.set_footer(text=f"Winner: {interaction.user.name}", icon_url=interaction.user.display_avatar.url)

        # 3. Edit the Original Message and remove buttons
        await interaction.response.edit_message(embed=embed, view=None)
        
        # 4. Stop the view logic
        self.view.stop()

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user}')
    card_drop.start()
    expire_market_listings.start()
    bot.loop.create_task(_start_dashboard_api())

def weighted_choice(cards_with_weights):
    total = sum(weight for card, weight in cards_with_weights)
    r = random.uniform(0, total)
    upto = 0
    for card, weight in cards_with_weights:
        if upto + weight >= r:
            return card
        upto += weight


#---------------------------------------------------------AUTO DROP-------------------------------------------------------------------------------------
# Helper to run one drop in one channel
async def handle_single_drop(channel, card):
    try:
        embed = discord.Embed(
            title="🎁 Random Card Drop!", 
            description="Be the first to click **Collect** to claim this card!",
            color=discord.Color.blue()
        )
        embed.set_image(url=f"attachment://{card.image_path.split('/')[-1]}")
        embed.set_footer(text="Hurry! This drop expires in 2 minutes.")

        # Use our custom view that tracks the collected state
        view = DropView(timeout=120)
        view.add_item(TimedCollectButton(card, None)) # Passing None as owner_id since auto-drops have no owner priority

        msg = await channel.send(embed=embed, view=view, file=discord.File(card.image_path))
        
        # Wait until button clicked OR timeout
        await view.wait()
        
        # If the view stopped and it was NOT collected, it must have timed out
        if not view.collected:
            # Create expired embed
            expired_embed = discord.Embed(
                title="❌ Drop Expired", 
                description=f"No one collected **{card.name}** in time.", 
                color=discord.Color.red()
            )
            expired_embed.set_image(url=f"attachment://{card.image_path.split('/')[-1]}")
            
            # Remove the button by setting view=None
            await msg.edit(embed=expired_embed, view=None)
            
    except Exception as e:
        logger.error(f"Error dropping in channel {channel.id}: {e}")

@tasks.loop(minutes=30)
async def card_drop():
    await bot.wait_until_ready()
    
    # 1. Choose ONE card for this cycle
    card = weighted_choice(cards_with_weights)
    add_card(card)

    # 2. Drop it in EVERY configured channel
    for channel_id in DROP_CHANNEL_IDS:
        channel = bot.get_channel(channel_id)
        if channel:
            # Run in background so Channel 2 doesn't wait for Channel 1
            bot.loop.create_task(handle_single_drop(channel, card))
        else:
            logger.error(f"Could not find drop channel ID: {channel_id}")



#---------------------------------------------------------STARTER PACK-------------------------------------------------------------------------------------

@bot.hybrid_command(name='get_starter_pack', description="Claim your free starter cards")
async def get_starter_pack(ctx):
    ensure_player_exists(ctx.author.id, ctx.author.name)
    cursor.execute('SELECT has_claimed_starter_pack FROM players WHERE user_id = ?', (ctx.author.id,))
    has_claimed_starter_pack = cursor.fetchone()[0]
    if has_claimed_starter_pack:
        await ctx.send("You have already claimed your starter pack!")
        return

    common_pack = random.sample([card for card in all_cards if 70 <= card.overall <= 79 and card.card_type not in NON_DROPPABLE_TYPES], 6)
    uncommon_pack = random.sample([card for card in all_cards if 80 <= card.overall <= 85 and card.card_type not in NON_DROPPABLE_TYPES], 3)
    rare_pack = random.sample([card for card in all_cards if card.overall > 85 and card.card_type == 'Standard'], 1)

    all_cards_received = common_pack + uncommon_pack + rare_pack

    for card in all_cards_received:
        increment_card_copies(card.card_id)
        add_card_to_inventory(ctx.author.id, card.card_id)
        

    cursor.execute('UPDATE players SET has_claimed_starter_pack = 1 WHERE user_id = ?', (ctx.author.id,))
    conn.commit()

    card_names = "\n".join([f"{card.name} (ID: {card.card_id})" for card in all_cards_received])
    await ctx.send(f"**{ctx.author.name} has claimed their starter pack!**\nYou received:\n{card_names}")
    logger.info(f'{ctx.author.name} claimed a starter pack')

def increment_card_copies(card_id):
    cursor.execute('UPDATE cards SET copies = copies + 1 WHERE card_id = ?', (card_id,))
    conn.commit()

#---------------------------------------------------------VIEW-------------------------------------------------------------------------------------

def get_card_by_name_or_id(identifier):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    
    if identifier.isdigit():
        cursor.execute('SELECT * FROM cards WHERE card_id = ?', (int(identifier),))
        rows = cursor.fetchall()
    else:
        cursor.execute('SELECT DISTINCT player_id FROM cards WHERE LOWER(name) LIKE ?', ('%' + identifier.lower() + '%',))
        player_ids = cursor.fetchall()
        
        if player_ids:
            player_ids = [pid[0] for pid in player_ids]
            query = 'SELECT * FROM cards WHERE player_id IN ({})'.format(','.join('?' for _ in player_ids))
            cursor.execute(query, player_ids)
            rows = cursor.fetchall()
        else:
            rows = []

    conn.close()
    return [Card(*row) for row in rows]




from discord.ui import Select, View

class ViewCardSelect(discord.ui.Select):
    def __init__(self, cards, user, ctx):
        # Added ctx to init so we can pass it to the View later
        options = [discord.SelectOption(label=f"{card.name} - {card.card_type}", description=f"ID: {card.card_id} | OVR: {card.overall}", value=f"{card.card_id}-{i}") for i, card in enumerate(cards)]
        super().__init__(placeholder="Select the card...", min_values=1, max_values=1, options=options)
        self.cards = cards
        self.user = user
        self.ctx = ctx

    async def callback(self, interaction: discord.Interaction):
        # 1. Identify Selected Card
        selected_card_id, _ = self.values[0].split('-')
        selected_card_id = int(selected_card_id)
        card = next(c for c in self.cards if c.card_id == selected_card_id)

        # 2. Fetch Advanced Stats from DB
        conn = sqlite3.connect('cards_game.db')
        cursor = conn.cursor()
        
        # Ownership
        cursor.execute('SELECT edition, trade_count FROM inventories WHERE user_id = ? AND card_id = ? ORDER BY edition', (self.user.id, card.card_id))
        user_copies = cursor.fetchall()
        
        # Global Stats
        cursor.execute('''
            SELECT wishlist_count, 
                   total_battles_played, total_battles_won, 
                   total_rounds_played, total_rounds_won 
            FROM cards WHERE card_id = ?
        ''', (card.card_id,))
        row = cursor.fetchone()
        
        wl_count = row[0] if row else 0
        g_b_played = row[1] if row else 0
        g_b_won = row[2] if row else 0
        g_r_played = row[3] if row else 0
        g_r_won = row[4] if row else 0

        # Wishlist State (for button)
        cursor.execute('SELECT 1 FROM wishlists WHERE user_id = ? AND card_id = ?', (self.user.id, card.card_id))
        is_wishlisted = cursor.fetchone() is not None
        
        conn.close()

        win_rate = f"{(g_b_won / g_b_played * 100):.1f}%" if g_b_played > 0 else "0%"

        # 3. Build the New Embed Style
        embed = discord.Embed(title=f"**{card.name}**", color=discord.Color.blue())

        embed.add_field(name="Info", value=f"🆔 {card.card_id}\n💎 {card.card_rarity}\n🏆 {card.card_type}", inline=True)
        embed.add_field(name="Base Stats", value=f"⭐ **{card.overall}** | ⚔️ {card.attack} | 🛡️ {card.defense} | ⚡ {card.speed}", inline=True)

        meta_stats = (
            f"❤️ **{wl_count}** Wishlists\n"
            f"⚔️ **Battles:** {g_b_won}/{g_b_played} ({win_rate})\n"
            f"🔄 **Rounds:** {g_r_won}/{g_r_played}"
        )
        embed.add_field(name="Global Statistics", value=meta_stats, inline=False)

        if user_copies:
            edition_list = ', '.join(f'#{r[0]}' for r in user_copies)
            if len(user_copies) == 1:
                ownership = "First Owner" if user_copies[0][1] == 0 else "Traded In"
                inv_value = f"✅ 1 copy — Edition {edition_list} ({ownership})"
            else:
                inv_value = f"✅ {len(user_copies)} copies — Editions: {edition_list}"
            embed.add_field(name="Your Inventory", value=inv_value, inline=True)

        embed.set_footer(text=f"Requested by {self.user.name}", icon_url=self.user.display_avatar.url)

        # 4. Create View with Button
        # We use self.ctx from init. If not available, we can rely on interaction.user check inside button
        view = CardDetailsView(self.ctx, card.card_id, is_wishlisted)

        # 5. Send/Edit Message
        # Since we are replying to the dropdown interaction, we use response.send_message
        # We attach the file and the view.
        await interaction.response.send_message(embed=embed, file=discord.File(card.image_path), view=view)
        logger.info(f'{self.user.name} viewed card {card.name} (ID: {card.card_id}) via selection')

class ViewCardSelectView(discord.ui.View):
    def __init__(self, cards, user, ctx):
        super().__init__(timeout=60)
        # Pass ctx here ----------------v
        self.add_item(ViewCardSelect(cards, user, ctx))


class ToggleWishlistButton(discord.ui.Button):
    def __init__(self, card_id, is_wishlisted):
        label = "Remove from Wishlist" if is_wishlisted else "Add to Wishlist"
        emoji = "💔" if is_wishlisted else "❤️"
        style = discord.ButtonStyle.red if is_wishlisted else discord.ButtonStyle.secondary
        
        super().__init__(style=style, label=label, emoji=emoji, custom_id=f"wl_toggle_{card_id}")
        self.card_id = card_id
        self.is_wishlisted = is_wishlisted

    async def callback(self, interaction: discord.Interaction):
        conn = sqlite3.connect('cards_game.db')
        cursor = conn.cursor()

        try:
            # 1. Toggle Wishlist Logic
            cursor.execute('SELECT 1 FROM wishlists WHERE user_id = ? AND card_id = ?', (interaction.user.id, self.card_id))
            exists = cursor.fetchone()

            if exists:
                cursor.execute('DELETE FROM wishlists WHERE user_id = ? AND card_id = ?', (interaction.user.id, self.card_id))
                cursor.execute('UPDATE cards SET wishlist_count = MAX(0, wishlist_count - 1) WHERE card_id = ?', (self.card_id,))
                
                # Update Button Visuals
                self.style = discord.ButtonStyle.secondary
                self.label = "Add to Wishlist"
                self.emoji = "❤️"
                self.is_wishlisted = False
                action_msg = "Removed from your wishlist."
            else:
                cursor.execute('INSERT INTO wishlists (user_id, card_id) VALUES (?, ?)', (interaction.user.id, self.card_id))
                cursor.execute('UPDATE cards SET wishlist_count = wishlist_count + 1 WHERE card_id = ?', (self.card_id,))
                
                # Update Button Visuals
                self.style = discord.ButtonStyle.red
                self.label = "Remove from Wishlist"
                self.emoji = "💔"
                self.is_wishlisted = True
                action_msg = "Added to your wishlist."

            conn.commit()

            # 2. Fetch Fresh Stats to Rebuild Embed
            cursor.execute('''
                SELECT wishlist_count, 
                       total_battles_played, total_battles_won, 
                       total_rounds_played, total_rounds_won 
                FROM cards WHERE card_id = ?
            ''', (self.card_id,))
            
            row = cursor.fetchone()
            # Safety defaults
            wl_count = row[0] if row else 0
            g_b_played = row[1] if row else 0
            g_b_won = row[2] if row else 0
            g_r_played = row[3] if row else 0
            g_r_won = row[4] if row else 0
            
            conn.close()

            # 3. Reconstruct the Field Text
            win_rate = f"{(g_b_won / g_b_played * 100):.1f}%" if g_b_played > 0 else "0%"
            
            new_meta_stats = (
                f"❤️ **{wl_count}** Wishlists\n"
                f"⚔️ **Battles:** {g_b_won}/{g_b_played} ({win_rate})\n"
                f"🔄 **Rounds:** {g_r_won}/{g_r_played}"
            )

            # 4. Update the Message Embed
            embed = interaction.message.embeds[0]
            
            # Find the "Global Statistics" field and update it
            for i, field in enumerate(embed.fields):
                if field.name == "Global Statistics":
                    embed.set_field_at(i, name="Global Statistics", value=new_meta_stats, inline=False)
                    break
            
            # Ensure image stays on top (by not setting it in embed)
            embed.set_image(url=None) 
            
            await interaction.response.edit_message(embed=embed, view=self.view)
            await interaction.followup.send(f"✅ {action_msg}", ephemeral=True)

        except Exception as e:
            if conn: conn.close()
            print(f"Wishlist Button Error: {e}")
            await interaction.response.send_message("Error updating wishlist.", ephemeral=True)

class CardDetailsView(discord.ui.View):
    def __init__(self, ctx, card_id, is_wishlisted):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.add_item(ToggleWishlistButton(card_id, is_wishlisted))

    # --- SECURITY CHECK ---
    # This prevents other users from clicking the button
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("⛔ This is not your menu. Run `/view` yourself to wishlist this card!", ephemeral=True)
            return False
        return True



#----------------------------------------------------------VIEW COMMAND-------------------------------------------------------------------------------------

async def card_search_autocomplete(interaction: discord.Interaction, current: str) -> List[discord.app_commands.Choice[str]]:
    if not current:
        return []

    matches = []
    count = 0
    
    for card in all_cards:
        # Check if search term is in name (case insensitive); hide Unique cards from autocomplete
        if current.lower() in card.name.lower() and card.card_type not in NON_DROPPABLE_TYPES:
            matches.append(card)
            count += 1
            if count == 25: # Discord limit
                break

    return [
        discord.app_commands.Choice(
            name=f"{card.name} | ⭐ {card.overall} | {card.card_type}", 
            value=str(card.card_id) # Pass the ID as the value
        )
        for card in matches
    ]



@bot.hybrid_command(name='view', description="View details of a card")
@app_commands.describe(player_name="Search for a player...")
@app_commands.autocomplete(player_name=card_search_autocomplete)
async def view(ctx, *, player_name: str):
    ensure_player_exists(ctx.author.id, ctx.author.name)
    
    # 1. Check if input is a Card ID (from Autocomplete or manual ID)
    if player_name.isdigit():
        card = get_card_by_id(int(player_name))
        cards = [card] if card else []
    else:
        # 2. Fallback: Search by name text
        cards = get_card_by_name_or_id(player_name)
    
    # Remove Unique cards the requesting user doesn't own
    visible = []
    for c in cards:
        if c.card_type not in NON_DROPPABLE_TYPES:
            visible.append(c)
        else:
            cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (ctx.author.id, c.card_id))
            if cursor.fetchone():
                visible.append(c)
    cards = visible

    if cards:
        if len(cards) == 1:
            card = cards[0]

            conn = sqlite3.connect('cards_game.db')
            cursor = conn.cursor()
            
            cursor.execute('SELECT edition, trade_count FROM inventories WHERE user_id = ? AND card_id = ? ORDER BY edition', (ctx.author.id, card.card_id))
            user_copies = cursor.fetchall()
            
            cursor.execute('''
                SELECT wishlist_count, 
                       total_battles_played, total_battles_won, 
                       total_rounds_played, total_rounds_won 
                FROM cards WHERE card_id = ?
            ''', (card.card_id,))
            
            row = cursor.fetchone()
            wl_count = row[0] if row else 0
            g_b_played = row[1] if row else 0
            g_b_won = row[2] if row else 0
            g_r_played = row[3] if row else 0
            g_r_won = row[4] if row else 0
            
            cursor.execute('SELECT 1 FROM wishlists WHERE user_id = ? AND card_id = ?', (ctx.author.id, card.card_id))
            is_wishlisted = cursor.fetchone() is not None
            
            conn.close()
            
            win_rate = f"{(g_b_won / g_b_played * 100):.1f}%" if g_b_played > 0 else "0%"

            embed = discord.Embed(title=f"**{card.name}**", color=discord.Color.blue())
            embed.add_field(name="Info", value=f"🆔 {card.card_id}\n💎 {card.card_rarity}\n🏆 {card.card_type}", inline=True)
            embed.add_field(name="Base Stats", value=f"⭐ **{card.overall}** | ⚔️ {card.attack} | 🛡️ {card.defense} | ⚡ {card.speed}", inline=True)
            
            meta_stats = (
                f"❤️ **{wl_count}** Wishlists\n"
                f"⚔️ **Battles:** {g_b_won}/{g_b_played} ({win_rate})\n"
                f"🔄 **Rounds:** {g_r_won}/{g_r_played}"
            )
            embed.add_field(name="Global Statistics", value=meta_stats, inline=False)
            
            if user_copies:
                edition_list = ', '.join(f'#{r[0]}' for r in user_copies)
                if len(user_copies) == 1:
                    ownership = "First Owner" if user_copies[0][1] == 0 else "Traded In"
                    inv_value = f"✅ 1 copy — Edition {edition_list} ({ownership})"
                else:
                    inv_value = f"✅ {len(user_copies)} copies — Editions: {edition_list}"
                embed.add_field(name="Your Inventory", value=inv_value, inline=True)

            embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)

            view = CardDetailsView(ctx, card.card_id, is_wishlisted)
            await ctx.send(embed=embed, file=discord.File(card.image_path), view=view)
            
            logger.info(f'{ctx.author.name} viewed card {card.name}')

        else:
            view = ViewCardSelectView(cards, ctx.author, ctx)
            await ctx.send("Multiple cards found, please select one:", view=view)
    else:
        await ctx.send(f'No card found matching "{player_name}"')



#---------------------------------------------------------LOOKUP-------------------------------------------------------------------------------------

class LookupEditionSelect(discord.ui.Select):
    def __init__(self, rows, target_user, card_id_int):
        self.rows_map = {str(row[9]): row for row in rows}
        self.target_user = target_user
        self.card_id_int = card_id_int
        options = [
            discord.SelectOption(
                label=f"Edition #{row[9]}/{row[8]}",
                description=f"Battles: {row[11]}/{row[10]}",
                value=str(row[9])
            )
            for row in rows
        ]
        super().__init__(placeholder="Select an edition to inspect...", options=options)

    async def callback(self, interaction: discord.Interaction):
        row = self.rows_map[self.values[0]]
        name, overall, atk, def_, spd, rarity, type_, image_path, total_copies, edition, b_played, b_won, r_played, r_won = row
        edition_str = f"#{edition}/{total_copies}"
        win_rate = f"{(b_won / b_played * 100):.1f}%" if b_played > 0 else "0%"

        await interaction.response.defer()
        try:
            avatar_bytes = await self.target_user.display_avatar.read()
        except:
            avatar_bytes = None

        image_buffer = await interaction.client.loop.run_in_executor(
            None, generate_minted_card, image_path, avatar_bytes, self.target_user.name, edition_str
        )
        if not image_buffer:
            return await interaction.followup.send("❌ Error generating card image.")

        await interaction.edit_original_response(
            embed=discord.Embed(description=f"Loaded edition #{edition}", color=discord.Color.gold()),
            view=None
        )
        file = discord.File(fp=image_buffer, filename=f"minted_{self.card_id_int}.png")
        embed = discord.Embed(title=f"🔍 Card Inspection: {name}", color=discord.Color.gold())
        embed.set_author(name=f"Property of {self.target_user.name}", icon_url=self.target_user.display_avatar.url)
        embed.add_field(name="Mint Details", value=f"🆔 **ID:** {self.card_id_int}\n#️⃣ **Edition:** {edition_str}", inline=True)
        embed.add_field(name="Card Info", value=f"💎 {rarity}\n🏆 {type_}", inline=True)
        embed.add_field(name="Base Stats", value=f"⭐ **{overall}** | ⚔️ {atk} | 🛡️ {def_} | ⚡ {spd}", inline=False)
        stats_text = f"⚔️ **Battles:** {b_won}/{b_played} ({win_rate})\n🔄 **Rounds:** {r_won}/{r_played}"
        embed.add_field(name="Match Record (This Copy)", value=stats_text, inline=False)
        await interaction.followup.send(file=file, embed=embed)


class LookupEditionView(discord.ui.View):
    def __init__(self, rows, target_user, card_id_int):
        super().__init__(timeout=60)
        self.add_item(LookupEditionSelect(rows, target_user, card_id_int))
        self.message = None

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            await self.message.edit(view=self)


def generate_minted_card(card_path, avatar_bytes, owner_name, edition_text):
    try:
        # 1. Load Base Card
        card_img = Image.open(card_path).convert("RGBA")
        card_w, card_h = card_img.size
        draw = ImageDraw.Draw(card_img)

        # --- DYNAMIC SCALING MATH ---
        # We base everything on the card's width to ensure readability
        # Example: On a 1000px card, font will be 45px. On a 2000px card, it becomes 90px.
        scale_factor = card_w / 1000 
        
        font_size_main = int(45 * scale_factor) # Base size 45
        pfp_size = int(90 * scale_factor)       # Base size 90
        padding = int(20 * scale_factor)        # Padding 20
        edge_margin = int(30 * scale_factor)    # Margin from edge 30
        border_width = int(3 * scale_factor)    # Border thickness
        corner_radius = int(25 * scale_factor)  # Pill roundness

        # Ensure minimum visible sizes for very small cards
        font_size_main = max(20, font_size_main)
        pfp_size = max(40, pfp_size)

        # --- Setup Fonts ---
        try:
            font_owner = ImageFont.truetype("arialbd.ttf", font_size_main)
            font_edition = ImageFont.truetype("arialbd.ttf", font_size_main)
        except:
            font_owner = ImageFont.load_default()
            font_edition = ImageFont.load_default()

        # ==============================================================================
        # LEFT BOTTOM: OWNER TAG (PFP + Name)
        # ==============================================================================
        
        # Calculate text dimensions
        owner_bbox = draw.textbbox((0, 0), owner_name, font=font_owner)
        owner_text_w = owner_bbox[2] - owner_bbox[0]
        owner_text_h = owner_bbox[3] - owner_bbox[1]
        
        # Pill Dimensions
        owner_pill_w = pfp_size + owner_text_w + (padding * 3)
        owner_pill_h = pfp_size + padding

        # Create Pill
        owner_pill = Image.new("RGBA", (owner_pill_w, owner_pill_h), (0, 0, 0, 0))
        pill_draw = ImageDraw.Draw(owner_pill)

        # Draw Background
        pill_draw.rounded_rectangle(
            [(0, 0), (owner_pill_w, owner_pill_h)],
            radius=corner_radius,
            fill=(20, 20, 20, 245), # Very dark background for contrast
            outline=(255, 255, 255, 150), 
            width=max(1, int(border_width/2))
        )

        # Paste Avatar
        if avatar_bytes:
            avatar_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            avatar_img = avatar_img.resize((pfp_size, pfp_size), Image.Resampling.LANCZOS)
            
            mask = Image.new("L", (pfp_size, pfp_size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, pfp_size, pfp_size), fill=255)
            circular_avatar = ImageOps.fit(avatar_img, mask.size, centering=(0.5, 0.5))
            circular_avatar.putalpha(mask)
            
            # Vertically center PFP in pill
            pfp_y = (owner_pill_h - pfp_size) // 2
            owner_pill.paste(circular_avatar, (padding, pfp_y), circular_avatar)

        # Draw Name
        text_x = padding * 2 + pfp_size
        # Vertically center text
        text_y = (owner_pill_h - owner_text_h) // 2 - int(5 * scale_factor)
        pill_draw.text((text_x, text_y), owner_name, font=font_owner, fill="white")

        # Paste onto Card (Bottom Left)
        card_img.paste(owner_pill, (edge_margin, card_h - owner_pill_h - edge_margin), owner_pill)


        # ==============================================================================
        # RIGHT BOTTOM: EDITION PLATE
        # ==============================================================================
        ed_bbox = draw.textbbox((0, 0), edition_text, font=font_edition)
        ed_w = ed_bbox[2] - ed_bbox[0]
        ed_h = ed_bbox[3] - ed_bbox[1]

        plate_w = ed_w + (padding * 4)
        plate_h = owner_pill_h # Match height of owner pill for symmetry

        # Create Plate
        plate_img = Image.new("RGBA", (plate_w, plate_h), (0, 0, 0, 0))
        plate_draw = ImageDraw.Draw(plate_img)

        gold_border = (218, 165, 32, 255)
        gold_text = (255, 223, 0, 255)
        dark_fill = (30, 30, 30, 245)

        # Draw Background
        plate_draw.rounded_rectangle(
            [(0, 0), (plate_w, plate_h)],
            radius=corner_radius,
            fill=dark_fill,
            outline=gold_border,
            width=border_width
        )

        # Draw Text
        text_pos_x = (plate_w - ed_w) // 2
        text_pos_y = (plate_h - ed_h) // 2 - int(5 * scale_factor)
        plate_draw.text((text_pos_x, text_pos_y), edition_text, font=font_edition, fill=gold_text)

        # Paste onto Card (Bottom Right)
        card_img.paste(plate_img, (card_w - plate_w - edge_margin, card_h - plate_h - edge_margin), plate_img)

        # --- Finalize ---
        buffer = io.BytesIO()
        card_img.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    except Exception as e:
        print(f"Error generating minted card: {e}")
        import traceback
        traceback.print_exc()
        return None


@bot.hybrid_command(name='lookup', aliases=['lu'], description="Inspect a specific card owned by a user (Visual Slab)")
@app_commands.describe(card="Search for the card to inspect...")
@app_commands.autocomplete(card=card_search_autocomplete)
async def lookup(ctx, card: str, user: discord.User = None):
    # Defer immediately as image generation can take time
    await ctx.defer()

    target_user = user or ctx.author
    ensure_player_exists(target_user.id, target_user.name)

    # 1. Resolve Card ID
    if card.isdigit():
        card_id_int = int(card)
    else:
        # If they typed a name blindly without clicking (rare), try to find it
        found_card = get_card_by_name(card)
        if not found_card:
            return await ctx.send(f"❌ Could not find card: {card}")
        card_id_int = found_card.card_id

    # 2. Database Check
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT c.name, c.overall, c.attack, c.defense, c.speed, 
               c.card_rarity, c.card_type, c.image_path, c.copies, i.edition,
               i.battles_played, i.battles_won, i.rounds_played, i.rounds_won
        FROM inventories i
        JOIN cards c ON i.card_id = c.card_id
        WHERE i.user_id = ? AND i.card_id = ?
    ''', (target_user.id, card_id_int))
    
    results = cursor.fetchall()
    conn.close()

    # 3. Handle "User doesn't own it"
    if not results:
        return await ctx.send(f"❌ **{target_user.name}** does not own Card ID `{card_id_int}`.")

    # 4a. Multiple copies — show edition picker
    if len(results) > 1:
        view = LookupEditionView(results, target_user, card_id_int)
        embed = discord.Embed(
            title=f"📋 Multiple Editions — {results[0][0]}",
            description=f"**{target_user.name}** owns **{len(results)} copies**. Select an edition to inspect.",
            color=discord.Color.gold()
        )
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg
        return

    # 4b. Single copy — generate slab directly
    name, overall, atk, def_, spd, rarity, type_, image_path, total_copies, edition, b_played, b_won, r_played, r_won = results[0]
    
    edition_str = f"#{edition}/{total_copies}"
    win_rate = f"{(b_won / b_played * 100):.1f}%" if b_played > 0 else "0%"

    try:
        avatar_bytes = await target_user.display_avatar.read()
    except:
        avatar_bytes = None

    image_buffer = await bot.loop.run_in_executor(
        None, 
        generate_minted_card, 
        image_path, 
        avatar_bytes, 
        target_user.name, 
        edition_str
    )

    if not image_buffer:
        return await ctx.send("❌ Error generating card image.")

    file = discord.File(fp=image_buffer, filename=f"minted_{card_id_int}.png")
    
    embed = discord.Embed(title=f"🔍 Card Inspection: {name}", color=discord.Color.gold())
    embed.set_author(name=f"Property of {target_user.name}", icon_url=target_user.display_avatar.url)
    
    embed.add_field(name="Mint Details", value=f"🆔 **ID:** {card_id_int}\n#️⃣ **Edition:** {edition_str}", inline=True)
    embed.add_field(name="Card Info", value=f"💎 {rarity}\n🏆 {type_}", inline=True)
    embed.add_field(name="Base Stats", value=f"⭐ **{overall}** | ⚔️ {atk} | 🛡️ {def_} | ⚡ {spd}", inline=False)
    
    stats_text = (
        f"⚔️ **Battles:** {b_won}/{b_played} ({win_rate})\n"
        f"🔄 **Rounds:** {r_won}/{r_played}"
    )
    embed.add_field(name="Match Record (This Copy)", value=stats_text, inline=False)

    await ctx.send(file=file, embed=embed)

#---------------------------------------------------------DROPS-------------------------------------------------------------------------------------


class DailyView(discord.ui.View):
    def __init__(self, timeout=120):
        super().__init__(timeout=timeout)
        self.collected = False



@bot.hybrid_command(name='daily', description="Claim your daily reward card")
async def daily(ctx):
    logger.info(f"User {ctx.author.name} (ID: {ctx.author.id}) invoked the daily command.")
    ensure_player_exists(ctx.author.id, ctx.author.name)
    
    # --- CALENDAR DAY STREAK SYSTEM ---
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    
    # Get current streak info
    cursor.execute('SELECT daily_streak, last_daily_claim FROM players WHERE user_id = ?', (ctx.author.id,))
    result = cursor.fetchone()
    current_streak = result[0] if result[0] else 0
    last_claim = result[1]
    
    today = datetime.now().date()
    
    # Check if already claimed today
    if last_claim:
        last_claim_date = datetime.fromisoformat(last_claim).date()
        
        if last_claim_date == today:
            # Already claimed today - show cooldown message
            tomorrow = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            time_until = tomorrow - datetime.now()
            hours = int(time_until.total_seconds() // 3600)
            minutes = int((time_until.total_seconds() % 3600) // 60)
            
            embed = discord.Embed(
                title="⏳ Daily Cooldown",
                description=f"You've already claimed today!\n\n**Come back in:** {hours}h {minutes}m",
                color=discord.Color.orange()
            )
            embed.add_field(name="🔥 Current Streak", value=f"**{current_streak}** days", inline=True)
            
            if current_streak < 7:
                embed.add_field(name="📍 Next Milestone", value=f"{7 - current_streak} days → Free Pack!", inline=True)
            elif current_streak < 14:
                embed.add_field(name="📍 Next Milestone", value=f"{14 - current_streak} days → Rare Pack!", inline=True)
            else:
                embed.add_field(name="🏆 Status", value="Max tier reached!", inline=True)
            
            await ctx.send(embed=embed)
            conn.close()
            return
        
        # Calculate days since last claim
        days_since_claim = (today - last_claim_date).days
        
        if days_since_claim == 1:
            # Claimed yesterday - streak continues!
            current_streak += 1
        elif days_since_claim > 1:
            # Missed a day - streak resets
            current_streak = 1
    else:
        # First ever claim
        current_streak = 1
    
    # Update streak in database
    cursor.execute('''
        UPDATE players 
        SET daily_streak = ?, last_daily_claim = ? 
        WHERE user_id = ?
    ''', (current_streak, datetime.now().isoformat(), ctx.author.id))
    
    # --- CALCULATE REWARDS ---
    # Base coins based on streak tier
    if current_streak >= 14:
        bonus_coins = 300
        tier_name = "🔥 Legendary"
    elif current_streak >= 7:
        bonus_coins = 200
        tier_name = "💎 Diamond"
    elif current_streak >= 4:
        bonus_coins = 150
        tier_name = "🥈 Silver"
    else:
        bonus_coins = 100
        tier_name = "🥉 Bronze"
    
    # Add coins to player
    cursor.execute('UPDATE players SET coins = coins + ? WHERE user_id = ?', (bonus_coins, ctx.author.id))
    
    # Check for milestone bonuses
    milestone_text = ""
    if current_streak == 7:
        # Award a free Rare Player Pack at 7-day milestone
        cursor.execute('SELECT * FROM packs WHERE user_id = ?', (ctx.author.id,))
        if cursor.fetchone():
            cursor.execute('UPDATE packs SET rare_player_pack = rare_player_pack + 1 WHERE user_id = ?', (ctx.author.id,))
        else:
            cursor.execute('INSERT INTO packs (user_id, rare_player_pack, icon_pack, hero_pack, tester_pack) VALUES (?, 1, 0, 0, 0)', (ctx.author.id,))
        milestone_text = "\n\n🎁 **MILESTONE BONUS!** You got a FREE **Rare Player Pack**!"
    elif current_streak == 14:
        # Award a Rare Player Pack at 14-day milestone (every 7 days = rare pack)
        cursor.execute('SELECT * FROM packs WHERE user_id = ?', (ctx.author.id,))
        if cursor.fetchone():
            cursor.execute('UPDATE packs SET rare_player_pack = rare_player_pack + 1 WHERE user_id = ?', (ctx.author.id,))
        else:
            cursor.execute('INSERT INTO packs (user_id, rare_player_pack, icon_pack, hero_pack, tester_pack) VALUES (?, 1, 0, 0, 0)', (ctx.author.id,))
        milestone_text = "\n\n🎁 **2-WEEK MILESTONE!** You got a FREE **Rare Player Pack**!"
    
    conn.commit()
    conn.close()
    
    # --- GENERATE CARDS ---
    cards = [weighted_choice(cards_with_weights) for _ in range(2)]
    for card in cards:
        card.copies += 1

    if increment_cards_dropped(ctx.author.id):
        logger.info(f"User {ctx.author.name}'s cards_dropped incremented successfully.")
    else:
        logger.error(f"Failed to increment cards_dropped for user {ctx.author.name}.")

    # --- BUILD EMBED ---
    streak_display = f"🔥 **{current_streak} Day Streak!** ({tier_name})"
    coins_display = f"💰 **+{bonus_coins} coins** earned!"
    
    content = f'{ctx.author.mention}, here is your daily reward! Choose a card below:'

    view = DailyView(timeout=120)
    for card in cards:
        view.add_item(CollectCardButton(card, ctx.author.id))

    embed = discord.Embed(
        title="📅 Daily Reward", 
        description=f"{streak_display}\n{coins_display}{milestone_text}\n\n**Choose one card below:**", 
        color=discord.Color.gold()
    )
    
    for i, card in enumerate(cards, 1):
        embed.add_field(name=f"Card {i} - {card.name}", value=(
            f"**ID:** {card.card_id}\n"
            f"**Rarity:** {card.card_rarity}\n"
            f"**Type:** {card.card_type}\n"
            f"**Overall:** {card.overall}\n"
            f"**Total Copies:** {card.copies}\n"
        ), inline=True)
    
    # Show next milestone
    if current_streak < 7:
        days_to_milestone = 7 - current_streak
        embed.set_footer(text=f"📍 {days_to_milestone} days until FREE Rare Player Pack!")
    elif current_streak < 14:
        days_to_milestone = 14 - current_streak
        embed.set_footer(text=f"📍 {days_to_milestone} days until next FREE Rare Player Pack!")
    else:
        embed.set_footer(text=f"🏆 You've reached max streak tier! Keep it going!")
    
    files = [discord.File(card.image_path) for card in cards]
    
    try:
        msg = await ctx.send(content=content, embed=embed, view=view, files=files)
        
        await view.wait()
        
        if not view.collected:
            expired_embed = discord.Embed(
                title="❌ Daily Reward Expired",
                description="You didn't pick a card in time! The options have vanished.\n\n*(Your streak and coins are still saved!)*",
                color=discord.Color.red()
            )
            await msg.edit(content=None, embed=expired_embed, view=None)
            logger.info(f"Daily reward for {ctx.author.name} expired.")
            
    except Exception as e:
        logger.error(f"Failed to send daily reward message to {ctx.author.name}: {e}")









# --- ADMIN: Streak Testing Command ---



@bot.hybrid_command(name='drop', description="Drop a random card in the chat")
@commands.cooldown(1, 1800, commands.BucketType.user)
async def drop_card(ctx):
    logger.info(f"User {ctx.author.name} (ID: {ctx.author.id}) invoked the drop command.")
    ensure_player_exists(ctx.author.id, ctx.author.name)
    
    # 1. Logic
    card = weighted_choice(cards_with_weights)
    card.copies += 1
    add_card(card)
    increment_cards_dropped(ctx.author.id)

    # 2. Calculate Timestamps
    current_time = int(time.time())
    unlock_time = current_time + 10
    
    description_text = (
        f"🔒 **Owner Priority:** Ends <t:{unlock_time}:R>\n"
        f"Anyone can claim after the timer ends!"
    )

    content = f'{ctx.author.mention} dropped a card!'
    
    embed = discord.Embed(title="🎁 Card Drop", description=description_text, color=discord.Color.blue())
    
    embed.add_field(name="Name", value=card.name, inline=True)
    embed.add_field(name="Rarity", value=card.card_rarity, inline=True)
    embed.add_field(name="Type", value=card.card_type, inline=True)
    embed.add_field(name="ID", value=card.card_id, inline=True)
    embed.add_field(name="Total Copies", value=card.copies, inline=True)

    embed.set_image(url=f"attachment://{card.image_path.split('/')[-1]}")

    # Use DropView (which has the .collected flag we added earlier)
    view = DropView(timeout=120)
    view.add_item(TimedCollectButton(card, ctx.author.id))

    try:
        msg = await ctx.send(content=content, embed=embed, view=view, file=discord.File(card.image_path))
        
        # --- PHASE 1: Priority Timer (10 Seconds) ---
        await asyncio.sleep(10)
        
        # If it was collected during these 10 seconds, stop here.
        if view.is_finished():
            return

        # Update text to show it's free for everyone
        embed.description = "🔓 **Owner Priority Ended**\nAnyone can claim now!"
        embed.color = discord.Color.green()
        await msg.edit(embed=embed)

        # --- PHASE 2: Expiration Timer (Remaining Time) ---
        # Now we wait for the view to finish naturally (timeout or click)
        await view.wait()
        
        # Check if it timed out (was NOT collected)
        if not hasattr(view, 'collected') or not view.collected:
            embed.title = "❌ Drop Expired"
            embed.description = "No one collected this card in time."
            embed.color = discord.Color.red()
            # Remove the button
            await msg.edit(embed=embed, view=None)

    except Exception as e:
        logger.error(f"Failed to send card drop message: {e}")

@drop_card.error
async def drop_card_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        retry_after = int(error.retry_after)
        minutes, seconds = divmod(retry_after, 60)
        await ctx.send(f"This command is on cooldown. Please wait {minutes} minutes and {seconds} seconds to use it again.")
        logger.info(f"User {ctx.author.name} tried to drop a card but is on cooldown: {minutes} minutes and {seconds} seconds remaining.")


class DropView(discord.ui.View):
    def __init__(self, timeout=120):
        super().__init__(timeout=timeout)
        self.collected = False

class TimedCollectButton(discord.ui.Button):
    def __init__(self, card, owner_id):
        super().__init__(style=discord.ButtonStyle.green, label="Collect", custom_id="timed_collect_card")
        self.card = card
        self.owner_id = owner_id
        self.drop_time = time.time()

    async def callback(self, interaction: discord.Interaction):
        # 1. Check Time Lock
        time_elapsed = time.time() - self.drop_time
        
        # FIX: Added "self.owner_id is not None" check
        # This ensures the lock ONLY applies if there is a specific owner (Manual Drop).
        # For Auto-Drops (where owner_id is None), this check is skipped.
        if self.owner_id is not None and interaction.user.id != self.owner_id and time_elapsed < 10:
            remaining = 10 - int(time_elapsed)
            await interaction.response.send_message(f"✋ **Locked!** Priority to owner for {remaining} more seconds.", ephemeral=True)
            return

        # 2. Add to Inventory
        ensure_player_exists(interaction.user.id, interaction.user.name)
        try:
            add_card_to_inventory(interaction.user.id, self.card.card_id)
        except ValueError:
            return await interaction.response.send_message("You already have this card!", ephemeral=True)

        # 3. Success Embed
        embed = discord.Embed(
            title="✅ Card Collected!",
            description=f"**{self.card.name}** has been collected by {interaction.user.mention}!",
            color=discord.Color.gold()
        )
        embed.set_image(url=f"attachment://{self.card.image_path.split('/')[-1]}")
        
        embed.add_field(name="Stats", value=f"⭐ {self.card.overall} | ⚔️ {self.card.attack} | 🛡️ {self.card.defense} | ⚡ {self.card.speed}", inline=False)
        embed.add_field(name="Card Details", value=f"ID: {self.card.card_id} | Rarity: {self.card.card_rarity} | Total Copies: {self.card.copies + 1}", inline=False)
        embed.set_footer(text=f"Winner: {interaction.user.name}", icon_url=interaction.user.display_avatar.url)

        # --- UPDATE STATUS ---
        if hasattr(self.view, 'collected'):
            self.view.collected = True

        # 4. Remove Button
        await interaction.response.edit_message(embed=embed, view=None)
        self.view.stop()


def increment_cards_dropped(user_id):
    try:
        connection = sqlite3.connect('cards_game.db')
        cursor = connection.cursor()
        cursor.execute('UPDATE players SET cards_dropped = cards_dropped + 1 WHERE user_id = ?', (user_id,))
        connection.commit()
        cursor.close()
        connection.close()
        return True
    except sqlite3.Error as e:
        logger.error(f'SQLite error: {e}')
        return False

#---------------------------------------------------------STATS-------------------------------------------------------------------------------------

@bot.hybrid_command(name='stats', description="View player stats")
async def stats(ctx, member: discord.Member = None):
    if member is None:
        member = ctx.author

    ensure_player_exists(member.id, member.name)
    cursor.execute('SELECT * FROM players WHERE user_id = ?', (member.id,))
    player = cursor.fetchone()
    if player:
        embed = discord.Embed(title=f"**{player[1]}'s Stats**")

        # Add display title
        display_title = player[16] if player[16] else "No Title Set"
        embed.add_field(name="**Title**", value=display_title, inline=False)

        embed.add_field(name="**Battles Played**", value=player[2])
        embed.add_field(name="**Battles Won**", value=player[3])
        embed.add_field(name="**Battles Lost**", value=player[4])
        embed.add_field(name="**Rounds Played**", value=player[6])
        embed.add_field(name="**Rounds Won**", value=player[7])
        embed.add_field(name="**Rounds Lost**", value=player[8])

        cards, editions = get_player_inventory(player[0])
        total_cards = len(cards)
        common_cards = sum(1 for card in cards if card.card_rarity == 'Common')
        uncommon_cards = sum(1 for card in cards if card.card_rarity == 'Uncommon')
        rare_cards = sum(1 for card in cards if card.card_rarity == 'Rare')

        embed.add_field(name="**Total Cards**", value=total_cards)
        embed.add_field(name="**Common Cards**", value=common_cards)
        embed.add_field(name="**Uncommon Cards**", value=uncommon_cards)
        embed.add_field(name="**Rare Cards**", value=rare_cards)

        await ctx.send(embed=embed)
        logger.info(f'{ctx.author.name} viewed {member.name}\'s stats')
    else:
        await ctx.send("Player not found.")
        logger.info(f'{ctx.author.name} tried to view stats but no player found')


class TitleDropdown(discord.ui.Select):
    def __init__(self, titles, user_id):
        options = [discord.SelectOption(label=title, value=str(achievement_id)) for title, achievement_id in titles]
        super().__init__(placeholder="Choose a title...", options=options)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        achievement_id = int(self.values[0])
        cursor.execute('SELECT title FROM achievements WHERE achievement_id = ?', (achievement_id,))
        title = cursor.fetchone()[0]
        
        cursor.execute('UPDATE players SET display_title = ? WHERE user_id = ?', (title, self.user_id))
        conn.commit()

        await interaction.response.send_message(f"Your title has been set to: {title}", ephemeral=True)
        logger.info(f'{interaction.user.name} set their title to {title}')

class TitleDropdownView(discord.ui.View):
    def __init__(self, titles, user_id):
        super().__init__(timeout=60)
        self.add_item(TitleDropdown(titles, user_id))


@bot.hybrid_command(name='set_title', description="Equip a title you have unlocked")
async def set_title(ctx):
    ensure_player_exists(ctx.author.id, ctx.author.name)
    
    cursor.execute('''
    SELECT achievements.title, achievements.achievement_id
    FROM achievements
    JOIN user_achievements ON achievements.achievement_id = user_achievements.achievement_id
    WHERE user_achievements.user_id = ?
    ''', (ctx.author.id,))
    titles = cursor.fetchall()

    if titles:
        view = TitleDropdownView(titles, ctx.author.id)
        await ctx.send("Choose a title from the dropdown menu below:", view=view)
        logger.info(f'{ctx.author.name} is setting a title')
    else:
        await ctx.send("You have no titles to set.")
        logger.info(f'{ctx.author.name} tried to set a title but has no achievements')



import discord
from discord.ui import Button

class CollectCardButton(discord.ui.Button):
    def __init__(self, card, user_id):
        super().__init__(label=f"Collect {card.name}", style=discord.ButtonStyle.green)
        self.card = card
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("You cannot collect this card.", ephemeral=True)
            return

        try:
            add_card_to_inventory(self.user_id, self.card.card_id)
            await interaction.response.send_message(f'{interaction.user.name} collected {self.card.name}!', ephemeral=True)

            # Update embed to success state
            embed = interaction.message.embeds[0]
            embed.title = "✅ Daily Reward Collected!"
            embed.description = f"{interaction.user.mention} has collected **{self.card.name}**!"
            embed.color = discord.Color.green()
            embed.set_image(url=f"attachment://{self.card.image_path.split('/')[-1]}")
            
            embed.clear_fields()
            # Added "Total Copies" here as well
            embed.add_field(
                name="Card Details", 
                value=f"ID: {self.card.card_id} | Rarity: {self.card.card_rarity} | Overall: {self.card.overall} | Total Copies: {self.card.copies}", 
                inline=False
            )

            if hasattr(self.view, 'collected'):
                self.view.collected = True
            
            self.view.stop()
            await interaction.message.edit(content=None, embed=embed, view=None)
            
            logger.info(f"{interaction.user.name} collected card {self.card.name} (ID: {self.card.card_id})")
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)


def get_user_rank_and_details(user_id, criteria):
    cursor.execute(f'SELECT user_id, name, {criteria}, ROW_NUMBER() OVER (ORDER BY {criteria} DESC) as rank FROM players')
    rows = cursor.fetchall()
    for row in rows:
        if row[0] == user_id:
            return row
    return None


#---------------------------------------------------------LEADERBOARDS-------------------------------------------------------------------------------------

async def build_leaderboard_embed(guild, author_id, stat_column, stat_name, scope):
    """Helper to generate the leaderboard Embed."""
    scope = scope.title() # "Server" or "Global"
    
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()

    # 1. Fetch ALL players sorted by the stat
    cursor.execute(f'SELECT user_id, name, {stat_column} FROM players ORDER BY {stat_column} DESC')
    all_rows = cursor.fetchall()
    conn.close()

    leaderboard_data = []
    user_rank_info = None
    rank_counter = 1

    # 2. Filter Logic
    if scope == 'Server':
        if not guild:
            return discord.Embed(title="Error", description="Server leaderboard cannot be used in DMs.", color=discord.Color.red())

        # Optimization: Get set of member IDs for O(1) lookup
        guild_member_ids = {member.id for member in guild.members}

        for row in all_rows:
            u_id, u_name, u_stat = row

            if u_id in guild_member_ids:
                if len(leaderboard_data) < 10:
                    leaderboard_data.append((rank_counter, u_name, u_stat))

                if u_id == author_id:
                    user_rank_info = (rank_counter, u_name, u_stat)

                rank_counter += 1

            if len(leaderboard_data) == 10 and user_rank_info:
                break

    elif scope == 'Friends':
        # Build the friend set (include self so caller sees their own rank)
        conn2 = sqlite3.connect('cards_game.db')
        c2 = conn2.cursor()
        c2.execute('SELECT friend_id FROM friendships WHERE user_id = ?', (author_id,))
        friend_ids = {r[0] for r in c2.fetchall()}
        conn2.close()
        friend_ids.add(author_id)

        for row in all_rows:
            u_id, u_name, u_stat = row

            if u_id in friend_ids:
                if len(leaderboard_data) < 10:
                    leaderboard_data.append((rank_counter, u_name, u_stat))

                if u_id == author_id:
                    user_rank_info = (rank_counter, u_name, u_stat)

                rank_counter += 1

            if len(leaderboard_data) == 10 and user_rank_info:
                break

    else: # Global
        for row in all_rows:
            u_id, u_name, u_stat = row

            if len(leaderboard_data) < 10:
                leaderboard_data.append((rank_counter, u_name, u_stat))

            if u_id == author_id:
                user_rank_info = (rank_counter, u_name, u_stat)

            rank_counter += 1

            if len(leaderboard_data) == 10 and user_rank_info:
                break

    # 3. Build Embed
    icon = {"Global": "🌍", "Server": "🏰", "Friends": "👥"}.get(scope, "🏆")
    embed = discord.Embed(title=f"{icon} {scope} Leaderboard - {stat_name}", color=discord.Color.gold())
    
    if not leaderboard_data:
        embed.description = "No ranked players found."
        return embed

    description = ""
    for rank, name, value in leaderboard_data:
        formatted_value = f"{value:,}"
        medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"**{rank}.**"
        description += f"{medal} **{name}** • {formatted_value}\n"

    embed.description = description

    if user_rank_info:
        rank, name, value = user_rank_info
        embed.set_footer(text=f"Your Rank: #{rank} • {value:,}")
    else:
        embed.set_footer(text="You are unranked or not in the top list.")

    return embed


class LeaderboardSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Battles Won", value="battles_won", emoji="🏆"),
            discord.SelectOption(label="Battles Played", value="battles_played", emoji="⚔️"),
            discord.SelectOption(label="Rounds Won", value="rounds_won", emoji="🥊"),
            discord.SelectOption(label="Rounds Played", value="rounds_played", emoji="🔄"),
            discord.SelectOption(label="Richest Players", value="coins", emoji="💰"),
            discord.SelectOption(label="Cards Dropped", value="cards_dropped", emoji="🎁"),
        ]
        super().__init__(placeholder="Select Leaderboard Type...", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        # Update view state
        view.current_stat_key = self.values[0]
        
        # Map values to display names
        names = {
            "battles_won": "Battles Won",
            "battles_played": "Battles Played",
            "rounds_won": "Rounds Won",
            "rounds_played": "Rounds Played",
            "coins": "Coins",
            "cards_dropped": "Cards Dropped"
        }
        view.current_stat_name = names.get(view.current_stat_key, "Stat")

        # Generate new embed
        embed = await build_leaderboard_embed(
            interaction.guild, 
            interaction.user.id, 
            view.current_stat_key, 
            view.current_stat_name, 
            view.scope
        )
        
        await interaction.response.edit_message(embed=embed, view=view)


class ScopeButton(discord.ui.Button):
    # current_scope -> (next_scope, button_label, button_emoji)
    _CYCLE = {
        "Server":  ("Global",  "Show Global",  "🌍"),
        "Global":  ("Friends", "Show Friends", "👥"),
        "Friends": ("Server",  "Show Server",  "🏰"),
    }

    def __init__(self, current_scope):
        _, label, emoji = self._CYCLE[current_scope]
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        next_scope, _, _ = self._CYCLE[view.scope]
        view.scope = next_scope
        _, new_label, new_emoji = self._CYCLE[view.scope]
        self.label = new_label
        self.emoji = new_emoji

        embed = await build_leaderboard_embed(
            interaction.guild,
            interaction.user.id,
            view.current_stat_key,
            view.current_stat_name,
            view.scope
        )

        await interaction.response.edit_message(embed=embed, view=view)


class LeaderboardView(discord.ui.View):
    def __init__(self, scope, initial_stat_key="battles_won", initial_stat_name="Battles Won"):
        super().__init__(timeout=120)
        self.scope = scope
        self.current_stat_key = initial_stat_key
        self.current_stat_name = initial_stat_name
        
        # Add components
        self.add_item(LeaderboardSelect())
        self.add_item(ScopeButton(scope))


# --- COMMANDS ---

@bot.hybrid_command(name='leaderboard', aliases=['lb'], description="View game rankings")
async def leaderboard(ctx):
    # Default to Server Scope, Battles Won
    embed = await build_leaderboard_embed(ctx.guild, ctx.author.id, 'battles_won', 'Battles Won', 'Server')
    view = LeaderboardView('Server')
    await ctx.send(embed=embed, view=view)

@bot.hybrid_command(name='richest', description="View the coins leaderboard")
async def richest(ctx):
    # Default to Server Scope, Coins
    embed = await build_leaderboard_embed(ctx.guild, ctx.author.id, 'coins', 'Coins', 'Server')
    
    # Initialize view with Coins set as current
    view = LeaderboardView('Server', initial_stat_key='coins', initial_stat_name='Coins')
    
    # Optional: Pre-select 'coins' in dropdown if you want, 
    # but the placeholder logic works fine.
    
    await ctx.send(embed=embed, view=view)


#---------------------------------------------------------INVENTORY-------------------------------------------------------------------------------------
import logging
logger = logging.getLogger(__name__)

@bot.hybrid_command(name='inventory', description="View your card collection")
async def view_inventory(ctx, user: discord.User = None, search: str = None):
    target_user = user or ctx.author
    ensure_player_exists(target_user.id, target_user.name)
    
    inventory, editions = get_player_inventory(target_user.id)
    
    if not inventory:
        return await ctx.send(f"{target_user.name} has no cards in their inventory.")

    view = InventoryView(inventory, target_user, editions, ctx)

    # If they provided a search term immediately in the command
    if search:
        # Filter immediately
        search = search.lower()
        view.data = [item for item in view.full_data if search in item[0].name.lower()]
        view.filter_label = f"Name: {search}"
        
        if not view.data:
            return await ctx.send(f"No cards found matching '{search}' in {target_user.name}'s inventory.")

    embed = view.update_view()
    view.update_buttons() # Refresh to show/hide Reset button
    view.message = await ctx.send(embed=embed, view=view)
    logger.info(f'{ctx.author.name} viewed inventory')

# --- SORT DROPDOWN ---
class SortSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Sort by Overall", value="overall", emoji="⭐"),
            discord.SelectOption(label="Sort by Pace", value="speed", emoji="⚡"),
            discord.SelectOption(label="Sort by Attack", value="attack", emoji="⚔️"),
            discord.SelectOption(label="Sort by Defense", value="defense", emoji="🛡️"),
            discord.SelectOption(label="Sort by Rarity", value="rarity", emoji="💎"),
            # --- NEW OPTION ---
            discord.SelectOption(label="Sort by Popularity", value="popularity", emoji="❤️")
        ]
        super().__init__(placeholder="Sort items...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        sort_key = self.values[0]

        labels = {
            "overall": "Overall", "speed": "Pace", "attack": "Attack",
            "defense": "Defense", "rarity": "Rarity", "popularity": "Popularity"
        }
        view.sort_label = labels.get(sort_key, "Overall")

        # Sorting Logic
        if sort_key == "overall":
            view.data.sort(key=lambda x: x[0].overall, reverse=True)
        elif sort_key == "speed":
            view.data.sort(key=lambda x: x[0].speed, reverse=True)
        elif sort_key == "attack":
            view.data.sort(key=lambda x: x[0].attack, reverse=True)
        elif sort_key == "defense":
            view.data.sort(key=lambda x: x[0].defense, reverse=True)
        elif sort_key == "rarity":
            view.data.sort(key=lambda x: x[0].copies, reverse=False)
        elif sort_key == "popularity":
            # Sort by wishlist_count (Highest first)
            view.data.sort(key=lambda x: x[0].wishlist_count, reverse=True)

        view.current_page = 0
        embed = view.update_view()
        view.update_buttons()
        
        await interaction.response.edit_message(embed=embed, view=view)

class FilterModal(discord.ui.Modal, title="Filter Inventory"):
    name_input = discord.ui.TextInput(label="Player Name", placeholder="e.g. Messi", required=False)
    min_rating_input = discord.ui.TextInput(label="Min Rating", placeholder="e.g. 85", required=False, max_length=2)
    rarity_input = discord.ui.TextInput(label="Rarity", placeholder="e.g. Rare, Common", required=False)
    type_input = discord.ui.TextInput(label="Card Type", placeholder="e.g. Icon, Hero", required=False)

    def __init__(self, view):
        super().__init__()
        self.inv_view = view

    async def on_submit(self, interaction: discord.Interaction):
        # Save inputs
        self.inv_view.filter_name = self.name_input.value.lower() if self.name_input.value else None
        self.inv_view.filter_rating = int(self.min_rating_input.value) if self.min_rating_input.value.isdigit() else None
        self.inv_view.filter_rarity = self.rarity_input.value.lower() if self.rarity_input.value else None
        self.inv_view.filter_type = self.type_input.value.lower() if self.type_input.value else None
        
        # Apply filters
        self.inv_view.apply_filters()
        
        # --- FIX: Force Reset Sort to Overall ---
        self.inv_view.data.sort(key=lambda x: x[0].overall, reverse=True)
        self.inv_view.sort_label = "Overall"
        
        # Reset Dropdown Visuals to show "Overall" is selected
        dropdown = self.inv_view.children[0]
        for opt in dropdown.options:
            opt.default = (opt.value == "overall")
        
        embed = self.inv_view.update_view()
        self.inv_view.update_buttons()
        await interaction.response.edit_message(embed=embed, view=self.inv_view)




# --- 2. ALL BUTTONS (Row 1) ---

class PreviousButton(discord.ui.Button):
    def __init__(self):
        # ROW 1, Text Label
        super().__init__(label='Previous', style=discord.ButtonStyle.primary, custom_id='previous', row=1)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if view.current_page > 0:
            view.current_page -= 1
            embed = view.update_view()
            view.update_buttons()
            await interaction.response.edit_message(embed=embed, view=view)

class FilterButton(discord.ui.Button):
    def __init__(self):
        # ROW 1
        super().__init__(label="Filter", style=discord.ButtonStyle.secondary, emoji="🔍", row=1)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(FilterModal(self.view))

class ResetFilterButton(discord.ui.Button):
    def __init__(self):
        # ROW 1
        super().__init__(label="Reset", style=discord.ButtonStyle.danger, emoji="✖️", row=1)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        view.data = view.full_data[:]
        
        # Reset filters
        view.filter_name = None
        view.filter_rating = None
        view.filter_rarity = None
        view.filter_type = None
        view.current_page = 0
        
        # Reset Sort
        view.children[0].options[0].default = True 
        view.data.sort(key=lambda x: x[0].overall, reverse=True)
        view.sort_label = "Overall"

        embed = view.update_view()
        view.update_buttons()
        await interaction.response.edit_message(embed=embed, view=view)

class NextButton(discord.ui.Button):
    def __init__(self):
        # ROW 1, Text Label
        super().__init__(label='Next', style=discord.ButtonStyle.primary, custom_id='next', row=1)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if view.current_page < view.total_pages - 1:
            view.current_page += 1
            embed = view.update_view()
            view.update_buttons()
            await interaction.response.edit_message(embed=embed, view=view)

# --- 3. MAIN VIEW ---

class InventoryView(discord.ui.View):
    def __init__(self, inventory, target_user, editions, ctx):
        super().__init__(timeout=120)
        self.full_data = list(zip(inventory, editions))
        self.data = self.full_data[:] 
        self.target_user = target_user
        self.ctx = ctx
        self.current_page = 0
        
        self.sort_label = "Overall"
        self.filter_name = None
        self.filter_rating = None
        self.filter_rarity = None
        self.filter_type = None
        
        self.total_pages = max(1, (len(self.data) - 1) // 10 + 1)
        self.data.sort(key=lambda x: x[0].overall, reverse=True)
        
        self.add_item(SortSelect())
        self.update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("⛔ You cannot control this menu. Run `/inventory` yourself!", ephemeral=True)
            return False
        return True

    def apply_filters(self):
        filtered = self.full_data[:]
        if self.filter_name:
            filtered = [x for x in filtered if self.filter_name in x[0].name.lower()]
        if self.filter_rating:
            filtered = [x for x in filtered if x[0].overall >= self.filter_rating]
        if self.filter_rarity:
            filtered = [x for x in filtered if self.filter_rarity in x[0].card_rarity.lower()]
        if self.filter_type:
            filtered = [x for x in filtered if self.filter_type in x[0].card_type.lower()]

        self.data = filtered
        self.current_page = 0 
        self.total_pages = max(1, (len(self.data) - 1) // 10 + 1)

    def update_view(self):
        start = self.current_page * 10
        end = start + 10
        page_items = self.data[start:end]

        card_descriptions = []
        for card, edition in page_items:
            # --- FORMAT CHANGE: Wishlists placed BEFORE Edition ---
            line = (
                f"**{card.name} (ID: {card.card_id})** - "
                f"❤️ {card.wishlist_count}, "
                f"Edition: {edition}, "
                f"Overall: {card.overall}, "
                f"Attack: {card.attack}, "
                f"Defense: {card.defense}, "
                f"Speed: {card.speed}, "
                f"Type: {card.card_type}"
            )
            card_descriptions.append(line)

        description = '\n'.join(card_descriptions) if card_descriptions else "No cards found matching your filters."

        status = [f"Sort: {self.sort_label}"]
        if self.filter_name: status.append(f"Name: {self.filter_name}")
        if self.filter_rating: status.append(f"OVR>={self.filter_rating}")
        if self.filter_rarity: status.append(f"Rarity: {self.filter_rarity}")
        if self.filter_type: status.append(f"Type: {self.filter_type}")
        
        embed = discord.Embed(
            title=f"{self.target_user.name}'s Inventory ({' | '.join(status)})", 
            description=description, 
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} | Showing {len(self.data)} of {len(self.full_data)} Cards")
        return embed

    def update_buttons(self):
        while len(self.children) > 1:
            self.remove_item(self.children[1])

        self.add_item(FilterButton())
        
        is_filtered = (len(self.data) != len(self.full_data))
        if is_filtered:
            self.add_item(ResetFilterButton())

        if self.current_page > 0:
            self.add_item(PreviousButton())
        
        if self.current_page < self.total_pages - 1:
            self.add_item(NextButton())



# ---------------------------------------------------------TRADES REFACTORED-------------------------------------------------------------------------------------

class TradeView(discord.ui.View):
    def __init__(self, ctx, your_card, other_user, their_card):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.your_card = your_card
        self.other_user = other_user
        self.their_card = their_card
        self.message = None

    async def on_timeout(self):
        if self.message:
            for child in self.children:
                child.disabled = True
            await self.message.edit(content=f"⏰ Trade offer to {self.other_user.mention} timed out.", view=self)

    @discord.ui.button(label="Accept Trade", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.other_user.id:
            return await interaction.response.send_message("This trade offer is not for you!", ephemeral=True)

        conn = sqlite3.connect('cards_game.db')
        cursor = conn.cursor()
        
        # Verify ownership one last time
        cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (self.ctx.author.id, self.your_card.card_id))
        sender_has = cursor.fetchone()
        cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (self.other_user.id, self.their_card.card_id))
        receiver_has = cursor.fetchone()

        if not sender_has or not receiver_has:
            conn.close()
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.red()
            embed.title = "❌ Trade Failed"
            embed.description = "One of the players no longer owns the required card."
            return await interaction.response.edit_message(embed=embed, view=None)

        try:
            # --- FIX: Update user_id AND increment trade_count ---
            
            # 1. Move Author's card to Other User (one copy via rowid)
            cursor.execute('SELECT rowid FROM inventories WHERE user_id = ? AND card_id = ? LIMIT 1', (self.ctx.author.id, self.your_card.card_id))
            your_rowid = cursor.fetchone()[0]
            cursor.execute('UPDATE inventories SET user_id = ?, trade_count = trade_count + 1 WHERE rowid = ?', (self.other_user.id, your_rowid))

            # 2. Move Other User's card to Author (one copy via rowid)
            cursor.execute('SELECT rowid FROM inventories WHERE user_id = ? AND card_id = ? LIMIT 1', (self.other_user.id, self.their_card.card_id))
            their_rowid = cursor.fetchone()[0]
            cursor.execute('UPDATE inventories SET user_id = ?, trade_count = trade_count + 1 WHERE rowid = ?', (self.ctx.author.id, their_rowid))
            
            conn.commit()
        except Exception as e:
            conn.close()
            logger.error(f"Trade Error: {e}")
            return await interaction.response.send_message("Database error occurred.", ephemeral=True)
        
        conn.close()

        embed = interaction.message.embeds[0]
        embed.title = "✅ Trade Successful"
        embed.description = f"**{self.ctx.author.name}** and **{self.other_user.name}** have swapped cards!"
        embed.color = discord.Color.green()
        
        # Update visual fields
        embed.set_field_at(0, name=f"Now owned by {self.other_user.name}", value=f"**{self.your_card.name}**", inline=True)
        embed.set_field_at(1, name=f"Now owned by {self.ctx.author.name}", value=f"**{self.their_card.name}**", inline=True)

        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.other_user.id:
            return await interaction.response.send_message("This trade offer is not for you!", ephemeral=True)

        embed = interaction.message.embeds[0]
        embed.title = "❌ Trade Declined"
        embed.description = f"{self.other_user.name} declined the trade offer."
        embed.color = discord.Color.red()

        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("Only the person who started the trade can cancel it.", ephemeral=True)

        embed = interaction.message.embeds[0]
        embed.title = "🚫 Trade Cancelled"
        embed.description = f"{self.ctx.author.name} cancelled the trade request."
        embed.color = discord.Color.dark_grey()

        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()


@bot.hybrid_command(name='trade', description="Trade cards with another player")
async def trade(ctx, your_card_id: int, other_user: discord.User, their_card_id: int):
    # 1. Self Trade Check
    if ctx.author.id == other_user.id:
        return await ctx.send("You cannot trade with yourself.")

    ensure_player_exists(ctx.author.id, ctx.author.name)
    ensure_player_exists(other_user.id, other_user.name)

    # 2. Fetch Card Objects
    your_card = get_card_by_id(your_card_id)
    their_card = get_card_by_id(their_card_id)

    if not your_card:
        return await ctx.send(f"Card ID **{your_card_id}** not found.")
    if not their_card:
        return await ctx.send(f"Card ID **{their_card_id}** not found.")

    # 3. Ownership Checks
    if not check_card_ownership(ctx.author.id, your_card_id):
        return await ctx.send(f"You do not own the card **{your_card.name}** (ID: {your_card_id}).")
    
    if not check_card_ownership(other_user.id, their_card_id):
        return await ctx.send(f"{other_user.name} does not own the card **{their_card.name}** (ID: {their_card_id}).")

    # 4. Build UI
    view = TradeView(ctx, your_card, other_user, their_card)
    
    embed = discord.Embed(title="🤝 Trade Offer", description=f"{ctx.author.mention} wants to trade with {other_user.mention}!", color=discord.Color.gold())
    
    embed.add_field(name=f"{ctx.author.name} offers:", value=f"**{your_card.name}**\n⭐ {your_card.overall} | 🆔 {your_card.card_id}", inline=True)
    embed.add_field(name=f"{other_user.name} offers:", value=f"**{their_card.name}**\n⭐ {their_card.overall} | 🆔 {their_card.card_id}", inline=True)
    
    embed.set_thumbnail(url=f"attachment://{your_card.image_path.split('/')[-1]}")
    embed.set_image(url=f"attachment://{their_card.image_path.split('/')[-1]}")
    embed.set_footer(text="Both players must verify the cards before accepting.")

    # --- FIX: Added 'content' to ping the user ---
    msg = await ctx.send(
        content=f"Hey {other_user.mention}, you have a trade offer!", 
        embed=embed, 
        view=view, 
        files=[discord.File(your_card.image_path), discord.File(their_card.image_path)]
    )
    view.message = msg


def add_deck(user_id, deck_name, card_ids):
    # Check if deck already exists
    cursor.execute('SELECT * FROM decks WHERE user_id = ? AND deck_name = ?', (user_id, deck_name))
    if cursor.fetchone() is not None:
        raise ValueError("Deck with this name already exists")

    # Check for duplicate player IDs in the deck
    player_ids = set()
    for card_id in card_ids:
        cursor.execute('SELECT player_id FROM cards WHERE card_id = ?', (card_id,))
        player_id = cursor.fetchone()[0]
        if player_id in player_ids:
            raise ValueError("Deck cannot contain more than one card with the same player ID")
        player_ids.add(player_id)

    # Convert card IDs to comma-separated string
    cards_str = ','.join(map(str, card_ids))

    # Insert the deck into the database
    cursor.execute('INSERT INTO decks (user_id, deck_name, cards) VALUES (?, ?, ?)', (user_id, deck_name, cards_str))
    conn.commit()


def get_deck(user_id, deck_name):
    cursor.execute('SELECT cards FROM decks WHERE user_id = ? AND deck_name = ?', (user_id, deck_name))
    result = cursor.fetchone()
    if result is None:
        return None
    return list(map(int, result[0].split(',')))




from discord.ui import Select, View, Button





# ---------------------------------------------------------ADVANCED EXCHANGE SYSTEM-------------------------------------------------------------------------------------

class ExchangeSession:
    """Holds state for the advanced /exchange command"""
    def __init__(self, p1, p2):
        self.p1 = p1
        self.p2 = p2
        # Offers: {'cards': [card_objects], 'coins': 0}
        self.p1_offer = {'cards': [], 'coins': 0}
        self.p2_offer = {'cards': [], 'coins': 0}
        
        self.p1_locked = False
        self.p2_locked = False
        self.p1_confirmed = False
        self.p2_confirmed = False

class ExchangeAddCoinsModal(discord.ui.Modal, title="Add Coins to Exchange"):
    amount = discord.ui.TextInput(label="Amount", placeholder="e.g. 500", min_length=1, max_length=10)

    def __init__(self, view, user_side):
        super().__init__()
        self.exchange_view = view
        self.user_side = user_side # 'p1' or 'p2'

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.amount.value)
            if amount <= 0: raise ValueError
        except ValueError:
            return await interaction.response.send_message("Please enter a valid positive number.", ephemeral=True)

        user_id = interaction.user.id
        
        # Verify Balance
        conn = sqlite3.connect('cards_game.db')
        cursor = conn.cursor()
        cursor.execute('SELECT coins FROM players WHERE user_id = ?', (user_id,))
        balance = cursor.fetchone()[0]
        conn.close()

        if balance < amount:
            return await interaction.response.send_message(f"You only have {balance} coins.", ephemeral=True)

        # Update Offer
        if self.user_side == 'p1':
            self.exchange_view.session.p1_offer['coins'] = amount
        else:
            self.exchange_view.session.p2_offer['coins'] = amount
            
        # Unlock logic on change
        self.exchange_view.session.p1_locked = False
        self.exchange_view.session.p2_locked = False
        self.exchange_view.session.p1_confirmed = False
        self.exchange_view.session.p2_confirmed = False
        
        await self.exchange_view.update_display(interaction)

class ExchangeAddCardModal(discord.ui.Modal, title="Add Card to Exchange"):
    card_id_input = discord.ui.TextInput(label="Card ID", placeholder="Enter Card ID", min_length=1, max_length=10)

    def __init__(self, view, user_side):
        super().__init__()
        self.exchange_view = view
        self.user_side = user_side

    async def on_submit(self, interaction: discord.Interaction):
        identifier = self.card_id_input.value
        if not identifier.isdigit():
            return await interaction.response.send_message("Invalid ID.", ephemeral=True)
        
        card_id = int(identifier)
        
        # 1. Check if YOU own it
        if not check_card_ownership(interaction.user.id, card_id):
            return await interaction.response.send_message("You do not own this card.", ephemeral=True)

        card = get_card_by_id(card_id)
        if not card:
            return await interaction.response.send_message("Card not found.", ephemeral=True)

        current_offer = self.exchange_view.session.p1_offer['cards'] if self.user_side == 'p1' else self.exchange_view.session.p2_offer['cards']
        
        if any(c.card_id == card_id for c in current_offer):
            return await interaction.response.send_message("Card already in offer.", ephemeral=True)

        current_offer.append(card)
        
        # Unlock logic on change
        self.exchange_view.session.p1_locked = False
        self.exchange_view.session.p2_locked = False
        self.exchange_view.session.p1_confirmed = False
        self.exchange_view.session.p2_confirmed = False
        
        await self.exchange_view.update_display(interaction)

# 1. The Dropdown to Pick the Card (After Searching)
class ExchangeCardSearchSelect(discord.ui.Select):
    def __init__(self, cards, view, side):
        self.exchange_view = view
        self.side = side # 'p1' or 'p2'
        
        options = []
        for card in cards[:25]: # Discord limit
            options.append(discord.SelectOption(
                label=card.name,
                description=f"ID: {card.card_id} | ⭐ {card.overall}",
                value=str(card.card_id)
            ))
            
        super().__init__(placeholder="Select the card to add...", min_values=1, max_values=1, options=options)

    # In ExchangeCardSearchSelect class
    async def callback(self, interaction: discord.Interaction):
        card_id = int(self.values[0])
        user = interaction.user
        
        # ... (Keep your ownership/duplicate checks here) ...
        # 1. Ownership Check
        if not check_card_ownership(user.id, card_id):
            return await interaction.response.send_message("❌ You no longer own this card.", ephemeral=True)

        # 2. Add to Offer
        card = get_card_by_id(card_id)
        current_offer = self.exchange_view.session.p1_offer['cards'] if self.side == 'p1' else self.exchange_view.session.p2_offer['cards']
        
        if any(c.card_id == card.card_id for c in current_offer):
            return await interaction.response.send_message("⚠️ You already added this card.", ephemeral=True)

        current_offer.append(card)
        
        # Reset locks
        self.exchange_view.session.p1_locked = False
        self.exchange_view.session.p2_locked = False
        self.exchange_view.session.p1_confirmed = False
        self.exchange_view.session.p2_confirmed = False
        
        # --- THE FIX ---
        # 1. Update the MAIN table (Public)
        # We pass None so it edits self.message directly
        await self.exchange_view.update_display(interaction=None)
        
        # 2. Update the USER (Private)
        # We use the interaction to reply to the dropdown click
        await interaction.response.send_message(f"✅ Added **{card.name}** to the exchange.", ephemeral=True)

# 2. The Search Modal
class ExchangeSearchModal(discord.ui.Modal, title="Search your Inventory"):
    query = discord.ui.TextInput(label="Card Name", placeholder="e.g. Messi")

    def __init__(self, view, side):
        super().__init__()
        self.exchange_view = view
        self.side = side

    async def on_submit(self, interaction: discord.Interaction):
        search_term = self.query.value.lower()
        user_id = interaction.user.id
        
        # Fetch user inventory
        inventory, _ = get_player_inventory(user_id)
        
        # Filter matches
        matches = [c for c in inventory if search_term in c.name.lower()]
        
        if not matches:
            return await interaction.response.send_message("❌ No cards found matching that name.", ephemeral=True)

        # Show Dropdown (Ephemeral)
        view = discord.ui.View()
        view.add_item(ExchangeCardSearchSelect(matches, self.exchange_view, self.side))
        
        await interaction.response.send_message(
            f"🔍 Found {len(matches)} cards matching '**{self.query.value}**'. Select one below:", 
            view=view, 
            ephemeral=True
        )


class ExchangeView(discord.ui.View):
    def __init__(self, ctx, p1, p2):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.session = ExchangeSession(p1, p2)
        self.message = None
        
        # --- FIX: Add buttons immediately ---
        self.add_item(ExAddCardButton())
        self.add_item(ExAddCoinsButton())
        self.add_item(ExClearButton())
        self.add_item(ExLockButton())
        self.add_item(ExCancelButton())

    async def update_display(self, interaction):
        p1_status = "🔒 Locked" if self.session.p1_locked else "✏️ Editing..."
        p2_status = "🔒 Locked" if self.session.p2_locked else "✏️ Editing..."
        
        color = discord.Color.gold()
        if self.session.p1_locked and self.session.p2_locked:
            color = discord.Color.green()

        embed = discord.Embed(title="⚖️ Exchange Table", color=color)
        
        p1_cards = "\n".join([f"• {c.name} (ID: {c.card_id})" for c in self.session.p1_offer['cards']]) or "No cards"
        p1_coins = self.session.p1_offer['coins']
        embed.add_field(name=f"{self.session.p1.name} ({p1_status})", value=f"💰 {p1_coins}\n{p1_cards}", inline=True)

        p2_cards = "\n".join([f"• {c.name} (ID: {c.card_id})" for c in self.session.p2_offer['cards']]) or "No cards"
        p2_coins = self.session.p2_offer['coins']
        embed.add_field(name=f"{self.session.p2.name} ({p2_status})", value=f"💰 {p2_coins}\n{p2_cards}", inline=True)

        if self.session.p1_locked and self.session.p2_locked:
            embed.description = "**BOTH LOCKED!** Click **Confirm** to execute."
        else:
            embed.description = "Add items using the buttons below. Lock when ready."

        self.clear_items()
        if not (self.session.p1_locked and self.session.p2_locked):
            self.add_item(ExAddCardButton())
            self.add_item(ExAddCoinsButton())
            self.add_item(ExClearButton())
            self.add_item(ExLockButton())
            self.add_item(ExCancelButton())
        else:
            self.add_item(ExConfirmButton())
            self.add_item(ExCancelButton())

        if interaction is None:
            # Called from a different context (like the search menu) -> Edit message directly
            await self.message.edit(embed=embed, view=self)
        elif interaction.response.is_done():
            await self.message.edit(embed=embed, view=self)
        else:
            # Called from a button on the menu itself -> Edit response
            await interaction.response.edit_message(embed=embed, view=self)

    async def execute_exchange(self, interaction):
        conn = sqlite3.connect('cards_game.db')
        cursor = conn.cursor()
        
        try:
            # 1. FINAL ASSET VERIFICATION
            cursor.execute('SELECT coins FROM players WHERE user_id = ?', (self.session.p1.id,))
            if cursor.fetchone()[0] < self.session.p1_offer['coins']: raise ValueError(f"{self.session.p1.name} missing coins.")
            
            cursor.execute('SELECT coins FROM players WHERE user_id = ?', (self.session.p2.id,))
            if cursor.fetchone()[0] < self.session.p2_offer['coins']: raise ValueError(f"{self.session.p2.name} missing coins.")

            # Check P1 Cards Ownership
            for card in self.session.p1_offer['cards']:
                cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (self.session.p1.id, card.card_id))
                if not cursor.fetchone(): raise ValueError(f"{self.session.p1.name} no longer owns {card.name}")

            # Check P2 Cards Ownership
            for card in self.session.p2_offer['cards']:
                cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (self.session.p2.id, card.card_id))
                if not cursor.fetchone(): raise ValueError(f"{self.session.p2.name} no longer owns {card.name}")

            # 2. EXECUTE SWAPS (Coins)
            if self.session.p1_offer['coins'] > 0:
                cursor.execute('UPDATE players SET coins = coins - ? WHERE user_id = ?', (self.session.p1_offer['coins'], self.session.p1.id))
                cursor.execute('UPDATE players SET coins = coins + ? WHERE user_id = ?', (self.session.p1_offer['coins'], self.session.p2.id))
            
            if self.session.p2_offer['coins'] > 0:
                cursor.execute('UPDATE players SET coins = coins - ? WHERE user_id = ?', (self.session.p2_offer['coins'], self.session.p2.id))
                cursor.execute('UPDATE players SET coins = coins + ? WHERE user_id = ?', (self.session.p2_offer['coins'], self.session.p1.id))

            # 3. EXECUTE SWAPS (Cards)
            for card in self.session.p1_offer['cards']:
                cursor.execute('SELECT rowid FROM inventories WHERE user_id = ? AND card_id = ? LIMIT 1', (self.session.p1.id, card.card_id))
                rowid = cursor.fetchone()[0]
                cursor.execute('UPDATE inventories SET user_id = ?, trade_count = trade_count + 1 WHERE rowid = ?', (self.session.p2.id, rowid))

            for card in self.session.p2_offer['cards']:
                cursor.execute('SELECT rowid FROM inventories WHERE user_id = ? AND card_id = ? LIMIT 1', (self.session.p2.id, card.card_id))
                rowid = cursor.fetchone()[0]
                cursor.execute('UPDATE inventories SET user_id = ?, trade_count = trade_count + 1 WHERE rowid = ?', (self.session.p1.id, rowid))

            conn.commit()
            
            # --- NEW SUMMARY EMBED ---
            embed = discord.Embed(title="✅ Exchange Complete!", color=discord.Color.green())
            
            # Format P1 Summary
            p1_items = []
            if self.session.p1_offer['coins'] > 0:
                p1_items.append(f"💰 {self.session.p1_offer['coins']:,} Coins")
            for c in self.session.p1_offer['cards']:
                p1_items.append(f"🃏 {c.name}")
            p1_val = "\n".join(p1_items) if p1_items else "*Nothing*"

            # Format P2 Summary
            p2_items = []
            if self.session.p2_offer['coins'] > 0:
                p2_items.append(f"💰 {self.session.p2_offer['coins']:,} Coins")
            for c in self.session.p2_offer['cards']:
                p2_items.append(f"🃏 {c.name}")
            p2_val = "\n".join(p2_items) if p2_items else "*Nothing*"

            embed.add_field(name=f"{self.session.p1.name} sent:", value=p1_val, inline=True)
            embed.add_field(name=f"{self.session.p2.name} sent:", value=p2_val, inline=True)
            
            await interaction.response.edit_message(embed=embed, view=None)
            self.stop()

        except Exception as e:
            conn.rollback()
            await interaction.response.send_message(f"❌ Failed: {str(e)}", ephemeral=True)
        finally:
            conn.close()

# --- EXCHANGE BUTTONS ---

class ExAddCardButton(discord.ui.Button):
    def __init__(self): 
        super().__init__(label="Add Card", style=discord.ButtonStyle.primary, emoji="🃏", row=0)
        
    async def callback(self, interaction):
        view = self.view
        side = 'p1' if interaction.user.id == view.session.p1.id else 'p2' if interaction.user.id == view.session.p2.id else None
        
        if side: 
            # Open the new Search Modal
            await interaction.response.send_modal(ExchangeSearchModal(view, side))
        else: 
            await interaction.response.send_message("Not your session.", ephemeral=True)

class ExAddCoinsButton(discord.ui.Button):
    def __init__(self): super().__init__(label="Set Coins", style=discord.ButtonStyle.primary, emoji="💰", row=0)
    async def callback(self, interaction):
        view = self.view
        side = 'p1' if interaction.user.id == view.session.p1.id else 'p2' if interaction.user.id == view.session.p2.id else None
        if side: await interaction.response.send_modal(ExchangeAddCoinsModal(view, side))
        else: await interaction.response.send_message("Not your session.", ephemeral=True)

class ExClearButton(discord.ui.Button):
    def __init__(self): super().__init__(label="Clear", style=discord.ButtonStyle.secondary, emoji="🧹", row=0)
    async def callback(self, interaction):
        view = self.view
        if interaction.user.id == view.session.p1.id:
            view.session.p1_offer = {'cards': [], 'coins': 0}
            view.session.p1_locked = False
            view.session.p2_locked = False
        elif interaction.user.id == view.session.p2.id:
            view.session.p2_offer = {'cards': [], 'coins': 0}
            view.session.p1_locked = False
            view.session.p2_locked = False
        else: return await interaction.response.send_message("Not your session.", ephemeral=True)
        await view.update_display(interaction)

class ExLockButton(discord.ui.Button):
    def __init__(self): super().__init__(label="Lock Offer", style=discord.ButtonStyle.success, emoji="🔒", row=1)
    async def callback(self, interaction):
        view = self.view
        if interaction.user.id == view.session.p1.id: view.session.p1_locked = not view.session.p1_locked
        elif interaction.user.id == view.session.p2.id: view.session.p2_locked = not view.session.p2_locked
        else: return await interaction.response.send_message("Not your session.", ephemeral=True)
        await view.update_display(interaction)

class ExConfirmButton(discord.ui.Button):
    def __init__(self): super().__init__(label="Confirm Exchange", style=discord.ButtonStyle.green, emoji="🤝", row=1)
    async def callback(self, interaction):
        view = self.view
        if interaction.user.id == view.session.p1.id: view.session.p1_confirmed = True
        elif interaction.user.id == view.session.p2.id: view.session.p2_confirmed = True
        
        if view.session.p1_confirmed and view.session.p2_confirmed: await view.execute_exchange(interaction)
        else: await interaction.response.send_message(f"{interaction.user.name} confirmed. Waiting for partner...", ephemeral=False)

class ExCancelButton(discord.ui.Button):
    def __init__(self): super().__init__(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖️", row=1)
    async def callback(self, interaction):
        if interaction.user.id in [self.view.session.p1.id, self.view.session.p2.id]:
            await interaction.response.edit_message(content="🚫 Exchange Cancelled.", embed=None, view=None)
            self.view.stop()

@bot.hybrid_command(name='exchange', description="Start a complex trading session (Cards + Coins)")
async def exchange(ctx, user: discord.User):
    if user.id == ctx.author.id: return await ctx.send("You cannot exchange with yourself.")
    if user.bot: return await ctx.send("Bots cannot trade.")

    ensure_player_exists(ctx.author.id, ctx.author.name)
    ensure_player_exists(user.id, user.name)

    view = ExchangeView(ctx, ctx.author, user)
    embed = discord.Embed(title="⚖️ Exchange Request", description=f"{ctx.author.mention} wants to negotiate an exchange with {user.mention}!", color=discord.Color.blue())
    embed.add_field(name="Instructions", value="Use the buttons to add Cards or Coins. Lock when ready.")
    
    # Send the view immediately so buttons appear
    msg = await ctx.send(content=f"{user.mention}", embed=embed, view=view)
    view.message = msg



    
# ---------------------------------------------------------BATTLES REFACTORED-------------------------------------------------------------------------------------

class Battle:
    def __init__(self, ctx, player1, player2):
        self.ctx = ctx
        self.message = None 
        self.player1 = player1
        self.player2 = player2
        
        # State Data
        self.player1_deck = []
        self.player2_deck = []
        self.player1_used_cards = []
        self.player2_used_cards = []
        
        self.player1_wins = 0
        self.player2_wins = 0
        self.draws = 0 
        self.round = 1
        
        # --- FIX: Add this flag to prevent double counting ---
        self.round_resolved = False 
        self.last_result_text = ""
        self.last_winner = None
        
        self.turn_player = player1 
        self.p1_action = None
        self.p2_action = None
        self.p1_card = None
        self.p2_card = None
        
        self.draw_offers = set()
        self.phase = "SETUP"

    async def start(self):
        embed = discord.Embed(title="⚔️ Battle Arena ⚔️", description="Both players must select their decks to begin.")
        embed.add_field(name=self.player1.name, value="❌ Deck Not Selected", inline=True)
        embed.add_field(name=self.player2.name, value="❌ Deck Not Selected", inline=True)
        
        view = SetupView(self)
        self.message = await self.ctx.send(embed=embed, view=view)


    
    # --- SURRENDER LOGIC ---
    async def request_surrender(self, interaction):
        if interaction.user.id not in [self.player1.id, self.player2.id]:
            return await interaction.response.send_message("Only battlers can surrender!", ephemeral=True)
        
        view = SurrenderConfirmView(self, interaction.user)
        await interaction.response.send_message("Are you sure you want to surrender? This will count as a loss.", view=view, ephemeral=True)

    
    async def confirm_surrender(self, interaction, loser):
        winner = self.player1 if loser == self.player2 else self.player2
        
        conn = sqlite3.connect('cards_game.db')
        cursor = conn.cursor()
        
        try:
            cursor.execute('UPDATE players SET battles_played = battles_played + 1, battles_won = battles_won + 1 WHERE user_id = ?', (winner.id,))
            cursor.execute('UPDATE players SET battles_played = battles_played + 1, battles_lost = battles_lost + 1 WHERE user_id = ?', (loser.id,))
            cursor.execute('UPDATE players SET coins = coins + 200 WHERE user_id = ?', (winner.id,))
            cursor.execute('UPDATE players SET coins = coins + 100 WHERE user_id = ?', (loser.id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Surrender DB Error: {e}")
        finally:
            conn.close()
        
        # 1. Update the Battle Message (Background)
        embed = discord.Embed(title="🏳️ Battle Surrendered", color=discord.Color.red())
        embed.add_field(name="Result", value=f"**{winner.name}** wins! {loser.name} has surrendered.", inline=False)
        embed.add_field(name="Rewards", value=f"{winner.name}: +200 Coins\n{loser.name}: +100 Coins", inline=False)
        await self.message.edit(embed=embed, view=None)
        
        # 2. Acknowledge the Interaction (Ephemeral "You surrendered")
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(content="🏳️ You surrendered.", view=None)
        except:
            pass

        # 3. NOW check achievements (Safe because interaction is done)
        await self.check_achievements(winner.id, 'battles_won', interaction)

    # 2. DRAW LOGIC (Fixed DB Locking)
    async def confirm_draw(self, interaction):
        conn = sqlite3.connect('cards_game.db')
        cursor = conn.cursor()
        
        try:
            # Update Stats
            cursor.execute('UPDATE players SET battles_played = battles_played + 1, battles_drawn = battles_drawn + 1 WHERE user_id = ?', (self.player1.id,))
            cursor.execute('UPDATE players SET battles_played = battles_played + 1, battles_drawn = battles_drawn + 1 WHERE user_id = ?', (self.player2.id,))
            
            # Update Coins
            cursor.execute('UPDATE players SET coins = coins + 150 WHERE user_id = ?', (self.player1.id,))
            cursor.execute('UPDATE players SET coins = coins + 150 WHERE user_id = ?', (self.player2.id,))

            conn.commit()
        except Exception as e:
            logger.error(f"Draw DB Error: {e}")
        finally:
            conn.close()

        embed = discord.Embed(title="🤝 Battle Drawn", description="Both players agreed to a mutual draw.", color=discord.Color.greyple())
        embed.add_field(name="Rewards", value="Both players received +150 Coins", inline=False)
        
        await self.message.edit(embed=embed, view=None)
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except:
            pass

    # 3. ROUND UPDATE LOGIC (Fixed DB Locking)
    def update_round_db_stats(self, winner):
        conn = sqlite3.connect('cards_game.db')
        cursor = conn.cursor()

        try:
            # Update Player Stats
            cursor.execute('UPDATE players SET rounds_played = rounds_played + 1 WHERE user_id = ?', (self.player1.id,))
            cursor.execute('UPDATE players SET rounds_played = rounds_played + 1 WHERE user_id = ?', (self.player2.id,))
            
            if winner:
                loser = self.player2 if winner == self.player1 else self.player1
                cursor.execute('UPDATE players SET rounds_won = rounds_won + 1 WHERE user_id = ?', (winner.id,))
                cursor.execute('UPDATE players SET rounds_lost = rounds_lost + 1 WHERE user_id = ?', (loser.id,))
            else:
                cursor.execute('UPDATE players SET rounds_drawn = rounds_drawn + 1 WHERE user_id = ?', (self.player1.id,))
                cursor.execute('UPDATE players SET rounds_drawn = rounds_drawn + 1 WHERE user_id = ?', (self.player2.id,))

            # Update Card Stats (Rounds)
            for card, user in [(self.p1_card, self.player1), (self.p2_card, self.player2)]:
                cursor.execute('UPDATE inventories SET rounds_played = rounds_played + 1 WHERE card_id = ? AND user_id = ?', (card.card_id, user.id))
                cursor.execute('UPDATE cards SET total_rounds_played = total_rounds_played + 1 WHERE card_id = ?', (card.card_id,))

            if winner:
                winning_card = self.p1_card if winner == self.player1 else self.p2_card
                cursor.execute('UPDATE inventories SET rounds_won = rounds_won + 1 WHERE card_id = ? AND user_id = ?', (winning_card.card_id, winner.id))
                cursor.execute('UPDATE cards SET total_rounds_won = total_rounds_won + 1 WHERE card_id = ?', (winning_card.card_id,))

            conn.commit()
        except Exception as e:
            logger.error(f"Round Update DB Error: {e}")
        finally:
            conn.close()

    # 4. END GAME LOGIC (Fixed DB Locking + Card Stats)
    async def end_game(self, interaction, last_round_embed):
        # FIX: Acknowledge interaction immediately so followups work
        try:
            if interaction and not interaction.response.is_done():
                await interaction.response.defer()
        except:
            pass

        winner, loser, is_draw = None, None, False
        if self.player1_wins > self.player2_wins:
            winner, loser = self.player1, self.player2
        elif self.player2_wins > self.player1_wins:
            winner, loser = self.player2, self.player1
        else:
            is_draw = True

        conn = sqlite3.connect('cards_game.db')
        cursor = conn.cursor()

        try:
            cursor.execute('UPDATE players SET battles_played = battles_played + 1 WHERE user_id = ?', (self.player1.id,))
            cursor.execute('UPDATE players SET battles_played = battles_played + 1 WHERE user_id = ?', (self.player2.id,))

            def update_deck_stats(deck, user, won_battle):
                for card in deck:
                    cursor.execute('UPDATE inventories SET battles_played = battles_played + 1 WHERE card_id = ? AND user_id = ?', (card.card_id, user.id))
                    cursor.execute('UPDATE cards SET total_battles_played = total_battles_played + 1 WHERE card_id = ?', (card.card_id,))
                    if won_battle:
                        cursor.execute('UPDATE inventories SET battles_won = battles_won + 1 WHERE card_id = ? AND user_id = ?', (card.card_id, user.id))
                        cursor.execute('UPDATE cards SET total_battles_won = total_battles_won + 1 WHERE card_id = ?', (card.card_id,))

            if is_draw:
                cursor.execute('UPDATE players SET battles_drawn = battles_drawn + 1 WHERE user_id = ?', (self.player1.id,))
                cursor.execute('UPDATE players SET battles_drawn = battles_drawn + 1 WHERE user_id = ?', (self.player2.id,))
                cursor.execute('UPDATE players SET coins = coins + 150 WHERE user_id = ?', (self.player1.id,))
                cursor.execute('UPDATE players SET coins = coins + 150 WHERE user_id = ?', (self.player2.id,))
                
                update_deck_stats(self.player1_deck, self.player1, False)
                update_deck_stats(self.player2_deck, self.player2, False)

                embed = discord.Embed(title="🤝 Battle Drawn 🤝", color=discord.Color.greyple())
                embed.add_field(name="Result", value="The battle ended in a draw!", inline=False)
                embed.add_field(name="Rewards", value="Both players received +100 Coins", inline=False)
            else:
                cursor.execute('UPDATE players SET battles_won = battles_won + 1 WHERE user_id = ?', (winner.id,))
                cursor.execute('UPDATE players SET battles_lost = battles_lost + 1 WHERE user_id = ?', (loser.id,))
                cursor.execute('UPDATE players SET coins = coins + 200 WHERE user_id = ?', (winner.id,))
                cursor.execute('UPDATE players SET coins = coins + 100 WHERE user_id = ?', (loser.id,))
                
                update_deck_stats(self.player1_deck, self.player1, (winner == self.player1))
                update_deck_stats(self.player2_deck, self.player2, (winner == self.player2))

                embed = discord.Embed(title="🏆 Battle Finished 🏆", color=discord.Color.gold())
                embed.add_field(name="Winner", value=f"**{winner.name}**", inline=False)
                embed.add_field(name="Rewards", value=f"{winner.name}: +200 Coins\n{loser.name}: +100 Coins", inline=False)
            
            conn.commit()
        except Exception as e:
            logger.error(f"End Game DB Error: {e}")
        finally:
            conn.close()

        # Achievements (Safe now because we deferred earlier)
        if not is_draw:
            await self.check_achievements(winner.id, 'battles_won', interaction)

        embed.add_field(name="Final Score", value=f"{self.player1.name}: {self.player1_wins} | {self.player2.name}: {self.player2_wins} | Draws: {self.draws}", inline=False)
        
        if last_round_embed:
            embed.add_field(name="────── Final Round ──────", value=f"**Result:** {last_round_embed.description}", inline=False)
            for field in last_round_embed.fields:
                embed.add_field(name=field.name, value=field.value, inline=True)

        await self.message.edit(embed=embed, view=None)

    

    # --- DRAW LOGIC ---
    async def request_draw(self, interaction):
        user = interaction.user
        if user.id not in [self.player1.id, self.player2.id]:
            return await interaction.response.send_message("Not your battle!", ephemeral=True)

        # Check if this user already offered
        if user.id in self.draw_offers:
            return await interaction.response.send_message("You already offered a draw. Waiting for opponent...", ephemeral=True)

        self.draw_offers.add(user.id)

        # If BOTH have offered (meaning 2nd person clicked it)
        if len(self.draw_offers) >= 2:
            return await self.confirm_draw(interaction)
        
        # If only one person offered, update the view to show "Accept Draw"
        opponent = self.player2 if user == self.player1 else self.player1
        await interaction.response.send_message(f"🤝 Draw offer sent to {opponent.name}!", ephemeral=True)
        
        # Refresh the current view to update button color/text
        await self.update_game_state()

    

    def get_valid_deck(self, player):
        # 1. Select the correct deck and used list based on player ID
        if player.id == self.player1.id:
            full_deck = self.player1_deck
            used_cards = self.player1_used_cards
        else:
            full_deck = self.player2_deck
            used_cards = self.player2_used_cards
        
        # 2. Extract IDs of used cards for reliable comparison
        used_ids = {card.card_id for card in used_cards}
        
        # 3. Filter the deck: Keep cards ONLY if their ID is not in the used list
        return [card for card in full_deck if card.card_id not in used_ids]

    async def update_game_state(self, interaction=None):
        # 1. SETUP
        if self.phase == "SETUP":
            if self.player1_deck and self.player2_deck:
                self.phase = "ACTION"
                await self.update_game_state(interaction)
            return

        # 2. ACTION
        if self.phase == "ACTION":
            embed = discord.Embed(title=f"⚔️ Round {self.round} | Action Phase", color=discord.Color.blue())
            embed.add_field(name="Score", value=f"{self.player1.name}: {self.player1_wins} | {self.player2.name}: {self.player2_wins} | Draws: {self.draws}", inline=False)
            embed.add_field(name="Current Turn", value=f"It is **{self.turn_player.name}'s** turn to choose the tactic.", inline=False)
            
            view = ActionView(self, self.turn_player)
            
            if interaction and not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=view)
            else:
                await self.message.edit(embed=embed, view=view)

        # 3. CARD SELECT
        elif self.phase == "CARD_SELECT":
            p1_status = "✅ Selected" if self.p1_card else "⏳ Waiting..."
            p2_status = "✅ Selected" if self.p2_card else "⏳ Waiting..."

            embed = discord.Embed(title=f"⚔️ Round {self.round} | Card Phase", color=discord.Color.gold())
            embed.add_field(name="Tactics", value=f"{self.player1.name}: **{self.p1_action.upper()}**\n{self.player2.name}: **{self.p2_action.upper()}**", inline=False)
            embed.add_field(name="Card Selection", value=f"**{self.player1.name}:** {p1_status}\n**{self.player2.name}:** {p2_status}", inline=False)
            
            view = CardSelectView(self)
            
            if interaction and not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=view)
            else:
                await self.message.edit(embed=embed, view=view)

        # 4. RESULT
        elif self.phase == "RESULT":
            if not self.round_resolved:
                result_text, winner = self.calculate_winner()
                self.update_round_db_stats(winner)
                
                # --- FIX: Restore these lines to track used cards ---
                self.player1_used_cards.append(self.p1_card)
                self.player2_used_cards.append(self.p2_card)
                # --------------------------------------------------

                # Store result
                self.last_result_text = result_text
                self.last_winner = winner
                self.round_resolved = True
            else:
                result_text = self.last_result_text
                winner = self.last_winner

            embed = discord.Embed(title=f"⚔️ Round {self.round} Result", description=result_text, color=discord.Color.purple())
            embed.add_field(name=f"{self.player1.name} ({self.p1_action})", value=f"**{self.p1_card.name}**\n⭐ {self.p1_card.overall}\n⚔️ {self.p1_card.attack} | 🛡️ {self.p1_card.defense} | ⚡ {self.p1_card.speed}", inline=True)
            embed.add_field(name=f"{self.player2.name} ({self.p2_action})", value=f"**{self.p2_card.name}**\n⭐ {self.p2_card.overall}\n⚔️ {self.p2_card.attack} | 🛡️ {self.p2_card.defense} | ⚡ {self.p2_card.speed}", inline=True)
            
            if self.player1_wins >= 3 or self.player2_wins >= 3 or self.round == 5:
                self.phase = "GAME_OVER"
                await self.end_game(interaction, embed) 
            else:
                view = NextRoundView(self)
                if interaction and not interaction.response.is_done():
                    await interaction.response.edit_message(embed=embed, view=view)
                else:
                    await self.message.edit(embed=embed, view=view)
                
                # Check Achievements
                if winner and not self.round_resolved: 
                     await self.check_achievements(winner.id, 'rounds_won', interaction)

    def calculate_winner(self):
        p1_val, p2_val, stat_name = 0, 0, "Stat"
        if self.p1_action == 'attack' and self.p2_action == 'defense':
             p1_val, p2_val, stat_name = self.p1_card.attack, self.p2_card.defense, "Attack vs Defense"
        elif self.p1_action == 'defense' and self.p2_action == 'attack':
             p1_val, p2_val, stat_name = self.p1_card.defense, self.p2_card.attack, "Defense vs Attack"
        else: 
             p1_val, p2_val, stat_name = self.p1_card.speed, self.p2_card.speed, "Speed vs Speed"

        if p1_val > p2_val:
            self.player1_wins += 1
            return f"🏆 **{self.player1.name}** Wins with {stat_name}!", self.player1
        elif p2_val > p1_val:
            self.player2_wins += 1
            return f"🏆 **{self.player2.name}** Wins with {stat_name}!", self.player2
        else:
            if self.p1_card.overall > self.p2_card.overall:
                self.player1_wins += 1
                return f"⚠️ Stats Draw! **{self.player1.name}** wins on Overall ({self.p1_card.overall} vs {self.p2_card.overall})!", self.player1
            elif self.p2_card.overall > self.p1_card.overall:
                self.player2_wins += 1
                return f"⚠️ Stats Draw! **{self.player2.name}** wins on Overall ({self.p2_card.overall} vs {self.p1_card.overall})!", self.player2
            else:
                self.draws += 1
                return f"🤝 **It's a Draw!** Both stats and overall are equal!", None

    

    
    async def check_achievements(self, user_id, stat_type, interaction):
        try:
            conn = sqlite3.connect('cards_game.db')
            cursor = conn.cursor()
            cursor.execute(f'SELECT {stat_type} FROM players WHERE user_id = ?', (user_id,))
            stat_value = cursor.fetchone()[0]
            if stat_type == 'rounds_won':
                thresholds = {10: 1, 50: 2, 100: 8}
            elif stat_type == 'battles_won':
                thresholds = {1: 3, 10: 4, 25: 5, 50: 6, 100: 8}
            for threshold, achievement_id in thresholds.items():
                if stat_value == threshold:
                    cursor.execute('INSERT OR IGNORE INTO user_achievements (user_id, achievement_id) VALUES (?, ?)', (user_id, achievement_id))
                    conn.commit()
                    cursor.execute('SELECT title, description FROM achievements WHERE achievement_id = ?', (achievement_id,))
                    achievement = cursor.fetchone()
                    if achievement and interaction:
                        user = await interaction.client.fetch_user(user_id)
                        await interaction.followup.send(f"🎉 **Achievement Unlocked!** {user.mention} unlocked **{achievement[0]}**: {achievement[1]}", ephemeral=True)
            conn.close()
        except Exception as e:
            logger.error(f"Error checking achievements: {e}")


# ---------------- VIEWS ----------------

# Helper Function to configure Surrender/Draw buttons dynamically
def configure_battle_buttons(view, battle):
    # Surrender Button (Always Red)
    view.add_item(SurrenderButton(battle))
    
    # Draw Button (Dynamic)
    label = "Offer Draw"
    style = discord.ButtonStyle.secondary
    
    # If someone offered, change look for visual urgency
    if len(battle.draw_offers) > 0:
        label = "Accept Draw 🤝"
        style = discord.ButtonStyle.success
        
    view.add_item(DrawButton(battle, label=label, style=style))

# --- BUTTON CLASSES ---

class SurrenderButton(discord.ui.Button):
    def __init__(self, battle):
        super().__init__(style=discord.ButtonStyle.danger, label="Surrender", emoji="🏳️", row=2)
        self.battle = battle

    async def callback(self, interaction: discord.Interaction):
        await self.battle.request_surrender(interaction)

class DrawButton(discord.ui.Button):
    def __init__(self, battle, label="Offer Draw", style=discord.ButtonStyle.secondary):
        super().__init__(style=style, label=label, emoji="🤝", row=2)
        self.battle = battle

    async def callback(self, interaction: discord.Interaction):
        await self.battle.request_draw(interaction)

class SurrenderConfirmView(discord.ui.View):
    def __init__(self, battle, user):
        super().__init__(timeout=60)
        self.battle = battle
        self.user = user

    @discord.ui.button(label="Yes, Surrender", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id: return
        await self.battle.confirm_surrender(interaction, self.user)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id: return
        await interaction.response.edit_message(content="Surrender cancelled.", view=None)

# --- PHASE VIEWS ---

class SetupView(discord.ui.View):
    def __init__(self, battle):
        super().__init__(timeout=120)
        self.battle = battle
        self.add_item(DeckSelectMenu(battle, battle.player1))
        self.add_item(DeckSelectMenu(battle, battle.player2))

    @discord.ui.button(label="Cancel Setup", style=discord.ButtonStyle.red, row=2)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in [self.battle.player1.id, self.battle.player2.id]:
            return await interaction.response.send_message("Not your battle.", ephemeral=True)
        await interaction.response.edit_message(content="Battle setup cancelled.", embed=None, view=None)

class DeckSelectMenu(discord.ui.Select):
    def __init__(self, battle, player):
        self.battle = battle
        self.player = player
        cursor.execute('SELECT deck_name FROM decks WHERE user_id = ?', (player.id,))
        decks = cursor.fetchall()
        options = [discord.SelectOption(label=d[0]) for d in decks] if decks else [discord.SelectOption(label="No Decks", value="none")]
        super().__init__(placeholder=f"{player.name}, choose...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.player.id:
            return await interaction.response.send_message("Not for you!", ephemeral=True)
        
        deck_name = self.values[0]
        if deck_name == "none": return await interaction.response.send_message("Create a deck first!", ephemeral=True)

        deck_cards = get_deck(self.player.id, deck_name)
        if self.player.id == self.battle.player1.id: self.battle.player1_deck = deck_cards
        else: self.battle.player2_deck = deck_cards

        if self.battle.player1_deck and self.battle.player2_deck:
             await self.battle.update_game_state(interaction)
        else:
            embed = interaction.message.embeds[0]
            index = 0 if self.player.id == self.battle.player1.id else 1
            embed.set_field_at(index, name=self.player.name, value=f"✅ Ready ({deck_name})", inline=True)
            await interaction.response.edit_message(embed=embed, view=self.view)

class ActionView(discord.ui.View):
    def __init__(self, battle, turn_player):
        super().__init__(timeout=60)
        self.battle = battle
        self.turn_player = turn_player
        
        # Add Surrender/Draw buttons dynamically
        configure_battle_buttons(self, battle)

    @discord.ui.button(label="Attack", style=discord.ButtonStyle.danger)
    async def attack(self, interaction: discord.Interaction, button: discord.ui.Button): await self.process_action(interaction, "attack")

    @discord.ui.button(label="Defense", style=discord.ButtonStyle.primary)
    async def defense(self, interaction: discord.Interaction, button: discord.ui.Button): await self.process_action(interaction, "defense")
        
    @discord.ui.button(label="Speed", style=discord.ButtonStyle.success)
    async def speed(self, interaction: discord.Interaction, button: discord.ui.Button): await self.process_action(interaction, "speed")

    async def process_action(self, interaction, action):
        if interaction.user.id != self.turn_player.id:
            return await interaction.response.send_message("Not your turn!", ephemeral=True)
        
        if self.turn_player.id == self.battle.player1.id:
            self.battle.p1_action = action
            if action == "attack": self.battle.p2_action = "defense"
            elif action == "defense": self.battle.p2_action = "attack"
            else: self.battle.p2_action = "speed"
        else:
            self.battle.p2_action = action
            if action == "attack": self.battle.p1_action = "defense"
            elif action == "defense": self.battle.p1_action = "attack"
            else: self.battle.p1_action = "speed"
            
        self.battle.phase = "CARD_SELECT"
        await self.battle.update_game_state(interaction)

class CardSelectView(discord.ui.View):
    def __init__(self, battle):
        super().__init__(timeout=60)
        self.battle = battle
        self.add_item(CardDropdown(battle, battle.player1))
        self.add_item(CardDropdown(battle, battle.player2))
        
        # Add Surrender/Draw buttons dynamically
        configure_battle_buttons(self, battle)

class CardDropdown(discord.ui.Select):
    def __init__(self, battle, player):
        self.battle = battle
        self.player = player
        cards = battle.get_valid_deck(player)
        options = [discord.SelectOption(label=c.name, description=f"OVR: {c.overall}", value=str(c.card_id)) for c in cards]
        super().__init__(placeholder=f"{player.name}'s Card", options=options, min_values=1, max_values=1)
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.player.id: return await interaction.response.send_message("Not for you!", ephemeral=True)
            
        selected_id = int(self.values[0])
        deck = self.battle.player1_deck if self.player.id == self.battle.player1.id else self.battle.player2_deck
        card_obj = next((c for c in deck if c.card_id == selected_id), None)
        
        if self.player.id == self.battle.player1.id: self.battle.p1_card = card_obj
        else: self.battle.p2_card = card_obj

        if self.battle.p1_card and self.battle.p2_card:
            self.battle.phase = "RESULT"
            await self.battle.update_game_state(interaction)
        else:
            await self.battle.update_game_state(interaction)

class NextRoundView(discord.ui.View):
    def __init__(self, battle):
        super().__init__(timeout=60)
        self.battle = battle
        self.ready_players = set()
        
        # Add Surrender/Draw buttons dynamically
        configure_battle_buttons(self, battle)

    @discord.ui.button(label="Ready for Next Round", style=discord.ButtonStyle.primary, row=0)
    async def next_round(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in [self.battle.player1.id, self.battle.player2.id]: 
            return await interaction.response.send_message("Not your battle.", ephemeral=True)
        
        if interaction.user.id in self.ready_players: 
            return await interaction.response.send_message("Waiting for opponent...", ephemeral=True)

        self.ready_players.add(interaction.user.id)
        
        if len(self.ready_players) == 2:
            # RESET ROUND DATA
            self.battle.p1_action = None
            self.battle.p2_action = None
            self.battle.p1_card = None
            self.battle.p2_card = None
            self.battle.round += 1
            
            # --- FIX: Reset the safety flag for the new round ---
            self.battle.round_resolved = False 
            
            # Rotate Turn
            if self.battle.turn_player.id == self.battle.player1.id: 
                self.battle.turn_player = self.battle.player2
            else: 
                self.battle.turn_player = self.battle.player1

            self.battle.phase = "ACTION"
            await self.battle.update_game_state(interaction)
        else:
            await interaction.response.send_message(f"{interaction.user.name} is ready! Waiting for opponent...", ephemeral=False)


# ---------------- COMMANDS ----------------

class BattleInviteView(discord.ui.View):
    def __init__(self, ctx, challenger, challengee):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.challenger = challenger
        self.challengee = challengee

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.challengee.id:
            return await interaction.response.send_message("Not for you!", ephemeral=True)
        
        battle_instance = Battle(self.ctx, self.challenger, self.challengee)
        await interaction.response.edit_message(content="Battle Accepted! Loading Arena...", embed=None, view=None)
        await battle_instance.start()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.challengee.id:
            return await interaction.response.send_message("Not for you!", ephemeral=True)
        await interaction.response.edit_message(content="Battle Declined.", embed=None, view=None)


@bot.hybrid_command(name='battle', description="Challenge a player to a battle")
async def battle(ctx, user: discord.User):
    if user.id == ctx.author.id:
        return await ctx.send("You cannot battle yourself.")
        
    ensure_player_exists(ctx.author.id, ctx.author.name)
    ensure_player_exists(user.id, user.name)
    
    embed = discord.Embed(title="Battle Request", description=f"{ctx.author.name} has challenged {user.name} to a battle!")
    view = BattleInviteView(ctx, ctx.author, user)
    await ctx.send(embed=embed, view=view)


def get_deck(user_id, deck_name):
    cursor.execute('SELECT cards FROM decks WHERE user_id = ? AND deck_name = ?', (user_id, deck_name))
    result = cursor.fetchone()
    if result is None:
        return None
    
    card_ids = list(map(int, result[0].split(',')))
    cards = []
    for card_id in card_ids:
        cursor.execute('SELECT * FROM cards WHERE card_id = ?', (card_id,))
        card_data = cursor.fetchone()
        if card_data:
            cards.append(Card(*card_data))
    return cards

#---------------------------------------------------------COINS AND SALES-------------------------------------------------------------------------------------

def increment_cards_sold(user_id):
    cursor.execute('UPDATE players SET cards_sold = cards_sold + 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    

def check_card_ownership(user_id, card_id):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inventories WHERE user_id = ? AND card_id = ?", (user_id, card_id))
    ownership = cursor.fetchone()
    conn.close()
    return ownership is not None


def add_coins(user_id, coins):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE players SET coins = coins + ? WHERE user_id = ?", (coins, user_id))
    conn.commit()
    conn.close()

def remove_card_from_inventory(user_id, card_id):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM inventories WHERE user_id = ? AND card_id = ?", (user_id, card_id))
    conn.commit()
    conn.close()

class ConfirmButton(Button):
    def __init__(self, card, user_id, sale_value):
        super().__init__(style=discord.ButtonStyle.green, label="Confirm")
        self.card = card
        self.user_id = user_id
        self.sale_value = sale_value

    async def callback(self, interaction):
        add_coins(self.user_id, self.sale_value)
        remove_card_from_inventory(self.user_id, self.card.card_id)
        increment_cards_sold(self.user_id)
        
        embed = interaction.message.embeds[0]
        embed.add_field(name="Status", value="Sold", inline=True)
        embed.set_image(url=f"attachment://{self.card.image_path.split('/')[-1]}")  # Ensure the image is correctly set
        await interaction.response.edit_message(embed=embed, content="The card has been sold.", view=None)
        logger.info(f'Card {self.card.card_id} sold by user {self.user_id}')

class DeclineButton(Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.red, label="Decline")

    async def callback(self, interaction):
        await interaction.response.edit_message(content="The sale has been declined.", view=None)


def calculate_card_value(card):
    """Calculates the sell price of a card."""
    card_type = card.card_type.lower()
    overall = card.overall
    
    # Base Value
    if "icon" in card_type:
        value = 250
    elif "hero" in card_type:
        value = 175
    else:
        value = 100
        
    # Overall Multiplier
    if overall >= 70:
        value += 50 + ((overall - 70) * 5)
        
    return int(value)


@bot.hybrid_command(name='sell', description="Sell cards for coins. Use arguments to quick-select.")
@app_commands.describe(
    card1="Optional: First card to sell",
    card2="Optional: Second card to sell",
    card3="Optional: Third card to sell",
    card4="Optional: Fourth card to sell",
    card5="Optional: Fifth card to sell"
)
@app_commands.autocomplete(
    card1=card_search_autocomplete,
    card2=card_search_autocomplete,
    card3=card_search_autocomplete,
    card4=card_search_autocomplete,
    card5=card_search_autocomplete
)
async def sell(ctx, card1: str = None, card2: str = None, card3: str = None, card4: str = None, card5: str = None):
    ensure_player_exists(ctx.author.id, ctx.author.name)
    
    # 1. Fetch Inventory
    inventory, editions = get_player_inventory(ctx.author.id)
    if not inventory:
        return await ctx.send("You have no cards to sell!")

    # 2. Process Arguments (Resolve inputs to IDs)
    inputs = [card1, card2, card3, card4, card5]
    pre_selected_ids = []
    
    # Map IDs to inventory items for validation
    inventory_map = {c.card_id: c for c in inventory}
    
    warnings = []

    for item in inputs:
        if not item: continue
        
        target_id = None
        
        # A. Check if Autocomplete ID
        if item.isdigit():
            target_id = int(item)
        else:
            # B. Check if Manual Text Name
            found = get_card_by_name(item)
            if found:
                target_id = found.card_id
        
        # Validation
        if target_id:
            if target_id in inventory_map:
                if target_id not in pre_selected_ids:
                    pre_selected_ids.append(target_id)
            else:
                warnings.append(f"You don't own ID {target_id} (or input '{item}')")
        else:
            warnings.append(f"Could not find card '{item}'")

    # 3. Launch UI
    view = MultiSellView(ctx, inventory, initial_ids=pre_selected_ids)
    
    # Inside the sell command...
    embed = discord.Embed(title="📉 Transfer Market", description="Initializing...", color=discord.Color.blue())
    embed.add_field(name="Loading...", value="Preparing your sell offer...", inline=False)
    
    if warnings:
        embed.set_footer(text=f"⚠️ Warning: {', '.join(warnings[:3])}")
    
    msg = await ctx.send(embed=embed, view=view)
    view.message = msg
    
    # Force immediate update to show pre-selected items
    await view.update_display(ctx.interaction if ctx.interaction else None)

# ---------------------------------------------------------UNIFIED SELL MENU-------------------------------------------------------------------------------------

class MultiSellSelect(discord.ui.Select):
    def __init__(self, page_cards, selected_ids):
        options = []
        for i, card in enumerate(page_cards):
            is_selected = card.card_id in selected_ids
            value = calculate_card_value(card)
            
            # Formatting: "✅ Messi" vs "Messi"
            label = f"{'✅ ' if is_selected else ''}{card.name}"
            desc = f"Sell for: {value} coins | OVR: {card.overall}"
            
            options.append(discord.SelectOption(
                label=label, 
                description=desc, 
                value=f"{card.card_id}-{i}",
                emoji="💰"
            ))

        super().__init__(
            placeholder="Select cards to add/remove...",
            min_values=1,
            max_values=1, 
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        card_id = int(self.values[0].split('-')[0])
        
        # Toggle: If in list, remove it. If not, add it.
        if card_id in view.selected_ids:
            view.selected_ids.remove(card_id)
        else:
            view.selected_ids.append(card_id)

        await view.update_display(interaction)

class MultiSellView(discord.ui.View):
    def __init__(self, ctx, inventory, initial_ids=None):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.inventory = inventory
        
        # Pre-load the cart with cards from command arguments
        self.selected_ids = initial_ids if initial_ids else []
        
        self.current_page = 0
        self.items_per_page = 20
        self.total_pages = max(1, (len(inventory) - 1) // self.items_per_page + 1)
        
        # Determine starting page (try to show the first selected card if possible)
        if self.selected_ids:
            first_selected = self.selected_ids[0]
            for i, card in enumerate(inventory):
                if card.card_id == first_selected:
                    self.current_page = i // self.items_per_page
                    break

        self.update_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("⛔ This is not your menu.", ephemeral=True)
            return False
        return True

    def get_page_items(self):
        start = self.current_page * self.items_per_page
        end = start + self.items_per_page
        return self.inventory[start:end]

    def update_components(self):
        self.clear_items()
        
        # 1. Dropdown
        page_cards = self.get_page_items()
        if page_cards:
            self.add_item(MultiSellSelect(page_cards, self.selected_ids))

        # 2. Navigation
        if self.current_page > 0:
            self.add_item(BuilderPrevButton()) 
        if self.current_page < self.total_pages - 1:
            self.add_item(BuilderNextButton())

        # 3. Actions
        # Enable Confirm button only if at least 1 card is selected
        self.add_item(MultiSellConfirmButton(disabled=(len(self.selected_ids) == 0)))
        self.add_item(BuilderCancelButton())

    async def update_display(self, interaction):
        self.update_components()
        
        # Calculate Totals
        total_value = 0
        selected_count = len(self.selected_ids)
        
        # Generate Display List
        if selected_count > 0:
            item_list = []
            for cid in self.selected_ids:
                card = next((c for c in self.inventory if c.card_id == cid), None)
                if card:
                    val = calculate_card_value(card)
                    total_value += val
                    # Format: "Messi (94) • 150c"
                    item_list.append(f"• **{card.name}** ({card.overall}) — {val:,} coins")
            
            # Show last 10 items
            display_str = "\n".join(item_list[-10:])
            if len(item_list) > 10:
                display_str += f"\n...and {len(item_list)-10} more"
        else:
            display_str = "*No assets selected.*"

        embed = discord.Embed(title="📉 Transfer Market", color=discord.Color.blue())
        embed.add_field(name=f"Selected Assets [{selected_count}]", value=display_str, inline=False)
        embed.add_field(name="Transaction Value", value=f"**{total_value:,}** Coins", inline=False)
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages}")

        if interaction and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await self.message.edit(embed=embed, view=self)

class MultiSellConfirmButton(discord.ui.Button):
    def __init__(self, disabled=True):
        style = discord.ButtonStyle.grey if disabled else discord.ButtonStyle.green
        label = "Select Cards..." if disabled else "Confirm Sale"
        emoji = "💵"
        super().__init__(label=label, style=style, emoji=emoji, disabled=disabled, row=2)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        user_id = interaction.user.id
        
        total_coins = 0
        count = 0
        
        conn = sqlite3.connect('cards_game.db')
        cursor = conn.cursor()
        
        try:
            for cid in view.selected_ids:
                # 1. Verify Ownership
                cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (user_id, cid))
                if not cursor.fetchone(): continue
                
                # 2. Get Value
                card_obj = next((c for c in view.inventory if c.card_id == cid), None)
                if not card_obj: continue
                
                val = calculate_card_value(card_obj)
                total_coins += val
                count += 1
                
                # 3. Delete & Update
                cursor.execute('DELETE FROM inventories WHERE user_id = ? AND card_id = ?', (user_id, cid))
                cursor.execute('UPDATE players SET cards_sold = cards_sold + 1 WHERE user_id = ?', (user_id,))
            
            # 4. Give Coins
            cursor.execute('UPDATE players SET coins = coins + ? WHERE user_id = ?', (total_coins, user_id))
            conn.commit()
            
            embed = discord.Embed(title="✅ Sale Complete!", color=discord.Color.green())
            embed.description = f"You sold **{count}** cards for **{total_coins:,}** coins."
            
            await interaction.response.edit_message(embed=embed, view=None)
            view.stop()
            logger.info(f"{interaction.user.name} sold {count} cards for {total_coins}")
            
        except Exception as e:
            logger.error(f"Sell Error: {e}")
            await interaction.response.send_message("❌ Database error.", ephemeral=True)
        finally:
            conn.close()

#---------------------------------------------------------PACKS-------------------------------------------------------------------------------------

PACKS = {
    1: {
        "name": "rare_player_pack",
        "display_name": "Rare Player Pack",
        "buyable": True,
        "cost": 1000
    },
    2: {
        "name": "icon_pack",
        "display_name": "Icon Pack",
        "buyable": True,
        "cost": 2500
    },

    3: {
        "name": "hero_pack",
        "display_name": "Hero Pack",
        "buyable": True,
        "cost": 1750  
    },

    4: {
        "name": "tester_pack",
        "display_name": "Tester Pack",
        "buyable": False,
        "cost": 0  # Not buyable, so cost is 0
    }
}



@bot.hybrid_command(name='shop', description="View the pack shop")
async def shop(ctx):
    embed = discord.Embed(title="Shop", description="Available packs for purchase:\nUse `buy pack_no` to buy the pack.")
    for pack_id, pack_info in PACKS.items():
        if pack_info["buyable"]:
            embed.add_field(name=pack_info["display_name"], value=f"Pack ID: {pack_id}\nCost: {pack_info['cost']} coins", inline=False)
    await ctx.send(embed=embed)


@bot.hybrid_command(name='buy', description="Buy a pack with coins")
async def buy(ctx, pack_id: int):
    user_id = ctx.author.id
    
    if pack_id not in PACKS:
        await ctx.send("Invalid pack ID.")
        return

    pack = PACKS[pack_id]

    # --- FIX: Check the 'buyable' key properly ---
    if not pack["buyable"]:
        await ctx.send("⛔ This pack cannot be purchased.")
        return
    # ---------------------------------------------

    pack_name = pack["name"]
    cost = pack["cost"]

    if not has_sufficient_coins(user_id, cost):
        await ctx.send(f"You need **{cost}** coins to buy this pack.")
        return
    
    deduct_coins(user_id, cost)
    add_pack_to_user(user_id, pack['name'])
    await ctx.send(f"✅ You have bought a **{pack['display_name']}** for {cost} coins.")
    logger.info(f"User {ctx.author.name} bought a {pack['display_name']} pack.")

@bot.hybrid_command(name='packs', description="View your unopened packs")
async def packs(ctx):
    user_id = ctx.author.id
    user_packs = get_user_packs(user_id)
    
    if not user_packs:
        await ctx.send("You don't have any packs.")
        return
    
    embed = discord.Embed(title=f"{ctx.author.name}'s Packs")
    has_packs = False
    for pack_id, pack in PACKS.items():
        pack_name = pack['name']
        pack_quantity = user_packs.get(pack_name, 0)
        if pack_quantity > 0:
            has_packs = True
            embed.add_field(name=f"{pack['display_name']} (ID: {pack_id})", value=f"Quantity: {pack_quantity}", inline=False)
    
    if not has_packs:
        await ctx.send("You don't have any packs.")
    else:
        await ctx.send(embed=embed)


# Helper functions
def get_user_packs(user_id):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM packs WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))
    return {}


def add_pack_to_user(user_id, pack_name):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    
    # Check if the user exists in the packs table
    cursor.execute('SELECT * FROM packs WHERE user_id = ?', (user_id,))
    user_packs = cursor.fetchone()
    
    if user_packs:
        # User exists, update the pack quantity
        cursor.execute(f'UPDATE packs SET {pack_name} = {pack_name} + 1 WHERE user_id = ?', (user_id,))
    else:
        # User does not exist, insert a new record with initial quantities
        cursor.execute('INSERT INTO packs (user_id, rare_player_pack, icon_pack, hero_pack, tester_pack) VALUES (?, 0, 0, 0, 0)', (user_id,))
        cursor.execute(f'UPDATE packs SET {pack_name} = {pack_name} + 1 WHERE user_id = ?', (user_id,))
    
    conn.commit()
    conn.close()




def remove_pack_from_user(user_id, pack_name):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute(f"UPDATE packs SET {pack_name} = {pack_name} - 1 WHERE user_id = ? AND {pack_name} > 0", (user_id,))
    conn.commit()
    conn.close()

def has_sufficient_coins(user_id, cost):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute("SELECT coins FROM players WHERE user_id = ?", (user_id,))
    coins = cursor.fetchone()[0]
    conn.close()
    return coins >= cost

def deduct_coins(user_id, amount):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE players SET coins = coins - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def get_player_id(username):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM players WHERE name = ?', (username,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return result[0]
    return None

def is_duplicate_card(user_id, card_id):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM inventories WHERE user_id = ? AND card_id = ?', (user_id, card_id))
    result = cursor.fetchone()[0]
    conn.close()
    return result > 0


@bot.hybrid_command(name='open', description="Open a pack")
async def open(ctx, pack_id: int):
    user_id = ctx.author.id

    if pack_id not in PACKS:
        await ctx.send("Invalid pack ID.")
        return

    pack_name = PACKS[pack_id]["name"]

    user_packs = get_user_packs(user_id)
    if not user_packs or user_packs.get(pack_name, 0) <= 0:
        await ctx.send("You don't own this pack.")
        return

    # Open the pack based on its type
    if pack_id == 1:
        card_obtained = await open_rare_player_pack(ctx, user_id)
    elif pack_id == 2:
        card_obtained = await open_icon_pack(ctx, user_id)
    elif pack_id == 3:
        card_obtained = await open_hero_pack(ctx, user_id)
    elif pack_id == 4:
        card_obtained = await open_tester_pack(ctx, user_id)

    remove_pack_from_user(user_id, pack_name)
    await ctx.send(f"You have opened a {PACKS[pack_id]['display_name']} and obtained {card_obtained}.")



async def open_rare_player_pack(ctx, user_id):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()

    card = None
    while True:
        # Define the card types and their respective probabilities
        card_types = ['Standard', 'Other']
        probabilities = [0.8, 0.2]

        # Choose a card type based on the defined probabilities
        chosen_type = random.choices(card_types, probabilities)[0]

        if chosen_type == 'Standard':
            cursor.execute("SELECT card_id, name, card_rarity, card_type, attack, defense, speed, overall, league, nation, image_path FROM cards WHERE card_type = 'Standard' AND overall > 85 ORDER BY RANDOM() LIMIT 1")
        else:
            cursor.execute(f"SELECT card_id, name, card_rarity, card_type, attack, defense, speed, overall, league, nation, image_path FROM cards WHERE card_type != 'Standard' AND card_type NOT IN ({_ndt_sql}) AND overall > 85 ORDER BY RANDOM() LIMIT 1", NON_DROPPABLE_TYPES)

        card = cursor.fetchone()

        if card and not is_duplicate_card(user_id, card[0]):
            break

    card_id, name, rarity, card_type, attack, defense, speed, overall, league, nation, image_path = card

    # Increment the copies attribute of the chosen card
    cursor.execute('UPDATE cards SET copies = copies + 1 WHERE card_id = ?', (card_id,))
    conn.commit()

    # Add the card to the user's inventory
    add_card_to_inventory(user_id, card_id)
    conn.close()

    embed = discord.Embed(title="You have received a card!", description=f"**{name}**")
    embed.set_image(url=f"attachment://{image_path.split('/')[-1]}")
    embed.add_field(name="Rarity", value=rarity, inline=True)
    embed.add_field(name="Type", value=card_type, inline=True)
    embed.add_field(name="Attack", value=attack, inline=True)
    embed.add_field(name="Defense", value=defense, inline=True)
    embed.add_field(name="Speed", value=speed, inline=True)
    embed.add_field(name="Overall", value=overall, inline=True)
    embed.add_field(name="League", value=league, inline=True)
    embed.add_field(name="Nation", value=nation, inline=True)
    embed.add_field(name="Copies", value=1, inline=True)
    embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
    file = discord.File(image_path, filename=image_path.split('/')[-1])
    await ctx.send(embed=embed, file=file)
    return name



async def open_icon_pack(ctx, user_id):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()

    card = None
    while True:
        cursor.execute("SELECT card_id, name, card_rarity, card_type, attack, defense, speed, overall, league, nation, image_path FROM cards WHERE card_type = 'Icon' ORDER BY RANDOM() LIMIT 1")
        card = cursor.fetchone()

        if card and not is_duplicate_card(user_id, card[0]):
            break

    card_id, name, rarity, card_type, attack, defense, speed, overall, league, nation, image_path = card

    # Increment the copies attribute of the chosen card
    cursor.execute('UPDATE cards SET copies = copies + 1 WHERE card_id = ?', (card_id,))
    conn.commit()

    # Add the card to the user's inventory
    add_card_to_inventory(user_id, card_id)
    conn.close()

    embed = discord.Embed(title="You have received a card!", description=f"**{name}**")
    embed.set_image(url=f"attachment://{image_path.split('/')[-1]}")
    embed.add_field(name="Rarity", value=rarity, inline=True)
    embed.add_field(name="Type", value=card_type, inline=True)
    embed.add_field(name="Attack", value=attack, inline=True)
    embed.add_field(name="Defense", value=defense, inline=True)
    embed.add_field(name="Speed", value=speed, inline=True)
    embed.add_field(name="Overall", value=overall, inline=True)
    embed.add_field(name="League", value=league, inline=True)
    embed.add_field(name="Nation", value=nation, inline=True)
    embed.add_field(name="Copies", value=1, inline=True)
    embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
    file = discord.File(image_path, filename=image_path.split('/')[-1])
    await ctx.send(embed=embed, file=file)
    return name

async def open_hero_pack(ctx, user_id):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()

    card = None
    while True:
        cursor.execute("SELECT card_id, name, card_rarity, card_type, attack, defense, speed, overall, league, nation, image_path FROM cards WHERE card_type = 'Hero' ORDER BY RANDOM() LIMIT 1")
        card = cursor.fetchone()

        if card and not is_duplicate_card(user_id, card[0]):
            break

    card_id, name, rarity, card_type, attack, defense, speed, overall, league, nation, image_path = card

    # Increment the copies attribute of the chosen card
    cursor.execute('UPDATE cards SET copies = copies + 1 WHERE card_id = ?', (card_id,))
    conn.commit()

    # Add the card to the user's inventory
    add_card_to_inventory(user_id, card_id)
    conn.close()

    embed = discord.Embed(title="You have received a card!", description=f"**{name}**")
    embed.set_image(url=f"attachment://{image_path.split('/')[-1]}")
    embed.add_field(name="Rarity", value=rarity, inline=True)
    embed.add_field(name="Type", value=card_type, inline=True)
    embed.add_field(name="Attack", value=attack, inline=True)
    embed.add_field(name="Defense", value=defense, inline=True)
    embed.add_field(name="Speed", value=speed, inline=True)
    embed.add_field(name="Overall", value=overall, inline=True)
    embed.add_field(name="League", value=league, inline=True)
    embed.add_field(name="Nation", value=nation, inline=True)
    embed.add_field(name="Copies", value=1, inline=True)
    embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
    file = discord.File(image_path, filename=image_path.split('/')[-1])
    await ctx.send(embed=embed, file=file)
    return name


async def open_tester_pack(ctx, user_id):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()

    icon_card = None
    while True:
        cursor.execute("SELECT card_id, name, card_rarity, card_type, attack, defense, speed, overall, league, nation, image_path FROM cards WHERE card_type = 'Icon' ORDER BY RANDOM() LIMIT 1")
        icon_card = cursor.fetchone()

        if icon_card and not is_duplicate_card(user_id, icon_card[0]):
            break

    high_overall_cards = []
    while len(high_overall_cards) < 4:
        card_types = ['Standard', 'Other']
        probabilities = [0.9, 0.1]

        chosen_type = random.choices(card_types, probabilities)[0]

        if chosen_type == 'Standard':
            cursor.execute("SELECT card_id, name, card_rarity, card_type, attack, defense, speed, overall, league, nation, image_path FROM cards WHERE card_type = 'Standard' AND overall > 85 ORDER BY RANDOM() LIMIT 1")
        else:
            cursor.execute(f"SELECT card_id, name, card_rarity, card_type, attack, defense, speed, overall, league, nation, image_path FROM cards WHERE card_type != 'Standard' AND card_type NOT IN ({_ndt_sql}) AND overall > 85 ORDER BY RANDOM() LIMIT 1", NON_DROPPABLE_TYPES)

        card = cursor.fetchone()

        if card and not is_duplicate_card(user_id, card[0]):
            high_overall_cards.append(card)

    icon_card_id, icon_name, icon_rarity, icon_card_type, icon_attack, icon_defense, icon_speed, icon_overall, icon_league, icon_nation, icon_image_path = icon_card

    cursor.execute('UPDATE cards SET copies = copies + 1 WHERE card_id = ?', (icon_card_id,))
    conn.commit()
    add_card_to_inventory(user_id, icon_card_id)

    for card in high_overall_cards:
        card_id, name, rarity, card_type, attack, defense, speed, overall, league, nation, image_path = card
        cursor.execute('UPDATE cards SET copies = copies + 1 WHERE card_id = ?', (card_id,))
        conn.commit()
        add_card_to_inventory(user_id, card_id)

    conn.close()

    embed = discord.Embed(title="You have received a Tester Pack!", description=f"**Icon Card: {icon_name}**")
    embed.set_image(url=f"attachment://{icon_image_path.split('/')[-1]}")
    embed.add_field(name="Rarity", value=icon_rarity, inline=True)
    embed.add_field(name="Type", value=icon_card_type, inline=True)
    embed.add_field(name="Attack", value=icon_attack, inline=True)
    embed.add_field(name="Defense", value=icon_defense, inline=True)
    embed.add_field(name="Speed", value=icon_speed, inline=True)
    embed.add_field(name="Overall", value=icon_overall, inline=True)
    embed.add_field(name="League", value=icon_league, inline=True)
    embed.add_field(name="Nation", value=icon_nation, inline=True)
    embed.add_field(name="Copies", value=1, inline=True)

    card_names = [f"{card[0]}: {card[1]}" for card in high_overall_cards]
    await ctx.send(embed=embed, file=discord.File(icon_image_path, filename=icon_image_path.split('/')[-1]))
    await ctx.send(f"Other cards obtained: {', '.join(card_names)}")

    return "5 Cards"


#---------------------------------DECKS-----

@bot.hybrid_command(name='decks', description="View list of your decks")
async def view_decks(ctx, user: discord.User = None):
    if user is None:
        user = ctx.author

    ensure_player_exists(user.id, user.name)
    cursor.execute('SELECT deck_name, cards FROM decks WHERE user_id = ?', (user.id,))
    decks = cursor.fetchall()

    if not decks:
        await ctx.send(f"{user.name} has no decks.")
        return

    embed = discord.Embed(title=f"{user.name}'s Decks")
    for deck_name, cards in decks:
        card_ids = cards.split(',')
        card_details = []
        for card_id in card_ids:
            cursor.execute('SELECT name FROM cards WHERE card_id = ?', (card_id,))
            card_data = cursor.fetchone()
            if card_data:
                card_details.append(card_data[0])
        embed.add_field(name=deck_name, value=', '.join(card_details), inline=False)

    await ctx.send(embed=embed)


@bot.hybrid_command(name='create_deck', description="Create a battle deck (Requires 5 Card IDs)")
async def create_deck(ctx, deck_name: str, card1: int, card2: int, card3: int, card4: int, card5: int):
    card_ids = [card1, card2, card3, card4, card5]

    if len(card_ids) != 5:
        await ctx.send("A deck must contain exactly 5 cards.")
        return

    ensure_player_exists(ctx.author.id, ctx.author.name)

    # --- SECURITY CHECK: OWNERSHIP ---
    # We loop through every card ID to make sure the user actually owns it
    for card_id in card_ids:
        cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (ctx.author.id, card_id))
        if cursor.fetchone() is None:
            await ctx.send(f"⛔ You cannot create this deck because you do not own the card with ID **{card_id}**.")
            return

    try:
        # If they own all cards, we proceed to create the deck
        add_deck(ctx.author.id, deck_name, card_ids)
        await ctx.send(f"✅ Deck '**{deck_name}**' created successfully!")
    except ValueError as e:
        await ctx.send(f"❌ Error: {str(e)}")


@bot.hybrid_command(name='edit_deck', description="Edit an existing deck (Requires 5 Card IDs)")
async def edit_deck(ctx, deck_name: str, card1: int, card2: int, card3: int, card4: int, card5: int):
    card_ids = [card1, card2, card3, card4, card5]
    
    if len(card_ids) != 5:
        await ctx.send("A deck must contain exactly 5 cards.")
        return

    ensure_player_exists(ctx.author.id, ctx.author.name)

    # 1. Check if deck exists
    cursor.execute('SELECT deck_name FROM decks WHERE user_id = ? AND deck_name = ?', (ctx.author.id, deck_name))
    if cursor.fetchone() is None:
        await ctx.send(f"❌ No deck found with the name '**{deck_name}**'.")
        return

    # 2. Check Ownership & 3. Check for Duplicate Players
    player_ids_in_deck = set()

    for card_id in card_ids:
        # Check Ownership
        cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (ctx.author.id, card_id))
        if cursor.fetchone() is None:
            await ctx.send(f"⛔ You cannot use ID **{card_id}** because you do not own it.")
            return

        # Check for Duplicate Player IDs (e.g. 2 different cards of the same player)
        cursor.execute('SELECT player_id, name FROM cards WHERE card_id = ?', (card_id,))
        card_data = cursor.fetchone()
        if card_data:
            player_id, player_name = card_data
            if player_id in player_ids_in_deck:
                 await ctx.send(f"⛔ Invalid Deck: You cannot have **{player_name}** twice in the same deck!")
                 return
            player_ids_in_deck.add(player_id)

    # 4. Save Changes
    try:
        cards_str = ','.join(map(str, card_ids))
        cursor.execute('UPDATE decks SET cards = ? WHERE user_id = ? AND deck_name = ?', (cards_str, ctx.author.id, deck_name))
        conn.commit()
        await ctx.send(f"✅ Deck '**{deck_name}**' updated successfully!")
    except Exception as e:
        await ctx.send(f"❌ Error updating deck: {e}")





#---------------------DECK VIEWER------------------------

def generate_lineup_image(deck_cards):
    # 1. Load Background
    try:
        bg = Image.open("pitch.png").convert("RGBA")
    except FileNotFoundError:
        bg = Image.new('RGBA', (1080, 1350), (0, 128, 0, 255))

    # --- FORCE RESIZE: Make the background HD (1080x1350) ---
    # This ensures the output is always big, even if pitch.png is small
    bg = bg.resize((1080, 1350), Image.Resampling.LANCZOS)
    bg_width, bg_height = bg.size

    # ---------------- SORTING LOGIC ----------------
    pool = deck_cards[:]
    
    # Sort for 2-1-2 Formation
    pool.sort(key=lambda x: x.attack, reverse=True)
    attackers = pool[:2]
    for card in attackers: pool.remove(card)

    pool.sort(key=lambda x: x.defense, reverse=True)
    defenders = pool[:2]
    for card in defenders: pool.remove(card)

    midfielder = pool[0]
    sorted_lineup = [attackers[0], attackers[1], midfielder, defenders[0], defenders[1]]

    # ---------------- POSITIONING (2-1-2) ----------------
    positions = [
        (0.28, 0.20), # Attacker Left 
        (0.72, 0.20), # Attacker Right 
        (0.50, 0.50), # Midfielder 
        (0.28, 0.80), # Defender Left 
        (0.72, 0.80)  # Defender Right 
    ]

    # ---------------- DRAWING ----------------
    for i, card in enumerate(sorted_lineup):
        try:
            card_img = Image.open(card.image_path).convert("RGBA")
            
            # Card size relative to the NEW huge background (35% width)
            target_width = int(bg_width * 0.35) 
            aspect_ratio = card_img.height / card_img.width
            target_height = int(target_width * aspect_ratio)
            
            card_img = card_img.resize((target_width, target_height), Image.Resampling.LANCZOS)

            pos_x_percent, pos_y_percent = positions[i]
            x = int((bg_width * pos_x_percent) - (target_width / 2))
            y = int((bg_height * pos_y_percent) - (target_height / 2))

            bg.paste(card_img, (x, y), card_img)

        except Exception as e:
            print(f"Error loading image for card {card.name}: {e}")
            continue

    buffer = io.BytesIO()
    bg.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

@bot.hybrid_command(name='view_deck', description="Visualize a specific deck (Optional: @user to view theirs)")
async def view_deck(ctx, deck_name: str, user: discord.User = None):
    # 1. Determine who we are looking at
    target_user = user or ctx.author

    # 2. Ensure they exist in DB (just in case)
    ensure_player_exists(target_user.id, target_user.name)
    
    # 3. Get the deck using TARGET's ID
    deck_cards = get_deck(target_user.id, deck_name)
    
    if deck_cards is None:
        await ctx.send(f"❌ Deck '**{deck_name}**' not found for **{target_user.name}**.")
        return
    
    if len(deck_cards) != 5:
        await ctx.send("This deck does not have 5 cards, cannot generate lineup.")
        return

    # 4. Generate Image
    # Note: The generation logic is the same, it just processes the cards we found
    image_buffer = await bot.loop.run_in_executor(None, generate_lineup_image, deck_cards)
    file = discord.File(fp=image_buffer, filename=f"{deck_name}.png")

    # 5. Generate Text
    description_text = ""
    for card in deck_cards:
        description_text += (
            f"**{card.name}**\n"
            f"⭐ {card.overall} | ⚔️ {card.attack} | 🛡️ {card.defense} | ⚡ {card.speed}\n\n"
        )

    # 6. Create Embed
    embed = discord.Embed(
        title=f"📋 Deck Details: {deck_name}", 
        description=description_text, 
        color=discord.Color.green()
    )
    # FIX: Use target_user details for the footer
    embed.set_footer(text=f"Owner: {target_user.name}", icon_url=target_user.display_avatar.url)

    # 7. Send
    await ctx.send(file=file, embed=embed)



# ---------------------------------------------------------VISUAL DECK BUILDER-------------------------------------------------------------------------------------

class DeckBuilderSelect(discord.ui.Select):
    def __init__(self, page_cards, selected_ids):
        options = []
        for i, card in enumerate(page_cards):
            # Check if this card is currently selected in the draft
            is_selected = card.card_id in selected_ids
            
            # Visual feedback: Add checkmark if selected
            label = f"{'✅ ' if is_selected else ''}{card.name}"
            desc = f"OVR: {card.overall} | {card.card_type}"
            
            options.append(discord.SelectOption(
                label=label, 
                description=desc, 
                value=f"{card.card_id}-{i}",
                emoji="⚽"
            ))

        super().__init__(
            placeholder="Select cards to add/remove...",
            min_values=1,
            max_values=1, # We handle one click at a time to keep logic simple across pages
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        card_id = int(self.values[0].split('-')[0])
        
        # Toggle Logic
        if card_id in view.selected_ids:
            view.selected_ids.remove(card_id)
            action = "removed"
        else:
            if len(view.selected_ids) >= 5:
                return await interaction.response.send_message("⛔ Your deck is full (5/5). Remove a card first.", ephemeral=True)
            view.selected_ids.append(card_id)
            action = "added"

        await view.update_display(interaction)

class DeckBuilderView(discord.ui.View):
    def __init__(self, ctx, inventory, deck_name):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.inventory = inventory
        self.deck_name = deck_name
        
        self.current_page = 0
        self.items_per_page = 20 # Discord dropdown max is 25
        self.total_pages = max(1, (len(inventory) - 1) // self.items_per_page + 1)
        
        self.selected_ids = [] # List of Card IDs
        
        # Initial Render
        self.update_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("⛔ This is not your deck builder.", ephemeral=True)
            return False
        return True

    def get_page_items(self):
        start = self.current_page * self.items_per_page
        end = start + self.items_per_page
        return self.inventory[start:end]

    def update_components(self):
        self.clear_items()
        
        # 1. Add Dropdown for current page
        page_cards = self.get_page_items()
        if page_cards:
            self.add_item(DeckBuilderSelect(page_cards, self.selected_ids))

        # 2. Add Navigation Buttons
        if self.current_page > 0:
            self.add_item(BuilderPrevButton())
        if self.current_page < self.total_pages - 1:
            self.add_item(BuilderNextButton())

        # 3. Add Save/Cancel Buttons
        self.add_item(BuilderSaveButton(disabled=(len(self.selected_ids) != 5)))
        self.add_item(BuilderCancelButton())

    async def update_display(self, interaction):
        self.update_components()
        
        # Build the Status Embed
        embed = discord.Embed(title=f"🛠️ Deck Builder: {self.deck_name}", color=discord.Color.blue())
        
        # List Selected Cards
        if self.selected_ids:
            # We need to find the card names for the IDs
            selected_names = []
            for cid in self.selected_ids:
                # Find card object in inventory list
                card_obj = next((c for c in self.inventory if c.card_id == cid), None)
                if card_obj:
                    selected_names.append(f"• **{card_obj.name}** ({card_obj.overall})")
            
            card_list_str = "\n".join(selected_names)
        else:
            card_list_str = "*No cards selected*"

        embed.add_field(name=f"Current Lineup ({len(self.selected_ids)}/5)", value=card_list_str, inline=False)
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} | Select a card in the dropdown to Add/Remove it.")

        if interaction.response.is_done():
            await self.message.edit(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

# --- BUTTONS ---

class BuilderPrevButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Previous", style=discord.ButtonStyle.primary, row=1)
    async def callback(self, interaction):
        self.view.current_page -= 1
        await self.view.update_display(interaction)

class BuilderNextButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Next", style=discord.ButtonStyle.primary, row=1)
    async def callback(self, interaction):
        self.view.current_page += 1
        await self.view.update_display(interaction)

class BuilderSaveButton(discord.ui.Button):
    def __init__(self, disabled=True):
        style = discord.ButtonStyle.grey if disabled else discord.ButtonStyle.green
        label = "Wait..." if disabled else "Save Deck"
        emoji = "⏳" if disabled else "💾"
        super().__init__(label=label, style=style, emoji=emoji, disabled=disabled, row=2)

    async def callback(self, interaction):
        view = self.view
        
        # Final Safety Check
        if len(view.selected_ids) != 5:
            return await interaction.response.send_message("You need exactly 5 cards!", ephemeral=True)

        # Logic to Save to DB
        try:
            # We use the existing logic inside add_deck, but we call it safely here
            # Since view.selected_ids is a list of INTs, we are good.
            
            # Note: add_deck helper might raise errors if deck name exists
            # We should probably check if deck exists first or handle the error
            add_deck(interaction.user.id, view.deck_name, view.selected_ids)
            
            embed = discord.Embed(title="✅ Deck Saved!", description=f"Deck **{view.deck_name}** has been created successfully.", color=discord.Color.green())
            await interaction.response.edit_message(embed=embed, view=None)
            view.stop()
            
        except ValueError as e:
            # If deck name exists, maybe we update it? 
            # For now, let's just show the error.
            if "already exists" in str(e):
                await interaction.response.send_message(f"⚠️ A deck named '{view.deck_name}' already exists. Use `/edit_deck` or choose a different name.", ephemeral=True)
            else:
                await interaction.response.send_message(f"Error: {e}", ephemeral=True)

class BuilderCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cancel", style=discord.ButtonStyle.danger, row=2)
    async def callback(self, interaction):
        await interaction.response.edit_message(content="❌ Deck building cancelled.", embed=None, view=None)
        self.view.stop()

# --- COMMAND ---

@bot.hybrid_command(name='build_deck', description="Interactively build a deck")
async def build_deck(ctx, deck_name: str):
    ensure_player_exists(ctx.author.id, ctx.author.name)
    
    # 1. Check if deck name already exists to save time
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM decks WHERE user_id = ? AND deck_name = ?', (ctx.author.id, deck_name))
    if cursor.fetchone():
        conn.close()
        return await ctx.send(f"❌ You already have a deck named **{deck_name}**. Please choose a different name.")
    conn.close()

    # 2. Get Inventory
    inventory, editions = get_player_inventory(ctx.author.id)
    if not inventory:
        return await ctx.send("You have no cards to build a deck with!")

    # 3. Start UI
    view = DeckBuilderView(ctx, inventory, deck_name)
    
    embed = discord.Embed(title=f"🛠️ Deck Builder: {deck_name}", description="Loading inventory...", color=discord.Color.blue())
    msg = await ctx.send(embed=embed, view=view)
    view.message = msg
    
    # Trigger first render
    await view.update_display(ctx.interaction if ctx.interaction else None) 
    # Note: For text commands, update_display might need a small tweak or just rely on initial init.
    # Actually, let's just let the user click. The initial init handles components. 
    # We just need to set the initial embed content correctly.
    
    # Re-do initial embed properly
    embed = discord.Embed(title=f"🛠️ Deck Builder: {deck_name}", color=discord.Color.blue())
    embed.add_field(name="Current Lineup (0/5)", value="*No cards selected*", inline=False)
    embed.set_footer(text=f"Page 1/{view.total_pages} | Select a card in the dropdown to Add/Remove it.")
    await msg.edit(embed=embed, view=view)




#-----------------------------------ECONOMY-----------------------------------------

@bot.hybrid_command(name='coins', description="Check your coin balance")
async def coins(ctx, user: discord.User = None):
    if user is None:
        user = ctx.author

    ensure_player_exists(user.id, user.name)
    cursor.execute('SELECT coins FROM players WHERE user_id = ?', (user.id,))
    coins = cursor.fetchone()[0]

    embed = discord.Embed(title=f"{user.name}'s Coins", description=f'''{user.mention} has {coins} coins.
    Earn more coins by selling cards or battling other players''', color=discord.Color.gold())
    await ctx.send(embed=embed)

def add_winner_coins(user_id):
    cursor.execute('UPDATE players SET coins = coins + 200 WHERE user_id = ?', (user_id,))
    conn.commit()

def add_loser_coins(user_id):
    cursor.execute('UPDATE players SET coins = coins + 100 WHERE user_id = ?', (user_id,))
    conn.commit()


#-----------------------------------CATALOG VIEWER----------------------------------

class CatalogView(discord.ui.View):
    def __init__(self, cards, ctx):
        super().__init__(timeout=120)
        self.full_data = [(card, None) for card in cards]
        self.data = self.full_data[:] 
        self.ctx = ctx
        self.current_page = 0
        
        self.sort_label = "Overall"
        self.filter_name = None
        self.filter_rating = None
        self.filter_rarity = None
        self.filter_type = None
        
        self.total_pages = max(1, (len(self.data) - 1) // 10 + 1)
        self.data.sort(key=lambda x: x[0].overall, reverse=True)
        
        self.add_item(SortSelect()) 
        self.update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("⛔ You cannot control this menu. Run `/catalog` yourself!", ephemeral=True)
            return False
        return True

    def apply_filters(self):
        filtered = self.full_data[:]
        if self.filter_name:
            filtered = [x for x in filtered if self.filter_name in x[0].name.lower()]
        if self.filter_rating:
            filtered = [x for x in filtered if x[0].overall >= self.filter_rating]
        if self.filter_rarity:
            filtered = [x for x in filtered if self.filter_rarity in x[0].card_rarity.lower()]
        if self.filter_type:
            filtered = [x for x in filtered if self.filter_type in x[0].card_type.lower()]

        self.data = filtered
        self.current_page = 0 
        self.total_pages = max(1, (len(self.data) - 1) // 10 + 1)

    def update_view(self):
        start = self.current_page * 10
        end = start + 10
        page_items = self.data[start:end]

        card_descriptions = []
        for card, _ in page_items:
            # --- FORMAT CHANGE: Wishlists (Emoji) replaces Edition ---
            line = (
                f"**{card.name} (ID: {card.card_id})** - "
                f"❤️ {card.wishlist_count}, "
                f"Overall: {card.overall}, "
                f"Attack: {card.attack}, "
                f"Defense: {card.defense}, "
                f"Speed: {card.speed}, "
                f"Total Copies: {card.copies}, "
                f"Type: {card.card_type}"
            )
            card_descriptions.append(line)

        description = '\n'.join(card_descriptions) if card_descriptions else "No cards found matching your filters."

        status = [f"Sort: {self.sort_label}"]
        if self.filter_name: status.append(f"Name: {self.filter_name}")
        if self.filter_rating: status.append(f"OVR>={self.filter_rating}")
        if self.filter_rarity: status.append(f"Rarity: {self.filter_rarity}")
        if self.filter_type: status.append(f"Type: {self.filter_type}")
        
        embed = discord.Embed(
            title=f"📖 Card Catalog ({' | '.join(status)})", 
            description=description, 
            color=discord.Color.gold()
        )
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} | Showing {len(self.data)} of {len(self.full_data)} Cards")
        return embed

    def update_buttons(self):
        while len(self.children) > 1:
            self.remove_item(self.children[1])

        self.add_item(FilterButton())
        
        is_filtered = (len(self.data) != len(self.full_data))
        if is_filtered:
            self.add_item(ResetFilterButton())

        if self.current_page > 0:
            self.add_item(PreviousButton())
        
        if self.current_page < self.total_pages - 1:
            self.add_item(NextButton())

@bot.hybrid_command(name='catalog', description="View every card available in the game")
async def catalog(ctx, *, search: str = None):
    # 1. Fetch all cards (exclude non-droppable types like Unique)
    all_cards = [c for c in fetch_all_cards() if c.card_type not in NON_DROPPABLE_TYPES]

    if not all_cards:
        return await ctx.send("The game database appears to be empty.")

    view = CatalogView(all_cards, ctx)

    # 2. Apply Search if provided (e.g. /catalog messi)
    if search:
        search = search.lower()
        view.filter_name = search
        # Apply Logic
        view.apply_filters()
        
        # Force Sort to Overall
        view.data.sort(key=lambda x: x[0].overall, reverse=True)
        view.sort_label = "Overall"
        
        if not view.data:
            return await ctx.send(f"No cards found matching '{search}' in the catalog.")

    embed = view.update_view()
    view.update_buttons() # Ensure Reset button appears if filtered
    view.message = await ctx.send(embed=embed, view=view)


#-----------------------------------WISHLIST----------------------------------


class WishlistView(discord.ui.View):
    def __init__(self, data, target_user, ctx):
        super().__init__(timeout=120)
        self.data = data
        self.target_user = target_user
        self.ctx = ctx
        self.current_page = 0
        self.total_pages = max(1, (len(self.data) - 1) // 10 + 1)
        self.update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("⛔ You cannot control this menu.", ephemeral=True)
            return False
        return True

    def update_view(self):
        start = self.current_page * 10
        end = start + 10
        page_items = self.data[start:end]

        descriptions = []
        for card in page_items:
            # Row Format: Name (ID) | Type | Overall
            # row[0]=Name, row[1]=ID, row[2]=Overall, row[3]=Type (Swapped from Rarity)
            line = f"**{card[0]}** (ID: {card[1]}) | {card[3]} | ⭐ {card[2]}"
            descriptions.append(line)

        description = '\n'.join(descriptions) if descriptions else "List is empty."

        embed = discord.Embed(
            title=f"❤️ {self.target_user.name}'s Wishlist", 
            description=description, 
            color=discord.Color.magenta()
        )
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} | Total: {len(self.data)} Cards")
        return embed

    def update_buttons(self):
        self.clear_items()
        if self.current_page > 0:
            self.add_item(PreviousButton())
        
        if self.current_page < self.total_pages - 1:
            self.add_item(NextButton())


@bot.hybrid_command(name='wishlists', description="View a player's wishlist")
async def wishlists(ctx, user: discord.User = None):
    target_user = user or ctx.author
    ensure_player_exists(target_user.id, target_user.name)

    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    
    # --- FIX: Changed c.card_rarity to c.card_type ---
    cursor.execute('''
        SELECT c.name, c.card_id, c.overall, c.card_type 
        FROM wishlists w
        JOIN cards c ON w.card_id = c.card_id
        WHERE w.user_id = ?
        ORDER BY c.overall DESC
    ''', (target_user.id,))
    
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        msg = "You have no cards in your wishlist." if target_user == ctx.author else f"**{target_user.name}** has no cards in their wishlist."
        return await ctx.send(msg)

    view = WishlistView(rows, target_user, ctx)
    embed = view.update_view()
    await ctx.send(embed=embed, view=view)



@bot.hybrid_command(name='wishlist', aliases=['wl'], description="Add or remove a card from your wishlist")
async def wishlist(ctx, card_id: int):
    ensure_player_exists(ctx.author.id, ctx.author.name)
    
    card = get_card_by_id(card_id)
    if not card:
        return await ctx.send(f"❌ Card ID `{card_id}` not found.")

    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()

    try:
        # Check if already wishlisted
        cursor.execute('SELECT 1 FROM wishlists WHERE user_id = ? AND card_id = ?', (ctx.author.id, card_id))
        exists = cursor.fetchone()

        if exists:
            # REMOVE
            cursor.execute('DELETE FROM wishlists WHERE user_id = ? AND card_id = ?', (ctx.author.id, card_id))
            cursor.execute('UPDATE cards SET wishlist_count = MAX(0, wishlist_count - 1) WHERE card_id = ?', (card_id,))
            action_text = "removed from"
            emoji = "💔"
            color = discord.Color.red()
        else:
            # ADD
            cursor.execute('INSERT INTO wishlists (user_id, card_id) VALUES (?, ?)', (ctx.author.id, card_id))
            cursor.execute('UPDATE cards SET wishlist_count = wishlist_count + 1 WHERE card_id = ?', (card_id,))
            action_text = "added to"
            emoji = "❤️"
            color = discord.Color.magenta()

        conn.commit()
        
        # Get updated count
        cursor.execute('SELECT wishlist_count FROM cards WHERE card_id = ?', (card_id,))
        new_count = cursor.fetchone()[0]

        embed = discord.Embed(
            description=f"{emoji} **{card.name}** has been {action_text} your wishlist.\nGlobal Wishlists: **{new_count}**",
            color=color
        )
        
        # --- FIX: Changed set_thumbnail to set_image ---
        # This moves the image from the top-right corner to the bottom (Full Width)
        embed.set_image(url=f"attachment://{card.image_path.split('/')[-1]}")
        
        await ctx.send(embed=embed, file=discord.File(card.image_path))

    except Exception as e:
        logger.error(f"Wishlist Error: {e}")
        await ctx.send("An error occurred updating your wishlist.")
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# FRIENDS
# ═══════════════════════════════════════════════════════════════════════════════

def _are_friends(uid_a, uid_b):
    cursor.execute('SELECT 1 FROM friendships WHERE user_id=? AND friend_id=?', (uid_a, uid_b))
    return cursor.fetchone() is not None


def _add_friendship(uid_a, uid_b):
    cursor.execute('INSERT OR IGNORE INTO friendships (user_id, friend_id) VALUES (?, ?)', (uid_a, uid_b))
    cursor.execute('INSERT OR IGNORE INTO friendships (user_id, friend_id) VALUES (?, ?)', (uid_b, uid_a))
    conn.commit()


class FriendRequestView(discord.ui.View):
    """Accept/Decline buttons for a single friend request."""
    def __init__(self, request_id, requester_id, recipient_id, requester_name=''):
        super().__init__(timeout=600)
        self.request_id = request_id
        self.requester_id = requester_id
        self.recipient_id = recipient_id
        self.requester_name = requester_name

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.recipient_id:
            return await interaction.response.send_message("This request isn't for you.", ephemeral=True)
        # Make sure request still exists
        cursor.execute('SELECT 1 FROM friend_requests WHERE request_id=?', (self.request_id,))
        if not cursor.fetchone():
            return await interaction.response.edit_message(
                content="⚠️ This request no longer exists.", embed=None, view=None
            )
        _add_friendship(self.requester_id, self.recipient_id)
        cursor.execute('DELETE FROM friend_requests WHERE request_id=?', (self.request_id,))
        conn.commit()
        embed = discord.Embed(
            title="🤝 Friend Request Accepted",
            description=f"You are now friends with **{self.requester_name or self.requester_id}**.",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red, emoji="❌")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.recipient_id:
            return await interaction.response.send_message("This request isn't for you.", ephemeral=True)
        cursor.execute('DELETE FROM friend_requests WHERE request_id=?', (self.request_id,))
        conn.commit()
        embed = discord.Embed(
            title="❌ Friend Request Declined",
            description=f"Declined request from **{self.requester_name or self.requester_id}**.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()


class FriendsListView(discord.ui.View):
    """Shown alongside /friends — has a 'View Pending Requests' button if any exist."""
    def __init__(self, user, pending_count):
        super().__init__(timeout=120)
        self.user = user
        self.pending_count = pending_count
        if pending_count > 0:
            btn = discord.ui.Button(
                label=f"View {pending_count} Pending Request{'s' if pending_count != 1 else ''}",
                style=discord.ButtonStyle.primary,
                emoji="📬"
            )
            btn.callback = self._show_pending
            self.add_item(btn)

    async def _show_pending(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("Not your friends list.", ephemeral=True)
        cursor.execute('''
            SELECT fr.request_id, fr.requester_id, p.name, fr.requested_at
            FROM friend_requests fr
            LEFT JOIN players p ON fr.requester_id = p.user_id
            WHERE fr.recipient_id = ?
            ORDER BY fr.requested_at ASC
        ''', (self.user.id,))
        requests = cursor.fetchall()
        if not requests:
            return await interaction.response.send_message("No pending requests.", ephemeral=True)
        request_id, requester_id, name, requested_at = requests[0]
        embed = discord.Embed(
            title="📬 Pending Friend Request",
            description=f"**{name or requester_id}** wants to be your friend.",
            color=discord.Color.gold()
        )
        if len(requests) > 1:
            embed.set_footer(text=f"{len(requests) - 1} more pending after this — accept or decline to see the next.")
        view = FriendRequestView(request_id, requester_id, self.user.id, name or '')
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.hybrid_command(name='addfriend', description="Send a friend request to a player")
@app_commands.describe(user="The player you want to friend")
async def addfriend(ctx, user: discord.User):
    if user.id == ctx.author.id:
        return await ctx.send("❌ You can't friend yourself.")
    if user.bot:
        return await ctx.send("❌ You can't friend a bot.")

    ensure_player_exists(ctx.author.id, ctx.author.name)
    ensure_player_exists(user.id, user.name)

    if _are_friends(ctx.author.id, user.id):
        return await ctx.send(f"❌ You're already friends with **{user.name}**.")

    cursor.execute('SELECT 1 FROM friend_requests WHERE requester_id=? AND recipient_id=?',
                   (ctx.author.id, user.id))
    if cursor.fetchone():
        return await ctx.send(f"❌ You've already sent a request to **{user.name}**.")

    # If they already requested you, auto-accept
    cursor.execute('SELECT request_id FROM friend_requests WHERE requester_id=? AND recipient_id=?',
                   (user.id, ctx.author.id))
    incoming = cursor.fetchone()
    if incoming:
        _add_friendship(ctx.author.id, user.id)
        cursor.execute('DELETE FROM friend_requests WHERE request_id=?', (incoming[0],))
        conn.commit()
        return await ctx.send(f"🤝 **{user.name}** had already requested you — you're now friends!")

    cursor.execute('INSERT INTO friend_requests (requester_id, recipient_id) VALUES (?, ?)',
                   (ctx.author.id, user.id))
    conn.commit()
    await ctx.send(f"✅ Friend request sent to **{user.name}**. They'll be notified next time they use a command.")


@bot.hybrid_command(name='removefriend', description="Remove a player from your friends list")
@app_commands.describe(user="The friend to remove")
async def removefriend(ctx, user: discord.User):
    if not _are_friends(ctx.author.id, user.id):
        return await ctx.send(f"❌ You're not friends with **{user.name}**.")
    cursor.execute('DELETE FROM friendships WHERE user_id=? AND friend_id=?', (ctx.author.id, user.id))
    cursor.execute('DELETE FROM friendships WHERE user_id=? AND friend_id=?', (user.id, ctx.author.id))
    conn.commit()
    await ctx.send(f"✅ Removed **{user.name}** from your friends.")


@bot.hybrid_command(name='friends', description="View your friends list")
@app_commands.describe(user="View someone else's friends list (optional)")
async def friends_cmd(ctx, user: discord.User = None):
    target = user or ctx.author
    ensure_player_exists(target.id, target.name)

    cursor.execute('''
        SELECT f.friend_id, p.name
        FROM friendships f
        LEFT JOIN players p ON f.friend_id = p.user_id
        WHERE f.user_id = ?
        ORDER BY p.name
    ''', (target.id,))
    friend_rows = cursor.fetchall()

    pending_count = 0
    if target.id == ctx.author.id:
        cursor.execute('SELECT COUNT(*) FROM friend_requests WHERE recipient_id = ?', (target.id,))
        pending_count = cursor.fetchone()[0]

    embed = discord.Embed(
        title=f"👥 {target.name}'s Friends",
        color=discord.Color.blue()
    )
    if pending_count > 0:
        embed.description = f"📬 **You have {pending_count} pending friend request{'s' if pending_count != 1 else ''}!** Click the button below to view."

    if friend_rows:
        names = '\n'.join(f"• {fname or fid}" for fid, fname in friend_rows)
        embed.add_field(name=f"Friends ({len(friend_rows)})", value=names[:1024], inline=False)
    else:
        embed.add_field(name="Friends", value="No friends yet.", inline=False)

    view = FriendsListView(ctx.author, pending_count) if target.id == ctx.author.id else None
    await ctx.send(embed=embed, view=view)


# --- Notification hook: fires after every command (prefix + slash) ---
async def _send_pending_friend_notification(author, send_func):
    """Sends the oldest unnotified friend request to `author`, if any."""
    try:
        cursor.execute('''
            SELECT fr.request_id, fr.requester_id, p.name
            FROM friend_requests fr
            LEFT JOIN players p ON fr.requester_id = p.user_id
            WHERE fr.recipient_id = ? AND fr.notified = 0
            ORDER BY fr.requested_at ASC
            LIMIT 1
        ''', (author.id,))
        row = cursor.fetchone()
        if not row:
            return
        request_id, requester_id, name = row
        cursor.execute('UPDATE friend_requests SET notified = 1 WHERE request_id = ?', (request_id,))
        conn.commit()

        cursor.execute('SELECT COUNT(*) FROM friend_requests WHERE recipient_id = ? AND request_id != ?',
                       (author.id, request_id))
        more = cursor.fetchone()[0]

        embed = discord.Embed(
            title="📬 New Friend Request",
            description=f"**{name or requester_id}** has sent you a friend request!",
            color=discord.Color.gold()
        )
        if more > 0:
            embed.set_footer(text=f"You have {more} other pending request(s) — use /friends to view them.")
        view = FriendRequestView(request_id, requester_id, author.id, name or '')
        await send_func(content=author.mention, embed=embed, view=view)
    except Exception as e:
        logger.error(f"Friend notify error: {e}")


@bot.event
async def on_command_completion(ctx):
    """Fires for both prefix and slash (hybrid) command completions."""
    await _send_pending_friend_notification(ctx.author, ctx.send)


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE UP
# ═══════════════════════════════════════════════════════════════════════════════

class TradeUpSelect(discord.ui.Select):
    def __init__(self, eligible, tier):
        self.tier = tier
        self.rowid_map = {str(row[0]): row for row in eligible}
        options = [
            discord.SelectOption(
                label=f"{row[1][:50]} (#{row[3]})",
                description=f"⭐{row[2]} | {row[5]}",
                value=str(row[0])
            )
            for row in eligible[:25]
        ]
        super().__init__(placeholder="Select exactly 5 cards to sacrifice...",
                         min_values=5, max_values=5, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_cards = [self.rowid_map[v] for v in self.values]
        selected_rowids = [int(v) for v in self.values]
        card_list = '\n'.join(f"• {r[1]} ⭐{r[2]} (Edition #{r[3]})" for r in selected_cards)

        target_labels = {
            'common':   'a random **Uncommon**',
            'uncommon': 'a random **Rare**',
            'rare':     'a random **Hero or Icon** (≤ 93 OVR)',
        }
        embed = discord.Embed(
            title="🔄 Confirm Trade Up",
            description=f"Sacrifice these 5 cards for {target_labels[self.tier]}?",
            color=discord.Color.purple()
        )
        embed.add_field(name="Sacrificing", value=card_list, inline=False)
        embed.set_footer(text="This cannot be undone.")

        view = TradeUpConfirmView(interaction.user, selected_rowids, self.tier)
        await interaction.response.edit_message(embed=embed, view=view)


class TradeUpConfirmView(discord.ui.View):
    def __init__(self, user, rowids, tier):
        super().__init__(timeout=60)
        self.user = user
        self.rowids = rowids
        self.tier = tier

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn't yours.", ephemeral=True)

        conn = sqlite3.connect('cards_game.db')
        cursor = conn.cursor()
        try:
            # Verify all 5 cards are still owned
            for rowid in self.rowids:
                cursor.execute('SELECT 1 FROM inventories WHERE rowid = ? AND user_id = ?',
                               (rowid, self.user.id))
                if not cursor.fetchone():
                    conn.close()
                    return await interaction.response.send_message(
                        "❌ One or more cards are no longer in your inventory.", ephemeral=True)

            # Pick result card
            result_card_id = None
            if self.tier == 'common':
                cursor.execute(
                    "SELECT card_id FROM cards WHERE card_rarity = 'Uncommon' AND card_type != 'Unique' ORDER BY RANDOM() LIMIT 1")
                row = cursor.fetchone()
                if row:
                    result_card_id = row[0]

            elif self.tier == 'uncommon':
                # Two-step: pick category by weight, then random card from that category.
                # This gives exact probabilities regardless of how many cards are in each bucket.
                TRADEUP_RARE_CATEGORIES = [
                    ('std_mid', 55),   # Rare Standard 86-89 (~67%)
                    ('std_hi',  20),   # Rare Standard 90+   (~24%)
                    ('hero',     5),   # Hero                (~6%)
                    ('icon',     2),   # Icon                (~2.4%)
                ]
                chosen = random.choices(
                    [c[0] for c in TRADEUP_RARE_CATEGORIES],
                    weights=[c[1] for c in TRADEUP_RARE_CATEGORIES], k=1
                )[0]

                queries = {
                    'std_mid': ("SELECT card_id FROM cards WHERE card_type='Standard' AND card_rarity='Rare' AND overall BETWEEN 86 AND 89", []),
                    'std_hi':  ("SELECT card_id FROM cards WHERE card_type='Standard' AND card_rarity='Rare' AND overall >= 90", []),
                    'hero':    ("SELECT card_id FROM cards WHERE card_type='Hero'", []),
                    'icon':    ("SELECT card_id FROM cards WHERE card_type='Icon'", []),
                }
                sql, params = queries[chosen]
                cursor.execute(sql, params)
                pool = [r[0] for r in cursor.fetchall()]

                if pool:
                    result_card_id = random.choice(pool)
                else:
                    # Fallback: any Rare Standard 86+ if chosen bucket is empty
                    cursor.execute("SELECT card_id FROM cards WHERE card_type='Standard' AND card_rarity='Rare' AND overall >= 86 ORDER BY RANDOM() LIMIT 1")
                    row = cursor.fetchone()
                    if row:
                        result_card_id = row[0]

            else:  # rare → Hero/Icon ≤93
                cursor.execute(
                    "SELECT card_id FROM cards WHERE card_type IN ('Hero', 'Icon') AND overall <= 93 ORDER BY RANDOM() LIMIT 1")
                row = cursor.fetchone()
                if row:
                    result_card_id = row[0]

            if not result_card_id:
                conn.close()
                return await interaction.response.send_message(
                    "❌ No eligible reward cards found.", ephemeral=True)

            # Remove sacrificed cards
            for rowid in self.rowids:
                cursor.execute('DELETE FROM inventories WHERE rowid = ?', (rowid,))

            # Give result card (manual increment to stay in same transaction)
            cursor.execute('SELECT copies FROM cards WHERE card_id = ?', (result_card_id,))
            edition = cursor.fetchone()[0] + 1
            cursor.execute('UPDATE cards SET copies = copies + 1 WHERE card_id = ?', (result_card_id,))
            cursor.execute('INSERT INTO inventories (user_id, card_id, edition) VALUES (?, ?, ?)',
                           (self.user.id, result_card_id, edition))
            conn.commit()

            cursor.execute(
                'SELECT name, overall, card_rarity, card_type, attack, defense, speed, image_path FROM cards WHERE card_id = ?',
                (result_card_id,))
            name, overall, rarity, card_type, atk, def_, spd, image_path = cursor.fetchone()
        except Exception as e:
            conn.close()
            logger.error(f"Trade up error: {e}")
            return await interaction.response.send_message("❌ Trade up failed.", ephemeral=True)
        conn.close()

        filename = f"card_{result_card_id}.png"
        embed = discord.Embed(title=f"✨ Trade Up Complete! — {name}", color=discord.Color.purple())
        embed.set_author(name=f"Rewarded to {self.user.name}", icon_url=self.user.display_avatar.url)
        embed.add_field(name="Info", value=f"🆔 {result_card_id}\n💎 {rarity}\n🏆 {card_type}", inline=True)
        embed.add_field(name="Base Stats", value=f"⭐ **{overall}** | ⚔️ {atk} | 🛡️ {def_} | ⚡ {spd}", inline=True)
        embed.add_field(name="Your Copy", value=f"#️⃣ Edition #{edition}\n✅ Added to inventory", inline=True)
        embed.set_image(url=f"attachment://{filename}")
        await interaction.response.edit_message(
            embed=embed, attachments=[discord.File(image_path, filename=filename)], view=None
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("This isn't yours.", ephemeral=True)
        await interaction.response.edit_message(content="Trade up cancelled.", embed=None, view=None)
        self.stop()


@bot.hybrid_command(name='tradeup', description="Sacrifice 5 cards of one rarity for 1 of a higher rarity")
@app_commands.describe(tier="Which rarity tier to trade up from")
@app_commands.choices(tier=[
    app_commands.Choice(name="Common → Uncommon (5 Commons)", value="common"),
    app_commands.Choice(name="Uncommon → Rare (5 Uncommons)", value="uncommon"),
    app_commands.Choice(name="Rare Standard → Hero/Icon ≤93 OVR (5 Rare Standards)", value="rare"),
])
async def tradeup(ctx, tier: str):
    ensure_player_exists(ctx.author.id, ctx.author.name)

    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()

    if tier == 'common':
        cursor.execute('''
            SELECT i.rowid, c.name, c.overall, i.edition, c.card_rarity, c.card_type
            FROM inventories i JOIN cards c ON i.card_id = c.card_id
            WHERE i.user_id = ? AND c.card_rarity = 'Common'
            ORDER BY c.overall ASC
        ''', (ctx.author.id,))
    elif tier == 'uncommon':
        cursor.execute('''
            SELECT i.rowid, c.name, c.overall, i.edition, c.card_rarity, c.card_type
            FROM inventories i JOIN cards c ON i.card_id = c.card_id
            WHERE i.user_id = ? AND c.card_rarity = 'Uncommon'
            ORDER BY c.overall ASC
        ''', (ctx.author.id,))
    else:
        cursor.execute('''
            SELECT i.rowid, c.name, c.overall, i.edition, c.card_rarity, c.card_type
            FROM inventories i JOIN cards c ON i.card_id = c.card_id
            WHERE i.user_id = ? AND c.card_rarity = 'Rare' AND c.card_type = 'Standard'
            ORDER BY c.overall ASC
        ''', (ctx.author.id,))

    eligible = cursor.fetchall()
    conn.close()

    tier_names = {'common': 'Common', 'uncommon': 'Uncommon', 'rare': 'Rare Standard'}
    tier_rewards = {'common': 'Uncommon', 'uncommon': 'Rare', 'rare': 'Hero/Icon (≤ 93 OVR)'}

    if len(eligible) < 5:
        return await ctx.send(
            f"❌ You need at least **5 {tier_names[tier]}** cards to trade up. You have **{len(eligible)}**.")

    embed = discord.Embed(
        title="🔄 Trade Up",
        description=f"Select **5 {tier_names[tier]}** cards to sacrifice for a random **{tier_rewards[tier]}**.",
        color=discord.Color.purple()
    )
    embed.set_footer(text=f"You have {len(eligible)} eligible card(s) · Sorted lowest OVR first")

    view = discord.ui.View(timeout=60)
    view.add_item(TradeUpSelect(eligible, tier))
    await ctx.send(embed=embed, view=view)


# ═══════════════════════════════════════════════════════════════════════════════
# TRANSFER MARKET
# ═══════════════════════════════════════════════════════════════════════════════

MARKET_DURATIONS = {'1h': 1, '3h': 3, '6h': 6, '12h': 12, '24h': 24, '48h': 48}

def _market_time_left(expires_at_str):
    try:
        expires = datetime.strptime(expires_at_str, '%Y-%m-%d %H:%M:%S')
        delta = expires - datetime.utcnow()
        if delta.total_seconds() <= 0:
            return "Expired"
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m = rem // 60
        return f"{h}h {m}m" if h else f"{m}m"
    except:
        return "?"


class MarketDurationSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=label, value=label) for label in MARKET_DURATIONS]
        super().__init__(placeholder="Select listing duration...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.seller.id:
            return await interaction.response.send_message("This isn't your listing.", ephemeral=True)
        hours = MARKET_DURATIONS[self.values[0]]
        self.view.expires_at = (datetime.utcnow() + timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')
        self.view.duration_label = self.values[0]
        for item in self.view.children:
            if isinstance(item, MarketConfirmButton):
                item.disabled = False
        embed = discord.Embed(title="📋 Confirm Listing", color=discord.Color.gold())
        embed.add_field(name="Card", value=f"**{self.view.card.name}** — Edition #{self.view.edition}", inline=True)
        embed.add_field(name="Price", value=f"💰 {self.view.price:,} coins", inline=True)
        embed.add_field(name="Duration", value=f"⏱ {self.values[0]}", inline=True)
        embed.set_footer(text="Card is held in escrow until sold or expired.")
        await interaction.response.edit_message(embed=embed, view=self.view)


class MarketConfirmButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Confirm Listing", style=discord.ButtonStyle.green, disabled=True)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.seller.id:
            return await interaction.response.send_message("This isn't your listing.", ephemeral=True)
        conn = sqlite3.connect('cards_game.db')
        cursor = conn.cursor()
        try:
            cursor.execute(
                'SELECT rowid FROM inventories WHERE user_id = ? AND card_id = ? AND edition = ? LIMIT 1',
                (self.view.seller.id, self.view.card.card_id, self.view.edition)
            )
            row = cursor.fetchone()
            if not row:
                conn.close()
                return await interaction.response.send_message("❌ You no longer own this copy.", ephemeral=True)
            cursor.execute("SELECT 1 FROM market_listings WHERE seller_id = ? AND card_id = ? AND edition = ? AND status = 'active'",
                           (self.view.seller.id, self.view.card.card_id, self.view.edition))
            if cursor.fetchone():
                conn.close()
                return await interaction.response.send_message("❌ This edition is already listed.", ephemeral=True)
            cursor.execute('DELETE FROM inventories WHERE rowid = ?', (row[0],))
            cursor.execute(
                'INSERT INTO market_listings (seller_id, card_id, edition, trade_count, price, listed_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (self.view.seller.id, self.view.card.card_id, self.view.edition,
                 self.view.trade_count, self.view.price,
                 datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), self.view.expires_at)
            )
            conn.commit()
        except Exception as e:
            conn.close()
            logger.error(f"Market list error: {e}")
            return await interaction.response.send_message("❌ Failed to create listing.", ephemeral=True)
        conn.close()
        embed = discord.Embed(
            title="✅ Card Listed!",
            description=f"**{self.view.card.name}** (Edition #{self.view.edition}) listed for **{self.view.price:,} coins** for {self.view.duration_label}.",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.view.stop()


class MarketCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cancel", style=discord.ButtonStyle.red)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.seller.id:
            return await interaction.response.send_message("This isn't your listing.", ephemeral=True)
        await interaction.response.edit_message(content="Listing cancelled.", embed=None, view=None)
        self.view.stop()


class MarketListingView(discord.ui.View):
    def __init__(self, seller, card, edition, trade_count, price):
        super().__init__(timeout=120)
        self.seller = seller
        self.card = card
        self.edition = edition
        self.trade_count = trade_count
        self.price = price
        self.expires_at = None
        self.duration_label = None
        self.add_item(MarketDurationSelect())
        self.add_item(MarketConfirmButton())
        self.add_item(MarketCancelButton())


class MarketEditionSelect(discord.ui.Select):
    """Shown when user owns multiple copies — lets them pick which one to list."""
    def __init__(self, copies, card, price, seller):
        self.card = card
        self.price = price
        self.seller = seller
        options = [
            discord.SelectOption(
                label=f"Edition #{row[0]}",
                description="First Owner" if row[1] == 0 else "Traded In",
                value=str(row[2])
            )
            for row in copies  # (edition, trade_count, rowid)
        ]
        super().__init__(placeholder="Select which copy to list...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.seller.id:
            return await interaction.response.send_message("This isn't yours.", ephemeral=True)
        selected_rowid = int(self.values[0])
        conn = sqlite3.connect('cards_game.db')
        cursor = conn.cursor()
        cursor.execute('SELECT edition, trade_count FROM inventories WHERE rowid = ?', (selected_rowid,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return await interaction.response.send_message("❌ Copy not found.", ephemeral=True)
        edition, trade_count = row
        view = MarketListingView(self.seller, self.card, edition, trade_count, self.price)
        embed = discord.Embed(
            title=f"📋 List {self.card.name} — Edition #{edition}",
            description=f"Price: **{self.price:,} coins**\nSelect a duration below.",
            color=discord.Color.gold()
        )
        await interaction.response.edit_message(embed=embed, view=view)


@bot.hybrid_command(name='list', description="List a card on the transfer market")
@app_commands.describe(card="Card to list", price="Selling price in coins")
@app_commands.autocomplete(card=card_search_autocomplete)
async def list_card(ctx, card: str, price: int):
    ensure_player_exists(ctx.author.id, ctx.author.name)

    card_obj = get_card_by_id(int(card)) if card.isdigit() else get_card_by_name(card)
    if not card_obj:
        return await ctx.send("❌ Card not found.")

    min_price = calculate_card_value(card_obj)
    if price < min_price:
        return await ctx.send(f"❌ Minimum listing price for **{card_obj.name}** is **{min_price:,} coins**.")

    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute(
        'SELECT edition, trade_count, rowid FROM inventories WHERE user_id = ? AND card_id = ? ORDER BY edition',
        (ctx.author.id, card_obj.card_id)
    )
    copies = cursor.fetchall()
    conn.close()

    if not copies:
        return await ctx.send(f"❌ You don't own **{card_obj.name}**.")

    if len(copies) == 1:
        edition, trade_count, _ = copies[0]
        view = MarketListingView(ctx.author, card_obj, edition, trade_count, price)
        embed = discord.Embed(
            title=f"📋 List {card_obj.name} — Edition #{edition}",
            description=f"Price: **{price:,} coins**\nSelect a duration below.",
            color=discord.Color.gold()
        )
        await ctx.send(embed=embed, view=view)
    else:
        view = discord.ui.View(timeout=60)
        view.add_item(MarketEditionSelect(copies, card_obj, price, ctx.author))
        embed = discord.Embed(
            title=f"📋 List {card_obj.name}",
            description=f"You own **{len(copies)} copies**. Select which one to list for **{price:,} coins**.",
            color=discord.Color.gold()
        )
        await ctx.send(embed=embed, view=view)


# ── Market Browse ──────────────────────────────────────────────────────────────

MARKET_PAGE_SIZE = 5

def _fetch_listings(search='', page=0):
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    query = '''
        SELECT ml.listing_id, ml.seller_id, ml.card_id, ml.edition,
               ml.price, ml.expires_at,
               c.name, c.overall, c.card_rarity,
               p.name AS seller_name
        FROM market_listings ml
        JOIN cards c  ON ml.card_id  = c.card_id
        JOIN players p ON ml.seller_id = p.user_id
        WHERE ml.status = 'active'
    '''
    params = []
    if search:
        query += ' AND c.name LIKE ?'
        params.append(f'%{search}%')
    query += ' ORDER BY ml.listed_at DESC'
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    total = len(rows)
    start = page * MARKET_PAGE_SIZE
    return rows[start:start + MARKET_PAGE_SIZE], total


class MarketBuyButton(discord.ui.Button):
    def __init__(self, listing_id, card_name, price, label_num):
        super().__init__(label=f"Buy #{label_num}", style=discord.ButtonStyle.green)
        self.listing_id = listing_id
        self.card_name = card_name
        self.price = price

    async def callback(self, interaction: discord.Interaction):
        conn = sqlite3.connect('cards_game.db')
        cursor = conn.cursor()
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute(
            "SELECT seller_id, card_id, edition, trade_count, price FROM market_listings WHERE listing_id = ? AND status = 'active'",
            (self.listing_id,)
        )
        listing = cursor.fetchone()
        if not listing:
            conn.close()
            return await interaction.response.send_message("❌ This listing is no longer available.", ephemeral=True)
        seller_id, card_id, edition, trade_count, price = listing
        if interaction.user.id == seller_id:
            conn.close()
            return await interaction.response.send_message("❌ You can't buy your own listing.", ephemeral=True)
        cursor.execute('SELECT coins FROM players WHERE user_id = ?', (interaction.user.id,))
        buyer_coins = cursor.fetchone()[0]
        if buyer_coins < price:
            conn.close()
            return await interaction.response.send_message(
                f"❌ You need **{price:,}** coins but only have **{buyer_coins:,}**.", ephemeral=True
            )
        try:
            cursor.execute('UPDATE players SET coins = coins - ? WHERE user_id = ?', (price, interaction.user.id))
            cursor.execute('UPDATE players SET coins = coins + ? WHERE user_id = ?', (price, seller_id))
            cursor.execute(
                'INSERT INTO inventories (user_id, card_id, edition, trade_count) VALUES (?, ?, ?, ?)',
                (interaction.user.id, card_id, edition, trade_count + 1)
            )
            cursor.execute(
                "UPDATE market_listings SET status='sold', resolved_at=?, buyer_id=? WHERE listing_id=?",
                (datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), interaction.user.id, self.listing_id)
            )
            conn.commit()
        except Exception as e:
            conn.close()
            logger.error(f"Market buy error: {e}")
            return await interaction.response.send_message("❌ Purchase failed.", ephemeral=True)
        conn.close()
        await interaction.response.send_message(
            f"✅ You bought **{self.card_name}** (Edition #{edition}) for **{price:,} coins**!", ephemeral=True
        )
        await self.view.refresh(interaction)


class MarketBrowseView(discord.ui.View):
    def __init__(self, ctx, search=''):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.search = search
        self.page = 0
        self.message = None

    def build(self):
        self.clear_items()
        rows, total = _fetch_listings(self.search, self.page)
        total_pages = max(1, (total + MARKET_PAGE_SIZE - 1) // MARKET_PAGE_SIZE)

        embed = discord.Embed(title="🏪 Transfer Market", color=discord.Color.gold())
        if self.search:
            embed.description = f"Search: **{self.search}**"

        if not rows:
            embed.description = "No listings found."
        else:
            for i, row in enumerate(rows, 1):
                listing_id, seller_id, card_id, edition, price, expires_at, card_name, overall, rarity, seller_name = row
                time_left = _market_time_left(expires_at)
                embed.add_field(
                    name=f"#{i}  {card_name}  ⭐{overall}",
                    value=f"💰 **{price:,}** | 💎 {rarity} | Edition #{edition} | 🕒 {time_left}\n👤 {seller_name}",
                    inline=False
                )
                self.add_item(MarketBuyButton(listing_id, card_name, price, i))

        embed.set_footer(text=f"Page {self.page + 1}/{total_pages} · {total} listing(s)")

        if self.page > 0:
            prev_btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary)
            async def prev_cb(interaction):
                self.page -= 1
                await self.refresh(interaction)
            prev_btn.callback = prev_cb
            self.add_item(prev_btn)

        if (self.page + 1) * MARKET_PAGE_SIZE < total:
            next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def next_cb(interaction):
                self.page += 1
                await self.refresh(interaction)
            next_btn.callback = next_cb
            self.add_item(next_btn)

        return embed

    async def refresh(self, interaction):
        embed = self.build()
        if interaction.response.is_done():
            await self.message.edit(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)


@bot.hybrid_command(name='market', description="Browse the transfer market")
@app_commands.describe(search="Search by card name")
async def market(ctx, *, search: str = ''):
    ensure_player_exists(ctx.author.id, ctx.author.name)
    view = MarketBrowseView(ctx, search)
    embed = view.build()
    msg = await ctx.send(embed=embed, view=view)
    view.message = msg


@bot.hybrid_command(name='unlist', description="Remove your card from the transfer market")
@app_commands.describe(listing_id="Listing ID to cancel (shown in /market)")
async def unlist(ctx, listing_id: int):
    ensure_player_exists(ctx.author.id, ctx.author.name)
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute(
        "SELECT card_id, edition, trade_count FROM market_listings WHERE listing_id = ? AND seller_id = ? AND status = 'active'",
        (listing_id, ctx.author.id)
    )
    listing = cursor.fetchone()
    if not listing:
        conn.close()
        return await ctx.send("❌ Listing not found, already sold, or it's not yours.")
    card_id, edition, trade_count = listing
    try:
        cursor.execute('INSERT INTO inventories (user_id, card_id, edition, trade_count) VALUES (?, ?, ?, ?)',
                       (ctx.author.id, card_id, edition, trade_count))
        cursor.execute(
            "UPDATE market_listings SET status='cancelled', resolved_at=? WHERE listing_id=?",
            (datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), listing_id)
        )
        conn.commit()
    except Exception as e:
        conn.close()
        logger.error(f"Unlist error: {e}")
        return await ctx.send("❌ Failed to remove listing.")
    conn.close()
    card = get_card_by_id(card_id)
    await ctx.send(f"✅ **{card.name if card else card_id}** (Edition #{edition}) returned to your inventory.")


@bot.hybrid_command(name='listed', description="View all your active transfer market listings")
async def listed(ctx):
    ensure_player_exists(ctx.author.id, ctx.author.name)
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT ml.listing_id, ml.card_id, ml.edition, ml.price, ml.expires_at, c.name, c.overall, c.card_rarity
        FROM market_listings ml
        JOIN cards c ON ml.card_id = c.card_id
        WHERE ml.seller_id = ? AND ml.status = 'active'
        ORDER BY ml.listed_at DESC
    ''', (ctx.author.id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return await ctx.send("You have no active listings on the transfer market.")

    embed = discord.Embed(
        title="📋 Your Active Listings",
        color=discord.Color.gold()
    )
    for listing_id, card_id, edition, price, expires_at, name, overall, rarity in rows:
        time_left = _market_time_left(expires_at)
        embed.add_field(
            name=f"{name}  ⭐{overall}",
            value=f"💰 **{price:,}** coins | 💎 {rarity} | Edition #{edition}\n🆔 Listing `#{listing_id}` · ⏱ {time_left} left",
            inline=False
        )
    embed.set_footer(text=f"{len(rows)} active listing(s) · Use /unlist <id> to cancel")
    await ctx.send(embed=embed)


@tasks.loop(minutes=10)
async def expire_market_listings():
    conn = sqlite3.connect('cards_game.db')
    cursor = conn.cursor()
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute(
        "SELECT listing_id, seller_id, card_id, edition, trade_count FROM market_listings WHERE expires_at <= ? AND status = 'active'", (now,)
    )
    expired = cursor.fetchall()
    for listing_id, seller_id, card_id, edition, trade_count in expired:
        cursor.execute('INSERT INTO inventories (user_id, card_id, edition, trade_count) VALUES (?, ?, ?, ?)',
                       (seller_id, card_id, edition, trade_count))
        cursor.execute(
            "UPDATE market_listings SET status='expired', resolved_at=? WHERE listing_id=?",
            (now, listing_id)
        )
    if expired:
        conn.commit()
        logger.info(f"Expired {len(expired)} market listing(s).")
    conn.close()


#-----------------------------------ADMIN COMMANDS----------------------------------

@bot.command(name='give_coins')
async def give_coins(ctx, user_id: int, amount: int):
    # Use the list loaded from .env
    if ctx.author.id not in ADMIN_IDS:
        await ctx.send("You do not have permission to use this command.")
        return

    if amount <= 0:
        await ctx.send("Amount must be positive.")
        return

    cursor.execute('SELECT name FROM players WHERE user_id = ?', (user_id,))
    user_name = cursor.fetchone()
    if not user_name:
        await ctx.send("User not found.")
        return

    cursor.execute('UPDATE players SET coins = coins + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    
    await ctx.send(f"Gave {amount} coins to user ID {user_id}.")
    logger.info(f"Admin {ctx.author.name} gave {amount} coins to user ID {user_id}.")




@bot.command(name='give_card')
async def give_player(ctx, user_id: int, card_id: int):
    if ctx.author.id not in ADMIN_IDS:
        return await ctx.send("You do not have permission to use this command.")

    card = get_card_by_id(card_id)
    if not card:
        await ctx.send("Card not found.")
        return

    cursor.execute('SELECT name FROM players WHERE user_id = ?', (user_id,))
    user_name = cursor.fetchone()
    if not user_name:
        await ctx.send("User not found.")
        return
    
    add_card_to_inventory(user_id, card_id)
    
    await ctx.send(f"Gave {card.name} to user ID {user_id}.")
    logger.info(f"Admin {ctx.author.name} gave card {card_id} to user ID {user_id}.")



@bot.command(name='remove_card')
async def remove_player(ctx, user_id: int, card_id: int):
    if ctx.author.id not in ADMIN_IDS:
        await ctx.send("You do not have permission to use this command.")
        return

    if not check_card_ownership(user_id, card_id):
        await ctx.send(f"User ID {user_id} does not own this card.")
        return
    
    remove_card_from_inventory(user_id, card_id)
    conn.commit()
    
    await ctx.send(f"Removed card {card_id} from user ID {user_id}.")
    logger.info(f"Admin {ctx.author.name} removed card {card_id} from user ID {user_id}.")


#--------------------SLASH COMMANDS------------------------

@bot.command(name='sync')
async def sync(ctx):
    if ctx.author.id not in ADMIN_IDS: # Changed to 'not in list'
        return await ctx.send("You are not a bot admin.")

    await ctx.send("Syncing commands... this might take a moment.")
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"✅ Synced {len(synced)} commands globally.")
    except Exception as e:
        await ctx.send(f"❌ Sync failed: {e}")


#---------------------DASHBOARD API (Phase 2)------------------------

def _api_auth(request):
    """Return True if the request carries the correct API key."""
    return request.headers.get('X-API-Key') == DASHBOARD_SECRET


def _json_ok(data):
    return aiohttp_web.Response(
        text=json.dumps(data),
        content_type='application/json',
        status=200,
    )


def _json_err(msg, status=400):
    return aiohttp_web.Response(
        text=json.dumps({'error': msg}),
        content_type='application/json',
        status=status,
    )


def _db_connect():
    """Open a short-lived SQLite connection (same file the bot uses)."""
    c = sqlite3.connect('cards_game.db')
    c.row_factory = sqlite3.Row
    return c


# ── /api/ping ────────────────────────────────────────────────────────────────
async def api_ping(request):
    if not _api_auth(request):
        return _json_err('Unauthorized', 401)
    return _json_ok({'ok': True, 'bot': str(bot.user)})


# ── /api/tables ──────────────────────────────────────────────────────────────
async def api_tables(request):
    if not _api_auth(request):
        return _json_err('Unauthorized', 401)
    with _db_connect() as c:
        rows = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return _json_ok({'tables': [r['name'] for r in rows]})


# ── /api/table/<name> ─────────────────────────────────────────────────────────
async def api_table_rows(request):
    if not _api_auth(request):
        return _json_err('Unauthorized', 401)
    table = request.match_info['table']
    # Basic whitelist: only allow alphanumeric + underscore table names
    if not table.replace('_', '').isalnum():
        return _json_err('Invalid table name')
    try:
        with _db_connect() as c:
            rows = c.execute(f'SELECT rowid, * FROM "{table}"').fetchall()
        return _json_ok({'rows': [dict(r) for r in rows]})
    except Exception as e:
        return _json_err(str(e))


# ── POST /api/table/<name>/insert ─────────────────────────────────────────────
async def api_table_insert(request):
    if not _api_auth(request):
        return _json_err('Unauthorized', 401)
    table = request.match_info['table']
    if not table.replace('_', '').isalnum():
        return _json_err('Invalid table name')
    try:
        data = await request.json()
        cols = list(data.keys())
        placeholders = ', '.join('?' for _ in cols)
        col_names = ', '.join(f'"{c}"' for c in cols)
        values = [data[c] for c in cols]
        with _db_connect() as c:
            cur = c.execute(
                f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders})',
                values,
            )
            c.commit()
            return _json_ok({'inserted_id': cur.lastrowid})
    except Exception as e:
        return _json_err(str(e))


# ── POST /api/table/<name>/update/<rowid> ─────────────────────────────────────
async def api_table_update(request):
    if not _api_auth(request):
        return _json_err('Unauthorized', 401)
    table = request.match_info['table']
    rowid = request.match_info['rowid']
    if not table.replace('_', '').isalnum():
        return _json_err('Invalid table name')
    if not rowid.isdigit():
        return _json_err('Invalid rowid')
    try:
        data = await request.json()
        set_clause = ', '.join(f'"{k}" = ?' for k in data)
        values = list(data.values()) + [int(rowid)]
        with _db_connect() as c:
            c.execute(
                f'UPDATE "{table}" SET {set_clause} WHERE rowid = ?',
                values,
            )
            c.commit()
        return _json_ok({'updated': True})
    except Exception as e:
        return _json_err(str(e))


# ── DELETE /api/table/<name>/delete/<rowid> ───────────────────────────────────
async def api_table_delete(request):
    if not _api_auth(request):
        return _json_err('Unauthorized', 401)
    table = request.match_info['table']
    rowid = request.match_info['rowid']
    if not table.replace('_', '').isalnum():
        return _json_err('Invalid table name')
    if not rowid.isdigit():
        return _json_err('Invalid rowid')
    try:
        with _db_connect() as c:
            c.execute(f'DELETE FROM "{table}" WHERE rowid = ?', (int(rowid),))
            c.commit()
        return _json_ok({'deleted': True})
    except Exception as e:
        return _json_err(str(e))


async def _start_dashboard_api():
    if not _AIOHTTP_AVAILABLE:
        logger.warning('aiohttp not installed — dashboard API disabled')
        return
    if not DASHBOARD_SECRET:
        logger.warning('DASHBOARD_SECRET not set — dashboard API disabled')
        return

    app = aiohttp_web.Application()
    app.router.add_get('/api/ping', api_ping)
    app.router.add_get('/api/tables', api_tables)
    app.router.add_get('/api/table/{table}', api_table_rows)
    app.router.add_post('/api/table/{table}/insert', api_table_insert)
    app.router.add_post('/api/table/{table}/update/{rowid}', api_table_update)
    app.router.add_delete('/api/table/{table}/delete/{rowid}', api_table_delete)

    runner = aiohttp_web.AppRunner(app)
    await runner.setup()
    site = aiohttp_web.TCPSite(runner, '0.0.0.0', API_PORT)
    await site.start()
    logger.info(f'Dashboard API listening on port {API_PORT}')


#---------------------RUN BOT------------------------

bot.run(TOKEN)
