"""
Admin Cog for FutBot
Admin-only commands for managing users, cards, and bot operations
"""
import discord
from discord.ext import commands
import logging
from datetime import datetime, timedelta

from utils.database import (
    get_connection, ADMIN_IDS, 
    add_card_to_inventory, remove_card_from_inventory, check_card_ownership
)
from utils.models import get_card_by_id

logger = logging.getLogger(__name__)


#---------------------------------------------------------COG CLASS-------------------------------------------------------------------------------------


class Admin(commands.Cog):
    """Admin commands for bot management"""
    
    def __init__(self, bot):
        self.bot = bot

    #---------------------------------------------------------GIVE COINS-------------------------------------------------------------------------------------

    @commands.command(name='give_coins')
    async def give_coins(self, ctx, user_id: int, amount: int):
        """Give coins to a user (Admin only)"""
        if ctx.author.id not in ADMIN_IDS:
            await ctx.send("You do not have permission to use this command.")
            return

        if amount <= 0:
            await ctx.send("Amount must be positive.")
            return

        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT name FROM players WHERE user_id = ?', (user_id,))
        user_name = cursor.fetchone()
        if not user_name:
            conn.close()
            await ctx.send("User not found.")
            return

        cursor.execute('UPDATE players SET coins = coins + ? WHERE user_id = ?', (amount, user_id))
        conn.commit()
        conn.close()
        
        await ctx.send(f"Gave {amount} coins to user ID {user_id}.")
        logger.info(f"Admin {ctx.author.name} gave {amount} coins to user ID {user_id}.")

    #---------------------------------------------------------GIVE CARD-------------------------------------------------------------------------------------

    @commands.command(name='give_card')
    async def give_card(self, ctx, user_id: int, card_id: int):
        """Give a card to a user (Admin only)"""
        if ctx.author.id not in ADMIN_IDS:
            return await ctx.send("You do not have permission to use this command.")

        card = get_card_by_id(card_id)
        if not card:
            await ctx.send("Card not found.")
            return

        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT name FROM players WHERE user_id = ?', (user_id,))
        user_name = cursor.fetchone()
        if not user_name:
            conn.close()
            await ctx.send("User not found.")
            return
        
        cursor.execute('UPDATE cards SET copies = copies + 1 WHERE card_id = ?', (card_id,))
        conn.commit()
        conn.close()
        
        try:
            add_card_to_inventory(user_id, card_id)
        except ValueError:
            await ctx.send("User already owns this card.")
            return
        
        await ctx.send(f"Gave {card.name} to user ID {user_id}.")
        logger.info(f"Admin {ctx.author.name} gave card {card_id} to user ID {user_id}.")

    #---------------------------------------------------------REMOVE CARD-------------------------------------------------------------------------------------

    @commands.command(name='remove_card')
    async def remove_card(self, ctx, user_id: int, card_id: int):
        """Remove a card from a user's inventory (Admin only)"""
        if ctx.author.id not in ADMIN_IDS:
            await ctx.send("You do not have permission to use this command.")
            return

        if not check_card_ownership(user_id, card_id):
            await ctx.send(f"User ID {user_id} does not own this card.")
            return
        
        remove_card_from_inventory(user_id, card_id)
        
        await ctx.send(f"Removed card {card_id} from user ID {user_id}.")
        logger.info(f"Admin {ctx.author.name} removed card {card_id} from user ID {user_id}.")

    #---------------------------------------------------------SYNC COMMANDS-------------------------------------------------------------------------------------

    @commands.command(name='sync')
    async def sync(self, ctx):
        """Sync slash commands globally (Admin only)"""
        if ctx.author.id not in ADMIN_IDS:
            return await ctx.send("You are not a bot admin.")

        await ctx.send("Syncing commands... this might take a moment.")
        try:
            synced = await self.bot.tree.sync()
            await ctx.send(f"✅ Synced {len(synced)} commands globally.")
        except Exception as e:
            await ctx.send(f"❌ Sync failed: {e}")

    #---------------------------------------------------------TEST STREAK-------------------------------------------------------------------------------------

    @commands.hybrid_command(name='teststreak', description="[ADMIN] Set your daily streak for testing")
    async def teststreak(self, ctx, streak: int):
        """Set daily streak for testing (Admin only)"""
        if ctx.author.id not in ADMIN_IDS:
            await ctx.send("❌ This command is for admins only.", ephemeral=True)
            return
        
        if streak < 0:
            await ctx.send("❌ Streak must be 0 or higher.", ephemeral=True)
            return
        
        conn = get_connection()
        cursor = conn.cursor()
        
        # Set streak and set last_daily_claim to YESTERDAY so claiming today will continue the streak
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        cursor.execute('''
            UPDATE players 
            SET daily_streak = ?, last_daily_claim = ? 
            WHERE user_id = ?
        ''', (streak, yesterday, ctx.author.id))
        conn.commit()
        conn.close()
        
        # Preview what they'll get next claim
        next_streak = streak + 1
        if next_streak >= 14:
            tier = "🔥 Legendary (300 coins)"
        elif next_streak >= 7:
            tier = "💎 Diamond (200 coins)"
        elif next_streak >= 4:
            tier = "🥈 Silver (150 coins)"
        else:
            tier = "🥉 Bronze (100 coins)"
        
        milestone = ""
        if next_streak == 7 or next_streak == 14:
            milestone = "\n🎁 **Milestone Pack!** You'll get a Rare Player Pack!"
        
        embed = discord.Embed(
            title="🧪 Test Mode Activated",
            description=f"Your streak set to **{streak}**.\nCooldown reset - you can claim `/daily` now!",
            color=discord.Color.purple()
        )
        embed.add_field(name="Next Claim Preview", value=f"Day **{next_streak}** → {tier}{milestone}", inline=False)
        
        await ctx.send(embed=embed, ephemeral=True)
        logger.info(f"[ADMIN] {ctx.author.name} set their streak to {streak} for testing.")


async def setup(bot):
    await bot.add_cog(Admin(bot))
