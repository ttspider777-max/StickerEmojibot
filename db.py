"""Хранилище бота. SQLite через aiosqlite — без внешних сервисов.

В базе лежит то, что должно пережить перезапуск: профили, наборы и
заказы. Заказ сохраняется до выставления счёта — иначе оплата, дошедшая
после рестарта, не нашла бы, что именно человек купил.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import aiosqlite

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY,
    username      TEXT,
    first_name    TEXT,
    created_at    INTEGER NOT NULL,
    last_seen_at  INTEGER NOT NULL,
    emoji_created INTEGER NOT NULL DEFAULT 0,
    stars_spent   INTEGER NOT NULL DEFAULT 0,
    balance       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS packs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL UNIQUE,
    title      TEXT NOT NULL,
    count      INTEGER NOT NULL DEFAULT 0,
    kind       TEXT NOT NULL DEFAULT 'emoji',
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_packs_user ON packs(user_id);

CREATE TABLE IF NOT EXISTS orders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    pack_id    TEXT NOT NULL,
    font_id    TEXT NOT NULL,
    font_path  TEXT,
    text       TEXT NOT NULL DEFAULT '',
    logo       BLOB,
    numbers    TEXT NOT NULL,
    amount     INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'new',
    charge_id   TEXT,
    target_pack TEXT,
    paid_from   TEXT,
    created_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crypto_invoices (
    invoice_id INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    chat_id    INTEGER NOT NULL,
    stars      INTEGER NOT NULL,
    amount     REAL NOT NULL,
    asset      TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS referrals (
    invited_id INTEGER PRIMARY KEY,
    inviter_id INTEGER NOT NULL,
    rewarded   INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_referrals_inviter ON referrals(inviter_id);

CREATE TABLE IF NOT EXISTS promo_codes (
    code       TEXT PRIMARY KEY,
    percent    INTEGER NOT NULL,
    max_uses   INTEGER NOT NULL DEFAULT 0,
    used       INTEGER NOT NULL DEFAULT 0,
    expires_at INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS user_promos (
    user_id INTEGER PRIMARY KEY,
    code    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS promo_uses (
    code    TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (code, user_id)
);

CREATE TABLE IF NOT EXISTS user_templates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id   INTEGER NOT NULL,
    title      TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    hidden     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS template_stats (
    number    INTEGER PRIMARY KEY,
    downloads INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS blocked (
    user_id    INTEGER PRIMARY KEY,
    reason     TEXT,
    created_at INTEGER NOT NULL
);
"""


#: Колонки, добавленные после первого релиза. CREATE TABLE IF NOT EXISTS
#: их в уже созданную базу не принесёт, поэтому досыпаем вручную.
_MIGRATIONS = (
    ("users", "balance", "INTEGER NOT NULL DEFAULT 0"),
    ("packs", "kind", "TEXT NOT NULL DEFAULT 'emoji'"),
    ("orders", "kind", "TEXT NOT NULL DEFAULT 'emoji'"),
    ("orders", "target_pack", "TEXT"),
    ("orders", "paid_from", "TEXT"),
    # Очередь выдачи: чтобы оплаченный заказ пережил перезапуск бота.
    ("orders", "chat_id", "INTEGER"),
    ("orders", "attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("orders", "next_try_at", "INTEGER NOT NULL DEFAULT 0"),
    ("orders", "paid_at", "INTEGER NOT NULL DEFAULT 0"),
    # Мини-приложение: кристаллы, премиум и промокод заказа.
    ("users", "crystals", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "premium_until", "INTEGER NOT NULL DEFAULT 0"),
    ("orders", "crystals", "INTEGER NOT NULL DEFAULT 0"),
    ("orders", "promo", "TEXT"),
)


async def init() -> None:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.executescript(_SCHEMA)
        for table, column, decl in _MIGRATIONS:
            async with conn.execute(f"PRAGMA table_info({table})") as cur:
                columns = {row[1] for row in await cur.fetchall()}
            if column not in columns:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        await conn.commit()


# --------------------------------------------------------------------------
# Пользователи
# --------------------------------------------------------------------------

async def ensure_user(user_id: int, username: Optional[str], first_name: Optional[str]) -> bool:
    """Заводит профиль при первом /start и обновляет ник при каждом входе.

    Ник в Telegram меняется, а в профиле он показывается — иначе бот
    рисовал бы устаревшее имя месяцами.
    """
    now = int(time.time())
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)) as cur:
            is_new = await cur.fetchone() is None
        await conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_seen_at = excluded.last_seen_at
            """,
            (user_id, username, first_name, now, now),
        )
        await conn.commit()
    return is_new


async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def add_emoji_counter(user_id: int, count: int, stars: int) -> None:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "UPDATE users SET emoji_created = emoji_created + ?, stars_spent = stars_spent + ? "
            "WHERE user_id = ?",
            (count, stars, user_id),
        )
        await conn.commit()


# --------------------------------------------------------------------------
# Наборы
# --------------------------------------------------------------------------

async def add_pack(user_id: int, name: str, title: str, kind: str = "emoji") -> None:
    now = int(time.time())
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO packs (user_id, name, title, count, kind, created_at) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (user_id, name, title, kind, now),
        )
        await conn.commit()


async def bump_pack(name: str, count: int = 1) -> None:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute("UPDATE packs SET count = count + ? WHERE name = ?", (count, name))
        await conn.commit()


async def get_packs(user_id: int) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM packs WHERE user_id = ? ORDER BY id",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_last_pack(user_id: int) -> Optional[Dict[str, Any]]:
    """Последний набор пользователя — в него доклеиваются новые эмодзи."""
    packs = await get_packs(user_id)
    return packs[-1] if packs else None


# --------------------------------------------------------------------------
# Заказы
# --------------------------------------------------------------------------

async def create_order(
    user_id: int,
    pack_id: str,
    font_id: str,
    font_path: Optional[str],
    text: str,
    logo: Optional[bytes],
    numbers: List[int],
    amount: int,
    kind: str = "emoji",
    promo: Optional[str] = None,
) -> int:
    """Сохраняет заказ перед счётом и возвращает его id.

    Id уходит в payload счёта: по нему оплата находит свой заказ даже
    после перезапуска бота, когда FSM-сессия уже потеряна.
    """
    now = int(time.time())
    async with aiosqlite.connect(config.DB_PATH) as conn:
        cur = await conn.execute(
            """
            INSERT INTO orders (user_id, pack_id, font_id, font_path, text, logo,
                                numbers, amount, kind, promo, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
            """,
            (
                user_id, pack_id, font_id, font_path, text, logo,
                ",".join(str(n) for n in numbers), amount, kind, promo, now,
            ),
        )
        await conn.commit()
        return int(cur.lastrowid)


async def get_order(order_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    order = dict(row)
    order["numbers"] = [int(x) for x in order["numbers"].split(",") if x]
    return order


async def set_order_status(order_id: int, status: str, charge_id: Optional[str] = None) -> None:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "UPDATE orders SET status = ?, charge_id = COALESCE(?, charge_id) WHERE id = ?",
            (status, charge_id, order_id),
        )
        await conn.commit()


async def last_paid_order(user_id: int) -> Optional[Dict[str, Any]]:
    """Последняя оплата пользователя — по ней админ делает возврат."""
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM orders WHERE user_id = ? AND charge_id IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def stats() -> Dict[str, int]:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(emoji_created), 0), COALESCE(SUM(stars_spent), 0) FROM users"
        ) as cur:
            users, emoji, stars = await cur.fetchone()
        async with conn.execute("SELECT COUNT(*) FROM packs") as cur:
            (packs,) = await cur.fetchone()
        async with conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'done'") as cur:
            (orders,) = await cur.fetchone()
    return {
        "users": int(users),
        "emoji": int(emoji),
        "stars": int(stars),
        "packs": int(packs),
        "orders": int(orders),
    }


# --------------------------------------------------------------------------
# Настройки, меняемые из админки
# --------------------------------------------------------------------------

async def get_settings() -> Dict[str, str]:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute("SELECT key, value FROM settings") as cur:
            rows = await cur.fetchall()
    return {str(k): str(v) for k, v in rows}


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await conn.commit()


# --------------------------------------------------------------------------
# Блокировки
# --------------------------------------------------------------------------

async def block_user(user_id: int, reason: str = "") -> None:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO blocked (user_id, reason, created_at) VALUES (?, ?, ?)",
            (user_id, reason, int(time.time())),
        )
        await conn.commit()


async def unblock_user(user_id: int) -> None:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute("DELETE FROM blocked WHERE user_id = ?", (user_id,))
        await conn.commit()


async def is_blocked(user_id: int) -> bool:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute("SELECT 1 FROM blocked WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone() is not None


async def blocked_ids() -> List[int]:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute("SELECT user_id FROM blocked") as cur:
            return [int(r[0]) for r in await cur.fetchall()]


# --------------------------------------------------------------------------
# Выборки для админки
# --------------------------------------------------------------------------

async def list_users(limit: int, offset: int) -> List[Dict[str, Any]]:
    """Пользователи от новых к старым — так админ видит свежий приток."""
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC, user_id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def count_users() -> int:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute("SELECT COUNT(*) FROM users") as cur:
            (count,) = await cur.fetchone()
    return int(count)


async def find_users(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Поиск по id или части ника — то, чем пользуются в поддержке."""
    query = query.strip().lstrip("@")
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        if query.isdigit():
            sql = "SELECT * FROM users WHERE user_id = ? LIMIT ?"
            args: tuple = (int(query), limit)
        else:
            sql = ("SELECT * FROM users WHERE username LIKE ? OR first_name LIKE ? "
                   "ORDER BY created_at DESC LIMIT ?")
            args = (f"%{query}%", f"%{query}%", limit)
        async with conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def list_orders(limit: int, offset: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
    where = "WHERE status = ?" if status else ""
    args: tuple = (status, limit, offset) if status else (limit, offset)
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            f"SELECT id, user_id, font_id, text, numbers, amount, status, charge_id, created_at "
            f"FROM orders {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            args,
        ) as cur:
            rows = await cur.fetchall()
    result = []
    for row in rows:
        order = dict(row)
        order["numbers"] = [int(x) for x in order["numbers"].split(",") if x]
        result.append(order)
    return result


async def count_orders(status: Optional[str] = None) -> int:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        if status:
            sql, args = "SELECT COUNT(*) FROM orders WHERE status = ?", (status,)
        else:
            sql, args = "SELECT COUNT(*) FROM orders", ()
        async with conn.execute(sql, args) as cur:
            (count,) = await cur.fetchone()
    return int(count)


async def orders_of_user(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT id, amount, status, charge_id, numbers, created_at FROM orders "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    result = []
    for row in rows:
        order = dict(row)
        order["numbers"] = [int(x) for x in order["numbers"].split(",") if x]
        result.append(order)
    return result


async def period_stats(since: int) -> Dict[str, int]:
    """Срез за период: новые люди, оплаченные заказы и звёзды с них."""
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= ?", (since,),
        ) as cur:
            (users,) = await cur.fetchone()
        async with conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM orders "
            "WHERE status = 'done' AND created_at >= ?",
            (since,),
        ) as cur:
            orders, stars = await cur.fetchone()
    return {"users": int(users), "orders": int(orders), "stars": int(stars)}


async def all_user_ids() -> List[int]:
    """Адресаты рассылки: все, кроме заблокированных."""
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute(
            "SELECT user_id FROM users WHERE user_id NOT IN (SELECT user_id FROM blocked)"
        ) as cur:
            return [int(r[0]) for r in await cur.fetchall()]


# --------------------------------------------------------------------------
# Баланс
# --------------------------------------------------------------------------

async def add_balance(user_id: int, amount: int) -> int:
    """Меняет баланс и возвращает новое значение.

    Сумма может быть отрицательной — админ снимает выданное по ошибке.
    В минус баланс при этом не уходит: MAX(0, …) считается в самом SQL,
    иначе между чтением и записью успел бы влезть заказ.
    """
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "UPDATE users SET balance = MAX(0, balance + ?) WHERE user_id = ?",
            (amount, user_id),
        )
        await conn.commit()
        async with conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def spend_balance(user_id: int, amount: int) -> bool:
    """Списывает с баланса, если хватает. False — денег не хватило.

    Проверка и списание идут одним UPDATE с условием: между отдельными
    SELECT и UPDATE пользователь успел бы нажать «оплатить» дважды и
    увести баланс в минус.
    """
    async with aiosqlite.connect(config.DB_PATH) as conn:
        cur = await conn.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
            (amount, user_id, amount),
        )
        await conn.commit()
        return cur.rowcount > 0


async def get_balance(user_id: int) -> int:
    user = await get_user(user_id)
    return int(user.get("balance", 0)) if user else 0


# --------------------------------------------------------------------------
# Куда складывать эмодзи
# --------------------------------------------------------------------------

async def set_order_target(order_id: int, target_pack: Optional[str]) -> None:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "UPDATE orders SET target_pack = ? WHERE id = ?", (target_pack, order_id),
        )
        await conn.commit()


async def mark_order_paid(order_id: int, source: str, charge_id: Optional[str] = None) -> None:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "UPDATE orders SET status = 'paid', paid_from = ?, "
            "charge_id = COALESCE(?, charge_id) WHERE id = ?",
            (source, charge_id, order_id),
        )
        await conn.commit()


async def packs_with_room(user_id: int, limit: int, kind: str) -> List[Dict[str, Any]]:
    """Наборы нужного типа, куда ещё влезет ``limit`` штук.

    Тип обязателен: дописать эмодзи в стикерпак Telegram не даёт, и такой
    набор в списке был бы кнопкой, ведущей в ошибку.
    """
    packs = await get_packs(user_id)
    return [
        p for p in packs
        if p.get("kind", "emoji") == kind and int(p["count"]) + limit <= config.PACK_LIMIT
    ]


async def get_pack(name: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM packs WHERE name = ?", (name,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------
# Очередь выдачи
# --------------------------------------------------------------------------

async def mark_paid_for_delivery(order_id: int, source: str, chat_id: int,
                                 charge_id: Optional[str] = None) -> None:
    """Ставит заказ в очередь выдачи.

    chat_id запоминаем здесь: выдача может случиться через час и после
    перезапуска бота, когда исходного сообщения уже нет, а написать
    человеку всё равно нужно.
    """
    now = int(time.time())
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "UPDATE orders SET status = 'paid', paid_from = ?, chat_id = ?, "
            "paid_at = ?, next_try_at = 0, attempts = 0, "
            "charge_id = COALESCE(?, charge_id) WHERE id = ?",
            (source, chat_id, now, charge_id, order_id),
        )
        await conn.commit()


async def due_orders(limit: int = 5) -> List[Dict[str, Any]]:
    """Оплаченные заказы, которым пора попробовать выдаться."""
    now = int(time.time())
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM orders WHERE status = 'paid' AND next_try_at <= ? "
            "ORDER BY paid_at, id LIMIT ?",
            (now, limit),
        ) as cur:
            rows = await cur.fetchall()
    result = []
    for row in rows:
        order = dict(row)
        order["numbers"] = [int(x) for x in order["numbers"].split(",") if x]
        result.append(order)
    return result


async def postpone_order(order_id: int, seconds: int) -> None:
    """Откладывает следующую попытку выдачи и считает их количество."""
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "UPDATE orders SET next_try_at = ?, attempts = attempts + 1 WHERE id = ?",
            (int(time.time()) + max(0, seconds), order_id),
        )
        await conn.commit()


async def retry_order_now(order_id: int) -> None:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "UPDATE orders SET next_try_at = 0 WHERE id = ? AND status = 'paid'",
            (order_id,),
        )
        await conn.commit()


async def pending_orders(limit: int = 20) -> List[Dict[str, Any]]:
    """Всё, что оплачено и ещё не выдано — для админ-панели."""
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT id, user_id, amount, attempts, next_try_at, paid_at, numbers "
            "FROM orders WHERE status = 'paid' ORDER BY paid_at LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Счета на пополнение криптой
# --------------------------------------------------------------------------

async def add_invoice(invoice_id: int, user_id: int, chat_id: int,
                      stars: int, amount: float, asset: str) -> None:
    now = int(time.time())
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO crypto_invoices "
            "(invoice_id, user_id, chat_id, stars, amount, asset, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'active', ?)",
            (invoice_id, user_id, chat_id, stars, amount, asset, now),
        )
        await conn.commit()


async def open_invoices(limit: int = 50) -> List[Dict[str, Any]]:
    """Счета, по которым ещё ждём оплату."""
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM crypto_invoices WHERE status = 'active' "
            "ORDER BY created_at LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_invoice(invoice_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM crypto_invoices WHERE invoice_id = ?", (invoice_id,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def close_invoice(invoice_id: int, status: str) -> bool:
    """Закрывает счёт. False — его уже закрыли раньше.

    Проверка «был активен» тут не для красоты: опрос и кнопка «я
    оплатил» могут сойтись на одном счёте, и без неё звёзды начислились
    бы дважды.
    """
    async with aiosqlite.connect(config.DB_PATH) as conn:
        cur = await conn.execute(
            "UPDATE crypto_invoices SET status = ? WHERE invoice_id = ? AND status = 'active'",
            (status, invoice_id),
        )
        await conn.commit()
        return cur.rowcount > 0


# --------------------------------------------------------------------------
# Кристаллы и премиум
# --------------------------------------------------------------------------

async def add_crystals(user_id: int, amount: int) -> int:
    """Меняет число кристаллов; в минус они не уходят. Возвращает остаток."""
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "UPDATE users SET crystals = MAX(0, crystals + ?) WHERE user_id = ?",
            (amount, user_id),
        )
        await conn.commit()
        async with conn.execute("SELECT crystals FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def spend_crystals(user_id: int, amount: int) -> bool:
    """Списывает кристаллы одним UPDATE с условием — как и баланс."""
    async with aiosqlite.connect(config.DB_PATH) as conn:
        cur = await conn.execute(
            "UPDATE users SET crystals = crystals - ? WHERE user_id = ? AND crystals >= ?",
            (amount, user_id, amount),
        )
        await conn.commit()
        return cur.rowcount > 0


def is_premium(user: Optional[Dict[str, Any]]) -> bool:
    return bool(user) and int(user.get("premium_until") or 0) > int(time.time())


async def user_is_premium(user_id: int) -> bool:
    return is_premium(await get_user(user_id))


async def grant_premium(user_id: int, days: int) -> int:
    """Продлевает премиум на ``days`` дней, считая от конца текущего."""
    now = int(time.time())
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute("SELECT premium_until FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        current = int(row[0]) if row and row[0] else 0
        until = max(now, current) + days * 86400
        await conn.execute("UPDATE users SET premium_until = ? WHERE user_id = ?", (until, user_id))
        await conn.commit()
    return until


async def set_order_crystals(order_id: int, crystals: int) -> None:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute("UPDATE orders SET crystals = ? WHERE id = ?", (crystals, order_id))
        await conn.commit()


# --------------------------------------------------------------------------
# Рефералы
# --------------------------------------------------------------------------

async def register_referral(invited_id: int, inviter_id: int) -> bool:
    """Запоминает, кто кого пригласил. Только один раз и не самого себя."""
    if invited_id == inviter_id:
        return False
    if not await get_user(inviter_id):
        return False
    async with aiosqlite.connect(config.DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT OR IGNORE INTO referrals (invited_id, inviter_id, created_at) VALUES (?, ?, ?)",
            (invited_id, inviter_id, int(time.time())),
        )
        await conn.commit()
        return cur.rowcount > 0


async def referral_count(inviter_id: int) -> int:
    """Сколько приглашённых уже засчитано (прошли проверку подписки)."""
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM referrals WHERE inviter_id = ? AND rewarded = 1", (inviter_id,),
        ) as cur:
            (count,) = await cur.fetchone()
    return int(count)


def referral_bonus(friends: int) -> int:
    """Бонус за ступень, которую только что достигли ``friends`` друзей.

    Ступени заданы как «итого кристаллов к этому моменту», поэтому бонус —
    это разница между итогом и обычной наградой за друзей, за вычетом
    бонусов, выданных на прошлых ступенях.
    """
    previous_bonus = 0
    for need, total in sorted(config.REFERRAL_TIERS.items()):
        bonus = total - config.REFERRAL_REWARD * need
        if need == friends:
            return max(0, bonus - previous_bonus)
        previous_bonus = max(previous_bonus, bonus)
    return 0


async def reward_referral(invited_id: int) -> Optional[Dict[str, Any]]:
    """Начисляет награду пригласившему — один раз на приглашённого.

    Помечаем ``rewarded`` условным UPDATE, поэтому два параллельных
    вызова не выдадут награду дважды. Возвращает описание начисления
    либо None, если награждать некого.
    """
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute(
            "SELECT inviter_id FROM referrals WHERE invited_id = ? AND rewarded = 0", (invited_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        inviter = int(row[0])
        cur = await conn.execute(
            "UPDATE referrals SET rewarded = 1 WHERE invited_id = ? AND rewarded = 0", (invited_id,),
        )
        await conn.commit()
        if cur.rowcount == 0:
            return None
        async with conn.execute(
            "SELECT COUNT(*) FROM referrals WHERE inviter_id = ? AND rewarded = 1", (inviter,),
        ) as cur2:
            (friends,) = await cur2.fetchone()

    friends = int(friends)
    bonus = referral_bonus(friends)
    gained = config.REFERRAL_REWARD + bonus
    total = await add_crystals(inviter, gained)
    return {"inviter": inviter, "friends": friends, "gained": gained, "bonus": bonus, "crystals": total}


# --------------------------------------------------------------------------
# Промокоды
# --------------------------------------------------------------------------

def _norm_code(code: str) -> str:
    return "".join(str(code).split()).upper()


async def create_promo(code: str, percent: int, max_uses: int = 0, days: int = 0) -> bool:
    """Заводит промокод. False — такой код уже есть."""
    code = _norm_code(code)
    now = int(time.time())
    expires = now + days * 86400 if days > 0 else 0
    async with aiosqlite.connect(config.DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT OR IGNORE INTO promo_codes (code, percent, max_uses, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (code, percent, max_uses, expires, now),
        )
        await conn.commit()
        return cur.rowcount > 0


async def list_promos() -> List[Dict[str, Any]]:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM promo_codes ORDER BY created_at DESC") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_promo(code: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM promo_codes WHERE code = ?", (_norm_code(code),)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def toggle_promo(code: str) -> None:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "UPDATE promo_codes SET active = 1 - active WHERE code = ?", (_norm_code(code),),
        )
        await conn.commit()


async def delete_promo(code: str) -> None:
    code = _norm_code(code)
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute("DELETE FROM promo_codes WHERE code = ?", (code,))
        await conn.execute("DELETE FROM user_promos WHERE code = ?", (code,))
        await conn.commit()


def promo_problem(promo: Optional[Dict[str, Any]]) -> Optional[str]:
    if not promo or not int(promo["active"]):
        return "Такого промокода нет"
    if int(promo["expires_at"]) and int(promo["expires_at"]) < int(time.time()):
        return "Срок действия промокода истёк"
    if int(promo["max_uses"]) and int(promo["used"]) >= int(promo["max_uses"]):
        return "Промокод уже использован максимальное число раз"
    return None


async def redeem_promo(user_id: int, code: str) -> Dict[str, Any]:
    """Привязывает промокод к пользователю: скидка применится к следующему заказу.

    Один код на одного человека — один раз за всё время.
    """
    promo = await get_promo(code)
    problem = promo_problem(promo)
    if problem:
        return {"ok": False, "error": problem}
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute(
            "SELECT 1 FROM promo_uses WHERE code = ? AND user_id = ?", (promo["code"], user_id),
        ) as cur:
            if await cur.fetchone():
                return {"ok": False, "error": "Вы уже использовали этот промокод"}
        await conn.execute(
            "INSERT INTO user_promos (user_id, code) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET code = excluded.code",
            (user_id, promo["code"]),
        )
        await conn.commit()
    return {"ok": True, "code": promo["code"], "percent": int(promo["percent"])}


async def active_promo(user_id: int) -> Optional[Dict[str, Any]]:
    """Промокод, ожидающий следующего заказа. Протухший сбрасывается."""
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute("SELECT code FROM user_promos WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    promo = await get_promo(row[0])
    if promo_problem(promo):
        async with aiosqlite.connect(config.DB_PATH) as conn:
            await conn.execute("DELETE FROM user_promos WHERE user_id = ?", (user_id,))
            await conn.commit()
        return None
    return promo


async def apply_promo(user_id: int, amount: int):
    """Цена со скидкой активного промокода и его код. Минимум — 1 звезда."""
    promo = await active_promo(user_id)
    if not promo or amount <= 0:
        return amount, None
    discounted = max(1, round(amount * (100 - int(promo["percent"])) / 100))
    return discounted, str(promo["code"])


async def consume_order_promo(order_id: int) -> None:
    """Списывает промокод заказа в момент оплаты: код сгорает для человека."""
    order = await get_order(order_id)
    if not order or not order.get("promo"):
        return
    code, user_id = str(order["promo"]), int(order["user_id"])
    async with aiosqlite.connect(config.DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT OR IGNORE INTO promo_uses (code, user_id) VALUES (?, ?)", (code, user_id),
        )
        if cur.rowcount:
            await conn.execute("UPDATE promo_codes SET used = used + 1 WHERE code = ?", (code,))
        await conn.execute(
            "DELETE FROM user_promos WHERE user_id = ? AND code = ?", (user_id, code),
        )
        await conn.commit()


# --------------------------------------------------------------------------
# Пользовательские шаблоны и скачивания
# --------------------------------------------------------------------------

async def add_user_template(owner_id: int, title: str) -> int:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO user_templates (owner_id, title, created_at) VALUES (?, ?, ?)",
            (owner_id, title, int(time.time())),
        )
        await conn.commit()
        return int(cur.lastrowid)


async def list_user_templates(include_hidden: bool = False) -> List[Dict[str, Any]]:
    query = "SELECT * FROM user_templates"
    if not include_hidden:
        query += " WHERE hidden = 0"
    query += " ORDER BY id DESC"
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(query) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_user_template(template_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM user_templates WHERE id = ?", (template_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def set_user_template_hidden(template_id: int, hidden: bool) -> None:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "UPDATE user_templates SET hidden = ? WHERE id = ?", (1 if hidden else 0, template_id),
        )
        await conn.commit()


async def count_user_templates_since(owner_id: int, since: int) -> int:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM user_templates WHERE owner_id = ? AND created_at >= ?",
            (owner_id, since),
        ) as cur:
            (count,) = await cur.fetchone()
    return int(count)


async def bump_downloads(numbers: List[int]) -> None:
    """Каждая собранная копия шаблона — одно «скачивание»."""
    if not numbers:
        return
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.executemany(
            "INSERT INTO template_stats (number, downloads) VALUES (?, 1) "
            "ON CONFLICT(number) DO UPDATE SET downloads = downloads + 1",
            [(int(n),) for n in numbers],
        )
        await conn.commit()


async def downloads_map() -> Dict[int, int]:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute("SELECT number, downloads FROM template_stats") as cur:
            rows = await cur.fetchall()
    return {int(n): int(d) for n, d in rows}


async def recent_orders_of_user(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """Заказы пользователя для мини-приложения (без тяжёлого поля logo)."""
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT id, status, amount, crystals, numbers, kind, created_at FROM orders "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def claim_order(order_id: int) -> bool:
    """Забирает заказ на оплату: ``new`` -> ``paying`` одним UPDATE.

    Двойное нажатие «оплатить» в мини-приложении приходит двумя
    параллельными запросами; выигрывает ровно один, второй получает False
    и ничего не списывает.
    """
    async with aiosqlite.connect(config.DB_PATH) as conn:
        cur = await conn.execute(
            "UPDATE orders SET status = 'paying' WHERE id = ? AND status = 'new'", (order_id,),
        )
        await conn.commit()
        return cur.rowcount > 0


async def release_order(order_id: int) -> None:
    """Возвращает заказ в ``new``, если оплата сорвалась."""
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "UPDATE orders SET status = 'new' WHERE id = ? AND status = 'paying'", (order_id,),
        )
        await conn.commit()


async def set_order_amount(order_id: int, amount: int, crystals: int = 0) -> None:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.execute(
            "UPDATE orders SET amount = ?, crystals = ? WHERE id = ?", (amount, crystals, order_id),
        )
        await conn.commit()
