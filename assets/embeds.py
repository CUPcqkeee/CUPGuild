from collections.abc import Sequence
import re
from typing import Any

import disnake


def _color(value: str) -> disnake.Color:
    return disnake.Color(int(value.removeprefix("#"), 16))


def remove_sentence_periods(embed: disnake.Embed) -> disnake.Embed:
    def clean(value: str | None) -> str | None:
        if value is None:
            return None
        return re.sub(r"\.(?=\s|$)", "", value)

    if embed.title:
        embed.title = clean(embed.title)
    if embed.description:
        embed.description = clean(embed.description)
    for index, field in enumerate(embed.fields):
        embed.set_field_at(
            index,
            name=clean(field.name) or field.name,
            value=clean(field.value) or field.value,
            inline=field.inline,
        )
    if embed.footer.text:
        embed.set_footer(text=clean(embed.footer.text), icon_url=embed.footer.icon_url)
    if embed.author.name:
        embed.set_author(
            name=clean(embed.author.name),
            url=embed.author.url,
            icon_url=embed.author.icon_url,
        )
    return embed


def action_result_embed(
    message: str,
    success: bool,
    success_color: str,
    error_color: str,
) -> disnake.Embed:
    return remove_sentence_periods(disnake.Embed(
        title="✅ Действие выполнено" if success else "❌ Не удалось выполнить действие",
        description=message,
        color=_color(success_color if success else error_color),
    ))


def overview_embed(
    guild_name: str,
    clan: dict[str, Any] | None,
    clan_count: int,
    member_count: int,
    color: str,
    notice: str | None = None,
    success_color: str | None = None,
) -> disnake.Embed:
    embed = disnake.Embed(
        title="🏰 CUPGuild | Центр кланов",
        description=(
            f"Добро пожаловать на **{guild_name}**!\n"
            "Создавай команду, собирай состав, зарабатывай монеты и участвуй в событиях и войнах.\n"
            "```Все действия доступны в этом меню```"
        ),
        color=_color(success_color if notice and success_color else color),
    )
    if clan:
        embed.add_field(
            name="🛡️ Ваш клан",
            value=f"**[{clan['tag']}] {clan['name']}**\n{clan['description'] or 'Описание пока не добавлено.'}",
            inline=False,
        )
    else:
        embed.add_field(
            name="🛡️ Ваш клан",
            value="Вы пока не состоите в клане. Создайте свой или выберите открытый клан в разделе **«Найти клан»**.",
            inline=False,
        )
    embed.add_field(name="🏰 Кланы сервера", value=f"`{clan_count}`", inline=True)
    embed.add_field(name="👥 Игроки в кланах", value=f"`{member_count}`", inline=True)
    embed.add_field(
        name="🧭 Разделы",
        value=(
            "🏠 Обзор · 🛡️ Мой клан · 🔎 Найти клан\n"
            "🏆 Рейтинг · 📖 Справка"
        ),
        inline=False,
    )
    if notice:
        embed.add_field(name="✅ Готово", value=notice, inline=False)
    embed.set_footer(text="Выберите раздел в меню ниже — сообщение обновится на месте.")
    return embed


def clan_embed(
    guild_name: str,
    clan: dict[str, Any] | None,
    members: Sequence[dict[str, Any]],
    member_limit: int,
    member_list_limit: int,
    color: str,
    currency_name: str = "крон",
    notice: str | None = None,
) -> disnake.Embed:
    embed = disnake.Embed(title="🛡️ Мой клан", color=_color(color))
    if clan is None:
        embed.description = (
            "У вас пока нет клана.\n\n"
            "Создайте собственную команду или найдите подходящую в разделе **«Найти клан»**."
        )
        embed.set_footer(text=f"Сервер: {guild_name}")
        return embed

    embed.description = f"**[{clan['tag']}] {clan['name']}**\n{clan['description'] or 'Описание не указано.'}"
    embed.add_field(name="👑 Владелец", value=f"<@{clan['owner_id']}>", inline=True)
    embed.add_field(name="📅 Создан", value=clan["created_at"][:10], inline=True)
    embed.add_field(name="👥 Состав", value=f"{clan['member_count']} / {member_limit}", inline=True)
    embed.add_field(name="🏦 Казна", value=f"{clan['treasury']:,} {currency_name}", inline=True)

    shown_members = members[:member_list_limit]
    member_lines = [
        f"{_rank_emoji(member, clan['owner_id'])} <@{member['user_id']}> · {_rank_name(member, clan['owner_id'])}"
        for member in shown_members
    ]
    remaining = clan["member_count"] - len(shown_members)
    if remaining > 0:
        member_lines.append(f"… и ещё **{remaining}**")
    embed.add_field(name="📋 Участники", value="\n".join(member_lines) or "Состав пока пуст.", inline=False)
    if notice:
        embed.add_field(name="📣 Результат", value=notice, inline=False)
    embed.add_field(name="🔓 Набор", value="Открыт" if clan.get("is_open", 1) else "Закрыт", inline=True)
    embed.set_footer(text=f"Сервер: {guild_name} · У каждого ранга свои полномочия")
    return embed


def _rank_name(member: dict[str, Any], owner_id: int) -> str:
    if member["user_id"] == owner_id:
        return "Владелец"
    return {
        "deputy": "Заместитель",
        "officer": "Офицер",
        "veteran": "Ветеран",
        "member": "Участник",
    }[member["role"]]


def _rank_emoji(member: dict[str, Any], owner_id: int) -> str:
    if member["user_id"] == owner_id:
        return "👑"
    return {"deputy": "💠", "officer": "⚔️", "veteran": "🛡️", "member": "・"}[member["role"]]


def clan_management_embed(
    clan: dict[str, Any],
    actor_rank: str,
    members: Sequence[dict[str, Any]],
    color: str,
) -> disnake.Embed:
    embed = disnake.Embed(
        title=f"⚙️ Управление кланом [{clan['tag']}]",
        description=(
            f"**{clan['name']}**\n"
            f"Ваш ранг: **{actor_rank}**\n\n"
            "Выберите участника, затем используйте кнопки для изменения ранга или исключения."
        ),
        color=_color(color),
    )
    shown_members = members[:15]
    lines = [
        f"{_rank_emoji(member, clan['owner_id'])} <@{member['user_id']}> — "
        f"**{_rank_name(member, clan['owner_id'])}**"
        for member in shown_members
    ]
    if len(members) > len(shown_members):
        lines.append(f"… и ещё **{len(members) - len(shown_members)}**")
    embed.add_field(name="📋 Иерархия", value="\n".join(lines) or "Пока нет других участников.", inline=False)
    embed.add_field(
        name="🔐 Полномочия",
        value=(
            "👑 **Владелец** — полный контроль и настройка прав рангов\n"
            "💠 **Заместитель** — права назначает владелец\n"
            "⚔️ **Офицер** — права назначает владелец\n"
            "🛡️ **Ветеран** — права назначает владелец\n"
            "・ **Участник** — права назначает владелец"
        ),
        inline=False,
    )
    return embed


def clan_settings_embed(clan: dict[str, Any], color: str) -> disnake.Embed:
    embed = disnake.Embed(
        title="🎨 Параметры клана",
        description=f"**[{clan['tag']}] {clan['name']}**",
        color=_color(color),
    )
    embed.add_field(name="🎨 Цвет", value=f"`{clan.get('color', color)}`", inline=True)
    embed.add_field(name="🚪 Вступление", value="Открыто" if clan.get("is_open", 1) else "Закрыто", inline=True)
    embed.add_field(
        name="ℹ️ Как изменить",
        value="Нажмите кнопку настройки и заполните форму. Цвет указывается в формате `#RRGGBB`",
        inline=False,
    )
    return embed


def role_permissions_embed(
    clan: dict[str, Any],
    role_name: str,
    permission_labels: dict[str, str],
    color: str,
) -> disnake.Embed:
    embed = disnake.Embed(
        title=f"🔐 Права ранга · {role_name}",
        description=(
            f"Клан **[{clan['tag']}] {clan['name']}**\n"
            "Нажмите на право, чтобы включить или отключить его. Кнопки показывают текущий статус"
        ),
        color=_color(color),
    )
    embed.add_field(
        name="Доступные права",
        value="\n".join(permission_labels.values()),
        inline=False,
    )
    embed.set_footer(text="Владелец всегда сохраняет полный доступ к клану")
    return embed


def confirmation_embed(clan: dict[str, Any], color: str) -> disnake.Embed:
    return disnake.Embed(
        title="⚠️ Распустить клан?",
        description=(
            f"Вы собираетесь окончательно распустить **[{clan['tag']}] {clan['name']}**.\n"
            "Все участники будут удалены из состава. Это действие нельзя отменить."
        ),
        color=_color(color),
    )


def profile_embed(
    user: disnake.User | disnake.Member,
    profile: dict[str, Any],
    currency_name: str,
    color: str,
) -> disnake.Embed:
    embed = disnake.Embed(
        title=f"👤 Профиль · {user.display_name}",
        color=_color(color),
        timestamp=disnake.utils.utcnow(),
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="⭐ Уровень", value=f"**{profile['level']}**", inline=True)
    embed.add_field(name="✨ Опыт", value=f"**{profile['xp']} XP**", inline=True)
    embed.add_field(name="💰 Баланс", value=f"**{profile['balance']:,} {currency_name}**", inline=True)

    progress = profile["xp_progress"]
    required = profile["xp_required"]
    filled = min(12, int(progress / required * 12)) if required else 12
    bar = "🟦" * filled + "⬛" * (12 - filled)
    embed.add_field(
        name="📈 Прогресс уровня",
        value=f"{bar}\n`{progress} / {required} XP` до уровня **{profile['level'] + 1}**",
        inline=False,
    )
    if profile["clan_id"] is not None:
        clan_role = {
            "member": "Участник",
            "veteran": "Ветеран",
            "officer": "Офицер",
            "deputy": "Заместитель",
        }.get(profile["clan_role"], profile["clan_role"])
        embed.add_field(
            name="🛡️ Клан",
            value=f"**[{profile['clan_tag']}] {profile['clan_name']}** · {clan_role}",
            inline=False,
        )
    else:
        embed.add_field(name="🛡️ Клан", value="Пока не состоит в клане", inline=False)
    embed.set_footer(text=f"Профиль игрока на сервере · ID {user.id}")
    return remove_sentence_periods(embed)


def economy_leaderboard_embed(
    entries: Sequence[dict[str, Any]],
    currency_name: str,
    color: str,
) -> disnake.Embed:
    embed = disnake.Embed(title="💰 Богатейшие игроки", color=_color(color))
    if not entries:
        embed.description = "Пока нет участников в таблице. Заберите первую награду в разделе «Экономика»."
        return embed
    medals = ("🥇", "🥈", "🥉")
    embed.description = "\n".join(
        f"{medals[index] if index < len(medals) else f'**{index + 1}.**'} "
        f"<@{entry['user_id']}> · **{entry['balance']:,} {currency_name}** · ур. {entry['level']}"
        for index, entry in enumerate(entries)
    )
    return embed


def economy_menu_embed(
    user: disnake.User | disnake.Member | None,
    profile: dict[str, Any],
    currency_name: str,
    color: str,
) -> disnake.Embed:
    embed = (
        profile_embed(user, profile, currency_name, color)
        if user is not None
        else disnake.Embed(title="💰 Кошелёк и уровень", color=_color(color))
    )
    embed.title = "💰 Кошелёк и уровень"
    embed.description = (
        f"Ежедневная серия: **{profile['daily_streak']}** дн.\n"
        "Забирайте награду, работайте, переводите монеты и управляйте казной здесь."
    )
    return embed


def clan_events_embed(
    events: Sequence[dict[str, Any]],
    currency_name: str,
    color: str,
) -> disnake.Embed:
    embed = disnake.Embed(
        title="🎉 События клана",
        description="Создавайте активности, собирайте участников и делите призовой фонд.",
        color=_color(color),
    )
    if not events:
        embed.description = (
            "Открытых событий нет.\n"
            "Владелец может выдавать право на создание событий любому рангу"
        )
        return embed
    embed.description = "\n\n".join(
        f"**#{event['id']} · {event['title']}**\n"
        f"{event['description'] or 'Описание не добавлено.'}\n"
        f"👥 Участников: **{event['participant_count']}** · "
        f"🏆 Приз: **{event['reward']:,} {currency_name}**"
        for event in events[:6]
    )
    if len(events) > 6:
        embed.set_footer(text=f"Показаны 6 из {len(events)} событий. Список выбора содержит до 25 событий.")
    return embed


def clan_wars_embed(
    wars: Sequence[dict[str, Any]],
    clan_id: int | None,
    currency_name: str,
    color: str,
) -> disnake.Embed:
    embed = disnake.Embed(
        title="⚔️ Клановые войны",
        description="Бросайте вызов соперникам, согласуйте ставку казной и ведите счёт кнопками",
        color=_color(color),
    )
    if clan_id is None:
        embed.description = "Вступите в клан, чтобы участвовать в клановых войнах."
        return embed
    if not wars:
        embed.description = "Нет открытых вызовов или активных войн. Владелец может выдать право вызывать соперников любому рангу"
        return embed
    status_labels = {"pending": "Ожидает ответа", "active": "Идёт"}
    embed.description = "\n\n".join(
        f"**#{war['id']} · {status_labels[war['status']]}**\n"
        f"[{war['clan_one_tag']}] {war['clan_one_name']} "
        f"`{war['score_one']}` : `{war['score_two']}` "
        f"[{war['clan_two_tag']}] {war['clan_two_name']}\n"
        f"🏆 Приз: **{war['reward']:,} {currency_name}**"
        for war in wars[:10]
    )
    if len(wars) > 10:
        embed.set_footer(text=f"Показаны 10 из {len(wars)} войн. Список выбора содержит до 25 войн.")
    return embed


def discovery_embed(
    clans: Sequence[dict[str, Any]],
    member_limit: int,
    color: str,
) -> disnake.Embed:
    embed = disnake.Embed(
        title="🔎 Найти клан",
        description="Выберите открытый клан из списка ниже, чтобы присоединиться.",
        color=_color(color),
    )
    if not clans:
        embed.description = "Сейчас нет открытых кланов с доступными местами. Загляните позже или создайте свой."
        return embed

    lines = [
        f"**[{clan['tag']}] {clan['name']}** — {clan['member_count']}/{member_limit} участников"
        for clan in clans[:10]
    ]
    embed.add_field(name="✨ Доступные команды", value="\n".join(lines), inline=False)
    embed.set_footer(text="Список может содержать до 25 кланов. Выбор сделан прямо в меню.")
    return embed


def ranking_embed(guild_name: str, clans: Sequence[dict[str, Any]], color: str) -> disnake.Embed:
    embed = disnake.Embed(title="🏆 Рейтинг кланов", color=_color(color))
    if not clans:
        embed.description = "Пока не с кем соревноваться — первым создайте клан!"
        return embed

    lines = [
        f"**{index}. [{clan['tag']}] {clan['name']}** · {clan['member_count']} участ. · "
        f"{'Открыт' if clan.get('is_open', 1) else 'Закрыт'}"
        for index, clan in enumerate(clans, start=1)
    ]
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Рейтинг по числу участников · {guild_name}")
    return embed


def guide_embed(color: str) -> disnake.Embed:
    embed = disnake.Embed(
        title="📖 Путеводитель CUPGuild",
        description="Коротко о том, как устроен клановый центр.",
        color=_color(color),
    )
    embed.add_field(
        name="🏰 Создание клана",
        value="Нажмите **«Создать клан»**, задайте тег, название и описание. Автор автоматически станет владельцем",
        inline=False,
    )
    embed.add_field(
        name="🤝 Вступление",
        value="Откройте **«Найти клан»** и выберите команду из списка. В одном сервере можно состоять только в одном клане.",
        inline=False,
    )
    embed.add_field(
        name="📌 Уже работает",
        value=(
            "Кланы, уровни, валюта и составы сохраняются в SQLite. Ежедневная награда, работа, "
            "переводы и профиль доступны кнопками в меню."
        ),
        inline=False,
    )
    embed.add_field(
        name="💰 Экономика клана",
        value=(
            "Владелец и назначенные им ранги могут получить клановый доход. Часть делится "
            "между руководителями, остаток поступает в казну. Пополняйте и снимайте средства кнопками."
        ),
        inline=False,
    )
    embed.add_field(
        name="⚔️ Войны и события",
        value=(
            "Создавайте события с призом из казны, записывайте участников и распределяйте награду. "
            "Объявляйте войны с другой командой, принимайте вызов, ведите счёт и забирайте приз прямо в меню."
        ),
        inline=False,
    )
    embed.add_field(
        name="🎖️ Иерархия",
        value="👑 Владелец · 💠 Заместитель · ⚔️ Офицер · 🛡️ Ветеран · 👤 Участник",
        inline=False,
    )
    embed.add_field(
        name="🗺️ Следующие направления",
        value="Заявки и набор · союзы · войны · события и статистика.",
        inline=False,
    )
    return embed


def notice_embed(title: str, description: str, color: str) -> disnake.Embed:
    return remove_sentence_periods(
        disnake.Embed(
            title=title,
            description=description,
            color=_color(color),
        )
    )
