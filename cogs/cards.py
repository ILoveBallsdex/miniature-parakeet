"""
Cards Cog for FutBot
Card viewing, details, wishlist, and lookup commands
"""
import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import List

from utils.database import get_connection, ensure_player_exists
from utils.models import get_card_by_id, fetch_all_cards

logger = logging.getLogger(__name__)


#---------------------------------------------------------HELPER FUNCTIONS-------------------------------------------------------------------------------------


def get_card_by_name_or_id(identifier):
    """Get cards matching a name or ID."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Try exact ID match first
    if identifier.isdigit():
        cursor.execute("SELECT * FROM cards WHERE card_id = ?", (int(identifier),))
    else:
        # Search by name (fuzzy match)
        cursor.execute("SELECT * FROM cards WHERE LOWER(name) LIKE ?", (f"%{identifier.lower()}%",))
    
    rows = cursor.fetchall()
    conn.close()
    
    cards = []
    for row in rows:
        from utils.models import Card
        cards.append(Card(*row[:14]))
    return cards


#---------------------------------------------------------UI COMPONENTS-------------------------------------------------------------------------------------


class ViewCardSelect(discord.ui.Select):
    def __init__(self, cards, user, ctx):
        options = [discord.SelectOption(label=card.name[:100], value=str(card.card_id)) for card in cards[:25]]
        super().__init__(placeholder="Select the card...", min_values=1, max_values=1, options=options)
        self.cards = cards
        self.user = user
        self.ctx = ctx

    async def callback(self, interaction: discord.Interaction):
        card_id = int(self.values[0])
        card = next((c for c in self.cards if c.card_id == card_id), None)
        
        if not card:
            return await interaction.response.send_message("Card not found.", ephemeral=True)
        
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT trade_count FROM inventories WHERE user_id = ? AND card_id = ?', (self.ctx.author.id, card.card_id))
        inventory_entry = cursor.fetchone()
        
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
        
        cursor.execute('SELECT 1 FROM wishlists WHERE user_id = ? AND card_id = ?', (self.ctx.author.id, card.card_id))
        is_wishlisted = cursor.fetchone() is not None
        
        conn.close()
        
        owned_by_user = "Yes" if inventory_entry else "No"
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
        
        if owned_by_user == "Yes":
            trade_count = inventory_entry[0]
            ownership = "First Owner" if trade_count == 0 else "Traded In"
            embed.add_field(name="Your Inventory", value=f"✅ Owned ({ownership})", inline=True)

        embed.set_footer(text=f"Requested by {self.ctx.author.name}", icon_url=self.ctx.author.display_avatar.url)

        view = CardDetailsView(self.ctx, card.card_id, is_wishlisted)
        await interaction.response.edit_message(embed=embed, view=view)


class ViewCardSelectView(discord.ui.View):
    def __init__(self, cards, user, ctx):
        super().__init__(timeout=60)
        self.add_item(ViewCardSelect(cards, user, ctx))


class ToggleWishlistButton(discord.ui.Button):
    def __init__(self, card_id, is_wishlisted):
        label = "Remove from Wishlist" if is_wishlisted else "Add to Wishlist"
        emoji = "💔" if is_wishlisted else "❤️"
        style = discord.ButtonStyle.danger if is_wishlisted else discord.ButtonStyle.secondary
        
        super().__init__(style=style, label=label, emoji=emoji, custom_id=f"wl_toggle_{card_id}")
        self.card_id = card_id
        self.is_wishlisted = is_wishlisted

    async def callback(self, interaction: discord.Interaction):
        conn = get_connection()
        cursor = conn.cursor()
        
        if self.is_wishlisted:
            # Remove from wishlist
            cursor.execute('DELETE FROM wishlists WHERE user_id = ? AND card_id = ?', (interaction.user.id, self.card_id))
            cursor.execute('UPDATE cards SET wishlist_count = wishlist_count - 1 WHERE card_id = ?', (self.card_id,))
            self.is_wishlisted = False
            self.label = "Add to Wishlist"
            self.emoji = "❤️"
            self.style = discord.ButtonStyle.secondary
            msg = "Card removed from wishlist."
        else:
            # Add to wishlist
            try:
                cursor.execute('INSERT INTO wishlists (user_id, card_id) VALUES (?, ?)', (interaction.user.id, self.card_id))
                cursor.execute('UPDATE cards SET wishlist_count = wishlist_count + 1 WHERE card_id = ?', (self.card_id,))
                self.is_wishlisted = True
                self.label = "Remove from Wishlist"
                self.emoji = "💔"
                self.style = discord.ButtonStyle.danger
                msg = "Card added to wishlist!"
            except Exception:
                conn.close()
                await interaction.response.send_message("Card is already in your wishlist.", ephemeral=True)
                return
        
        conn.commit()
        conn.close()
        
        await interaction.response.edit_message(view=self.view)
        await interaction.followup.send(msg, ephemeral=True)


class CardDetailsView(discord.ui.View):
    def __init__(self, ctx, card_id, is_wishlisted):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.add_item(ToggleWishlistButton(card_id, is_wishlisted))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("⛔ This is not your menu. Run `/view` yourself!", ephemeral=True)
            return False
        return True


#---------------------------------------------------------COG CLASS-------------------------------------------------------------------------------------


class Cards(commands.Cog):
    """Card viewing and wishlist commands"""
    
    def __init__(self, bot):
        self.bot = bot
        self.all_cards = fetch_all_cards()

    async def card_search_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete for card search"""
        if not current:
            return []

        matches = []
        count = 0
        
        for card in self.all_cards:
            if current.lower() in card.name.lower():
                matches.append(card)
                count += 1
                if count == 25:
                    break

        return [
            app_commands.Choice(
                name=f"{card.name} | ⭐ {card.overall} | {card.card_type}"[:100], 
                value=str(card.card_id)
            )
            for card in matches
        ]

    @commands.hybrid_command(name='view', description="View details of a card")
    @app_commands.describe(player_name="Search for a player...")
    @app_commands.autocomplete(player_name=card_search_autocomplete)
    async def view(self, ctx, *, player_name: str):
        """View detailed information about a card"""
        ensure_player_exists(ctx.author.id, ctx.author.name)
        
        if player_name.isdigit():
            card = get_card_by_id(int(player_name))
            cards = [card] if card else []
        else:
            cards = get_card_by_name_or_id(player_name)
        
        if not cards:
            return await ctx.send(f"No card found matching '{player_name}'.")
        
        if len(cards) == 1:
            card = cards[0]
            
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute('SELECT trade_count FROM inventories WHERE user_id = ? AND card_id = ?', (ctx.author.id, card.card_id))
            inventory_entry = cursor.fetchone()
            
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
            
            owned_by_user = "Yes" if inventory_entry else "No"
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
            
            if owned_by_user == "Yes":
                trade_count = inventory_entry[0]
                ownership = "First Owner" if trade_count == 0 else "Traded In"
                embed.add_field(name="Your Inventory", value=f"✅ Owned ({ownership})", inline=True)

            embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)

            view = CardDetailsView(ctx, card.card_id, is_wishlisted)
            await ctx.send(embed=embed, file=discord.File(card.image_path), view=view)
            
            logger.info(f'{ctx.author.name} viewed card {card.name}')
        else:
            # Multiple matches - show selection
            embed = discord.Embed(
                title="Multiple Cards Found",
                description=f"Found **{len(cards)}** cards matching '{player_name}'.\nSelect one from the dropdown:",
                color=discord.Color.blue()
            )
            view = ViewCardSelectView(cards, ctx.author, ctx)
            await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(name='wishlist', description="View your wishlist")
    async def wishlist(self, ctx, user: discord.User = None):
        """View your or another user's card wishlist"""
        target_user = user or ctx.author
        ensure_player_exists(target_user.id, target_user.name)
        
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT cards.card_id, cards.name, cards.overall, cards.card_type 
            FROM wishlists 
            JOIN cards ON wishlists.card_id = cards.card_id 
            WHERE wishlists.user_id = ?
            ORDER BY cards.overall DESC
        ''', (target_user.id,))
        wishlist = cursor.fetchall()
        conn.close()
        
        if not wishlist:
            return await ctx.send(f"{target_user.name} has no cards in their wishlist.")
        
        lines = [f"**{name}** (ID: {card_id}) - ⭐ {overall} | {card_type}" for card_id, name, overall, card_type in wishlist[:20]]
        
        embed = discord.Embed(
            title=f"❤️ {target_user.name}'s Wishlist",
            description="\n".join(lines),
            color=discord.Color.red()
        )
        
        if len(wishlist) > 20:
            embed.set_footer(text=f"Showing 20 of {len(wishlist)} cards")
        else:
            embed.set_footer(text=f"{len(wishlist)} cards")
        
        await ctx.send(embed=embed)
        logger.info(f'{ctx.author.name} viewed {target_user.name}\'s wishlist')

    @commands.hybrid_command(name='wishlists', description="View a player's wishlist")
    async def wishlists(self, ctx, user: discord.User = None):
        """View your or another user's card wishlist (alias for wishlist)"""
        await self.wishlist(ctx, user)

    @commands.hybrid_command(name='weight', description="Check the pack weight of a card")
    async def weight(self, ctx, *, card_name: str):
        """Check the drop weight of a specific card"""
        from utils.models import get_card_weight_by_name
        
        weight_val, actual_card_name = get_card_weight_by_name(card_name)
        if weight_val:
            embed = discord.Embed(title=f"Card Weight: {actual_card_name}", color=0x00ff00)
            embed.add_field(name="Pack Weight", value=f"{weight_val:.6f}", inline=False)
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(title="Card Not Found", color=0xff0000)
            embed.add_field(name="Error", value=f"Card '{card_name}' not found or does not have a defined weight.", inline=False)
            await ctx.send(embed=embed)

    @commands.hybrid_command(name='lookup', aliases=['lu'], description="Inspect a specific card owned by a user")
    @app_commands.describe(card="Search for the card to inspect...")
    @app_commands.autocomplete(card=card_search_autocomplete)
    async def lookup(self, ctx, card: str, user: discord.User = None):
        """Generate a visual minted card slab for an owned card"""
        await ctx.defer()
        
        target_user = user or ctx.author
        ensure_player_exists(target_user.id, target_user.name)

        # Resolve Card ID
        if card.isdigit():
            card_id_int = int(card)
        else:
            cards = get_card_by_name_or_id(card)
            if not cards:
                return await ctx.send(f"❌ Could not find card: {card}")
            card_id_int = cards[0].card_id

        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT c.name, c.overall, c.attack, c.defense, c.speed, 
                   c.card_rarity, c.card_type, c.image_path, c.copies, i.edition,
                   i.battles_played, i.battles_won, i.rounds_played, i.rounds_won
            FROM inventories i
            JOIN cards c ON i.card_id = c.card_id
            WHERE i.user_id = ? AND i.card_id = ?
        ''', (target_user.id, card_id_int))
        
        result = cursor.fetchone()
        conn.close()

        if not result:
            return await ctx.send(f"❌ **{target_user.name}** does not own Card ID `{card_id_int}`.")

        name, overall, atk, def_, spd, rarity, type_, image_path, total_copies, edition, b_played, b_won, r_played, r_won = result
        
        edition_str = f"#{edition}/{total_copies}"
        win_rate = f"{(b_won / b_played * 100):.1f}%" if b_played > 0 else "0%"

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

        # Try to send with image
        try:
            await ctx.send(file=discord.File(image_path), embed=embed)
        except:
            await ctx.send(embed=embed)
        
        logger.info(f'{ctx.author.name} used lookup for card {card_id_int}')


async def setup(bot):
    await bot.add_cog(Cards(bot))
