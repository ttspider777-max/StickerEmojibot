"""Пополнение баланса криптой через Crypto Pay (@CryptoBot, @send).

Модуль знает только про HTTP-API платёжной системы: выставить счёт и
спросить, оплачен ли он. Ни про Telegram, ни про баланс он не знает —
этим занимается bot.py.

Вебхук не используем намеренно: он требует публичного HTTPS-адреса,
которого у бота на обычном хостинге нет. Вместо этого счета опрашиваются
фоновой задачей — для десятка пополнений в день этого с запасом хватает.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import aiohttp

import config

log = logging.getLogger("emoji-bot.crypto")

#: Сколько ждём ответа платёжной системы. Больше — и очередь опроса
#: начнёт залипать на одном зависшем запросе.
TIMEOUT = 20


def enabled() -> bool:
    """Настроена ли оплата криптой."""
    return bool(config.CRYPTO_TOKEN)


class CryptoError(Exception):
    """Платёжная система ответила отказом."""


async def _call(method: str, **params: Any) -> Any:
    """Запрос к Crypto Pay. Токен уходит заголовком, как требует API."""
    if not enabled():
        raise CryptoError("оплата криптой не настроена")

    url = f"{config.CRYPTO_API}/{method}"
    headers = {"Crypto-Pay-API-Token": config.CRYPTO_TOKEN}
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=params, headers=headers) as response:
            body = await response.json(content_type=None)

    if not isinstance(body, dict) or not body.get("ok"):
        error = (body or {}).get("error") if isinstance(body, dict) else None
        raise CryptoError(str(error or body)[:200])
    return body.get("result")


async def check_token() -> Optional[str]:
    """Проверяет токен. Возвращает название приложения либо None.

    Зовётся при старте и из админки: без этого неверный токен
    обнаруживался бы только в момент, когда человек уже жмёт «оплатить».
    """
    try:
        result = await _call("getMe")
    except Exception as exc:
        log.error("Crypto Pay не отвечает: %s", exc)
        return None
    return str((result or {}).get("name") or "Crypto Pay")


async def create_invoice(amount: float, description: str, payload: str) -> Dict[str, Any]:
    """Выставляет счёт и возвращает его id со ссылкой на оплату."""
    result = await _call(
        "createInvoice",
        asset=config.CRYPTO_ASSET,
        amount=f"{amount:.2f}",
        description=description[:1024],
        payload=payload,
        allow_comments=False,
        allow_anonymous=True,
        expires_in=config.CRYPTO_INVOICE_TTL,
    )
    # У разных клиентов кошелька ссылки называются по-разному: mini app
    # у @send, обычная ссылка у @CryptoBot. Берём ту, что пришла.
    url = (
        result.get("mini_app_invoice_url")
        or result.get("bot_invoice_url")
        or result.get("web_app_invoice_url")
        or result.get("pay_url")
    )
    return {"id": int(result["invoice_id"]), "url": url, "amount": amount}


async def statuses(invoice_ids: List[int]) -> Dict[int, str]:
    """Статусы счетов одним запросом: {id: active|paid|expired}."""
    if not invoice_ids:
        return {}
    result = await _call("getInvoices", invoice_ids=",".join(str(i) for i in invoice_ids))
    items = (result or {}).get("items") or []
    return {int(item["invoice_id"]): str(item.get("status") or "") for item in items}


def stars_to_amount(stars: int) -> float:
    """Во что обходятся звёзды в валюте счёта.

    Курс держим в настройках, а не считаем на лету: биржевой курс к
    звёздам всё равно неоткуда взять, а владелец бота сам решает, почём
    продаёт внутреннюю валюту.
    """
    return round(stars * config.CRYPTO_RATE, 2)
