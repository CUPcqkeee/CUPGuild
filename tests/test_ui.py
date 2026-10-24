import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import disnake
from disnake.ext import commands

from API.API import ClanDatabase
from assets.embeds import remove_sentence_periods
from cogs.clan import (
    BackButton,
    ClanPermissionButton,
    ClanCog,
    ClanModal,
    ClanMenuView,
    ClanMemberSelect,
    ClanSettingsModal,
    EconomyModal,
    EventSelect,
    MenuActionButton,
    WarSelect,
    EventModal,
    WarChallengeModal,
    WarScoreModal,
    RoleSelect,
    _create_clan_discord_resources,
    _set_news_permission,
    _sync_clan_member_role,
)
from main import load_settings


class ClanMenuButtonTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = load_settings()
        self.database = ClanDatabase(
            Path(self.temp_dir.name) / "clans.sqlite3",
            self.settings["clans"],
            self.settings["economy"],
        )
        self.view = ClanMenuView(
            self.database,
            self.settings,
            100,
            "Test server",
            200,
        )
        await self.view.prepare("overview")

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def get_action(self, action: str) -> MenuActionButton:
        return next(
            item
            for item in self.view.children
            if isinstance(item, MenuActionButton) and item.action == action
        )

    async def test_home_menu_has_only_clan_and_profile_actions(self) -> None:
        bot = commands.InteractionBot()
        bot.add_cog(ClanCog(self.database, self.settings))
        self.assertEqual({command.name for command in bot.slash_commands}, {"clan", "profile"})
        await bot.close()

    async def test_profile_argument_has_russian_name_and_description(self) -> None:
        bot = commands.InteractionBot()
        bot.add_cog(ClanCog(self.database, self.settings))
        command = next(command for command in bot.slash_commands if command.name == "profile")
        option = command.body.to_dict()["options"][0]

        self.assertEqual(option["name"], "игрок")
        self.assertEqual(option["name_localizations"]["ru"], "игрок")
        self.assertEqual(option["description_localizations"]["ru"], "Чей профиль показать")
        await bot.close()

    async def test_clan_page_replaces_navigation_with_buttons_and_single_back_row(self) -> None:
        clan = self.database.create_clan(100, 200, "CUP", "Cup Guild", "")
        self.database.join_clan(100, 201, clan["id"])

        await self.view.prepare("clan")

        self.assertFalse(any(isinstance(item, disnake.ui.StringSelect) for item in self.view.children))
        self.assertTrue(any(item.action == "manage" for item in self.view.children if isinstance(item, MenuActionButton)))
        self.assertTrue(any(item.action == "events" for item in self.view.children if isinstance(item, MenuActionButton)))
        back_buttons = [item for item in self.view.children if isinstance(item, BackButton)]
        self.assertEqual(len(back_buttons), 1)
        self.assertEqual([item for item in self.view.children if item.row == 4], back_buttons)

    async def test_economy_page_shows_work_daily_transfer_and_profile_buttons(self) -> None:
        await self.view.prepare("economy")

        actions = {item.action for item in self.view.children if isinstance(item, MenuActionButton)}
        self.assertTrue({"daily", "work", "transfer", "profile", "leaderboard"} <= actions)
        self.assertEqual([item for item in self.view.children if item.row == 4], [
            item for item in self.view.children if isinstance(item, BackButton)
        ])

    async def test_event_and_war_pages_use_selects_and_buttons_with_bottom_back(self) -> None:
        clan = self.database.create_clan(100, 200, "CUP", "Cup Guild", "")
        self.view.user_id = 200
        await self.view.prepare("events")

        self.assertTrue(any(item.action == "event_create" for item in self.view.children if isinstance(item, MenuActionButton)))
        self.assertEqual([item for item in self.view.children if item.row == 4], [
            item for item in self.view.children if isinstance(item, BackButton)
        ])

        self.database.connection.execute("UPDATE clans SET treasury = 1000 WHERE id = ?", (clan["id"],))
        self.database.connection.commit()
        self.database.create_clan(100, 300, "RIV", "Rival Clan", "")
        self.database.create_war_challenge(100, 200, "RIV")
        await self.view.prepare("wars")
        self.assertTrue(any(isinstance(item, WarSelect) for item in self.view.children))
        self.assertTrue(any(item.action == "war_challenge" for item in self.view.children if isinstance(item, MenuActionButton)))

    async def test_manage_page_keeps_back_button_alone_on_last_row(self) -> None:
        clan = self.database.create_clan(100, 200, "CUP", "Cup Guild", "")
        self.database.join_clan(100, 201, clan["id"])

        await self.view.prepare("manage")

        self.assertTrue(any(isinstance(item, ClanMemberSelect) for item in self.view.children))
        self.assertEqual([item for item in self.view.children if item.row == 4], [
            item for item in self.view.children if isinstance(item, BackButton)
        ])

    async def test_owner_sees_clan_settings_and_permission_controls(self) -> None:
        self.database.create_clan(100, 200, "CUP", "Cup Guild", "")

        await self.view.prepare("clan")
        clan_actions = {item.action for item in self.view.children if isinstance(item, MenuActionButton)}
        self.assertTrue({"clan_settings", "permissions"} <= clan_actions)

        await self.view.prepare("permissions")
        self.assertTrue(any(isinstance(item, RoleSelect) for item in self.view.children))
        self.assertEqual(
            sum(isinstance(item, ClanPermissionButton) for item in self.view.children),
            8,
        )
        self.assertEqual([item for item in self.view.children if item.row == 4], [
            item for item in self.view.children if isinstance(item, BackButton)
        ])

    async def test_permission_toggle_changes_database_and_preserves_main_embed(self) -> None:
        self.database.create_clan(100, 200, "CUP", "Cup Guild", "")
        await self.view.prepare("permissions")
        interaction = SimpleNamespace(
            response=SimpleNamespace(edit_message=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        original_embed = await self.view.prepare("permissions")

        await self.view.handle_action("toggle_permission:edit_clan", interaction)

        self.assertTrue(self.database.has_permission(100, 200, "edit_clan"))
        interaction.response.edit_message.assert_awaited_once_with(view=self.view)
        self.assertNotIn("embed", interaction.response.edit_message.await_args.kwargs)
        interaction.followup.send.assert_awaited_once()
        self.assertEqual(interaction.followup.send.await_args.kwargs["embed"].title, "✅ Действие выполнено")
        self.assertEqual(original_embed.title, "🔐 Права ранга · Участник")

    async def test_sentence_periods_are_removed_from_embed_copy(self) -> None:
        embed = disnake.Embed(description="Первое предложение. Второе предложение.")

        remove_sentence_periods(embed)

        self.assertEqual(embed.description, "Первое предложение Второе предложение")

    async def test_clan_settings_modal_updates_clan_without_editing_menu(self) -> None:
        self.database.create_clan(100, 200, "CUP", "Cup Guild", "")
        self.assertEqual(len(ClanSettingsModal(self.view).components), 4)
        await self.view.prepare("clan")
        acknowledged = {"value": False}
        defer = AsyncMock(side_effect=lambda **kwargs: acknowledged.update(value=True))
        interaction = SimpleNamespace(response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            defer=defer,
            is_done=lambda: acknowledged["value"],
        ), guild=None)
        interaction.followup = SimpleNamespace(send=AsyncMock())

        await ClanSettingsModal(self.view).callback(SimpleNamespace(
            text_values={
                "name": "New Guild",
                "tag": "NEW",
                "color": "#AABBCC",
                "status": "закрыт",
            },
            response=interaction.response,
            followup=interaction.followup,
            guild=None,
        ))

        updated = self.database.get_user_clan(100, 200)
        self.assertEqual(updated["name"], "New Guild")
        self.assertEqual(updated["tag"], "NEW")
        self.assertEqual(updated["color"], "#AABBCC")
        self.assertEqual(updated["is_open"], 0)
        defer.assert_awaited_once_with(ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        interaction.response.send_message.assert_not_awaited()
        interaction.response.edit_message.assert_not_awaited()

    async def test_clan_creation_creates_private_category_role_and_five_channels(self) -> None:
        clan = self.database.create_clan(100, 200, "CUP", "Cup Guild", "")
        role = MagicMock(id=900)
        category = MagicMock(id=901, overwrites={})
        owner = MagicMock(add_roles=AsyncMock())
        channels = [MagicMock(id=902 + index) for index in range(5)]
        guild = MagicMock()
        guild.id = 100
        guild.default_role = MagicMock()
        guild.get_member.return_value = owner
        guild.create_role = AsyncMock(return_value=role)
        guild.create_category = AsyncMock(return_value=category)
        guild.create_text_channel = AsyncMock(side_effect=channels[:2])
        guild.create_voice_channel = AsyncMock(side_effect=channels[2:])

        await _create_clan_discord_resources(self.database, guild, 200, clan)

        guild.create_role.assert_awaited_once()
        self.assertEqual(guild.create_role.await_args.kwargs["name"], "CUP")
        self.assertEqual(guild.create_category.await_args.kwargs["name"], "🛡️・CUP — Cup Guild")
        category_overwrites = guild.create_category.await_args.kwargs["overwrites"]
        self.assertFalse(category_overwrites[guild.default_role].view_channel)
        self.assertTrue(category_overwrites[role].view_channel)
        self.assertEqual(guild.create_text_channel.await_count, 2)
        self.assertEqual(guild.create_voice_channel.await_count, 3)
        news_overwrites = guild.create_text_channel.await_args_list[0].kwargs["overwrites"]
        self.assertFalse(news_overwrites[role].send_messages)
        self.assertTrue(news_overwrites[owner].send_messages)
        self.assertEqual(owner.add_roles.await_args.args[0], role)
        saved_clan = self.database.get_user_clan(100, 200)
        self.assertEqual(saved_clan["discord_role_id"], 900)
        self.assertEqual(saved_clan["category_id"], 901)
        self.assertEqual(saved_clan["news_channel_id"], 902)

    async def test_create_clan_modal_defers_before_creating_discord_resources(self) -> None:
        acknowledged = {"value": False}
        defer = AsyncMock(side_effect=lambda **kwargs: acknowledged.update(value=True))
        response = SimpleNamespace(
            defer=defer,
            is_done=lambda: acknowledged["value"],
        )
        interaction = SimpleNamespace(
            guild=MagicMock(id=100),
            text_values={"tag": "CUP", "name": "Cup Guild", "description": ""},
            response=response,
            followup=SimpleNamespace(send=AsyncMock()),
        )

        async def verify_deferred(*args: object, **kwargs: object) -> None:
            defer.assert_awaited_once_with(ephemeral=True)

        with patch(
            "cogs.clan._create_clan_discord_resources",
            new_callable=AsyncMock,
            side_effect=verify_deferred,
        ) as create_resources:
            await ClanModal(self.view).callback(interaction)

        create_resources.assert_awaited_once()
        defer.assert_awaited_once_with(ephemeral=True)
        interaction.followup.send.assert_awaited_once()

    async def test_leaving_clan_removes_clan_role_and_member_news_override(self) -> None:
        role = MagicMock()
        member = MagicMock(roles=[role], remove_roles=AsyncMock())
        channel = MagicMock(spec=disnake.TextChannel, set_permissions=AsyncMock())
        guild = MagicMock()
        guild.get_role.return_value = role
        guild.get_member.return_value = member
        guild.get_channel.return_value = channel
        clan = {
            "tag": "CUP",
            "discord_role_id": 900,
            "news_channel_id": 901,
        }

        await _sync_clan_member_role(guild, clan, 200, add=False)

        channel.set_permissions.assert_awaited_once_with(
            member,
            overwrite=None,
            reason="Удаление прав участника клана [CUP]",
        )
        member.remove_roles.assert_awaited_once_with(role, reason="Выход из клана [CUP]")

    async def test_news_write_permission_can_be_granted_to_and_revoked_from_deputy(self) -> None:
        member = MagicMock()
        channel = MagicMock(spec=disnake.TextChannel, set_permissions=AsyncMock())
        guild = MagicMock()
        guild.get_channel.return_value = channel
        guild.get_member.return_value = member
        clan = {"tag": "CUP", "news_channel_id": 901}

        await _set_news_permission(guild, clan, 201, True)
        channel.set_permissions.assert_awaited_once_with(
            member,
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            reason="Права записи в новости клана",
        )

        channel.set_permissions.reset_mock()
        await _set_news_permission(guild, clan, 201, False)
        self.assertFalse(channel.set_permissions.await_args.kwargs["send_messages"])

    async def test_daily_button_sends_a_separate_success_embed_without_editing_menu(self) -> None:
        await self.view.prepare("economy")
        interaction = SimpleNamespace(response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            is_done=lambda: False,
        ))

        await self.get_action("daily").callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        interaction.response.edit_message.assert_not_awaited()
        response = interaction.response.send_message.await_args.kwargs
        self.assertTrue(response["ephemeral"])
        self.assertEqual(response["embed"].title, "✅ Действие выполнено")
        self.assertIn("Получено", response["embed"].description)
        self.assertEqual(self.view.page, "economy")

    async def test_failed_action_sends_a_separate_error_embed_without_editing_menu(self) -> None:
        await self.view.prepare("economy")
        self.database.claim_daily(self.view.guild_id, self.view.user_id)
        interaction = SimpleNamespace(response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            is_done=lambda: False,
        ))

        await self.get_action("daily").callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        interaction.response.edit_message.assert_not_awaited()
        response = interaction.response.send_message.await_args.kwargs
        self.assertTrue(response["ephemeral"])
        self.assertEqual(response["embed"].title, "❌ Не удалось выполнить действие")
        self.assertEqual(self.view.page, "economy")

    async def test_modal_action_sends_result_without_editing_menu(self) -> None:
        self.database.create_clan(self.view.guild_id, self.view.user_id, "CUP", "Cup Guild", "")
        self.database.get_profile(self.view.guild_id, self.view.user_id)
        self.database.connection.execute(
            "UPDATE player_profiles SET balance = 500 WHERE guild_id = ? AND user_id = ?",
            (self.view.guild_id, self.view.user_id),
        )
        self.database.connection.commit()
        await self.view.prepare("clan")
        interaction = SimpleNamespace(response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            is_done=lambda: False,
        ))

        await self.view.handle_modal(interaction, "deposit", {"amount": "100"})

        interaction.response.send_message.assert_awaited_once()
        interaction.response.edit_message.assert_not_awaited()
        self.assertIn("Казна клана", interaction.response.send_message.await_args.kwargs["embed"].description)
        self.assertEqual(self.view.page, "clan")

    async def test_failed_modal_action_sends_error_embed_without_editing_menu(self) -> None:
        await self.view.prepare("economy")
        interaction = SimpleNamespace(response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            is_done=lambda: False,
        ))

        await self.view.handle_modal(interaction, "transfer", {"recipient": "123", "amount": "invalid"})

        interaction.response.send_message.assert_awaited_once()
        interaction.response.edit_message.assert_not_awaited()
        response = interaction.response.send_message.await_args.kwargs
        self.assertTrue(response["ephemeral"])
        self.assertEqual(response["embed"].title, "❌ Не удалось выполнить действие")
        self.assertEqual(self.view.page, "economy")

    async def test_economy_event_and_war_forms_are_available_from_menu(self) -> None:
        self.assertEqual(len(EconomyModal(self.view, "transfer").components), 2)
        self.assertEqual(len(EconomyModal(self.view, "deposit").components), 1)
        self.assertEqual(len(EventModal(self.view).components), 3)
        self.assertEqual(len(WarChallengeModal(self.view).components), 1)
        self.assertEqual(len(WarScoreModal(self.view).components), 1)

        await self.view.prepare("economy")
        interaction = SimpleNamespace(response=SimpleNamespace(send_modal=AsyncMock()))
        await self.get_action("transfer").callback(interaction)
        interaction.response.send_modal.assert_awaited_once()

    async def test_event_selector_contains_open_events(self) -> None:
        clan = self.database.create_clan(100, 200, "CUP", "Cup Guild", "")
        self.database.connection.execute("UPDATE clans SET treasury = 100 WHERE id = ?", (clan["id"],))
        self.database.connection.commit()
        self.database.create_clan_event(100, 200, "Турнир", "Матчи", 25)

        await self.view.prepare("events")

        selector = next(item for item in self.view.children if isinstance(item, EventSelect))
        self.assertEqual(selector.options[0].label, "#1 · Турнир")


if __name__ == "__main__":
    unittest.main()
