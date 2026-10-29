"""
Battles Cog for FutBot
Battle system, deck management, and deck builder
"""
import discord
from discord.ext import commands
import sqlite3
import logging
import io

try:
    from PIL import Image
except ImportError:
    Image = None

from utils.database import get_connection, ensure_player_exists, get_player_inventory
from utils.models import Card, get_card_by_id

logger = logging.getLogger(__name__)


#---------------------------------------------------------HELPER FUNCTIONS-------------------------------------------------------------------------------------


def add_deck(user_id, deck_name, card_ids):
    """Add a deck to the database"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT deck_name FROM decks WHERE user_id = ? AND deck_name = ?', (user_id, deck_name))
    if cursor.fetchone():
        conn.close()
        raise ValueError(f"Deck '{deck_name}' already exists.")
    
    cards_str = ','.join(map(str, card_ids))
    cursor.execute('INSERT INTO decks (user_id, deck_name, cards) VALUES (?, ?, ?)', (user_id, deck_name, cards_str))
    conn.commit()
    conn.close()


def get_deck(user_id, deck_name):
    """Get deck cards by name"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT cards FROM decks WHERE user_id = ? AND deck_name = ?', (user_id, deck_name))
    result = cursor.fetchone()
    
    if result is None:
        conn.close()
        return None
    
    card_ids = list(map(int, result[0].split(',')))
    cards = []
    for card_id in card_ids:
        cursor.execute('SELECT * FROM cards WHERE card_id = ?', (card_id,))
        card_data = cursor.fetchone()
        if card_data:
            cards.append(Card(*card_data[:14]))
    
    conn.close()
    return cards


def generate_lineup_image(deck_cards):
    """Generate a visual lineup image for a deck"""
    if Image is None:
        return None
    
    try:
        bg = Image.open("pitch.png").convert("RGBA")
    except FileNotFoundError:
        bg = Image.new('RGBA', (1080, 1350), (0, 128, 0, 255))

    bg = bg.resize((1080, 1350), Image.Resampling.LANCZOS)
    bg_width, bg_height = bg.size

    # Sort for 2-1-2 Formation
    pool = deck_cards[:]
    pool.sort(key=lambda x: x.attack, reverse=True)
    attackers = pool[:2]
    for card in attackers: pool.remove(card)

    pool.sort(key=lambda x: x.defense, reverse=True)
    defenders = pool[:2]
    for card in defenders: pool.remove(card)

    midfielder = pool[0] if pool else None
    if midfielder:
        sorted_lineup = [attackers[0], attackers[1], midfielder, defenders[0], defenders[1]]
    else:
        sorted_lineup = deck_cards[:5]

    positions = [
        (0.28, 0.20), (0.72, 0.20),  # Attackers
        (0.50, 0.50),                 # Midfielder
        (0.28, 0.80), (0.72, 0.80)   # Defenders
    ]

    for i, card in enumerate(sorted_lineup):
        try:
            card_img = Image.open(card.image_path).convert("RGBA")
            target_width = int(bg_width * 0.35)
            aspect_ratio = card_img.height / card_img.width
            target_height = int(target_width * aspect_ratio)
            card_img = card_img.resize((target_width, target_height), Image.Resampling.LANCZOS)

            pos_x_percent, pos_y_percent = positions[i]
            x = int((bg_width * pos_x_percent) - (target_width / 2))
            y = int((bg_height * pos_y_percent) - (target_height / 2))
            bg.paste(card_img, (x, y), card_img)
        except Exception as e:
            logger.error(f"Error loading image for card {card.name}: {e}")
            continue

    buffer = io.BytesIO()
    bg.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


#---------------------------------------------------------BATTLE CLASS-------------------------------------------------------------------------------------


class Battle:
    def __init__(self, ctx, player1, player2):
        self.ctx = ctx
        self.message = None
        self.player1 = player1
        self.player2 = player2
        
        self.player1_deck = []
        self.player2_deck = []
        self.player1_used_cards = []
        self.player2_used_cards = []
        
        self.player1_wins = 0
        self.player2_wins = 0
        self.draws = 0
        self.round = 1
        
        self.round_resolved = False
        self.last_result_text = ""
        self.last_winner = None
        
        self.turn_player = player1
        self.p1_action = None
        self.p2_action = None
        self.p1_card = None
        self.p2_card = None
        
        self.draw_offers = set()
        self.phase = "SETUP"

    async def start(self):
        embed = discord.Embed(title="⚔️ Battle Arena ⚔️", description="Both players must select their decks to begin.")
        embed.add_field(name=self.player1.name, value="❌ Deck Not Selected", inline=True)
        embed.add_field(name=self.player2.name, value="❌ Deck Not Selected", inline=True)
        
        view = SetupView(self)
        self.message = await self.ctx.send(embed=embed, view=view)

    async def request_surrender(self, interaction):
        if interaction.user.id not in [self.player1.id, self.player2.id]:
            return await interaction.response.send_message("Only battlers can surrender!", ephemeral=True)
        view = SurrenderConfirmView(self, interaction.user)
        await interaction.response.send_message("Are you sure you want to surrender?", view=view, ephemeral=True)

    async def confirm_surrender(self, interaction, loser):
        winner = self.player1 if loser == self.player2 else self.player2
        
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('UPDATE players SET battles_played = battles_played + 1, battles_won = battles_won + 1 WHERE user_id = ?', (winner.id,))
            cursor.execute('UPDATE players SET battles_played = battles_played + 1, battles_lost = battles_lost + 1 WHERE user_id = ?', (loser.id,))
            cursor.execute('UPDATE players SET coins = coins + 200 WHERE user_id = ?', (winner.id,))
            cursor.execute('UPDATE players SET coins = coins + 100 WHERE user_id = ?', (loser.id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Surrender DB Error: {e}")
        finally:
            conn.close()
        
        embed = discord.Embed(title="🏳️ Battle Surrendered", color=discord.Color.red())
        embed.add_field(name="Result", value=f"**{winner.name}** wins! {loser.name} has surrendered.", inline=False)
        embed.add_field(name="Rewards", value=f"{winner.name}: +200 Coins\n{loser.name}: +100 Coins", inline=False)
        await self.message.edit(embed=embed, view=None)
        
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(content="🏳️ You surrendered.", view=None)
        except:
            pass

    async def request_draw(self, interaction):
        user = interaction.user
        if user.id not in [self.player1.id, self.player2.id]:
            return await interaction.response.send_message("Not your battle!", ephemeral=True)

        if user.id in self.draw_offers:
            return await interaction.response.send_message("You already offered a draw.", ephemeral=True)

        self.draw_offers.add(user.id)

        if len(self.draw_offers) >= 2:
            return await self.confirm_draw(interaction)
        
        opponent = self.player2 if user == self.player1 else self.player1
        await interaction.response.send_message(f"🤝 Draw offer sent to {opponent.name}!", ephemeral=True)
        await self.update_game_state()

    async def confirm_draw(self, interaction):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('UPDATE players SET battles_played = battles_played + 1, battles_drawn = battles_drawn + 1 WHERE user_id = ?', (self.player1.id,))
            cursor.execute('UPDATE players SET battles_played = battles_played + 1, battles_drawn = battles_drawn + 1 WHERE user_id = ?', (self.player2.id,))
            cursor.execute('UPDATE players SET coins = coins + 100 WHERE user_id = ?', (self.player1.id,))
            cursor.execute('UPDATE players SET coins = coins + 100 WHERE user_id = ?', (self.player2.id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Draw DB Error: {e}")
        finally:
            conn.close()

        embed = discord.Embed(title="🤝 Battle Drawn", description="Both players agreed to a draw.", color=discord.Color.greyple())
        embed.add_field(name="Rewards", value="Both players received +100 Coins", inline=False)
        await self.message.edit(embed=embed, view=None)
        
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except:
            pass

    def get_valid_deck(self, player):
        if player.id == self.player1.id:
            full_deck = self.player1_deck
            used_cards = self.player1_used_cards
        else:
            full_deck = self.player2_deck
            used_cards = self.player2_used_cards
        
        used_ids = {card.card_id for card in used_cards}
        return [card for card in full_deck if card.card_id not in used_ids]

    async def update_game_state(self, interaction=None):
        if self.phase == "SETUP":
            if self.player1_deck and self.player2_deck:
                self.phase = "ACTION"
                await self.update_game_state(interaction)
            return

        if self.phase == "ACTION":
            embed = discord.Embed(title=f"⚔️ Round {self.round} | Action Phase", color=discord.Color.blue())
            embed.add_field(name="Score", value=f"{self.player1.name}: {self.player1_wins} | {self.player2.name}: {self.player2_wins} | Draws: {self.draws}", inline=False)
            embed.add_field(name="Current Turn", value=f"It is **{self.turn_player.name}'s** turn to choose the tactic.", inline=False)
            
            view = ActionView(self, self.turn_player)
            
            if interaction and not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=view)
            else:
                await self.message.edit(embed=embed, view=view)

        elif self.phase == "CARD_SELECT":
            p1_status = "✅ Selected" if self.p1_card else "⏳ Waiting..."
            p2_status = "✅ Selected" if self.p2_card else "⏳ Waiting..."

            embed = discord.Embed(title=f"⚔️ Round {self.round} | Card Phase", color=discord.Color.gold())
            embed.add_field(name="Tactics", value=f"{self.player1.name}: **{self.p1_action.upper()}**\n{self.player2.name}: **{self.p2_action.upper()}**", inline=False)
            embed.add_field(name="Card Selection", value=f"**{self.player1.name}:** {p1_status}\n**{self.player2.name}:** {p2_status}", inline=False)
            
            view = CardSelectView(self)
            
            if interaction and not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=view)
            else:
                await self.message.edit(embed=embed, view=view)

        elif self.phase == "RESULT":
            if not self.round_resolved:
                result_text, winner = self.calculate_winner()
                self.update_round_db_stats(winner)
                self.player1_used_cards.append(self.p1_card)
                self.player2_used_cards.append(self.p2_card)
                self.last_result_text = result_text
                self.last_winner = winner
                self.round_resolved = True
            else:
                result_text = self.last_result_text
                winner = self.last_winner

            embed = discord.Embed(title=f"⚔️ Round {self.round} Result", description=result_text, color=discord.Color.purple())
            embed.add_field(name=f"{self.player1.name} ({self.p1_action})", value=f"**{self.p1_card.name}**\n⭐ {self.p1_card.overall}\n⚔️ {self.p1_card.attack} | 🛡️ {self.p1_card.defense} | ⚡ {self.p1_card.speed}", inline=True)
            embed.add_field(name=f"{self.player2.name} ({self.p2_action})", value=f"**{self.p2_card.name}**\n⭐ {self.p2_card.overall}\n⚔️ {self.p2_card.attack} | 🛡️ {self.p2_card.defense} | ⚡ {self.p2_card.speed}", inline=True)
            
            if self.player1_wins >= 3 or self.player2_wins >= 3 or self.round == 5:
                self.phase = "GAME_OVER"
                await self.end_game(interaction, embed)
            else:
                view = NextRoundView(self)
                if interaction and not interaction.response.is_done():
                    await interaction.response.edit_message(embed=embed, view=view)
                else:
                    await self.message.edit(embed=embed, view=view)

    def calculate_winner(self):
        p1_val, p2_val, stat_name = 0, 0, "Stat"
        if self.p1_action == 'attack' and self.p2_action == 'defense':
            p1_val, p2_val, stat_name = self.p1_card.attack, self.p2_card.defense, "Attack vs Defense"
        elif self.p1_action == 'defense' and self.p2_action == 'attack':
            p1_val, p2_val, stat_name = self.p1_card.defense, self.p2_card.attack, "Defense vs Attack"
        else:
            p1_val, p2_val, stat_name = self.p1_card.speed, self.p2_card.speed, "Speed vs Speed"

        if p1_val > p2_val:
            self.player1_wins += 1
            return f"🏆 **{self.player1.name}** Wins with {stat_name}!", self.player1
        elif p2_val > p1_val:
            self.player2_wins += 1
            return f"🏆 **{self.player2.name}** Wins with {stat_name}!", self.player2
        else:
            if self.p1_card.overall > self.p2_card.overall:
                self.player1_wins += 1
                return f"⚠️ Stats Draw! **{self.player1.name}** wins on Overall!", self.player1
            elif self.p2_card.overall > self.p1_card.overall:
                self.player2_wins += 1
                return f"⚠️ Stats Draw! **{self.player2.name}** wins on Overall!", self.player2
            else:
                self.draws += 1
                return f"🤝 **It's a Draw!**", None

    def update_round_db_stats(self, winner):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('UPDATE players SET rounds_played = rounds_played + 1 WHERE user_id = ?', (self.player1.id,))
            cursor.execute('UPDATE players SET rounds_played = rounds_played + 1 WHERE user_id = ?', (self.player2.id,))
            
            if winner:
                loser = self.player2 if winner == self.player1 else self.player1
                cursor.execute('UPDATE players SET rounds_won = rounds_won + 1 WHERE user_id = ?', (winner.id,))
                cursor.execute('UPDATE players SET rounds_lost = rounds_lost + 1 WHERE user_id = ?', (loser.id,))
            else:
                cursor.execute('UPDATE players SET rounds_drawn = rounds_drawn + 1 WHERE user_id = ?', (self.player1.id,))
                cursor.execute('UPDATE players SET rounds_drawn = rounds_drawn + 1 WHERE user_id = ?', (self.player2.id,))

            for card, user in [(self.p1_card, self.player1), (self.p2_card, self.player2)]:
                cursor.execute('UPDATE inventories SET rounds_played = rounds_played + 1 WHERE card_id = ? AND user_id = ?', (card.card_id, user.id))
                cursor.execute('UPDATE cards SET total_rounds_played = total_rounds_played + 1 WHERE card_id = ?', (card.card_id,))

            if winner:
                winning_card = self.p1_card if winner == self.player1 else self.p2_card
                cursor.execute('UPDATE inventories SET rounds_won = rounds_won + 1 WHERE card_id = ? AND user_id = ?', (winning_card.card_id, winner.id))
                cursor.execute('UPDATE cards SET total_rounds_won = total_rounds_won + 1 WHERE card_id = ?', (winning_card.card_id,))

            conn.commit()
        except Exception as e:
            logger.error(f"Round Update DB Error: {e}")
        finally:
            conn.close()

    async def end_game(self, interaction, last_round_embed):
        try:
            if interaction and not interaction.response.is_done():
                await interaction.response.defer()
        except:
            pass

        winner, loser, is_draw = None, None, False
        if self.player1_wins > self.player2_wins:
            winner, loser = self.player1, self.player2
        elif self.player2_wins > self.player1_wins:
            winner, loser = self.player2, self.player1
        else:
            is_draw = True

        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('UPDATE players SET battles_played = battles_played + 1 WHERE user_id = ?', (self.player1.id,))
            cursor.execute('UPDATE players SET battles_played = battles_played + 1 WHERE user_id = ?', (self.player2.id,))

            def update_deck_stats(deck, user, won_battle):
                for card in deck:
                    cursor.execute('UPDATE inventories SET battles_played = battles_played + 1 WHERE card_id = ? AND user_id = ?', (card.card_id, user.id))
                    cursor.execute('UPDATE cards SET total_battles_played = total_battles_played + 1 WHERE card_id = ?', (card.card_id,))
                    if won_battle:
                        cursor.execute('UPDATE inventories SET battles_won = battles_won + 1 WHERE card_id = ? AND user_id = ?', (card.card_id, user.id))
                        cursor.execute('UPDATE cards SET total_battles_won = total_battles_won + 1 WHERE card_id = ?', (card.card_id,))

            if is_draw:
                cursor.execute('UPDATE players SET battles_drawn = battles_drawn + 1 WHERE user_id = ?', (self.player1.id,))
                cursor.execute('UPDATE players SET battles_drawn = battles_drawn + 1 WHERE user_id = ?', (self.player2.id,))
                cursor.execute('UPDATE players SET coins = coins + 100 WHERE user_id = ?', (self.player1.id,))
                cursor.execute('UPDATE players SET coins = coins + 100 WHERE user_id = ?', (self.player2.id,))
                update_deck_stats(self.player1_deck, self.player1, False)
                update_deck_stats(self.player2_deck, self.player2, False)
                embed = discord.Embed(title="🤝 Battle Drawn 🤝", color=discord.Color.greyple())
                embed.add_field(name="Result", value="The battle ended in a draw!", inline=False)
                embed.add_field(name="Rewards", value="Both players received +100 Coins", inline=False)
            else:
                cursor.execute('UPDATE players SET battles_won = battles_won + 1 WHERE user_id = ?', (winner.id,))
                cursor.execute('UPDATE players SET battles_lost = battles_lost + 1 WHERE user_id = ?', (loser.id,))
                cursor.execute('UPDATE players SET coins = coins + 200 WHERE user_id = ?', (winner.id,))
                cursor.execute('UPDATE players SET coins = coins + 100 WHERE user_id = ?', (loser.id,))
                update_deck_stats(self.player1_deck, self.player1, (winner == self.player1))
                update_deck_stats(self.player2_deck, self.player2, (winner == self.player2))
                embed = discord.Embed(title="🏆 Battle Finished 🏆", color=discord.Color.gold())
                embed.add_field(name="Winner", value=f"**{winner.name}**", inline=False)
                embed.add_field(name="Rewards", value=f"{winner.name}: +200 Coins\n{loser.name}: +100 Coins", inline=False)
            
            conn.commit()
        except Exception as e:
            logger.error(f"End Game DB Error: {e}")
        finally:
            conn.close()

        embed.add_field(name="Final Score", value=f"{self.player1.name}: {self.player1_wins} | {self.player2.name}: {self.player2_wins} | Draws: {self.draws}", inline=False)
        await self.message.edit(embed=embed, view=None)


#---------------------------------------------------------BATTLE UI VIEWS-------------------------------------------------------------------------------------


def configure_battle_buttons(view, battle):
    view.add_item(SurrenderButton(battle))
    label = "Accept Draw 🤝" if len(battle.draw_offers) > 0 else "Offer Draw"
    style = discord.ButtonStyle.success if len(battle.draw_offers) > 0 else discord.ButtonStyle.secondary
    view.add_item(DrawButton(battle, label=label, style=style))


class SurrenderButton(discord.ui.Button):
    def __init__(self, battle):
        super().__init__(style=discord.ButtonStyle.danger, label="Surrender", emoji="🏳️", row=2)
        self.battle = battle

    async def callback(self, interaction):
        await self.battle.request_surrender(interaction)


class DrawButton(discord.ui.Button):
    def __init__(self, battle, label="Offer Draw", style=discord.ButtonStyle.secondary):
        super().__init__(style=style, label=label, emoji="🤝", row=2)
        self.battle = battle

    async def callback(self, interaction):
        await self.battle.request_draw(interaction)


class SurrenderConfirmView(discord.ui.View):
    def __init__(self, battle, user):
        super().__init__(timeout=60)
        self.battle = battle
        self.user = user

    @discord.ui.button(label="Yes, Surrender", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if interaction.user.id != self.user.id: return
        await self.battle.confirm_surrender(interaction, self.user)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction, button):
        if interaction.user.id != self.user.id: return
        await interaction.response.edit_message(content="Surrender cancelled.", view=None)


class SetupView(discord.ui.View):
    def __init__(self, battle):
        super().__init__(timeout=120)
        self.battle = battle
        self.add_item(DeckSelectMenu(battle, battle.player1))
        self.add_item(DeckSelectMenu(battle, battle.player2))

    @discord.ui.button(label="Cancel Setup", style=discord.ButtonStyle.red, row=2)
    async def cancel(self, interaction, button):
        if interaction.user.id not in [self.battle.player1.id, self.battle.player2.id]:
            return await interaction.response.send_message("Not your battle.", ephemeral=True)
        await interaction.response.edit_message(content="Battle setup cancelled.", embed=None, view=None)


class DeckSelectMenu(discord.ui.Select):
    def __init__(self, battle, player):
        self.battle = battle
        self.player = player
        
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT deck_name FROM decks WHERE user_id = ?', (player.id,))
        decks = cursor.fetchall()
        conn.close()
        
        options = [discord.SelectOption(label=d[0]) for d in decks] if decks else [discord.SelectOption(label="No Decks", value="none")]
        super().__init__(placeholder=f"{player.name}, choose...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction):
        if interaction.user.id != self.player.id:
            return await interaction.response.send_message("Not for you!", ephemeral=True)
        
        deck_name = self.values[0]
        if deck_name == "none":
            return await interaction.response.send_message("Create a deck first!", ephemeral=True)

        deck_cards = get_deck(self.player.id, deck_name)
        if self.player.id == self.battle.player1.id:
            self.battle.player1_deck = deck_cards
        else:
            self.battle.player2_deck = deck_cards

        if self.battle.player1_deck and self.battle.player2_deck:
            await self.battle.update_game_state(interaction)
        else:
            embed = interaction.message.embeds[0]
            index = 0 if self.player.id == self.battle.player1.id else 1
            embed.set_field_at(index, name=self.player.name, value=f"✅ Ready ({deck_name})", inline=True)
            await interaction.response.edit_message(embed=embed, view=self.view)


class ActionView(discord.ui.View):
    def __init__(self, battle, turn_player):
        super().__init__(timeout=60)
        self.battle = battle
        self.turn_player = turn_player
        configure_battle_buttons(self, battle)

    @discord.ui.button(label="Attack", style=discord.ButtonStyle.danger)
    async def attack(self, interaction, button):
        await self.process_action(interaction, "attack")

    @discord.ui.button(label="Defense", style=discord.ButtonStyle.primary)
    async def defense(self, interaction, button):
        await self.process_action(interaction, "defense")

    @discord.ui.button(label="Speed", style=discord.ButtonStyle.success)
    async def speed(self, interaction, button):
        await self.process_action(interaction, "speed")

    async def process_action(self, interaction, action):
        if interaction.user.id != self.turn_player.id:
            return await interaction.response.send_message("Not your turn!", ephemeral=True)
        
        if self.turn_player.id == self.battle.player1.id:
            self.battle.p1_action = action
            if action == "attack": self.battle.p2_action = "defense"
            elif action == "defense": self.battle.p2_action = "attack"
            else: self.battle.p2_action = "speed"
        else:
            self.battle.p2_action = action
            if action == "attack": self.battle.p1_action = "defense"
            elif action == "defense": self.battle.p1_action = "attack"
            else: self.battle.p1_action = "speed"
            
        self.battle.phase = "CARD_SELECT"
        await self.battle.update_game_state(interaction)


class CardSelectView(discord.ui.View):
    def __init__(self, battle):
        super().__init__(timeout=60)
        self.battle = battle
        self.add_item(CardDropdown(battle, battle.player1))
        self.add_item(CardDropdown(battle, battle.player2))
        configure_battle_buttons(self, battle)


class CardDropdown(discord.ui.Select):
    def __init__(self, battle, player):
        self.battle = battle
        self.player = player
        cards = battle.get_valid_deck(player)
        options = [discord.SelectOption(label=c.name[:100], description=f"OVR: {c.overall}", value=str(c.card_id)) for c in cards]
        if not options:
            options = [discord.SelectOption(label="No cards left", value="none")]
        super().__init__(placeholder=f"{player.name}'s Card", options=options, min_values=1, max_values=1)
    
    async def callback(self, interaction):
        if interaction.user.id != self.player.id:
            return await interaction.response.send_message("Not for you!", ephemeral=True)
        
        if self.values[0] == "none":
            return await interaction.response.send_message("No cards available!", ephemeral=True)
            
        selected_id = int(self.values[0])
        deck = self.battle.player1_deck if self.player.id == self.battle.player1.id else self.battle.player2_deck
        card_obj = next((c for c in deck if c.card_id == selected_id), None)
        
        if self.player.id == self.battle.player1.id:
            self.battle.p1_card = card_obj
        else:
            self.battle.p2_card = card_obj

        if self.battle.p1_card and self.battle.p2_card:
            self.battle.phase = "RESULT"
        await self.battle.update_game_state(interaction)


class NextRoundView(discord.ui.View):
    def __init__(self, battle):
        super().__init__(timeout=60)
        self.battle = battle
        self.ready_players = set()
        configure_battle_buttons(self, battle)

    @discord.ui.button(label="Ready for Next Round", style=discord.ButtonStyle.primary, row=0)
    async def next_round(self, interaction, button):
        if interaction.user.id not in [self.battle.player1.id, self.battle.player2.id]:
            return await interaction.response.send_message("Not your battle.", ephemeral=True)
        
        if interaction.user.id in self.ready_players:
            return await interaction.response.send_message("Waiting for opponent...", ephemeral=True)

        self.ready_players.add(interaction.user.id)
        
        if len(self.ready_players) == 2:
            self.battle.p1_action = None
            self.battle.p2_action = None
            self.battle.p1_card = None
            self.battle.p2_card = None
            self.battle.round += 1
            self.battle.round_resolved = False
            
            if self.battle.turn_player.id == self.battle.player1.id:
                self.battle.turn_player = self.battle.player2
            else:
                self.battle.turn_player = self.battle.player1

            self.battle.phase = "ACTION"
            await self.battle.update_game_state(interaction)
        else:
            await interaction.response.send_message(f"{interaction.user.name} is ready! Waiting for opponent...", ephemeral=False)


class BattleInviteView(discord.ui.View):
    def __init__(self, ctx, challenger, challengee):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.challenger = challenger
        self.challengee = challengee

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept(self, interaction, button):
        if interaction.user.id != self.challengee.id:
            return await interaction.response.send_message("Not for you!", ephemeral=True)
        
        battle_instance = Battle(self.ctx, self.challenger, self.challengee)
        await interaction.response.edit_message(content="Battle Accepted! Loading Arena...", embed=None, view=None)
        await battle_instance.start()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red)
    async def decline(self, interaction, button):
        if interaction.user.id != self.challengee.id:
            return await interaction.response.send_message("Not for you!", ephemeral=True)
        await interaction.response.edit_message(content="Battle Declined.", embed=None, view=None)


#---------------------------------------------------------DECK BUILDER UI-------------------------------------------------------------------------------------


class DeckBuilderSelect(discord.ui.Select):
    def __init__(self, page_cards, selected_ids):
        options = []
        for card in page_cards:
            is_selected = card.card_id in selected_ids
            label = f"{'✅ ' if is_selected else ''}{card.name}"[:100]
            desc = f"OVR: {card.overall} | {card.card_type}"
            options.append(discord.SelectOption(label=label, description=desc, value=str(card.card_id), emoji="⚽"))
        super().__init__(placeholder="Select cards to add/remove...", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction):
        view = self.view
        card_id = int(self.values[0])
        
        if card_id in view.selected_ids:
            view.selected_ids.remove(card_id)
        else:
            if len(view.selected_ids) >= 5:
                return await interaction.response.send_message("⛔ Your deck is full (5/5). Remove a card first.", ephemeral=True)
            view.selected_ids.append(card_id)
        await view.update_display(interaction)


class DeckBuilderView(discord.ui.View):
    def __init__(self, ctx, inventory, deck_name):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.inventory = inventory
        self.deck_name = deck_name
        self.message = None
        self.current_page = 0
        self.items_per_page = 20
        self.total_pages = max(1, (len(inventory) - 1) // self.items_per_page + 1)
        self.selected_ids = []
        self.update_components()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("⛔ This is not your deck builder.", ephemeral=True)
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
            self.add_item(DeckBuilderSelect(page_cards, self.selected_ids))

        if self.current_page > 0:
            self.add_item(BuilderPrevButton())
        if self.current_page < self.total_pages - 1:
            self.add_item(BuilderNextButton())

        self.add_item(BuilderSaveButton(disabled=(len(self.selected_ids) != 5)))
        self.add_item(BuilderCancelButton())

    async def update_display(self, interaction):
        self.update_components()
        embed = discord.Embed(title=f"🛠️ Deck Builder: {self.deck_name}", color=discord.Color.blue())
        
        if self.selected_ids:
            selected_names = []
            for cid in self.selected_ids:
                card_obj = next((c for c in self.inventory if c.card_id == cid), None)
                if card_obj:
                    selected_names.append(f"• **{card_obj.name}** ({card_obj.overall})")
            card_list_str = "\n".join(selected_names)
        else:
            card_list_str = "*No cards selected*"

        embed.add_field(name=f"Current Lineup ({len(self.selected_ids)}/5)", value=card_list_str, inline=False)
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages}")

        if interaction.response.is_done():
            await self.message.edit(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)


class BuilderPrevButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Previous", style=discord.ButtonStyle.primary, row=1)
    async def callback(self, interaction):
        self.view.current_page -= 1
        await self.view.update_display(interaction)


class BuilderNextButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Next", style=discord.ButtonStyle.primary, row=1)
    async def callback(self, interaction):
        self.view.current_page += 1
        await self.view.update_display(interaction)


class BuilderSaveButton(discord.ui.Button):
    def __init__(self, disabled=True):
        style = discord.ButtonStyle.grey if disabled else discord.ButtonStyle.green
        label = "Wait..." if disabled else "Save Deck"
        super().__init__(label=label, style=style, emoji="💾", disabled=disabled, row=2)

    async def callback(self, interaction):
        view = self.view
        if len(view.selected_ids) != 5:
            return await interaction.response.send_message("You need exactly 5 cards!", ephemeral=True)

        try:
            add_deck(interaction.user.id, view.deck_name, view.selected_ids)
            embed = discord.Embed(title="✅ Deck Saved!", description=f"Deck **{view.deck_name}** has been created successfully.", color=discord.Color.green())
            await interaction.response.edit_message(embed=embed, view=None)
            view.stop()
        except ValueError as e:
            if "already exists" in str(e):
                await interaction.response.send_message(f"⚠️ A deck named '{view.deck_name}' already exists.", ephemeral=True)
            else:
                await interaction.response.send_message(f"Error: {e}", ephemeral=True)


class BuilderCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cancel", style=discord.ButtonStyle.danger, row=2)
    async def callback(self, interaction):
        await interaction.response.edit_message(content="❌ Deck building cancelled.", embed=None, view=None)
        self.view.stop()


#---------------------------------------------------------COG CLASS-------------------------------------------------------------------------------------


class Battles(commands.Cog):
    """Battle system and deck management"""
    
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='battle', description="Challenge a player to a battle")
    async def battle(self, ctx, user: discord.User):
        """Challenge another player to a card battle"""
        if user.id == ctx.author.id:
            return await ctx.send("You cannot battle yourself.")
        
        ensure_player_exists(ctx.author.id, ctx.author.name)
        ensure_player_exists(user.id, user.name)
        
        embed = discord.Embed(title="⚔️ Battle Request", description=f"{ctx.author.name} has challenged {user.name} to a battle!")
        view = BattleInviteView(ctx, ctx.author, user)
        await ctx.send(embed=embed, view=view)
        logger.info(f'{ctx.author.name} challenged {user.name} to battle')

    @commands.hybrid_command(name='decks', description="View list of your decks")
    async def view_decks(self, ctx, user: discord.User = None):
        """View your or another user's decks"""
        if user is None:
            user = ctx.author

        ensure_player_exists(user.id, user.name)
        
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT deck_name, cards FROM decks WHERE user_id = ?', (user.id,))
        decks = cursor.fetchall()
        conn.close()

        if not decks:
            return await ctx.send(f"{user.name} has no decks.")

        embed = discord.Embed(title=f"📋 {user.name}'s Decks")
        for deck_name, cards in decks:
            card_ids = cards.split(',')
            card_details = []
            conn = get_connection()
            cursor = conn.cursor()
            for card_id in card_ids:
                cursor.execute('SELECT name FROM cards WHERE card_id = ?', (card_id,))
                card_data = cursor.fetchone()
                if card_data:
                    card_details.append(card_data[0])
            conn.close()
            embed.add_field(name=deck_name, value=', '.join(card_details) or "No cards", inline=False)

        await ctx.send(embed=embed)

    @commands.hybrid_command(name='create_deck', description="Create a battle deck (Requires 5 Card IDs)")
    async def create_deck(self, ctx, deck_name: str, card1: int, card2: int, card3: int, card4: int, card5: int):
        """Create a new battle deck with 5 cards"""
        card_ids = [card1, card2, card3, card4, card5]

        ensure_player_exists(ctx.author.id, ctx.author.name)

        conn = get_connection()
        cursor = conn.cursor()
        for card_id in card_ids:
            cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (ctx.author.id, card_id))
            if cursor.fetchone() is None:
                conn.close()
                return await ctx.send(f"⛔ You do not own the card with ID **{card_id}**.")
        conn.close()

        try:
            add_deck(ctx.author.id, deck_name, card_ids)
            await ctx.send(f"✅ Deck '**{deck_name}**' created successfully!")
        except ValueError as e:
            await ctx.send(f"❌ Error: {str(e)}")

    @commands.hybrid_command(name='edit_deck', description="Edit an existing deck")
    async def edit_deck(self, ctx, deck_name: str, card1: int, card2: int, card3: int, card4: int, card5: int):
        """Edit an existing battle deck"""
        card_ids = [card1, card2, card3, card4, card5]

        ensure_player_exists(ctx.author.id, ctx.author.name)

        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT deck_name FROM decks WHERE user_id = ? AND deck_name = ?', (ctx.author.id, deck_name))
        if cursor.fetchone() is None:
            conn.close()
            return await ctx.send(f"❌ No deck found with the name '**{deck_name}**'.")

        for card_id in card_ids:
            cursor.execute('SELECT 1 FROM inventories WHERE user_id = ? AND card_id = ?', (ctx.author.id, card_id))
            if cursor.fetchone() is None:
                conn.close()
                return await ctx.send(f"⛔ You do not own ID **{card_id}**.")

        try:
            cards_str = ','.join(map(str, card_ids))
            cursor.execute('UPDATE decks SET cards = ? WHERE user_id = ? AND deck_name = ?', (cards_str, ctx.author.id, deck_name))
            conn.commit()
            conn.close()
            await ctx.send(f"✅ Deck '**{deck_name}**' updated successfully!")
        except Exception as e:
            conn.close()
            await ctx.send(f"❌ Error updating deck: {e}")

    @commands.hybrid_command(name='delete_deck', description="Delete a deck")
    async def delete_deck(self, ctx, deck_name: str):
        """Delete one of your battle decks"""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM decks WHERE user_id = ? AND deck_name = ?', (ctx.author.id, deck_name))
        if cursor.rowcount == 0:
            conn.close()
            return await ctx.send(f"❌ No deck found with the name '**{deck_name}**'.")
        conn.commit()
        conn.close()
        await ctx.send(f"✅ Deck '**{deck_name}**' deleted.")

    @commands.hybrid_command(name='view_deck', description="Visualize a specific deck")
    async def view_deck(self, ctx, deck_name: str, user: discord.User = None):
        """View a deck with visual lineup"""
        target_user = user or ctx.author
        ensure_player_exists(target_user.id, target_user.name)
        
        deck_cards = get_deck(target_user.id, deck_name)
        
        if deck_cards is None:
            return await ctx.send(f"❌ Deck '**{deck_name}**' not found for **{target_user.name}**.")
        
        if len(deck_cards) != 5:
            return await ctx.send("This deck does not have 5 cards.")

        description_text = ""
        for card in deck_cards:
            description_text += f"**{card.name}**\n⭐ {card.overall} | ⚔️ {card.attack} | 🛡️ {card.defense} | ⚡ {card.speed}\n\n"

        embed = discord.Embed(title=f"📋 Deck Details: {deck_name}", description=description_text, color=discord.Color.green())
        embed.set_footer(text=f"Owner: {target_user.name}", icon_url=target_user.display_avatar.url)

        if Image:
            image_buffer = await self.bot.loop.run_in_executor(None, generate_lineup_image, deck_cards)
            if image_buffer:
                file = discord.File(fp=image_buffer, filename=f"{deck_name}.png")
                embed.set_image(url=f"attachment://{deck_name}.png")
                await ctx.send(file=file, embed=embed)
                return

        await ctx.send(embed=embed)

    @commands.hybrid_command(name='build_deck', description="Interactively build a deck")
    async def build_deck(self, ctx, deck_name: str):
        """Visual deck builder interface"""
        ensure_player_exists(ctx.author.id, ctx.author.name)
        
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM decks WHERE user_id = ? AND deck_name = ?', (ctx.author.id, deck_name))
        if cursor.fetchone():
            conn.close()
            return await ctx.send(f"❌ You already have a deck named **{deck_name}**.")
        conn.close()

        inventory, _ = get_player_inventory(ctx.author.id)
        if not inventory:
            return await ctx.send("You have no cards to build a deck with!")

        view = DeckBuilderView(ctx, inventory, deck_name)
        
        embed = discord.Embed(title=f"🛠️ Deck Builder: {deck_name}", color=discord.Color.blue())
        embed.add_field(name="Current Lineup (0/5)", value="*No cards selected*", inline=False)
        embed.set_footer(text=f"Page 1/{view.total_pages}")
        
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg


async def setup(bot):
    await bot.add_cog(Battles(bot))
