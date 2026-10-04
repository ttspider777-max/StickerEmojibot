"""Мелкая механика показа экранов, общая для пользовательской части и админки."""

from __future__ import annotations

from typing import Optional

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)


async def drop(message: Optional[Message]) -> None:
    """Удаляет сообщение, молча переживая чужие ограничения.

    Удалять сообщения старше 48 часов Telegram не даёт, и падать из-за
    этого на ровном месте бот не должен.
    """
    if message is None:
        return
    try:
        await message.delete()
    except TelegramBadRequest:
        pass


async def show(
    callback: CallbackQuery,
    *,
    text: Optional[str] = None,
    photo: Optional[bytes] = None,
    caption: Optional[str] = None,
    markup: Optional[InlineKeyboardMarkup] = None,
) -> Optional[Message]:
    """Показывает экран, по возможности редактируя текущее сообщение.

    Текст и фото в Telegram не заменяют друг друга, поэтому при смене
    типа старое сообщение удаляем и шлём новое — иначе экран каталога
    остался бы висеть над каждым следующим шагом.
    """
    message = callback.message
    if message is None or not hasattr(message, "answer"):
        # Сообщение старше 48 часов приходит недоступным объектом: писать
        # в него нельзя, поэтому отвечаем отдельным сообщением в чат.
        return await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=text or caption or "",
            reply_markup=markup,
        )

    has_photo = bool(message.photo)

    if photo is not None:
        if has_photo:
            try:
                return await message.edit_media(
                    media=InputMediaPhoto(
                        media=BufferedInputFile(photo, filename="preview.png"),
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                    ),
                    reply_markup=markup,
                )
            except TelegramBadRequest:
                pass
        await drop(message)
        return await message.answer_photo(
            photo=BufferedInputFile(photo, filename="preview.png"),
            caption=caption,
            reply_markup=markup,
        )

    if not has_photo:
        try:
            return await message.edit_text(text or "", reply_markup=markup)
        except TelegramBadRequest:
            pass
    await drop(message)
    return await message.answer(text or "", reply_markup=markup)
