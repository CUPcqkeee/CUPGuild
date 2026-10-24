import sqlite3
import tempfile
import unittest
from pathlib import Path

from API.API import ClanDatabase, ClanError


RULES = {
    "member_limit": 2,
    "tag_min_length": 2,
    "tag_max_length": 5,
    "name_min_length": 3,
    "name_max_length": 32,
    "description_max_length": 300,
}


class ClanDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = ClanDatabase(Path(self.temp_dir.name) / "clans.sqlite3", RULES)

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def test_create_clan_registers_owner_and_normalizes_tag(self) -> None:
        clan = self.database.create_clan(100, 200, "cup", "Cup Guild", "A team")

        self.assertEqual(clan["tag"], "CUP")
        self.assertEqual(clan["owner_id"], 200)
        self.assertEqual(clan["member_count"], 1)
        self.assertEqual(self.database.get_user_clan(100, 200)["id"], clan["id"])

    def test_tag_and_membership_are_unique_per_guild(self) -> None:
        self.database.create_clan(100, 200, "CUP", "Cup Guild", "")

        with self.assertRaises(ClanError):
            self.database.create_clan(100, 201, "cup", "Another Guild", "")
        with self.assertRaises(ClanError):
            self.database.create_clan(100, 200, "ALT", "Another Guild", "")

    def test_join_enforces_member_limit_and_reports_guild_stats(self) -> None:
        clan = self.database.create_clan(100, 200, "CUP", "Cup Guild", "")
        updated = self.database.join_clan(100, 201, clan["id"])

        self.assertEqual(updated["member_count"], 2)
        self.assertEqual(self.database.get_guild_stats(100), (1, 2))
        with self.assertRaises(ClanError):
            self.database.join_clan(100, 202, clan["id"])

    def test_membership_is_independent_between_guilds(self) -> None:
        clan = self.database.create_clan(100, 200, "CUP", "Cup Guild", "")
        other_clan = self.database.create_clan(101, 200, "CUP", "Other Guild", "")

        self.assertNotEqual(clan["id"], other_clan["id"])
        self.assertEqual(self.database.get_guild_stats(100), (1, 1))
        self.assertEqual(self.database.get_guild_stats(101), (1, 1))

    def test_rank_hierarchy_limits_promotions_and_member_removal(self) -> None:
        self.database.member_limit = 4
        clan = self.database.create_clan(100, 200, "CUP", "Cup Guild", "")
        self.database.join_clan(100, 201, clan["id"])
        self.database.join_clan(100, 202, clan["id"])

        self.assertEqual(self.database.change_member_rank(100, 200, 201, 1), "veteran")
        with self.assertRaises(ClanError):
            self.database.change_member_rank(100, 201, 202, 1)
        self.assertEqual(self.database.change_member_rank(100, 200, 201, 1), "officer")
        with self.assertRaises(ClanError):
            self.database.change_member_rank(100, 201, 202, 1)
        self.assertEqual(self.database.change_member_rank(100, 200, 201, 1), "deputy")
        self.assertEqual(self.database.change_member_rank(100, 201, 202, 1), "veteran")
        self.assertEqual(self.database.change_member_rank(100, 201, 202, 1), "officer")
        with self.assertRaises(ClanError):
            self.database.kick_member(100, 201, 200)
        with self.assertRaises(ClanError):
            self.database.kick_member(100, 202, 201)

        self.database.kick_member(100, 201, 202)
        self.assertIsNone(self.database.get_user_clan(100, 202))

    def test_officer_can_remove_members_but_cannot_change_ranks(self) -> None:
        self.database.member_limit = 4
        clan = self.database.create_clan(100, 200, "CUP", "Cup Guild", "")
        self.database.join_clan(100, 201, clan["id"])
        self.database.join_clan(100, 202, clan["id"])
        self.database.change_member_rank(100, 200, 201, 1)
        self.database.change_member_rank(100, 200, 201, 1)

        with self.assertRaises(ClanError):
            self.database.change_member_rank(100, 201, 202, 1)
        self.database.kick_member(100, 201, 202)
        self.assertIsNone(self.database.get_user_clan(100, 202))

    def test_only_owner_can_disband_and_owner_cannot_leave(self) -> None:
        clan = self.database.create_clan(100, 200, "CUP", "Cup Guild", "")
        self.database.join_clan(100, 201, clan["id"])

        with self.assertRaises(ClanError):
            self.database.leave_clan(100, 200)
        with self.assertRaises(ClanError):
            self.database.disband_clan(100, 201)

        self.database.disband_clan(100, 200)
        self.assertIsNone(self.database.get_user_clan(100, 201))

    def test_existing_database_is_migrated_with_member_rank(self) -> None:
        self.database.close()
        database_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        connection = sqlite3.connect(database_path)
        connection.executescript(
            """
            CREATE TABLE clans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                tag TEXT NOT NULL COLLATE NOCASE,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                owner_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                UNIQUE (guild_id, tag)
            );
            CREATE TABLE memberships (
                guild_id INTEGER NOT NULL,
                clan_id INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL,
                joined_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                PRIMARY KEY (guild_id, user_id)
            );
            INSERT INTO clans (guild_id, tag, name, owner_id) VALUES (100, 'OLD', 'Old Clan', 200);
            INSERT INTO memberships (guild_id, clan_id, user_id) VALUES (100, 1, 200);
            """
        )
        connection.close()

        migrated_database = ClanDatabase(database_path, RULES)
        try:
            clan = migrated_database.get_user_clan(100, 200)
            self.assertEqual(clan["member_role"], "member")
            self.assertEqual(migrated_database.get_guild_stats(100), (1, 1))
            self.assertEqual(clan["color"], "#5865F2")
            self.assertEqual(clan["is_open"], 1)
            self.assertIn("veteran", migrated_database.get_clan_role_permissions(clan["id"]))
        finally:
            migrated_database.close()

    def test_owner_can_configure_rank_permissions_and_only_assigned_members_can_use_them(self) -> None:
        self.database.member_limit = 5
        clan = self.database.create_clan(100, 200, "CUP", "Cup Guild", "")
        self.database.join_clan(100, 201, clan["id"])
        self.assertFalse(self.database.has_permission(100, 201, "edit_clan"))

        self.database.set_role_permission(100, 200, "member", "edit_clan", True)
        self.assertTrue(self.database.has_permission(100, 201, "edit_clan"))
        with self.assertRaises(ClanError):
            self.database.set_role_permission(100, 201, "member", "manage_ranks", True)

        self.database.update_clan_settings(100, 201, "New Guild", "NEW", "#AABBCC", False)
        updated = self.database.get_user_clan(100, 201)
        self.assertEqual(updated["name"], "New Guild")
        self.assertEqual(updated["color"], "#AABBCC")
        self.assertEqual(updated["is_open"], 0)

    def test_closed_clan_is_hidden_and_cannot_be_joined(self) -> None:
        clan = self.database.create_clan(100, 200, "CUP", "Cup Guild", "")
        self.database.update_clan_settings(100, 200, "Cup Guild", "CUP", "#5865F2", False)

        self.assertEqual(self.database.discover_clans(100, 201, 10), [])
        with self.assertRaises(ClanError):
            self.database.join_clan(100, 201, clan["id"])

    def test_clan_settings_validate_tag_color_and_duplicate_tags(self) -> None:
        first = self.database.create_clan(100, 200, "CUP", "Cup Guild", "")
        self.database.create_clan(100, 201, "ALT", "Alt Guild", "")

        with self.assertRaises(ClanError):
            self.database.update_clan_settings(100, 200, "Cup Guild", "ALT", "#5865F2", True)
        with self.assertRaises(ClanError):
            self.database.update_clan_settings(100, 200, "Cup Guild", "CUP", "blue", True)
        self.assertEqual(self.database.get_clan(100, first["id"])["tag"], "CUP")


if __name__ == "__main__":
    unittest.main()
