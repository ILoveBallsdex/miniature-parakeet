"""Basic regression tests for the first standalone FutBot API services."""

import os
import tempfile
import unittest

from futbot_api.database import init_database, transaction
from futbot_api import services


class FutBotServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["FUTBOT_DB_PATH"] = os.path.join(self.temp_dir.name, "test.db")
        init_database()
        with transaction() as db:
            db.execute("INSERT INTO cards (card_id, name, attack, defense, speed, overall, card_type, card_rarity, copies) VALUES (1, 'Test Icon', 90, 90, 90, 92, 'Icon', 'Icon', 0)")
            db.execute("INSERT INTO cards (card_id, name, attack, defense, speed, overall, card_type, card_rarity, copies) VALUES (2, 'Test Hero', 88, 85, 80, 88, 'Hero', 'Hero', 0)")
            db.execute("INSERT INTO cards (card_id, name, attack, defense, speed, overall, card_type, card_rarity, copies) VALUES (3, 'Test Standard', 87, 86, 84, 87, 'Standard', 'Rare', 0)")
        services.ensure_player(123, "Test User")

    def tearDown(self):
        self.temp_dir.cleanup()
        os.environ.pop("FUTBOT_DB_PATH", None)

    def test_catalog_and_player(self):
        self.assertEqual(services.get_card(1)["name"], "Test Icon")
        self.assertEqual(services.get_player(123)["name"], "Test User")

    def test_buy_pack_requires_balance(self):
        with self.assertRaises(services.GameError):
            services.buy_pack(123, 1)

    def test_inventory_sell_updates_balance(self):
        with transaction() as db:
            db.execute("INSERT INTO inventories (user_id, card_id, edition) VALUES (123, 3, 1)")
        result = services.sell_cards(123, [3])
        self.assertEqual(result["coins_earned"], services.card_value(87))
        self.assertEqual(result["balance"], services.card_value(87))
        self.assertEqual(services.get_inventory(123), [])

    def test_wishlist_toggle(self):
        self.assertTrue(services.toggle_wishlist(123, 1)["wishlisted"])
        self.assertFalse(services.toggle_wishlist(123, 1)["wishlisted"])

    def test_deck_requires_five_owned_cards(self):
        with self.assertRaises(services.GameError):
            services.save_deck(123, "Main", [1, 2, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()
