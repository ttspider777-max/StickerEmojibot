"""Все тексты бота в одном месте — чтобы правки формулировок не лезли в логику."""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

import branding
import config
import settings


def main_menu(first_name: Optional[str], templates: int, bot_username: str = "") -> str:
    """Текст главного меню. Переопределяется в branding.json ключом menu."""
    name = (first_name or "друг").strip()
    return branding.text(
        "menu",
        _default_main_menu(name, templates),
        name=name, templates=templates, price=settings.price(),
        bot=bot_username, support=config.SUPPORT_CONTACT,
        channel=config.REQUIRED_CHANNEL, fonts=len(config.FONTS),
    )


def _default_main_menu(name: str, templates: int) -> str:
    return (
        f"👋 <b>Привет, {name}!</b>\n\n"
        "Я делаю <b>анимированные кастом-эмодзи</b> из готовых шаблонов: "
        "ты выбираешь шаблоны и шрифт, присылаешь надпись или логотип — "
        "я собираю готовый набор для Telegram.\n\n"
        f"📦 Шаблонов в боте: <b>{templates}</b>\n"
        "🔤 6 встроенных шрифтов + можно загрузить свой\n"
        f"⭐️ Цена: <b>{settings.price()}</b> звезда за одно эмодзи\n\n"
        "Выбери, что делаем:"
    )


def profile(user: Dict[str, Any], packs: List[Dict[str, Any]], username: Optional[str]) -> str:
    created = _dt.datetime.fromtimestamp(int(user.get("created_at", 0))).strftime("%d.%m.%Y")
    handle = f"@{username}" if username else "—"
    lines = [
        "👤 <b>Твой профиль</b>\n",
        f"🆔 ID: <code>{user['user_id']}</code>",
        f"🔗 Ник: {handle}",
        f"📅 В боте с: {created}",
        f"💼 Баланс: <b>{user.get('balance', 0)}</b> ⭐️",
        f"✨ Создано эмодзи: <b>{user.get('emoji_created', 0)}</b>",
        f"⭐️ Потрачено звёзд: <b>{user.get('stars_spent', 0)}</b>",
        f"📦 Наборов: <b>{len(packs)}</b>",
    ]
    if packs:
        lines.append("\n<b>Твои наборы:</b>")
        for pack in packs:
            kind = config.KINDS.get(pack.get("kind", "emoji"), config.KINDS["emoji"])
            lines.append(
                f"• {kind['icon']} <a href=\"{config.pack_url(pack.get('kind', 'emoji'), pack['name'])}\">"
                f"{pack['title']}</a> — {pack['count']} шт."
            )
    else:
        lines.append("\nПока пусто. Собери первый набор — он появится здесь.")
    return "\n".join(lines)


def support() -> str:
    lines = [
        "💬 <b>Поддержка</b>\n",
        "Не собрался набор, звёзды списались без результата, "
        "шаблон рисует надпись криво — пиши, разберёмся.\n",
        f"👤 Связь: {config.SUPPORT_CONTACT}",
    ]
    if config.SUPPORT_CHAT:
        lines.append(f"📣 Канал: {config.SUPPORT_CHAT}")
    lines.append(
        "\n<b>Частые вопросы</b>\n"
        "• <i>Эмодзи не вставляется в чат</i> — кастом-эмодзи работают "
        "только с Telegram Premium.\n"
        f"• <i>Надпись не влезла</i> — максимум {config.MAX_TEXT_LENGTH} символов.\n"
        "• <i>Не приняло мой шрифт</i> — нужен файл <code>.ttf</code> или "
        "<code>.otf</code> до 5 МБ.\n"
        "• <i>Не приняло логотип</i> — нужен <code>.svg</code> до 256 КБ."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Выбор набора и режима
# --------------------------------------------------------------------------

def choose_pack() -> str:
    return (
        "📦 <b>Наборы шаблонов</b>\n\n"
        "Выбери, из какого набора собирать эмодзи."
    )


def choose_mode(pack_title: str, total: int) -> str:
    return (
        f"📦 Набор: <b>{pack_title}</b> — {total} шаблонов\n\n"
        "Как выбираем шаблоны?\n\n"
        "🎲 <b>Случайно</b> — скажешь, сколько эмодзи нужно, и бот сам "
        "наберёт их из набора.\n"
        "✋ <b>Выбрать самому</b> — листаешь страницы по 12 штук и "
        "отмечаешь нужные."
    )


# --------------------------------------------------------------------------
# Шрифты
# --------------------------------------------------------------------------

def choose_font(sample: str) -> str:
    return (
        "🔤 <b>Выбор шрифта</b>\n\n"
        f"На примерах — надпись <b>{sample}</b> в каждом из шрифтов, "
        "по одному случайному шаблону на шрифт.\n\n"
        "Нажми на название нужного шрифта или загрузи свой."
    )


def ask_font_file() -> str:
    return (
        "⬆️ <b>Свой шрифт</b>\n\n"
        "Пришли файлом <code>.ttf</code> или <code>.otf</code> — до 5 МБ.\n\n"
        "<i>Для русских надписей в шрифте должна быть кириллица, иначе "
        "буквы не нарисуются и бот возьмёт запасной шрифт.</i>"
    )


def font_rejected(reason: str) -> str:
    return f"❌ Шрифт не принят: {reason}\n\nПришли другой файл."


def font_accepted(missing: str = "") -> str:
    text = "✅ Шрифт загружен и добавлен в примеры."
    if missing:
        text += f"\n\n⚠️ В нём нет символов: <code>{missing}</code> — они не нарисуются."
    return text


# --------------------------------------------------------------------------
# Содержимое эмодзи
# --------------------------------------------------------------------------

def ask_content(font_name: str) -> str:
    return (
        f"✏️ Шрифт: <b>{font_name}</b>\n\n"
        "Теперь пришли, что писать на эмодзи:\n\n"
        f"• <b>текстом</b> — надпись до {config.MAX_TEXT_LENGTH} символов\n"
        "• <b>файлом .svg</b> — векторный логотип до 256 КБ\n\n"
        "<i>Логотип встанет в ту же зону, где в шаблоне была надпись.</i>"
    )


def text_too_long(length: int) -> str:
    return (
        f"❌ Слишком длинно: {length} символов, "
        f"а влезает {config.MAX_TEXT_LENGTH}. Пришли покороче."
    )


def logo_rejected(reason: str) -> str:
    return f"❌ Логотип не принят: {reason}\n\nПришли другой .svg или просто текст."


# --------------------------------------------------------------------------
# Количество
# --------------------------------------------------------------------------

def ask_quantity(available: int) -> str:
    return (
        "🔢 <b>Сколько эмодзи собрать?</b>\n\n"
        f"Доступно шаблонов: <b>{available}</b>\n"
        f"Цена: <b>{settings.price()}</b> ⭐️ за эмодзи\n\n"
        "Выбери кнопкой или пришли своё число."
    )


def bad_quantity(available: int) -> str:
    return f"❌ Нужно число от {config.MIN_ORDER} до {min(config.MAX_ORDER, available)}."


# --------------------------------------------------------------------------
# Ручной выбор
# --------------------------------------------------------------------------

def selection(page: int, pages: int, numbers: List[int], selected: int) -> str:
    first, last = (numbers[0], numbers[-1]) if numbers else (0, 0)
    return (
        f"✋ <b>Шаблоны {first}–{last}</b> · страница {page + 1}/{pages}\n"
        f"Выбрано: <b>{selected}</b> — отмеченные обведены синим\n"
        "Диапазон можно прислать текстом: <code>1-12, 20-25</code>"
    )


def nothing_selected() -> str:
    return "Сначала отметь хотя бы один шаблон"


def range_parsed(added: int, total: int) -> str:
    return f"✅ Добавлено шаблонов: <b>{added}</b>. Всего выбрано: <b>{total}</b>."


def range_failed() -> str:
    return (
        "❌ Не понял диапазон. Примеры: <code>1-12</code>, "
        "<code>3,7,15</code>, <code>1-12, 20-25</code>."
    )


# --------------------------------------------------------------------------
# Итог и оплата
# --------------------------------------------------------------------------

def order_preview(count: int, shown: int, font_name: str, content: str,
                  amount: int, target: str, balance: int, kind: str) -> str:
    meta = config.KINDS[kind]
    tail = "" if shown >= count else f"\n<i>На картинке показаны первые {shown} из {count}.</i>"
    return (
        "🖼 <b>Финальное превью</b>\n\n"
        f"✨ Штук: <b>{count}</b>\n"
        f"🔤 Шрифт: <b>{font_name}</b>\n"
        f"✏️ Содержимое: <b>{content}</b>\n"
        f"{meta['icon']} Тип: <b>{meta['title']}</b> — {meta['note']}\n"
        f"📦 Куда: <b>{target}</b>\n"
        + ("🎁 Оплата: <b>не нужна</b> — админская сборка\n" if amount == 0 else
           f"⭐️ К оплате: <b>{amount}</b> · на балансе: <b>{balance}</b>\n")
        +
        f"{tail}\n\n"
        "Можно перевыбрать любой шаг — или оплатить и получить набор."
    )


def invoice_title() -> str:
    return "Набор кастом-эмодзи"


def invoice_description(count: int, content: str) -> str:
    return f"{count} анимированных эмодзи с надписью «{content}». Готовый набор придёт в чат."


def payment_done(count: int) -> str:
    return (
        "✅ <b>Оплата прошла</b>\n\n"
        f"Собираю набор из <b>{count}</b> эмодзи — это займёт до минуты. "
        "Не закрывай чат."
    )


def _bar(done: int, total: int) -> str:
    filled = int(done / total * 10) if total else 0
    return "▰" * filled + "▱" * (10 - filled)


def duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes <= 0:
        return "меньше минуты"
    if minutes == 1:
        return "около минуты"
    if minutes < 5:
        return f"около {minutes} минут"
    return f"около {minutes} мин"


def progress(done: int, total: int, uploading: bool = False, eta: float = 0.0) -> str:
    """Ход сборки. На этапе записи в набор показываем ещё и остаток времени.

    Telegram пускает в наборы 8 обращений за 4 минуты, поэтому большой
    заказ идёт медленно и это нормально. Без явной строки про ожидание
    выглядит как зависший бот, и человек уходит из чата.
    """
    if uploading:
        return f"⏳ Готовлю эмодзи…\n\n{_bar(done, total)}  {done}/{total}"
    tail = f"\n\n⏱ Осталось {duration(eta)}." if eta >= 60 else ""
    return (
        f"📦 Добавляю в набор…\n\n{_bar(done, total)}  {done}/{total}"
        f"{tail}\n\n"
        "<i>Telegram пускает в наборы по 8 штук за 4 минуты — паузы "
        "нормальны, бот не завис. Можно закрыть чат, набор придёт сам.</i>"
    )


def long_build_warning(count: int, eta: float) -> str:
    """Предупреждение о долгой сборке — до оплаты, а не после."""
    return (
        f"\n⏱ <i>Сборка {count} шт. займёт {duration(eta)}: Telegram "
        "пускает в наборы по 8 обращений за 4 минуты. Бот дособерёт сам, "
        "чат можно закрыть.</i>"
    )


def pack_ready(pack_name: str, pack_title: str, added: int, total: int,
               failed: int, is_new: bool = True, kind: str = "emoji") -> str:
    meta = config.KINDS.get(kind, config.KINDS["emoji"])
    lines = [
        ("🎉 <b>Набор готов!</b>\n" if is_new else "✅ <b>Добавлено в набор</b>\n"),
        f"{meta['icon']} {pack_title} — {meta['title'].lower()}",
        f"✨ Добавлено: <b>{added}</b> (всего в наборе: {total})",
    ]
    if failed:
        lines.append(f"⚠️ Не прошли проверку Telegram: {failed} — звёзды за них вернём по запросу")
    lines.append(f"\n👉 <a href=\"{config.pack_url(kind, pack_name)}\">Открыть набор</a>")
    if kind == "emoji":
        lines.append("\n<i>Ставить кастом-эмодзи в сообщения можно с Telegram Premium.</i>")
    return "\n".join(lines)


def pack_failed(reason: str) -> str:
    return (
        "😔 <b>Набор собрать не удалось</b>\n\n"
        f"Ответ Telegram: <code>{reason}</code>\n\n"
        f"Звёзды не пропали — напиши в поддержку {config.SUPPORT_CONTACT}, "
        "вернём или пересоберём вручную."
    )


def build_failed(reason: str) -> str:
    return (
        "😔 <b>Не получилось собрать превью</b>\n\n"
        f"Причина: <code>{reason}</code>\n\n"
        "Попробуй другую надпись или другие шаблоны."
    )


WORKING = "⏳ Рисую превью…"
SESSION_LOST = "Сессия потерялась. Открой меню и начни заново."


def maintenance() -> str:
    return (
        "🔧 <b>Технические работы</b>\n\n"
        "Приём заказов временно выключен — чиним и возвращаемся. "
        "Уже собранные наборы никуда не денутся."
    )


def blocked() -> str:
    return (
        "🚫 <b>Доступ ограничен</b>\n\n"
        f"Создание эмодзи для тебя закрыто. Если это ошибка — напиши "
        f"в поддержку: {config.SUPPORT_CONTACT}"
    )


# --------------------------------------------------------------------------
# Баланс
# --------------------------------------------------------------------------

def topup() -> str:
    return (
        "💼 <b>Пополнение баланса</b>\n\n"
        "Звёзды с баланса тратятся на наборы эмодзи — удобно, когда "
        "собираешь их регулярно и не хочешь платить счётом каждый раз.\n\n"
        "Выбери сумму кнопкой или пришли своё число."
    )


def bad_topup() -> str:
    return f"❌ Нужно число от {config.MIN_TOPUP} до {config.MAX_TOPUP}."


def topup_done(amount: int, balance: int) -> str:
    return (
        "✅ <b>Баланс пополнен</b>\n\n"
        f"⭐️ Зачислено: <b>{amount}</b>\n"
        f"💼 Теперь на балансе: <b>{balance}</b>"
    )


def topup_invoice_title() -> str:
    return "Пополнение баланса"


def topup_invoice_description(amount: int) -> str:
    return f"{amount} звёзд на баланс бота — их можно потратить на наборы эмодзи."


def not_enough_balance(need: int, have: int) -> str:
    return f"На балансе {have} ⭐️, а нужно {need}. Пополни или заплати счётом."


# --------------------------------------------------------------------------
# Куда складывать эмодзи
# --------------------------------------------------------------------------

NEW_PACK_LABEL = "новый набор"


def choose_kind() -> str:
    lines = ["🎭 <b>Что собираем</b>\n"]
    for meta in config.KINDS.values():
        lines.append(f"{meta['icon']} <b>{meta['title']}</b> — {meta['note']}")
    lines.append("\nПереключается в любой момент до оплаты.")
    return "\n".join(lines)


def choose_target(count: int, packs: int) -> str:
    if not packs:
        return (
            "📦 <b>Куда добавить эмодзи</b>\n\n"
            "Готовых наборов пока нет — соберём новый."
        )
    return (
        "📦 <b>Куда добавить эмодзи</b>\n\n"
        f"Можно завести новый набор или дописать <b>{count}</b> эмодзи "
        "в один из уже существующих.\n\n"
        "<i>Показаны только те наборы, куда весь заказ ещё помещается: "
        f"в одном наборе Telegram держит до {config.PACK_LIMIT} эмодзи.</i>"
    )


def balance_granted(amount: int, balance: int) -> str:
    """Сообщение человеку о том, что админ начислил ему звёзды."""
    if amount >= 0:
        return (
            "🎁 <b>Тебе начислили звёзды</b>\n\n"
            f"⭐️ Зачислено: <b>{amount}</b>\n"
            f"💼 На балансе: <b>{balance}</b>\n\n"
            "Можно сразу собирать набор — оплата спишется с баланса."
        )
    return (
        "💼 <b>Баланс изменён</b>\n\n"
        f"⭐️ Списано: <b>{abs(amount)}</b>\n"
        f"💼 Осталось: <b>{balance}</b>"
    )


def admin_free() -> str:
    return (
        "🎁 <b>Админская сборка</b>\n\n"
        "Оплата не нужна — собираю набор."
    )


def subscribe(channels: List[Dict[str, Any]]) -> str:
    """Экран подписки. Каналов может быть несколько — перечисляем все."""
    listing = "\n".join(f"• {c.get('title')}" for c in channels)
    word = "канал" if len(channels) == 1 else "каналы"
    return (
        "📣 <b>Нужна подписка</b>\n\n"
        f"Чтобы пользоваться ботом, подпишись на {word}:\n\n"
        f"{listing}\n\n"
        "Потом нажми «Я подписался» — проверю и пущу дальше."
    )


def still_not_subscribed() -> str:
    return "Подписка не найдена. Подпишись на канал и нажми ещё раз."


def pack_postponed(retry_after: int, has_packs: bool, auto: bool) -> str:
    """Telegram не даёт создать набор прямо сейчас — заказ отложен.

    Главное здесь — снять тревогу: человек заплатил и видит, что ничего
    не собралось. Поэтому первым делом говорим, что заказ цел, и только
    потом про сроки.
    """
    minutes = max(1, round(retry_after / 60))
    lines = [
        "⏸ <b>Сборка отложена</b>\n",
        f"Telegram сейчас не даёт создавать новые наборы — просит подождать "
        f"около <b>{minutes} мин</b>. Это ограничение на бота целиком, "
        "повтор раньше времени ничего не изменит.\n",
        "<b>Заказ не потерян, платить заново не нужно.</b>",
    ]
    if auto:
        lines.append(
            f"✅ Соберу сам примерно через {minutes} мин — чат можно закрыть, "
            "набор придёт сюда же."
        )
    else:
        lines.append("Нажми «Повторить сборку», когда время выйдет.")
    if has_packs:
        lines.append(
            "\n⚡️ Можно не ждать: собери в <b>уже готовый набор</b> — туда "
            "эмодзи добавляются без этого ограничения. Кнопка «Куда "
            "добавить» на экране заказа."
        )
    return "\n".join(lines)


def packs_ready(created: List[Any], added: int, failed: int, kind: str) -> str:
    """Итог, когда заказ разложен по нескольким наборам.

    Так собирается большой заказ: Telegram забирает до 50 штук одним
    вызовом, поэтому четыре набора по 50 готовы за минуту, а один
    набор на 200 собирался бы больше часа.
    """
    meta = config.KINDS.get(kind, config.KINDS["emoji"])
    lines = [
        "🎉 <b>Готово!</b>\n",
        f"✨ Собрано: <b>{added}</b> в {len(created)} наборах "
        f"({meta['title'].lower()})\n",
    ]
    for pack_name, pack_title, count in created:
        lines.append(
            f"{meta['icon']} <a href=\"{config.pack_url(kind, pack_name)}\">"
            f"{pack_title}</a> — {count} шт."
        )
    if failed:
        lines.append(f"\n⚠️ Не прошли проверку Telegram: {failed}")
    lines.append(
        "\n<i>Наборов несколько, потому что Telegram принимает по 50 штук "
        "за раз: так весь заказ готов сразу, а не через час.</i>"
    )
    return "\n".join(lines)


def switched_to_existing(pack_title: str, count: int) -> str:
    """Заказ уходит в готовый набор, потому что новый создать нельзя."""
    minutes = max(1, round(count * 30 / 60))
    return (
        "📦 <b>Добавляю в твой набор</b>\n\n"
        f"Telegram сейчас не даёт заводить новые наборы, поэтому эмодзи "
        f"уйдут в <b>{pack_title}</b> — так заказ не будет ждать.\n\n"
        f"⏱ Займёт около {minutes} мин: в готовый набор Telegram пускает "
        "по 8 штук за 4 минуты. Чат можно закрыть."
    )


def retry_too_early(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    return f"Telegram ещё держит паузу — примерно {minutes} мин. Бот повторит сам."


def order_refunded(amount: int, returned: bool) -> str:
    """Заказ так и не собрался — закрываем его возвратом.

    Держать чужие деньги без товара нельзя, поэтому бот возвращает их
    сам, не дожидаясь, пока человек напишет в поддержку.
    """
    if returned:
        return (
            "↩️ <b>Вернул звёзды</b>\n\n"
            f"Собрать набор так и не удалось — Telegram сутки не давал "
            f"создать его. Возвращаю <b>{amount}</b> ⭐️.\n\n"
            "Попробуй позже: обычно ограничение снимается само."
        )
    return (
        "😔 <b>Набор собрать не удалось</b>\n\n"
        f"Telegram сутки не давал его создать. Звёзды (<b>{amount}</b> ⭐️) "
        f"вернём вручную — напиши в поддержку {config.SUPPORT_CONTACT}."
    )


# --------------------------------------------------------------------------
# Пополнение криптой
# --------------------------------------------------------------------------

def topup_method(balance: int) -> str:
    return (
        "💼 <b>Пополнение баланса</b>\n\n"
        f"Сейчас на балансе: <b>{balance}</b> ⭐️\n\n"
        "Звёзды с баланса тратятся на наборы эмодзи. Выбери, чем платить:\n\n"
        "⭐️ <b>Telegram Stars</b> — счётом прямо в чате\n"
        f"🪙 <b>Криптой</b> — через @CryptoBot или @send, "
        f"{int(1 / config.CRYPTO_RATE)} звёзд за 1 {config.CRYPTO_ASSET}"
    )


def crypto_amounts() -> str:
    return (
        f"🪙 <b>Оплата криптой</b>\n\n"
        f"Курс: <b>{int(1 / config.CRYPTO_RATE)}</b> звёзд за 1 "
        f"{config.CRYPTO_ASSET}.\n\n"
        "Выбери, сколько звёзд зачислить, или пришли своё число."
    )


def crypto_invoice(stars: int, amount: float) -> str:
    return (
        "🪙 <b>Счёт выставлен</b>\n\n"
        f"⭐️ Зачислим: <b>{stars}</b> звёзд\n"
        f"💵 К оплате: <b>{amount} {config.CRYPTO_ASSET}</b>\n\n"
        "Нажми «Оплатить» — откроется кошелёк. После оплаты звёзды "
        "придут сами, обычно за несколько секунд.\n\n"
        f"<i>Счёт действует {config.CRYPTO_INVOICE_TTL // 60} минут.</i>"
    )


def crypto_paid(stars: int, balance: int) -> str:
    return (
        "✅ <b>Оплата получена</b>\n\n"
        f"⭐️ Зачислено: <b>{stars}</b>\n"
        f"💼 На балансе: <b>{balance}</b>"
    )


def crypto_pending() -> str:
    return "Оплата ещё не пришла. Если только что заплатил — подожди полминуты."


def crypto_failed(reason: str) -> str:
    return (
        "😔 <b>Счёт не выставился</b>\n\n"
        f"Ответ платёжной системы: <code>{reason}</code>\n\n"
        "Попробуй ещё раз или пополни звёздами Telegram."
    )
