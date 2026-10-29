"""
Leaderboards Cog for FutBot
Leaderboard views, stats, and titles
"""
import discord
from discord.ext import commands
import logging

from utils.database import get_connection, ensure_player_exists, get_player_inventory

logger = logging.getLogger(__name__)


#---------------------------------------------------------HELPER FUNCTIONS-------------------------------------------------------------------------------------


async def build_leaderboard_embed(guild, author_id, stat_column, stat_name, scope):
    """Helper to generate the leaderboard Embed."""
    scope = scope.title()  # "Server" or "Global"
    
    conn = get_connection()
    cursor = conn.cursor()

    # Fetch ALL players sorted by the stat
    cursor.execute(f'SELECT user_id, name, {stat_column} FROM players ORDER BY {stat_column} DESC')
    all_rows = cursor.fetchall()
    conn.close()

    leaderboard_data = []
    user_rank_info = None
    rank_counter = 1

    # Filter Logic
    if scope == 'Server':
        if not guild:
            return discord.Embed(title="Error", description="Server leaderboard cannot be used in DMs.", color=discord.Color.red())

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
                
    else:  # Global
        for row in all_rows:
            u_id, u_name, u_stat = row
            
            if len(leaderboard_data) < 10:
                leaderboard_data.append((rank_counter, u_name, u_stat))
            
            if u_id == author_id:
                user_rank_info = (rank_counter, u_name, u_stat)
            
            rank_counter += 1
            
            if len(leaderboard_data) == 10 and user_rank_info:
                break

    # Build Embed
    icon = "🌍" if scope == "Global" else "🏰"
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


#---------------------------------------------------------UI COMPONENTS-------------------------------------------------------------------------------------


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
        view.current_stat_key = self.values[0]
        
        names = {
            "battles_won": "Battles Won",
            "battles_played": "Battles Played",
            "rounds_won": "Rounds Won",
            "rounds_played": "Rounds Played",
            "coins": "Coins",
            "cards_dropped": "Cards Dropped"
        }
        view.current_stat_name = names.get(view.current_stat_key, "Stat")

        embed = await build_leaderboard_embed(
            interaction.guild, 
            interaction.user.id, 
            view.current_stat_key, 
            view.current_stat_name, 
            view.scope
        )
        
        await interaction.response.edit_message(embed=embed, view=view)


class ScopeButton(discord.ui.Button):
    def __init__(self, current_scope):
        label = "Show Global" if current_scope == "Server" else "Show Server"
        emoji = "🌍" if current_scope == "Server" else "🏰"
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if view.scope == "Server":
            view.scope = "Global"
            self.label = "Show Server"
            self.emoji = "🏰"
        else:
            view.scope = "Server"
            self.label = "Show Global"
            self.emoji = "🌍"
            
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
        
        self.add_item(LeaderboardSelect())
        self.add_item(ScopeButton(scope))


#---------------------------------------------------------TITLE UI-------------------------------------------------------------------------------------


class TitleDropdown(discord.ui.Select):
    def __init__(self, titles, user_id):
        options = [discord.SelectOption(label=title, value=str(achievement_id)) for title, achievement_id in titles]
        super().__init__(placeholder="Choose a title...", options=options)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        achievement_id = int(self.values[0])
        
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT title FROM achievements WHERE achievement_id = ?', (achievement_id,))
        title = cursor.fetchone()[0]
        
        cursor.execute('UPDATE players SET display_title = ? WHERE user_id = ?', (title, self.user_id))
        conn.commit()
        conn.close()

        await interaction.response.send_message(f"Your title has been set to: {title}", ephemeral=True)
        logger.info(f'{interaction.user.name} set their title to {title}')


class TitleDropdownView(discord.ui.View):
    def __init__(self, titles, user_id):
        super().__init__(timeout=60)
        self.add_item(TitleDropdown(titles, user_id))


#---------------------------------------------------------COG CLASS-------------------------------------------------------------------------------------


class Leaderboards(commands.Cog):
    """Leaderboard and stats commands"""
    
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='leaderboard', aliases=['lb'], description="View game rankings")
    async def leaderboard(self, ctx):
        """View the leaderboard with various rankings"""
        embed = await build_leaderboard_embed(ctx.guild, ctx.author.id, 'battles_won', 'Battles Won', 'Server')
        view = LeaderboardView('Server')
        await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(name='richest', description="View the coins leaderboard")
    async def richest(self, ctx):
        """View the richest players leaderboard"""
        embed = await build_leaderboard_embed(ctx.guild, ctx.author.id, 'coins', 'Coins', 'Server')
        view = LeaderboardView('Server', initial_stat_key='coins', initial_stat_name='Coins')
        await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(name='stats', description="View player stats")
    async def stats(self, ctx, member: discord.Member = None):
        """View stats for yourself or another player"""
        if member is None:
            member = ctx.author
        ensure_player_exists(member.id, member.name)
        
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM players WHERE user_id = ?', (member.id,))
        player = cursor.fetchone()
        conn.close()
        
        if player:
            embed = discord.Embed(title=f"**{player[1]}'s Stats**")

            display_title = player[16] if len(player) > 16 and player[16] else "No Title Set"
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

    @commands.hybrid_command(name='set_title', description="Equip a title you have unlocked")
    async def set_title(self, ctx):
        """Set your display title from earned achievements"""
        ensure_player_exists(ctx.author.id, ctx.author.name)
        
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('''
        SELECT achievements.title, achievements.achievement_id
        FROM achievements
        JOIN user_achievements ON achievements.achievement_id = user_achievements.achievement_id
        WHERE user_achievements.user_id = ?
        ''', (ctx.author.id,))
        titles = cursor.fetchall()
        conn.close()

        if titles:
            view = TitleDropdownView(titles, ctx.author.id)
            await ctx.send("Choose a title from the dropdown menu below:", view=view)
            logger.info(f'{ctx.author.name} is setting a title')
        else:
            await ctx.send("You have no titles to set.")
    @commands.hybrid_command(name='titles', description="View achievements")
    async def titles(self, ctx, member: discord.Member = None):
        """View all achievements or a user's unlocked achievements"""
        conn = get_connection()
        cursor = conn.cursor()
        
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
        
        conn.close()
        await ctx.send(embed=embed)
        logger.info(f'{ctx.author.name} viewed titles')


async def setup(bot):
    await bot.add_cog(Leaderboards(bot))
