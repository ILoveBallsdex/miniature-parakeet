"""
Drops Cog for FutBot
Daily rewards, card drops, auto-drops, starter pack
"""
import discord
from discord.ext import commands, tasks
import random
import asyncio
import time
import logging
from datetime import datetime, timedelta

from utils.database import (
    get_connection, ensure_player_exists, add_card_to_inventory,
    increment_cards_dropped, DROP_CHANNEL_IDS
)
from utils.models import (
    Card, get_cards_with_weights, weighted_choice, add_card, fetch_all_cards
)

logger = logging.getLogger(__name__)


#---------------------------------------------------------UI COMPONENTS-------------------------------------------------------------------------------------


class DailyView(discord.ui.View):
    def __init__(self, timeout=120):
        super().__init__(timeout=timeout)
        self.collected = False


class DropView(discord.ui.View):
    def __init__(self, timeout=120):
        super().__init__(timeout=timeout)
        self.collected = False


class CollectCardButton(discord.ui.Button):
    """Button for collecting a card from daily reward"""
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


class TimedCollectButton(discord.ui.Button):
    """Button for collecting a dropped card with owner priority"""
    def __init__(self, card, owner_id):
        super().__init__(style=discord.ButtonStyle.green, label="Collect", custom_id="timed_collect_card")
        self.card = card
        self.owner_id = owner_id
        self.drop_time = time.time()

    async def callback(self, interaction: discord.Interaction):
        # Check Time Lock
        time_elapsed = time.time() - self.drop_time
        
        # Lock only applies if there is a specific owner (Manual Drop)
        if self.owner_id is not None and interaction.user.id != self.owner_id and time_elapsed < 10:
            remaining = 10 - int(time_elapsed)
            await interaction.response.send_message(f"✋ **Locked!** Priority to owner for {remaining} more seconds.", ephemeral=True)
            return

        # Add to Inventory
        ensure_player_exists(interaction.user.id, interaction.user.name)
        try:
            add_card_to_inventory(interaction.user.id, self.card.card_id)
        except ValueError:
            return await interaction.response.send_message("You already have this card!", ephemeral=True)

        # Success Embed
        embed = discord.Embed(
            title="✅ Card Collected!",
            description=f"**{self.card.name}** has been collected by {interaction.user.mention}!",
            color=discord.Color.gold()
        )
        embed.set_image(url=f"attachment://{self.card.image_path.split('/')[-1]}")
        
        embed.add_field(name="Stats", value=f"⭐ {self.card.overall} | ⚔️ {self.card.attack} | 🛡️ {self.card.defense} | ⚡ {self.card.speed}", inline=False)
        embed.add_field(name="Card Details", value=f"ID: {self.card.card_id} | Rarity: {self.card.card_rarity} | Total Copies: {self.card.copies + 1}", inline=False)
        embed.set_footer(text=f"Winner: {interaction.user.name}", icon_url=interaction.user.display_avatar.url)

        if hasattr(self.view, 'collected'):
            self.view.collected = True

        await interaction.response.edit_message(embed=embed, view=None)
        self.view.stop()


#---------------------------------------------------------COG CLASS-------------------------------------------------------------------------------------


class Drops(commands.Cog):
    """Daily rewards, card drops, and starter pack commands"""
    
    def __init__(self, bot):
        self.bot = bot
        self.cards_with_weights = get_cards_with_weights()
        self.all_cards = fetch_all_cards()

    def cog_load(self):
        self.card_drop.start()

    def cog_unload(self):
        self.card_drop.cancel()

    def increment_card_copies(self, card_id):
        """Increment the copies count for a card."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE cards SET copies = copies + 1 WHERE card_id = ?', (card_id,))
        conn.commit()
        conn.close()

    # --- Auto Drop Task ---
    @tasks.loop(minutes=30)
    async def card_drop(self):
        """Automatic card drop every 30 minutes"""
        await self.bot.wait_until_ready()
        
        card = weighted_choice(self.cards_with_weights)
        add_card(card)

        for channel_id in DROP_CHANNEL_IDS:
            channel = self.bot.get_channel(channel_id)
            if channel:
                self.bot.loop.create_task(self.handle_single_drop(channel, card))
            else:
                logger.error(f"Could not find drop channel ID: {channel_id}")

    async def handle_single_drop(self, channel, card):
        """Handle a drop in a single channel"""
        try:
            embed = discord.Embed(
                title="🎁 Random Card Drop!", 
                description="Be the first to click **Collect** to claim this card!",
                color=discord.Color.blue()
            )
            embed.set_image(url=f"attachment://{card.image_path.split('/')[-1]}")
            embed.set_footer(text="Hurry! This drop expires in 2 minutes.")

            view = DropView(timeout=120)
            view.add_item(TimedCollectButton(card, None))

            msg = await channel.send(embed=embed, view=view, file=discord.File(card.image_path))
            
            await view.wait()
            
            if not view.collected:
                expired_embed = discord.Embed(
                    title="❌ Drop Expired", 
                    description=f"No one collected **{card.name}** in time.", 
                    color=discord.Color.red()
                )
                expired_embed.set_image(url=f"attachment://{card.image_path.split('/')[-1]}")
                await msg.edit(embed=expired_embed, view=None)
                
        except Exception as e:
            logger.error(f"Error dropping in channel {channel.id}: {e}")

    # --- Commands ---
    
    @commands.hybrid_command(name='daily', description="Claim your daily reward card")
    async def daily(self, ctx):
        """Claim your daily reward with streak bonus (once per calendar day)"""
        logger.info(f"User {ctx.author.name} (ID: {ctx.author.id}) invoked the daily command.")
        ensure_player_exists(ctx.author.id, ctx.author.name)
        
        conn = get_connection()
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
                logger.info(f"User {ctx.author.name} tried to claim daily reward but already claimed today")
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
        
        # Calculate rewards
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
        
        cursor.execute('UPDATE players SET coins = coins + ? WHERE user_id = ?', (bonus_coins, ctx.author.id))
        
        # Check for milestone bonuses
        milestone_text = ""
        if current_streak == 7:
            cursor.execute('SELECT * FROM packs WHERE user_id = ?', (ctx.author.id,))
            if cursor.fetchone():
                cursor.execute('UPDATE packs SET rare_player_pack = rare_player_pack + 1 WHERE user_id = ?', (ctx.author.id,))
            else:
                cursor.execute('INSERT INTO packs (user_id, rare_player_pack, icon_pack, hero_pack, tester_pack) VALUES (?, 1, 0, 0, 0)', (ctx.author.id,))
            milestone_text = "\n\n🎁 **MILESTONE BONUS!** You got a FREE **Rare Player Pack**!"
        elif current_streak == 14:
            cursor.execute('SELECT * FROM packs WHERE user_id = ?', (ctx.author.id,))
            if cursor.fetchone():
                cursor.execute('UPDATE packs SET rare_player_pack = rare_player_pack + 1 WHERE user_id = ?', (ctx.author.id,))
            else:
                cursor.execute('INSERT INTO packs (user_id, rare_player_pack, icon_pack, hero_pack, tester_pack) VALUES (?, 1, 0, 0, 0)', (ctx.author.id,))
            milestone_text = "\n\n🎁 **2-WEEK MILESTONE!** You got a FREE **Rare Player Pack**!"
        
        conn.commit()
        conn.close()
        
        # Generate cards
        cards = [weighted_choice(self.cards_with_weights) for _ in range(2)]
        for card in cards:
            card.copies += 1

        increment_cards_dropped(ctx.author.id)

        # Build embed
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

    @commands.hybrid_command(name='drop', description="Drop a random card in the chat")
    @commands.cooldown(1, 1800, commands.BucketType.user)
    async def drop_card(self, ctx):
        """Drop a random card for others to collect"""
        logger.info(f"User {ctx.author.name} (ID: {ctx.author.id}) invoked the drop command.")
        ensure_player_exists(ctx.author.id, ctx.author.name)
        
        card = weighted_choice(self.cards_with_weights)
        card.copies += 1
        add_card(card)
        increment_cards_dropped(ctx.author.id)

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

        view = DropView(timeout=120)
        view.add_item(TimedCollectButton(card, ctx.author.id))

        try:
            msg = await ctx.send(content=content, embed=embed, view=view, file=discord.File(card.image_path))
            
            await asyncio.sleep(10)
            
            if view.is_finished():
                return

            embed.description = "🔓 **Owner Priority Ended**\nAnyone can claim now!"
            embed.color = discord.Color.green()
            await msg.edit(embed=embed)

            await view.wait()
            
            if not hasattr(view, 'collected') or not view.collected:
                embed.title = "❌ Drop Expired"
                embed.description = "No one collected this card in time."
                embed.color = discord.Color.red()
                await msg.edit(embed=embed, view=None)

        except Exception as e:
            logger.error(f"Failed to send card drop message: {e}")

    @drop_card.error
    async def drop_card_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            retry_after = int(error.retry_after)
            minutes, seconds = divmod(retry_after, 60)
            await ctx.send(f"This command is on cooldown. Please wait {minutes} minutes and {seconds} seconds to use it again.")
            logger.info(f"User {ctx.author.name} tried to drop a card but is on cooldown")

    @commands.hybrid_command(name='get_starter_pack', description="Claim your free starter cards")
    async def get_starter_pack(self, ctx):
        """Claim free starter cards for new players"""
        ensure_player_exists(ctx.author.id, ctx.author.name)
        
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT has_claimed_starter_pack FROM players WHERE user_id = ?', (ctx.author.id,))
        has_claimed_starter_pack = cursor.fetchone()[0]
        if has_claimed_starter_pack:
            conn.close()
            await ctx.send("You have already claimed your starter pack!")
            return

        all_cards_list = self.all_cards
        common_pack = random.sample([card for card in all_cards_list if 70 <= card.overall <= 79], 6)
        uncommon_pack = random.sample([card for card in all_cards_list if 80 <= card.overall <= 85], 3)
        rare_pack = random.sample([card for card in all_cards_list if card.overall > 85 and card.card_type == 'Standard'], 1)

        all_cards_received = common_pack + uncommon_pack + rare_pack

        for card in all_cards_received:
            self.increment_card_copies(card.card_id)
            add_card_to_inventory(ctx.author.id, card.card_id)

        cursor.execute('UPDATE players SET has_claimed_starter_pack = 1 WHERE user_id = ?', (ctx.author.id,))
        conn.commit()
        conn.close()

        card_names = "\n".join([f"{card.name} (ID: {card.card_id})" for card in all_cards_received])
        await ctx.send(f"**{ctx.author.name} has claimed their starter pack!**\nYou received:\n{card_names}")
        logger.info(f'{ctx.author.name} claimed a starter pack')


async def setup(bot):
    await bot.add_cog(Drops(bot))
