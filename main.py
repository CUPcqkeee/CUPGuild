import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import disnake
from disnake.ext import commands
from dotenv import load_dotenv

from API.API import ClanDatabase
from cogs.clan import ClanCog


ROOT_DIR = Path(__file__).resolve().parent
LOGGER = logging.getLogger("cupguild")


def load_settings() -> dict[str, Any]:
    settings_path = ROOT_DIR / "settings.json"
    try:
        with settings_path.open(encoding="utf-8") as settings_file:
            settings = json.load(settings_file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Не удалось загрузить настройки из {settings_path}: {error}") from error

    if not isinstance(settings, dict):
        raise ValueError("Корень settings.json должен быть JSON-объектом.")

    for section in ("bot", "database", "clans", "ui"):
        if not isinstance(settings.get(section), dict):
            raise ValueError(f"В settings.json отсутствует раздел «{section}» или он задан неверно.")
    if not isinstance(settings.get("economy"), dict):
        raise ValueError("В settings.json отсутствует раздел «economy» или он задан неверно.")

    if not isinstance(settings["database"].get("path"), str) or not settings["database"]["path"].strip():
        raise ValueError("Параметр database.path должен содержать путь к файлу SQLite.")

    for key in ("member_limit", "tag_min_length", "tag_max_length", "name_min_length", "name_max_length", "description_max_length"):
        value = settings["clans"].get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"Параметр clans.{key} должен быть положительным целым числом.")

    if settings["clans"]["tag_min_length"] > settings["clans"]["tag_max_length"]:
        raise ValueError("clans.tag_min_length не может быть больше clans.tag_max_length.")
    if settings["clans"]["name_min_length"] > settings["clans"]["name_max_length"]:
        raise ValueError("clans.name_min_length не может быть больше clans.name_max_length.")
    if any(
        settings["clans"][key] > 4000
        for key in ("tag_max_length", "name_max_length", "description_max_length")
    ):
        raise ValueError("Максимальная длина полей клана не может превышать ограничение Discord в 4000 символов.")

    for key, maximum in (("timeout_seconds", None), ("member_list_limit", 20), ("ranking_limit", 25), ("discovery_limit", 25)):
        value = settings["ui"].get(key)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 1
            or (maximum is not None and value > maximum)
        ):
            allowed = f"от 1 до {maximum}" if maximum is not None else "положительным целым числом"
            raise ValueError(f"Параметр ui.{key} должен быть {allowed}.")

    for key in ("color", "success_color", "error_color"):
        value = settings["ui"].get(key)
        if not isinstance(value, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
            raise ValueError(f"Параметр ui.{key} должен быть цветом в формате #RRGGBB.")

    economy = settings["economy"]
    for key in (
        "daily_reward",
        "daily_streak_bonus",
        "daily_xp",
        "work_reward_min",
        "work_reward_max",
        "work_xp",
        "work_cooldown_seconds",
        "clan_income_base",
        "clan_income_per_member",
        "event_max_reward",
        "event_xp",
        "war_stake",
    ):
        value = economy.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Параметр economy.{key} должен быть неотрицательным целым числом.")
    if economy["work_reward_min"] > economy["work_reward_max"]:
        raise ValueError("economy.work_reward_min не может быть больше economy.work_reward_max.")
    if (
        not isinstance(economy.get("clan_income_leader_share_percent"), int)
        or isinstance(economy["clan_income_leader_share_percent"], bool)
        or not 0 <= economy["clan_income_leader_share_percent"] <= 100
    ):
        raise ValueError("economy.clan_income_leader_share_percent должен быть числом от 0 до 100.")
    if not isinstance(economy.get("currency_name"), str) or not economy["currency_name"].strip():
        raise ValueError("Параметр economy.currency_name должен содержать название валюты.")

    return settings


async def run_bot() -> None:
    load_dotenv(ROOT_DIR / ".env")
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Не найден DISCORD_TOKEN. Укажите токен бота в файле .env.")

    settings = load_settings()
    database_path = (ROOT_DIR / settings["database"]["path"]).resolve()
    database = ClanDatabase(database_path, settings["clans"], settings["economy"])

    test_guild_ids = settings["bot"].get("test_guild_ids", [])
    if not isinstance(test_guild_ids, list) or not all(
        isinstance(guild_id, int) and not isinstance(guild_id, bool) and guild_id > 0
        for guild_id in test_guild_ids
    ):
        raise ValueError("bot.test_guild_ids должен быть списком числовых ID серверов.")

    bot = commands.InteractionBot(
        intents=disnake.Intents.default(),
        test_guilds=test_guild_ids or None,
    )
    bot.add_cog(ClanCog(database, settings))

    try:
        LOGGER.info("Запуск бота CUPGuild.")
        await bot.start(token)
    finally:
        await bot.close()
        database.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        LOGGER.info("Бот остановлен.")
