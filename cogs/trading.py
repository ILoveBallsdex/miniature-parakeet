"""
Trading Cog for FutBot
Card trading and exchange commands
"""
import discord
from discord.ext import commands
import logging

from utils.database import (
    get_connection, ensure_player_exists, get_player_inventory
)
from utils.models import get_card_by_id

logger = logging.getLogger(__name__)


#---------------------------------------------------------HELPER FUNCTIONS-------------------------------------------------------------------------------------


def check_card_ownership(user_id, card_id):
    """Check if a user owns a specific card."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (user_id, card_id))
    result = cursor.fetchone()
    conn.close()
    return result is not None


#---------------------------------------------------------TRADE VIEW (SIMPLE 1:1)-------------------------------------------------------------------------------------


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

        conn = get_connection()
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
            # Move Author's card to Other User
            cursor.execute('''
                UPDATE inventories 
                SET user_id = ?, trade_count = trade_count + 1 
                WHERE user_id = ? AND card_id = ?
            ''', (self.other_user.id, self.ctx.author.id, self.your_card.card_id))
            
            # Move Other User's card to Author
            cursor.execute('''
                UPDATE inventories 
                SET user_id = ?, trade_count = trade_count + 1 
                WHERE user_id = ? AND card_id = ?
            ''', (self.ctx.author.id, self.other_user.id, self.their_card.card_id))
            
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


#---------------------------------------------------------EXCHANGE SYSTEM (MULTI-CARD + COINS)-------------------------------------------------------------------------------------


class ExchangeSession:
    def __init__(self, p1, p2):
        self.p1 = p1
        self.p2 = p2
        self.p1_offer = {'cards': [], 'coins': 0}
        self.p2_offer = {'cards': [], 'coins': 0}
        self.p1_locked = False
        self.p2_locked = False
        self.p1_confirmed = False
        self.p2_confirmed = False


class ExchangeCardSearchSelect(discord.ui.Select):
    def __init__(self, cards, view, side):
        self.exchange_view = view
        self.side = side
        
        options = []
        for card in cards[:25]:
            options.append(discord.SelectOption(
                label=card.name[:100],
                description=f"ID: {card.card_id} | ⭐ {card.overall}",
                value=str(card.card_id)
            ))
            
        super().__init__(placeholder="Select the card to add...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        card_id = int(self.values[0])
        user = interaction.user
        
        if not check_card_ownership(user.id, card_id):
            return await interaction.response.send_message("❌ You no longer own this card.", ephemeral=True)

        opponent = self.exchange_view.session.p2 if self.side == 'p1' else self.exchange_view.session.p1
        if check_card_ownership(opponent.id, card_id):
            return await interaction.response.send_message(f"⛔ **{opponent.name}** already owns this card!", ephemeral=True)

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
        
        await self.exchange_view.update_display(interaction=None)
        await interaction.response.send_message(f"✅ Added **{card.name}** to the exchange.", ephemeral=True)


class ExchangeSearchModal(discord.ui.Modal, title="Search your Inventory"):
    query = discord.ui.TextInput(label="Card Name", placeholder="e.g. Messi")

    def __init__(self, view, side):
        super().__init__()
        self.exchange_view = view
        self.side = side

    async def on_submit(self, interaction: discord.Interaction):
        search_term = self.query.value.lower()
        user_id = interaction.user.id
        
        inventory, _ = get_player_inventory(user_id)
        matches = [c for c in inventory if search_term in c.name.lower()]
        
        if not matches:
            return await interaction.response.send_message("❌ No cards found matching that name.", ephemeral=True)

        view = discord.ui.View()
        view.add_item(ExchangeCardSearchSelect(matches, self.exchange_view, self.side))
        
        await interaction.response.send_message(
            f"🔍 Found {len(matches)} cards matching '**{self.query.value}**'. Select one below:", 
            view=view, 
            ephemeral=True
        )


class ExAddCardButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Add Card", style=discord.ButtonStyle.primary, emoji="🃏")
    
    async def callback(self, interaction: discord.Interaction):
        session = self.view.session
        if interaction.user.id == session.p1.id:
            side = 'p1'
        elif interaction.user.id == session.p2.id:
            side = 'p2'
        else:
            return await interaction.response.send_message("You're not part of this exchange.", ephemeral=True)
        
        await interaction.response.send_modal(ExchangeSearchModal(self.view, side))


class ExAddCoinsModal(discord.ui.Modal, title="Add Coins"):
    amount = discord.ui.TextInput(label="Amount", placeholder="Enter coin amount...")
    
    def __init__(self, view, side):
        super().__init__()
        self.exchange_view = view
        self.side = side
    
    async def on_submit(self, interaction: discord.Interaction):
        if not self.amount.value.isdigit():
            return await interaction.response.send_message("Invalid amount.", ephemeral=True)
        
        amount = int(self.amount.value)
        
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT coins FROM players WHERE user_id = ?', (interaction.user.id,))
        user_coins = cursor.fetchone()[0]
        conn.close()
        
        if amount > user_coins:
            return await interaction.response.send_message(f"You only have {user_coins} coins!", ephemeral=True)
        
        if self.side == 'p1':
            self.exchange_view.session.p1_offer['coins'] = amount
        else:
            self.exchange_view.session.p2_offer['coins'] = amount
        
        self.exchange_view.session.p1_locked = False
        self.exchange_view.session.p2_locked = False
        
        await self.exchange_view.update_display(interaction=None)
        await interaction.response.send_message(f"✅ Set your coin offer to **{amount}**.", ephemeral=True)


class ExAddCoinsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Add Coins", style=discord.ButtonStyle.secondary, emoji="💰")
    
    async def callback(self, interaction: discord.Interaction):
        session = self.view.session
        if interaction.user.id == session.p1.id:
            side = 'p1'
        elif interaction.user.id == session.p2.id:
            side = 'p2'
        else:
            return await interaction.response.send_message("You're not part of this exchange.", ephemeral=True)
        
        await interaction.response.send_modal(ExAddCoinsModal(self.view, side))


class ExClearButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Clear", style=discord.ButtonStyle.danger, emoji="🗑️")
    
    async def callback(self, interaction: discord.Interaction):
        session = self.view.session
        if interaction.user.id == session.p1.id:
            session.p1_offer = {'cards': [], 'coins': 0}
            session.p1_locked = False
        elif interaction.user.id == session.p2.id:
            session.p2_offer = {'cards': [], 'coins': 0}
            session.p2_locked = False
        else:
            return await interaction.response.send_message("You're not part of this exchange.", ephemeral=True)
        
        await self.view.update_display(interaction)


class ExLockButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Lock", style=discord.ButtonStyle.success, emoji="🔒")
    
    async def callback(self, interaction: discord.Interaction):
        session = self.view.session
        if interaction.user.id == session.p1.id:
            session.p1_locked = True
        elif interaction.user.id == session.p2.id:
            session.p2_locked = True
        else:
            return await interaction.response.send_message("You're not part of this exchange.", ephemeral=True)
        
        await self.view.update_display(interaction)


class ExConfirmButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Confirm", style=discord.ButtonStyle.success, emoji="✅")
    
    async def callback(self, interaction: discord.Interaction):
        session = self.view.session
        if interaction.user.id == session.p1.id:
            session.p1_confirmed = True
        elif interaction.user.id == session.p2.id:
            session.p2_confirmed = True
        else:
            return await interaction.response.send_message("You're not part of this exchange.", ephemeral=True)
        
        if session.p1_confirmed and session.p2_confirmed:
            await self.view.execute_exchange(interaction)
        else:
            await interaction.response.send_message("Waiting for the other player to confirm...", ephemeral=True)


class ExCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌")
    
    async def callback(self, interaction: discord.Interaction):
        session = self.view.session
        if interaction.user.id not in [session.p1.id, session.p2.id]:
            return await interaction.response.send_message("You're not part of this exchange.", ephemeral=True)
        
        embed = discord.Embed(title="❌ Exchange Cancelled", color=discord.Color.red())
        embed.description = f"{interaction.user.name} cancelled the exchange."
        await interaction.response.edit_message(embed=embed, view=None)
        self.view.stop()


class ExchangeView(discord.ui.View):
    def __init__(self, ctx, p1, p2):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.session = ExchangeSession(p1, p2)
        self.message = None
        
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
            await self.message.edit(embed=embed, view=self)
        elif interaction.response.is_done():
            await self.message.edit(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    async def execute_exchange(self, interaction):
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            # Transfer cards from P1 to P2
            for card in self.session.p1_offer['cards']:
                cursor.execute('UPDATE inventories SET user_id = ?, trade_count = trade_count + 1 WHERE user_id = ? AND card_id = ?',
                              (self.session.p2.id, self.session.p1.id, card.card_id))
            
            # Transfer cards from P2 to P1
            for card in self.session.p2_offer['cards']:
                cursor.execute('UPDATE inventories SET user_id = ?, trade_count = trade_count + 1 WHERE user_id = ? AND card_id = ?',
                              (self.session.p1.id, self.session.p2.id, card.card_id))
            
            # Transfer coins
            p1_coins = self.session.p1_offer['coins']
            p2_coins = self.session.p2_offer['coins']
            
            cursor.execute('UPDATE players SET coins = coins - ? + ? WHERE user_id = ?',
                          (p1_coins, p2_coins, self.session.p1.id))
            cursor.execute('UPDATE players SET coins = coins - ? + ? WHERE user_id = ?',
                          (p2_coins, p1_coins, self.session.p2.id))
            
            conn.commit()
            
            embed = discord.Embed(title="✅ Exchange Complete!", color=discord.Color.green())
            embed.description = f"Items have been swapped between **{self.session.p1.name}** and **{self.session.p2.name}**!"
            await interaction.response.edit_message(embed=embed, view=None)
            self.stop()
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Exchange Error: {e}")
            await interaction.response.send_message("❌ Exchange failed due to database error.", ephemeral=True)
        finally:
            conn.close()


#---------------------------------------------------------COG CLASS-------------------------------------------------------------------------------------


class Trading(commands.Cog):
    """Trading and exchange commands"""
    
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='trade', description="Trade cards with another player")
    async def trade(self, ctx, your_card_id: int, other_user: discord.User, their_card_id: int):
        """Simple 1:1 card trade"""
        if ctx.author.id == other_user.id:
            return await ctx.send("You cannot trade with yourself.")

        ensure_player_exists(ctx.author.id, ctx.author.name)
        ensure_player_exists(other_user.id, other_user.name)

        your_card = get_card_by_id(your_card_id)
        their_card = get_card_by_id(their_card_id)

        if not your_card:
            return await ctx.send(f"Card ID **{your_card_id}** not found.")
        if not their_card:
            return await ctx.send(f"Card ID **{their_card_id}** not found.")

        if not check_card_ownership(ctx.author.id, your_card_id):
            return await ctx.send(f"You do not own the card **{your_card.name}** (ID: {your_card_id}).")
        
        if not check_card_ownership(other_user.id, their_card_id):
            return await ctx.send(f"{other_user.name} does not own the card **{their_card.name}** (ID: {their_card_id}).")

        if check_card_ownership(ctx.author.id, their_card_id):
            return await ctx.send(f"You already own **{their_card.name}**. Cannot trade for duplicates.")
        
        if check_card_ownership(other_user.id, your_card_id):
            return await ctx.send(f"{other_user.name} already owns **{your_card.name}**. Cannot trade duplicates.")

        view = TradeView(ctx, your_card, other_user, their_card)
        
        embed = discord.Embed(title="🤝 Trade Offer", description=f"{ctx.author.mention} wants to trade with {other_user.mention}!", color=discord.Color.gold())
        embed.add_field(name=f"{ctx.author.name} offers:", value=f"**{your_card.name}**\n⭐ {your_card.overall} | 🆔 {your_card.card_id}", inline=True)
        embed.add_field(name=f"{other_user.name} offers:", value=f"**{their_card.name}**\n⭐ {their_card.overall} | 🆔 {their_card.card_id}", inline=True)
        embed.set_footer(text="Both players must verify the cards before accepting.")

        msg = await ctx.send(content=f"Hey {other_user.mention}, you have a trade offer!", embed=embed, view=view)
        view.message = msg
        logger.info(f'{ctx.author.name} initiated trade with {other_user.name}')

    @commands.hybrid_command(name='exchange', description="Open an exchange table with another player")
    async def exchange(self, ctx, other_user: discord.User):
        """Multi-card + coins exchange table"""
        if ctx.author.id == other_user.id:
            return await ctx.send("You cannot exchange with yourself.")

        ensure_player_exists(ctx.author.id, ctx.author.name)
        ensure_player_exists(other_user.id, other_user.name)

        view = ExchangeView(ctx, ctx.author, other_user)
        
        embed = discord.Embed(title="⚖️ Exchange Table", color=discord.Color.gold())
        embed.description = "Add items using the buttons below. Lock when ready."
        embed.add_field(name=f"{ctx.author.name} (✏️ Editing...)", value="💰 0\nNo cards", inline=True)
        embed.add_field(name=f"{other_user.name} (✏️ Editing...)", value="💰 0\nNo cards", inline=True)

        msg = await ctx.send(content=f"{other_user.mention}, you've been invited to an exchange!", embed=embed, view=view)
        view.message = msg
        logger.info(f'{ctx.author.name} opened exchange with {other_user.name}')


async def setup(bot):
    await bot.add_cog(Trading(bot))
