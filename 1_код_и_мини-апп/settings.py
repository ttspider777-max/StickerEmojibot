"""Настройки, которые админ меняет на ходу, без правки .env и рестарта.

Значения лежат в базе, а в памяти держится их копия: цена спрашивается
на каждом экране заказа, и ходить за ней в SQLite по каждому нажатию
незачем. Копия обновляется при старте и при каждой записи.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import config
import db

#: Ключ → значение по умолчанию. Оно же задаёт тип при чтении из базы.
DEFAULTS: Dict[str, Any] = {
    "price": config.PRICE_PER_EMOJI,
    "maintenance": False,
    "subscription": True,
    "split_packs": False,
}

_cache: Dict[str, Any] = dict(DEFAULTS)


async def load() -> None:
    """Поднимает сохранённые значения при запуске бота."""
    stored = await db.get_settings()
    for key, default in DEFAULTS.items():
        raw = stored.get(key)
        if raw is None:
            continue
        _cache[key] = raw == "1" if isinstance(default, bool) else type(default)(raw)


def price() -> int:
    return int(_cache["price"])


def maintenance() -> bool:
    return bool(_cache["maintenance"])


async def set_price(value: int) -> None:
    _cache["price"] = int(value)
    await db.set_setting("price", str(int(value)))


def split_packs() -> bool:
    """Бить большой заказ на наборы по 50 штук.

    Telegram пускает в наборы 8 обращений за 4 минуты, а
    createNewStickerSet забирает до 50 стикеров одним вызовом. Поэтому
    200 эмодзи одним набором — это 151 обращение и больше часа, а
    четырьмя наборами по 50 — четыре обращения и минута.
    """
    return bool(_cache.get("split_packs", False))


async def set_split_packs(value: bool) -> None:
    _cache["split_packs"] = bool(value)
    await db.set_setting("split_packs", "1" if value else "0")


async def set_maintenance(value: bool) -> None:
    _cache["maintenance"] = bool(value)
    await db.set_setting("maintenance", "1" if value else "0")


# --------------------------------------------------------------------------
# Обязательная подписка
# --------------------------------------------------------------------------

#: Каналы, на которые нужно подписаться. Каждый — словарь
#: {"id": -100…, "title": "…", "url": "https://t.me/…"}.
_channels: List[Dict[str, Any]] = []


def normalize_channel(value: Any) -> Any:
    """Приводит канал к тому, что понимает getChatMember.

    В .env легко положить ссылку вида https://t.me/name — Telegram на
    неё отвечает «chat not found», и проверка подписки молча
    выключается. Вытаскиваем из ссылки @имя, числовой id оставляем как
    есть.
    """
    raw = str(value).strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if raw.startswith(prefix):
            tail = raw[len(prefix):].strip("/").split("/")[0]
            return raw if tail.startswith("+") else "@" + tail
    return value


async def load_channels() -> None:
    """Поднимает список каналов при старте.

    Если в базе пусто, а в .env указан REQUIRED_CHANNEL — переносим его
    в базу. Так старые установки продолжают работать, а дальше список
    живёт в админке.
    """
    global _channels
    raw = (await db.get_settings()).get("channels")
    if raw:
        try:
            stored = json.loads(raw)
        except Exception:
            stored = []
        if stored:
            # Чиним записи, сохранённые ссылкой: с ней проверка не работала.
            fixed = False
            for channel in stored:
                clean = normalize_channel(channel.get("id"))
                if clean != channel.get("id"):
                    channel["id"], fixed = clean, True
            _channels = stored
            if fixed:
                await _save_channels()
            return

    if config.REQUIRED_CHANNEL:
        _channels = [{
            "id": normalize_channel(config.REQUIRED_CHANNEL),
            "title": config.REQUIRED_CHANNEL,
            "url": config.REQUIRED_CHANNEL_URL,
        }]
        await _save_channels()


async def _save_channels() -> None:
    await db.set_setting("channels", json.dumps(_channels, ensure_ascii=False))


def channels() -> List[Dict[str, Any]]:
    return list(_channels)


def subscription_on() -> bool:
    """Проверка включена, только если есть хотя бы один канал."""
    return bool(_channels) and bool(_cache.get("subscription", True))


async def set_subscription(enabled: bool) -> None:
    _cache["subscription"] = bool(enabled)
    await db.set_setting("subscription", "1" if enabled else "0")


async def add_channel(chat_id: Any, title: str, url: str) -> bool:
    """Добавляет канал. False — такой уже есть."""
    if any(str(c["id"]) == str(chat_id) for c in _channels):
        return False
    _channels.append({"id": chat_id, "title": title, "url": url})
    await _save_channels()
    return True


async def remove_channel(chat_id: Any) -> None:
    global _channels
    _channels = [c for c in _channels if str(c["id"]) != str(chat_id)]
    await _save_channels()
