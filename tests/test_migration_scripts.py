"""Tests for safe database and artwork migration scripts."""

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MigrationScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_script(self, script, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), *map(str, args)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True,
        )

    def test_catalog_import_resets_only_destination_player_state(self):
        source = self.root / "old.db"
        target = self.root / "api.db"
        db = sqlite3.connect(source)
        db.execute("""CREATE TABLE cards (
            card_id INTEGER PRIMARY KEY, player_id TEXT, name TEXT, attack INTEGER,
            defense INTEGER, speed INTEGER, height TEXT, club TEXT, position TEXT,
            overall INTEGER, image_path TEXT, card_rarity TEXT, card_type TEXT,
            league TEXT, nation TEXT, copies INTEGER, total_battles_played INTEGER,
            wishlist_count INTEGER
        )""")
        db.execute(
            """INSERT INTO cards VALUES
            (1, 'p1', 'Catalog Card', 90, 80, 70, '180', 'Club', 'ST', 90,
             'card.png', 'Rare', 'Standard', 'League', 'Nation', 99, 123, 45)"""
        )
        db.execute("CREATE TABLE players (user_id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO players VALUES (123, 'Old Player')")
        db.execute("CREATE TABLE inventories (user_id INTEGER, card_id INTEGER)")
        db.execute("INSERT INTO inventories VALUES (123, 1)")
        db.execute("CREATE TABLE achievements (achievement_id INTEGER PRIMARY KEY, title TEXT, description TEXT)")
        db.execute("INSERT INTO achievements VALUES (3, 'First Win', 'Win once')")
        db.execute("CREATE TABLE user_achievements (user_id INTEGER, achievement_id INTEGER)")
        db.execute("INSERT INTO user_achievements VALUES (123, 3)")
        db.commit()
        db.close()

        self.run_script("import_card_catalog.py", "--source", source, "--target", target)

        db = sqlite3.connect(target)
        card = db.execute("SELECT name, copies, total_battles_played, wishlist_count FROM cards WHERE card_id = 1").fetchone()
        self.assertEqual(card, ("Catalog Card", 0, 0, 0))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM players").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM inventories").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT title FROM achievements WHERE achievement_id = 3").fetchone()[0], "First Win")
        db.close()

        source_db = sqlite3.connect(source)
        self.assertEqual(source_db.execute("SELECT COUNT(*) FROM players").fetchone()[0], 1)
        source_db.close()

    def test_asset_migration_normalizes_image_paths(self):
        source = self.root / "old.db"
        target = self.root / "api.db"
        source_root = self.root / "old_project"
        image_file = source_root / "images" / "card.png"
        image_file.parent.mkdir(parents=True)
        image_file.write_bytes(b"test image bytes")

        db = sqlite3.connect(source)
        db.execute("CREATE TABLE cards (card_id INTEGER, image_path TEXT)")
        db.execute("INSERT INTO cards VALUES (1, 'images/card.png')")
        db.commit()
        db.close()

        db = sqlite3.connect(target)
        db.execute("CREATE TABLE cards (card_id INTEGER, image_path TEXT)")
        db.execute("INSERT INTO cards VALUES (1, 'images/card.png')")
        db.commit()
        db.close()

        assets = self.root / "assets"
        self.run_script(
            "migrate_card_assets.py",
            "--source-db", source,
            "--source-root", source_root,
            "--target-db", target,
            "--target-assets", assets,
        )

        db = sqlite3.connect(target)
        image_path = db.execute("SELECT image_path FROM cards WHERE card_id = 1").fetchone()[0]
        db.close()
        self.assertEqual(image_path, "card_1.png")
        self.assertEqual((assets / image_path).read_bytes(), b"test image bytes")


if __name__ == "__main__":
    unittest.main()
