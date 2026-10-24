import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from API.API import ClanDatabase, ClanError


RULES = {
    "member_limit": 5,
    "tag_min_length": 2,
    "tag_max_length": 5,
    "name_min_length": 3,
    "name_max_length": 32,
    "description_max_length": 300,
}
ECONOMY = {
    "currency_name": "крон",
    "daily_reward": 100,
    "daily_streak_bonus": 10,
    "daily_xp": 35,
    "work_reward_min": 30,
    "work_reward_max": 30,
    "work_xp": 15,
    "work_cooldown_seconds": 3600,
    "clan_income_base": 100,
    "clan_income_per_member": 15,
    "clan_income_leader_share_percent": 40,
    "event_max_reward": 1000,
    "event_xp": 25,
    "war_stake": 100,
}


class EconomyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = ClanDatabase(
            Path(self.temp_dir.name) / "clans.sqlite3",
            RULES,
            ECONOMY,
        )

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def create_clan(self, guild_id: int, owner_id: int, tag: str) -> dict:
        return self.database.create_clan(guild_id, owner_id, tag, f"{tag} Clan", "")

    def set_treasury(self, guild_id: int, clan_id: int, amount: int) -> None:
        self.database.connection.execute(
            "UPDATE clans SET treasury = ? WHERE guild_id = ? AND id = ?",
            (amount, guild_id, clan_id),
        )
        self.database.connection.commit()

    def test_daily_reward_streak_and_xp_are_atomic(self) -> None:
        yesterday = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
        today = yesterday + timedelta(days=1)

        first = self.database.claim_daily(100, 200, yesterday)
        second = self.database.claim_daily(100, 200, today)

        self.assertEqual((first["reward"], first["streak"]), (100, 1))
        self.assertEqual((second["reward"], second["streak"]), (110, 2))
        self.assertEqual(second["profile"]["balance"], 210)
        self.assertEqual(second["profile"]["xp"], 70)
        with self.assertRaises(ClanError):
            self.database.claim_daily(100, 200, today)

    def test_daily_streak_resets_after_a_missed_day(self) -> None:
        first_day = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        self.database.claim_daily(100, 200, first_day)

        reward = self.database.claim_daily(100, 200, first_day + timedelta(days=2))

        self.assertEqual(reward["streak"], 1)
        self.assertEqual(reward["reward"], 100)

    def test_work_has_cooldown_and_adds_balance_and_experience(self) -> None:
        now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)

        result = self.database.work(100, 200, now)

        self.assertEqual(result["reward"], 30)
        self.assertEqual(result["profile"]["balance"], 30)
        self.assertEqual(result["profile"]["xp"], 15)
        with self.assertRaises(ClanError):
            self.database.work(100, 200, now + timedelta(minutes=30))
        self.assertEqual(self.database.work(100, 200, now + timedelta(hours=1))["reward"], 30)

    def test_transfers_are_server_scoped_and_cannot_overdraw(self) -> None:
        self.database.claim_daily(100, 200)
        self.database.transfer_coins(100, 200, 201, 60)

        self.assertEqual(self.database.get_profile(100, 200)["balance"], 40)
        self.assertEqual(self.database.get_profile(100, 201)["balance"], 60)
        self.assertEqual(self.database.get_profile(101, 201)["balance"], 0)
        with self.assertRaises(ClanError):
            self.database.transfer_coins(100, 200, 201, 41)

    def test_level_progress_and_leaderboard(self) -> None:
        self.database.get_profile(100, 200)
        self.database.connection.execute(
            "UPDATE player_profiles SET xp = 95 WHERE guild_id = 100 AND user_id = 200"
        )
        self.database.connection.commit()
        self.database._add_xp(100, 200, 10)
        profile = self.database.get_profile(100, 200)

        self.assertEqual(profile["level"], 2)
        self.assertEqual(profile["xp_progress"], 5)
        self.assertEqual(profile["xp_required"], 300)
        self.assertEqual(self.database.economy_leaderboard(100)[0]["user_id"], 200)

    def test_clan_income_pays_founder_and_deputy_and_funds_treasury_once(self) -> None:
        clan = self.create_clan(100, 200, "ONE")
        self.database.join_clan(100, 201, clan["id"])
        self.database.change_member_rank(100, 200, 201, 1)
        self.database.change_member_rank(100, 200, 201, 1)
        self.database.change_member_rank(100, 200, 201, 1)
        today = datetime(2026, 10, 24, tzinfo=timezone.utc).date()

        income = self.database.claim_clan_income(100, 200, today)

        self.assertEqual(income["total"], 130)
        self.assertEqual(income["payments"], {200: 26, 201: 26})
        self.assertEqual(income["treasury_share"], 78)
        self.assertEqual(self.database.get_profile(100, 200)["balance"], 26)
        self.assertEqual(self.database.get_profile(100, 201)["balance"], 26)
        self.assertEqual(self.database.get_clan(100, clan["id"])["treasury"], 78)
        with self.assertRaises(ClanError):
            self.database.claim_clan_income(100, 201, today)
        next_day = self.database.claim_clan_income(100, 201, today + timedelta(days=1))
        self.assertEqual(next_day["payments"], {200: 26, 201: 26})

    def test_only_founder_and_deputies_can_claim_clan_income(self) -> None:
        clan = self.create_clan(100, 200, "ONE")
        self.database.join_clan(100, 201, clan["id"])

        with self.assertRaises(ClanError):
            self.database.claim_clan_income(100, 201)

    def test_owner_can_grant_clan_income_right_to_another_rank(self) -> None:
        clan = self.create_clan(100, 200, "ONE")
        self.database.join_clan(100, 201, clan["id"])
        self.database.set_role_permission(100, 200, "member", "clan_income", True)

        income = self.database.claim_clan_income(100, 201)

        self.assertEqual(income["payments"], {200: 26, 201: 26})

    def test_clan_bank_deposits_and_withdrawals_enforce_permissions(self) -> None:
        clan = self.create_clan(100, 200, "ONE")
        self.database.claim_daily(100, 201)
        self.database.join_clan(100, 201, clan["id"])
        self.database.move_clan_funds(100, 201, 50, deposit=True)

        with self.assertRaises(ClanError):
            self.database.move_clan_funds(100, 201, 10, deposit=False)
        self.database.move_clan_funds(100, 200, 20, deposit=False)

        self.assertEqual(self.database.get_clan(100, clan["id"])["treasury"], 30)
        self.assertEqual(self.database.get_profile(100, 200)["balance"], 20)

    def test_event_escrows_rewards_and_pays_registered_members(self) -> None:
        clan = self.create_clan(100, 200, "ONE")
        self.database.join_clan(100, 201, clan["id"])
        self.set_treasury(100, clan["id"], 500)
        event_id = self.database.create_clan_event(100, 200, "Турнир", "Финал", 101)
        self.assertEqual(self.database.get_clan(100, clan["id"])["treasury"], 399)

        self.database.join_clan_event(100, 200, event_id)
        self.database.join_clan_event(100, 201, event_id)
        with self.assertRaises(ClanError):
            self.database.join_clan_event(100, 201, event_id)
        result = self.database.finish_clan_event(100, 200, event_id)

        self.assertEqual(result["payments"], {200: 51, 201: 50})
        self.assertEqual(self.database.get_profile(100, 200)["balance"], 51)
        self.assertEqual(self.database.get_profile(100, 201)["xp"], 25)
        with self.assertRaises(ClanError):
            self.database.finish_clan_event(100, 200, event_id)

    def test_event_without_participants_refunds_the_treasury(self) -> None:
        clan = self.create_clan(100, 200, "ONE")
        self.set_treasury(100, clan["id"], 200)
        event_id = self.database.create_clan_event(100, 200, "Сбор", "", 100)

        result = self.database.finish_clan_event(100, 200, event_id)

        self.assertEqual(result["participants"], 0)
        self.assertEqual(self.database.get_clan(100, clan["id"])["treasury"], 200)

    def test_war_challenge_acceptance_scoring_and_prize(self) -> None:
        first = self.create_clan(100, 200, "ONE")
        second = self.create_clan(100, 300, "TWO")
        self.set_treasury(100, first["id"], 500)
        self.set_treasury(100, second["id"], 500)
        war_id = self.database.create_war_challenge(100, 200, "TWO")

        with self.assertRaises(ClanError):
            self.database.record_war_score(100, 200, war_id, 1)
        war = self.database.accept_war_challenge(100, 300, war_id)
        self.assertEqual(war["status"], "active")
        self.database.record_war_score(100, 200, war_id, 3)
        result = self.database.finish_war(100, 300, war_id)

        self.assertEqual(result["winner_id"], first["id"])
        self.assertEqual(self.database.get_clan(100, first["id"])["treasury"], 600)
        self.assertEqual(self.database.get_clan(100, second["id"])["treasury"], 400)

    def test_war_can_be_declined_without_changing_economy(self) -> None:
        first = self.create_clan(100, 200, "ONE")
        self.create_clan(100, 300, "TWO")
        war_id = self.database.create_war_challenge(100, 200, "TWO")

        self.database.cancel_war_challenge(100, 300, war_id)

        self.assertEqual(self.database.get_war(100, war_id)["status"], "declined")
        with self.assertRaises(ClanError):
            self.database.accept_war_challenge(100, 300, war_id)
        self.assertEqual(self.database.get_clan(100, first["id"])["treasury"], 0)


if __name__ == "__main__":
    unittest.main()
