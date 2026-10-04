"""Сервер мини-приложения (Telegram Web App).

Живёт в одном процессе с ботом: общая база, общая очередь выдачи, один
запуск. Фронтенд — статика из ``webapp/static``, всё остальное — JSON
API под ``/api``.

Каждый запрос к API подписан Telegram: клиент присылает ``initData`` в
заголовке ``X-Init-Data``, сервер проверяет HMAC по токену бота и только
после этого верит user.id. Подделать чужой профиль без токена нельзя.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import hmac
import json
import logging
import os
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import parse_qsl, quote

from aiohttp import web
from aiogram import Bot
from aiogram.types import BufferedInputFile, LabeledPrice

import config
import crypto
import db
import notify
import settings
import tgs_engine as engine

log = logging.getLogger("emoji-bot.webapp")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

#: Initdata старше суток не принимаем: подпись не имеет срока годности
#: сама по себе, и украденный заголовок жил бы вечно.
INIT_DATA_TTL = 24 * 3600

#: Сколько превью рисуем одновременно. Растеризация — чистый CPU.
_preview_slots = asyncio.Semaphore(2)

#: Сколько плиток в предпросмотре заказа.
PREVIEW_TILES = 24

MAX_TITLE = 40

Hook = Callable[..., Awaitable[Any]]


# --------------------------------------------------------------------------
# Авторизация
# --------------------------------------------------------------------------

def verify_init_data(raw: str, token: str) -> Optional[Dict[str, Any]]:
    """Проверяет подпись initData. Возвращает user либо None."""
    if not raw or not token:
        return None
    try:
        pairs = dict(parse_qsl(raw, keep_blank_values=True))
    except Exception:
        return None
    received = pairs.pop("hash", None)
    if not received:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        if time.time() - int(pairs.get("auth_date", "0")) > INIT_DATA_TTL:
            return None
        user = json.loads(pairs.get("user", ""))
    except Exception:
        return None
    return user if isinstance(user, dict) and user.get("id") else None


def _err(message: str, status: int = 400, **extra: Any) -> web.Response:
    return web.json_response({"ok": False, "error": message, **extra}, status=status)


def _ok(**data: Any) -> web.Response:
    return web.json_response({"ok": True, **data})


@web.middleware
async def auth_middleware(request: web.Request, handler):
    # Статика и страница открываются без подписи; картинки и анимации
    # грузятся тегами <img>/lottie без заголовков и отдают только
    # публичные шаблоны.
    if not request.path.startswith("/api/") or request.path.startswith(
        ("/api/lottie/", "/api/preview/"),
    ):
        return await handler(request)

    user = verify_init_data(request.headers.get("X-Init-Data", ""), config.BOT_TOKEN)
    if not user:
        return _err("Откройте приложение из Telegram", 401)

    user_id = int(user["id"])
    if await db.is_blocked(user_id):
        return _err("Доступ закрыт", 403)

    await db.ensure_user(user_id, user.get("username"), user.get("first_name"))
    request["user"] = user
    request["uid"] = user_id
    return await handler(request)


# --------------------------------------------------------------------------
# Вспомогательное
# --------------------------------------------------------------------------

def _clean_text(raw: Any) -> str:
    return re.sub(r"\s+", " ", str(raw or "").replace("\n", " ")).strip()


def _sample(app: web.Application) -> str:
    return f"@{app['bot_username']}"


async def _body(request: web.Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _order_limit(is_premium: bool, is_admin: bool) -> int:
    if is_admin or is_premium:
        return config.PREMIUM_MAX_ORDER
    return config.FREE_ORDER_LIMIT


async def _all_numbers() -> Dict[int, Dict[str, Any]]:
    """Все доступные шаблоны: номер -> описание."""
    items: Dict[int, Dict[str, Any]] = {}
    for n in engine.available_templates():
        items[n] = {"n": n, "title": f"Шаблон {n:03d}", "kind": "builtin", "owner": 0}
    for row in await db.list_user_templates():
        number = config.USER_TEMPLATE_BASE + int(row["id"])
        if os.path.exists(engine.template_path(number)):
            items[number] = {
                "n": number, "title": row["title"], "kind": "user",
                "owner": int(row["owner_id"]), "created_at": int(row["created_at"]),
            }
    return items


def _pack_links(packs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "name": p["name"], "title": p["title"], "count": int(p["count"]),
            "kind": p["kind"], "url": config.pack_url(p["kind"], p["name"]),
        }
        for p in packs
    ]


def _tiers(friends: int) -> Dict[str, Any]:
    tiers = [
        {"need": need, "total": total, "reached": friends >= need}
        for need, total in sorted(config.REFERRAL_TIERS.items())
    ]
    nxt = next((t for t in tiers if not t["reached"]), None)
    return {"tiers": tiers, "next": nxt}


# --------------------------------------------------------------------------
# Профиль и общая информация
# --------------------------------------------------------------------------

async def api_me(request: web.Request) -> web.Response:
    app = request.app
    uid = request["uid"]
    user = await db.get_user(uid) or {}
    friends = await db.referral_count(uid)
    promo = await db.active_promo(uid)
    premium = db.is_premium(user)
    missing = await app["hooks"]["missing_channels"](uid)

    return _ok(
        user={
            "id": uid,
            "name": request["user"].get("first_name") or "",
            "username": request["user"].get("username") or "",
            "photo": request["user"].get("photo_url") or "",
        },
        bot=app["bot_username"],
        balance=int(user.get("balance") or 0),
        crystals=int(user.get("crystals") or 0),
        premium={"active": premium, "until": int(user.get("premium_until") or 0)},
        emoji_created=int(user.get("emoji_created") or 0),
        friends=friends,
        referral={
            "link": f"https://t.me/{app['bot_username']}?start=ref_{uid}",
            "reward": config.REFERRAL_REWARD,
            **_tiers(friends),
        },
        promo={"code": promo["code"], "percent": int(promo["percent"])} if promo else None,
        is_admin=uid in config.ADMIN_IDS,
        gate=[{"title": c.get("title") or "Канал", "url": c.get("url") or ""} for c in missing],
        maintenance=settings.maintenance() and uid not in config.ADMIN_IDS,
        config={
            "price": settings.price(),
            "crystals_per_emoji": config.CRYSTALS_PER_EMOJI,
            "premium_price": config.PREMIUM_PRICE,
            "premium_days": config.PREMIUM_DAYS,
            "free_limit": config.FREE_ORDER_LIMIT,
            "premium_limit": config.PREMIUM_MAX_ORDER,
            "text_max": config.MAX_TEXT_LENGTH,
            "support_url": config.WEBAPP_SUPPORT_URL,
            "support": config.WEBAPP_SUPPORT,
            "topup_presets": config.TOPUP_PRESETS,
            "min_topup": config.MIN_TOPUP,
            "max_topup": config.MAX_TOPUP,
            "crypto": crypto.enabled(),
            "fonts": [{"id": fid, "label": label} for fid, (label, _f) in config.FONTS.items()],
            "default_font": config.DEFAULT_FONT,
            "templates_total": len(await _all_numbers()),
            "templates_builtin": len(engine.available_templates()),
            "user_template_daily": config.USER_TEMPLATE_DAILY,
            "kinds": {k: {"title": v["title"], "note": v["note"]} for k, v in config.KINDS.items()},
        },
    )


async def api_packs(request: web.Request) -> web.Response:
    packs = await db.get_packs(request["uid"])
    return _ok(packs=_pack_links(list(reversed(packs))))


async def api_orders(request: web.Request) -> web.Response:
    rows = await db.recent_orders_of_user(request["uid"], 15)
    return _ok(orders=[
        {
            "id": r["id"], "status": r["status"], "amount": int(r["amount"]),
            "crystals": int(r["crystals"] or 0), "count": len([x for x in str(r["numbers"]).split(",") if x]),
            "kind": r["kind"], "created_at": int(r["created_at"]),
        }
        for r in rows
    ])


# --------------------------------------------------------------------------
# Магазин
# --------------------------------------------------------------------------

async def api_shop(request: web.Request) -> web.Response:
    uid = request["uid"]
    items = await _all_numbers()
    stats = await db.downloads_map()

    rows = []
    for number, item in items.items():
        rows.append({
            **item,
            "downloads": stats.get(number, 0),
            "mine": item["owner"] == uid,
        })

    # «Новые»: сначала добавленные пользователями (свежие выше), затем
    # встроенные от последнего номера к первому.
    def fresh_key(row: Dict[str, Any]):
        return (1 if row["kind"] == "user" else 0, row.get("created_at", 0), row["n"])

    rows.sort(key=fresh_key, reverse=True)
    return _ok(items=rows)


async def api_preview(request: web.Request) -> web.Response:
    try:
        number = int(request.match_info["n"])
    except ValueError:
        raise web.HTTPNotFound()
    if not os.path.exists(engine.template_path(number)):
        raise web.HTTPNotFound()
    sample = _sample(request.app)
    try:
        async with _preview_slots:
            path = await asyncio.to_thread(engine.ensure_preview, number, sample)
    except Exception as exc:
        log.warning("Превью %s не собралось: %s", number, exc)
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


async def api_lottie(request: web.Request) -> web.Response:
    """Lottie-JSON шаблона для живой анимации в карточке магазина."""
    try:
        number = int(request.match_info["n"])
    except ValueError:
        raise web.HTTPNotFound()
    if not os.path.exists(engine.template_path(number)):
        raise web.HTTPNotFound()
    try:
        data = await asyncio.to_thread(engine.build_emoji, number, request.app["bot_username"], config.DEFAULT_FONT)
        raw = gzip.decompress(data)
    except Exception:
        try:
            raw = gzip.decompress(engine.load_template(number))
        except Exception:
            raise web.HTTPNotFound()
    return web.Response(body=raw, content_type="application/json",
                        headers={"Cache-Control": "public, max-age=3600"})


async def api_template_add(request: web.Request) -> web.Response:
    uid = request["uid"]
    try:
        form = await request.post()
    except Exception:
        return _err("Не удалось прочитать форму")

    upload = form.get("file")
    title = _clean_text(form.get("title"))[:MAX_TITLE]
    if upload is None or not hasattr(upload, "file"):
        return _err("Приложите файл .tgs")
    if not title:
        return _err("Введите название шаблона")

    data = upload.file.read(config.USER_TEMPLATE_MAX_BYTES + 1)
    problem = await asyncio.to_thread(engine.validate_template, data)
    if problem:
        return _err(f"Шаблон не подходит: {problem}")

    if uid not in config.ADMIN_IDS:
        since = int(time.time()) - 86400
        if await db.count_user_templates_since(uid, since) >= config.USER_TEMPLATE_DAILY:
            return _err(f"Лимит: не больше {config.USER_TEMPLATE_DAILY} шаблонов в сутки")

    template_id = await db.add_user_template(uid, title)
    with open(engine.user_template_path(template_id), "wb") as fh:
        fh.write(data)
    number = config.USER_TEMPLATE_BASE + template_id
    log.info("Пользователь %s добавил шаблон %s (%s)", uid, number, title)
    return _ok(number=number)


async def api_template_delete(request: web.Request) -> web.Response:
    uid = request["uid"]
    try:
        number = int(request.match_info["n"])
    except ValueError:
        return _err("Некорректный номер")
    if number < config.USER_TEMPLATE_BASE:
        return _err("Встроенные шаблоны удалить нельзя", 403)
    row = await db.get_user_template(number - config.USER_TEMPLATE_BASE)
    if not row:
        return _err("Шаблон не найден", 404)
    if int(row["owner_id"]) != uid and uid not in config.ADMIN_IDS:
        return _err("Это чужой шаблон", 403)
    await db.set_user_template_hidden(int(row["id"]), True)
    return _ok()


# --------------------------------------------------------------------------
# Заказ
# --------------------------------------------------------------------------

async def _validated_order_input(request: web.Request, data: Dict[str, Any]):
    """Разбирает и проверяет состав заказа. Возвращает (params, ошибка)."""
    uid = request["uid"]
    text = _clean_text(data.get("text"))
    if not text:
        return None, "Введите надпись для эмодзи"
    if len(text) > config.MAX_TEXT_LENGTH:
        return None, f"Надпись длиннее {config.MAX_TEXT_LENGTH} символов"

    font = str(data.get("font") or config.DEFAULT_FONT)
    if font not in config.FONTS:
        font = config.DEFAULT_FONT
    kind = str(data.get("kind") or config.DEFAULT_KIND)
    if kind not in config.KINDS:
        kind = config.DEFAULT_KIND

    available = await _all_numbers()
    mode = data.get("mode")
    if mode == "random":
        try:
            count = int(data.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        pool = [n for n in available if n < config.USER_TEMPLATE_BASE] or list(available)
        if count < 1:
            return None, "Укажите количество эмодзи"
        count = min(count, len(pool))
        import random
        numbers = sorted(random.sample(pool, count))
    else:
        raw = data.get("numbers") or []
        try:
            numbers = sorted({int(n) for n in raw})
        except (TypeError, ValueError):
            return None, "Некорректный список шаблонов"
        numbers = [n for n in numbers if n in available]
        if not numbers:
            return None, "Выберите хотя бы один шаблон"

    premium = await db.user_is_premium(uid)
    limit = _order_limit(premium, uid in config.ADMIN_IDS)
    if len(numbers) > limit:
        if premium or uid in config.ADMIN_IDS:
            return None, f"За один заказ — не больше {limit} эмодзи"
        return None, (
            f"Без премиума — до {limit} эмодзи за заказ. "
            f"Оформите премиум во вкладке «Прочее», чтобы убрать лимит."
        )

    return {"text": text, "font": font, "kind": kind, "numbers": numbers}, None


async def _order_view(order_id: int, uid: int) -> Dict[str, Any]:
    order = await db.get_order(order_id)
    user = await db.get_user(uid) or {}
    count = len(order["numbers"])
    return {
        "order_id": order_id,
        "count": count,
        "amount": int(order["amount"]),
        "base_amount": count * settings.price() if uid not in config.ADMIN_IDS else 0,
        "promo": order.get("promo"),
        "crystals_cost": count * config.CRYSTALS_PER_EMOJI,
        "balance": int(user.get("balance") or 0),
        "crystals": int(user.get("crystals") or 0),
        "free": uid in config.ADMIN_IDS,
    }


async def api_order_create(request: web.Request) -> web.Response:
    uid = request["uid"]
    if await request.app["hooks"]["missing_channels"](uid):
        return _err("Сначала подпишитесь на каналы")
    if settings.maintenance() and uid not in config.ADMIN_IDS:
        return _err("Идут технические работы, попробуйте позже", 503)

    params, problem = await _validated_order_input(request, await _body(request))
    if problem:
        return _err(problem)

    base = 0 if uid in config.ADMIN_IDS else len(params["numbers"]) * settings.price()
    amount, promo = await db.apply_promo(uid, base)
    order_id = await db.create_order(
        user_id=uid, pack_id="main", font_id=params["font"], font_path=None,
        text=params["text"], logo=None, numbers=params["numbers"],
        amount=amount, kind=params["kind"], promo=promo,
    )
    return _ok(**await _order_view(order_id, uid), numbers=params["numbers"])


async def api_order_preview(request: web.Request) -> web.Response:
    """Картинка-сетка с готовыми эмодзи — показывается до оплаты."""
    params, problem = await _validated_order_input(request, await _body(request))
    if problem:
        return _err(problem)
    shown = params["numbers"][:PREVIEW_TILES]

    def work() -> bytes:
        items = [(n, engine.build_emoji(n, params["text"], params["font"])) for n in shown]
        return engine.result_grid(items)

    try:
        async with _preview_slots:
            image = await asyncio.to_thread(work)
    except Exception as exc:
        log.warning("Предпросмотр заказа не собрался: %s", exc)
        return _err("Не удалось собрать предпросмотр")
    return web.Response(body=image, content_type="image/png")


async def _own_order(request: web.Request):
    try:
        order_id = int(request.match_info["id"])
    except ValueError:
        return None
    order = await db.get_order(order_id)
    if not order or int(order["user_id"]) != request["uid"]:
        return None
    return order


async def api_order_status(request: web.Request) -> web.Response:
    order = await _own_order(request)
    if not order:
        return _err("Заказ не найден", 404)
    return _ok(status=order["status"], count=len(order["numbers"]))


async def api_order_pay(request: web.Request) -> web.Response:
    uid = request["uid"]
    order = await _own_order(request)
    if not order:
        return _err("Заказ не найден", 404)
    if order["status"] != "new":
        return _err("Этот заказ уже оплачен")

    data = await _body(request)
    method = data.get("method")
    app = request.app
    hooks = app["hooks"]
    count = len(order["numbers"])
    amount = int(order["amount"])
    order_id = int(order["id"])
    from_user = _FakeUser(request["user"])

    if method == "stars":
        if amount <= 0:
            method = "balance"
        else:
            link = await app["bot"].create_invoice_link(
                title="Набор эмодзи",
                description=f"{count} шт. · надпись «{order['text']}»"[:200],
                payload=f"order:{order_id}",
                currency=config.CURRENCY,
                prices=[LabeledPrice(label=f"{count} шт.", amount=amount)],
            )
            return _ok(invoice=link)

    if not await db.claim_order(order_id):
        return _err("Этот заказ уже оплачивается")

    if method == "balance":
        if amount > 0 and not await db.spend_balance(uid, amount):
            await db.release_order(order_id)
            have = await db.get_balance(uid)
            return _err(f"Не хватает звёзд на балансе: нужно {amount}, есть {have}")
        await hooks["queue_order"](app["bot"], order_id, "balance" if amount > 0 else "admin", uid)
        await notify.purchase(app["bot"], from_user, order, "balance" if amount > 0 else "admin",
                              balance=await db.get_balance(uid))
        return _ok(queued=True)

    if method == "crystals":
        cost = count * config.CRYSTALS_PER_EMOJI
        if not await db.spend_crystals(uid, cost):
            await db.release_order(order_id)
            user = await db.get_user(uid) or {}
            return _err(f"Не хватает кристаллов: нужно {cost}, есть {int(user.get('crystals') or 0)}")
        # Кристаллы — отдельная валюта: звёздная цена заказа обнуляется,
        # чтобы статистика и возврат не считали его оплаченным звёздами.
        await db.set_order_amount(order_id, 0, cost)
        await hooks["queue_order"](app["bot"], order_id, "crystals", uid)
        await notify.purchase(app["bot"], from_user, {**order, "amount": 0, "crystals": cost}, "crystals")
        return _ok(queued=True)

    await db.release_order(order_id)
    return _err("Неизвестный способ оплаты")


class _FakeUser:
    """Минимальная замена aiogram.types.User для уведомлений админам."""

    def __init__(self, raw: Dict[str, Any]):
        self.id = int(raw["id"])
        self.username = raw.get("username")
        self.first_name = raw.get("first_name") or ""
        self.last_name = raw.get("last_name") or ""
        self.full_name = (self.first_name + " " + self.last_name).strip()


# --------------------------------------------------------------------------
# Баланс, промокод, премиум
# --------------------------------------------------------------------------

async def api_topup(request: web.Request) -> web.Response:
    uid = request["uid"]
    data = await _body(request)
    try:
        amount = int(data.get("amount"))
    except (TypeError, ValueError):
        return _err("Введите сумму")
    if not (config.MIN_TOPUP <= amount <= config.MAX_TOPUP):
        return _err(f"Сумма от {config.MIN_TOPUP} до {config.MAX_TOPUP} ⭐️")

    if data.get("method") == "crypto":
        if not crypto.enabled():
            return _err("Оплата криптой не подключена")
        stars_amount = crypto.stars_to_amount(amount)
        try:
            invoice = await crypto.create_invoice(
                amount=stars_amount, description=f"{amount} звёзд на баланс бота",
                payload=f"topup:{uid}:{amount}",
            )
        except Exception as exc:
            log.error("Счёт в Crypto Pay не выставился: %s", exc)
            return _err("Не удалось выставить крипто-счёт")
        await db.add_invoice(invoice["id"], uid, uid, amount, stars_amount, config.CRYPTO_ASSET)
        return _ok(url=invoice["url"])

    link = await request.app["bot"].create_invoice_link(
        title="Пополнение баланса",
        description=f"{amount} ⭐️ на баланс бота",
        payload=f"topup:{amount}",
        currency=config.CURRENCY,
        prices=[LabeledPrice(label=f"{amount} ⭐️", amount=amount)],
    )
    return _ok(invoice=link)


async def api_promo(request: web.Request) -> web.Response:
    code = str((await _body(request)).get("code") or "").strip()
    if not code:
        return _err("Введите промокод")
    result = await db.redeem_promo(request["uid"], code)
    if not result["ok"]:
        return _err(result["error"])
    return _ok(code=result["code"], percent=result["percent"])


async def api_premium(request: web.Request) -> web.Response:
    uid = request["uid"]
    data = await _body(request)
    app = request.app

    if data.get("method") == "balance":
        if not await db.spend_balance(uid, config.PREMIUM_PRICE):
            have = await db.get_balance(uid)
            return _err(f"Не хватает звёзд: нужно {config.PREMIUM_PRICE}, есть {have}")
        until = await db.grant_premium(uid, config.PREMIUM_DAYS)
        await notify.to_admins(
            app["bot"], f"💎 <b>Премиум</b> с баланса: <code>{uid}</code> · {config.PREMIUM_PRICE} ⭐️",
        )
        return _ok(until=until)

    link = await app["bot"].create_invoice_link(
        title="Премиум",
        description=f"Безлимитное создание эмодзи на {config.PREMIUM_DAYS} дн.",
        payload=f"premium:{config.PREMIUM_DAYS}",
        currency=config.CURRENCY,
        prices=[LabeledPrice(label="Премиум", amount=config.PREMIUM_PRICE)],
    )
    return _ok(invoice=link)


# --------------------------------------------------------------------------
# Запуск
# --------------------------------------------------------------------------

async def _index(request: web.Request) -> web.Response:
    return web.FileResponse(
        os.path.join(STATIC_DIR, "index.html"),
        headers={"Cache-Control": "no-store"},
    )


async def _health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def build_app(bot: Bot, bot_username: str, hooks: Dict[str, Hook]) -> web.Application:
    app = web.Application(middlewares=[auth_middleware], client_max_size=2 * 1024 * 1024)
    app["bot"] = bot
    app["bot_username"] = bot_username
    app["hooks"] = hooks

    app.router.add_get("/", _index)
    app.router.add_get("/health", _health)
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/packs", api_packs)
    app.router.add_get("/api/orders", api_orders)
    app.router.add_get("/api/shop", api_shop)
    app.router.add_get("/api/preview/{n}.png", api_preview)
    app.router.add_get("/api/lottie/{n}.json", api_lottie)
    app.router.add_post("/api/templates", api_template_add)
    app.router.add_delete("/api/templates/{n}", api_template_delete)
    app.router.add_post("/api/order", api_order_create)
    app.router.add_post("/api/order/preview", api_order_preview)
    app.router.add_get("/api/order/{id}", api_order_status)
    app.router.add_post("/api/order/{id}/pay", api_order_pay)
    app.router.add_post("/api/topup", api_topup)
    app.router.add_post("/api/promo", api_promo)
    app.router.add_post("/api/premium", api_premium)
    app.router.add_static("/static/", STATIC_DIR, show_index=False)
    return app


async def start(bot: Bot, hooks: Dict[str, Hook]) -> web.AppRunner:
    """Поднимает сервер рядом с поллингом бота. Возвращает runner для остановки."""
    me = await bot.me()
    app = build_app(bot, me.username or "", hooks)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.WEBAPP_HOST, config.WEBAPP_PORT)
    await site.start()
    log.info("Мини-приложение: %s (слушаю %s:%s)", config.WEBAPP_URL,
             config.WEBAPP_HOST, config.WEBAPP_PORT)
    return runner
