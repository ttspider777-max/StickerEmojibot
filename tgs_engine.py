"""Работа со встроенными TGS-шаблонами: подстановка текста и превью.

Файл — единственная точка, через которую бот трогает Lottie. Всё, что
ниже (движок в engine/), про Telegram ничего не знает, а всё, что выше
(bot.py), не знает про устройство анимации.
"""

from __future__ import annotations

import io
import json
import logging
import os
import random
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

import config
from engine import sticker_utils as _su
from engine import tgs_text_pack as _tp

log = logging.getLogger(__name__)

#: Размер кадра в превью одного шаблона.
PREVIEW_SIZE = 256

#: Сколько кадров пробуем, выбирая самый наполненный.
SCAN_FRAMES = 9

#: Файл с найденными долями таймлайна: {"26": 0.25, …}
RATIO_CACHE_PATH = os.path.join(config.PREVIEWS_DIR, "frames.json")

_RATIOS: Optional[Dict[str, float]] = None

#: Цвета коллажей. Фон тёмный — под тему Telegram, а сами плитки серые:
#: часть шаблонов нарисована сплошным чёрным, часть сплошным белым, и на
#: середине серого видно и то, и другое.
BG_COLOR = (23, 33, 43)
TILE_COLOR = (128, 140, 152)
PICKED_TILE_COLOR = (156, 178, 200)
TEXT_COLOR = (233, 237, 240)
ACCENT_COLOR = (100, 181, 246)
MUTED_COLOR = (120, 140, 158)
BADGE_COLOR = (18, 26, 34)


# --------------------------------------------------------------------------
# Шрифты
# --------------------------------------------------------------------------

def register_fonts() -> None:
    """Прописывает встроенные шрифты в движок.

    Штатно sticker_utils качает гарнитуры из интернета. Боту это не
    подходит: шрифты лежат рядом с ним, и генерация не должна зависеть от
    сети. Пресеты с локальными путями подставляются первыми, поэтому
    загрузка не запускается вообще.
    """
    for font_id, (label, filename) in config.FONTS.items():
        path = os.path.join(config.FONTS_DIR, filename)
        if not os.path.exists(path):
            log.warning("Шрифт %s не найден: %s", font_id, path)
            continue
        _su._FONT_PRESETS[font_id] = {
            "label": label,
            "button": label,
            "filename": filename,
            "url": None,
            "local_candidates": [path],
            "fallback": None,
        }

    # Запасной шрифт тоже переводим на локальный файл: иначе при пропуске
    # кириллицы в выбранной гарнитуре движок полез бы в сеть.
    default_path = os.path.join(config.FONTS_DIR, config.FONTS[config.DEFAULT_FONT][1])
    if os.path.exists(default_path):
        preset = dict(_su._FONT_PRESETS.get("default") or {})
        preset.update({
            "filename": config.FONTS[config.DEFAULT_FONT][1],
            "url": None,
            "local_candidates": [default_path],
            "fallback": None,
        })
        _su._FONT_PRESETS["default"] = preset


register_fonts()


def user_font_path(user_id: int) -> Optional[str]:
    """Путь к шрифту, который пользователь загрузил сам."""
    for ext in config.FONT_EXTENSIONS:
        path = os.path.join(config.USER_FONTS_DIR, f"{user_id}{ext}")
        if os.path.exists(path):
            return path
    return None


def register_user_font(user_id: int, path: str) -> str:
    """Регистрирует личный шрифт пользователя и возвращает его id.

    Движок работает с идентификаторами пресетов, а не с путями, поэтому
    каждый загруженный файл получает собственный id ``u<user_id>``. Так
    личный шрифт одного человека не может подменить чужой.
    """
    font_id = f"u{user_id}"
    _su._FONT_PRESETS[font_id] = {
        "label": "Свой шрифт",
        "button": "Свой шрифт",
        "filename": os.path.basename(path),
        "url": None,
        "local_candidates": [path],
        "fallback": config.DEFAULT_FONT,
    }
    return font_id


def validate_font(data: bytes, filename: str) -> Optional[str]:
    """Проверяет присланный шрифт. Возвращает описание проблемы либо None."""
    if len(data) > config.MAX_FONT_BYTES:
        return "файл больше 5 МБ"
    if not filename.lower().endswith(config.FONT_EXTENSIONS):
        return "нужен файл .ttf или .otf"
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(io.BytesIO(data), fontNumber=0, lazy=True)
        cmap = font.getBestCmap() or {}
        font.close()
    except Exception as exc:
        return f"файл не читается как шрифт: {exc}"
    if not cmap:
        return "в шрифте нет таблицы символов"
    return None


def font_label(font_id: str) -> str:
    if font_id in config.FONTS:
        return config.FONTS[font_id][0]
    return "Свой шрифт"


# --------------------------------------------------------------------------
# Шаблоны
# --------------------------------------------------------------------------

def template_path(number: int) -> str:
    if number >= config.USER_TEMPLATE_BASE:
        # Шаблоны, добавленные пользователями через мини-приложение.
        return os.path.join(config.USER_TEMPLATES_DIR, f"{number - config.USER_TEMPLATE_BASE}.tgs")
    return os.path.join(config.TEMPLATES_DIR, f"{number:03d}.tgs")


def available_templates() -> List[int]:
    """Номера шаблонов, которые реально лежат на диске."""
    return [n for n in range(1, config.TEMPLATE_COUNT + 1) if os.path.exists(template_path(n))]


def user_template_path(template_id: int) -> str:
    return template_path(config.USER_TEMPLATE_BASE + template_id)


def validate_template(data: bytes) -> Optional[str]:
    """Проверяет присланный пользователем шаблон.

    Годится только анимация, в которой есть слово TEXT: именно его бот
    заменяет надписью. Проверка прогоняет настоящую подстановку, поэтому
    шаблон, который потом не соберётся, не пройдёт и сюда.
    """
    if not data:
        return "пустой файл"
    if len(data) > config.USER_TEMPLATE_MAX_BYTES:
        return f"файл больше {config.USER_TEMPLATE_MAX_BYTES // 1024} КБ"
    try:
        lottie = _tp.lottie_from_tgs(data)
    except Exception as exc:
        return f"это не .tgs: {exc}"
    try:
        if not _tp.find_text_groups(lottie):
            return "в анимации нет слоя с надписью TEXT"
    except Exception as exc:
        return f"не удалось найти надпись: {exc}"
    try:
        built = _tp.customize(data, "Test", font_id=config.DEFAULT_FONT)
    except Exception as exc:
        return f"подстановка текста не удалась: {exc}"
    return _tp.validate_tgs(built)


@lru_cache(maxsize=1024)
def load_template(number: int) -> bytes:
    with open(template_path(number), "rb") as fh:
        return fh.read()


def pack_templates(pack_id: str) -> List[int]:
    """Номера шаблонов внутри набора из config.PACKS."""
    pack = config.PACKS.get(pack_id) or {}
    low, high = pack.get("range", (1, config.TEMPLATE_COUNT))
    return [n for n in available_templates() if low <= n <= high]


def total_pages(pack_id: str) -> int:
    total = len(pack_templates(pack_id))
    return max(1, (total + config.PAGE_SIZE - 1) // config.PAGE_SIZE)


def page_templates(pack_id: str, page: int) -> List[int]:
    items = pack_templates(pack_id)
    start = page * config.PAGE_SIZE
    return items[start:start + config.PAGE_SIZE]


def random_templates(pack_id: str, count: int, seed: Optional[int] = None) -> List[int]:
    """Случайная выборка без повторов — один шаблон не приходит дважды."""
    items = pack_templates(pack_id)
    rnd = random.Random(seed)
    count = max(1, min(count, len(items)))
    return sorted(rnd.sample(items, count))


# --------------------------------------------------------------------------
# Генерация
# --------------------------------------------------------------------------

def build_emoji(
    number: int,
    text: str,
    font_id: str,
    logo_svg: Optional[bytes] = None,
) -> bytes:
    """Готовый .tgs с надписью или логотипом пользователя.

    Цвет надписи не задаём: она наследует цвет исходного слова TEXT, и
    разноцветные шаблоны остаются такими, какими их нарисовал автор.
    """
    return _tp.customize(
        load_template(number),
        text or "",
        font_id=font_id,
        logo_svg=logo_svg,
    )


def validate(data: bytes) -> Optional[str]:
    return _tp.validate_tgs(data)


def validate_logo(data: bytes) -> Optional[str]:
    """Проверяет присланный SVG. Возвращает описание проблемы либо None."""
    if len(data) > config.MAX_LOGO_BYTES:
        return "файл больше 256 КБ"
    try:
        _tp.validate_logo_svg(data)
    except Exception as exc:
        return str(exc)
    return None


# --------------------------------------------------------------------------
# Выбор кадра
# --------------------------------------------------------------------------

def _ink(image: Image.Image) -> int:
    """Сколько в кадре непрозрачных пикселей — мера «кадр не пустой»."""
    return sum(value * count for value, count in enumerate(image.getchannel("A").histogram()))


def _scan_best_ratio(tgs_bytes: bytes) -> float:
    """Доля таймлайна, на которой в кадре больше всего рисунка.

    Фиксированный кадр брать нельзя: часть шаблонов доигрывает уход и в
    хвосте пустая — именно поэтому превью шаблона 26 выходило пустой
    плиткой. Из кадров, близких к максимуму, берём самый поздний: к концу
    анимация уже собрана, а в начале часто идёт разлёт элементов.
    """
    from rlottie_python import LottieAnimation

    anim = LottieAnimation.from_data(
        json.dumps(_tp.lottie_from_tgs(tgs_bytes), separators=(",", ":")),
    )
    try:
        total = anim.lottie_animation_get_totalframe() or 1
        scored = []
        for step in range(SCAN_FRAMES):
            ratio = step / (SCAN_FRAMES - 1)
            frame = min(total - 1, int(total * ratio))
            scored.append((ratio, _ink(
                anim.render_pillow_frame(frame_num=frame, width=96, height=96),
            )))
    finally:
        anim.lottie_animation_destroy()

    best = max(ink for _, ink in scored)
    if best <= 0:
        return 0.0
    return [ratio for ratio, ink in scored if ink >= best * 0.95][-1]


def _ratio_cache() -> Dict[str, float]:
    global _RATIOS
    if _RATIOS is None:
        try:
            with open(RATIO_CACHE_PATH, encoding="utf-8") as fh:
                _RATIOS = json.load(fh)
        except Exception:
            _RATIOS = {}
    return _RATIOS


def frame_ratio(number: int) -> float:
    """Кэш «лучшего кадра» шаблона.

    Считается один раз на шаблон: подстановка текста таймлайн не трогает,
    поэтому у готового эмодзи удачный кадр тот же, что у исходника.
    """
    cache = _ratio_cache()
    key = str(number)
    if key not in cache:
        cache[key] = _scan_best_ratio(load_template(number))
        try:
            with open(RATIO_CACHE_PATH, "w", encoding="utf-8") as fh:
                json.dump(cache, fh)
        except OSError as exc:
            log.warning("Кэш кадров не записался: %s", exc)
    return float(cache[key])


# --------------------------------------------------------------------------
# Растровые превью
# --------------------------------------------------------------------------

def _render_frame(tgs_bytes: bytes, size: int = PREVIEW_SIZE,
                  ratio: Optional[float] = None) -> Image.Image:
    """Кадр анимации в виде RGBA-картинки."""
    from rlottie_python import LottieAnimation

    anim = LottieAnimation.from_data(
        json.dumps(_tp.lottie_from_tgs(tgs_bytes), separators=(",", ":")),
    )
    try:
        total = anim.lottie_animation_get_totalframe() or 1
        frame = min(total - 1, max(0, int(total * (0.66 if ratio is None else ratio))))
        return anim.render_pillow_frame(frame_num=frame, width=size, height=size)
    finally:
        anim.lottie_animation_destroy()


def render_png(tgs_bytes: bytes, size: int = 512, number: Optional[int] = None) -> bytes:
    """PNG-превью анимации — им бот показывает результат до сборки набора."""
    frame = _render_frame(tgs_bytes, size, frame_ratio(number) if number else None)
    canvas = Image.new("RGBA", (size, size), BG_COLOR + (255,))
    canvas.alpha_composite(frame)
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _slug(value: str) -> str:
    """Имя файла кэша из надписи — только безопасные символы."""
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower())[:24] or "plain"


def preview_path(number: int, sample: str = "") -> str:
    return os.path.join(config.PREVIEWS_DIR, f"{number:03d}_{_slug(sample)}.png")


def ensure_preview(number: int, sample: str = "") -> str:
    """Кэш превью шаблона на диске.

    sample — надпись, которая рисуется внутри вместо шаблонного TEXT: в
    каталоге показываем не заглушку, а юзернейм бота, то есть ровно то,
    что человек и получит. Считается один раз на пару «шаблон + надпись».
    """
    path = preview_path(number, sample)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path

    data = load_template(number)
    if sample:
        try:
            data = build_emoji(number, sample, config.DEFAULT_FONT)
        except Exception as exc:
            # Не собралось с надписью — рисуем шаблон как есть: плитка со
            # словом TEXT лучше, чем пустая дырка в каталоге.
            log.warning("Превью %s с надписью не собралось: %s", number, exc)

    _render_frame(data, PREVIEW_SIZE, frame_ratio(number)).save(path, format="PNG")
    return path


@lru_cache(maxsize=8)
def _label_font(size: int):
    path = os.path.join(config.FONTS_DIR, config.FONTS[config.DEFAULT_FONT][1])
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _centered(draw: ImageDraw.ImageDraw, x: float, y: float, width: float,
              text: str, font, fill) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text((x + (width - (bbox[2] - bbox[0])) / 2, y), text, font=font, fill=fill)


def _badge(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, fill, text_color,
           size: int = 22) -> None:
    """Кружок с номером в углу плитки."""
    font = _label_font(size)
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    radius = max(15, int(max(w, h) * 0.85))
    draw.ellipse([x, y, x + radius * 2, y + radius * 2], fill=fill)
    draw.text((x + radius - w / 2 - bbox[0], y + radius - h / 2 - bbox[1]),
              text, font=font, fill=text_color)


def _check(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
    """Галочка в кружке.

    Рисуется линиями, а не символом: знака «✓» нет ни в одной из
    встроенных гарнитур, и вместо него оставался пустой кружок.
    """
    draw.ellipse([x, y, x + size, y + size], fill=ACCENT_COLOR)
    draw.line(
        [(x + size * 0.26, y + size * 0.52),
         (x + size * 0.44, y + size * 0.70),
         (x + size * 0.76, y + size * 0.32)],
        fill=(255, 255, 255), width=max(3, size // 10), joint="curve",
    )


def _watermark(canvas: Image.Image, label: str) -> Image.Image:
    """Диагональная подпись поверх превью.

    Ставится только на витринах до оплаты: показать, как выглядит
    результат, но не отдать готовую картинку даром.
    """
    if not label:
        return canvas
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = _label_font(28)
    bbox = draw.textbbox((0, 0), label, font=font)
    step_x = (bbox[2] - bbox[0]) + 90
    step_y = (bbox[3] - bbox[1]) + 90
    for row, y in enumerate(range(-step_y, canvas.size[1] + step_y, step_y)):
        offset = (row % 2) * step_x // 2
        for x in range(-step_x, canvas.size[0] + step_x, step_x):
            draw.text((x + offset, y), label, font=font, fill=(255, 255, 255, 38))
    layer = layer.rotate(30, resample=Image.BICUBIC)
    return Image.alpha_composite(canvas.convert("RGBA"), layer)


def _grid_canvas(cols: int, rows: int, tile: int, label_h: int) -> Tuple[Image.Image, int, int, int]:
    pad, gap = 16, 12
    width = pad * 2 + cols * tile + (cols - 1) * gap
    height = pad * 2 + rows * (tile + label_h) + (rows - 1) * gap
    return Image.new("RGB", (width, height), BG_COLOR), pad, gap, label_h


def _to_png(canvas: Image.Image) -> bytes:
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def selection_grid(pack_id: str, page: int, sample: str = "",
                   selected: Optional[Sequence[int]] = None) -> Tuple[bytes, List[int]]:
    """Страница ручного выбора: 12 шаблонов сеткой 4×3.

    Номер сидит кружком в углу самой плитки, а выбранное подсвечено прямо
    на картинке. Иначе приходится глазами сверять номера под сеткой с
    номерами на кнопках — это и есть главное неудобство такого экрана.
    """
    numbers = page_templates(pack_id, page)
    chosen = set(selected or ())
    cols, rows, tile = 4, 3, 190
    canvas, pad, gap, _ = _grid_canvas(cols, rows, tile, 0)
    draw = ImageDraw.Draw(canvas)

    for idx, number in enumerate(numbers):
        col, row = idx % cols, idx // cols
        x = pad + col * (tile + gap)
        y = pad + row * (tile + gap)
        picked = number in chosen

        draw.rounded_rectangle(
            [x, y, x + tile, y + tile], radius=16,
            fill=PICKED_TILE_COLOR if picked else TILE_COLOR,
            outline=ACCENT_COLOR if picked else None, width=4,
        )
        try:
            frame = Image.open(ensure_preview(number, sample)).convert("RGBA").resize((tile, tile))
            canvas.paste(frame, (x, y), frame)
        except Exception as exc:  # пустая плитка лучше, чем упавшая страница
            log.warning("Превью шаблона %s не построилось: %s", number, exc)

        _badge(draw, x + 6, y + 6, str(number),
               ACCENT_COLOR if picked else BADGE_COLOR, (255, 255, 255))
        if picked:
            _check(draw, x + tile - 46, y + 6, 40)

    return _to_png(canvas), numbers


def font_showcase(pack_id: str, sample_text: str, watermark: str,
                  custom_font_id: Optional[str] = None) -> Tuple[bytes, Dict[str, int]]:
    """Витрина шрифтов: по одному случайному эмодзи на каждый шрифт.

    Так человек видит не название гарнитуры, а как она реально ложится в
    анимацию — с его собственным текстом, а не абстрактным образцом.
    Возвращает картинку и раскладку «шрифт → показанный шаблон», чтобы
    подпись под кнопкой совпадала с тем, что нарисовано.
    """
    font_ids = list(config.FONTS.keys())
    if custom_font_id:
        font_ids.append(custom_font_id)

    numbers = random_templates(pack_id, len(font_ids))
    cols = 3
    rows = (len(font_ids) + cols - 1) // cols
    tile, label_h = 240, 34
    canvas, pad, gap, label_h = _grid_canvas(cols, rows, tile, label_h)
    draw = ImageDraw.Draw(canvas)
    font = _label_font(24)
    layout: Dict[str, int] = {}

    for idx, font_id in enumerate(font_ids):
        number = numbers[idx % len(numbers)]
        layout[font_id] = number
        col, row = idx % cols, idx // cols
        x = pad + col * (tile + gap)
        y = pad + row * (tile + label_h + gap)
        draw.rounded_rectangle([x, y, x + tile, y + tile], radius=16, fill=TILE_COLOR)
        try:
            frame = _render_frame(
                build_emoji(number, sample_text, font_id), tile, frame_ratio(number),
            )
            canvas.paste(frame, (x, y), frame)
        except Exception as exc:
            log.warning("Витрина шрифта %s не построилась: %s", font_id, exc)
        _centered(draw, x, y + tile + 6, tile, font_label(font_id), font, TEXT_COLOR)

    return _to_png(_watermark(canvas, watermark)), layout


def result_grid(items: Sequence[Tuple[int, bytes]], columns: int = 4) -> bytes:
    """Общая картинка заказа: все выбранные эмодзи одной сеткой.

    Показывать 30 отдельных превью в чате невозможно, а по одной сетке
    сразу видно, что именно уйдёт в набор.
    """
    items = list(items)
    cols = min(columns, max(1, len(items)))
    rows = (len(items) + cols - 1) // cols
    tile = 180
    canvas, pad, gap, _ = _grid_canvas(cols, rows, tile, 0)
    draw = ImageDraw.Draw(canvas)

    for idx, (number, data) in enumerate(items):
        col, row = idx % cols, idx // cols
        x = pad + col * (tile + gap)
        y = pad + row * (tile + gap)
        draw.rounded_rectangle([x, y, x + tile, y + tile], radius=14, fill=TILE_COLOR)
        try:
            frame = _render_frame(data, tile, frame_ratio(number))
            canvas.paste(frame, (x, y), frame)
        except Exception as exc:
            log.warning("Превью результата %s не построилось: %s", number, exc)
        _badge(draw, x + 6, y + 6, str(number), BADGE_COLOR, (255, 255, 255), size=18)

    return _to_png(canvas)
