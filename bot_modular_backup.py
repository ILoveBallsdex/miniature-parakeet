"""
FutBot - Football Card Trading Bot
Refactored modular cog-based structure
"""
import discord
from discord.ext import commands
import os
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load token
TOKEN = os.getenv('DISCORD_TOKEN')

# Setup bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=['f', 'F'], 
    intents=intents,
    help_command=None  # We have our own help command
)

# List of cogs to load
COGS = [
    'cogs.misc',         # Help, about, changelog, facts, suggest
    'cogs.admin',        # Admin commands, teststreak, sync
    'cogs.drops',        # Daily, drop, auto-drop, starter pack
    'cogs.leaderboards', # Leaderboards, stats, set_title, titles
    'cogs.economy',      # Coins, shop, buy, sell, packs, open
    'cogs.inventory',    # Inventory, catalog
    'cogs.cards',        # View, wishlist, weight, lookup
    'cogs.trading',      # Trade, exchange
    'cogs.battles',      # Battle system, decks, deck builder
    'cogs.secrets',      # Hidden Easter egg commands
]


@bot.event
async def on_ready():
    """Called when bot is ready"""
    logger.info(f'Logged in as {bot.user}')
    
    # Load all cogs
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            logger.info(f'Loaded cog: {cog}')
        except Exception as e:
            logger.error(f'Failed to load cog {cog}: {e}')
    
    # Sync commands
    try:
        synced = await bot.tree.sync()
        logger.info(f'Synced {len(synced)} slash commands')
    except Exception as e:
        logger.error(f'Failed to sync commands: {e}')


@bot.event
async def on_command_error(ctx, error):
    """Global error handler"""
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("You don't have permission to use this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing required argument: {error.param.name}")
    else:
        logger.error(f'Command error: {error}')


# Run the bot
if __name__ == '__main__':
    if TOKEN:
        bot.run(TOKEN)
    else:
        logger.error("No DISCORD_TOKEN found in environment variables!")
