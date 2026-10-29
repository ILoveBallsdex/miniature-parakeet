"""
Misc Cog for FutBot
Help, About, Changelog, Facts, Suggestions commands
"""
import discord
from discord.ext import commands
from discord import app_commands
import random
import logging

from utils.database import SUGGESTION_CHANNEL_ID

logger = logging.getLogger(__name__)


#---------------------------------------------------------BOT METADATA-------------------------------------------------------------------------------------

# Bot metadata
BOT_VERSION = "1.4.7"
CREATOR = "noobmaster"
DESCRIPTION = "This bot is designed to give maximum resemblance to Match Attax card games. With this bot, you can collect football player cards and battle with your friends using your favourite players."

CHANGELOG_DATA = [
    "1.4.8 - Daily streak system with escalating rewards!",
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

FACTS_LIST = [
    "England's last FIFA World Cup victory was in 1966, when they triumphed at Wembley Stadium, a moment the fans nostalgically reminisce about, since when they began their famous chant.",
    "Football is one of the best things to come out of France after their food, with their iconic 1998 World Cup victory on home soil showcasing the team's flair and elegance.",
    "Brazil holds the record for the most FIFA World Cup wins, with a total of five championships, known for their beautiful style of play that dazzles spectators.",
    "The Netherlands, known for their 'Total Football' style, reached the FIFA World Cup final three times but have yet to win the tournament, earning the nickname for their graceful and flying playstyle.",
    "Italy is renowned for its strong defensive tactics and has won the FIFA World Cup four times, with their latest victory in 2006, a victory almost as controversial as certain tropical toppings on pizza.",
    "Germany has a storied football history, having won the FIFA World Cup four times, and is known for their consistent performance in international tournaments, embodying the spirit of their beloved 'Fußball'."
]


#---------------------------------------------------------UI COMPONENTS-------------------------------------------------------------------------------------


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
            embed.add_field(name="Earning", value="`/daily` - Claim free cards + coins (18h Cooldown).\n`/drop` - Drop a card in chat (30m Cooldown).", inline=False)
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


class ChangelogView(discord.ui.View):
    def __init__(self, data):
        super().__init__(timeout=120)
        self.data = data
        self.current_page = 0
        self.items_per_page = 10
        self.total_pages = max(1, (len(data) - 1) // self.items_per_page + 1)
        self.update_buttons()

    def get_embed(self):
        start = self.current_page * self.items_per_page
        end = start + self.items_per_page
        page_items = self.data[start:end]
        
        text_content = "\n".join(page_items)
        
        embed = discord.Embed(title="📜 Bot Changelog", color=discord.Color.blue())
        embed.description = f"```\n{text_content}\n```"
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} | Version {BOT_VERSION}")
        return embed

    def update_buttons(self):
        self.children[0].disabled = (self.current_page == 0)
        self.children[1].disabled = (self.current_page == self.total_pages - 1)

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


#---------------------------------------------------------COG CLASS-------------------------------------------------------------------------------------


class Misc(commands.Cog):
    """Miscellaneous commands - Help, About, Facts, Suggestions"""
    
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='help', description="Show the help menu")
    async def help_command(self, ctx):
        embed = discord.Embed(title="⚽ FutBot Help Center", description="Welcome to the ultimate football card battle bot!", color=discord.Color.gold())
        embed.add_field(name="Getting Started", value="Use the dropdown menu below to browse specific command categories.", inline=False)
        embed.add_field(name="Quick Start", value="`/get_starter_pack` - Get your first cards\n`/daily` - Claim free rewards\n`/build_deck` - Create a team\n`/battle` - Fight players", inline=False)
        
        view = HelpView()
        await ctx.send(embed=embed, view=view)

    #---------------------------------------------------------BOT INFO-------------------------------------------------------------------------------------

    @commands.hybrid_command(name='about', description="About this bot")
    async def about(self, ctx):
        embed = discord.Embed(title="About This Bot", color=discord.Color.blue())
        embed.add_field(name="Version", value=f"```{BOT_VERSION}```", inline=True)
        embed.add_field(name="Creator", value=CREATOR, inline=True)
        embed.add_field(name="Description", value=DESCRIPTION, inline=False)
        embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
        
        await ctx.send(embed=embed)

    @commands.hybrid_command(name='version', description="Check bot version")
    async def version(self, ctx):
        embed = discord.Embed(title="Bot Version")
        embed.add_field(name="Version", value=f"```{BOT_VERSION}```", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name='changelog', description="Check recent changes")
    async def changelog(self, ctx):
        view = ChangelogView(CHANGELOG_DATA)
        await ctx.send(embed=view.get_embed(), view=view)

    #---------------------------------------------------------SUGGESTIONS-------------------------------------------------------------------------------------

    @commands.hybrid_command(name='suggest', description="Submit a suggestion")
    async def suggest(self, ctx, *, suggestion: str):
        suggestion_channel = self.bot.get_channel(SUGGESTION_CHANNEL_ID)
        
        if suggestion_channel:
            embed = discord.Embed(title="New Suggestion", description=suggestion, color=0x0000ff)
            embed.add_field(name="Suggested by", value=ctx.author.mention, inline=False)
            await suggestion_channel.send(embed=embed)
            await ctx.send("Thank you for your suggestion! It has been forwarded to the team.")
        else:
            await ctx.send("Sorry, I couldn't find the suggestion channel. Please try again later.")

    #---------------------------------------------------------FACTS-------------------------------------------------------------------------------------

    @commands.hybrid_command(name='facts', description="Get a random football fact")
    async def facts(self, ctx):
        fact = random.choice(FACTS_LIST)
        embed = discord.Embed(title="Football Fact", description=fact, color=discord.Color.blue())
        await ctx.send(embed=embed)
        logger.info(f'{ctx.author.name} used the facts command and received: {fact}')


async def setup(bot):
    await bot.add_cog(Misc(bot))
