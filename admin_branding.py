"""Редактор оформления прямо в боте.

Отдельный роутер, потому что экранов много, а admin.py и без того
большой. Смысл модуля: владелец меняет текст меню, картинку над ним,
подписи кнопок, их цвет и премиум-эмодзи, не заходя на сервер и не
трогая ни одного файла — всё кнопками в чате.

Правки складываются в branding.json в папке данных, поэтому переживают
и перезапуск, и передеплой.
"""

from __future__ import annotations

import html
import logging
import os
from typing import Any, List, Tuple

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import branding
import config
import ui

log = logging.getLogger("emoji-bot.branding-ui")

router = Router(name="admin-branding")
router.message.filter(F.from_user.id.in_(config.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(config.ADMIN_IDS))

STYLE_LABELS = {"primary": "🔵 Синий", "success": "🟢 Зелёный", "danger": "🔴 Красный"}
MEDIA_KINDS = {"photo": "фото", "animation": "гифка", "video": "видео"}

#: Сколько кнопок показываем на одной странице списка.
BRAND_PAGE = 6


class Brand(StatesGroup):
    menu_text = State()
    media = State()
    label = State()
    icon = State()


def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


# --------------------------------------------------------------------------
# Главный экран оформления
# --------------------------------------------------------------------------

def markup() -> InlineKeyboardMarkup:
    theme = branding.theme()
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Текст меню", callback_data="adm:bmenu"),
            InlineKeyboardButton(text="🖼 Медиа меню", callback_data="adm:bmedia"),
        ],
        [InlineKeyboardButton(text="🔘 Кнопки и цвета", callback_data="adm:bbtns:0")],
        [InlineKeyboardButton(
            text=f"🎨 Тема: {STYLE_LABELS.get(theme, theme)}", callback_data="adm:btheme",
        )],
        [InlineKeyboardButton(text="👁 Показать меню", callback_data="adm:bpreview")],
        [InlineKeyboardButton(text="♻️ Сбросить оформление", callback_data="adm:breset")],
        [InlineKeyboardButton(text="◀️ В панель", callback_data="adm:home")],
    ])


def screen() -> str:
    media = branding.media()
    lines = [
        "🎨 <b>Оформление</b>\n",
        f"✏️ Текст меню: <b>{'свой' if branding.menu_text_raw() else 'стандартный'}</b>",
        f"🖼 Медиа меню: <b>{MEDIA_KINDS.get(media['type'], media['type']) if media else 'нет'}</b>",
        f"🎨 Тема кнопок: <b>{STYLE_LABELS.get(branding.theme())}</b>",
        f"🔘 Изменено кнопок: <b>{branding.customized_count()}</b>",
    ]
    if branding.problem():
        lines.append(f"\n⚠️ <code>{_esc(branding.problem())}</code>")
    lines.append(
        "\n<i>Всё меняется здесь, кнопками. На сервер заходить не нужно, "
        "перезапускать бота тоже.</i>"
    )
    return "\n".join(lines)


async def _home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    await ui.show(callback, text=screen(), markup=markup())


@router.callback_query(F.data == "adm:brand")
async def cb_brand(callback: CallbackQuery, state: FSMContext) -> None:
    await _home(callback, state)
    await callback.answer()


@router.callback_query(F.data == "adm:btheme")
async def cb_theme(callback: CallbackQuery, state: FSMContext) -> None:
    """Тема перебирается по кругу: три цвета, одно нажатие."""
    order = list(branding.STYLES)
    branding.set_theme(order[(order.index(branding.theme()) + 1) % len(order)])
    await callback.answer(STYLE_LABELS[branding.theme()])
    await _home(callback, state)


@router.callback_query(F.data == "adm:bpreview")
async def cb_preview(callback: CallbackQuery) -> None:
    """Показывает меню ровно так, как его увидит пользователь."""
    await callback.answer()
    # Импорт внутри функции: bot.py сам подключает этот роутер, и
    # встречный импорт на уровне модуля замкнул бы их друг на друга.
    import bot as user_bot

    await user_bot._send_menu(callback.message, callback.from_user.first_name)


@router.callback_query(F.data == "adm:breset")
async def cb_reset(callback: CallbackQuery, state: FSMContext) -> None:
    branding.reset_all()
    await callback.answer("Вернул стандартное оформление")
    await _home(callback, state)


# --------------------------------------------------------------------------
# Текст главного меню
# --------------------------------------------------------------------------

@router.callback_query(F.data == "adm:bmenu")
async def cb_menu_text(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Brand.menu_text)
    current = branding.menu_text_raw()
    body = (
        "✏️ <b>Текст главного меню</b>\n\n"
        "Пришли сообщение — оно станет текстом меню <b>как есть</b>. "
        "Жирный, курсив, ссылки, цитаты и премиум-эмодзи сохранятся: "
        "пиши сообщение так, как оно должно выглядеть.\n\n"
        "<b>Переменные</b> подставятся сами:\n"
        "<code>{name}</code> — имя · <code>{bot}</code> — юзернейм бота · "
        "<code>{templates}</code> — шаблонов · <code>{price}</code> — цена · "
        "<code>{fonts}</code> — шрифтов"
    )
    if current:
        body += f"\n\n<b>Сейчас:</b>\n<code>{_esc(current)[:500]}</code>"

    rows: List[List[InlineKeyboardButton]] = []
    if current:
        rows.append([InlineKeyboardButton(
            text="🧹 Вернуть стандартный", callback_data="adm:bmenuclear",
        )])
    rows.append([InlineKeyboardButton(text="◀️ К оформлению", callback_data="adm:brand")])
    await ui.show(callback, text=body, markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "adm:bmenuclear")
async def cb_menu_clear(callback: CallbackQuery, state: FSMContext) -> None:
    branding.set_menu_text(None)
    await callback.answer("Вернул стандартный текст")
    await _home(callback, state)


@router.message(Brand.menu_text)
async def on_menu_text(message: Message, state: FSMContext) -> None:
    """Сохраняет присланное сообщение как текст меню.

    Берём html_text, а не text: Telegram сам разворачивает оформление
    сообщения в теги, включая премиум-эмодзи. Поэтому админу не нужно
    знать ни одного тега — достаточно написать сообщение как надо.
    """
    body = message.html_text if (message.text or message.caption) else ""
    if not body.strip():
        await message.answer("❌ Нужен текст. Пришли сообщение с текстом меню.")
        return
    branding.set_menu_text(body)
    await state.set_state(None)
    await message.answer("✅ Текст меню обновлён.", reply_markup=markup())


# --------------------------------------------------------------------------
# Медиа над меню
# --------------------------------------------------------------------------

@router.callback_query(F.data == "adm:bmedia")
async def cb_media(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Brand.media)
    media = branding.media()
    rows: List[List[InlineKeyboardButton]] = []
    if media:
        rows.append([InlineKeyboardButton(text="🧹 Убрать медиа", callback_data="adm:bmediaclear")])
    rows.append([InlineKeyboardButton(text="◀️ К оформлению", callback_data="adm:brand")])
    await ui.show(
        callback,
        text=(
            "🖼 <b>Медиа над меню</b>\n\n"
            "Пришли <b>фото</b>, <b>гифку</b> или <b>видео</b> — оно встанет "
            "над текстом главного меню.\n\n"
            f"Сейчас: <b>{MEDIA_KINDS.get(media['type']) if media else 'нет'}</b>"
        ),
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:bmediaclear")
async def cb_media_clear(callback: CallbackQuery, state: FSMContext) -> None:
    branding.set_media(None, None)
    await callback.answer("Медиа убрано")
    await _home(callback, state)


@router.message(Brand.media)
async def on_media(message: Message, state: FSMContext) -> None:
    """Скачивает присланное медиа к себе и ставит его над меню.

    Файл сохраняем локально, а не храним file_id: id живут в пределах
    одного бота, и при смене токена меню осталось бы без картинки.
    """
    if message.photo:
        kind, file_id, ext = "photo", message.photo[-1].file_id, ".jpg"
    elif message.animation:
        kind, file_id, ext = "animation", message.animation.file_id, ".mp4"
    elif message.video:
        kind, file_id, ext = "video", message.video.file_id, ".mp4"
    else:
        await message.answer("❌ Нужно фото, гифка или видео — медиа, а не файл-документ.")
        return

    try:
        file = await message.bot.get_file(file_id)
        data = (await message.bot.download_file(file.file_path)).read()
    except Exception as exc:
        await message.answer(f"❌ Не скачалось: <code>{_esc(exc)}</code>")
        return

    path = os.path.join(branding.MEDIA_DIR, f"menu{ext}")
    with open(path, "wb") as fh:
        fh.write(data)
    branding.set_media(kind, path)

    await state.set_state(None)
    await message.answer(
        f"✅ {MEDIA_KINDS[kind].capitalize()} поставлено над меню.", reply_markup=markup(),
    )


# --------------------------------------------------------------------------
# Кнопки: подпись, цвет, премиум-эмодзи
# --------------------------------------------------------------------------

def _list_markup(page: int) -> InlineKeyboardMarkup:
    items = branding.EDITABLE_BUTTONS
    start = page * BRAND_PAGE
    rows: List[List[InlineKeyboardButton]] = []

    for index in range(start, min(start + BRAND_PAGE, len(items))):
        key, title, default = items[index]
        cfg = branding.button_config(key)
        mark = "🎨" if cfg else "▫️"
        label = str(cfg.get("text", default))
        rows.append([InlineKeyboardButton(
            text=f"{mark} {title} — {label[:18]}",
            callback_data=f"adm:bbtn:{index}",
        )])

    nav: List[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"adm:bbtns:{page - 1}"))
    if start + BRAND_PAGE < len(items):
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"adm:bbtns:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="◀️ К оформлению", callback_data="adm:brand")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("adm:bbtns:"))
async def cb_buttons(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    page = int(callback.data.rsplit(":", 1)[1])
    await ui.show(
        callback,
        text=(
            f"🔘 <b>Кнопки бота</b> — {len(branding.EDITABLE_BUTTONS)} шт.\n\n"
            "🎨 — изменена, ▫️ — стандартная.\n"
            "Нажми на кнопку, чтобы сменить подпись, цвет или поставить "
            "премиум-эмодзи."
        ),
        markup=_list_markup(page),
    )
    await callback.answer()


def _editor(index: int) -> Tuple[str, InlineKeyboardMarkup]:
    key, title, default = branding.EDITABLE_BUTTONS[index]
    cfg = branding.button_config(key)
    style = cfg.get("style")
    if style == branding.THEME:
        style_name = f"по теме ({STYLE_LABELS.get(branding.theme())})"
    else:
        style_name = STYLE_LABELS.get(style, "без цвета")

    text = (
        f"🔘 <b>{_esc(title)}</b>\n\n"
        f"✏️ Подпись: <code>{_esc(cfg.get('text', default))}</code>\n"
        f"🎨 Цвет: <b>{style_name}</b>\n"
        f"😀 Премиум-эмодзи: <b>{'стоит' if cfg.get('icon') else 'нет'}</b>\n\n"
        "<i>Telegram не разрешает форматирование внутри подписи кнопки, "
        "поэтому премиум-эмодзи ставится отдельно и рисуется перед "
        "подписью. Обычные эмодзи пиши прямо в подписи.</i>"
    )
    rows = [
        [InlineKeyboardButton(text="✏️ Сменить подпись", callback_data=f"adm:blabel:{index}")],
        [
            InlineKeyboardButton(text="🔵", callback_data=f"adm:bstyle:{index}:primary"),
            InlineKeyboardButton(text="🟢", callback_data=f"adm:bstyle:{index}:success"),
            InlineKeyboardButton(text="🔴", callback_data=f"adm:bstyle:{index}:danger"),
            InlineKeyboardButton(text="🎨", callback_data=f"adm:bstyle:{index}:theme"),
            InlineKeyboardButton(text="⚪️", callback_data=f"adm:bstyle:{index}:none"),
        ],
        [InlineKeyboardButton(text="😀 Премиум-эмодзи", callback_data=f"adm:bicon:{index}")],
    ]
    if cfg:
        rows.append([InlineKeyboardButton(
            text="🧹 Сбросить кнопку", callback_data=f"adm:bclear:{index}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ К списку", callback_data="adm:bbtns:0")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("adm:bbtn:"))
async def cb_button(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    text, keyboard = _editor(int(callback.data.rsplit(":", 1)[1]))
    await ui.show(callback, text=text, markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:bstyle:"))
async def cb_style(callback: CallbackQuery) -> None:
    _, _, index_raw, style = callback.data.split(":")
    index = int(index_raw)
    branding.set_button(
        branding.EDITABLE_BUTTONS[index][0], "style", None if style == "none" else style,
    )
    await callback.answer("Цвет применён")
    text, keyboard = _editor(index)
    await ui.show(callback, text=text, markup=keyboard)


@router.callback_query(F.data.startswith("adm:bclear:"))
async def cb_button_clear(callback: CallbackQuery) -> None:
    index = int(callback.data.rsplit(":", 1)[1])
    key = branding.EDITABLE_BUTTONS[index][0]
    for field in ("text", "style", "icon"):
        branding.set_button(key, field, None)
    await callback.answer("Кнопка сброшена")
    text, keyboard = _editor(index)
    await ui.show(callback, text=text, markup=keyboard)


@router.callback_query(F.data.startswith("adm:blabel:"))
async def cb_label(callback: CallbackQuery, state: FSMContext) -> None:
    index = int(callback.data.rsplit(":", 1)[1])
    _, title, default = branding.EDITABLE_BUTTONS[index]
    await state.set_state(Brand.label)
    await state.update_data(brand_index=index)

    hint = ""
    if "{" in default:
        variable = default[default.index("{"):default.index("}") + 1]
        hint = (f"\n\n⚠️ <i>В этой кнопке есть переменная "
                f"<code>{_esc(variable)}</code> — оставь её в подписи, "
                "иначе пропадёт число.</i>")
    await ui.show(
        callback,
        text=f"✏️ Пришли новую подпись для «{_esc(title)}».{hint}",
        markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"adm:bbtn:{index}")],
        ]),
    )
    await callback.answer()


@router.message(Brand.label, F.text)
async def on_label(message: Message, state: FSMContext) -> None:
    index = (await state.get_data()).get("brand_index")
    if index is None:
        await message.answer("Потерял кнопку, открой список заново.", reply_markup=markup())
        return

    label = (message.text or "").strip()
    if not label or len(label) > 64:
        await message.answer("❌ Подпись от 1 до 64 символов.")
        return

    branding.set_button(branding.EDITABLE_BUTTONS[int(index)][0], "text", label)
    await state.set_state(None)
    text, keyboard = _editor(int(index))
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("adm:bicon:"))
async def cb_icon(callback: CallbackQuery, state: FSMContext) -> None:
    index = int(callback.data.rsplit(":", 1)[1])
    key = branding.EDITABLE_BUTTONS[index][0]
    await state.set_state(Brand.icon)
    await state.update_data(brand_index=index)

    rows: List[List[InlineKeyboardButton]] = []
    if branding.button_config(key).get("icon"):
        rows.append([InlineKeyboardButton(
            text="🧹 Убрать эмодзи", callback_data=f"adm:biconoff:{index}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Отмена", callback_data=f"adm:bbtn:{index}")])
    await ui.show(
        callback,
        text=(
            "😀 <b>Премиум-эмодзи на кнопку</b>\n\n"
            "Пришли сообщение, в котором стоит нужное премиум-эмодзи — "
            "бот сам возьмёт из него id.\n\n"
            "<i>Отправить его может аккаунт с Telegram Premium. Обычные "
            "эмодзи сюда не нужны — их пиши прямо в подписи кнопки.</i>"
        ),
        markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:biconoff:"))
async def cb_icon_off(callback: CallbackQuery) -> None:
    index = int(callback.data.rsplit(":", 1)[1])
    branding.set_button(branding.EDITABLE_BUTTONS[index][0], "icon", None)
    await callback.answer("Эмодзи убрано")
    text, keyboard = _editor(index)
    await ui.show(callback, text=text, markup=keyboard)


@router.message(Brand.icon)
async def on_icon(message: Message, state: FSMContext) -> None:
    """Достаёт id премиум-эмодзи из присланного сообщения.

    Просить у админа голый id бессмысленно — его негде взять. Зато
    отправить само эмодзи может любой владелец Premium, а id лежит
    в сущностях сообщения.
    """
    entities = list(message.entities or []) + list(message.caption_entities or [])
    custom = next((e for e in entities if e.type == "custom_emoji"), None)
    if not custom:
        await message.answer("❌ В сообщении нет премиум-эмодзи. Пришли сообщение, где оно есть.")
        return

    index = (await state.get_data()).get("brand_index")
    if index is None:
        await message.answer("Потерял кнопку, открой список заново.", reply_markup=markup())
        return

    branding.set_button(
        branding.EDITABLE_BUTTONS[int(index)][0], "icon", custom.custom_emoji_id,
    )
    await state.set_state(None)
    text, keyboard = _editor(int(index))
    await message.answer("✅ Эмодзи поставлено на кнопку.")
    await message.answer(text, reply_markup=keyboard)
