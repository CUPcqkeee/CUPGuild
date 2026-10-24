import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class ClanError(Exception):
    """A user-facing clan operation could not be completed."""


RANK_LEVELS = {"member": 0, "veteran": 1, "officer": 2, "deputy": 3}
RANK_LABELS = {
    "member": "Участник",
    "veteran": "Ветеран",
    "officer": "Офицер",
    "deputy": "Заместитель",
}
CLAN_ROLES = ("deputy", "officer", "veteran", "member")
CLAN_PERMISSIONS = {
    "manage_ranks": "Повышать и понижать участников",
    "kick_members": "Исключать участников",
    "edit_clan": "Изменять параметры клана",
    "manage_events": "Управлять событиями",
    "manage_wars": "Управлять вызовами и войнами",
    "war_score": "Начислять очки в войнах",
    "clan_income": "Получать клановый доход",
    "withdraw_treasury": "Снимать монеты из казны",
}
DEFAULT_ROLE_PERMISSIONS = {
    "deputy": {permission: True for permission in CLAN_PERMISSIONS},
    "officer": {
        "manage_ranks": False,
        "kick_members": True,
        "edit_clan": False,
        "manage_events": False,
        "manage_wars": False,
        "war_score": True,
        "clan_income": False,
        "withdraw_treasury": False,
    },
    "veteran": {permission: False for permission in CLAN_PERMISSIONS},
    "member": {permission: False for permission in CLAN_PERMISSIONS},
}


class ClanDatabase:
    def __init__(
        self,
        database_path: Path,
        rules: dict[str, Any],
        economy: dict[str, Any] | None = None,
    ) -> None:
        self.member_limit = rules["member_limit"]
        self.tag_min_length = rules["tag_min_length"]
        self.tag_max_length = rules["tag_max_length"]
        self.name_min_length = rules["name_min_length"]
        self.name_max_length = rules["name_max_length"]
        self.description_max_length = rules["description_max_length"]
        self.economy = economy or {
            "daily_reward": 100,
            "daily_streak_bonus": 10,
            "daily_xp": 35,
            "work_reward_min": 20,
            "work_reward_max": 60,
            "work_xp": 15,
            "work_cooldown_seconds": 3600,
            "clan_income_base": 100,
            "clan_income_per_member": 15,
            "clan_income_leader_share_percent": 40,
            "event_max_reward": 10000,
            "event_xp": 25,
            "war_stake": 500,
        }

        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 10000")
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS clans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                tag TEXT NOT NULL COLLATE NOCASE,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                owner_id INTEGER NOT NULL,
                color TEXT NOT NULL DEFAULT '#5865F2',
                is_open INTEGER NOT NULL DEFAULT 1,
                discord_role_id INTEGER,
                category_id INTEGER,
                news_channel_id INTEGER,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                UNIQUE (guild_id, tag)
            );

            CREATE TABLE IF NOT EXISTS memberships (
                guild_id INTEGER NOT NULL,
                clan_id INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                joined_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS clan_role_permissions (
                clan_id INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                permissions TEXT NOT NULL,
                PRIMARY KEY (clan_id, role)
            );

            CREATE INDEX IF NOT EXISTS idx_clans_guild ON clans(guild_id);
            CREATE INDEX IF NOT EXISTS idx_memberships_clan ON memberships(clan_id);
            """
        )
        membership_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(memberships)")
        }
        if "role" not in membership_columns:
            self.connection.execute(
                "ALTER TABLE memberships ADD COLUMN role TEXT NOT NULL DEFAULT 'member'"
            )
        clan_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(clans)")}
        if "treasury" not in clan_columns:
            self.connection.execute(
                "ALTER TABLE clans ADD COLUMN treasury INTEGER NOT NULL DEFAULT 0"
            )
        if "color" not in clan_columns:
            self.connection.execute(
                "ALTER TABLE clans ADD COLUMN color TEXT NOT NULL DEFAULT '#5865F2'"
            )
        if "is_open" not in clan_columns:
            self.connection.execute(
                "ALTER TABLE clans ADD COLUMN is_open INTEGER NOT NULL DEFAULT 1"
            )
        for column in ("discord_role_id", "category_id", "news_channel_id"):
            if column not in clan_columns:
                self.connection.execute(f"ALTER TABLE clans ADD COLUMN {column} INTEGER")
        for clan_row in self.connection.execute("SELECT id FROM clans"):
            self._initialize_clan_permissions(clan_row["id"])
        self.connection.commit()
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS player_profiles (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
                xp INTEGER NOT NULL DEFAULT 0 CHECK (xp >= 0),
                level INTEGER NOT NULL DEFAULT 1 CHECK (level >= 1),
                daily_streak INTEGER NOT NULL DEFAULT 0,
                last_daily TEXT,
                last_work_at TEXT,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS economy_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );

            CREATE INDEX IF NOT EXISTS idx_economy_ledger_player
                ON economy_ledger(guild_id, user_id, id DESC);

            CREATE TABLE IF NOT EXISTS clan_income_claims (
                guild_id INTEGER NOT NULL,
                clan_id INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
                claim_date TEXT NOT NULL,
                total INTEGER NOT NULL,
                PRIMARY KEY (guild_id, clan_id, claim_date)
            );

            CREATE TABLE IF NOT EXISTS clan_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                clan_id INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
                creator_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                reward INTEGER NOT NULL CHECK (reward >= 0),
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS clan_event_participants (
                event_id INTEGER NOT NULL REFERENCES clan_events(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                clan_id INTEGER NOT NULL,
                PRIMARY KEY (event_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS clan_wars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                clan_one_id INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
                clan_two_id INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'pending',
                score_one INTEGER NOT NULL DEFAULT 0,
                score_two INTEGER NOT NULL DEFAULT 0,
                reward INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                finished_at TEXT,
                CHECK (clan_one_id != clan_two_id)
            );

            CREATE INDEX IF NOT EXISTS idx_clan_wars_guild_status
                ON clan_wars(guild_id, status);
            CREATE INDEX IF NOT EXISTS idx_clan_events_guild_status
                ON clan_events(guild_id, status);
            """
        )

    def close(self) -> None:
        self.connection.close()

    def _begin_write(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def _initialize_clan_permissions(self, clan_id: int) -> None:
        for role, permissions in DEFAULT_ROLE_PERMISSIONS.items():
            self.connection.execute(
                """
                INSERT OR IGNORE INTO clan_role_permissions (clan_id, role, permissions)
                VALUES (?, ?, ?)
                """,
                (clan_id, role, json.dumps(permissions)),
            )

    def get_clan_role_permissions(self, clan_id: int) -> dict[str, dict[str, bool]]:
        rows = self.connection.execute(
            "SELECT role, permissions FROM clan_role_permissions WHERE clan_id = ?",
            (clan_id,),
        ).fetchall()
        configured = {row["role"]: json.loads(row["permissions"]) for row in rows}
        return {
            role: configured.get(role, DEFAULT_ROLE_PERMISSIONS[role])
            for role in CLAN_ROLES
        }

    def has_permission(self, guild_id: int, user_id: int, permission: str) -> bool:
        if permission not in CLAN_PERMISSIONS:
            raise ValueError(f"Неизвестное право клана: {permission}")
        membership = self._get_membership(guild_id, user_id)
        if membership is None:
            return False
        if membership["owner_id"] == user_id:
            return True
        row = self.connection.execute(
            """
            SELECT permissions FROM clan_role_permissions
            WHERE clan_id = ? AND role = ?
            """,
            (membership["clan_id"], membership["role"]),
        ).fetchone()
        permissions = (
            json.loads(row["permissions"])
            if row
            else DEFAULT_ROLE_PERMISSIONS[membership["role"]]
        )
        return bool(permissions.get(permission, False))

    def _require_permission(self, guild_id: int, user_id: int, permission: str) -> sqlite3.Row:
        membership = self._require_member(guild_id, user_id)
        if not self.has_permission(guild_id, user_id, permission):
            raise ClanError(f"У вашего ранга нет права «{CLAN_PERMISSIONS[permission]}»")
        return membership

    def set_role_permission(
        self,
        guild_id: int,
        owner_id: int,
        role: str,
        permission: str,
        enabled: bool,
    ) -> None:
        if role not in CLAN_ROLES or permission not in CLAN_PERMISSIONS:
            raise ClanError("Неизвестный ранг или тип права")
        self._begin_write()
        try:
            membership = self._require_member(guild_id, owner_id)
            if membership["owner_id"] != owner_id:
                raise ClanError("Настраивать права рангов может только владелец клана")
            permissions = self.get_clan_role_permissions(membership["clan_id"])
            permissions[role][permission] = enabled
            self.connection.execute(
                """
                INSERT INTO clan_role_permissions (clan_id, role, permissions)
                VALUES (?, ?, ?)
                ON CONFLICT(clan_id, role) DO UPDATE SET permissions = excluded.permissions
                """,
                (membership["clan_id"], role, json.dumps(permissions[role])),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def update_clan_settings(
        self,
        guild_id: int,
        user_id: int,
        name: str,
        tag: str,
        color: str,
        is_open: bool,
    ) -> dict[str, Any]:
        name = name.strip()
        tag = tag.strip().upper()
        color = color.strip().upper()
        if not self.name_min_length <= len(name) <= self.name_max_length:
            raise ClanError(f"Название должно содержать от {self.name_min_length} до {self.name_max_length} символов")
        if not self.tag_min_length <= len(tag) <= self.tag_max_length or not re.fullmatch(r"[A-Z0-9]+", tag):
            raise ClanError(f"Тег должен содержать от {self.tag_min_length} до {self.tag_max_length} латинских букв или цифр")
        if not re.fullmatch(r"#[0-9A-F]{6}", color):
            raise ClanError("Цвет должен быть в формате #RRGGBB, например #5865F2")
        self._begin_write()
        try:
            membership = self._require_permission(guild_id, user_id, "edit_clan")
            self.connection.execute(
                """
                UPDATE clans SET name = ?, tag = ?, color = ?, is_open = ?
                WHERE guild_id = ? AND id = ?
                """,
                (name, tag, color, int(is_open), guild_id, membership["clan_id"]),
            )
            self.connection.commit()
        except sqlite3.IntegrityError as error:
            self.connection.rollback()
            raise ClanError("Этот тег уже используется другим кланом") from error
        except Exception:
            self.connection.rollback()
            raise
        clan = self.get_clan(guild_id, membership["clan_id"])
        if clan is None:
            raise RuntimeError("Клан не найден после обновления параметров")
        return clan

    def save_clan_discord_resources(
        self,
        guild_id: int,
        owner_id: int,
        role_id: int,
        category_id: int,
        news_channel_id: int,
    ) -> None:
        membership = self._require_member(guild_id, owner_id)
        if membership["owner_id"] != owner_id:
            raise ClanError("Сохранить каналы клана может только владелец")
        self.connection.execute(
            """
            UPDATE clans
            SET discord_role_id = ?, category_id = ?, news_channel_id = ?
            WHERE guild_id = ? AND id = ?
            """,
            (role_id, category_id, news_channel_id, guild_id, membership["clan_id"]),
        )
        self.connection.commit()

    def create_clan(self, guild_id: int, user_id: int, tag: str, name: str, description: str) -> dict[str, Any]:
        tag = tag.strip().upper()
        name = name.strip()
        description = description.strip()

        if not self.tag_min_length <= len(tag) <= self.tag_max_length or not re.fullmatch(r"[A-Z0-9]+", tag):
            raise ClanError(
                f"Тег должен содержать от {self.tag_min_length} до {self.tag_max_length} латинских букв или цифр."
            )
        if not self.name_min_length <= len(name) <= self.name_max_length:
            raise ClanError(f"Название должно содержать от {self.name_min_length} до {self.name_max_length} символов.")
        if len(description) > self.description_max_length:
            raise ClanError(f"Описание не может быть длиннее {self.description_max_length} символов.")

        self._begin_write()
        try:
            membership = self.connection.execute(
                "SELECT 1 FROM memberships WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            if membership:
                raise ClanError("Вы уже состоите в клане на этом сервере.")

            duplicate = self.connection.execute(
                "SELECT 1 FROM clans WHERE guild_id = ? AND tag = ?",
                (guild_id, tag),
            ).fetchone()
            if duplicate:
                raise ClanError(f"Тег **{tag}** уже занят на этом сервере.")

            cursor = self.connection.execute(
                "INSERT INTO clans (guild_id, tag, name, description, owner_id) VALUES (?, ?, ?, ?, ?)",
                (guild_id, tag, name, description, user_id),
            )
            self._initialize_clan_permissions(int(cursor.lastrowid))
            self.connection.execute(
                "INSERT INTO memberships (guild_id, clan_id, user_id) VALUES (?, ?, ?)",
                (guild_id, cursor.lastrowid, user_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

        clan = self.get_clan(guild_id, cursor.lastrowid)
        if clan is None:
            raise RuntimeError("Созданный клан не найден в базе данных.")
        return clan

    def get_clan(self, guild_id: int, clan_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT c.*, COUNT(m.user_id) AS member_count
            FROM clans AS c
            LEFT JOIN memberships AS m ON m.clan_id = c.id AND m.guild_id = c.guild_id
            WHERE c.guild_id = ? AND c.id = ?
            GROUP BY c.id
            """,
            (guild_id, clan_id),
        ).fetchone()
        return self._dict(row)

    def get_user_clan(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT c.*, mine.role AS member_role, COUNT(m.user_id) AS member_count
            FROM memberships AS mine
            JOIN clans AS c ON c.id = mine.clan_id AND c.guild_id = mine.guild_id
            LEFT JOIN memberships AS m ON m.clan_id = c.id AND m.guild_id = c.guild_id
            WHERE mine.guild_id = ? AND mine.user_id = ?
            GROUP BY c.id
            """,
            (guild_id, user_id),
        ).fetchone()
        return self._dict(row)

    def get_members(self, guild_id: int, clan_id: int, limit: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT user_id, role, joined_at
            FROM memberships
            WHERE guild_id = ? AND clan_id = ?
            ORDER BY CASE WHEN user_id = (
                SELECT owner_id FROM clans WHERE id = ? AND guild_id = ?
            ) THEN 0 ELSE 1 END, joined_at, user_id
            LIMIT ?
            """,
            (guild_id, clan_id, clan_id, guild_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def _get_membership(self, guild_id: int, user_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT c.id AS clan_id, c.owner_id, m.role
            FROM memberships AS m
            JOIN clans AS c ON c.id = m.clan_id AND c.guild_id = m.guild_id
            WHERE m.guild_id = ? AND m.user_id = ?
            """,
            (guild_id, user_id),
        ).fetchone()

    def _require_member(self, guild_id: int, user_id: int) -> sqlite3.Row:
        membership = self._get_membership(guild_id, user_id)
        if membership is None:
            raise ClanError("Участник уже не состоит в этом клане.")
        return membership

    @staticmethod
    def _rank_level(membership: sqlite3.Row, user_id: int) -> int:
        if membership["owner_id"] == user_id:
            return 4
        return RANK_LEVELS[membership["role"]]

    def _require_manage_target(self, guild_id: int, actor_id: int, target_id: int) -> tuple[sqlite3.Row, sqlite3.Row]:
        actor = self._require_member(guild_id, actor_id)
        target = self._require_member(guild_id, target_id)
        if actor["clan_id"] != target["clan_id"]:
            raise ClanError("Этот участник не состоит в вашем клане.")
        if actor_id == target_id:
            raise ClanError("Нельзя управлять собственным рангом.")
        if target["owner_id"] == target_id:
            raise ClanError("Нельзя изменить права владельца клана")
        if self._rank_level(actor, actor_id) <= self._rank_level(target, target_id):
            raise ClanError("Ваш ранг должен быть выше ранга выбранного участника.")
        return actor, target

    def change_member_rank(self, guild_id: int, actor_id: int, target_id: int, direction: int) -> str:
        if direction not in (-1, 1):
            raise ValueError("Направление изменения ранга должно быть -1 или 1.")

        self._begin_write()
        try:
            actor, target = self._require_manage_target(guild_id, actor_id, target_id)
            actor_level = self._rank_level(actor, actor_id)
            target_level = self._rank_level(target, target_id)
            self._require_permission(guild_id, actor_id, "manage_ranks")

            new_level = target_level + direction
            if direction > 0 and new_level >= actor_level:
                raise ClanError("Нельзя назначить ранг, равный вашему или выше.")
            if direction < 0 and new_level < 0:
                raise ClanError("У участника уже начальный ранг.")

            new_role = {level: role for role, level in RANK_LEVELS.items()}[new_level]
            self.connection.execute(
                "UPDATE memberships SET role = ? WHERE guild_id = ? AND user_id = ?",
                (new_role, guild_id, target_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return new_role

    def kick_member(self, guild_id: int, actor_id: int, target_id: int) -> None:
        self._begin_write()
        try:
            actor, _ = self._require_manage_target(guild_id, actor_id, target_id)
            self._require_permission(guild_id, actor_id, "kick_members")
            self.connection.execute(
                "DELETE FROM memberships WHERE guild_id = ? AND user_id = ?",
                (guild_id, target_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def leave_clan(self, guild_id: int, user_id: int) -> str:
        self._begin_write()
        try:
            membership = self._require_member(guild_id, user_id)
            if membership["owner_id"] == user_id:
                raise ClanError("Владелец не может покинуть клан. Передайте права или распустите клан.")
            self.connection.execute(
                "DELETE FROM memberships WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return str(membership["clan_id"])

    def disband_clan(self, guild_id: int, owner_id: int) -> None:
        self._begin_write()
        try:
            membership = self._require_member(guild_id, owner_id)
            if membership["owner_id"] != owner_id:
                raise ClanError("Распустить клан может только владелец.")
            self.connection.execute(
                "DELETE FROM clans WHERE guild_id = ? AND id = ?",
                (guild_id, membership["clan_id"]),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def _ensure_profile(self, guild_id: int, user_id: int) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO player_profiles (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )

    def _add_xp(self, guild_id: int, user_id: int, amount: int) -> tuple[int, int]:
        self._ensure_profile(guild_id, user_id)
        row = self.connection.execute(
            "SELECT xp, level FROM player_profiles WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        old_level = row["level"]
        new_xp = row["xp"] + amount
        new_level = 1
        while new_xp >= new_level * new_level * 100:
            new_level += 1
        self.connection.execute(
            "UPDATE player_profiles SET xp = ?, level = ? WHERE guild_id = ? AND user_id = ?",
            (new_xp, new_level, guild_id, user_id),
        )
        return new_xp, new_level - old_level

    def _credit_player(self, guild_id: int, user_id: int, amount: int, reason: str) -> None:
        self._ensure_profile(guild_id, user_id)
        self.connection.execute(
            "UPDATE player_profiles SET balance = balance + ? WHERE guild_id = ? AND user_id = ?",
            (amount, guild_id, user_id),
        )
        self.connection.execute(
            "INSERT INTO economy_ledger (guild_id, user_id, amount, reason) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, amount, reason),
        )

    def get_profile(self, guild_id: int, user_id: int) -> dict[str, Any]:
        self._ensure_profile(guild_id, user_id)
        self.connection.commit()
        row = self.connection.execute(
            """
            SELECT p.*, m.clan_id, m.role AS clan_role, c.name AS clan_name, c.tag AS clan_tag
            FROM player_profiles AS p
            LEFT JOIN memberships AS m ON m.guild_id = p.guild_id AND m.user_id = p.user_id
            LEFT JOIN clans AS c ON c.id = m.clan_id AND c.guild_id = m.guild_id
            WHERE p.guild_id = ? AND p.user_id = ?
            """,
            (guild_id, user_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("Не удалось загрузить профиль после его создания.")
        profile = dict(row)
        profile["xp_for_next_level"] = profile["level"] * profile["level"] * 100
        profile["xp_progress"] = profile["xp"] - (profile["level"] - 1) ** 2 * 100
        profile["xp_required"] = profile["xp_for_next_level"] - (profile["level"] - 1) ** 2 * 100
        return profile

    def claim_daily(self, guild_id: int, user_id: int, now: datetime | None = None) -> dict[str, Any]:
        now = now or self._utc_now()
        today = now.astimezone(timezone.utc).date()
        self._begin_write()
        try:
            self._ensure_profile(guild_id, user_id)
            row = self.connection.execute(
                "SELECT daily_streak, last_daily FROM player_profiles WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            if row["last_daily"] == today.isoformat():
                raise ClanError("Ежедневная награда уже получена. Загляните снова завтра.")
            yesterday = (today - timedelta(days=1)).isoformat()
            streak = row["daily_streak"] + 1 if row["last_daily"] == yesterday else 1
            reward = self.economy["daily_reward"] + (streak - 1) * self.economy["daily_streak_bonus"]
            xp_reward = self.economy["daily_xp"]
            self.connection.execute(
                "UPDATE player_profiles SET daily_streak = ?, last_daily = ? WHERE guild_id = ? AND user_id = ?",
                (streak, today.isoformat(), guild_id, user_id),
            )
            self._credit_player(guild_id, user_id, reward, "daily")
            xp, levels_gained = self._add_xp(guild_id, user_id, xp_reward)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return {
            "reward": reward,
            "xp_reward": xp_reward,
            "streak": streak,
            "levels_gained": levels_gained,
            "xp": xp,
            "profile": self.get_profile(guild_id, user_id),
        }

    def work(self, guild_id: int, user_id: int, now: datetime | None = None) -> dict[str, Any]:
        import random

        now = now or self._utc_now()
        now = now.astimezone(timezone.utc)
        self._begin_write()
        try:
            self._ensure_profile(guild_id, user_id)
            row = self.connection.execute(
                "SELECT last_work_at FROM player_profiles WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            if row["last_work_at"]:
                last_work = datetime.fromisoformat(row["last_work_at"])
                available_at = last_work + timedelta(seconds=self.economy["work_cooldown_seconds"])
                if now < available_at:
                    remaining = int((available_at - now).total_seconds())
                    minutes, seconds = divmod(remaining, 60)
                    raise ClanError(f"Отдохните ещё **{minutes} мин. {seconds} сек.** перед следующей работой.")

            reward = random.randint(
                self.economy["work_reward_min"],
                self.economy["work_reward_max"],
            )
            xp_reward = self.economy["work_xp"]
            self.connection.execute(
                "UPDATE player_profiles SET last_work_at = ? WHERE guild_id = ? AND user_id = ?",
                (now.isoformat(), guild_id, user_id),
            )
            self._credit_player(guild_id, user_id, reward, "work")
            xp, levels_gained = self._add_xp(guild_id, user_id, xp_reward)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return {
            "reward": reward,
            "xp_reward": xp_reward,
            "levels_gained": levels_gained,
            "xp": xp,
            "profile": self.get_profile(guild_id, user_id),
        }

    def transfer_coins(self, guild_id: int, sender_id: int, recipient_id: int, amount: int) -> None:
        if sender_id == recipient_id:
            raise ClanError("Нельзя переводить монеты самому себе.")
        if amount < 1:
            raise ClanError("Сумма перевода должна быть положительной.")
        self._begin_write()
        try:
            self._ensure_profile(guild_id, sender_id)
            self._ensure_profile(guild_id, recipient_id)
            result = self.connection.execute(
                """
                UPDATE player_profiles SET balance = balance - ?
                WHERE guild_id = ? AND user_id = ? AND balance >= ?
                """,
                (amount, guild_id, sender_id, amount),
            )
            if result.rowcount != 1:
                raise ClanError("Недостаточно монет для перевода.")
            self._credit_player(guild_id, recipient_id, amount, f"transfer_from:{sender_id}")
            self.connection.execute(
                "INSERT INTO economy_ledger (guild_id, user_id, amount, reason) VALUES (?, ?, ?, ?)",
                (guild_id, sender_id, -amount, f"transfer_to:{recipient_id}"),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def economy_leaderboard(self, guild_id: int, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT user_id, balance, level, xp
            FROM player_profiles
            WHERE guild_id = ?
            ORDER BY balance DESC, level DESC, xp DESC, user_id ASC
            LIMIT ?
            """,
            (guild_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def claim_clan_income(
        self,
        guild_id: int,
        user_id: int,
        today: date | None = None,
    ) -> dict[str, Any]:
        today = today or self._utc_now().date()
        self._begin_write()
        try:
            actor = self._require_permission(guild_id, user_id, "clan_income")
            clan_id = actor["clan_id"]
            members = self.connection.execute(
                """
                SELECT user_id, role
                FROM memberships
                WHERE guild_id = ? AND clan_id = ?
                ORDER BY CASE WHEN user_id = ? THEN 0 ELSE 1 END, user_id
                """,
                (guild_id, clan_id, actor["owner_id"]),
            ).fetchall()
            total = (
                self.economy["clan_income_base"]
                + len(members) * self.economy["clan_income_per_member"]
            )
            already_claimed = self.connection.execute(
                "SELECT 1 FROM clan_income_claims WHERE guild_id = ? AND clan_id = ? AND claim_date = ?",
                (guild_id, clan_id, today.isoformat()),
            ).fetchone()
            if already_claimed:
                raise ClanError("Клановый доход уже получен сегодня.")
            try:
                self.connection.execute(
                    "INSERT INTO clan_income_claims (guild_id, clan_id, claim_date, total) VALUES (?, ?, ?, ?)",
                    (guild_id, clan_id, today.isoformat(), total),
                )
            except sqlite3.IntegrityError as error:
                raise ClanError("Клановый доход уже получен сегодня.") from error

            payout_percent = self.economy["clan_income_leader_share_percent"]
            payout_pool = total * payout_percent // 100
            treasury_share = total - payout_pool
            recipients = [
                member
                for member in members
                if self.has_permission(guild_id, member["user_id"], "clan_income")
            ]
            if not recipients:
                raise RuntimeError("У клана не найден владелец или заместители для выплаты.")
            base_share, remainder = divmod(payout_pool, len(recipients))
            payments: dict[int, int] = {}
            for index, recipient in enumerate(recipients):
                amount = base_share + (1 if index < remainder else 0)
                payments[recipient["user_id"]] = amount
                if amount:
                    self._credit_player(guild_id, recipient["user_id"], amount, "clan_income")

            self.connection.execute(
                "UPDATE clans SET treasury = treasury + ? WHERE guild_id = ? AND id = ?",
                (treasury_share, guild_id, clan_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return {"total": total, "treasury_share": treasury_share, "payments": payments}

    def move_clan_funds(self, guild_id: int, user_id: int, amount: int, *, deposit: bool) -> int:
        if amount < 1:
            raise ClanError("Сумма должна быть положительной.")
        self._begin_write()
        try:
            membership = self._require_member(guild_id, user_id)
            clan_id = membership["clan_id"]
            if deposit:
                self._ensure_profile(guild_id, user_id)
                result = self.connection.execute(
                    """
                    UPDATE player_profiles SET balance = balance - ?
                    WHERE guild_id = ? AND user_id = ? AND balance >= ?
                    """,
                    (amount, guild_id, user_id, amount),
                )
                if result.rowcount != 1:
                    raise ClanError("Недостаточно монет на личном балансе.")
                self.connection.execute(
                    "UPDATE clans SET treasury = treasury + ? WHERE guild_id = ? AND id = ?",
                    (amount, guild_id, clan_id),
                )
                self.connection.execute(
                    "INSERT INTO economy_ledger (guild_id, user_id, amount, reason) VALUES (?, ?, ?, 'clan_deposit')",
                    (guild_id, user_id, -amount),
                )
            else:
                self._require_permission(guild_id, user_id, "withdraw_treasury")
                result = self.connection.execute(
                    "UPDATE clans SET treasury = treasury - ? WHERE guild_id = ? AND id = ? AND treasury >= ?",
                    (amount, guild_id, clan_id, amount),
                )
                if result.rowcount != 1:
                    raise ClanError("В казне клана недостаточно монет.")
                self._credit_player(guild_id, user_id, amount, "clan_withdrawal")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        clan = self.get_clan(guild_id, clan_id)
        if clan is None:
            raise RuntimeError("Клан не найден после операции с казной.")
        return clan["treasury"]

    def create_clan_event(
        self,
        guild_id: int,
        creator_id: int,
        title: str,
        description: str,
        reward: int,
    ) -> int:
        title = title.strip()
        description = description.strip()
        if not title or len(title) > 100:
            raise ClanError("Название события должно содержать от 1 до 100 символов.")
        if len(description) > 500:
            raise ClanError("Описание события не может превышать 500 символов.")
        if reward < 0 or reward > self.economy["event_max_reward"]:
            raise ClanError(f"Награда события должна быть от 0 до {self.economy['event_max_reward']} монет.")
        self._begin_write()
        try:
            membership = self._require_permission(guild_id, creator_id, "manage_events")
            result = self.connection.execute(
                "UPDATE clans SET treasury = treasury - ? WHERE guild_id = ? AND id = ? AND treasury >= ?",
                (reward, guild_id, membership["clan_id"], reward),
            )
            if result.rowcount != 1:
                raise ClanError("В казне клана недостаточно средств для такой награды.")
            cursor = self.connection.execute(
                """
                INSERT INTO clan_events (guild_id, clan_id, creator_id, title, description, reward)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, membership["clan_id"], creator_id, title, description, reward),
            )
            self.connection.commit()
            return int(cursor.lastrowid)
        except Exception:
            self.connection.rollback()
            raise

    def join_clan_event(self, guild_id: int, user_id: int, event_id: int) -> dict[str, Any]:
        self._begin_write()
        try:
            membership = self._require_member(guild_id, user_id)
            event = self.connection.execute(
                "SELECT * FROM clan_events WHERE guild_id = ? AND id = ?",
                (guild_id, event_id),
            ).fetchone()
            if event is None or event["status"] != "open":
                raise ClanError("Событие не найдено или регистрация закрыта.")
            if event["clan_id"] != membership["clan_id"]:
                raise ClanError("В этом событии могут участвовать только участники клана-организатора.")
            self.connection.execute(
                "INSERT INTO clan_event_participants (event_id, guild_id, clan_id, user_id) VALUES (?, ?, ?, ?)",
                (event_id, guild_id, membership["clan_id"], user_id),
            )
            self.connection.commit()
        except sqlite3.IntegrityError as error:
            self.connection.rollback()
            raise ClanError("Вы уже записаны на это событие.") from error
        except Exception:
            self.connection.rollback()
            raise
        return dict(event)

    def list_clan_events(self, guild_id: int, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        membership = self._get_membership(guild_id, user_id)
        if membership is None:
            return []
        rows = self.connection.execute(
            """
            SELECT e.*, COUNT(p.user_id) AS participant_count, c.tag AS clan_tag, c.name AS clan_name
            FROM clan_events AS e
            JOIN clans AS c ON c.id = e.clan_id AND c.guild_id = e.guild_id
            LEFT JOIN clan_event_participants AS p ON p.event_id = e.id
            WHERE e.guild_id = ? AND e.clan_id = ? AND e.status = 'open'
            GROUP BY e.id
            ORDER BY e.id DESC
            LIMIT ?
            """,
            (guild_id, membership["clan_id"], limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def finish_clan_event(self, guild_id: int, organizer_id: int, event_id: int) -> dict[str, Any]:
        self._begin_write()
        try:
            organizer = self._require_permission(guild_id, organizer_id, "manage_events")
            event = self.connection.execute(
                "SELECT * FROM clan_events WHERE guild_id = ? AND id = ?",
                (guild_id, event_id),
            ).fetchone()
            if event is None or event["status"] != "open":
                raise ClanError("Событие не найдено или уже завершено.")
            if event["clan_id"] != organizer["clan_id"]:
                raise ClanError("Это событие создано другим кланом.")
            participants = self.connection.execute(
                "SELECT user_id FROM clan_event_participants WHERE event_id = ? ORDER BY user_id",
                (event_id,),
            ).fetchall()
            reward = event["reward"]
            per_player = reward // len(participants) if participants else 0
            remainder = reward % len(participants) if participants else reward
            payments: dict[int, int] = {}
            for index, participant in enumerate(participants):
                amount = per_player + (1 if index < remainder else 0)
                payments[participant["user_id"]] = amount
                if amount:
                    self._credit_player(guild_id, participant["user_id"], amount, f"event:{event_id}")
                    self._add_xp(guild_id, participant["user_id"], self.economy["event_xp"])
            if not participants and reward:
                self.connection.execute(
                    "UPDATE clans SET treasury = treasury + ? WHERE guild_id = ? AND id = ?",
                    (reward, guild_id, event["clan_id"]),
                )
            self.connection.execute(
                "UPDATE clan_events SET status = 'finished', finished_at = ? WHERE id = ?",
                (self._utc_now().isoformat(), event_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return {"title": event["title"], "payments": payments, "participants": len(participants)}

    def create_war_challenge(self, guild_id: int, challenger_id: int, target_tag: str) -> int:
        self._begin_write()
        try:
            challenger = self._require_permission(guild_id, challenger_id, "manage_wars")
            target = self.connection.execute(
                "SELECT id, tag, name FROM clans WHERE guild_id = ? AND tag = ? COLLATE NOCASE",
                (guild_id, target_tag.strip()),
            ).fetchone()
            if target is None:
                raise ClanError("Клан с таким тегом не найден на этом сервере.")
            if target["id"] == challenger["clan_id"]:
                raise ClanError("Нельзя объявить войну собственному клану.")
            active = self.connection.execute(
                """
                SELECT 1 FROM clan_wars
                WHERE guild_id = ? AND status IN ('pending', 'active')
                  AND (clan_one_id IN (?, ?) OR clan_two_id IN (?, ?))
                LIMIT 1
                """,
                (guild_id, challenger["clan_id"], target["id"], challenger["clan_id"], target["id"]),
            ).fetchone()
            if active:
                raise ClanError("Один из кланов уже участвует в активной войне или ожидает ответа.")
            cursor = self.connection.execute(
                """
                INSERT INTO clan_wars (guild_id, clan_one_id, clan_two_id, reward)
                VALUES (?, ?, ?, ?)
                """,
                (
                    guild_id,
                    challenger["clan_id"],
                    target["id"],
                    self.economy["war_stake"] * 2,
                ),
            )
            self.connection.commit()
            return int(cursor.lastrowid)
        except Exception:
            self.connection.rollback()
            raise

    def cancel_war_challenge(self, guild_id: int, user_id: int, war_id: int) -> None:
        self._begin_write()
        try:
            membership = self._require_permission(guild_id, user_id, "manage_wars")
            war = self.connection.execute(
                "SELECT * FROM clan_wars WHERE guild_id = ? AND id = ? AND status = 'pending'",
                (guild_id, war_id),
            ).fetchone()
            if war is None:
                raise ClanError("Ожидающий вызов с таким номером не найден.")
            if membership["clan_id"] not in (war["clan_one_id"], war["clan_two_id"]):
                raise ClanError("Ваш клан не участвует в этом вызове.")
            self.connection.execute(
                "UPDATE clan_wars SET status = 'declined', finished_at = ? WHERE guild_id = ? AND id = ?",
                (self._utc_now().isoformat(), guild_id, war_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def accept_war_challenge(self, guild_id: int, user_id: int, war_id: int) -> dict[str, Any]:
        self._begin_write()
        try:
            member = self._require_permission(guild_id, user_id, "manage_wars")
            war = self.connection.execute(
                "SELECT * FROM clan_wars WHERE guild_id = ? AND id = ? AND status = 'pending'",
                (guild_id, war_id),
            ).fetchone()
            if war is None:
                raise ClanError("Ожидающий вызов с таким номером не найден.")
            if war["clan_two_id"] != member["clan_id"]:
                raise ClanError("Принять этот вызов может только клан-цель.")
            stake = self.economy["war_stake"]
            for clan_id in (war["clan_one_id"], war["clan_two_id"]):
                result = self.connection.execute(
                    "UPDATE clans SET treasury = treasury - ? WHERE guild_id = ? AND id = ? AND treasury >= ?",
                    (stake, guild_id, clan_id, stake),
                )
                if result.rowcount != 1:
                    raise ClanError("Обоим кланам нужна достаточная казна для ставки войны.")
            self.connection.execute(
                "UPDATE clan_wars SET status = 'active' WHERE guild_id = ? AND id = ?",
                (guild_id, war_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        updated = self.get_war(guild_id, war_id)
        if updated is None:
            raise RuntimeError("Война не найдена после принятия вызова.")
        return updated

    def get_war(self, guild_id: int, war_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT w.*, one.tag AS clan_one_tag, one.name AS clan_one_name,
                   two.tag AS clan_two_tag, two.name AS clan_two_name
            FROM clan_wars AS w
            JOIN clans AS one ON one.id = w.clan_one_id AND one.guild_id = w.guild_id
            JOIN clans AS two ON two.id = w.clan_two_id AND two.guild_id = w.guild_id
            WHERE w.guild_id = ? AND w.id = ?
            """,
            (guild_id, war_id),
        ).fetchone()
        return self._dict(row)

    def list_clan_wars(self, guild_id: int, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        membership = self._get_membership(guild_id, user_id)
        if membership is None:
            return []
        rows = self.connection.execute(
            """
            SELECT w.*, one.tag AS clan_one_tag, one.name AS clan_one_name,
                   two.tag AS clan_two_tag, two.name AS clan_two_name
            FROM clan_wars AS w
            JOIN clans AS one ON one.id = w.clan_one_id AND one.guild_id = w.guild_id
            JOIN clans AS two ON two.id = w.clan_two_id AND two.guild_id = w.guild_id
            WHERE w.guild_id = ?
              AND (w.clan_one_id = ? OR w.clan_two_id = ?)
              AND w.status IN ('pending', 'active')
            ORDER BY CASE w.status WHEN 'pending' THEN 0 ELSE 1 END, w.id DESC
            LIMIT ?
            """,
            (guild_id, membership["clan_id"], membership["clan_id"], limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_war_score(self, guild_id: int, user_id: int, war_id: int, points: int) -> dict[str, Any]:
        if not 1 <= points <= 100:
            raise ClanError("За один раз можно добавить от 1 до 100 очков.")
        self._begin_write()
        try:
            member = self._require_permission(guild_id, user_id, "war_score")
            war = self.connection.execute(
                "SELECT * FROM clan_wars WHERE guild_id = ? AND id = ? AND status = 'active'",
                (guild_id, war_id),
            ).fetchone()
            if war is None:
                raise ClanError("Активная война с таким номером не найдена.")
            if member["clan_id"] == war["clan_one_id"]:
                column = "score_one"
            elif member["clan_id"] == war["clan_two_id"]:
                column = "score_two"
            else:
                raise ClanError("Ваш клан не участвует в этой войне.")
            self.connection.execute(
                f"UPDATE clan_wars SET {column} = {column} + ? WHERE guild_id = ? AND id = ?",
                (points, guild_id, war_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        war_result = self.get_war(guild_id, war_id)
        if war_result is None:
            raise RuntimeError("Война не найдена после обновления счёта.")
        return war_result

    def finish_war(self, guild_id: int, user_id: int, war_id: int) -> dict[str, Any]:
        self._begin_write()
        try:
            member = self._require_permission(guild_id, user_id, "manage_wars")
            war = self.connection.execute(
                "SELECT * FROM clan_wars WHERE guild_id = ? AND id = ? AND status = 'active'",
                (guild_id, war_id),
            ).fetchone()
            if war is None:
                raise ClanError("Активная война с таким номером не найдена.")
            if member["clan_id"] not in (war["clan_one_id"], war["clan_two_id"]):
                raise ClanError("Ваш клан не участвует в этой войне.")
            winner_id = None
            if war["score_one"] > war["score_two"]:
                winner_id = war["clan_one_id"]
            elif war["score_two"] > war["score_one"]:
                winner_id = war["clan_two_id"]

            if winner_id is None:
                stake = self.economy["war_stake"]
                for clan_id in (war["clan_one_id"], war["clan_two_id"]):
                    self.connection.execute(
                        "UPDATE clans SET treasury = treasury + ? WHERE guild_id = ? AND id = ?",
                        (stake, guild_id, clan_id),
                    )
            else:
                self.connection.execute(
                    "UPDATE clans SET treasury = treasury + ? WHERE guild_id = ? AND id = ?",
                    (war["reward"], guild_id, winner_id),
                )
            self.connection.execute(
                "UPDATE clan_wars SET status = 'finished', finished_at = ? WHERE guild_id = ? AND id = ?",
                (self._utc_now().isoformat(), guild_id, war_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        result = self.get_war(guild_id, war_id)
        if result is None:
            raise RuntimeError("Завершённая война не найдена.")
        result["winner_id"] = winner_id
        return result

    def get_guild_stats(self, guild_id: int) -> tuple[int, int]:
        row = self.connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM clans WHERE guild_id = ?) AS clan_count,
                (SELECT COUNT(*) FROM memberships WHERE guild_id = ?) AS member_count
            """,
            (guild_id, guild_id),
        ).fetchone()
        return row["clan_count"], row["member_count"]

    def list_clans(self, guild_id: int, limit: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT c.*, COUNT(m.user_id) AS member_count
            FROM clans AS c
            LEFT JOIN memberships AS m ON m.clan_id = c.id AND m.guild_id = c.guild_id
            WHERE c.guild_id = ?
            GROUP BY c.id
            ORDER BY member_count DESC, c.created_at ASC, c.id ASC
            LIMIT ?
            """,
            (guild_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def discover_clans(self, guild_id: int, user_id: int, limit: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT c.*, COUNT(m.user_id) AS member_count
            FROM clans AS c
            LEFT JOIN memberships AS m ON m.clan_id = c.id AND m.guild_id = c.guild_id
            WHERE c.guild_id = ?
              AND c.is_open = 1
              AND NOT EXISTS (
                  SELECT 1 FROM memberships AS mine
                  WHERE mine.guild_id = ? AND mine.user_id = ?
              )
            GROUP BY c.id
            HAVING COUNT(m.user_id) < ?
            ORDER BY member_count DESC, c.created_at ASC, c.id ASC
            LIMIT ?
            """,
            (guild_id, guild_id, user_id, self.member_limit, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def join_clan(self, guild_id: int, user_id: int, clan_id: int) -> dict[str, Any]:
        self._begin_write()
        try:
            membership = self.connection.execute(
                "SELECT 1 FROM memberships WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            if membership:
                raise ClanError("Вы уже состоите в клане на этом сервере.")

            clan = self.connection.execute(
                """
                SELECT c.id, c.tag, c.name, c.is_open, COUNT(m.user_id) AS member_count
                FROM clans AS c
                LEFT JOIN memberships AS m ON m.clan_id = c.id AND m.guild_id = c.guild_id
                WHERE c.guild_id = ? AND c.id = ?
                GROUP BY c.id
                """,
                (guild_id, clan_id),
            ).fetchone()
            if clan is None:
                raise ClanError("Этот клан больше не существует.")
            if not clan["is_open"]:
                raise ClanError("Этот клан закрыт для вступления.")
            if clan["member_count"] >= self.member_limit:
                raise ClanError("В этом клане больше нет свободных мест.")

            self.connection.execute(
                "INSERT INTO memberships (guild_id, clan_id, user_id) VALUES (?, ?, ?)",
                (guild_id, clan_id, user_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

        updated_clan = self.get_clan(guild_id, clan_id)
        if updated_clan is None:
            raise RuntimeError("Клан не найден после вступления участника.")
        return updated_clan
