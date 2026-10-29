"""
Inventory Cog for FutBot
Inventory viewing, catalog, and related commands
"""
import discord
from discord.ext import commands
import logging

from utils.database import get_connection, ensure_player_exists, get_player_inventory
from utils.models import fetch_all_cards

logger = logging.getLogger(__name__)


#---------------------------------------------------------UI COMPONENTS-------------------------------------------------------------------------------------


class SortSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Sort by Overall", value="overall", emoji="⭐"),
            discord.SelectOption(label="Sort by Pace", value="speed", emoji="⚡"),
            discord.SelectOption(label="Sort by Attack", value="attack", emoji="⚔️"),
            discord.SelectOption(label="Sort by Defense", value="defense", emoji="🛡️"),
            discord.SelectOption(label="Sort by Rarity", value="rarity", emoji="💎"),
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
            view.data.sort(key=lambda x: getattr(x[0], 'wishlist_count', 0), reverse=True)

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
        self.inv_view.filter_name = self.name_input.value.lower() if self.name_input.value else None
        self.inv_view.filter_rating = int(self.min_rating_input.value) if self.min_rating_input.value.isdigit() else None
        self.inv_view.filter_rarity = self.rarity_input.value.lower() if self.rarity_input.value else None
        self.inv_view.filter_type = self.type_input.value.lower() if self.type_input.value else None
        
        self.inv_view.apply_filters()
        self.inv_view.data.sort(key=lambda x: x[0].overall, reverse=True)
        self.inv_view.sort_label = "Overall"
        
        dropdown = self.inv_view.children[0]
        for opt in dropdown.options:
            opt.default = (opt.value == "overall")
        
        embed = self.inv_view.update_view()
        self.inv_view.update_buttons()
        await interaction.response.edit_message(embed=embed, view=self.inv_view)


class PreviousButton(discord.ui.Button):
    def __init__(self):
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
        super().__init__(label="Filter", style=discord.ButtonStyle.secondary, emoji="🔍", row=1)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(FilterModal(self.view))


class ResetFilterButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Reset", style=discord.ButtonStyle.danger, emoji="✖️", row=1)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        view.data = view.full_data[:]
        
        view.filter_name = None
        view.filter_rating = None
        view.filter_rarity = None
        view.filter_type = None
        view.current_page = 0
        
        view.children[0].options[0].default = True 
        view.data.sort(key=lambda x: x[0].overall, reverse=True)
        view.sort_label = "Overall"

        embed = view.update_view()
        view.update_buttons()
        await interaction.response.edit_message(embed=embed, view=view)


class NextButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label='Next', style=discord.ButtonStyle.primary, custom_id='next', row=1)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if view.current_page < view.total_pages - 1:
            view.current_page += 1
            embed = view.update_view()
            view.update_buttons()
            await interaction.response.edit_message(embed=embed, view=view)


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
            wishlist_count = getattr(card, 'wishlist_count', 0)
            line = (
                f"**{card.name} (ID: {card.card_id})** - "
                f"❤️ {wishlist_count}, "
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


#---------------------------------------------------------CATALOG VIEW-------------------------------------------------------------------------------------


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
            wishlist_count = getattr(card, 'wishlist_count', 0)
            line = (
                f"**{card.name} (ID: {card.card_id})** - "
                f"❤️ {wishlist_count}, "
                f"Copies: {card.copies}, "
                f"Overall: {card.overall}, "
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
            title=f"📚 Card Catalog ({' | '.join(status)})", 
            description=description, 
            color=discord.Color.purple()
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


#---------------------------------------------------------COG CLASS-------------------------------------------------------------------------------------


class Inventory(commands.Cog):
    """Inventory and catalog commands"""
    
    def __init__(self, bot):
        self.bot = bot
        self.all_cards = fetch_all_cards()

    @commands.hybrid_command(name='inventory', aliases=['inv'], description="View your card collection")
    async def view_inventory(self, ctx, user: discord.User = None, search: str = None):
        """View your or another user's card inventory"""
        target_user = user or ctx.author
        ensure_player_exists(target_user.id, target_user.name)
        
        inventory, editions = get_player_inventory(target_user.id)
        
        if not inventory:
            return await ctx.send(f"{target_user.name} has no cards in their inventory.")

        view = InventoryView(inventory, target_user, editions, ctx)

        if search:
            search = search.lower()
            view.data = [item for item in view.full_data if search in item[0].name.lower()]
            view.filter_label = f"Name: {search}"
            
            if not view.data:
                return await ctx.send(f"No cards found matching '{search}' in {target_user.name}'s inventory.")

        embed = view.update_view()
        view.update_buttons()
        view.message = await ctx.send(embed=embed, view=view)
        logger.info(f'{ctx.author.name} viewed inventory')

    @commands.hybrid_command(name='catalog', description="View all available cards")
    async def catalog(self, ctx, search: str = None):
        """View the complete card catalog"""
        cards = self.all_cards
        
        if not cards:
            return await ctx.send("No cards available in the catalog.")

        view = CatalogView(cards, ctx)

        if search:
            search = search.lower()
            view.data = [item for item in view.full_data if search in item[0].name.lower()]
            
            if not view.data:
                return await ctx.send(f"No cards found matching '{search}'.")

        embed = view.update_view()
        view.update_buttons()
        await ctx.send(embed=embed, view=view)
        logger.info(f'{ctx.author.name} viewed catalog')


async def setup(bot):
    await bot.add_cog(Inventory(bot))
