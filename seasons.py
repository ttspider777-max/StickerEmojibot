"""Сезоны активности: 5 сезонов по 30 дней, победители и рассылка итогов.

Сезон запускает админ кнопкой в панели — через 30 дней он закрывается сам:
считаются места, топ-N получают приз, всем пользователям уходит сообщение
с итогами и их личным результатом.

Откуда берутся очки:
  * сообщения в чате сезона и в чате обсуждения канала (+1, кулдаун, дневной лимит);
  * реакции на сообщения в чате (+1, дневной лимит; бот должен быть админом чата);
  * действия в боте: оплаченный заказ и приглашённый друг;
  * разовый бонус за подписку на обязательные каналы.

Бот не видит просмотры и реакции в самом канале — это ограничение Telegram.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import aiosqlite
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageReactionUpdated

import config
import db

log = logging.getLogger("emoji-bot.seasons")

TOTAL_SEASONS = 5
SEASON_DAYS = 30

#: Очки и ограничения, чтобы нельзя было накрутить флудом.
POINTS_MESSAGE = 1
POINTS_REACTION = 1
POINTS_REFERRAL = 10
POINTS_SUBSCRIBE = 20
POINTS_ORDER_BASE = 5
POINTS_ORDER_PER_EMOJI = 1
POINTS_ORDER_MAX = 40
MESSAGE_COOLDOWN = 20          # секунд между засчитанными сообщениями
MESSAGE_DAILY_CAP = 60         # очков за сообщения в сутки
REACTION_DAILY_CAP = 20
MIN_TEXT = 3                   # короче — не считаем (флуд «+», «ок»)

#: Призы: топ-N получают генерации, первое место — ещё и NFT-подарок (вручную).
PRIZE_WINNERS = int(os.getenv("SEASON_WINNERS", "3"))
PRIZE_GENERATIONS = int(os.getenv("SEASON_GENS", "300"))
NFT_PLACES = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seasons (
    num         INTEGER PRIMARY KEY,
    status      TEXT NOT NULL,
    started_at  INTEGER NOT NULL,
    ends_at     INTEGER NOT NULL,
    finished_at INTEGER NOT NULL DEFAULT 0,
    results     TEXT
);
CREATE TABLE IF NOT EXISTS season_points (
    season      INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    name        TEXT,
    msgs        INTEGER NOT NULL DEFAULT 0,
    reacts      INTEGER NOT NULL DEFAULT 0,
    actions     INTEGER NOT NULL DEFAULT 0,
    bonus       INTEGER NOT NULL DEFAULT 0,
    sub_claimed INTEGER NOT NULL DEFAULT 0,
    day         TEXT,
    day_msgs    INTEGER NOT NULL DEFAULT 0,
    day_reacts  INTEGER NOT NULL DEFAULT 0,
    updated_at  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (season, user_id)
);
"""

_KINDS = {"msgs", "reacts", "actions", "bonus"}

#: Кэш активного сезона: сообщения идут потоком, в базу за ним не ходим.
_active: Optional[Dict[str, Any]] = None
_chats: List[int] = []
_last_msg: Dict[int, float] = {}
_known_users: set = set()
_finish_lock = asyncio.Lock()


def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


# --------------------------------------------------------------------------
# NFT-приз: админ вставляет название/ссылку, выдаёт его сам
# --------------------------------------------------------------------------

_URL = re.compile(r"(https?://\S+|t\.me/\S+)", re.I)


def parse_nft(raw: str) -> Dict[str, str]:
    """Из строки админа достаёт ссылку и название: «Plush Pepe t.me/nft/PlushPepe-1»."""
    raw = (raw or "").strip()
    match = _URL.search(raw)
    url = match.group(1) if match else ""
    if url and not url.lower().startswith("http"):
        url = "https://" + url
    title = _URL.sub("", raw).strip(" -–—:") or ("NFT-подарок" if url else "")
    return {"title": title, "url": url}


def nft_html(raw: str) -> str:
    """NFT для сообщений бота: ссылка, если она есть, иначе просто название."""
    info = parse_nft(raw)
    if not info["title"]:
        return "NFT-подарок в Telegram"
    if info["url"]:
        return f'NFT-подарок «<a href="{_esc(info["url"])}">{_esc(info["title"])}</a>»'
    return f"NFT-подарок «{_esc(info['title'])}»"


async def get_nft(num: int) -> str:
    return (await db.get_settings()).get(f"season_nft_{num}", "")


async def set_nft(num: int, raw: str) -> None:
    await db.set_setting(f"season_nft_{num}", (raw or "").strip()[:200])


async def target_season() -> int:
    """Сезон, для которого сейчас задаётся NFT: идущий или ближайший следующий."""
    return int(_active["num"]) if _active else min(await next_number(), TOTAL_SEASONS)


async def mark_given(bot: Bot, num: int) -> Dict[str, Any]:
    """Админ нажал «Выдал»: запоминаем и сообщаем победителю."""
    settings_ = await db.get_settings()
    if settings_.get(f"season_nft_given_{num}") == "1":
        return {"ok": False, "error": "Уже отмечено"}
    seasons_ = {int(x["num"]): x for x in await all_seasons()}
    row = seasons_.get(num)
    winner = None
    if row and row.get("results"):
        try:
            winner = next((w for w in json.loads(row["results"]).get("winners", []) if w["place"] == 1), None)
        except ValueError:
            winner = None
    await db.set_setting(f"season_nft_given_{num}", "1")
    if winner:
        await _send(bot, int(winner["user_id"]),
                    f"🎁 Администратор отправил тебе приз сезона {num}: {nft_html(settings_.get(f'season_nft_{num}', ''))}.\n"
                    "Проверь входящие подарки в Telegram!")
    return {"ok": True, "winner": winner}


# --------------------------------------------------------------------------
# Старт и состояние
# --------------------------------------------------------------------------

async def init() -> None:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.executescript(_SCHEMA)
        await conn.commit()
    await refresh()


async def refresh() -> None:
    """Перечитывает активный сезон и список чатов из базы."""
    global _active, _chats
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM seasons WHERE status = 'active' LIMIT 1") as cur:
            row = await cur.fetchone()
    _active = dict(row) if row else None
    stored = (await db.get_settings()).get("season_chats", "")
    _chats = [int(x) for x in stored.split(",") if x.strip().lstrip("-").isdigit()]


def active() -> Optional[Dict[str, Any]]:
    if _active and time.time() < int(_active["ends_at"]):
        return _active
    return None


def chats() -> List[int]:
    return list(_chats)


async def add_chat(chat_id: int) -> None:
    if chat_id not in _chats:
        _chats.append(chat_id)
        await db.set_setting("season_chats", ",".join(str(c) for c in _chats))


async def remove_chat(chat_id: int) -> None:
    if chat_id in _chats:
        _chats.remove(chat_id)
        await db.set_setting("season_chats", ",".join(str(c) for c in _chats))


async def all_seasons() -> List[Dict[str, Any]]:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM seasons ORDER BY num") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def next_number() -> int:
    """Номер следующего сезона; больше TOTAL_SEASONS — значит, все сыграны."""
    seasons = await all_seasons()
    return (max((int(s["num"]) for s in seasons), default=0)) + 1


async def start_next() -> Dict[str, Any]:
    """Запускает следующий сезон на 30 дней. Возвращает {ok, error | season}."""
    global _active
    if active():
        return {"ok": False, "error": f"Сезон {_active['num']} уже идёт"}
    num = await next_number()
    if num > TOTAL_SEASONS:
        return {"ok": False, "error": f"Все {TOTAL_SEASONS} сезонов уже сыграны"}
    now = int(time.time())
    row = {"num": num, "status": "active", "started_at": now, "ends_at": now + SEASON_DAYS * 86400}
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO seasons (num, status, started_at, ends_at) VALUES (?, 'active', ?, ?)",
            (num, row["started_at"], row["ends_at"]),
        )
        await conn.commit()
    _active = row
    _last_msg.clear()
    return {"ok": True, "season": row}


# --------------------------------------------------------------------------
# Начисление очков
# --------------------------------------------------------------------------

async def add(user_id: int, kind: str, amount: int, name: Optional[str] = None) -> int:
    """Добавляет очки в активный сезон. Возвращает сколько реально начислено."""
    season = active()
    if not season or kind not in _KINDS or amount <= 0:
        return 0
    num, today, now = int(season["num"]), time.strftime("%Y-%m-%d"), int(time.time())
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO season_points (season, user_id, name, day) VALUES (?, ?, ?, ?)",
            (num, user_id, name, today),
        )
        async with conn.execute(
            "SELECT day, day_msgs, day_reacts FROM season_points WHERE season = ? AND user_id = ?",
            (num, user_id),
        ) as cur:
            day, day_msgs, day_reacts = await cur.fetchone()
        if day != today:
            day_msgs = day_reacts = 0
        if kind == "msgs":
            amount = min(amount, MESSAGE_DAILY_CAP - day_msgs)
            day_msgs += max(amount, 0)
        elif kind == "reacts":
            amount = min(amount, REACTION_DAILY_CAP - day_reacts)
            day_reacts += max(amount, 0)
        if amount <= 0:
            return 0
        await conn.execute(
            f"UPDATE season_points SET {kind} = {kind} + ?, day = ?, day_msgs = ?, day_reacts = ?, "
            "updated_at = ?, name = COALESCE(?, name) WHERE season = ? AND user_id = ?",
            (amount, today, day_msgs, day_reacts, now, name, num, user_id),
        )
        await conn.commit()
    return amount


async def on_action(user_id: int, points: int) -> None:
    """Очки за действия в боте: заказ, приглашённый друг. Никогда не падает."""
    try:
        await add(user_id, "actions", points)
    except Exception:
        log.exception("Сезон: не удалось начислить очки за действие")


def order_points(count: int) -> int:
    return min(POINTS_ORDER_MAX, POINTS_ORDER_BASE + POINTS_ORDER_PER_EMOJI * max(count, 0))


async def _remember(user: Any) -> None:
    """Участник чата мог ни разу не запускать бота — заводим профиль,
    чтобы приз (кристаллы) было куда начислить."""
    if user.id in _known_users:
        return
    _known_users.add(user.id)
    try:
        await db.ensure_user(user.id, user.username, user.first_name)
    except Exception:
        log.exception("Сезон: не удалось завести профиль участника")


def _name_of(user: Any) -> str:
    return (user.first_name or user.username or "Участник")[:40]


async def claim_subscription(user_id: int, subscribed: bool) -> Dict[str, Any]:
    season = active()
    if not season:
        return {"ok": False, "error": "Сезон сейчас не идёт"}
    num = int(season["num"])
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO season_points (season, user_id, day) VALUES (?, ?, ?)",
            (num, user_id, time.strftime("%Y-%m-%d")),
        )
        async with conn.execute(
            "SELECT sub_claimed FROM season_points WHERE season = ? AND user_id = ?", (num, user_id),
        ) as cur:
            (claimed,) = await cur.fetchone()
        if claimed:
            return {"ok": False, "error": "Бонус за подписку уже получен"}
        if not subscribed:
            return {"ok": False, "error": "Сначала подпишитесь на каналы"}
        await conn.execute(
            "UPDATE season_points SET bonus = bonus + ?, sub_claimed = 1, updated_at = ? "
            "WHERE season = ? AND user_id = ?",
            (POINTS_SUBSCRIBE, int(time.time()), num, user_id),
        )
        await conn.commit()
    return {"ok": True, "points": POINTS_SUBSCRIBE}


# --------------------------------------------------------------------------
# Счётчики активности в чатах (подключаются первым роутером)
# --------------------------------------------------------------------------

router = Router(name="seasons")


@router.message.outer_middleware()
async def _count_messages(handler, message: Message, data: Dict[str, Any]):
    """Считает сообщения и всегда пропускает событие дальше — другие
    обработчики не должны замечать, что их кто-то подсматривает."""
    try:
        season = active()
        user = message.from_user
        if (season and user and not user.is_bot and message.chat.id in _chats
                and not message.is_automatic_forward):
            text = (message.text or message.caption or "").strip()
            media = bool(message.photo or message.video or message.sticker or message.voice
                         or message.animation or message.video_note or message.document)
            if not text.startswith("/") and (len(text) >= MIN_TEXT or media):
                now = time.time()
                if now - _last_msg.get(user.id, 0) >= MESSAGE_COOLDOWN:
                    _last_msg[user.id] = now
                    await _remember(user)
                    await add(user.id, "msgs", POINTS_MESSAGE, _name_of(user))
    except Exception:
        log.exception("Сезон: ошибка подсчёта сообщения")
    return await handler(message, data)


@router.message_reaction()
async def on_reaction(event: MessageReactionUpdated) -> None:
    """Реакция на сообщение в чате сезона. Нужны права админа у бота."""
    try:
        user = event.user
        if not active() or not user or user.is_bot or event.chat.id not in _chats:
            return
        if len(event.new_reaction) > len(event.old_reaction):
            await _remember(user)
            await add(user.id, "reacts", POINTS_REACTION, _name_of(user))
    except Exception:
        log.exception("Сезон: ошибка подсчёта реакции")


# --------------------------------------------------------------------------
# Таблицы лидеров
# --------------------------------------------------------------------------

_TOTAL = "(sp.msgs + sp.reacts + sp.actions + sp.bonus)"


async def standings(season: int, limit: int = 0) -> List[Dict[str, Any]]:
    """Все участники сезона по убыванию очков (при равенстве — кто раньше набрал)."""
    sql = (
        f"SELECT sp.user_id, COALESCE(NULLIF(u.first_name, ''), sp.name, u.username, 'Участник') AS name, "
        f"u.username AS username, sp.msgs, sp.reacts, sp.actions, sp.bonus, {_TOTAL} AS points "
        "FROM season_points sp LEFT JOIN users u ON u.user_id = sp.user_id "
        f"WHERE sp.season = ? AND {_TOTAL} > 0 ORDER BY points DESC, sp.updated_at ASC, sp.user_id ASC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(sql, (season,)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    for place, row in enumerate(rows, 1):
        row["place"] = place
    return rows


async def snapshot(user_id: int, has_channels: bool) -> Dict[str, Any]:
    """Всё, что нужно экрану «Сезон» мини-приложения, одним ответом."""
    seasons = await all_seasons()
    season = active()
    shown = int(season["num"]) if season else None
    out: Dict[str, Any] = {
        "total": TOTAL_SEASONS, "days": SEASON_DAYS,
        "active": None, "next_num": None, "me": None, "top": [],
        "seasons": [], "chats": bool(_chats),
        "prizes": {"winners": PRIZE_WINNERS, "generations": PRIZE_GENERATIONS, "nft_places": NFT_PLACES},
        "rules": {
            "message": POINTS_MESSAGE, "cooldown": MESSAGE_COOLDOWN, "message_cap": MESSAGE_DAILY_CAP,
            "reaction": POINTS_REACTION, "reaction_cap": REACTION_DAILY_CAP,
            "order_base": POINTS_ORDER_BASE, "order_per": POINTS_ORDER_PER_EMOJI, "order_max": POINTS_ORDER_MAX,
            "referral": POINTS_REFERRAL, "subscribe": POINTS_SUBSCRIBE,
        },
    }
    nft_raw = await get_nft(shown if season else (await next_number()))
    out["prizes"]["nft"] = parse_nft(nft_raw)
    for s in seasons:
        item: Dict[str, Any] = {"num": int(s["num"]), "status": s["status"],
                                "started_at": int(s["started_at"]), "ends_at": int(s["ends_at"])}
        if s["status"] == "finished" and s.get("results"):
            try:
                item["winners"] = json.loads(s["results"]).get("winners", [])
            except ValueError:
                item["winners"] = []
        out["seasons"].append(item)
    if season:
        out["active"] = {"num": shown, "started_at": int(season["started_at"]), "ends_at": int(season["ends_at"]),
                         "left": max(0, int(season["ends_at"]) - int(time.time()))}
        rows = await standings(shown)
        out["participants"] = len(rows)
        out["top"] = [
            {"place": r["place"], "name": r["name"], "points": int(r["points"]), "me": int(r["user_id"]) == user_id}
            for r in rows[:10]
        ]
        mine = next((r for r in rows if int(r["user_id"]) == user_id), None)
        claimed = 0
        async with aiosqlite.connect(config.DB_PATH) as conn:
            async with conn.execute(
                "SELECT sub_claimed FROM season_points WHERE season = ? AND user_id = ?", (shown, user_id),
            ) as cur:
                row = await cur.fetchone()
                claimed = int(row[0]) if row else 0
        gap = None
        if mine and mine["place"] > 1:
            gap = int(rows[mine["place"] - 2]["points"]) - int(mine["points"]) + 1
        out["me"] = {
            "points": int(mine["points"]) if mine else 0, "place": mine["place"] if mine else None,
            "msgs": int(mine["msgs"]) if mine else 0, "reacts": int(mine["reacts"]) if mine else 0,
            "actions": int(mine["actions"]) if mine else 0, "bonus": int(mine["bonus"]) if mine else 0,
            "gap": gap, "sub_claimed": bool(claimed), "sub_available": has_channels and not claimed,
        }
    else:
        nxt = await next_number()
        out["next_num"] = nxt if nxt <= TOTAL_SEASONS else None
    return out


# --------------------------------------------------------------------------
# Завершение сезона
# --------------------------------------------------------------------------

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _summary(num: int, rows: List[Dict[str, Any]], crystals: int, nft: str = "") -> str:
    lines = [f"🏆 <b>Сезон {num} из {TOTAL_SEASONS} завершён!</b>", ""]
    if rows:
        lines.append("<b>Лучшие участники:</b>")
        for r in rows[:10]:
            medal = MEDALS.get(r["place"], f"{r['place']}.")
            lines.append(f"{medal} {_esc(r['name'])} — <b>{int(r['points'])}</b> очк.")
        lines += ["", "🎁 <b>Призы</b>",
                  f"🥇 {nft_html(nft)} + {PRIZE_GENERATIONS} генераций"]
        if PRIZE_WINNERS == 2:
            lines.append(f"🥈 {PRIZE_GENERATIONS} генераций")
        elif PRIZE_WINNERS > 2:
            lines.append(f"🥈 2–{PRIZE_WINNERS} места — по {PRIZE_GENERATIONS} генераций")
        lines.append(f"<i>Генерации уже начислены кристаллами ({crystals} 💎 каждому призёру).</i>")
    else:
        lines.append("В этом сезоне никто не набрал очков — следующий будет активнее!")
    return "\n".join(lines)


async def _send(bot: Bot, chat_id: int, text: str, markup: Any = None) -> bool:
    for _ in range(2):
        try:
            await bot.send_message(chat_id, text, reply_markup=markup)
            return True
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
        except Exception:
            return False
    return False


async def finish(bot: Bot, num: Optional[int] = None) -> Dict[str, Any]:
    """Закрывает сезон: места, призы, рассылка. Безопасно вызывать повторно."""
    global _active
    async with _finish_lock:
        season = _active if num is None else next((s for s in await all_seasons() if int(s["num"]) == num), None)
        if not season or season["status"] != "active":
            return {"ok": False, "error": "Активного сезона нет"}
        num = int(season["num"])
        rows = await standings(num)
        winners = rows[:PRIZE_WINNERS]
        crystals = PRIZE_GENERATIONS * config.CRYSTALS_PER_EMOJI
        results = json.dumps({
            "winners": [{"place": w["place"], "user_id": int(w["user_id"]), "name": w["name"],
                         "points": int(w["points"])} for w in winners],
            "participants": len(rows),
        }, ensure_ascii=False)
        async with aiosqlite.connect(config.DB_PATH) as conn:
            cur = await conn.execute(
                "UPDATE seasons SET status = 'finished', finished_at = ?, results = ? "
                "WHERE num = ? AND status = 'active'", (int(time.time()), results, num),
            )
            await conn.commit()
            if cur.rowcount == 0:
                return {"ok": False, "error": "Сезон уже закрыт"}
        _active = None
        _last_msg.clear()

    for w in winners:
        try:
            await db.add_crystals(int(w["user_id"]), crystals)
        except Exception:
            log.exception("Сезон %s: не начислен приз %s", num, w["user_id"])

    asyncio.create_task(_announce(bot, num, rows, winners, crystals))
    return {"ok": True, "num": num, "winners": winners, "participants": len(rows)}


async def _announce(bot: Bot, num: int, rows: List[Dict[str, Any]], winners: List[Dict[str, Any]],
                    crystals: int) -> None:
    """Рассылка итогов: общий топ + личная строка каждому, отдельно — призёрам и админам."""
    nft = await get_nft(num)
    base = _summary(num, rows, crystals, nft)
    place_of = {int(r["user_id"]): r for r in rows}
    left = TOTAL_SEASONS - num
    tail = (f"\n\n🔜 Впереди ещё сезонов: <b>{left}</b>. Будьте активны в чате и канале!"
            if left > 0 else "\n\n🎉 Это был последний сезон. Спасибо, что были с нами!")
    try:
        ids = set(await db.all_user_ids()) - set(await db.blocked_ids())
    except Exception:
        ids = set()
    sent = 0
    for uid in ids:
        mine = place_of.get(uid)
        if mine:
            extra = f"\n\n👤 <b>Твой результат:</b> {mine['place']} место, {int(mine['points'])} очк."
            if mine["place"] <= PRIZE_WINNERS:
                extra += f"\n💎 Приз начислен: +{crystals} кристаллов ({PRIZE_GENERATIONS} генераций)."
                if mine["place"] <= NFT_PLACES:
                    extra += "\n🎁 NFT-подарок тебе отправит администратор лично — жди сообщение."
        else:
            extra = "\n\n👤 Ты не набрал очков в этом сезоне — в следующем всё впереди!"
        if await _send(bot, uid, base + extra + tail):
            sent += 1
        await asyncio.sleep(0.05)
    for chat in chats():
        await _send(bot, chat, base + tail)

    top = [w for w in winners if w["place"] <= NFT_PLACES]
    note = [f"✅ Сезон {num} закрыт. Рассылка: {sent}/{len(ids)}. Призёров: {len(winners)}."]
    for w in top:
        note.append(
            f"\n🎁 <b>Выдай NFT сам:</b> {nft_html(nft) if nft else 'NFT не задан — выбери любой подарок'}\n"
            f"Победитель: <a href=\"tg://user?id={int(w['user_id'])}\">{_esc(w['name'])}</a> "
            f"(<code>{int(w['user_id'])}</code>) — {int(w['points'])} очк.\n"
            "Отправь подарок и нажми кнопку — победитель получит уведомление."
        )
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Я выдал NFT", callback_data=f"adm:nftgiven:{num}"),
    ]]) if top else None
    for admin_id in config.ADMIN_IDS:
        await _send(bot, admin_id, "\n".join(note), markup)


async def worker(bot: Bot) -> None:
    """Раз в полминуты проверяет, не пора ли закрыть сезон."""
    while True:
        try:
            if _active and time.time() >= int(_active["ends_at"]):
                await finish(bot)
        except Exception:
            log.exception("Сезон: ошибка в воркере")
        await asyncio.sleep(30)
