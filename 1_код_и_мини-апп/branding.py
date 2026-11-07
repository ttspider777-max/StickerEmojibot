"""Оформление бота, которое меняется без правки кода.

Всё, что видит пользователь снаружи — текст главного меню, картинка над
ним, подписи кнопок, их цвет и премиум-эмодзи на них — лежит в
``branding.json`` рядом с ботом. Модуль читает файл, подставляет
переменные и отдаёт готовые кнопки.

Файл можно перечитать на ходу: ``/reload`` у админа или кнопка в панели.
Если файла нет или он сломан, бот работает на значениях по умолчанию из
кода — оформление не та вещь, из-за которой бот должен падать.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

from aiogram.types import InlineKeyboardButton

import config

log = logging.getLogger("emoji-bot.branding")

#: Настройки оформления пишет сам бот из админки, поэтому файл живёт в
#: папке данных: она переживает передеплой, а папка с кодом — нет.
BRANDING_PATH = os.path.join(config.DATA_DIR, "branding.json")

#: Файл, положенный рядом с кодом. Читается один раз, если своих
#: настроек ещё нет, — это заготовка «из коробки».
SHIPPED_PATH = os.path.join(config.BASE_DIR, "branding.json")

#: Куда складываем присланные из админки картинки и видео меню.
MEDIA_DIR = os.path.join(config.DATA_DIR, "menu_media")
os.makedirs(MEDIA_DIR, exist_ok=True)

#: Допустимые цвета кнопок из Bot API.
STYLES = ("primary", "success", "danger")

#: Значение style, означающее «взять цвет из общей темы».
THEME = "theme"

#: Типы медиа, которые можно поставить над главным меню.
MEDIA_TYPES = ("photo", "animation", "video")

_data: Dict[str, Any] = {}
_problem: Optional[str] = None


class _Vars(dict):
    """Словарь, который не роняет format() на незнакомой переменной.

    В тексте меню человек может опечататься в имени переменной, и падать
    из-за этого посреди /start бот не должен: неизвестное имя останется
    в тексте как есть, и опечатка будет видна глазами.
    """

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def load() -> Optional[str]:
    """Читает branding.json. Возвращает описание проблемы либо None."""
    global _data, _problem
    path = BRANDING_PATH if os.path.exists(BRANDING_PATH) else SHIPPED_PATH
    if not os.path.exists(path):
        _data, _problem = {}, None
        log.info("branding.json не найден — оформление по умолчанию")
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if not isinstance(loaded, dict):
            raise ValueError("в корне файла должен быть объект {...}")
    except Exception as exc:
        # Старое оформление оставляем в силе: сломанный файл не повод
        # показывать людям пустое меню.
        _problem = str(exc)
        log.error("branding.json не прочитался: %s", exc)
        return _problem
    _data, _problem = loaded, None
    log.info("Оформление загружено: тема %s, кнопок настроено %d",
             theme(), len(_data.get("buttons") or {}))
    return None


def problem() -> Optional[str]:
    """Последняя ошибка чтения файла — её показывает админ-панель."""
    return _problem


def theme() -> str:
    value = str(_data.get("theme") or "").lower()
    return value if value in STYLES else "primary"


def set_theme(value: str) -> None:
    """Меняет тему и сразу пишет её в файл — правка переживёт перезапуск."""
    if value not in STYLES:
        return
    _data["theme"] = value
    save()


def save() -> None:
    try:
        with open(BRANDING_PATH, "w", encoding="utf-8") as fh:
            json.dump(_data, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        log.error("branding.json не записался: %s", exc)


# --------------------------------------------------------------------------
# Тексты
# --------------------------------------------------------------------------

def text(key: str, default: str, **variables: Any) -> str:
    """Текст из файла с подставленными переменными, иначе значение по умолчанию."""
    # Ключ ищем и на верхнем уровне, и в разделе texts: в файле удобнее
    # писать "menu": "...", а группировать в "texts" — дело вкуса.
    raw = _data.get(key)
    if raw is None:
        raw = (_data.get("texts") or {}).get(key)
    if not isinstance(raw, str) or not raw.strip():
        return default
    try:
        return raw.format_map(_Vars(variables))
    except Exception as exc:
        log.warning("Текст %s не подставился: %s", key, exc)
        return default


def media() -> Optional[Dict[str, str]]:
    """Картинка, гифка или видео над главным меню.

    Отдаём только то, что реально лежит на диске: ссылка на пропавший
    файл превратила бы главное меню в ошибку при каждом /start.
    """
    raw = _data.get("menu_media")
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("type") or "").lower()
    path = str(raw.get("file") or "")
    if kind not in MEDIA_TYPES or not path:
        return None
    full = path if os.path.isabs(path) else os.path.join(config.BASE_DIR, path)
    if not os.path.exists(full):
        # Второй заход: медиа из админки лежит в папке данных.
        full = os.path.join(config.DATA_DIR, os.path.basename(path))
    if not os.path.exists(full):
        log.warning("Медиа главного меню не найдено: %s", full)
        return None
    return {"type": kind, "path": full}


# --------------------------------------------------------------------------
# Кнопки
# --------------------------------------------------------------------------

def button(key: str, default_text: str, **kwargs: Any) -> InlineKeyboardButton:
    """Кнопка с подписью, цветом и премиум-эмодзи из файла.

    ``style`` берётся из настройки кнопки, а значение ``theme`` означает
    общий цвет бота — так одной строкой перекрашиваются все главные
    кнопки сразу. ``icon`` — id премиум-эмодзи, оно рисуется перед
    подписью; в самом тексте кнопки эмодзи-сущностей не бывает.
    """
    variables = kwargs.pop("variables", {}) or {}
    settings_for_key = (_data.get("buttons") or {}).get(key)

    label = default_text
    style: Optional[str] = None
    icon: Optional[str] = None

    if isinstance(settings_for_key, str):
        label = settings_for_key
    elif isinstance(settings_for_key, dict):
        label = str(settings_for_key.get("text") or default_text)
        raw_style = str(settings_for_key.get("style") or "").lower()
        if raw_style == THEME:
            style = theme()
        elif raw_style in STYLES:
            style = raw_style
        icon_value = settings_for_key.get("icon")
        if icon_value:
            icon = str(icon_value)

    if variables:
        try:
            label = label.format_map(_Vars(variables))
        except Exception:
            pass

    extra: Dict[str, Any] = {}
    if style:
        extra["style"] = style
    if icon:
        extra["icon_custom_emoji_id"] = icon
    return InlineKeyboardButton(text=label, **kwargs, **extra)


load()


# --------------------------------------------------------------------------
# Правки из админки
# --------------------------------------------------------------------------

#: Кнопки, которые админ может менять из панели: ключ, человеческое
#: название и значение по умолчанию. Порядок — как на экранах бота.
EDITABLE_BUTTONS = [
    ("menu.app", "Главное меню · Мини-приложение", "📱 Открыть мини-приложение"),
    ("menu.create", "Главное меню · Создать эмодзи", "✨ Создать эмодзи"),
    ("menu.profile", "Главное меню · Профиль", "👤 Профиль"),
    ("menu.support", "Главное меню · Поддержка", "💬 Поддержка"),
    ("back", "Везде · В меню", "◀️ В меню"),
    ("back.step", "Везде · Назад", "◀️ Назад"),
    ("profile.topup", "Профиль · Пополнить баланс", "💼 Пополнить баланс"),
    ("subscribe.join", "Подписка · Подписаться", "📣 Подписаться"),
    ("subscribe.check", "Подписка · Я подписался", "✅ Я подписался"),
    ("mode.random", "Выбор · Случайно", "🎲 Выбрать случайно"),
    ("mode.manual", "Выбор · Самому", "✋ Выбрать самому"),
    ("font.upload", "Шрифты · Загрузить свой", "⬆️ Загрузить свой"),
    ("font.reroll", "Шрифты · Другие примеры", "🔄 Другие примеры"),
    ("selection.done", "Каталог · Дальше", "➡️ Дальше ({count})"),
    ("selection.all", "Каталог · Вся страница", "✅ Вся страница"),
    ("selection.clear", "Каталог · Сбросить", "🧹 Сбросить"),
    ("order.pay", "Заказ · Оплатить счётом", "⭐️ Оплатить счётом · {amount}"),
    ("order.paybalance", "Заказ · Списать с баланса", "💼 Списать с баланса ({amount} ⭐️)"),
    ("order.target", "Заказ · Куда добавить", "📦 Куда добавить"),
    ("after.open", "Готово · Открыть набор", "📦 Открыть набор"),
    ("after.more", "Готово · Собрать ещё", "✨ Собрать ещё"),
]

BUTTON_TITLES = {key: title for key, title, _ in EDITABLE_BUTTONS}
BUTTON_DEFAULTS = {key: default for key, _, default in EDITABLE_BUTTONS}


def button_config(key: str) -> Dict[str, Any]:
    """Текущие настройки одной кнопки в виде словаря."""
    raw = (_data.get("buttons") or {}).get(key)
    if isinstance(raw, str):
        return {"text": raw}
    return dict(raw) if isinstance(raw, dict) else {}


def set_button(key: str, field: str, value: Optional[str]) -> None:
    """Меняет одно поле кнопки. None удаляет поле, пустая настройка — саму кнопку."""
    buttons = _data.setdefault("buttons", {})
    current = button_config(key)
    if value is None:
        current.pop(field, None)
    else:
        current[field] = value
    if current:
        buttons[key] = current
    else:
        buttons.pop(key, None)
    save()


def set_menu_text(value: Optional[str]) -> None:
    if value is None:
        _data.pop("menu", None)
    else:
        _data["menu"] = value
    save()


def menu_text_raw() -> Optional[str]:
    raw = _data.get("menu")
    return raw if isinstance(raw, str) else None


def set_media(kind: Optional[str], path: Optional[str]) -> None:
    """Ставит или снимает медиа над меню. Путь хранится относительный."""
    if kind is None or path is None:
        _data.pop("menu_media", None)
    else:
        relative = os.path.relpath(path, config.BASE_DIR) if path.startswith(config.BASE_DIR) else path
        _data["menu_media"] = {"type": kind, "file": relative}
    save()


def reset_all() -> None:
    """Возвращает оформление к тому, что зашито в коде."""
    global _data
    _data = {}
    save()


def customized_count() -> int:
    return len(_data.get("buttons") or {})
