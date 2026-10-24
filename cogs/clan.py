import logging
import re
from typing import Any

import disnake
from disnake.ext import commands

from API.API import (
    CLAN_PERMISSIONS,
    CLAN_ROLES,
    RANK_LABELS,
    RANK_LEVELS,
    ClanDatabase,
    ClanError,
)
from assets.embeds import (
    clan_settings_embed,
    clan_embed,
    clan_events_embed,
    clan_management_embed,
    clan_wars_embed,
    confirmation_embed,
    action_result_embed,
    discovery_embed,
    economy_leaderboard_embed,
    economy_menu_embed,
    guide_embed,
    notice_embed,
    overview_embed,
    profile_embed,
    ranking_embed,
    role_permissions_embed,
    remove_sentence_periods,
)

LOGGER = logging.getLogger("cupguild.clans")
MENTION_PATTERN = re.compile(r"^<@!?(\d+)>$")
ROLE_EMOJIS = {"deputy": "💠", "officer": "⚔️", "veteran": "🛡️", "member": "👤"}
PERMISSION_ROWS = {
    "manage_ranks": 1,
    "kick_members": 1,
    "edit_clan": 1,
    "manage_events": 1,
    "manage_wars": 1,
    "war_score": 2,
    "clan_income": 2,
    "withdraw_treasury": 2,
}

PAGE_OPTIONS = [
    disnake.SelectOption(label="Обзор", value="overview", emoji="🏠"),
    disnake.SelectOption(label="Мой клан", value="clan", emoji="🛡️"),
    disnake.SelectOption(label="Экономика", value="economy", emoji="💰"),
    disnake.SelectOption(label="События", value="events", emoji="🎉"),
    disnake.SelectOption(label="Войны", value="wars", emoji="⚔️"),
    disnake.SelectOption(label="Найти клан", value="discover", emoji="🔎"),
    disnake.SelectOption(label="Рейтинг", value="ranking", emoji="🏆"),
    disnake.SelectOption(label="Справка", value="guide", emoji="📖"),
]


def _discord_color(value: str) -> disnake.Color:
    return disnake.Color(int(value.removeprefix("#"), 16))


async def _create_clan_discord_resources(
    database: ClanDatabase,
    guild: disnake.Guild,
    user_id: int,
    clan: dict[str, Any],
) -> None:
    role: disnake.Role | None = None
    category: disnake.CategoryChannel | None = None
    channels: list[disnake.abc.GuildChannel] = []
    try:
        role = await guild.create_role(
            name=clan["tag"],
            colour=_discord_color(clan["color"]),
            reason=f"Клан [{clan['tag']}] {clan['name']}",
        )
        default_overwrite = disnake.PermissionOverwrite(view_channel=False)
        clan_overwrite = disnake.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            connect=True,
            speak=True,
        )
        category_overwrites = {
            guild.default_role: default_overwrite,
            role: clan_overwrite,
        }
        if guild.me is not None:
            category_overwrites[guild.me] = disnake.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages=True,
                manage_channels=True,
                connect=True,
                speak=True,
            )
        category = await guild.create_category(
            name=f"🛡️・{clan['tag']} — {clan['name']}"[:100],
            overwrites=category_overwrites,
            reason=f"Клан [{clan['tag']}]",
        )

        news_overwrites = dict(category.overwrites)
        news_overwrites[role] = disnake.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=False,
        )
        owner = guild.get_member(user_id)
        if owner is None:
            owner = await guild.fetch_member(user_id)
        news_overwrites[owner] = disnake.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
        )
        news = await guild.create_text_channel(
            name="📰・новости",
            category=category,
            overwrites=news_overwrites,
            reason=f"Новости клана [{clan['tag']}]",
        )
        channels.append(news)
        chat = await guild.create_text_channel(
            name="💬・чат",
            category=category,
            overwrites=dict(category.overwrites),
            reason=f"Чат клана [{clan['tag']}]",
        )
        channels.append(chat)
        for channel_name in ("🔊・общий", "🔊・игра-1", "🔊・игра-2"):
            voice = await guild.create_voice_channel(
                name=channel_name,
                category=category,
                overwrites=dict(category.overwrites),
                reason=f"Голосовой канал клана [{clan['tag']}]",
            )
            channels.append(voice)

        await owner.add_roles(role, reason=f"Вступление в клан [{clan['tag']}]")
        database.save_clan_discord_resources(
            guild.id,
            user_id,
            role.id,
            category.id,
            news.id,
        )
    except Exception:
        LOGGER.exception("Не удалось создать Discord-структуру клана [%s]", clan["tag"])
        for channel in reversed(channels):
            try:
                await channel.delete(reason="Откат создания клана")
            except disnake.HTTPException:
                LOGGER.exception("Не удалось удалить канал клана при откате")
        if category is not None:
            try:
                await category.delete(reason="Откат создания клана")
            except disnake.HTTPException:
                LOGGER.exception("Не удалось удалить категорию клана при откате")
        if role is not None:
            try:
                await role.delete(reason="Откат создания клана")
            except disnake.HTTPException:
                LOGGER.exception("Не удалось удалить роль клана при откате")
        try:
            database.disband_clan(guild.id, user_id)
        except ClanError:
            LOGGER.exception("Не удалось удалить запись клана при откате")
        raise


async def _set_news_permission(
    guild: disnake.Guild,
    clan: dict[str, Any],
    user_id: int,
    can_write: bool,
) -> None:
    channel_id = clan.get("news_channel_id")
    if channel_id is None:
        return
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, disnake.TextChannel):
        raise ClanError("Канал новостей клана не найден")
    member = guild.get_member(user_id)
    if member is None:
        member = await guild.fetch_member(user_id)
    await channel.set_permissions(
        member,
        view_channel=True,
        read_message_history=True,
        send_messages=can_write,
        reason="Права записи в новости клана",
    )


async def _sync_clan_discord_settings(guild: disnake.Guild, clan: dict[str, Any]) -> None:
    role_id = clan.get("discord_role_id")
    category_id = clan.get("category_id")
    role = guild.get_role(role_id) if role_id else None
    category = guild.get_channel(category_id) if category_id else None
    if role_id and role is None:
        raise ClanError("Роль клана не найдена в Discord")
    if category_id and not isinstance(category, disnake.CategoryChannel):
        raise ClanError("Категория клана не найдена в Discord")
    if role is not None:
        await role.edit(
            name=clan["tag"],
            colour=_discord_color(clan["color"]),
            reason=f"Обновление клана [{clan['tag']}]",
        )
    if isinstance(category, disnake.CategoryChannel):
        await category.edit(
            name=f"🛡️・{clan['tag']} — {clan['name']}"[:100],
            reason=f"Обновление клана [{clan['tag']}]",
        )


async def _sync_clan_member_role(
    guild: disnake.Guild,
    clan: dict[str, Any],
    user_id: int,
    *,
    add: bool,
) -> None:
    role_id = clan.get("discord_role_id")
    if role_id is None:
        return
    role = guild.get_role(role_id)
    if role is None:
        raise ClanError("Роль клана не найдена, обратитесь к владельцу сервера")
    member = guild.get_member(user_id)
    if member is None:
        member = await guild.fetch_member(user_id)
    if add:
        await member.add_roles(role, reason=f"Участник клана [{clan['tag']}]")
    else:
        news_channel_id = clan.get("news_channel_id")
        news_channel = guild.get_channel(news_channel_id) if news_channel_id else None
        if isinstance(news_channel, disnake.TextChannel):
            await news_channel.set_permissions(
                member,
                overwrite=None,
                reason=f"Удаление прав участника клана [{clan['tag']}]",
            )
        if role in member.roles:
            await member.remove_roles(role, reason=f"Выход из клана [{clan['tag']}]")


async def _delete_clan_discord_resources(guild: disnake.Guild, clan: dict[str, Any]) -> None:
    category = guild.get_channel(clan.get("category_id")) if clan.get("category_id") else None
    if isinstance(category, disnake.CategoryChannel):
        for channel in list(category.channels):
            await channel.delete(reason=f"Роспуск клана [{clan['tag']}]")
        await category.delete(reason=f"Роспуск клана [{clan['tag']}]")
    role = guild.get_role(clan.get("discord_role_id")) if clan.get("discord_role_id") else None
    if role is not None:
        await role.delete(reason=f"Роспуск клана [{clan['tag']}]")


class PageSelect(disnake.ui.StringSelect):
    def __init__(self, menu: "ClanMenuView") -> None:
        self.menu = menu
        super().__init__(
            placeholder="Выберите раздел",
            options=PAGE_OPTIONS,
            custom_id="clan-menu:page",
            row=0,
        )

    async def callback(self, interaction: disnake.MessageInteraction) -> None:
        embed = await self.menu.prepare(self.values[0])
        await interaction.response.edit_message(embed=embed, view=self.menu)


class ClanSelect(disnake.ui.StringSelect):
    def __init__(self, menu: "ClanMenuView", clans: list[dict[str, Any]]) -> None:
        self.menu = menu
        options = [
            disnake.SelectOption(
                label=f"[{clan['tag']}] {clan['name']}"[:100],
                value=str(clan["id"]),
                description=f"{clan['member_count']} из {menu.settings['clans']['member_limit']} мест занято"[:100],
            )
            for clan in clans[:25]
        ]
        super().__init__(
            placeholder="Выберите клан, чтобы вступить",
            options=options,
            custom_id="clan-menu:join",
            row=1,
        )

    async def callback(self, interaction: disnake.MessageInteraction) -> None:
        clan: dict[str, Any] | None = None
        try:
            clan = self.menu.database.join_clan(
                self.menu.guild_id,
                self.menu.user_id,
                int(self.values[0]),
            )
            if interaction.guild is not None:
                await _sync_clan_member_role(
                    interaction.guild,
                    clan,
                    self.menu.user_id,
                    add=True,
                )
        except disnake.HTTPException:
            if clan is not None:
                self.menu.database.leave_clan(self.menu.guild_id, self.menu.user_id)
            LOGGER.exception("Не удалось выдать Discord-роль участнику клана")
            await self.menu.send_action_result(
                interaction,
                "Не удалось выдать роль клана, поэтому вступление отменено",
                success=False,
            )
            return
        except ClanError as error:
            if clan is not None:
                self.menu.database.leave_clan(self.menu.guild_id, self.menu.user_id)
            await self.menu.send_action_result(interaction, str(error), success=False)
            return
        else:
            await self.menu.send_action_result(
                interaction,
                f"Вы присоединились к **[{clan['tag']}] {clan['name']}**.",
                success=True,
            )


class RoleSelect(disnake.ui.StringSelect):
    def __init__(self, menu: "ClanMenuView") -> None:
        self.menu = menu
        options = [
            disnake.SelectOption(
                label=RANK_LABELS[role],
                value=role,
                emoji=ROLE_EMOJIS[role],
                default=role == menu.selected_role,
            )
            for role in CLAN_ROLES
        ]
        super().__init__(
            placeholder="Выберите ранг для настройки прав",
            options=options,
            custom_id="clan-menu:role-permissions",
            row=0,
        )

    async def callback(self, interaction: disnake.MessageInteraction) -> None:
        self.menu.selected_role = self.values[0]
        embed = await self.menu.prepare("permissions")
        await interaction.response.edit_message(embed=embed, view=self.menu)


class ClanPermissionButton(disnake.ui.Button):
    def __init__(
        self,
        menu: "ClanMenuView",
        permission: str,
        enabled: bool,
    ) -> None:
        self.menu = menu
        self.permission = permission
        super().__init__(
            label=f"{'✅' if enabled else '❌'} {CLAN_PERMISSIONS[permission]}",
            style=disnake.ButtonStyle.success if enabled else disnake.ButtonStyle.secondary,
            custom_id=f"clan-menu:permission:{permission}",
            row=PERMISSION_ROWS[permission],
        )

    async def callback(self, interaction: disnake.MessageInteraction) -> None:
        await self.menu.handle_action(f"toggle_permission:{self.permission}", interaction)


class ClanMemberSelect(disnake.ui.StringSelect):
    def __init__(self, menu: "ClanMenuView", members: list[dict[str, Any]]) -> None:
        self.menu = menu
        options = [
            disnake.SelectOption(
                label=f"{RANK_LABELS[member['role']]} · {member['user_id']}"[:100],
                value=str(member["user_id"]),
                emoji=ROLE_EMOJIS[member["role"]],
            )
            for member in members[:25]
        ]
        super().__init__(
            placeholder="Выберите участника для управления",
            options=options,
            custom_id="clan-menu:manage-member",
            row=0,
        )

    async def callback(self, interaction: disnake.MessageInteraction) -> None:
        self.menu.target_member_id = int(self.values[0])
        embed = await self.menu.prepare("manage")
        await interaction.response.edit_message(embed=embed, view=self.menu)


class EventSelect(disnake.ui.StringSelect):
    def __init__(self, menu: "ClanMenuView", events: list[dict[str, Any]]) -> None:
        self.menu = menu
        options = [
            disnake.SelectOption(
                label=f"#{event['id']} · {event['title']}"[:100],
                value=str(event["id"]),
                description=f"{event['participant_count']} участников · награда {event['reward']}"[:100],
            )
            for event in events[:25]
        ]
        super().__init__(
            placeholder="Выберите событие",
            options=options,
            custom_id="clan-menu:event-select",
            row=0,
        )

    async def callback(self, interaction: disnake.MessageInteraction) -> None:
        self.menu.selected_event_id = int(self.values[0])
        embed = await self.menu.prepare("events")
        await interaction.response.edit_message(embed=embed, view=self.menu)


class WarSelect(disnake.ui.StringSelect):
    def __init__(self, menu: "ClanMenuView", wars: list[dict[str, Any]]) -> None:
        self.menu = menu
        options = [
            disnake.SelectOption(
                label=f"#{war['id']} · {war['clan_one_tag']} — {war['clan_two_tag']}"[:100],
                value=str(war["id"]),
                description="Ожидает ответа" if war["status"] == "pending" else "Идёт",
            )
            for war in wars[:25]
        ]
        super().__init__(
            placeholder="Выберите вызов или войну",
            options=options,
            custom_id="clan-menu:war-select",
            row=0,
        )

    async def callback(self, interaction: disnake.MessageInteraction) -> None:
        self.menu.selected_war_id = int(self.values[0])
        embed = await self.menu.prepare("wars")
        await interaction.response.edit_message(embed=embed, view=self.menu)


class MenuActionButton(disnake.ui.Button):
    def __init__(
        self,
        menu: "ClanMenuView",
        action: str,
        label: str,
        emoji: str,
        style: disnake.ButtonStyle = disnake.ButtonStyle.secondary,
        row: int = 1,
        disabled: bool = False,
    ) -> None:
        self.menu = menu
        self.action = action
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id=f"clan-menu:action:{action}",
            row=row,
            disabled=disabled,
        )

    async def callback(self, interaction: disnake.MessageInteraction) -> None:
        await self.menu.handle_action(self.action, interaction)


class BackButton(disnake.ui.Button):
    def __init__(self, menu: "ClanMenuView") -> None:
        self.menu = menu
        super().__init__(
            label="Вернуться назад",
            emoji="↩️",
            style=disnake.ButtonStyle.secondary,
            custom_id="clan-menu:back",
            row=4,
        )

    async def callback(self, interaction: disnake.MessageInteraction) -> None:
        destinations = {
            "manage": "clan",
            "permissions": "clan",
            "clan_settings": "clan",
            "confirm_disband": "clan",
            "profile": "economy",
            "leaderboard": "economy",
        }
        embed = await self.menu.prepare(destinations.get(self.menu.page, "overview"))
        await interaction.response.edit_message(embed=embed, view=self.menu)


class ClanActionModal(disnake.ui.Modal):
    menu: "ClanMenuView"

    async def acknowledge(self, interaction: disnake.ModalInteraction) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

    async def on_error(
        self,
        error: Exception,
        interaction: disnake.ModalInteraction,
    ) -> None:
        LOGGER.error(
            "Не удалось обработать модальную форму %s.",
            self.custom_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        await self.menu.send_action_result(
            interaction,
            "Произошла ошибка при выполнении действия. Попробуйте ещё раз.",
            success=False,
        )


class ClanModal(ClanActionModal):
    def __init__(self, menu: "ClanMenuView") -> None:
        self.menu = menu
        rules = menu.settings["clans"]
        super().__init__(
            title="Создать клан",
            custom_id="clan-menu:create",
            components=[
                disnake.ui.TextInput(
                    label="Тег клана",
                    custom_id="tag",
                    placeholder="Например, CUP",
                    min_length=rules["tag_min_length"],
                    max_length=rules["tag_max_length"],
                    required=True,
                ),
                disnake.ui.TextInput(
                    label="Название",
                    custom_id="name",
                    placeholder="Название вашей команды",
                    min_length=rules["name_min_length"],
                    max_length=rules["name_max_length"],
                    required=True,
                ),
                disnake.ui.TextInput(
                    label="Описание",
                    custom_id="description",
                    placeholder="Игра, цели и требования к участникам",
                    max_length=rules["description_max_length"],
                    style=disnake.TextInputStyle.paragraph,
                    required=False,
                ),
            ],
        )

    async def callback(self, interaction: disnake.ModalInteraction) -> None:
        await self.acknowledge(interaction)
        if interaction.guild is None:
            await self.menu.send_action_result(
                interaction,
                "Создавать клан можно только на сервере Discord",
                success=False,
            )
            return
        try:
            clan = self.menu.database.create_clan(
                self.menu.guild_id,
                self.menu.user_id,
                interaction.text_values["tag"],
                interaction.text_values["name"],
                interaction.text_values.get("description", ""),
            )
            await _create_clan_discord_resources(
                self.menu.database,
                interaction.guild,
                self.menu.user_id,
                clan,
            )
        except ClanError as error:
            await self.menu.send_action_result(interaction, str(error), success=False)
        except disnake.HTTPException:
            await self.menu.send_action_result(
                interaction,
                "Не удалось создать каналы и роль клана. Проверьте права бота и попробуйте снова",
                success=False,
            )
        else:
            await self.menu.send_action_result(
                interaction,
                f"Клан **[{clan['tag']}] {clan['name']}** создан. Вы стали владельцем.",
                success=True,
            )


class ClanSettingsModal(ClanActionModal):
    def __init__(self, menu: "ClanMenuView") -> None:
        self.menu = menu
        clan = menu.database.get_user_clan(menu.guild_id, menu.user_id)
        if clan is None:
            raise ClanError("Вы больше не состоите в клане")
        super().__init__(
            title="Изменить параметры клана",
            custom_id="clan-menu:modal:clan-settings",
            components=[
                disnake.ui.TextInput(
                    label="Название клана",
                    custom_id="name",
                    value=clan["name"],
                    max_length=menu.settings["clans"]["name_max_length"],
                    required=True,
                ),
                disnake.ui.TextInput(
                    label="Тег клана",
                    custom_id="tag",
                    value=clan["tag"],
                    min_length=menu.settings["clans"]["tag_min_length"],
                    max_length=menu.settings["clans"]["tag_max_length"],
                    required=True,
                ),
                disnake.ui.TextInput(
                    label="Цвет в формате #RRGGBB",
                    custom_id="color",
                    value=clan.get("color", menu.settings["ui"]["color"]),
                    max_length=7,
                    required=True,
                ),
                disnake.ui.TextInput(
                    label="Статус набора: открыт или закрыт",
                    custom_id="status",
                    value="открыт" if clan.get("is_open", 1) else "закрыт",
                    max_length=6,
                    required=True,
                ),
            ],
        )

    async def callback(self, interaction: disnake.ModalInteraction) -> None:
        await self.acknowledge(interaction)
        values = dict(interaction.text_values)
        status = values["status"].strip().casefold()
        if status not in {"открыт", "закрыт"}:
            await self.menu.send_action_result(
                interaction,
                "Статус должен быть «открыт» или «закрыт»",
                success=False,
            )
            return
        try:
            clan = self.menu.database.update_clan_settings(
                self.menu.guild_id,
                self.menu.user_id,
                values["name"],
                values["tag"],
                values["color"],
                is_open=status == "открыт",
            )
        except ClanError as error:
            await self.menu.send_action_result(interaction, str(error), success=False)
        else:
            if interaction.guild is not None:
                try:
                    await _sync_clan_discord_settings(interaction.guild, clan)
                except (ClanError, disnake.HTTPException):
                    LOGGER.exception("Параметры клана сохранены, но Discord-ресурсы не обновлены")
                    await self.menu.send_action_result(
                        interaction,
                        "Параметры сохранены, но не удалось обновить роль или категорию Discord",
                        success=False,
                    )
                    return
            await self.menu.send_action_result(
                interaction,
                f"Параметры клана обновлены: **[{clan['tag']}] {clan['name']}** · "
                f"{'набор открыт' if clan['is_open'] else 'набор закрыт'}",
                success=True,
            )


class EconomyModal(ClanActionModal):
    def __init__(self, menu: "ClanMenuView", action: str) -> None:
        self.menu = menu
        self.action = action
        if action == "transfer":
            components = [
                disnake.ui.TextInput(
                    label="ID или упоминание игрока",
                    custom_id="recipient",
                    placeholder="123456789012345678",
                    max_length=30,
                    required=True,
                ),
                disnake.ui.TextInput(
                    label="Сколько монет перевести",
                    custom_id="amount",
                    placeholder="Например, 100",
                    max_length=12,
                    required=True,
                ),
            ]
            title = "Перевести монеты"
        else:
            components = [
                disnake.ui.TextInput(
                    label="Сумма",
                    custom_id="amount",
                    placeholder="Например, 100",
                    max_length=12,
                    required=True,
                )
            ]
            title = "Внести в казну" if action == "deposit" else "Снять из казны"
        super().__init__(
            title=title,
            custom_id=f"clan-menu:modal:{action}",
            components=components,
        )

    async def callback(self, interaction: disnake.ModalInteraction) -> None:
        await self.acknowledge(interaction)
        await self.menu.handle_modal(
            interaction,
            self.action,
            dict(interaction.text_values),
        )


class EventModal(ClanActionModal):
    def __init__(self, menu: "ClanMenuView") -> None:
        self.menu = menu
        super().__init__(
            title="Создать событие клана",
            custom_id="clan-menu:modal:event",
            components=[
                disnake.ui.TextInput(
                    label="Название события",
                    custom_id="title",
                    max_length=100,
                    required=True,
                ),
                disnake.ui.TextInput(
                    label="Описание",
                    custom_id="description",
                    max_length=500,
                    style=disnake.TextInputStyle.paragraph,
                    required=False,
                ),
                disnake.ui.TextInput(
                    label="Призовой фонд из казны",
                    custom_id="reward",
                    placeholder="Например, 500",
                    max_length=12,
                    required=True,
                ),
            ],
        )

    async def callback(self, interaction: disnake.ModalInteraction) -> None:
        await self.acknowledge(interaction)
        try:
            reward = int(interaction.text_values["reward"].strip())
            event_id = self.menu.database.create_clan_event(
                self.menu.guild_id,
                self.menu.user_id,
                interaction.text_values["title"],
                interaction.text_values.get("description", ""),
                reward,
            )
            self.menu.selected_event_id = event_id
            result = f"Событие создано. Приз **{reward:,} {self.menu.currency_name}** зарезервирован."
        except ValueError:
            result = "Укажите приз целым числом."
            success = False
        except ClanError as error:
            result = str(error)
            success = False
        else:
            success = True
        await self.menu.send_action_result(interaction, result, success)


class WarChallengeModal(ClanActionModal):
    def __init__(self, menu: "ClanMenuView") -> None:
        self.menu = menu
        super().__init__(
            title="Вызвать клан на войну",
            custom_id="clan-menu:modal:war-challenge",
            components=[
                disnake.ui.TextInput(
                    label="Тег клана-соперника",
                    custom_id="tag",
                    placeholder="Например, RIV",
                    min_length=self.menu.settings["clans"]["tag_min_length"],
                    max_length=self.menu.settings["clans"]["tag_max_length"],
                    required=True,
                )
            ],
        )

    async def callback(self, interaction: disnake.ModalInteraction) -> None:
        await self.acknowledge(interaction)
        try:
            war_id = self.menu.database.create_war_challenge(
                self.menu.guild_id,
                self.menu.user_id,
                interaction.text_values["tag"],
            )
            self.menu.selected_war_id = war_id
            result = f"Вызов **#{war_id}** отправлен."
        except ClanError as error:
            result = str(error)
            success = False
        else:
            success = True
        await self.menu.send_action_result(interaction, result, success)


class WarScoreModal(ClanActionModal):
    def __init__(self, menu: "ClanMenuView") -> None:
        self.menu = menu
        super().__init__(
            title="Начислить очки войны",
            custom_id="clan-menu:modal:war-score",
            components=[
                disnake.ui.TextInput(
                    label="Сколько очков добавить",
                    custom_id="points",
                    placeholder="От 1 до 100",
                    max_length=3,
                    required=True,
                )
            ],
        )

    async def callback(self, interaction: disnake.ModalInteraction) -> None:
        await self.acknowledge(interaction)
        try:
            points = int(interaction.text_values["points"].strip())
            if self.menu.selected_war_id is None:
                raise ClanError("Сначала выберите активную войну.")
            war = self.menu.database.record_war_score(
                self.menu.guild_id,
                self.menu.user_id,
                self.menu.selected_war_id,
                points,
            )
            result = (
                f"Счёт обновлён: **{war['clan_one_tag']} {war['score_one']} : "
                f"{war['score_two']} {war['clan_two_tag']}**."
            )
        except ValueError:
            result = "Количество очков должно быть целым числом."
            success = False
        except ClanError as error:
            result = str(error)
            success = False
        else:
            success = True
        await self.menu.send_action_result(interaction, result, success)


class ClanMenuView(disnake.ui.View):
    def __init__(
        self,
        database: ClanDatabase,
        settings: dict[str, Any],
        guild_id: int,
        guild_name: str,
        user_id: int,
        user: disnake.User | disnake.Member | None = None,
    ) -> None:
        super().__init__(timeout=settings["ui"]["timeout_seconds"])
        self.database = database
        self.settings = settings
        self.guild_id = guild_id
        self.guild_name = guild_name
        self.user_id = user_id
        self.user = user
        self.page = "overview"
        self.target_member_id: int | None = None
        self.selected_event_id: int | None = None
        self.selected_war_id: int | None = None
        self.selected_role = "member"
        self.notice: str | None = None

    @property
    def currency_name(self) -> str:
        return self.settings["economy"]["currency_name"]

    async def interaction_check(self, interaction: disnake.MessageInteraction) -> bool:
        if interaction.author.id != self.user_id:
            await interaction.response.send_message(
                "Это личное меню другого участника",
                ephemeral=True,
            )
            return False
        return True

    async def on_error(
        self,
        error: Exception,
        item: disnake.ui.Item,
        interaction: disnake.MessageInteraction,
    ) -> None:
        LOGGER.error(
            "Не удалось обработать компонент меню %s.",
            item.custom_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        await self.send_action_result(
            interaction,
            "Не удалось выполнить действие. Попробуйте ещё раз.",
            success=False,
        )

    async def send_action_result(
        self,
        interaction: disnake.Interaction,
        message: str,
        success: bool,
    ) -> None:
        embed = action_result_embed(
            message,
            success,
            self.settings["ui"]["success_color"],
            self.settings["ui"]["error_color"],
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def prepare(self, page: str, notice: str | None = None) -> disnake.Embed:
        self.page = page
        clan = self.database.get_user_clan(self.guild_id, self.user_id)
        clan_count, member_count = self.database.get_guild_stats(self.guild_id)
        ui = self.settings["ui"]
        rules = self.settings["clans"]
        embed_color = clan.get("color", ui["color"]) if clan else ui["color"]
        self.clear_items()

        if page == "clan":
            members = (
                self.database.get_members(self.guild_id, clan["id"], ui["member_list_limit"])
                if clan
                else []
            )
            embed = clan_embed(
                self.guild_name,
                clan,
                members,
                rules["member_limit"],
                ui["member_list_limit"],
                embed_color,
                self.currency_name,
                self.notice,
            )
            self.notice = None
            if clan is None:
                self.add_item(MenuActionButton(self, "create_clan", "Создать клан", "✨", disnake.ButtonStyle.success, 0))
            else:
                if self.database.has_permission(self.guild_id, self.user_id, "manage_ranks") or self.database.has_permission(self.guild_id, self.user_id, "kick_members"):
                    self.add_item(MenuActionButton(self, "manage", "Управление составом", "⚙️", disnake.ButtonStyle.primary, 0))
                if clan["owner_id"] == self.user_id or self.database.has_permission(self.guild_id, self.user_id, "edit_clan"):
                    self.add_item(MenuActionButton(self, "clan_settings", "Параметры клана", "🎨", disnake.ButtonStyle.primary, 0))
                if clan["owner_id"] == self.user_id:
                    self.add_item(MenuActionButton(self, "permissions", "Права рангов", "🔐", disnake.ButtonStyle.primary, 0))
                self.add_item(MenuActionButton(self, "income", "Получить доход", "💰", disnake.ButtonStyle.success, 1, not self.database.has_permission(self.guild_id, self.user_id, "clan_income")))
                self.add_item(MenuActionButton(self, "deposit", "Пополнить казну", "📥", row=1))
                self.add_item(MenuActionButton(self, "withdraw", "Снять из казны", "📤", row=1, disabled=not self.database.has_permission(self.guild_id, self.user_id, "withdraw_treasury")))
                self.add_item(MenuActionButton(self, "events", "События", "🎉", row=2))
                self.add_item(MenuActionButton(self, "wars", "Войны", "⚔️", row=2))
                self.add_item(MenuActionButton(self, "leave", "Покинуть клан", "🚪", row=3, disabled=clan["owner_id"] == self.user_id))
                if clan["owner_id"] == self.user_id:
                    self.add_item(MenuActionButton(self, "confirm_disband", "Распустить клан", "🗑️", disnake.ButtonStyle.danger, 3))
            self.add_item(BackButton(self))
        elif page == "manage":
            if clan is None:
                self.notice = "Вы больше не состоите в клане."
                return await self.prepare("overview")
            members = self.database.get_members(
                self.guild_id,
                clan["id"],
                min(rules["member_limit"], 25),
            )
            actor_rank = "Владелец" if clan["owner_id"] == self.user_id else RANK_LABELS[clan["member_role"]]
            can_manage_ranks = self.database.has_permission(self.guild_id, self.user_id, "manage_ranks")
            can_kick_members = self.database.has_permission(self.guild_id, self.user_id, "kick_members")
            manageable_members = [
                member
                for member in members
                if member["user_id"] != self.user_id
                and (
                    clan["owner_id"] == self.user_id
                    or RANK_LEVELS[clan["member_role"]] > RANK_LEVELS[member["role"]]
                )
            ]
            embed = clan_management_embed(clan, actor_rank, members, embed_color)
            if self.notice:
                embed.add_field(name="📣 Результат", value=self.notice, inline=False)
            if self.target_member_id not in {member["user_id"] for member in manageable_members}:
                self.target_member_id = None
            if manageable_members:
                self.add_item(ClanMemberSelect(self, manageable_members))
                if can_manage_ranks:
                    self.add_item(MenuActionButton(self, "rank_up", "Повысить", "⬆️", disnake.ButtonStyle.success, 1, self.target_member_id is None))
                    self.add_item(MenuActionButton(self, "rank_down", "Понизить", "⬇️", row=1, disabled=self.target_member_id is None))
                if can_kick_members:
                    self.add_item(MenuActionButton(self, "kick", "Исключить", "🚫", disnake.ButtonStyle.danger, 2, self.target_member_id is None))
            self.add_item(BackButton(self))
        elif page == "permissions":
            if clan is None or clan["owner_id"] != self.user_id:
                self.notice = "Настраивать права рангов может только владелец клана"
                return await self.prepare("clan")
            permissions = self.database.get_clan_role_permissions(clan["id"])
            if self.selected_role not in permissions:
                self.selected_role = "member"
            embed = role_permissions_embed(
                clan,
                RANK_LABELS[self.selected_role],
                CLAN_PERMISSIONS,
                embed_color,
            )
            self.add_item(RoleSelect(self))
            for permission, enabled in permissions[self.selected_role].items():
                self.add_item(ClanPermissionButton(self, permission, enabled))
            self.add_item(BackButton(self))
        elif page == "clan_settings":
            if clan is None:
                return await self.prepare("overview")
            embed = clan_settings_embed(clan, embed_color)
            if clan["owner_id"] == self.user_id or self.database.has_permission(self.guild_id, self.user_id, "edit_clan"):
                self.add_item(MenuActionButton(self, "edit_clan", "Изменить параметры", "✏️", disnake.ButtonStyle.primary, 0))
            self.add_item(BackButton(self))
        elif page == "economy":
            profile = self.database.get_profile(self.guild_id, self.user_id)
            embed = economy_menu_embed(self.user, profile, self.currency_name, embed_color)
            if self.notice:
                embed.add_field(name="📣 Результат", value=self.notice, inline=False)
                self.notice = None
            self.add_item(MenuActionButton(self, "daily", "Ежедневная награда", "🎁", disnake.ButtonStyle.success, 0))
            self.add_item(MenuActionButton(self, "work", "На работу", "🧰", disnake.ButtonStyle.primary, 0))
            self.add_item(MenuActionButton(self, "profile", "Профиль", "👤", row=1))
            self.add_item(MenuActionButton(self, "transfer", "Перевести", "💸", row=1))
            self.add_item(MenuActionButton(self, "leaderboard", "Топ игроков", "🏆", row=1))
            if clan:
                self.add_item(MenuActionButton(self, "deposit", "Пополнить казну", "📥", row=2))
                self.add_item(MenuActionButton(self, "withdraw", "Снять из казны", "📤", row=2, disabled=not self.database.has_permission(self.guild_id, self.user_id, "withdraw_treasury")))
            self.add_item(BackButton(self))
        elif page == "profile":
            if self.user is None:
                embed = notice_embed("Профиль игрока", "Откройте `/clan` ещё раз, чтобы загрузить аватар", embed_color)
            else:
                embed = profile_embed(
                    self.user,
                    self.database.get_profile(self.guild_id, self.user_id),
                    self.currency_name,
                    embed_color,
                )
                self.add_item(MenuActionButton(self, "daily", "Ежедневная награда", "🎁", disnake.ButtonStyle.success, 0))
                self.add_item(MenuActionButton(self, "work", "На работу", "🧰", disnake.ButtonStyle.primary, 0))
            self.add_item(BackButton(self))
        elif page == "leaderboard":
            embed = economy_leaderboard_embed(
                self.database.economy_leaderboard(self.guild_id, 10),
                self.currency_name,
                embed_color,
            )
            self.add_item(BackButton(self))
        elif page == "events":
            events = self.database.list_clan_events(self.guild_id, self.user_id, 25)
            if self.selected_event_id not in {event["id"] for event in events}:
                self.selected_event_id = None
            embed = clan_events_embed(events, self.currency_name, embed_color)
            if self.notice:
                embed.add_field(name="📣 Результат", value=self.notice, inline=False)
                self.notice = None
            if events:
                self.add_item(EventSelect(self, events))
                self.add_item(MenuActionButton(self, "event_join", "Записаться", "✅", disnake.ButtonStyle.success, 1, self.selected_event_id is None))
                can_manage_events = self.database.has_permission(self.guild_id, self.user_id, "manage_events")
                self.add_item(MenuActionButton(self, "event_finish", "Завершить", "🏁", disnake.ButtonStyle.primary, 1, self.selected_event_id is None or not can_manage_events))
            if clan:
                self.add_item(MenuActionButton(self, "event_create", "Создать событие", "✨", disnake.ButtonStyle.success, 2, not self.database.has_permission(self.guild_id, self.user_id, "manage_events")))
            self.add_item(BackButton(self))
        elif page == "wars":
            wars = self.database.list_clan_wars(self.guild_id, self.user_id, 25)
            embed = clan_wars_embed(
                wars,
                clan["id"] if clan else None,
                self.currency_name,
                embed_color,
            )
            if self.notice:
                embed.add_field(name="📣 Результат", value=self.notice, inline=False)
                self.notice = None
            selected_war = next(
                (war for war in wars if war["id"] == self.selected_war_id),
                None,
            )
            if selected_war is None:
                self.selected_war_id = None
            if wars:
                self.add_item(WarSelect(self, wars))
                if selected_war and selected_war["status"] == "pending":
                    if clan and selected_war["clan_two_id"] == clan["id"]:
                        can_manage_wars = self.database.has_permission(self.guild_id, self.user_id, "manage_wars")
                        self.add_item(MenuActionButton(self, "war_accept", "Принять вызов", "✅", disnake.ButtonStyle.success, 1, not can_manage_wars))
                    can_manage_wars = self.database.has_permission(self.guild_id, self.user_id, "manage_wars")
                    self.add_item(MenuActionButton(self, "war_decline", "Отклонить вызов", "❌", disnake.ButtonStyle.danger, 1, not can_manage_wars))
                if selected_war and selected_war["status"] == "active":
                    can_score_war = self.database.has_permission(self.guild_id, self.user_id, "war_score")
                    can_manage_wars = self.database.has_permission(self.guild_id, self.user_id, "manage_wars")
                    self.add_item(MenuActionButton(self, "war_score", "Добавить очки", "➕", disnake.ButtonStyle.primary, 1, not can_score_war))
                    self.add_item(MenuActionButton(self, "war_finish", "Завершить войну", "🏁", disnake.ButtonStyle.danger, 2, not can_manage_wars))
            if clan:
                self.add_item(MenuActionButton(self, "war_challenge", "Вызвать соперника", "⚔️", disnake.ButtonStyle.primary, 3, not self.database.has_permission(self.guild_id, self.user_id, "manage_wars")))
            self.add_item(BackButton(self))
        elif page == "discover":
            available_clans = (
                self.database.discover_clans(self.guild_id, self.user_id, ui["discovery_limit"])
                if clan is None
                else []
            )
            embed = discovery_embed(available_clans, rules["member_limit"], embed_color)
            if available_clans:
                self.add_item(ClanSelect(self, available_clans))
            self.add_item(PageSelect(self))
            self.add_item(BackButton(self))
        elif page == "ranking":
            embed = ranking_embed(
                self.guild_name,
                self.database.list_clans(self.guild_id, ui["ranking_limit"]),
                embed_color,
            )
            self.add_item(PageSelect(self))
            self.add_item(BackButton(self))
        elif page == "guide":
            embed = guide_embed(embed_color)
            self.add_item(PageSelect(self))
            self.add_item(BackButton(self))
        elif page == "confirm_disband" and clan is not None:
            embed = confirmation_embed(clan, ui["error_color"])
            self.add_item(MenuActionButton(self, "disband", "Да, распустить клан", "⚠️", disnake.ButtonStyle.danger, 0))
            self.add_item(BackButton(self))
        else:
            self.page = "overview"
            embed = overview_embed(
                self.guild_name,
                clan,
                clan_count,
                member_count,
                ui["color"],
                notice or self.notice,
                ui["success_color"],
            )
            self.notice = None
            self.add_item(PageSelect(self))
            if clan is None:
                self.add_item(MenuActionButton(self, "create_clan", "Создать клан", "✨", disnake.ButtonStyle.success, 1))
            self.add_item(MenuActionButton(self, "profile", "Мой профиль", "👤", row=1))
        return remove_sentence_periods(embed)

    async def _render(self, interaction: disnake.MessageInteraction, page: str | None = None) -> None:
        embed = await self.prepare(page or self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    async def handle_action(self, action: str, interaction: disnake.MessageInteraction) -> None:
        if action == "create_clan":
            await interaction.response.send_modal(ClanModal(self))
            return
        if action == "manage":
            await self._render(interaction, "manage")
            return
        if action == "permissions":
            await self._render(interaction, "permissions")
            return
        if action == "clan_settings":
            await self._render(interaction, "clan_settings")
            return
        if action == "edit_clan":
            await interaction.response.send_modal(ClanSettingsModal(self))
            return
        if action in {"deposit", "withdraw", "transfer"}:
            await interaction.response.send_modal(EconomyModal(self, action))
            return
        if action == "event_create":
            await interaction.response.send_modal(EventModal(self))
            return
        if action == "war_challenge":
            await interaction.response.send_modal(WarChallengeModal(self))
            return
        if action == "war_score":
            await interaction.response.send_modal(WarScoreModal(self))
            return
        if action in {"events", "wars", "economy", "profile", "leaderboard", "clan"}:
            await self._render(interaction, action)
            return

        if action.startswith("toggle_permission:"):
            permission = action.partition(":")[2]
            try:
                if permission not in CLAN_PERMISSIONS:
                    raise RuntimeError(f"Неизвестное право клана: {permission}")
                role_permissions = self.database.get_clan_role_permissions(
                    self.database.get_user_clan(self.guild_id, self.user_id)["id"]
                )
                new_state = not role_permissions[self.selected_role][permission]
                self.database.set_role_permission(
                    self.guild_id,
                    self.user_id,
                    self.selected_role,
                    permission,
                    new_state,
                )
            except ClanError as error:
                await self.send_action_result(interaction, str(error), success=False)
                return
            await self.prepare("permissions")
            await interaction.response.edit_message(view=self)
            await self.send_action_result(
                interaction,
                f"Право «{CLAN_PERMISSIONS[permission]}» для ранга «{RANK_LABELS[self.selected_role]}» "
                f"{'выдано' if new_state else 'снято'}",
                success=True,
            )
            return

        try:
            if action == "daily":
                result = self.database.claim_daily(self.guild_id, self.user_id)
                message = (
                    f"Получено **{result['reward']:,} {self.currency_name}** и "
                    f"**{result['xp_reward']} XP**. Серия: **{result['streak']} дн.**"
                )
                if result["levels_gained"]:
                    message += f" Новый уровень: **{result['profile']['level']}**!"
            elif action == "work":
                result = self.database.work(self.guild_id, self.user_id)
                message = (
                    f"Заработано **{result['reward']:,} {self.currency_name}** "
                    f"и **{result['xp_reward']} XP**."
                )
                if result["levels_gained"]:
                    message += f" Новый уровень: **{result['profile']['level']}**!"
            elif action == "income":
                result = self.database.claim_clan_income(self.guild_id, self.user_id)
                message = (
                    f"Клановый доход: **{result['total']:,} {self.currency_name}**; "
                    f"в казну: **{result['treasury_share']:,} {self.currency_name}**."
                )
            elif action in {"rank_up", "rank_down"}:
                if self.target_member_id is None:
                    raise ClanError("Сначала выберите участника.")
                target_id = self.target_member_id
                role = self.database.change_member_rank(
                    self.guild_id,
                    self.user_id,
                    target_id,
                    1 if action == "rank_up" else -1,
                )
                if interaction.guild is not None:
                    clan = self.database.get_user_clan(self.guild_id, self.user_id)
                    try:
                        await _set_news_permission(
                            interaction.guild,
                            clan,
                            target_id,
                            role == "deputy",
                        )
                    except (ClanError, disnake.HTTPException):
                        LOGGER.exception("Не удалось обновить права записи в новости клана")
                        self.database.change_member_rank(
                            self.guild_id,
                            self.user_id,
                            target_id,
                            -1 if action == "rank_up" else 1,
                        )
                        raise ClanError("Не удалось обновить права участника в Discord, изменение ранга отменено")
                message = f"Ранг изменён: **{RANK_LABELS[role]}**."
            elif action == "kick":
                if self.target_member_id is None:
                    raise ClanError("Сначала выберите участника.")
                target_id = self.target_member_id
                clan = self.database.get_user_clan(self.guild_id, self.user_id)
                target_clan = self.database.get_user_clan(self.guild_id, target_id)
                if interaction.guild is not None:
                    await _sync_clan_member_role(interaction.guild, clan, target_id, add=False)
                try:
                    self.database.kick_member(self.guild_id, self.user_id, target_id)
                except ClanError:
                    if interaction.guild is not None:
                        await _sync_clan_member_role(interaction.guild, target_clan, target_id, add=True)
                        await _set_news_permission(
                            interaction.guild,
                            clan,
                            target_id,
                            target_clan["member_role"] == "deputy",
                        )
                    raise
                self.target_member_id = None
                message = f"Участник <@{target_id}> исключён из клана."
            elif action == "leave":
                clan = self.database.get_user_clan(self.guild_id, self.user_id)
                if interaction.guild is not None:
                    await _sync_clan_member_role(interaction.guild, clan, self.user_id, add=False)
                try:
                    self.database.leave_clan(self.guild_id, self.user_id)
                except ClanError:
                    if interaction.guild is not None:
                        await _sync_clan_member_role(interaction.guild, clan, self.user_id, add=True)
                        await _set_news_permission(
                            interaction.guild,
                            clan,
                            self.user_id,
                            clan["owner_id"] == self.user_id
                            or clan["member_role"] == "deputy",
                        )
                    raise
                message = "Вы покинули клан."
            elif action == "disband":
                clan = self.database.get_user_clan(self.guild_id, self.user_id)
                if clan is None:
                    raise ClanError("Вы больше не состоите в клане")
                if interaction.guild is not None:
                    await _delete_clan_discord_resources(interaction.guild, clan)
                self.database.disband_clan(self.guild_id, self.user_id)
                message = "Клан распущен."
            elif action == "event_join":
                if self.selected_event_id is None:
                    raise ClanError("Сначала выберите событие.")
                event = self.database.join_clan_event(
                    self.guild_id,
                    self.user_id,
                    self.selected_event_id,
                )
                message = f"Вы записаны на событие **{event['title']}**."
            elif action == "event_finish":
                if self.selected_event_id is None:
                    raise ClanError("Сначала выберите событие.")
                result = self.database.finish_clan_event(
                    self.guild_id,
                    self.user_id,
                    self.selected_event_id,
                )
                message = f"Событие **{result['title']}** завершено. Участников: {result['participants']}."
                self.selected_event_id = None
            elif action in {"war_accept", "war_decline", "war_finish"}:
                if self.selected_war_id is None:
                    raise ClanError("Сначала выберите войну.")
                war_id = self.selected_war_id
                if action == "war_accept":
                    self.database.accept_war_challenge(self.guild_id, self.user_id, war_id)
                    message = f"Вызов **#{war_id}** принят. Война началась."
                elif action == "war_decline":
                    self.database.cancel_war_challenge(self.guild_id, self.user_id, war_id)
                    message = f"Вызов **#{war_id}** отклонён."
                    self.selected_war_id = None
                else:
                    result = self.database.finish_war(self.guild_id, self.user_id, war_id)
                    if result["winner_id"] is None:
                        message = "Ничья! Ставки возвращены обоим кланам."
                    else:
                        winner_tag = result["clan_one_tag"] if result["winner_id"] == result["clan_one_id"] else result["clan_two_tag"]
                        message = f"Клан **[{winner_tag}]** победил и получил приз в казну."
                    self.selected_war_id = None
            elif action == "confirm_disband":
                await self._render(interaction, "confirm_disband")
                return
            else:
                raise RuntimeError(f"Неизвестное действие меню: {action}")
        except ClanError as error:
            await self.send_action_result(interaction, str(error), success=False)
            return
        await self.send_action_result(interaction, message, success=True)

    async def handle_modal(
        self,
        interaction: disnake.ModalInteraction,
        action: str,
        values: dict[str, str],
    ) -> None:
        try:
            if action == "deposit" or action == "withdraw":
                amount = int(values["amount"].strip())
                treasury = self.database.move_clan_funds(
                    self.guild_id,
                    self.user_id,
                    amount,
                    deposit=action == "deposit",
                )
                message = f"Казна клана: **{treasury:,} {self.currency_name}**."
            elif action == "transfer":
                recipient_text = values["recipient"].strip()
                mention = MENTION_PATTERN.fullmatch(recipient_text)
                recipient_id = int(mention.group(1) if mention else recipient_text)
                amount = int(values["amount"].strip())
                if interaction.guild is None:
                    raise ClanError("Перевод доступен только на сервере.")
                try:
                    recipient = await interaction.guild.fetch_member(recipient_id)
                except disnake.NotFound as error:
                    raise ClanError("Игрок с таким ID не найден на этом сервере.") from error
                self.database.transfer_coins(
                    self.guild_id,
                    self.user_id,
                    recipient.id,
                    amount,
                )
                message = f"Переведено **{amount:,} {self.currency_name}** игроку {recipient.mention}."
            else:
                raise RuntimeError(f"Неизвестная форма меню: {action}")
        except ValueError:
            message = "Проверьте введённые значения: сумма должна быть целым числом."
            success = False
        except ClanError as error:
            message = str(error)
            success = False
        except disnake.HTTPException:
            LOGGER.exception("Не удалось проверить участника для денежного перевода.")
            message = "Discord не смог проверить участника. Попробуйте ещё раз позже."
            success = False
        else:
            success = True
        await self.send_action_result(interaction, message, success)


class ClanCog(commands.Cog):
    def __init__(self, database: ClanDatabase, settings: dict[str, Any]) -> None:
        self.database = database
        self.settings = settings

    @commands.slash_command(
        name="clan",
        description="Открыть единое меню клана и экономики",
    )
    async def clan(self, interaction: disnake.ApplicationCommandInteraction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=notice_embed(
                    "Только на сервере",
                    "Откройте меню кланов в Discord-сервере.",
                    self.settings["ui"]["error_color"],
                ),
                ephemeral=True,
            )
            return
        view = ClanMenuView(
            self.database,
            self.settings,
            interaction.guild.id,
            interaction.guild.name,
            interaction.author.id,
            interaction.author,
        )
        embed = await view.prepare("overview")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @commands.slash_command(
        name="profile",
        description="Показать профиль игрока на этом сервере",
    )
    async def profile(
        self,
        interaction: disnake.ApplicationCommandInteraction,
        member: disnake.Member | None = commands.Param(
            default=None,
            name=disnake.Localized("игрок", data={disnake.Locale.ru: "игрок"}),
            description=disnake.Localized(
                "Чей профиль показать",
                data={disnake.Locale.ru: "Чей профиль показать"},
            ),
        ),
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Профиль доступен только на сервере",
                ephemeral=True,
            )
            return
        user = member or interaction.author
        profile = self.database.get_profile(interaction.guild.id, user.id)
        embed = profile_embed(
            user,
            profile,
            self.settings["economy"]["currency_name"],
            self.settings["ui"]["color"],
        )
        await interaction.response.send_message(embed=embed, ephemeral=member is None)
