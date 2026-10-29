"""
Economy Cog for FutBot
Shop, buy, sell, packs, coins, and open commands
"""
import discord
from discord.ext import commands
import random
import logging

from utils.database import (
    get_connection, ensure_player_exists, add_card_to_inventory,
    get_player_inventory
)

logger = logging.getLogger(__name__)


#---------------------------------------------------------PACK DEFINITIONS-------------------------------------------------------------------------------------


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
        "cost": 0
    }
}


#---------------------------------------------------------HELPER FUNCTIONS-------------------------------------------------------------------------------------


def calculate_card_value(card):
    """Calculate the sell value of a card based on overall."""
    ovr = card.overall
    if ovr >= 90:
        return 200
    elif ovr >= 85:
        return 150
    elif ovr >= 80:
        return 100
    elif ovr >= 75:
        return 50
    else:
        return 25


def get_user_packs(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM packs WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))
    return {}


def add_pack_to_user(user_id, pack_name):
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM packs WHERE user_id = ?', (user_id,))
    user_packs = cursor.fetchone()
    
    if user_packs:
        cursor.execute(f'UPDATE packs SET {pack_name} = {pack_name} + 1 WHERE user_id = ?', (user_id,))
    else:
        cursor.execute('INSERT INTO packs (user_id, rare_player_pack, icon_pack, hero_pack, tester_pack) VALUES (?, 0, 0, 0, 0)', (user_id,))
        cursor.execute(f'UPDATE packs SET {pack_name} = {pack_name} + 1 WHERE user_id = ?', (user_id,))
    
    conn.commit()
    conn.close()


def remove_pack_from_user(user_id, pack_name):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"UPDATE packs SET {pack_name} = {pack_name} - 1 WHERE user_id = ? AND {pack_name} > 0", (user_id,))
    conn.commit()
    conn.close()


def has_sufficient_coins(user_id, cost):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT coins FROM players WHERE user_id = ?", (user_id,))
    coins = cursor.fetchone()[0]
    conn.close()
    return coins >= cost


def deduct_coins(user_id, amount):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE players SET coins = coins - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()


def is_duplicate_card(user_id, card_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM inventories WHERE user_id = ? AND card_id = ?', (user_id, card_id))
    result = cursor.fetchone()[0]
    conn.close()
    return result > 0


#---------------------------------------------------------SELL UI COMPONENTS-------------------------------------------------------------------------------------


class MultiSellSelect(discord.ui.Select):
    def __init__(self, page_cards, selected_ids):
        options = []
        for card in page_cards:
            is_selected = card.card_id in selected_ids
            value = calculate_card_value(card)
            label = f"{'✅ ' if is_selected else ''}{card.name}"
            desc = f"Sell for: {value} coins | OVR: {card.overall}"
            options.append(discord.SelectOption(
                label=label[:100], 
                description=desc, 
                value=str(card.card_id),
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
        card_id = int(self.values[0])
        
        if card_id in view.selected_ids:
            view.selected_ids.remove(card_id)
        else:
            view.selected_ids.append(card_id)
        await view.update_display(interaction)


class SellPrevButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Previous", style=discord.ButtonStyle.primary, row=1)
    async def callback(self, interaction):
        self.view.current_page -= 1
        await self.view.update_display(interaction)


class SellNextButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Next", style=discord.ButtonStyle.primary, row=1)
    async def callback(self, interaction):
        self.view.current_page += 1
        await self.view.update_display(interaction)


class SellCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cancel", style=discord.ButtonStyle.danger, row=2)
    async def callback(self, interaction):
        await interaction.response.edit_message(content="❌ Sale cancelled.", embed=None, view=None)
        self.view.stop()


class MultiSellConfirmButton(discord.ui.Button):
    def __init__(self, disabled=True):
        style = discord.ButtonStyle.grey if disabled else discord.ButtonStyle.green
        label = "Select Cards..." if disabled else "Confirm Sale"
        super().__init__(label=label, style=style, emoji="💵", disabled=disabled, row=2)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        user_id = interaction.user.id
        
        total_coins = 0
        count = 0
        
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            for cid in view.selected_ids:
                cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (user_id, cid))
                if not cursor.fetchone(): 
                    continue
                
                card_obj = next((c for c in view.inventory if c.card_id == cid), None)
                if not card_obj: 
                    continue
                
                val = calculate_card_value(card_obj)
                total_coins += val
                count += 1
                
                cursor.execute('DELETE FROM inventories WHERE user_id = ? AND card_id = ?', (user_id, cid))
                cursor.execute('UPDATE players SET cards_sold = cards_sold + 1 WHERE user_id = ?', (user_id,))
            
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


class MultiSellView(discord.ui.View):
    def __init__(self, ctx, inventory, initial_ids=None):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.inventory = inventory
        self.selected_ids = initial_ids if initial_ids else []
        self.current_page = 0
        self.items_per_page = 20
        self.total_pages = max(1, (len(inventory) - 1) // self.items_per_page + 1)
        self.message = None
        
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
        
        page_cards = self.get_page_items()
        if page_cards:
            self.add_item(MultiSellSelect(page_cards, self.selected_ids))

        if self.current_page > 0:
            self.add_item(SellPrevButton())
        if self.current_page < self.total_pages - 1:
            self.add_item(SellNextButton())

        self.add_item(MultiSellConfirmButton(disabled=(len(self.selected_ids) == 0)))
        self.add_item(SellCancelButton())

    async def update_display(self, interaction):
        self.update_components()
        
        total_value = 0
        selected_count = len(self.selected_ids)
        
        if selected_count > 0:
            item_list = []
            for cid in self.selected_ids:
                card = next((c for c in self.inventory if c.card_id == cid), None)
                if card:
                    val = calculate_card_value(card)
                    total_value += val
                    item_list.append(f"• **{card.name}** ({card.overall}) — {val:,} coins")
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
        elif self.message:
            await self.message.edit(embed=embed, view=self)


#---------------------------------------------------------COG CLASS-------------------------------------------------------------------------------------


class Economy(commands.Cog):
    """Economy commands: coins, shop, buy, sell, packs, open"""
    
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='coins', description="Check your coin balance")
    async def coins(self, ctx, user: discord.User = None):
        """Check your or another user's coin balance"""
        if user is None:
            user = ctx.author

        ensure_player_exists(user.id, user.name)
        
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT coins FROM players WHERE user_id = ?', (user.id,))
        coins = cursor.fetchone()[0]
        conn.close()

        embed = discord.Embed(
            title=f"{user.name}'s Coins", 
            description=f"{user.mention} has **{coins:,}** coins.\nEarn more by selling cards or battling!", 
            color=discord.Color.gold()
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name='shop', description="View the pack shop")
    async def shop(self, ctx):
        """View available packs for purchase"""
        embed = discord.Embed(title="🛒 Shop", description="Available packs for purchase:\nUse `/buy <pack_id>` to buy.")
        for pack_id, pack_info in PACKS.items():
            if pack_info["buyable"]:
                embed.add_field(
                    name=f"{pack_info['display_name']} (ID: {pack_id})", 
                    value=f"Cost: **{pack_info['cost']:,}** coins", 
                    inline=False
                )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name='buy', description="Buy a pack with coins")
    async def buy(self, ctx, pack_id: int):
        """Purchase a pack from the shop"""
        user_id = ctx.author.id
        
        if pack_id not in PACKS:
            await ctx.send("❌ Invalid pack ID.")
            return

        pack = PACKS[pack_id]

        if not pack["buyable"]:
            await ctx.send("⛔ This pack cannot be purchased.")
            return

        cost = pack["cost"]

        if not has_sufficient_coins(user_id, cost):
            await ctx.send(f"❌ You need **{cost:,}** coins to buy this pack.")
            return
        
        deduct_coins(user_id, cost)
        add_pack_to_user(user_id, pack['name'])
        await ctx.send(f"✅ You bought a **{pack['display_name']}** for {cost:,} coins!")
        logger.info(f"User {ctx.author.name} bought a {pack['display_name']} pack.")

    @commands.hybrid_command(name='packs', description="View your unopened packs")
    async def packs(self, ctx):
        """View your pack inventory"""
        user_id = ctx.author.id
        user_packs = get_user_packs(user_id)
        
        if not user_packs:
            await ctx.send("You don't have any packs.")
            return
        
        embed = discord.Embed(title=f"📦 {ctx.author.name}'s Packs")
        has_packs = False
        for pack_id, pack in PACKS.items():
            pack_name = pack['name']
            pack_quantity = user_packs.get(pack_name, 0)
            if pack_quantity > 0:
                has_packs = True
                embed.add_field(
                    name=f"{pack['display_name']} (ID: {pack_id})", 
                    value=f"Quantity: **{pack_quantity}**", 
                    inline=False
                )
        
        if not has_packs:
            await ctx.send("You don't have any packs.")
        else:
            embed.set_footer(text="Use /open <pack_id> to open a pack!")
            await ctx.send(embed=embed)

    @commands.hybrid_command(name='open', description="Open a pack")
    async def open_pack(self, ctx, pack_id: int):
        """Open a pack from your inventory"""
        user_id = ctx.author.id

        if pack_id not in PACKS:
            await ctx.send("❌ Invalid pack ID.")
            return

        pack_name = PACKS[pack_id]["name"]

        user_packs = get_user_packs(user_id)
        if not user_packs or user_packs.get(pack_name, 0) <= 0:
            await ctx.send("❌ You don't own this pack.")
            return

        # Open the pack based on its type
        if pack_id == 1:
            card_obtained = await self.open_rare_player_pack(ctx, user_id)
        elif pack_id == 2:
            card_obtained = await self.open_icon_pack(ctx, user_id)
        elif pack_id == 3:
            card_obtained = await self.open_hero_pack(ctx, user_id)
        elif pack_id == 4:
            card_obtained = await self.open_tester_pack(ctx, user_id)

        remove_pack_from_user(user_id, pack_name)
        await ctx.send(f"🎉 You opened a **{PACKS[pack_id]['display_name']}** and got **{card_obtained}**!")

    async def open_rare_player_pack(self, ctx, user_id):
        """Open a rare player pack"""
        conn = get_connection()
        cursor = conn.cursor()

        card = None
        attempts = 0
        while attempts < 100:
            card_types = ['Standard', 'Other']
            probabilities = [0.8, 0.2]
            chosen_type = random.choices(card_types, probabilities)[0]

            if chosen_type == 'Standard':
                cursor.execute("SELECT card_id, name, card_rarity, card_type, attack, defense, speed, overall, league, nation, image_path FROM cards WHERE card_type = 'Standard' AND overall > 85 ORDER BY RANDOM() LIMIT 1")
            else:
                cursor.execute("SELECT card_id, name, card_rarity, card_type, attack, defense, speed, overall, league, nation, image_path FROM cards WHERE card_type != 'Standard' AND overall > 85 ORDER BY RANDOM() LIMIT 1")

            card = cursor.fetchone()
            if card and not is_duplicate_card(user_id, card[0]):
                break
            attempts += 1

        if not card:
            conn.close()
            return "No available cards"

        card_id, name, rarity, card_type, attack, defense, speed, overall, league, nation, image_path = card

        cursor.execute('UPDATE cards SET copies = copies + 1 WHERE card_id = ?', (card_id,))
        conn.commit()
        add_card_to_inventory(user_id, card_id)
        conn.close()

        embed = discord.Embed(title="🎁 Pack Opened!", description=f"**{name}**", color=discord.Color.gold())
        embed.set_image(url=f"attachment://{image_path.split('/')[-1]}")
        embed.add_field(name="Rarity", value=rarity, inline=True)
        embed.add_field(name="Type", value=card_type, inline=True)
        embed.add_field(name="Overall", value=overall, inline=True)
        embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
        file = discord.File(image_path, filename=image_path.split('/')[-1])
        await ctx.send(embed=embed, file=file)
        return name

    async def open_icon_pack(self, ctx, user_id):
        """Open an icon pack"""
        conn = get_connection()
        cursor = conn.cursor()

        card = None
        attempts = 0
        while attempts < 100:
            cursor.execute("SELECT card_id, name, card_rarity, card_type, attack, defense, speed, overall, league, nation, image_path FROM cards WHERE card_type = 'Icon' ORDER BY RANDOM() LIMIT 1")
            card = cursor.fetchone()
            if card and not is_duplicate_card(user_id, card[0]):
                break
            attempts += 1

        if not card:
            conn.close()
            return "No available icons"

        card_id, name, rarity, card_type, attack, defense, speed, overall, league, nation, image_path = card

        cursor.execute('UPDATE cards SET copies = copies + 1 WHERE card_id = ?', (card_id,))
        conn.commit()
        add_card_to_inventory(user_id, card_id)
        conn.close()

        embed = discord.Embed(title="⭐ Icon Pack Opened!", description=f"**{name}**", color=discord.Color.gold())
        embed.set_image(url=f"attachment://{image_path.split('/')[-1]}")
        embed.add_field(name="Overall", value=overall, inline=True)
        file = discord.File(image_path, filename=image_path.split('/')[-1])
        await ctx.send(embed=embed, file=file)
        return name

    async def open_hero_pack(self, ctx, user_id):
        """Open a hero pack"""
        conn = get_connection()
        cursor = conn.cursor()

        card = None
        attempts = 0
        while attempts < 100:
            cursor.execute("SELECT card_id, name, card_rarity, card_type, attack, defense, speed, overall, league, nation, image_path FROM cards WHERE card_type = 'Hero' ORDER BY RANDOM() LIMIT 1")
            card = cursor.fetchone()
            if card and not is_duplicate_card(user_id, card[0]):
                break
            attempts += 1

        if not card:
            conn.close()
            return "No available heroes"

        card_id, name, rarity, card_type, attack, defense, speed, overall, league, nation, image_path = card

        cursor.execute('UPDATE cards SET copies = copies + 1 WHERE card_id = ?', (card_id,))
        conn.commit()
        add_card_to_inventory(user_id, card_id)
        conn.close()

        embed = discord.Embed(title="🦸 Hero Pack Opened!", description=f"**{name}**", color=discord.Color.gold())
        embed.set_image(url=f"attachment://{image_path.split('/')[-1]}")
        embed.add_field(name="Overall", value=overall, inline=True)
        file = discord.File(image_path, filename=image_path.split('/')[-1])
        await ctx.send(embed=embed, file=file)
        return name

    async def open_tester_pack(self, ctx, user_id):
        """Open a tester pack (1 Icon + 4 high OVR cards)"""
        conn = get_connection()
        cursor = conn.cursor()

        # Get 1 icon
        icon_card = None
        attempts = 0
        while attempts < 100:
            cursor.execute("SELECT card_id, name FROM cards WHERE card_type = 'Icon' ORDER BY RANDOM() LIMIT 1")
            icon_card = cursor.fetchone()
            if icon_card and not is_duplicate_card(user_id, icon_card[0]):
                break
            attempts += 1

        if icon_card:
            cursor.execute('UPDATE cards SET copies = copies + 1 WHERE card_id = ?', (icon_card[0],))
            add_card_to_inventory(user_id, icon_card[0])

        # Get 4 high overall cards
        high_cards = []
        attempts = 0
        while len(high_cards) < 4 and attempts < 100:
            cursor.execute("SELECT card_id, name FROM cards WHERE overall > 85 ORDER BY RANDOM() LIMIT 1")
            card = cursor.fetchone()
            if card and not is_duplicate_card(user_id, card[0]):
                cursor.execute('UPDATE cards SET copies = copies + 1 WHERE card_id = ?', (card[0],))
                add_card_to_inventory(user_id, card[0])
                high_cards.append(card[1])
            attempts += 1

        conn.commit()
        conn.close()

        cards_obtained = [icon_card[1]] if icon_card else []
        cards_obtained.extend(high_cards)
        return ", ".join(cards_obtained) if cards_obtained else "No cards available"

    @commands.hybrid_command(name='sell', description="Sell cards for coins")
    async def sell(self, ctx, *, items: str = None):
        """Open the transfer market to sell cards"""
        ensure_player_exists(ctx.author.id, ctx.author.name)
        
        inventory, _ = get_player_inventory(ctx.author.id)
        if not inventory:
            return await ctx.send("You have no cards to sell!")

        # Pre-select cards if IDs/names provided
        pre_selected_ids = []
        warnings = []
        
        if items:
            card_lookup = {card.name.lower(): card.card_id for card in inventory}
            inputs = [i.strip() for i in items.replace(',', ' ').split()]
            
            for item in inputs:
                if item.isdigit():
                    target_id = int(item)
                    if any(c.card_id == target_id for c in inventory):
                        pre_selected_ids.append(target_id)
                    else:
                        warnings.append(f"You don't own ID {target_id}")
                elif item.lower() in card_lookup:
                    pre_selected_ids.append(card_lookup[item.lower()])
                else:
                    warnings.append(f"Could not find card '{item}'")

        view = MultiSellView(ctx, inventory, initial_ids=pre_selected_ids)
        
        embed = discord.Embed(title="📉 Transfer Market", description="Initializing...", color=discord.Color.blue())
        embed.add_field(name="Loading...", value="Preparing your sell offer...", inline=False)
        
        if warnings:
            embed.set_footer(text=f"⚠️ Warning: {', '.join(warnings[:3])}")
        
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg
        
        await view.update_display(ctx.interaction if ctx.interaction else None)


async def setup(bot):
    await bot.add_cog(Economy(bot))
