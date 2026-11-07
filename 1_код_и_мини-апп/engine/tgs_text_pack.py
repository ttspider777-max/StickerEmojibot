"""TGS TEXT PACK — движок подстановки текста в локальные TGS-шаблоны.

Модуль добавляет новый тип шаблона в существующую систему Black Hole,
не меняя её архитектуру. Он отвечает только за две вещи:

1. Источник шаблонов. В отличие от black_hole/color/passport, которые
   тянутся из Telegram-набора через get_sticker_set(short_name), этот пак
   лежит локально в папке TGS/. Здесь описан "shim" — объект, совместимый
   по утиной типизации с ответом get_sticker_set (у него есть .stickers,
   у каждого элемента .file_id и .emoji). Благодаря этому весь остальной
   конвейер бота (селектор, превью, премиум, очередь, создание пака,
   зеркала) работает с этим паком без единой правки.

2. Замену слова TEXT внутри Lottie.

Почему нельзя было переиспользовать customize_tgs_template
--------------------------------------------------------
В этих 200 файлах нет ни текстовых слоёв (ty:5), ни fonts/chars, ни имён
слоёв — слово TEXT нарисовано векторными контурами. При этом каждая буква
лежит отдельной подгруппой, её контуры заданы в em-пространстве шрифта
(~700 юнитов), а в слой она приводится собственным `tr` (масштаб ~7.7 % и
сдвиг по X).

_replace_textgroup из sticker_utils удаляет подгруппы букв ВМЕСТЕ с их
масштабирующим `tr` и кладёт 700-юнитовые контуры в неотмасштабированного
родителя. Итог — текст примерно в 13 раз больше нужного и уезжает за кадр
(визуально: надпись пропадает или торчит одна буква в углу).

Как ищется слово TEXT
---------------------
Глифы во всём паке геометрически идентичны, поэтому вместо эвристик
"похоже ли это на букву" используется точная сигнатура
(число вершин, ширина/высота):

    T = (8, 0.87)    E = (8, 0.77)    X = (16, 1.01)

Она находит слово в 200 файлах из 200. Замена делается в системе координат
группы-владельца, поэтому собственный `tr` группы, заливка, трансформация
слоя и вся анимация остаются нетронутыми — FPS, размер холста,
длительность и эффекты сохраняются.
"""

from __future__ import annotations

import copy
import gzip
import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import sticker_utils as _su

# --------------------------------------------------------------------------
# Константы
# --------------------------------------------------------------------------

#: Ключ шаблона в TEMPLATES (bot.py)
TEMPLATE_KEY = "tgs_text"

#: Папка с 200 исходными .tgs
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "TGS")

#: Префикс псевдо-file_id. Настоящие file_id Telegram так не начинаются,
#: поэтому коллизия с реальными файлами исключена.
LOCAL_FILE_ID_PREFIX = "tgstext:"

#: Эмодзи, присваиваемое каждому стикеру пака при загрузке в Telegram.
DEFAULT_EMOJI = "✨"

#: Слово, которое ищем и заменяем.
PLACEHOLDER_TEXT = "TEXT"

#: Сигнатуры глифов: (число вершин, round(ширина/высота, 2)) -> буква.
GLYPH_SIGNATURES: Dict[Tuple[int, float], str] = {
    (8, 0.87): "T",
    (8, 0.77): "E",
    (16, 1.01): "X",
}

#: Максимальная длина пользовательского текста.
MAX_TEXT_LENGTH = 20

#: Режимы раскладки надписи (пункт «настройки перед генерацией»).
WRAP_AUTO = "auto"       # сам выбирает 1 или 2 строки — как крупнее
WRAP_SINGLE = "single"   # всегда одна строка
WRAP_TWO = "two"         # принудительно две строки
WRAP_MODES = (WRAP_AUTO, WRAP_SINGLE, WRAP_TWO)

WRAP_MODE_LABELS = {
    WRAP_AUTO: "Автоматически",
    WRAP_SINGLE: "Всегда одна строка",
    WRAP_TWO: "Максимум две строки",
}

#: Доля высоты текстовой зоны, которая уходит на промежуток между строками.
LINE_GAP_RATIO = 0.12

#: Пределы ручной подстройки надписи (слайдеры «размер» и «высота»).
#: Размер — множитель текстовой зоны, высота — сдвиг в долях её высоты.
#: Рамки узкие намеренно: зона размечена под композицию шаблона, и при
#: большем размахе надпись уезжает за край стикера или на рисунок.
SIZE_SCALE_MIN, SIZE_SCALE_MAX, SIZE_SCALE_DEFAULT = 0.5, 1.5, 1.0
Y_OFFSET_MIN, Y_OFFSET_MAX, Y_OFFSET_DEFAULT = -0.6, 0.6, 0.0

#: Толщина контура вокруг рисунка — в долях стороны холста. У надписи
#: своя мера (высота букв), а контур обходит весь стикер, и привязывать
#: его к тексту неправильно: на шаблонах без надписи меры бы не было.
OUTLINE_WIDTH_MIN, OUTLINE_WIDTH_MAX, OUTLINE_WIDTH_DEFAULT = 0.004, 0.05, 0.014

#: Толщина обводки в долях высоты надписи, а не в пикселях: зоны у шаблонов
#: разного размера, и фиксированная толщина на мелком тексте съела бы буквы.
STROKE_WIDTH_MIN, STROKE_WIDTH_MAX, STROKE_WIDTH_DEFAULT = 0.01, 0.20, 0.06

#: Предел размера SVG-логотипа. Тот же, что у бота: контуры логотипа едут в
#: каждый из 200 стикеров, и тяжёлый файл упирается в лимит Telegram на
#: размер .tgs — отказать сразу понятнее, чем пропустить весь набор.
LOGO_MAX_BYTES = 256 * 1024


# --------------------------------------------------------------------------
# Разбор Lottie-свойств
# --------------------------------------------------------------------------

def _static(prop: Any, default: Any) -> Any:
    """Значение анимируемого свойства: статическое либо первый кейфрейм."""
    if not isinstance(prop, dict):
        return default
    k = prop.get("k")
    if isinstance(k, list) and k and isinstance(k[0], dict):
        s = k[0].get("s")
        return s if s is not None else default
    return k if k is not None else default


def _scalar(value: Any, default: float = 0.0) -> float:
    """Число из значения, которое может прийти списком ([r] у поворота)."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _identity_tr() -> dict:
    """Нейтральный shape-transform (ничего не двигает и не масштабирует)."""
    return {
        "ty": "tr",
        "p": {"a": 0, "k": [0, 0]},
        "a": {"a": 0, "k": [0, 0]},
        "s": {"a": 0, "k": [100, 100]},
        "r": {"a": 0, "k": 0},
        "o": {"a": 0, "k": 100},
        "sk": {"a": 0, "k": 0},
        "sa": {"a": 0, "k": 0},
    }


def _find_tr(items: Optional[List[dict]]) -> Optional[dict]:
    for item in items or []:
        if isinstance(item, dict) and item.get("ty") == "tr":
            return item
    return None


def _tr_mapper(tr: Optional[dict]):
    """Функция, переводящая точку через shape-transform группы."""
    if tr is None:
        return lambda point: point

    anchor = _static(tr.get("a"), [0, 0]) or [0, 0]
    position = _static(tr.get("p"), [0, 0]) or [0, 0]
    scale = _static(tr.get("s"), [100, 100]) or [100, 100]
    rotation = _scalar(_static(tr.get("r"), 0))

    ax = _scalar(anchor[0])
    ay = _scalar(anchor[1] if len(anchor) > 1 else 0)
    px = _scalar(position[0])
    py = _scalar(position[1] if len(position) > 1 else 0)
    sx = _scalar(scale[0], 100) / 100.0
    sy = _scalar(scale[1] if len(scale) > 1 else scale[0], 100) / 100.0

    radians = math.radians(rotation)
    cos_r, sin_r = math.cos(radians), math.sin(radians)

    def apply(point: Tuple[float, float]) -> Tuple[float, float]:
        x = (point[0] - ax) * sx
        y = (point[1] - ay) * sy
        if rotation:
            x, y = x * cos_r - y * sin_r, x * sin_r + y * cos_r
        return (x + px, y + py)

    return apply


def _path_vertices(shape: dict) -> List[Tuple[float, float]]:
    """Вершины безье-контура (`sh`), включая анимированный первый кейфрейм."""
    k = shape.get("ks", {}).get("k")
    if isinstance(k, list) and k and isinstance(k[0], dict):
        k = k[0].get("s")
        if isinstance(k, list) and k:
            k = k[0]
    if not isinstance(k, dict):
        return []
    return [(_scalar(v[0]), _scalar(v[1])) for v in (k.get("v") or [])]


def _glyph_of(vertices: List[Tuple[float, float]]) -> Optional[str]:
    """Буква по сигнатуре контура, либо None."""
    if not vertices:
        return None
    xs = [p[0] for p in vertices]
    ys = [p[1] for p in vertices]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    if width <= 0 or height <= 0:
        return None
    return GLYPH_SIGNATURES.get((len(vertices), round(width / height, 2)))


# --------------------------------------------------------------------------
# Поиск слова TEXT
# --------------------------------------------------------------------------

@dataclass
class TextGroup:
    """Найденное вхождение слова TEXT."""

    owner: dict                                   # группа-владелец букв
    letter_items: List[dict]                      # элементы, которые убираем
    bounds: Tuple[float, float, float, float]     # рамка в системе владельца


def find_text_groups(lottie: dict) -> List[TextGroup]:
    """Все вхождения слова TEXT в анимации.

    Многие шаблоны рисуют надпись несколько раз (тень, обводка, цветная
    подложка). Заменить нужно каждое вхождение, иначе старое слово
    останется видно позади нового.
    """
    found: List[TextGroup] = []

    def scan(group: dict) -> None:
        items = group.get("it", []) or []
        # (буква, вершины в системе владельца, элемент-держатель)
        glyphs: List[Tuple[str, List[Tuple[float, float]], dict]] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            if item.get("ty") == "sh":
                # Контур лежит прямо в группе.
                vertices = _path_vertices(item)
                letter = _glyph_of(vertices)
                if letter:
                    glyphs.append((letter, vertices, item))

            elif item.get("ty") == "gr":
                # Обычный случай: буква — вложенная подгруппа со своим `tr`,
                # приводящим em-координаты глифа в систему владельца.
                inner = item.get("it", []) or []
                to_owner = _tr_mapper(_find_tr(inner))
                for sub in inner:
                    if isinstance(sub, dict) and sub.get("ty") == "sh":
                        vertices = _path_vertices(sub)
                        letter = _glyph_of(vertices)
                        if letter:
                            glyphs.append((letter, [to_owner(p) for p in vertices], item))

        letters = [g for g, _, _ in glyphs]
        if letters.count("T") < 2 or "E" not in letters or "X" not in letters:
            return

        points = [p for _, verts, _ in glyphs for p in verts]
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]

        holders: List[dict] = []
        for _, _, holder in glyphs:
            if holder not in holders:
                holders.append(holder)

        found.append(TextGroup(
            owner=group,
            letter_items=holders,
            bounds=(min(xs), min(ys), max(xs), max(ys)),
        ))

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("ty") == "gr":
                scan(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(lottie)
    return found


# --------------------------------------------------------------------------
# Поиск запечённой подписи
# --------------------------------------------------------------------------

#: Во сколько раз буквы одного слова могут отличаться по высоте. Строчные
#: против прописных дают примерно двукратную разницу, «j» с хвостом —
#: чуть больше.
WORD_HEIGHT_TOLERANCE = 2.4

#: Максимальный разрыв между соседними буквами в долях высоты строки.
WORD_GAP_RATIO = 0.9

#: Допустимые пропорции контура-буквы (ширина к высоте). Всё, что шире
#: втрое, — это подложка или элемент рисунка, а не буква.
WORD_GLYPH_RATIO = (0.05, 3.0)

#: Сколько букв подряд считаем словом. Две — слишком легко собрать из
#: случайных деталей рисунка.
WORD_MIN_LETTERS = 3


def _contour_boxes(group: dict) -> List[Tuple[Tuple[float, float, float, float], dict]]:
    """Габариты всех контуров группы в её собственных координатах."""
    boxes: List[Tuple[Tuple[float, float, float, float], dict]] = []

    def add(vertices: List[Tuple[float, float]], holder: dict) -> None:
        if not vertices:
            return
        xs = [point[0] for point in vertices]
        ys = [point[1] for point in vertices]
        boxes.append(((min(xs), min(ys), max(xs), max(ys)), holder))

    for item in group.get("it", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("ty") == "sh":
            add(_path_vertices(item), item)
        elif item.get("ty") == "gr":
            inner = item.get("it", []) or []
            to_owner = _tr_mapper(_find_tr(inner))
            for sub in inner:
                if isinstance(sub, dict) and sub.get("ty") == "sh":
                    add([to_owner(point) for point in _path_vertices(sub)], item)
    return boxes


def _longest_run(boxes):
    """Самая длинная цепочка контуров, читающаяся как слово."""
    letters = []
    for box, holder in boxes:
        width = box[2] - box[0]
        height = box[3] - box[1]
        if height <= 0 or width <= 0:
            continue
        ratio = width / height
        if not (WORD_GLYPH_RATIO[0] <= ratio <= WORD_GLYPH_RATIO[1]):
            continue
        letters.append((box, holder))
    if len(letters) < WORD_MIN_LETTERS:
        return None

    letters.sort(key=lambda pair: pair[0][0])
    best = None
    for start in range(len(letters)):
        run = [letters[start]]
        for candidate in letters[start + 1:]:
            box = candidate[0]
            last = run[-1][0]
            height = max(last[3] - last[1], box[3] - box[1])
            low = min(last[3] - last[1], box[3] - box[1])
            if low <= 0 or height / low > WORD_HEIGHT_TOLERANCE:
                continue
            # Буквы стоят на одной строке: центры по вертикали рядом.
            if abs((box[1] + box[3]) / 2 - (last[1] + last[3]) / 2) > height * 0.6:
                continue
            # И идут подряд, без провалов в половину строки.
            if box[0] - last[2] > height * WORD_GAP_RATIO:
                continue
            run.append(candidate)
        if len(run) >= WORD_MIN_LETTERS and (best is None or len(run) > len(best)):
            best = run
    return best


def find_word_groups(lottie: dict) -> List[TextGroup]:
    """Запечённая подпись: цепочка контуров, стоящая в ряд как буквы.

    Первый детектор узнаёт буквы по таблице подписей и работает только со
    словом TEXT. В чужих паках вместо метки нарисовано своё слово —
    «Jade», «AYUB», «Nex9v», — и от остальной графики оно ничем не
    отличается: ни имени слоя, ни текстовой группы, в оптимизированном
    .tgs всё это вычищено. Зацепиться можно только за геометрию: буквы
    одного слова примерно одной высоты, сидят на общей строке и идут
    подряд с небольшими промежутками.

    Детектор намеренно осторожный: три буквы минимум, жёсткие рамки по
    пропорциям и разрывам. Лучше не узнать подпись и оставить шаблон
    как есть, чем принять за слово детали рисунка и стереть их.
    """
    found: List[TextGroup] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("ty") == "gr":
                run = _longest_run(_contour_boxes(node))
                if run:
                    xs = [box[0] for box, _ in run] + [box[2] for box, _ in run]
                    ys = [box[1] for box, _ in run] + [box[3] for box, _ in run]
                    holders: List[dict] = []
                    for _box, holder in run:
                        if holder not in holders:
                            holders.append(holder)
                    found.append(TextGroup(
                        owner=node,
                        letter_items=holders,
                        bounds=(min(xs), min(ys), max(xs), max(ys)),
                    ))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(lottie)
    # Слово вытянуто по горизонтали. Без этой проверки в подпись
    # записывалась любая кучка деталей подходящего размера — например
    # бахрома колпака, — и стиралась она, а не «Jade».
    words = [
        group for group in found
        if (group.bounds[2] - group.bounds[0]) >= (group.bounds[3] - group.bounds[1]) * 1.6
    ]
    if not words:
        return []
    # Из оставшегося берём самое длинное слово, при равенстве — крупное.
    best = max(words, key=lambda g: (
        len(g.letter_items),
        (g.bounds[2] - g.bounds[0]) * (g.bounds[3] - g.bounds[1]),
    ))

    # Подпись бывает из нескольких слов: «SO solutionss» — это два ряда
    # контуров, а не один. Раньше бралось только самое длинное слово, и на
    # стикере оставалось «SO» перед новой надписью. Берём всю строку:
    # соседей на той же линии и того же роста.
    line = [group for group in words if _same_text_line(group.bounds, best.bounds)]

    # И все копии строки. Шаблоны сплошь и рядом рисуют подпись дважды:
    # тень, обводка, цветная подложка — отдельными группами почти на том же
    # месте. Заменив только одну, мы получаем новую надпись поверх старой:
    # на превью это читалось как «Solęłions». То же самое делает и
    # find_text_groups, недаром она возвращает все вхождения.
    result = []
    for group in words:
        if any(_bounds_overlap(group.bounds, part.bounds) for part in line):
            result.append(group)
    return result or line


def _same_text_line(a, b) -> bool:
    """Стоят ли две группы контуров в одной строке подписи.

    Слова одной надписи сидят на общей строке и примерно одного роста, а
    промежуток между ними — это пробел, а не половина стикера. Пороги
    намеренно тесные: лучше не забрать соседнее слово, чем стереть деталь
    рисунка, оказавшуюся рядом.
    """
    a_height, b_height = a[3] - a[1], b[3] - b[1]
    if a_height <= 0 or b_height <= 0:
        return False

    # Рост слов в одной надписи различается из-за заглавных и хвостов
    # букв, но не в разы.
    if max(a_height, b_height) > min(a_height, b_height) * 2.2:
        return False

    # Общая строка: середины по вертикали расходятся меньше, чем на
    # половину высоты буквы.
    a_middle, b_middle = (a[1] + a[3]) / 2, (b[1] + b[3]) / 2
    if abs(a_middle - b_middle) > min(a_height, b_height) * 0.6:
        return False

    # Промежуток — это пробел между словами, а не расстояние до другой
    # части рисунка.
    gap = max(a[0], b[0]) - min(a[2], b[2])
    return gap <= min(a_height, b_height) * 1.6


def _bounds_overlap(a, b, threshold: float = 0.45) -> bool:
    """Пересекаются ли рамки достаточно, чтобы счесть их одной подписью."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return False
    overlap = width * height
    smallest = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return smallest > 0 and overlap / smallest >= threshold


def has_text_placeholder(tgs_bytes: bytes) -> bool:
    """Есть ли в файле заменяемое слово TEXT."""
    try:
        lottie = json.loads(gzip.decompress(tgs_bytes).decode("utf-8"))
    except Exception:
        return False
    return bool(find_text_groups(lottie))


# --------------------------------------------------------------------------
# Замена текста
# --------------------------------------------------------------------------

def _shapes_bounds(shapes: List[dict]):
    """Габариты набора контуров, либо None если контуров нет."""
    pts = []
    for sh in shapes:
        pts.extend(_path_vertices(sh))
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _glyph_height(shapes: List[dict]) -> float:
    """Фактическая высота отрисованных букв — метрика «насколько крупно»."""
    b = _shapes_bounds(shapes)
    return (b[3] - b[1]) if b else 0.0


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    """Число в заданных рамках. Мусор и None превращаются в значение по умолчанию."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:          # NaN
        return default
    return max(low, min(high, number))


def adjust_bounds(
    bounds: Tuple[float, float, float, float],
    size_scale: float = SIZE_SCALE_DEFAULT,
    y_offset: float = Y_OFFSET_DEFAULT,
) -> Tuple[float, float, float, float]:
    """Текстовая зона с учётом ручных «размера» и «высоты».

    Двигаем и масштабируем саму зону, а не готовые контуры: раскладка
    (одна строка или две, ужатие по ширине) считается уже по новой зоне,
    поэтому увеличенный текст остаётся правильно свёрстанным, а не просто
    растянутым. Масштабирование идёт от центра зоны, чтобы надпись не
    уползала вбок.
    """
    scale = _clamp(size_scale, SIZE_SCALE_MIN, SIZE_SCALE_MAX, SIZE_SCALE_DEFAULT)
    shift = _clamp(y_offset, Y_OFFSET_MIN, Y_OFFSET_MAX, Y_OFFSET_DEFAULT)
    if scale == 1.0 and shift == 0.0:
        return bounds

    x1, y1, x2, y2 = bounds
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    half_w = (x2 - x1) / 2.0 * scale
    half_h = (y2 - y1) / 2.0 * scale
    # Ось Y в Lottie направлена вниз, поэтому «поднять» — это вычесть:
    # у слайдера «высота» больше значит выше, как и ожидает пользователь.
    cy -= (y2 - y1) * shift
    return (cx - half_w, cy - half_h, cx + half_w, cy + half_h)


def build_text_shapes(
    text: str,
    font_path: str,
    bounds: Tuple[float, float, float, float],
    wrap_mode: str = WRAP_AUTO,
) -> List[dict]:
    """Раскладка надписи внутри текстовой зоны шаблона.

    Одна строка для длинной надписи означает сильное сжатие по ширине: текст
    остаётся в кадре, но становится нечитаемо мелким. Поэтому кандидаты
    (одна строка и две) строятся оба, у каждого измеряется фактическая высота
    букв, и выбирается тот, где буквы крупнее. Ничего не обрезается: перенос
    идёт по пробелу, а если слово одно — работает штатное ужатие по ширине.

    Текст остаётся отцентрованным по зоне: обе строки строятся симметрично
    относительно её центра, поэтому композиция шаблона не смещается.
    """
    text = (text or "").strip()
    if not text:
        return []

    x1, y1, x2, y2 = bounds
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    height = max(y2 - y1, 1.0)
    width = max(x2 - x1, 1.0)

    def render_line(line: str, center_y: float, line_height: float) -> List[dict]:
        """Контуры одной строки — обязательно СВОЯ копия.

        _text_to_lottie_shapes обёрнута @lru_cache и на одинаковые аргументы
        возвращает один и тот же объект списка с одними и теми же словарями.
        Любое изменение результата на месте испортило бы кеш для всех
        последующих вызовов (а _compact_lottie_numbers при упаковке .tgs
        правит числа именно на месте). Поэтому копируем.
        """
        return copy.deepcopy(
            _su._text_to_lottie_shapes(line, font_path, cx, center_y, line_height, max_width=width)
        )

    def one_line() -> List[dict]:
        return render_line(text, cy, height)

    def two_lines() -> List[dict]:
        # Переносим только по пробелу. _split_two_lines умеет рвать и посреди
        # слова, но «Xlorifov» → «Xlor / ifov» выглядит как ошибка вёрстки:
        # одно слово всегда остаётся одной строкой и просто ужимается.
        if " " not in text:
            return []
        line1, line2 = _su._split_two_lines(text)
        if not line1 or not line2:
            return []
        gap = height * LINE_GAP_RATIO
        line_h = (height - gap) / 2.0
        offset = (line_h + gap) / 2.0
        return render_line(line1, cy - offset, line_h) + render_line(line2, cy + offset, line_h)

    if wrap_mode == WRAP_SINGLE:
        return one_line()
    if wrap_mode == WRAP_TWO:
        return two_lines() or one_line()

    # WRAP_AUTO: берём вариант, где буквы получаются крупнее.
    single = one_line()
    double = two_lines()
    if not double:
        return single
    if not single:
        return double

    # У двух строк сравниваем высоту одной строки, а не всего блока.
    single_h = _glyph_height(single)
    double_h = _glyph_height(double) / 2.0
    return double if double_h > single_h * 1.05 else single


#: Насколько зона под логотип может стать выше исходной надписи.
#: Больше — и логотип начинает вылезать за пределы рисунка шаблона.
LOGO_ZONE_MAX_GROWTH = 2.2


def _squared_logo_zone(
    bounds: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    """Расширяет зону надписи по высоте под логотип без подписи.

    Слово TEXT занимает широкую и низкую полосу. Логотип вписывается в
    неё с сохранением пропорций, то есть упирается в высоту строки и
    выходит в несколько раз мельче, чем ожидает человек. Когда подписи
    под логотипом нет, зону имеет смысл добрать по высоте до квадрата —
    симметрично от центра, чтобы логотип остался там же, где была
    надпись. Рост ограничен: иначе на низких зонах логотип вылезал бы
    за пределы самой картинки шаблона.
    """
    x1, y1, x2, y2 = bounds
    width, height = x2 - x1, y2 - y1
    if height <= 0 or width <= height:
        return bounds
    target = min(width, height * LOGO_ZONE_MAX_GROWTH)
    center_y = (y1 + y2) / 2.0
    return (x1, center_y - target / 2.0, x2, center_y + target / 2.0)


def build_logo_shapes(
    logo_svg: bytes,
    text: str,
    font_path: str,
    bounds: Tuple[float, float, float, float],
    wrap_mode: str = WRAP_AUTO,
    keep_colors: bool = False,
) -> List[dict]:
    """Раскладка «логотип + подпись» внутри текстовой зоны шаблона.

    Зона делится тем же _brand_logo_and_text_bounds, что бот использует для
    своих брендовых шаблонов: логотип сверху, надпись под ним. Если текста
    нет — логотип занимает всю зону целиком. Своей геометрии не изобретаем,
    иначе логотип в боте и в Mini App стоял бы по-разному.

    _svg_to_lottie_shapes обёрнута @lru_cache и отдаёт один и тот же список
    словарей на повторяющиеся аргументы, а упаковщик .tgs правит числа на
    месте — поэтому копируем, как и для контуров букв.
    """
    if not (text or "").strip():
        bounds = _squared_logo_zone(bounds)
    logo_bounds, text_bounds = _su._brand_logo_and_text_bounds(bounds, text or "")
    shapes = copy.deepcopy(_su._svg_to_lottie_shapes(
        logo_svg, logo_bounds, padding_ratio=0.98, keep_colors=keep_colors,
    ))
    if text_bounds is not None:
        shapes += build_text_shapes(text, font_path, text_bounds, wrap_mode)
    return shapes


def validate_logo_svg(logo_svg: bytes) -> None:
    """Проверка SVG до генерации.

    Вызывать нужно один раз перед запуском пака: разбор внутри движка падал
    бы на каждом из 200 шаблонов по отдельности, и пользователь увидел бы
    «стикеры пропущены» вместо внятной причины.
    """
    _su.validate_svg_logo(logo_svg, max_bytes=LOGO_MAX_BYTES)


def hex_to_lottie_rgb(value: str) -> Optional[List[float]]:
    """#RRGGBB -> [r, g, b, 1] в долях единицы, как хранит Lottie."""
    raw = str(value or "").strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        return None
    try:
        r, g, b = (int(raw[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return None
    return [round(r, 4), round(g, 4), round(b, 4), 1]


def _apply_fill_color(style: List[dict], rgb: List[float]) -> None:
    """Перекрашивает заливку надписи, не трогая обводку.

    Меняем только `fl`: обводка (`st`) часто задаёт контур самого стикера,
    и её перекраска ломала бы рисунок. Градиенты (`gf`) тоже не трогаем —
    подменить их одним цветом значит потерять переход.
    """
    for item in style:
        if item.get("ty") == "fl" and isinstance(item.get("c"), dict):
            item["c"]["a"] = 0
            item["c"]["k"] = list(rgb)


def _make_fill(rgb: List[float]) -> dict:
    """Заливка для случая, когда у букв своей заливки не было."""
    return {"ty": "fl", "nm": "tgs_text_fill", "o": {"a": 0, "k": 100},
            "c": {"a": 0, "k": list(rgb)}, "r": 1}


def _make_stroke(rgb: List[float], width: float) -> dict:
    """Обводка надписи.

    Скруглённые концы и стыки (lc/lj = 2) — иначе на острых углах букв
    вылезают длинные пики, особенно заметные на толстой обводке.
    """
    return {"ty": "st", "nm": "tgs_text_stroke", "o": {"a": 0, "k": 100},
            "c": {"a": 0, "k": list(rgb)}, "w": {"a": 0, "k": round(width, 2)},
            "lc": 2, "lj": 2}


def _make_gradient_stroke(rgb_from: List[float], rgb_to: List[float],
                          width: float, box: Tuple[float, float, float, float]) -> dict:
    """Обводка с переходом цвета вдоль надписи.

    Точки начала и конца берём по диагонали габаритов самих букв, а не по
    холсту: у шаблонов зоны разного размера и в разных углах, и градиент,
    привязанный к холсту, на половине из них уходил бы за края надписи —
    оставалась бы видна одна крайняя краска перехода.
    """
    x1, y1, x2, y2 = box
    # В остановках градиента цвет идёт ТОЛЬКО тремя составляющими:
    # hex_to_lottie_rgb отдаёт ещё и альфу — если её не срезать, она
    # съезжает на место следующей позиции и переход выходит из чужих красок.
    stops = ([0.0] + [round(c, 4) for c in rgb_from[:3]]
             + [1.0] + [round(c, 4) for c in rgb_to[:3]])
    return {"ty": "gs", "nm": "tgs_text_stroke", "o": {"a": 0, "k": 100},
            "t": 1,
            "s": {"a": 0, "k": [round(x1, 2), round(y1, 2)]},
            "e": {"a": 0, "k": [round(x2, 2), round(y2, 2)]},
            "g": {"p": 2, "k": {"a": 0, "k": stops}},
            "w": {"a": 0, "k": round(width, 2)},
            "lc": 2, "lj": 2, "ml": 4,
            "h": {"a": 0, "k": 0}, "a": {"a": 0, "k": 0}}


def _apply_gradient_stroke(style: List[dict], shape: dict) -> List[dict]:
    """Ставит градиентную обводку вместо любой прежней.

    Обычную обводку здесь именно ВЫБРАСЫВАЕМ, а не перекрашиваем: у неё
    один цвет, и оставь мы её рядом — поверх перехода легла бы сплошная
    полоса и весь смысл градиента пропал бы.
    """
    kept = [item for item in style if item.get("ty") not in ("st", "gs")]
    return [shape] + kept


def _apply_stroke(style: List[dict], rgb: List[float], width: float) -> List[dict]:
    """Ставит обводку заданного цвета и толщины, обновляя уже имеющуюся.

    Обводка идёт ПЕРЕД заливкой. Lottie рисует стили группы в порядке
    списка, поэтому объявленная позже заливка ложится поверх обводки и
    наружу торчит только её внешняя половина. При обратном порядке обводка
    съедает букву изнутри: на толщине 0.16 от высоты «Тест» превращался в
    сплошное цветное пятно — это было видно на растеризации.
    """
    existing = [item for item in style if item.get("ty") == "st"]
    if existing:
        for item in existing:
            if isinstance(item.get("c"), dict):
                item["c"]["a"] = 0
                item["c"]["k"] = list(rgb)
            item["w"] = {"a": 0, "k": round(width, 2)}
            item["o"] = {"a": 0, "k": 100}
        return style
    return [_make_stroke(rgb, width)] + style


def _tint_shape_list(items: Any, rgb: List[float]) -> None:
    """Перекрашивает все заливки и обводки в один цвет, вглубь по группам."""
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = item.get("ty")
        if kind in ("fl", "st") and isinstance(item.get("c"), dict):
            item["c"]["a"] = 0
            item["c"]["k"] = list(rgb)
        elif kind in ("gf", "gs"):
            # У градиента красим каждую остановку: сохраняем позиции и
            # прозрачность, меняем только цвет. Так переход схлопывается в
            # ровную заливку, а не ломает форму.
            grad = item.get("g")
            colors = grad.get("k") if isinstance(grad, dict) else None
            values = colors.get("k") if isinstance(colors, dict) else None
            count = int(grad.get("p") or 0) if isinstance(grad, dict) else 0
            if isinstance(values, list) and count > 0:
                for stop in range(count):
                    base = stop * 4
                    if base + 3 < len(values):
                        values[base + 1] = rgb[0]
                        values[base + 2] = rgb[1]
                        values[base + 3] = rgb[2]
        elif kind == "gr":
            _tint_shape_list(item.get("it"), rgb)


def contrast_text_color(tint_color: Optional[str]) -> Optional[str]:
    """Цвет надписи, который будет виден на однотонном стикере.

    Когда весь рисунок перекрашен в один цвет, надпись без явно заданной
    заливки берёт цвет оттуда же — и пропадает. Поэтому, если человек цвет
    текста не выбирал, подставляем противоположный по светлоте: тёмный
    стикер получает белые буквы, светлый — почти чёрные.
    """
    rgb = hex_to_lottie_rgb(tint_color) if tint_color else None
    if rgb is None:
        return None
    # Светлота по восприятию: глаз считает зелёный намного ярче синего,
    # поэтому простое среднее давало серые буквы на ядовито-зелёном.
    luma = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    return "#14151A" if luma > 0.55 else "#FFFFFF"


#: Типы элементов Lottie, которые задают форму, и те, что задают краску.
_PATH_TYPES = ("sh", "el", "rc", "sr")
_PAINT_TYPES = ("fl", "gf", "st", "gs")


def _outline_group(items: Any, rgb: List[float], width: float) -> None:
    """Ставит контур вокруг форм одной группы, вглубь по вложенным."""
    if not isinstance(items, list):
        return

    has_path = False
    has_fill = False
    strokes: List[dict] = []
    first_paint = None

    for position, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        kind = item.get("ty")
        if kind in _PATH_TYPES:
            has_path = True
        elif kind in ("fl", "gf"):
            has_fill = True
            if first_paint is None:
                first_paint = position
        elif kind in ("st", "gs"):
            strokes.append(item)
            if first_paint is None:
                first_paint = position
        elif kind == "gr":
            _outline_group(item.get("it"), rgb, width)

    if not has_path:
        return

    if strokes:
        # У шаблона контур уже есть — перекрашиваем его, а не добавляем
        # второй поверх. Именно это и значит «заменить обводку».
        for stroke in strokes:
            if stroke.get("ty") == "gs":
                # Переход в контуре превращаем в ровный цвет: иначе один
                # выбранный цвет спорил бы с чужими красками перехода.
                stroke["ty"] = "st"
                stroke.pop("g", None)
                stroke.pop("s", None)
                stroke.pop("e", None)
                stroke.pop("t", None)
                stroke.pop("h", None)
                stroke.pop("a", None)
            colour = stroke.get("c")
            if not isinstance(colour, dict):
                stroke["c"] = {"a": 0, "k": list(rgb)}
            else:
                colour["a"] = 0
                colour["k"] = list(rgb)
            stroke["w"] = {"a": 0, "k": round(width, 2)}
            stroke["o"] = {"a": 0, "k": 100}
        return

    if not has_fill:
        # Ни заливки, ни обводки — это служебная группа (маска, обрезка),
        # и рисовать вокруг неё нечего.
        return

    # Контура не было — добавляем перед первой краской, чтобы заливка легла
    # поверх и наружу торчала только внешняя половина линии.
    stroke = {"ty": "st", "nm": "tgs_outline", "o": {"a": 0, "k": 100},
              "c": {"a": 0, "k": list(rgb)},
              "w": {"a": 0, "k": round(width, 2)}, "lc": 2, "lj": 2}
    items.insert(first_paint if first_paint is not None else len(items), stroke)


def apply_outline(lottie: dict, outline_color: Optional[str],
                  outline_width: Any = None) -> bool:
    """Обводит сам рисунок стикера линией заданного цвета.

    Это не обводка надписи: та идёт по буквам, а эта — по контурам
    картинки. Вместе с однотонной перекраской получается тот самый вид,
    когда стикер залит одним цветом, а по краю идёт яркая линия.

    Красим ПОСЛЕ перекраски: apply_tint сводит и заливки, и линии к одному
    цвету, и обводка, поставленная раньше, просто исчезла бы в нём.
    """
    rgb = hex_to_lottie_rgb(outline_color) if outline_color else None
    if rgb is None:
        return False

    ratio = _clamp(outline_width, OUTLINE_WIDTH_MIN, OUTLINE_WIDTH_MAX,
                   OUTLINE_WIDTH_DEFAULT)
    side = max(float(lottie.get("w") or 512), float(lottie.get("h") or 512))
    width = max(side * ratio, 1.0)

    for layer in lottie.get("layers", []) or []:
        if isinstance(layer, dict):
            _outline_group(layer.get("shapes"), rgb, width)
    for asset in lottie.get("assets", []) or []:
        if isinstance(asset, dict):
            for layer in asset.get("layers", []) or []:
                if isinstance(layer, dict):
                    _outline_group(layer.get("shapes"), rgb, width)
    return True


def apply_tint(lottie: dict, tint_color: Optional[str]) -> bool:
    """Делает стикер одноцветным, сохраняя рисунок и анимацию.

    Красим до подстановки надписи, а не после: иначе новый текст получил бы
    тот же цвет, что и картинка под ним, и на однотонном стикере его просто
    не было бы видно. Так рисунок становится силуэтом, а надпись остаётся
    управляемой отдельно — заливкой и обводкой.
    """
    rgb = hex_to_lottie_rgb(tint_color) if tint_color else None
    if rgb is None:
        return False
    for layer in lottie.get("layers", []) or []:
        if isinstance(layer, dict):
            _tint_shape_list(layer.get("shapes"), rgb)
    for asset in lottie.get("assets", []) or []:
        if isinstance(asset, dict):
            for layer in asset.get("layers", []) or []:
                if isinstance(layer, dict):
                    _tint_shape_list(layer.get("shapes"), rgb)
    return True


def replace_text_in_lottie(lottie: dict, new_text: str, font_path: str,
                           wrap_mode: str = WRAP_AUTO,
                           fill_color: Optional[str] = None,
                           logo_svg: Optional[bytes] = None,
                           stroke_color: Optional[str] = None,
                           stroke_width: float = STROKE_WIDTH_DEFAULT,
                           stroke_gradient: Optional[Dict[str, Any]] = None,
                           size_scale: float = SIZE_SCALE_DEFAULT,
                           y_offset: float = Y_OFFSET_DEFAULT,
                           logo_keep_colors: bool = False) -> int:
    """Заменяет каждое вхождение TEXT на new_text. Возвращает число замен.

    Собственный `tr` группы-владельца, трансформация слоя и вся анимация не
    трогаются, поэтому позиция, масштаб и движение сохраняются.

    fill_color задаёт цвет надписи. Если он не задан, надпись наследует
    цвет исходного слова TEXT — это важно для пака «Основной», где стикеры
    разноцветные и глобальная перекраска их бы испортила.

    stroke_color включает обводку, stroke_width задаёт её толщину в долях
    высоты надписи. size_scale и y_offset — ручная подстройка размера и
    положения надписи по вертикали.

    logo_svg подставляет в ту же зону векторный логотип: с текстом — сверху
    над ним, без текста — на всю зону. Логотип забирает цвет оттуда же,
    откуда его брала бы надпись, поэтому вписывается в шаблон.
    """
    rgb = hex_to_lottie_rgb(fill_color) if fill_color else None
    stroke_rgb = hex_to_lottie_rgb(stroke_color) if stroke_color else None
    # Градиент задаёт обводку сам и старший над одиночным цветом: если
    # выбраны оба, показываем переход — его выбирали позже и осознанно.
    gradient_from = gradient_to = None
    if isinstance(stroke_gradient, dict):
        gradient_from = hex_to_lottie_rgb(stroke_gradient.get("from"))
        gradient_to = hex_to_lottie_rgb(stroke_gradient.get("to"))
        if gradient_from is None or gradient_to is None:
            gradient_from = gradient_to = None
    replaced = 0

    # Сначала метка TEXT: там, где она есть, вопросов нет. Если её нет —
    # ищем запечённую чужую подпись и заменяем её же, чтобы на стикере не
    # осталось «Jade» рядом с новой надписью.
    groups = find_text_groups(lottie) or find_word_groups(lottie)

    for group in groups:
        # Ручные размер и высота меняют саму зону, поэтому раскладка ниже
        # считается уже по новой — текст остаётся свёрстанным, а не растянутым.
        bounds = adjust_bounds(group.bounds, size_scale, y_offset)

        # Векторизация текста существующим движком проекта + выбор раскладки
        # (одна строка или две) по фактическому размеру букв.
        if logo_svg:
            shapes = build_logo_shapes(
                logo_svg, new_text, font_path, bounds, wrap_mode,
                keep_colors=logo_keep_colors,
            )
        else:
            shapes = build_text_shapes(new_text, font_path, bounds, wrap_mode)
        if not shapes:
            continue

        items = group.owner.get("it", []) or []

        # Буквы могут нести собственную заливку вместо того, чтобы наследовать
        # её от владельца. Тогда её нужно перенести на новую надпись, иначе
        # текст останется без краски и просто исчезнет.
        style: List[dict] = []
        for holder in group.letter_items:
            if not isinstance(holder, dict) or holder.get("ty") != "gr":
                continue
            for sub in holder.get("it", []) or []:
                if isinstance(sub, dict) and sub.get("ty") in ("fl", "st", "gf", "gs"):
                    style.append(copy.deepcopy(sub))
            if style:
                break

        if rgb is not None:
            if style:
                _apply_fill_color(style, rgb)
            else:
                # У букв своей заливки не было — цвет приходил от владельца.
                # Добавляем собственную, иначе задать цвет нечему.
                style = [_make_fill(rgb)]

        if gradient_from is not None:
            ratio = _clamp(stroke_width, STROKE_WIDTH_MIN, STROKE_WIDTH_MAX,
                           STROKE_WIDTH_DEFAULT)
            width = max(_glyph_height(shapes) * ratio, 0.5)
            box = _shapes_bounds(shapes) or (0.0, 0.0, 1.0, 1.0)
            style = _apply_gradient_stroke(
                style, _make_gradient_stroke(gradient_from, gradient_to, width, box),
            )
        elif stroke_rgb is not None:
            # Толщина задана в долях высоты надписи, поэтому считаем её от
            # фактических габаритов уже собранных контуров: у шаблонов зоны
            # разного размера, и одна и та же доля даёт одинаковый вид.
            ratio = _clamp(stroke_width, STROKE_WIDTH_MIN, STROKE_WIDTH_MAX,
                           STROKE_WIDTH_DEFAULT)
            width = max(_glyph_height(shapes) * ratio, 0.5)
            # Своей заливки может не быть — она приходит от группы-владельца
            # и достаётся вложенной группе. Подставлять белую вместо неё
            # нельзя: это перекрасило бы надпись, которую не просили красить.
            style = _apply_stroke(style, stroke_rgb, width)

        kept = [item for item in items if item not in group.letter_items]

        if style:
            # Координаты глифов уже приведены к системе владельца,
            # поэтому контейнеру нужен нейтральный transform.
            group.owner["it"] = [{
                "ty": "gr",
                "nm": "tgs_text",
                "it": shapes + style + [_identity_tr()],
            }] + kept
        else:
            group.owner["it"] = shapes + kept

        replaced += 1

    if replaced == 0:
        # Запасной путь для шаблонов из чужих паков. Первый способ ищет
        # надпись по контурам букв T-E-X-T (GLYPH_SIGNATURES), и работает
        # только там, где в шаблон вшито именно слово TEXT. У паков со
        # своей подписью — «AYUB», «Nexov», «Jade», «Автор» — таких букв
        # нет, зато есть именованная группа TEXTGROUP/EMOJI, которой
        # пользуется движок sticker_utils. Берём её.
        if _replace_named_textgroup(lottie, new_text, font_path, logo_svg):
            replaced = 1

    return replaced


def normalize_zone(payload: Any) -> Optional[Dict[str, float]]:
    """Рамка текстовой зоны в долях холста: {x, y, w, h} из 0..1.

    Доли, а не пиксели: стикеры бывают 512×512 и 100×100, и рамка,
    записанная в пикселях, на втором размере уехала бы.
    """
    if not isinstance(payload, dict):
        return None
    try:
        x = float(payload.get("x"))
        y = float(payload.get("y"))
        w = float(payload.get("w"))
        h = float(payload.get("h"))
    except (TypeError, ValueError):
        return None

    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    # Совсем узкая рамка бессмысленна: надпись в неё либо не влезет, либо
    # станет нечитаемой. Ниже этого порога считаем, что зону не задали.
    w = min(max(w, 0.05), 1.0 - x)
    h = min(max(h, 0.05), 1.0 - y)
    if w < 0.05 or h < 0.05:
        return None
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def apply_text_in_zone(lottie: dict, new_text: str, font_path: str,
                       zone: Dict[str, float],
                       logo_svg: Optional[bytes] = None,
                       fill_color: Optional[str] = None) -> bool:
    """Кладёт надпись в заданную рамку отдельным слоем поверх рисунка.

    Это путь для чужих файлов из магазина. У них нет ни слова TEXT,
    нарисованного контурами, ни именованной группы — заменять нечего, и
    поиск по буквам тут бессилен. Зато автор сам показал рамкой, где
    надпись должна стоять, и этого достаточно.

    Слой добавляем первым в списке: Lottie рисует layers сверху вниз, и
    первый оказывается над картинкой, а не под ней.
    """
    if not zone:
        return False

    width = float(lottie.get("w", 512) or 512)
    height = float(lottie.get("h", 512) or 512)
    bounds = (
        zone["x"] * width,
        zone["y"] * height,
        (zone["x"] + zone["w"]) * width,
        (zone["y"] + zone["h"]) * height,
    )

    shapes = _su._compose_brand_shapes(
        new_text, font_path, bounds, logo_svg, canvas_size=(width, height),
    )
    if not shapes:
        return False

    # У букв нет собственной заливки: в шаблонах цвет приходит от
    # группы-владельца, а отдельный слой такого владельца не имеет — без
    # заливки контуры есть, а на экране пусто.
    if not any(isinstance(s, dict) and s.get("ty") in ("fl", "gf") for s in shapes):
        # По умолчанию чёрная, а не белая: стикеры лежат на прозрачном
        # фоне, и белая надпись пропадает в светлых чатах — а светлая тема
        # у Telegram по умолчанию. Свой цвет покупатель всё равно выберет.
        rgb = hex_to_lottie_rgb(fill_color) if fill_color else None
        shapes = shapes + [_make_fill(rgb if rgb is not None else [0.08, 0.08, 0.10])]

    layers = lottie.setdefault("layers", [])
    # Живём ровно столько же, сколько сама анимация: иначе надпись
    # мигала бы или пропадала в середине.
    in_point = min((float(l.get("ip", 0) or 0) for l in layers if isinstance(l, dict)), default=0.0)
    out_point = max((float(l.get("op", 0) or 0) for l in layers if isinstance(l, dict)),
                    default=float(lottie.get("op", 60) or 60))

    # Номер берём заведомо свободный. Перенумеровывать остальные нельзя:
    # слои ссылаются на родителей через ind, и сдвиг номеров рвёт все
    # эти связи разом — анимация разъезжается.
    free_ind = 1 + max(
        (int(l.get("ind", 0) or 0) for l in layers if isinstance(l, dict)),
        default=0,
    )

    layers.insert(0, {
        "ddd": 0,
        "ind": free_ind,
        "ty": 4,
        "nm": "solutions_text_zone",
        "sr": 1,
        "ks": {
            "o": {"a": 0, "k": 100},
            "r": {"a": 0, "k": 0},
            "p": {"a": 0, "k": [0, 0, 0]},
            "a": {"a": 0, "k": [0, 0, 0]},
            "s": {"a": 0, "k": [100, 100, 100]},
        },
        "ao": 0,
        "shapes": shapes,
        "ip": in_point,
        "op": out_point,
        "st": 0,
        "bm": 0,
    })

    return True


def customize_zone_to_lottie(
    tgs_bytes: bytes,
    text: str,
    zone: Dict[str, float],
    font_id: Optional[str] = None,
    logo_svg: Optional[bytes] = None,
    style: Optional[Dict[str, Any]] = None,
) -> dict:
    """Готовый стикер из чужого шаблона с отмеченной зоной.

    Отдельная функция, а не флаг в customize_to_lottie: там вся логика
    построена вокруг поиска существующей надписи, и подмешивать к ней
    ветку «надписи нет, рисуем свою» значило бы запутать обе.
    """
    lottie = lottie_from_tgs(tgs_bytes)
    font_path = _su.get_font_path(font_id or "montserrat") or _su._ensure_font()
    if font_path is None:
        raise RuntimeError("Не удалось загрузить шрифт для генерации текста.")

    # Исходную надпись, если она в шаблоне есть, убираем. Раньше рамка
    # просто клала новое слово поверх, и старое оставалось видно из-под
    # него: на карточке магазина «TEXT» и «Solutions» читались вместе, как
    # каша. Убираем ДО размещения новой — она встаёт на чистое место.
    erased_color = erase_existing_text(lottie)

    s = normalize_style(style)
    # Цвет стёртой заглушки — лучшая подсказка о том, каким автор задумал
    # текст. Без него надпись выходила тёмной на тёмном стикере.
    fill_color = erased_color
    if apply_tint(lottie, s["tint_color"]):
        fill_color = contrast_text_color(s["tint_color"])
    apply_outline(lottie, s["outline_color"], s["outline_width"])

    if not apply_text_in_zone(lottie, text, font_path, zone, logo_svg,
                              fill_color=fill_color):
        raise ValueError("Не удалось разместить надпись в заданной зоне.")
    return lottie


def erase_existing_text(lottie: dict) -> Optional[str]:
    """Стирает надпись, которая уже нарисована в шаблоне.

    Нужна только для режима с рамкой: там новая надпись кладётся своим
    слоем и старую собой не заменяет. Ищем теми же детекторами, что и
    обычная замена, — сначала слово TEXT по буквам, потом цепочку
    контуров, стоящую в ряд.

    Не нашли — и не надо: значит, в шаблоне надписи нет, и стирать нечего.

    Возвращает цвет стёртой надписи, если его удалось прочитать. Он и
    нужен новой: автор рисовал заглушку тем цветом, каким задумал текст,
    и без него надпись выходила тёмной на тёмном.
    """
    groups = find_text_groups(lottie) or find_word_groups(lottie)
    if not groups:
        return None

    colour: Optional[str] = None
    for group in groups:
        owner = group.owner
        items = owner.get("it")
        if not isinstance(items, list):
            continue
        if colour is None:
            colour = _group_fill_hex(items)
        kept = [item for item in items if item not in group.letter_items]
        if len(kept) != len(items):
            owner["it"] = kept
    return colour


def _group_fill_hex(items: List[Any]) -> Optional[str]:
    """Цвет заливки группы в виде #RRGGBB, если он там задан ровным цветом."""
    for item in items:
        if not isinstance(item, dict) or item.get("ty") != "fl":
            continue
        colour = item.get("c")
        if not isinstance(colour, dict):
            continue
        k = colour.get("k")
        if not isinstance(k, list) or len(k) < 3:
            continue
        try:
            return "#%02X%02X%02X" % tuple(
                max(0, min(255, int(round(float(v) * 255)))) for v in k[:3]
            )
        except (TypeError, ValueError):
            return None
    return None


#: Как в шаблоне нашлась надпись.
DETECT_GLYPHS = "glyphs"   #: по таблице подписей букв слова TEXT
DETECT_WORD = "word"       #: по геометрии — цепочка контуров в ряд
DETECT_NONE = ""           #: не нашлась, нужна рамка от автора


def detect_text_mode(tgs_bytes: bytes) -> str:
    """Ищет надпись в чужом шаблоне и говорит, каким способом нашлась.

    Нужна на приёме файла в магазин: если надпись нашлась сама, автор
    ничего не размечает, а замена идёт тем же путём, что и в наших
    шаблонах — с сохранением цвета, положения и анимации исходного слова.
    Рамку просим рисовать только тогда, когда не нашли ничего.

    Ошибки разбора здесь не поднимаем: файл уже проверен при приёме, а
    сорвать загрузку из-за неудачного поиска нельзя — на этот случай и
    есть ручная рамка.
    """
    try:
        lottie = lottie_from_tgs(tgs_bytes)
    except Exception:
        return DETECT_NONE
    try:
        if find_text_groups(lottie):
            return DETECT_GLYPHS
        if find_word_groups(lottie):
            return DETECT_WORD
    except Exception:
        return DETECT_NONE
    return DETECT_NONE


def customize_zone(
    tgs_bytes: bytes,
    text: str,
    zone: Dict[str, float],
    font_id: Optional[str] = None,
    logo_svg: Optional[bytes] = None,
    style: Optional[Dict[str, Any]] = None,
) -> bytes:
    """То же, что customize_zone_to_lottie, но сразу упакованный .tgs.

    Конвейеру генерации нужны байты стикера, а не объект Lottie: превью
    отдаётся в браузер разобранным, а в Telegram уходит gzip.
    """
    lottie = customize_zone_to_lottie(tgs_bytes, text, zone, font_id, logo_svg, style)
    return gzip.compress(
        json.dumps(lottie, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), 9,
    )


def _replace_named_textgroup(lottie: dict, new_text: str, font_path: str,
                             logo_svg: Optional[bytes] = None) -> bool:
    """Замена по именованной зоне, без перекраски всей анимации.

    sticker_utils.customize_tgs_template делает то же, но дополнительно
    зовёт tint_lottie: там это уместно, а здесь — нет. В этом паке стикеры
    разноцветные, и глобальный tint испортил бы жёлтую утку и зелёную
    жабу, поэтому красящий шаг сюда не переносится.

    Ползунки размера, высоты и обводки на такие шаблоны не действуют:
    зона задана самим шаблоном, а не найдена по буквам.
    """
    bounds = _su._get_textgroup_bounds(lottie)
    if bounds is None:
        return False

    canvas_size = (
        float(lottie.get("w", 512) or 512),
        float(lottie.get("h", 512) or 512),
    )
    shapes = _su._compose_brand_shapes(
        new_text, font_path, bounds, logo_svg, canvas_size=canvas_size,
    )
    if not shapes:
        return False

    main_replaced = _su._replace_textgroup(lottie, shapes)
    # Часть шаблонов рисует подпись автора отдельной группой — её движок
    # sticker_utils меняет тем же вызовом, иначе чужое имя осталось бы
    # висеть рядом с новой надписью.
    extra_replaced = _su._replace_username(lottie, new_text, font_path)
    return bool(main_replaced or extra_replaced)


def normalize_style(payload: Any) -> Dict[str, Any]:
    """Приводит настройки оформления из запроса к безопасному виду.

    Собраны в один словарь, а не в четыре отдельных аргумента: иначе хвост
    параметров тянулся бы через всю цепочку bot.py → creation → server, и
    каждый новый ползунок означал бы правку четырёх сигнатур.

    Всё, что пришло снаружи, здесь же и обрезается по допустимым рамкам —
    дальше по коду значениям можно доверять.
    """
    data = payload if isinstance(payload, dict) else {}
    stroke = data.get("stroke_color")
    # Цвет обводки принимаем только валидный: иначе обводка молча включилась
    # бы чёрной там, где её не просили.
    if stroke is not None and hex_to_lottie_rgb(stroke) is None:
        stroke = None
    # Градиент обводки принимаем только парой валидных цветов: одна
    # половина перехода — это не переход, а испорченная обводка.
    gradient = data.get("stroke_gradient")
    if isinstance(gradient, dict):
        start, end = gradient.get("from"), gradient.get("to")
        if hex_to_lottie_rgb(start) is None or hex_to_lottie_rgb(end) is None:
            gradient = None
        else:
            gradient = {"from": str(start), "to": str(end)}
    else:
        gradient = None

    tint = data.get("tint_color")
    if tint is not None and hex_to_lottie_rgb(tint) is None:
        tint = None

    outline = data.get("outline_color")
    if outline is not None and hex_to_lottie_rgb(outline) is None:
        outline = None

    return {
        "stroke_color": str(stroke) if stroke else None,
        "stroke_gradient": gradient,
        "tint_color": str(tint) if tint else None,
        "outline_color": str(outline) if outline else None,
        "outline_width": _clamp(data.get("outline_width"), OUTLINE_WIDTH_MIN,
                                OUTLINE_WIDTH_MAX, OUTLINE_WIDTH_DEFAULT),
        "stroke_width": _clamp(data.get("stroke_width"), STROKE_WIDTH_MIN,
                               STROKE_WIDTH_MAX, STROKE_WIDTH_DEFAULT),
        "size_scale": _clamp(data.get("size_scale"), SIZE_SCALE_MIN,
                             SIZE_SCALE_MAX, SIZE_SCALE_DEFAULT),
        "y_offset": _clamp(data.get("y_offset"), Y_OFFSET_MIN,
                           Y_OFFSET_MAX, Y_OFFSET_DEFAULT),
    }


def style_is_default(style: Optional[Dict[str, Any]]) -> bool:
    """Ничего не подстроено — можно не включать стиль в ключи кэша."""
    if not style:
        return True
    s = normalize_style(style)
    return (s["stroke_color"] is None
            and s["stroke_gradient"] is None
            and s["tint_color"] is None
            and s["outline_color"] is None
            and s["size_scale"] == SIZE_SCALE_DEFAULT
            and s["y_offset"] == Y_OFFSET_DEFAULT)


def style_cache_key(style: Optional[Dict[str, Any]]) -> str:
    """Короткая подпись настроек для ключа кэша готовых стикеров."""
    if style_is_default(style):
        return ""
    s = normalize_style(style)
    gradient = s["stroke_gradient"] or {}
    return "{0}|{1:.3f}|{2:.3f}|{3:.3f}|{4}>{5}|{6}|{7}@{8:.4f}".format(
        (s["stroke_color"] or "").upper(), s["stroke_width"],
        s["size_scale"], s["y_offset"],
        str(gradient.get("from") or "").upper(),
        str(gradient.get("to") or "").upper(),
        (s["tint_color"] or "").upper(),
        (s["outline_color"] or "").upper(), s["outline_width"],
    )


def lottie_from_tgs(tgs_bytes: bytes) -> dict:
    """Распакованная Lottie-анимация без каких-либо изменений.

    Нужна Mini App: браузер рисует анимацию сам через lottie-web, поэтому
    сервер отдаёт JSON и не запускает растеризацию.
    """
    return json.loads(gzip.decompress(tgs_bytes).decode("utf-8"))


def customize_to_lottie(
    tgs_bytes: bytes,
    text: str,
    font_id: Optional[str] = None,
    wrap_mode: str = WRAP_AUTO,
    fill_color: Optional[str] = None,
    logo_svg: Optional[bytes] = None,
    style: Optional[Dict[str, Any]] = None,
    logo_keep_colors: bool = False,
) -> dict:
    """То же, что customize(), но результат — объект Lottie, а не .tgs.

    Для превью в браузере упаковывать обратно в gzip незачем: это лишняя
    работа на сервере и лишний распаковщик на клиенте. Логика подстановки
    полностью общая с customize() — отдельного движка не появляется.
    """
    lottie = lottie_from_tgs(tgs_bytes)

    # Шрифт подбираем под саму надпись: в части гарнитур нет кириллицы,
    # и с русским ником они дают пустой контур вместо букв.
    font_path = _su.font_path_for_text(font_id or "montserrat", text) or _su._ensure_font()
    if font_path is None:
        raise RuntimeError("Не удалось загрузить шрифт для генерации текста.")

    s = normalize_style(style)
    if apply_tint(lottie, s["tint_color"]) and not fill_color:
        # Цвет текста человек не задавал — берём читаемый сами, иначе
        # надпись растворится в только что перекрашенном рисунке.
        fill_color = contrast_text_color(s["tint_color"])
    # Контур ставим ПОСЛЕ перекраски: apply_tint сводит и заливки, и линии
    # к одному цвету, и обводка, поставленная раньше, в нём бы растворилась.
    apply_outline(lottie, s["outline_color"], s["outline_width"])
    if not replace_text_in_lottie(
        lottie, text, font_path, wrap_mode, fill_color, logo_svg,
        s["stroke_color"], s["stroke_width"], s["stroke_gradient"],
        s["size_scale"], s["y_offset"],
        logo_keep_colors=logo_keep_colors,
    ):
        raise ValueError("Шаблон не содержит заменяемой надписи TEXT.")
    return lottie


def customize(
    tgs_bytes: bytes,
    text: str,
    font_id: str = "montserrat",
    enforce_size_limit: bool = True,
    wrap_mode: str = WRAP_AUTO,
    fill_color: Optional[str] = None,
    logo_svg: Optional[bytes] = None,
    style: Optional[Dict[str, Any]] = None,
    logo_keep_colors: bool = False,
) -> bytes:
    """Готовый .tgs с подставленным текстом.

    Сигнатура намеренно повторяет customize_tgs_template из sticker_utils,
    чтобы вызывающий код в bot.py выглядел единообразно.

    Цвет намеренно НЕ трогаем: в этом паке стикеры разноцветные (жёлтая
    утка, зелёная жаба, красное сердце), и глобальный tint_lottie их бы
    испортил. Надпись наследует цвет исходного слова TEXT.
    """
    lottie = json.loads(gzip.decompress(tgs_bytes).decode("utf-8"))

    # Шрифт подбираем под саму надпись: в части гарнитур нет кириллицы,
    # и с русским ником они дают пустой контур вместо букв.
    font_path = _su.font_path_for_text(font_id, text) or _su._ensure_font()
    if font_path is None:
        raise RuntimeError("Не удалось загрузить шрифт для генерации текста.")

    s = normalize_style(style)
    if apply_tint(lottie, s["tint_color"]) and not fill_color:
        # Цвет текста человек не задавал — берём читаемый сами, иначе
        # надпись растворится в только что перекрашенном рисунке.
        fill_color = contrast_text_color(s["tint_color"])
    # Контур ставим ПОСЛЕ перекраски: apply_tint сводит и заливки, и линии
    # к одному цвету, и обводка, поставленная раньше, в нём бы растворилась.
    apply_outline(lottie, s["outline_color"], s["outline_width"])
    if not replace_text_in_lottie(
        lottie, text, font_path, wrap_mode, fill_color, logo_svg,
        s["stroke_color"], s["stroke_width"], s["stroke_gradient"],
        s["size_scale"], s["y_offset"],
        logo_keep_colors=logo_keep_colors,
    ):
        raise ValueError("Шаблон не содержит заменяемой надписи TEXT.")

    data = _su._encode_lottie_tgs(lottie)
    if enforce_size_limit and len(data) > _su.MAX_ANIMATED_STICKER_BYTES:
        raise ValueError(
            f"animated sticker is too big after customization: "
            f"{len(data)} bytes > {_su.MAX_ANIMATED_STICKER_BYTES}"
        )

    # Контроль качества до того, как файл уйдёт в Telegram.
    problem = validate_tgs(data, check_size=enforce_size_limit)
    if problem:
        raise ValueError(f"проверка качества не пройдена: {problem}")
    return data


def validate_tgs(data: bytes, check_size: bool = True) -> Optional[str]:
    """Проверяет готовый .tgs. Возвращает описание проблемы либо None.

    Смысл — не выпустить наружу битый файл: Telegram на такой отвечает
    невнятной ошибкой уже на этапе загрузки, а пользователь получает
    наполовину собранный пак.
    """
    if not data:
        return "пустой файл"
    if check_size and len(data) > _su.MAX_ANIMATED_STICKER_BYTES:
        return f"превышен лимит Telegram: {len(data)} > {_su.MAX_ANIMATED_STICKER_BYTES}"
    try:
        raw = gzip.decompress(data)
    except Exception as exc:
        return f"не распаковывается gzip: {exc}"
    try:
        lottie = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        return f"невалидный JSON: {exc}"
    if not isinstance(lottie, dict):
        return "корень Lottie не объект"

    for key in ("v", "fr", "w", "h", "op", "layers"):
        if key not in lottie:
            return f"в Lottie нет обязательного поля {key!r}"
    if not lottie.get("layers"):
        return "в анимации не осталось слоёв"
    try:
        if float(lottie["fr"]) <= 0:
            return "некорректный FPS"
        if float(lottie["w"]) <= 0 or float(lottie["h"]) <= 0:
            return "некорректный размер холста"
        if float(lottie["op"]) <= 0:
            return "нулевая длительность анимации"
    except (TypeError, ValueError) as exc:
        return f"нечисловые параметры анимации: {exc}"

    # Надпись должна была замениться: исходного слова TEXT остаться не должно.
    if find_text_groups(lottie):
        return "в файле осталась незаменённая надпись TEXT"
    return None


# --------------------------------------------------------------------------
# Локальный источник шаблонов (shim под get_sticker_set)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class LocalTemplateSticker:
    """Минимальная замена telegram-объекта Sticker для локального шаблона."""

    file_id: str
    emoji: str = DEFAULT_EMOJI
    is_animated: bool = True
    is_video: bool = False


@dataclass(frozen=True)
class LocalTemplateSet:
    """Минимальная замена telegram-объекта StickerSet."""

    name: str
    title: str
    stickers: List[LocalTemplateSticker]


_TEMPLATE_FILES_CACHE: Optional[List[str]] = None


def reload_templates() -> List[str]:
    """Перечитывает список .tgs с диска (используется админкой)."""
    global _TEMPLATE_FILES_CACHE
    try:
        names = sorted(
            name for name in os.listdir(TEMPLATES_DIR)
            if name.lower().endswith(".tgs")
        )
    except FileNotFoundError:
        names = []
    _TEMPLATE_FILES_CACHE = names
    return names


def template_files() -> List[str]:
    """Имена файлов шаблонов в стабильном порядке (001.tgs, 002.tgs, ...)."""
    if _TEMPLATE_FILES_CACHE is None:
        return reload_templates()
    return _TEMPLATE_FILES_CACHE


def template_count() -> int:
    return len(template_files())


def local_file_id(index: int) -> str:
    """Псевдо-file_id для шаблона по его порядковому номеру (с нуля)."""
    return f"{LOCAL_FILE_ID_PREFIX}{index}"


def is_local_file_id(file_id: str) -> bool:
    return isinstance(file_id, str) and file_id.startswith(LOCAL_FILE_ID_PREFIX)


def load_local_bytes(file_id: str) -> bytes:
    """Байты шаблона по псевдо-file_id."""
    if not is_local_file_id(file_id):
        raise ValueError(f"not a local template file_id: {file_id!r}")
    try:
        index = int(file_id[len(LOCAL_FILE_ID_PREFIX):])
    except ValueError as exc:
        raise ValueError(f"malformed local template file_id: {file_id!r}") from exc

    names = template_files()
    if not 0 <= index < len(names):
        raise ValueError(f"local template index out of range: {index}")

    with open(os.path.join(TEMPLATES_DIR, names[index]), "rb") as fh:
        return fh.read()


def build_local_sticker_set(title: str = "TGS TEXT PACK") -> LocalTemplateSet:
    """Объект, совместимый по утиной типизации с ответом get_sticker_set."""
    stickers = [LocalTemplateSticker(file_id=local_file_id(i)) for i in range(template_count())]
    return LocalTemplateSet(name=TEMPLATE_KEY, title=title, stickers=stickers)


# --------------------------------------------------------------------------
# Нормализация текста и ключ кеша готовых наборов
# --------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """Схлопывает пробелы и регистр — для сравнения «тот же ли это текст».

    Нужно, чтобы «Xlorifov», «xlorifov » и «XLORIFOV» считались одним и тем
    же набором и переиспользовали уже созданный пак.
    """
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def pack_cache_key(text: str, indices: Optional[List[int]] = None) -> str:
    """Ключ кеша готового набора.

    Набор определяется текстом И составом выбранных шаблонов: одинаковый
    текст с разным набором номеров — это разные паки.
    """
    normalized = normalize_text(text)
    if indices:
        suffix = ",".join(str(i) for i in sorted(set(indices)))
    else:
        suffix = "all"
    return f"{normalized}|{suffix}"


def slug_for_text(text: str) -> str:
    """Базовое имя набора вида pack_xlorifov (только latin/digits).

    Кириллицу переводим в латиницу, а не вырезаем: раньше у человека с
    русской надписью от имени не оставалось ничего, все такие наборы
    получали одну и ту же заглушку и дрались за неё между собой.
    """
    base = re.sub(r"[^a-z0-9]+", "_",
                  _su.translit_to_latin(normalize_text(text))).strip("_")
    return f"pack_{base or 'text'}"
