"""
Secrets Cog for FutBot
Hidden Easter egg commands for country-themed special cards
"""
import discord
from discord.ext import commands
import random
import logging

from utils.database import get_connection, ensure_player_exists, add_card_to_inventory
from utils.models import get_card_by_id, add_card

logger = logging.getLogger(__name__)


#---------------------------------------------------------SECRET COMMAND HELPERS-------------------------------------------------------------------------------------


import functools

def secret_command():
    """Decorator to delete the command message after execution."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, ctx, *args, **kwargs):
            result = await func(self, ctx, *args, **kwargs)
            try:
                await ctx.message.delete()
            except:
                pass  # Message may already be deleted or in DMs
            return result
        return wrapper
    return decorator


async def process_secret_command(ctx, card_ids, db_column, command_name):
    """
    Generic handler for all secret commands.
    
    Args:
        ctx: Command context
        card_ids: List of possible card IDs to award
        db_column: Database column to check/update (e.g., 'itscominghome')
        command_name: Name of the command for logging
    """
    ensure_player_exists(ctx.author.id, ctx.author.name)
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Check if already used
    cursor.execute(f'SELECT {db_column} FROM players WHERE user_id = ?', (ctx.author.id,))
    used_command = cursor.fetchone()[0]

    if used_command:
        await ctx.author.send("You have already used this command.")
        conn.close()
        return
    
    # Find a card the user doesn't own
    card_id = None
    attempts = 0
    max_attempts = len(card_ids) * 2
    
    while attempts < max_attempts:
        attempts += 1
        card_id = random.choice(card_ids)
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

        cursor.execute(f'UPDATE players SET {db_column} = 1 WHERE user_id = ?', (ctx.author.id,))
        conn.commit()
        
        embed = discord.Embed(
            title="🎁 Special Drop",
            description="You have received a special card drop! Shh, don't tell anyone about this command.",
            color=discord.Color.gold()
        )
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
        logger.info(f'{ctx.author.name} received a special card {card.name} (ID: {card.card_id}) using !{command_name}')
    else:
        await ctx.author.send("An error occurred while processing your request.")
    
    conn.close()


#---------------------------------------------------------CARD ID POOLS-------------------------------------------------------------------------------------


# England - "It's Coming Home" (1966 World Cup reference)
ITSCOMINGHOME_CARD_IDS = [10392, 10397, 10406, 10407, 10408, 10411, 10412, 10418, 10428, 10443, 10451, 10453, 10457]

# Brazil - "Joga Bonito" (The Beautiful Game)
JOGABONITO_CARD_IDS = [10394, 10395, 10399, 10405, 10446, 10462, 10465, 10469]

# Italy - "Pineapple on Pizza" (Controversial topping reference to 2006 World Cup)
PINEAPPLEONPIZZA_CARD_IDS = [10391, 10393, 10414, 10415, 10430, 10455, 10459, 10460]

# Germany - "Fußball" (German for football)
MANNSCHAFT_CARD_IDS = [10417, 10447, 10449, 10452, 10463]

# Netherlands - "The Flying Dutchmen" (Total Football style)
FLYINGDUTCHMEN_CARD_IDS = [10420, 10422, 10424, 10432, 10433, 10448, 10456]

# France - "Mayonnaise" (French food reference with Les Bleus)
BLUES_CARD_IDS = [10398, 10410, 10419, 10421, 10426, 10439, 10467]


#---------------------------------------------------------COG CLASS-------------------------------------------------------------------------------------


class Secrets(commands.Cog):
    """Hidden Easter egg commands for special country-themed card drops"""
    
    def __init__(self, bot):
        self.bot = bot


    #---------------------------------------------------------ENGLAND-------------------------------------------------------------------------------------

    @commands.command(name='itscominghome')
    @secret_command()
    async def itscominghome(self, ctx):
        """🏴󠁧󠁢󠁥󠁮󠁧󠁿 England - Secret card drop"""
        await process_secret_command(ctx, ITSCOMINGHOME_CARD_IDS, 'itscominghome', 'itscominghome')


    #---------------------------------------------------------BRAZIL-------------------------------------------------------------------------------------

    @commands.command(name='jogabonito')
    @secret_command()
    async def jogabonito(self, ctx):
        """🇧🇷 Brazil - Secret card drop"""
        await process_secret_command(ctx, JOGABONITO_CARD_IDS, 'jogabonito', 'jogabonito')


    #---------------------------------------------------------ITALY-------------------------------------------------------------------------------------

    @commands.command(name='pineappleonpizza')
    @secret_command()
    async def pineappleonpizza(self, ctx):
        """🇮🇹 Italy - Secret card drop"""
        await process_secret_command(ctx, PINEAPPLEONPIZZA_CARD_IDS, 'pineappleonpizza', 'pineappleonpizza')


    #---------------------------------------------------------GERMANY-------------------------------------------------------------------------------------

    @commands.command(name='fubball')
    @secret_command()
    async def fubball(self, ctx):
        """🇩🇪 Germany - Secret card drop"""
        await process_secret_command(ctx, MANNSCHAFT_CARD_IDS, 'mannschaft', 'fubball')


    #---------------------------------------------------------NETHERLANDS-------------------------------------------------------------------------------------

    @commands.command(name='theflyingdutchmen')
    @secret_command()
    async def theflyingdutchmen(self, ctx):
        """🇳🇱 Netherlands - Secret card drop"""
        await process_secret_command(ctx, FLYINGDUTCHMEN_CARD_IDS, 'theflyingdutchmen', 'theflyingdutchmen')


    #---------------------------------------------------------FRANCE-------------------------------------------------------------------------------------

    @commands.command(name='mayonnaise')
    @secret_command()
    async def mayonnaise(self, ctx):
        """🇫🇷 France - Secret card drop"""
        await process_secret_command(ctx, BLUES_CARD_IDS, 'blues', 'mayonnaise')


#---------------------------------------------------------SETUP-------------------------------------------------------------------------------------


async def setup(bot):
    await bot.add_cog(Secrets(bot))
