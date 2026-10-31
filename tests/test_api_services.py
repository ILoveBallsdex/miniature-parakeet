"""Regression tests for the standalone FutBot API game services."""

import os
import tempfile
import unittest
from PIL import Image

from futbot_api.images import generate_minted_card

from futbot_api.database import init_database, transaction
from futbot_api import services


class FutBotServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["FUTBOT_DB_PATH"] = os.path.join(self.temp_dir.name, "test.db")
        init_database()
        with transaction() as db:
            db.execute("INSERT INTO cards (card_id, name, attack, defense, speed, overall, card_type, card_rarity, copies) VALUES (1, 'Test Icon', 95, 90, 90, 92, 'Icon', 'Icon', 0)")
            db.execute("INSERT INTO cards (card_id, name, attack, defense, speed, overall, card_type, card_rarity, copies) VALUES (2, 'Test Hero', 88, 85, 80, 88, 'Hero', 'Hero', 0)")
            db.execute("INSERT INTO cards (card_id, name, attack, defense, speed, overall, card_type, card_rarity, copies) VALUES (3, 'Test Standard', 87, 86, 84, 87, 'Standard', 'Rare', 0)")
            for card_id in range(10, 16):
                db.execute("INSERT INTO cards (card_id, name, overall, card_type, card_rarity, copies) VALUES (?, ?, 75, 'Standard', 'Common', 0)", (card_id, f"Common {card_id}"))
            for card_id in range(20, 23):
                db.execute("INSERT INTO cards (card_id, name, overall, card_type, card_rarity, copies) VALUES (?, ?, 82, 'Standard', 'Uncommon', 0)", (card_id, f"Uncommon {card_id}"))
            db.execute("INSERT INTO cards (card_id, name, overall, card_type, card_rarity, copies) VALUES (30, 'Rare Starter', 86, 'Standard', 'Rare', 0)")
        services.ensure_player(123, "Test User")
        services.ensure_player(456, "Opponent")

    def tearDown(self):
        self.temp_dir.cleanup()
        os.environ.pop("FUTBOT_DB_PATH", None)

    def add_owned_card(self, user_id, card_id, edition=1):
        with transaction() as db:
            db.execute(
                "INSERT INTO inventories (user_id, card_id, edition, trade_count) VALUES (?, ?, ?, 0)",
                (user_id, card_id, edition),
            )

    def test_catalog_and_player(self):
        self.assertEqual(services.get_card(1)["name"], "Test Icon")
        self.assertEqual(services.get_player(123)["name"], "Test User")

    def test_position_weighted_overall_uses_role_stats(self):
        self.assertEqual(services.calculate_overall("GK", goalkeeping=83), 83)
        self.assertEqual(services.calculate_overall("ST", shooting=100), 60)
        self.assertEqual(services.calculate_overall("ST", defense=100), 0)
        self.assertEqual(services.calculate_overall("CB", shooting=100), 0)
        self.assertEqual(services.calculate_overall("CB", dribbling=100), 0)
        self.assertEqual(services.calculate_overall("CB", defense=100), 85)

    def test_recalculate_backfills_missing_legacy_passing(self):
        with transaction() as db:
            db.execute(
                """UPDATE cards SET position = 'ST', attack = 90, defense = 70, speed = 80,
                shooting = 0, passing = 0, dribbling = 0, goalkeeping = 0
                WHERE card_id = 1"""
            )

        services.recalculate_catalog_overalls()
        card = services.get_card(1)
        self.assertEqual(card["shooting"], 90)
        self.assertEqual(card["passing"], 80)
        self.assertEqual(card["dribbling"], 80)

    def test_buy_pack_requires_balance(self):
        with self.assertRaises(services.GameError):
            services.buy_pack(123, 1)

    def test_pack_purchase_and_open(self):
        with transaction() as db:
            db.execute("UPDATE players SET coins = 3000 WHERE user_id = 123")
        services.buy_pack(123, 2)
        result = services.open_pack(123, 2)
        self.assertEqual(len(result["cards"]), 1)
        self.assertEqual(result["cards"][0]["card_type"], "Icon")
        self.assertEqual(len(services.get_inventory(123)), 1)

    def test_inventory_sell_updates_balance(self):
        self.add_owned_card(123, 3)
        result = services.sell_cards(123, [3])
        self.assertEqual(result["coins_earned"], services.card_value(87))
        self.assertEqual(result["balance"], services.card_value(87))
        self.assertEqual(services.get_inventory(123), [])


    def test_player_name_lookup_handles_small_typos_but_rejects_gibberish(self):
        card = services.find_catalog_card("Tset Icn")
        self.assertEqual(card["name"], "Test Icon")
        self.assertEqual(card["value"], services.card_value(92))
        with self.assertRaises(services.GameError):
            services.find_catalog_card("qzxvplm")

    def test_buy_player_card_deducts_value_and_adds_inventory(self):
        cost = services.card_value(92)
        with transaction() as db:
            db.execute("UPDATE players SET coins = ? WHERE user_id = 123", (cost + 500,))
        result = services.buy_player_card(123, 1)
        self.assertEqual(result["coins_spent"], cost)
        self.assertEqual(result["balance"], 500)
        self.assertEqual(len(services.get_inventory(123)), 1)

    def test_buy_player_card_rejects_insufficient_coins(self):
        with self.assertRaises(services.GameError):
            services.buy_player_card(123, 1)

    def test_sell_name_lookup_only_finds_owned_players(self):
        self.add_owned_card(123, 1)
        card = services.find_owned_card(123, "Test Icn")
        self.assertEqual(card["card_id"], 1)
        with self.assertRaises(services.GameError):
            services.find_owned_card(123, "Test Hero")

    def test_player_values_scale_with_overall_and_quicksell_is_half(self):
        self.assertEqual(services.card_value(60), 1000)
        self.assertEqual(services.quicksell_value(60), 500)
        values = [services.card_value(overall) for overall in range(60, 100)]
        self.assertEqual(values, sorted(values))
        self.assertGreater(services.card_value(90), services.card_value(80) * 2)

        self.add_owned_card(123, 1)
        result = services.quicksell_card(123, 1)
        self.assertEqual(result["coins_earned"], services.card_value(92) // 2)
        self.assertEqual(services.get_player(123)["coins"], services.card_value(92) // 2)

    def test_lineup_swap_moves_player_into_empty_slot(self):
        with transaction() as db:
            db.execute(
                "INSERT INTO lineups (user_id, formation, slots) VALUES (123, '2-2-3', ?)",
                ('{"5": 1}',),
            )

        result = services.swap_lineup_players(123, 5, 7)
        slots = {item["slot"]: item["card"] for item in result["slots"]}
        self.assertIsNone(slots[5])
        self.assertEqual(slots[7]["card_id"], 1)
        self.assertEqual(result["filled"], 1)

    def test_bronze_players_are_eligible_for_random_rewards(self):
        with transaction() as db:
            db.execute(
                "INSERT INTO cards (card_id, name, overall, card_type, card_rarity, copies) VALUES (40, 'Bronze Player', 63, 'Standard', 'Common', 0)"
            )
            weighted = services._weighted_card_rows(db)
        bronze = next((weight for card, weight in weighted if card["card_id"] == 40), None)
        self.assertGreater(bronze, 0)

    def test_quick_reward_includes_quicksell_preview_value(self):
        reward = services.claim_quick_reward(123)
        self.assertEqual(len(reward["cards"]), 1)
        card = reward["cards"][0]
        self.assertEqual(card["quicksell_value"], services.quicksell_value(card["overall"]))

    def test_wishlist_toggle(self):
        self.assertTrue(services.toggle_wishlist(123, 1)["wishlisted"])
        self.assertFalse(services.toggle_wishlist(123, 1)["wishlisted"])

    def test_deck_requires_five_owned_cards(self):
        with self.assertRaises(services.GameError):
            services.save_deck(123, "Main", [1, 2, 3, 4, 5])

    def test_starter_pack(self):
        result = services.claim_starter_pack(123)
        self.assertEqual(result["count"], 10)
        with self.assertRaises(services.GameError):
            services.claim_starter_pack(123)

    def test_daily_reward_and_collection(self):
        reward = services.claim_daily(123)
        self.assertEqual(len(reward["choices"]), 2)
        collected = services.collect_daily(123, reward["choices"][0]["card_id"])
        self.assertEqual(collected["card"]["card_id"], reward["choices"][0]["card_id"])

    def test_simple_trade(self):
        self.add_owned_card(123, 1)
        self.add_owned_card(456, 2)
        offer = services.create_trade_offer(123, 456, 1, 2)
        resolved = services.resolve_trade_offer(offer["trade_id"], 456, True)
        self.assertEqual(resolved["status"], "accepted")
        self.assertEqual([c["card_id"] for c in services.get_inventory(123)], [2])
        self.assertEqual([c["card_id"] for c in services.get_inventory(456)], [1])

    def test_advanced_exchange(self):
        self.add_owned_card(123, 1)
        self.add_owned_card(456, 2)
        with transaction() as db:
            db.execute("UPDATE players SET coins = 100 WHERE user_id IN (123, 456)")
        exchange = services.create_exchange(123, 456)
        session_id = exchange["session_id"]
        services.update_exchange_offer(session_id, 123, [1], 20)
        services.update_exchange_offer(session_id, 456, [2], 10)
        services.lock_exchange(session_id, 123)
        services.lock_exchange(session_id, 456)
        services.confirm_exchange(session_id, 123)
        result = services.confirm_exchange(session_id, 456)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(services.get_player(123)["coins"], 90)
        self.assertEqual(services.get_player(456)["coins"], 110)

    def test_trade_up(self):
        for card_id in range(10, 15):
            self.add_owned_card(123, card_id)
        eligible = services.eligible_trade_up_cards(123, "common")
        selected = [row["inventory_row_id"] for row in eligible[:5]]
        result = services.execute_trade_up(123, "common", selected)
        self.assertEqual(result["tier"], "common")
        self.assertEqual(result["reward"]["card_rarity"], "Uncommon")
        self.assertEqual(len(services.get_inventory(123)), 1)

    def test_battle_round(self):
        for card_id in [1, 2, 3, 10, 11]:
            self.add_owned_card(123, card_id)
        for card_id in [12, 13, 14, 15, 20]:
            self.add_owned_card(456, card_id)
        services.save_deck(123, "A", [1, 2, 3, 10, 11])
        services.save_deck(456, "B", [12, 13, 14, 15, 20])
        battle = services.create_battle(123, 456)
        battle_id = battle["battle_id"]
        services.accept_battle(battle_id, 456)
        services.select_battle_deck(battle_id, 123, "A")
        services.select_battle_deck(battle_id, 456, "B")
        services.choose_battle_action(battle_id, 123, "attack")
        services.choose_battle_card(battle_id, 123, 1)
        result = services.choose_battle_card(battle_id, 456, 12)
        self.assertEqual(result["state"]["phase"], "RESULT")
        self.assertEqual(result["state"]["p1_wins"], 1)

    def test_card_weight(self):
        result = services.get_card_weight("Test Standard")
        self.assertEqual(result["card_id"], 3)
        self.assertGreater(result["weight"], 0)

    def test_card_drop(self):
        drop = services.create_card_drop(123)
        collected = services.collect_card_drop(drop["drop_id"], 123, "Test User")
        self.assertEqual(collected["collected_by"], 123)
        self.assertEqual(services.get_player(123)["cards_dropped"], 1)

    def test_friend_requests(self):
        request = services.add_friend_request(123, 456)
        self.assertEqual(request["status"], "pending")
        services.resolve_friend_request(request["request_id"], 456, True)
        self.assertEqual(services.list_friends(123)[0]["friend_id"], 456)

    def test_admin_management(self):
        services.admin_give_coins(123, 500)
        services.admin_give_card(123, 1)
        result = services.admin_remove_card(123, 1)
        self.assertEqual(result["removed_copies"], 1)
        self.assertEqual(services.get_player(123)["coins"], 500)

    def test_battle_achievement_unlock(self):
        with transaction() as db:
            db.execute("INSERT INTO achievements (achievement_id, title, description) VALUES (3, 'First Win', 'Win one battle')")
            db.execute("UPDATE players SET battles_won = 1 WHERE user_id = 123")
            services._unlock_stat_achievements(db, 123, "battles_won")
        self.assertEqual(len(services.get_achievements(123)), 1)
        self.assertEqual(services.get_achievements(123)[0]["unlocked"], 1)

    def test_minted_card_image_generation(self):
        image_path = os.path.join(self.temp_dir.name, "card.png")
        Image.new("RGBA", (500, 700), (20, 120, 60, 255)).save(image_path)
        rendered = generate_minted_card(image_path, None, "Test User", "#1/5")
        self.assertTrue(rendered.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_market_escrow_and_purchase(self):
        self.add_owned_card(123, 1)
        with transaction() as db:
            db.execute("UPDATE players SET coins = 1000 WHERE user_id = 456")
        listing = services.create_market_listing(123, 1, 1, 250, 1)
        self.assertEqual(services.get_inventory(123), [])
        purchased = services.buy_market_listing(listing["listing_id"], 456)
        self.assertEqual(purchased["buyer_id"], 456)
        self.assertEqual(services.get_player(456)["coins"], 750)
        self.assertEqual(services.get_player(123)["coins"], 250)


if __name__ == "__main__":
    unittest.main()
