"""Инлайн-клавиатуры.

Каждая кнопка проходит через branding.button: подпись, цвет и премиум-
эмодзи берутся из branding.json, а в коде остаются только значения по
умолчанию и callback_data. Формат callback_data описан рядом с функциями.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

import branding
import config
import settings

b = branding.button


def back_to_menu() -> InlineKeyboardButton:
    return b("back", "◀️ В меню", callback_data="menu:main")


def _rows(buttons: Sequence[InlineKeyboardButton], per_row: int) -> List[List[InlineKeyboardButton]]:
    return [list(buttons[i:i + per_row]) for i in range(0, len(buttons), per_row)]


def main_menu() -> InlineKeyboardMarkup:
    """Главное меню: профиль, создание эмодзи, поддержка."""
    rows: List[List[InlineKeyboardButton]] = []
    if config.WEBAPP_URL:
        rows.append([b("menu.app", "📱 Открыть мини-приложение",
                       web_app=WebAppInfo(url=config.WEBAPP_URL))])
    rows.append([b("menu.create", "✨ Создать эмодзи", callback_data="create:start")])
    rows.append([
        b("menu.profile", "👤 Профиль", callback_data="menu:profile"),
        b("menu.support", "💬 Поддержка", callback_data="menu:support"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [b("menu.create", "✨ Создать эмодзи", callback_data="create:start")],
        [b("profile.topup", "💼 Пополнить баланс", callback_data="topup:open")],
        [back_to_menu()],
    ])


def support() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    if config.SUPPORT_URL:
        rows.append([b("support.write", "✍️ Написать в поддержку", url=config.SUPPORT_URL)])
    if config.SUPPORT_CHAT_URL:
        rows.append([b("support.channel", "📣 Наш канал", url=config.SUPPORT_CHAT_URL)])
    rows.append([back_to_menu()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscribe(channels: Sequence[Dict[str, object]]) -> InlineKeyboardMarkup:
    """Экран обязательной подписки: кнопка на каждый канал.

    callback_data: ``sub:check``. Каналов может быть несколько, и на
    каждый нужна своя ссылка — одной кнопкой «Подписаться» тут не
    обойтись.
    """
    rows: List[List[InlineKeyboardButton]] = []
    for channel in channels:
        url = str(channel.get("url") or "")
        if not url:
            continue
        title = str(channel.get("title") or "Канал")
        rows.append([InlineKeyboardButton(text=f"📣 {title[:40]}", url=url)])
    rows.append([b("subscribe.check", "✅ Я подписался", callback_data="sub:check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def packs(counts: Dict[str, int]) -> InlineKeyboardMarkup:
    """Список наборов шаблонов. callback_data: ``pack:<id>``."""
    rows = [
        [b(f"pack.{pack_id}", f"{pack.get('emoji', '📦')} {pack['title']} — {{count}} шт.",
           callback_data=f"pack:{pack_id}",
           variables={"count": counts.get(pack_id, 0), "title": pack["title"]})]
        for pack_id, pack in config.PACKS.items()
    ]
    rows.append([back_to_menu()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mode() -> InlineKeyboardMarkup:
    """Как выбирать шаблоны. callback_data: ``mode:random`` / ``mode:manual``."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [b("mode.random", "🎲 Выбрать случайно", callback_data="mode:random")],
        [b("mode.manual", "✋ Выбрать самому", callback_data="mode:manual")],
        [b("back.step", "◀️ Назад", callback_data="create:start")],
    ])


def fonts(custom_font_id: Optional[str], back_to: str) -> InlineKeyboardMarkup:
    """Витрина шрифтов. callback_data: ``font:<id>``."""
    buttons = [
        b(f"font.{font_id}", label, callback_data=f"font:{font_id}")
        for font_id, (label, _) in config.FONTS.items()
    ]
    rows = _rows(buttons, 2)
    if custom_font_id:
        rows.append([b("font.custom", "🅰️ Мой шрифт", callback_data=f"font:{custom_font_id}")])
    rows.append([
        b("font.upload", "⬆️ Загрузить свой", callback_data="font:upload"),
        b("font.reroll", "🔄 Другие примеры", callback_data="font:reroll"),
    ])
    rows.append([b("back.step", "◀️ Назад", callback_data=back_to)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def simple_back(target: str = "menu:main", label: str = "◀️ Назад") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [b("back.step", label, callback_data=target)],
    ])


def content_input() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [b("content.back", "◀️ К шрифтам", callback_data="step:font")],
        [back_to_menu()],
    ])


def quantity(available: int) -> InlineKeyboardMarkup:
    """Пресеты количества. callback_data: ``qty:<число>``."""
    # Потолок — меньшее из «сколько шаблонов есть» и «сколько влезает
    # в набор»: предлагать 325 штук бессмысленно, Telegram держит 200.
    ceiling = min(available, config.MAX_ORDER)
    values = [v for v in config.QUANTITY_PRESETS if v <= ceiling]
    if ceiling not in values:
        values.append(ceiling)
    buttons = [
        b("qty.item", "{count} ⭐️{price}", callback_data=f"qty:{v}",
          variables={"count": v, "price": v * settings.price()})
        for v in values
    ]
    rows = _rows(buttons, 3)
    rows.append([b("qty.back", "◀️ К надписи", callback_data="step:content")])
    rows.append([back_to_menu()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def selection(page: int, pages: int, numbers: List[int],
              selected: Iterable[int]) -> InlineKeyboardMarkup:
    """Страница ручного выбора.

    callback_data: ``sel:<номер>`` — переключить, ``selpage:<стр>`` —
    листание, ``selall`` / ``selclear`` — вся страница разом.
    Отмеченные шаблоны помечены галочкой, иначе после листания непонятно,
    что уже выбрано.
    """
    chosen = set(selected)
    buttons = [
        InlineKeyboardButton(
            text=("✅ " if n in chosen else "") + str(n),
            callback_data=f"sel:{n}",
        )
        for n in numbers
    ]
    rows = _rows(buttons, 4)

    if pages > 1:
        # На 28 страницах листать по одной невыносимо, поэтому рядом с
        # шагом стоит прыжок на пять страниц.
        rows.append([
            InlineKeyboardButton(text="⏪", callback_data=f"selpage:{(page - 5) % pages}"),
            InlineKeyboardButton(text="⬅️", callback_data=f"selpage:{(page - 1) % pages}"),
            InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"),
            InlineKeyboardButton(text="➡️", callback_data=f"selpage:{(page + 1) % pages}"),
            InlineKeyboardButton(text="⏩", callback_data=f"selpage:{(page + 5) % pages}"),
        ])

    rows.append([
        b("selection.all", "✅ Вся страница", callback_data="selall"),
        b("selection.clear", "🧹 Сбросить", callback_data="selclear"),
    ])
    rows.append([b("selection.done", "➡️ Дальше ({count})", callback_data="seldone",
                   variables={"count": len(chosen)})])
    rows.append([b("back.step", "◀️ Назад", callback_data="step:mode")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_preview(amount: int, can_reroll: bool, balance: int,
                  has_packs: bool, kind: str) -> InlineKeyboardMarkup:
    """Финальное превью: перевыбор любого шага, выбор набора и оплата."""
    rows: List[List[InlineKeyboardButton]] = []
    rows.append([b(
        "order.reroll",
        "🎲 Перевыбрать шаблоны" if can_reroll else "✋ Перевыбрать шаблоны",
        callback_data="order:reroll" if can_reroll else "mode:manual",
    )])
    rows.append([
        b("order.font", "🔤 Другой шрифт", callback_data="step:font"),
        b("order.text", "✏️ Другая надпись", callback_data="step:content"),
    ])
    # Тип набора — прямо здесь: человек уже видит результат и решает,
    # эмодзи это или стикеры, не возвращаясь на шаг назад.
    other = "sticker" if kind == "emoji" else "emoji"
    rows.append([b("order.kind", "{icon} {title} · сменить", callback_data=f"kind:{other}",
                   variables={"icon": config.KINDS[kind]["icon"],
                              "title": config.KINDS[kind]["title"]})])
    if has_packs:
        rows.append([b("order.target", "📦 Куда добавить", callback_data="target:open")])

    if amount == 0:
        # Нулевая сумма бывает только у админов: счёт на ноль звёзд
        # Telegram не примет, поэтому сборка запускается напрямую.
        rows.append([b("order.free", "🎁 Собрать бесплатно", callback_data="order:free")])
    else:
        # Оплата с баланса — первой кнопкой, когда денег хватает: это один
        # тап против счёта с подтверждением.
        if balance >= amount:
            rows.append([b("order.paybalance", "💼 Списать с баланса ({amount} ⭐️)",
                           callback_data="order:paybalance", variables={"amount": amount})])
        rows.append([b("order.pay", "⭐️ Оплатить счётом · {amount}",
                       callback_data="order:pay", variables={"amount": amount})])
    rows.append([back_to_menu()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def after_pack(pack_name: str, kind: str = "emoji") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [b("after.open", "📦 Открыть набор", url=config.pack_url(kind, pack_name))],
        [b("after.more", "✨ Собрать ещё", callback_data="create:start")],
        [back_to_menu()],
    ])


def topup() -> InlineKeyboardMarkup:
    """Суммы пополнения. callback_data: ``topup:<сумма>``."""
    buttons = [
        b("topup.item", "{amount} ⭐️", callback_data=f"topup:{v}", variables={"amount": v})
        for v in config.TOPUP_PRESETS
    ]
    rows = _rows(buttons, 3)
    rows.append([b("topup.profile", "👤 Профиль", callback_data="menu:profile")])
    rows.append([back_to_menu()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def targets(packs_list: Sequence[Dict[str, object]], current: Optional[str]) -> InlineKeyboardMarkup:
    """Выбор набора-получателя. callback_data: ``target:new`` / ``target:<имя>``.

    Текущий выбор помечен точкой: списком в пять наборов иначе непонятно,
    куда уйдёт заказ.
    """
    rows = [[b("target.new", ("• " if current is None else "") + "➕ Новый набор",
               callback_data="target:new")]]
    for pack in packs_list:
        name = str(pack["name"])
        rows.append([InlineKeyboardButton(
            text=("• " if current == name else "") + f"📦 {pack['title']} ({pack['count']})",
            callback_data=f"target:{name}",
        )])
    rows.append([b("target.back", "◀️ К превью", callback_data="target:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def retry_order(order_id: int) -> InlineKeyboardMarkup:
    """Кнопка повтора отложенной сборки. callback_data: ``order:retry:<id>``."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Повторить сборку", callback_data=f"order:retry:{order_id}")],
        [back_to_menu()],
    ])


def topup_methods() -> InlineKeyboardMarkup:
    """Чем пополнять баланс. Крипта показывается, только если настроена."""
    import crypto

    rows = [[b("topup.stars", "⭐️ Telegram Stars", callback_data="topup:stars")]]
    if crypto.enabled():
        rows.append([b("topup.crypto", "🪙 Криптой (@CryptoBot)", callback_data="topup:crypto")])
    rows.append([b("topup.profile", "👤 Профиль", callback_data="menu:profile")])
    rows.append([back_to_menu()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def crypto_amounts() -> InlineKeyboardMarkup:
    """Суммы пополнения криптой. callback_data: ``crypto:<звёзды>``."""
    buttons = [
        b("crypto.item", "{amount} ⭐️", callback_data=f"crypto:{v}", variables={"amount": v})
        for v in config.TOPUP_PRESETS
    ]
    rows = _rows(buttons, 3)
    rows.append([b("back.step", "◀️ Назад", callback_data="topup:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def crypto_invoice(url: str, invoice_id: int) -> InlineKeyboardMarkup:
    """Ссылка на оплату и ручная проверка. callback_data: ``paid:<id>``."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [b("crypto.pay", "💵 Оплатить", url=url)],
        [b("crypto.check", "✅ Я оплатил", callback_data=f"paid:{invoice_id}")],
        [back_to_menu()],
    ])
