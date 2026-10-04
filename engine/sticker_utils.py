import glob
import gzip
import io
import json
import logging
import os
import re
import time
import subprocess
import tempfile
import urllib.request
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("JellyColorBot")

_FONT_SEARCH = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    "/usr/local/share/fonts/NotoSans-Bold.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\calibrib.ttf",
    r"C:\Windows\Fonts\seguibl.ttf",
    r"C:\Windows\Fonts\seguisb.ttf",
]
_CACHED_FONT_PATH = os.path.join(tempfile.gettempdir(), "jelly_color_font.ttf")
_FONT_CACHE_DIR = os.path.join(tempfile.gettempdir(), "emoji_creation_fonts")
_FONT_CDN_URL = (
    "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/"
    "hinted/ttf/NotoSans/NotoSans-Bold.ttf"
)
_FONT_PRESETS: Dict[str, Dict[str, Optional[str]]] = {
    "montserrat": {
        "label": "Montserrat ExtraBold",
        "button": "🔤 Montserrat",
        "filename": "Montserrat-ExtraBold.ttf",
        "url": "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-ExtraBold.ttf",
        "local_candidates": [
            r"C:\Windows\Fonts\seguisb.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
        ],
        "fallback": "default",
    },
    "bebas_neue": {
        "label": "Bebas Neue",
        "button": "🏋️ Bebas Neue",
        "filename": "BebasNeue-Regular.ttf",
        "url": "https://github.com/dharmatype/Bebas-Neue/raw/master/fonts/BebasNeue(OFLv1.1)/TTF/BebasNeue-Regular.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "oswald": {
        "label": "Oswald Bold",
        "button": "⚡ Oswald",
        "filename": "Oswald-Bold.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/oswald/static/Oswald-Bold.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "russo_one": {
        "label": "Russo One",
        "button": "🔴 Russo One",
        "filename": "RussoOne-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/russoone/RussoOne-Regular.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "press_start": {
        "label": "Press Start 2P",
        "button": "👾 Press Start 2P",
        "filename": "PressStart2P-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/pressstart2p/PressStart2P-Regular.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "bungee": {
        "label": "Bungee",
        "button": "🚀 Bungee",
        "filename": "Bungee-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/bungee/Bungee-Regular.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "righteous": {
        "label": "Righteous",
        "button": "✨ Righteous",
        "filename": "Righteous-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/righteous/Righteous-Regular.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "audiowide": {
        "label": "Audiowide",
        "button": "🎵 Audiowide",
        "filename": "Audiowide-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/audiowide/Audiowide-Regular.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "orbitron": {
        "label": "Orbitron Bold",
        "button": "🛸 Orbitron",
        "filename": "Orbitron-Bold.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/orbitron/static/Orbitron-Bold.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "exo2": {
        "label": "Exo 2 ExtraBold",
        "button": "💠 Exo 2",
        "filename": "Exo2-ExtraBold.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/exo2/static/Exo2-ExtraBold.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "rajdhani": {
        "label": "Rajdhani Bold",
        "button": "⚔️ Rajdhani",
        "filename": "Rajdhani-Bold.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/rajdhani/Rajdhani-Bold.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "teko": {
        "label": "Teko Bold",
        "button": "🔲 Teko",
        "filename": "Teko-Bold.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/teko/static/Teko-Bold.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "rubik_glitch": {
        "label": "Rubik Glitch",
        "button": "⚡ Rubik Glitch",
        "filename": "RubikGlitch-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/rubikglitch/RubikGlitch-Regular.ttf",
        "local_candidates": [
            r"C:\Windows\Fonts\impact.ttf",
        ],
        "fallback": "default",
    },
    "space_mono": {
        "label": "Space Mono Bold",
        "button": "💻 Space Mono",
        "filename": "SpaceMono-Bold.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/spacemono/SpaceMono-Bold.ttf",
        "local_candidates": [
            r"C:\Windows\Fonts\cour.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        ],
        "fallback": "default",
    },
    "share_tech_mono": {
        "label": "Share Tech Mono",
        "button": "🖥️ Share Tech Mono",
        "filename": "ShareTechMono-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/sharetechmono/ShareTechMono-Regular.ttf",
        "local_candidates": [
            r"C:\Windows\Fonts\cour.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ],
        "fallback": "default",
    },
    "russo_one": {
        "label": "Russo One",
        "button": "🔴 Russo One",
        "filename": "RussoOne-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/russoone/RussoOne-Regular.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "ballet": {
        "label": "Ballet Regular",
        "button": "🩰 Ballet",
        "filename": "Ballet-Regular.ttf",
        "url": None,
        "local_candidates": [],
        "fallback": "default",
    },
    "anton": {
        "label": "Anton",
        "button": "🅰️ Anton",
        "filename": "Anton-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "archivo_black": {
        "label": "Archivo Black",
        "button": "⬛ Archivo Black",
        "filename": "ArchivoBlack-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/archivoblack/ArchivoBlack-Regular.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "unbounded": {
        "label": "Unbounded",
        "button": "🌐 Unbounded",
        "filename": "Unbounded-Variable.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/unbounded/Unbounded%5Bwght%5D.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "montserrat_alt": {
        "label": "Montserrat Alternates",
        "button": "🔡 Montserrat Alt",
        "filename": "MontserratAlternates-ExtraBold.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/montserratalternates/MontserratAlternates-ExtraBold.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "ubuntu": {
        "label": "Ubuntu Bold",
        "button": "🟠 Ubuntu",
        "filename": "Ubuntu-Bold.ttf",
        "url": "https://github.com/google/fonts/raw/main/ufl/ubuntu/Ubuntu-Bold.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "play": {
        "label": "Play Bold",
        "button": "🎮 Play",
        "filename": "Play-Bold.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/play/Play-Bold.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "philosopher": {
        "label": "Philosopher Bold",
        "button": "🏛️ Philosopher",
        "filename": "Philosopher-Bold.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/philosopher/Philosopher-Bold.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "days_one": {
        "label": "Days One",
        "button": "📅 Days One",
        "filename": "DaysOne-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/daysone/DaysOne-Regular.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "yeseva_one": {
        "label": "Yeseva One",
        "button": "👑 Yeseva One",
        "filename": "YesevaOne-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/yesevaone/YesevaOne-Regular.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "lobster": {
        "label": "Lobster",
        "button": "🦞 Lobster",
        "filename": "Lobster-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/lobster/Lobster-Regular.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "pacifico": {
        "label": "Pacifico",
        "button": "🌊 Pacifico",
        "filename": "Pacifico-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/pacifico/Pacifico-Regular.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "bad_script": {
        "label": "Bad Script",
        "button": "✍️ Bad Script",
        "filename": "BadScript-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/badscript/BadScript-Regular.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "neucha": {
        "label": "Neucha",
        "button": "📝 Neucha",
        "filename": "Neucha.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/neucha/Neucha.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "marmelad": {
        "label": "Marmelad",
        "button": "🍬 Marmelad",
        "filename": "Marmelad-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/marmelad/Marmelad-Regular.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "ruslan": {
        "label": "Ruslan Display",
        "button": "🏰 Ruslan",
        "filename": "RuslanDisplay-Regular.ttf",
        "url": "https://github.com/google/fonts/raw/main/ofl/ruslandisplay/RuslanDisplay-Regular.ttf",
        "local_candidates": [],
        "fallback": "default",
    },
    "default": {
        "label": "Noto Sans Bold",
        "button": "🔤 Noto Sans Bold",
        "filename": "NotoSans-Bold.ttf",
        "url": _FONT_CDN_URL,
        "local_candidates": _FONT_SEARCH,
        "fallback": None,
    },
}
MAX_STATIC_STICKER_BYTES = 512 * 1024
MAX_ANIMATED_STICKER_BYTES = 64 * 1024
MAX_VIDEO_STICKER_BYTES = 256 * 1024
MAX_VIDEO_STICKER_DURATION_SEC = 2.99
MAX_VIDEO_STICKER_FPS = 30
LOTTIE_FLOAT_PRECISION = 3
_FONTOOLS_CLASSES: Optional[Tuple[Any, Any, Any]] = None
_FONTOOLS_IMPORT_ERROR: Optional[str] = None
_FFMPEG_EXE_PATH: Optional[str] = None
_FFMPEG_EXE_ERROR: Optional[str] = None
_SVG_CLASSES: Optional[Tuple[Any, Any, Any, Any, Any, Any, Any]] = None
_SVG_IMPORT_ERROR: Optional[str] = None
_RLOTTIE_CLASS: Optional[Any] = None
_RLOTTIE_IMPORT_ERROR: Optional[str] = None


def _compact_lottie_numbers(value: Any, digits: int = LOTTIE_FLOAT_PRECISION) -> Any:
    if isinstance(value, float):
        rounded = round(value, digits)
        if abs(rounded) < 10 ** (-digits):
            return 0
        rounded_int = int(rounded)
        return rounded_int if abs(rounded - rounded_int) < 10 ** (-(digits + 2)) else rounded
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _compact_lottie_numbers(item, digits)
        return value
    if isinstance(value, dict):
        for key, item in value.items():
            value[key] = _compact_lottie_numbers(item, digits)
        return value
    return value


def _encode_lottie_tgs(lottie: dict) -> bytes:
    compact = _compact_lottie_numbers(lottie)
    return gzip.compress(json.dumps(compact, separators=(",", ":")).encode("utf-8"))


def _find_font() -> Optional[str]:
    for p in _FONT_SEARCH:
        if os.path.exists(p):
            return p
    for p in glob.glob("/usr/share/fonts/**/*Bold*.ttf", recursive=True):
        return p
    for p in glob.glob(r"C:\Windows\Fonts\*bold*.ttf"):
        return p
    found = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True)
    if not found:
        found = glob.glob(r"C:\Windows\Fonts\*.ttf")
    return found[0] if found else None


def _ensure_font() -> Optional[str]:
    p = _find_font()
    if p:
        return p
    if os.path.exists(_CACHED_FONT_PATH) and os.path.getsize(_CACHED_FONT_PATH) > 50_000:
        return _CACHED_FONT_PATH
    log.info("_ensure_font: no local font found, downloading from CDN…")
    # Загрузка с таймаутом и запоминанием неудачи — см. _download_cached_file.
    if _download_cached_file(_FONT_CDN_URL, _CACHED_FONT_PATH):
        log.info("_ensure_font: font downloaded → %s", _CACHED_FONT_PATH)
        return _CACHED_FONT_PATH
    return None


def _load_fonttools() -> Optional[Tuple[Any, Any, Any]]:
    """
    Returns (TTFont, RecordingPen, DecomposingRecordingPen).
    DecomposingRecordingPen is critical for correct Cyrillic rendering:
    many Cyrillic glyphs are composite (e.g. В = B + transform), and
    a plain RecordingPen returns only addComponent instructions instead
    of actual contour data, producing broken/missing shapes.
    """
    global _FONTOOLS_CLASSES
    global _FONTOOLS_IMPORT_ERROR

    if _FONTOOLS_CLASSES is not None:
        return _FONTOOLS_CLASSES
    if _FONTOOLS_IMPORT_ERROR is not None:
        return None

    try:
        from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
        from fontTools.ttLib import TTFont
    except ImportError as e:
        _FONTOOLS_IMPORT_ERROR = str(e)
        log.error("fontTools not installed: %s", e)
        return None

    _FONTOOLS_CLASSES = (TTFont, RecordingPen, DecomposingRecordingPen)
    return _FONTOOLS_CLASSES


def get_text_render_dependency_error() -> Optional[str]:
    if _load_fonttools() is None:
        return _FONTOOLS_IMPORT_ERROR or "fontTools is not installed"
    return None


#: Шрифты, в которых нет кириллицы. Список проверен по таблице символов
#: каждого файла, а не на глаз: у половины «дисплейных» гарнитур русских
#: букв нет, и надпись из них просто не собиралась — генерация падала с
#: «Шаблон не содержит заменяемой надписи TEXT», хотя дело было в шрифте.
LATIN_ONLY_FONTS = frozenset({
    "bungee", "righteous", "audiowide", "rajdhani", "anton", "archivo_black",
})


def get_font_presets() -> Dict[str, Dict[str, Optional[str]]]:
    return {
        key: {
            "label": value.get("label"),
            "button": value.get("button"),
            # Приложению нужно предупредить: с русским ником такой шрифт
            # молча подменится на запасной, и это лучше сказать заранее.
            "latin_only": key in LATIN_ONLY_FONTS,
        }
        for key, value in _FONT_PRESETS.items()
        if key != "default"
    }


@lru_cache(maxsize=64)
def _font_charset(font_path: str) -> frozenset:
    """Набор символов, которые шрифт действительно умеет рисовать."""
    loaded = _load_fonttools()
    if loaded is None or not font_path:
        return frozenset()
    ttfont_cls = loaded[0]
    font = None
    try:
        font = ttfont_cls(font_path, fontNumber=0, lazy=True)
        return frozenset(font.getBestCmap().keys())
    except Exception as e:
        log.warning("Не прочитать таблицу символов %s: %s", font_path, e)
        return frozenset()
    finally:
        if font is not None:
            try:
                font.close()
            except Exception:
                pass


def font_covers(font_path: str, text: str) -> bool:
    """Хватает ли шрифта на эту надпись."""
    if not text:
        return True
    charset = _font_charset(font_path)
    if not charset:
        # Таблицу прочитать не вышло — не мешаем: пусть отрисовка решает
        # сама, иначе один сбой чтения обесценил бы весь выбор шрифтов.
        return True
    return all(ord(ch) in charset for ch in text if not ch.isspace())


def _uncovered_count(font_path: str, text: str) -> int:
    """Сколько символов надписи шрифт нарисовать не сможет."""
    charset = _font_charset(font_path)
    if not charset:
        return 0
    return sum(1 for ch in text if not ch.isspace() and ord(ch) not in charset)


def font_path_for_text(font_id: str, text: str) -> Optional[str]:
    """Путь к шрифту, которым надпись действительно нарисуется.

    Сравниваем не «покрывает целиком или нет», а сколько символов теряется:
    ни один шрифт не содержит эмодзи, и требование полного покрытия
    ником «Ваня❤» отправляло бы в отказ вообще всех. Меняем шрифт
    только тогда, когда запасной теряет строго меньше.
    """
    path = get_font_path(font_id)
    if not path or not text:
        return path
    missing = _uncovered_count(path, text)
    if missing == 0:
        return path
    fallback = get_font_path("default")
    if fallback and fallback != path and _uncovered_count(fallback, text) < missing:
        log.info("Шрифт %s не покрывает надпись — берём запасной", font_id)
        return fallback
    return path


def _ensure_cache_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


#: Таймаут любой загрузки шрифта и пауза перед повтором после неудачи.
#: Без таймаута urlretrieve мог заблокировать поток рендера навсегда и
#: исчерпать пул, из-за чего вставала генерация всех превью сразу.
FONT_DOWNLOAD_TIMEOUT = 10.0
FONT_DOWNLOAD_RETRY_AFTER = 300.0
_FAILED_DOWNLOADS: Dict[str, float] = {}


def _download_cached_file(url: str, target_path: str) -> Optional[str]:
    _ensure_cache_dir(os.path.dirname(target_path))
    if os.path.exists(target_path) and os.path.getsize(target_path) > 50_000:
        return target_path

    now = time.monotonic()
    failed_at = _FAILED_DOWNLOADS.get(url)
    if failed_at is not None and now - failed_at < FONT_DOWNLOAD_RETRY_AFTER:
        return None

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=FONT_DOWNLOAD_TIMEOUT) as response:
            data = response.read()
        temp_path = f"{target_path}.part{os.getpid()}"
        with open(temp_path, "wb") as fh:
            fh.write(data)
        os.replace(temp_path, target_path)
    except Exception as e:
        log.error("_download_cached_file failed: %s", e)
        _FAILED_DOWNLOADS[url] = now
        return None

    if os.path.exists(target_path) and os.path.getsize(target_path) > 10_000:
        return target_path
    return None


def get_font_path(font_id: str) -> Optional[str]:
    preset = _FONT_PRESETS.get(font_id) or _FONT_PRESETS["default"]
    filename = preset.get("filename")
    url = preset.get("url")
    fallback = preset.get("fallback")

    for candidate in preset.get("local_candidates", []) or []:
        if candidate and os.path.exists(candidate):
            return candidate

    if filename:
        cached_path = os.path.join(_FONT_CACHE_DIR, filename)
        if os.path.exists(cached_path) and os.path.getsize(cached_path) > 10_000:
            return cached_path
        if url:
            downloaded = _download_cached_file(url, cached_path)
            if downloaded:
                return downloaded

    if fallback:
        return get_font_path(fallback)

    return _ensure_font()


@lru_cache(maxsize=16)
def _get_font_runtime(font_path: str) -> Optional[Tuple[Any, Any, Dict[int, str], int, float]]:
    fonttools_classes = _load_fonttools()
    if fonttools_classes is None:
        return None

    TTFont, _, _DecompPen = fonttools_classes
    ft = TTFont(font_path)
    glyph_set = ft.getGlyphSet()
    cmap = ft.getBestCmap() or {}
    units_per_em = int(ft["head"].unitsPerEm)
    os2 = ft.get("OS/2")
    cap_height = float(
        getattr(os2, "sCapHeight", 0) or getattr(os2, "sTypoAscender", units_per_em * 0.72)
    )
    if cap_height <= 0:
        cap_height = units_per_em * 0.72
    return glyph_set, cmap, units_per_em, cap_height


@lru_cache(maxsize=2048)
def _get_glyph_pen_value(font_path: str, glyph_name: str) -> Tuple[Tuple[str, Any], ...]:
    """
    Draw glyph using DecomposingRecordingPen so composite glyphs
    (common for Cyrillic in many fonts) are fully expanded into
    contour operations instead of addComponent calls.
    """
    font_runtime = _get_font_runtime(font_path)
    fonttools_classes = _load_fonttools()
    if font_runtime is None or fonttools_classes is None:
        return ()

    glyph_set, _, _, _ = font_runtime
    _, _RecPen, DecomposingRecordingPen = fonttools_classes

    # Use DecomposingRecordingPen — this is the key fix for Cyrillic:
    # composite glyphs like uni0412 (В) reference Latin B with a transform,
    # and a plain RecordingPen yields only addComponent which we cannot render.
    pen = DecomposingRecordingPen(glyph_set)
    try:
        glyph_set[glyph_name].draw(pen)
    except Exception as e:
        log.warning("_get_glyph_pen_value: failed to draw %r: %s", glyph_name, e)
        return ()
    return tuple(pen.value)


def _load_ffmpeg_exe() -> Optional[str]:
    global _FFMPEG_EXE_PATH
    global _FFMPEG_EXE_ERROR

    if _FFMPEG_EXE_PATH is not None:
        return _FFMPEG_EXE_PATH
    if _FFMPEG_EXE_ERROR is not None:
        return None

    try:
        from imageio_ffmpeg import get_ffmpeg_exe
    except ImportError as e:
        _FFMPEG_EXE_ERROR = str(e)
        log.error("imageio_ffmpeg not installed: %s", e)
        return None

    try:
        _FFMPEG_EXE_PATH = get_ffmpeg_exe()
    except Exception as e:
        _FFMPEG_EXE_ERROR = str(e)
        log.error("ffmpeg binary unavailable: %s", e)
        return None

    return _FFMPEG_EXE_PATH


def _load_svg_parser() -> Optional[Tuple[Any, Any, Any, Any, Any, Any, Any]]:
    global _SVG_CLASSES
    global _SVG_IMPORT_ERROR

    if _SVG_CLASSES is not None:
        return _SVG_CLASSES
    if _SVG_IMPORT_ERROR is not None:
        return None

    try:
        from svgelements import Close, CubicBezier, Line, Move, Path, QuadraticBezier, SVG
    except ImportError as e:
        _SVG_IMPORT_ERROR = str(e)
        log.error("svgelements not installed: %s", e)
        return None

    _SVG_CLASSES = (SVG, Path, Move, Line, Close, CubicBezier, QuadraticBezier)
    return _SVG_CLASSES


def get_svg_dependency_error() -> Optional[str]:
    if _load_svg_parser() is None:
        return _SVG_IMPORT_ERROR or "svgelements is not installed"
    return None


def _load_rlottie() -> Optional[Any]:
    global _RLOTTIE_CLASS
    global _RLOTTIE_IMPORT_ERROR

    if _RLOTTIE_CLASS is not None:
        return _RLOTTIE_CLASS
    if _RLOTTIE_IMPORT_ERROR is not None:
        return None

    try:
        from rlottie_python import LottieAnimation
    except ImportError as e:
        _RLOTTIE_IMPORT_ERROR = str(e)
        log.error("rlottie-python not installed: %s", e)
        return None

    _RLOTTIE_CLASS = LottieAnimation
    return _RLOTTIE_CLASS


def get_preview_render_dependency_error() -> Optional[str]:
    if _load_rlottie() is None:
        return _RLOTTIE_IMPORT_ERROR or "rlottie-python is not installed"
    return None


#: Кириллица в латиницу для имён стикерпаков. Telegram принимает в имени
#: только латиницу, цифры и подчёркивание, поэтому русские буквы раньше
#: просто вырезались — и у любого человека с русским ником имя набора
#: схлопывалось в одну и ту же заглушку.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    # украинские и белорусские буквы: их пишут в никах не реже
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "u",
}


def translit_to_latin(text: str) -> str:
    """Русские буквы латиницей. Всё остальное отдаётся без изменений."""
    out = []
    for ch in str(text or ""):
        lower = ch.lower()
        if lower in _TRANSLIT:
            replaced = _TRANSLIT[lower]
            out.append(replaced.upper() if ch.isupper() and replaced else replaced)
        else:
            out.append(ch)
    return "".join(out)


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _fit_image_to_telegram_limits(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    max_side = max(img.width, img.height)
    if max_side <= 512:
        return img

    scale = 512.0 / float(max_side)
    new_size = (
        max(1, int(round(img.width * scale))),
        max(1, int(round(img.height * scale))),
    )
    return img.resize(new_size, Image.LANCZOS)


def _encode_webp_with_limit(img: Image.Image, size_limit: int = MAX_STATIC_STICKER_BYTES) -> bytes:
    best_data: Optional[bytes] = None
    attempts = [{"lossless": True, "method": 6}]

    for quality in (95, 90, 85, 80, 75, 70, 65, 60, 55, 50, 45, 40):
        attempts.append({"lossless": False, "quality": quality, "method": 6})

    for options in attempts:
        buf = io.BytesIO()
        img.save(buf, format="WEBP", **options)
        data = buf.getvalue()

        if best_data is None or len(data) < len(best_data):
            best_data = data
        if len(data) <= size_limit:
            return data

    raise ValueError(
        f"static sticker is too big after compression: {len(best_data or b'')} bytes > {size_limit}"
    )


def _build_webm_color_filter(hex_color: str) -> str:
    r, g, b = hex_to_rgb(hex_color)
    return (
        "format=rgba,"
        "colorchannelmixer=.299:.587:.114:0:.299:.587:.114:0:.299:.587:.114:0:0:0:0:1,"
        f"lutrgb=r=val*{r}/255:g=val*{g}/255:b=val*{b}/255"
    )


def _build_webm_recolor_filter(hex_color: str) -> str:
    return (
        f"{_build_webm_color_filter(hex_color)},"
        f"fps={MAX_VIDEO_STICKER_FPS},"
        f"trim=duration={MAX_VIDEO_STICKER_DURATION_SEC:.2f},"
        "setpts=PTS-STARTPTS"
    )


def _recolor_webm(data: bytes, hex_color: str, size_limit: int = MAX_VIDEO_STICKER_BYTES) -> bytes:
    ffmpeg_exe = _load_ffmpeg_exe()
    if ffmpeg_exe is None:
        raise RuntimeError(_FFMPEG_EXE_ERROR or "ffmpeg binary is unavailable")

    tmp_dir = tempfile.mkdtemp(prefix="jelly_webm_")
    src_path = os.path.join(tmp_dir, "source.webm")
    best_data: Optional[bytes] = None
    last_error: Optional[str] = None

    with open(src_path, "wb") as f:
        f.write(data)

    try:
        for crf in (30, 34, 38, 42, 46, 50, 54, 58, 60, 63):
            out_path = os.path.join(tmp_dir, f"out_{crf}.webm")
            cmd = [
                ffmpeg_exe, "-y", "-c:v", "libvpx-vp9", "-i", src_path,
                "-an", "-vf", _build_webm_recolor_filter(hex_color),
                "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
                "-metadata:s:v:0", "alpha_mode=1",
                "-t", f"{MAX_VIDEO_STICKER_DURATION_SEC:.2f}",
                "-b:v", "0", "-crf", str(crf),
                "-deadline", "good", "-cpu-used", "4",
                "-row-mt", "1", "-tile-columns", "2",
                "-frame-parallel", "0", "-auto-alt-ref", "0",
                "-f", "webm", out_path,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0 or not os.path.exists(out_path):
                last_error = (proc.stderr or proc.stdout or "ffmpeg failed").strip()
                continue

            with open(out_path, "rb") as f:
                out_data = f.read()

            if best_data is None or len(out_data) < len(best_data):
                best_data = out_data
            if len(out_data) <= size_limit:
                return out_data

        if last_error:
            raise RuntimeError(last_error)
        raise ValueError(
            f"video sticker is too big after compression: {len(best_data or b'')} bytes > {size_limit}"
        )
    finally:
        for name in os.listdir(tmp_dir):
            try:
                os.remove(os.path.join(tmp_dir, name))
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


def tint_image(img: Image.Image, hex_color: str) -> Image.Image:
    r, g, b = hex_to_rgb(hex_color)
    img = img.convert("RGBA")
    data = img.load()
    for y in range(img.height):
        for x in range(img.width):
            ro, go, bo, ao = data[x, y]
            if ao > 0:
                gray = int(0.299 * ro + 0.587 * go + 0.114 * bo)
                data[x, y] = (
                    int(r * gray / 255),
                    int(g * gray / 255),
                    int(b * gray / 255),
                    ao,
                )
    return img


def tint_lottie(lottie_json: dict, hex_color: str) -> dict:
    r, g, b = hex_to_rgb(hex_color)
    nr, ng, nb = r / 255, g / 255, b / 255

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if "c" in obj and isinstance(obj["c"], dict) and "k" in obj["c"]:
                k = obj["c"]["k"]
                if isinstance(k, list) and len(k) >= 3 and isinstance(k[0], (int, float)):
                    gray = 0.299 * k[0] + 0.587 * k[1] + 0.114 * k[2]
                    obj["c"]["k"] = [nr * gray, ng * gray, nb * gray] + (k[3:] or [1.0])
                elif isinstance(k, list):
                    for kf in k:
                        if isinstance(kf, dict) and "s" in kf:
                            s = kf["s"]
                            if isinstance(s, list) and len(s) >= 3:
                                gray = 0.299 * s[0] + 0.587 * s[1] + 0.114 * s[2]
                                kf["s"] = [nr * gray, ng * gray, nb * gray] + (s[3:] or [1.0])
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(lottie_json)
    return lottie_json


def recolor_bytes(data: bytes, mime: str, hex_color: str) -> Tuple[bytes, str]:
    if mime == "application/x-tgsticker":
        raw = gzip.decompress(data)
        lottie = json.loads(raw)
        lottie = tint_lottie(lottie, hex_color)
        out_data = _encode_lottie_tgs(lottie)
        if len(out_data) > MAX_ANIMATED_STICKER_BYTES:
            raise ValueError(
                f"animated sticker is too big after compression: {len(out_data)} bytes > {MAX_ANIMATED_STICKER_BYTES}"
            )
        return out_data, "sticker.tgs"
    if mime == "video/webm":
        return _recolor_webm(data, hex_color), "sticker.webm"
    if mime != "image/webp":
        raise ValueError(f"unsupported sticker mime type: {mime}")

    img = Image.open(io.BytesIO(data))
    img = _fit_image_to_telegram_limits(img)
    img = tint_image(img, hex_color)
    return _encode_webp_with_limit(img), "sticker.webp"


def _collect_path_verts(obj: Any) -> List[Tuple[float, float]]:
    verts: List[Tuple[float, float]] = []

    def _walk(o: Any) -> None:
        if isinstance(o, dict):
            if o.get("ty") == "sh":
                k = o.get("ks", {}).get("k", {})
                if isinstance(k, list) and k and isinstance(k[0], dict):
                    k = k[0].get("s", k[0])
                if isinstance(k, list) and k and isinstance(k[0], dict):
                    k = k[0]
                if isinstance(k, dict):
                    for v in k.get("v", []):
                        if isinstance(v, (list, tuple)) and len(v) >= 2:
                            verts.append((float(v[0]), float(v[1])))
            for val in o.values():
                _walk(val)
        elif isinstance(o, list):
            for item in o:
                _walk(item)

    _walk(obj)
    return verts


def _verts_to_bounds(
    verts: List[Tuple[float, float]],
) -> Optional[Tuple[float, float, float, float]]:
    if not verts:
        return None
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    return (min(xs), min(ys), max(xs), max(ys))


def _is_primary_text_group_name(name: str) -> bool:
    normalized = name.strip().upper()
    return normalized in {"TEXTGROUP", "EMOJI"}


def _shape_has_any_fill(obj: Any) -> bool:
    if isinstance(obj, dict):
        if obj.get("ty") == "fl":
            return True
        return any(_shape_has_any_fill(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_shape_has_any_fill(item) for item in obj)
    return False


def _is_pure_letter_shapes_list(shapes: list) -> bool:
    if not shapes:
        return False
    gr_items = [s for s in shapes if isinstance(s, dict) and s.get("ty") == "gr"]
    other_items = [
        s for s in shapes
        if isinstance(s, dict) and s.get("ty") in ("sh", "el", "rc", "sr", "st")
    ]
    if other_items or not (3 <= len(gr_items) <= 40):
        return False
    filled = sum(1 for g in gr_items if _shape_has_any_fill(g))
    return filled >= max(3, int(len(gr_items) * 0.6))


def _find_letter_cluster_candidates(
    lottie: dict,
) -> List[Tuple[list, Tuple[float, float, float, float]]]:
    all_layer_lists = [lottie.get("layers", [])]
    for asset in lottie.get("assets", []):
        all_layer_lists.append(asset.get("layers", []))

    candidates: List[Tuple[list, Tuple[float, float, float, float]]] = []
    for layers in all_layer_lists:
        for layer in layers:
            if layer.get("ty") != 4:
                continue
            shapes = layer.get("shapes", [])
            if _is_pure_letter_shapes_list(shapes):
                b = _verts_to_bounds(_collect_path_verts(shapes))
                if b:
                    candidates.append((shapes, b))
    return candidates


def _get_textgroup_bounds(
    lottie: dict,
) -> Optional[Tuple[float, float, float, float]]:
    named_matches: List[Tuple[float, float, float, float]] = []

    def find_named_group(obj: Any) -> None:
        if isinstance(obj, dict):
            if obj.get("ty") == "gr" and _is_primary_text_group_name(str(obj.get("nm") or "")):
                b = _verts_to_bounds(_collect_path_verts(obj))
                if b:
                    named_matches.append(b)
            for v in obj.values():
                find_named_group(v)
        elif isinstance(obj, list):
            for item in obj:
                find_named_group(item)

    find_named_group(lottie)
    if named_matches:
        return max(named_matches, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))

    def find_text_shape_layer(layers: list) -> Optional[Tuple[float, float, float, float]]:
        for layer in layers:
            if layer.get("ty") == 4:
                nm = layer.get("nm", "")
                if "text" in nm.lower() or "Text" in nm:
                    shapes = layer.get("shapes", [])
                    n_sh = sum(1 for s in shapes if s.get("ty") == "sh")
                    has_fl = any(s.get("ty") == "fl" for s in shapes)
                    if n_sh >= 2 and has_fl:
                        b = _verts_to_bounds(_collect_path_verts({"shapes": shapes}))
                        if b:
                            return b
        return None

    all_layer_lists = [lottie.get("layers", [])]
    for asset in lottie.get("assets", []):
        all_layer_lists.append(asset.get("layers", []))

    for layers in all_layer_lists:
        b = find_text_shape_layer(layers)
        if b:
            return b

    def _group_has_direct_fl(gr: dict) -> bool:
        return any(x.get("ty") == "fl" for x in gr.get("it", []))

    def _count_direct_sh(gr: dict) -> int:
        return sum(1 for x in gr.get("it", []) if x.get("ty") == "sh")

    def _count_nested_sh(gr: dict) -> int:
        total = 0
        for item in gr.get("it", []):
            if item.get("ty") == "sh":
                total += 1
            elif item.get("ty") == "gr":
                total += _count_nested_sh(item)
        return total

    def find_unnamed_text_group(obj: Any) -> Optional[Tuple[float, float, float, float]]:
        if isinstance(obj, dict):
            if obj.get("ty") == "gr":
                if (
                    _group_has_direct_fl(obj)
                    and _count_direct_sh(obj) == 0
                    and _count_nested_sh(obj) >= 3
                ):
                    verts = _collect_path_verts(obj)
                    if verts:
                        xs = [v[0] for v in verts]
                        ys = [v[1] for v in verts]
                        w = max(xs) - min(xs)
                        h = max(ys) - min(ys) + 1e-9
                        if w > h * 1.3 or w > 0:
                            return _verts_to_bounds(verts)
            for v in obj.values():
                r = find_unnamed_text_group(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = find_unnamed_text_group(item)
                if r:
                    return r
        return None

    found = find_unnamed_text_group(lottie)
    if found is not None:
        return found

    candidates = _find_letter_cluster_candidates(lottie)
    if candidates:
        candidates.sort(key=lambda c: len(c[0]), reverse=True)
        return candidates[0][1]
    return None


@lru_cache(maxsize=1024)
def _text_to_lottie_shapes(
    text: str,
    font_path: str,
    cx: float,
    cy: float,
    height: float,
    max_width: Optional[float] = None,
) -> list:
    font_runtime = _get_font_runtime(font_path)
    if font_runtime is None:
        return []
    gs, cm, upm, cap_h = font_runtime

    sc = height / cap_h

    total_adv = 0.0
    glyph_list = []
    for ch in text:
        gn = cm.get(ord(ch))
        if not gn or gn not in gs:
            fallbacks = {
                0x0027: [0x2019, 0x02BC, 0x0060],
                0x2019: [0x0027, 0x02BC, 0x0060],
                ord("\u201c"): [0x0022],
                ord("\u201d"): [0x0022],
                ord("\u2013"): [0x002D],
                ord("\u2014"): [0x002D],
            }
            for alt in fallbacks.get(ord(ch), []):
                gn = cm.get(alt)
                if gn and gn in gs:
                    break
            else:
                gn = None
        adv = float(gs[gn].width) if gn and gn in gs else upm * 0.35
        glyph_list.append((gn, adv))
        total_adv += adv

    if max_width and total_adv > 0:
        sc_w = max_width / (total_adv * sc) * sc
        sc = min(sc, sc_w * 0.92)

    start_x = cx - total_adv * sc / 2.0
    base_y = cy + (cap_h / 2.0) * sc

    shapes = []
    cur_x = start_x

    for gn, adv in glyph_list:
        if gn is None:
            cur_x += adv * sc
            continue

        vs, ii, oo = [], [], []

        def _close() -> None:
            if vs:
                shapes.append({
                    "ty": "sh",
                    "nm": "p",
                    "ks": {
                        "a": 0,
                        "k": {
                            "c": True,
                            "v": [list(v) for v in vs],
                            "i": [list(v) for v in ii],
                            "o": [list(v) for v in oo],
                        },
                    },
                })

        # _get_glyph_pen_value now uses DecomposingRecordingPen
        # so Cyrillic composite glyphs are properly expanded
        for op, args in _get_glyph_pen_value(font_path, gn):
            if op == "moveTo":
                _close()
                vs, ii, oo = [], [], []
                fx, fy = args[0]
                lx, ly = fx * sc + cur_x, base_y - fy * sc
                vs.append([lx, ly])
                ii.append([0.0, 0.0])
                oo.append([0.0, 0.0])

            elif op == "lineTo":
                fx, fy = args[0]
                lx, ly = fx * sc + cur_x, base_y - fy * sc
                vs.append([lx, ly])
                ii.append([0.0, 0.0])
                oo.append([0.0, 0.0])

            elif op == "curveTo":
                (c1x, c1y), (c2x, c2y), (ex, ey) = args
                pvx, pvy = vs[-1]
                oo[-1] = [c1x * sc + cur_x - pvx, base_y - c1y * sc - pvy]
                nvx, nvy = ex * sc + cur_x, base_y - ey * sc
                vs.append([nvx, nvy])
                ii.append([c2x * sc + cur_x - nvx, base_y - c2y * sc - nvy])
                oo.append([0.0, 0.0])

            elif op == "qCurveTo":
                pts = list(args)
                p0x, p0y = vs[-1]
                for qi in range(len(pts) - 1):
                    qcx, qcy = pts[qi]
                    if qi < len(pts) - 2:
                        qex = (pts[qi][0] + pts[qi + 1][0]) / 2.0
                        qey = (pts[qi][1] + pts[qi + 1][1]) / 2.0
                    else:
                        qex, qey = pts[qi + 1]
                    qcs = (qcx * sc + cur_x, base_y - qcy * sc)
                    qes = (qex * sc + cur_x, base_y - qey * sc)
                    c1s = (p0x + 2 / 3 * (qcs[0] - p0x), p0y + 2 / 3 * (qcs[1] - p0y))
                    c2s = (qes[0] + 2 / 3 * (qcs[0] - qes[0]), qes[1] + 2 / 3 * (qcs[1] - qes[1]))
                    oo[-1] = [c1s[0] - p0x, c1s[1] - p0y]
                    vs.append(list(qes))
                    ii.append([c2s[0] - qes[0], c2s[1] - qes[1]])
                    oo.append([0.0, 0.0])
                    p0x, p0y = qes

            elif op in ("endPath", "closePath"):
                _close()
                vs, ii, oo = [], [], []

            # addComponent should NOT appear here anymore because we use
            # DecomposingRecordingPen, but handle it gracefully just in case
            elif op == "addComponent":
                log.warning("_text_to_lottie_shapes: unexpected addComponent in glyph %r", gn)

        _close()
        cur_x += adv * sc

    return shapes


def _replace_textgroup(lottie: dict, new_path_shapes: list) -> bool:
    def _has_fill(items: list) -> bool:
        return any(x.get("ty") == "fl" for x in items)

    def _is_letter_container(item: dict) -> bool:
        if item.get("ty") != "gr":
            return False
        inner = item.get("it", [])
        return not _has_fill(inner) and not any(x.get("ty") == "st" for x in inner)

    def _patch_list(lst: list) -> bool:
        style = [
            x for x in lst
            if x.get("ty") not in ("sh", "el", "rc", "sr")
            and not _is_letter_container(x)
        ]
        lst[:] = new_path_shapes + style
        return True

    def walk_group(obj: Any) -> bool:
        changed = False
        if isinstance(obj, dict):
            if obj.get("ty") == "gr" and _is_primary_text_group_name(str(obj.get("nm") or "")):
                _patch_list(obj.setdefault("it", []))
                changed = True
            for v in obj.values():
                if walk_group(v):
                    changed = True
        elif isinstance(obj, list):
            for item in obj:
                if walk_group(item):
                    changed = True
        return changed

    if walk_group(lottie):
        return True

    def try_patch_layer_shapes(layers: list) -> bool:
        for layer in layers:
            if layer.get("ty") != 4:
                continue
            shapes = layer.get("shapes", [])
            nm = layer.get("nm", "")
            n_sh = sum(1 for s in shapes if s.get("ty") == "sh")
            has_fl = any(s.get("ty") == "fl" for s in shapes)
            is_text_layer = "text" in nm.lower() or "Text" in nm
            if (is_text_layer and n_sh >= 2 and has_fl) or (n_sh >= 3 and has_fl):
                return _patch_list(shapes)
        return False

    all_layer_lists = [lottie.get("layers", [])]
    for asset in lottie.get("assets", []):
        all_layer_lists.append(asset.get("layers", []))

    for layers in all_layer_lists:
        if try_patch_layer_shapes(layers):
            return True

    def walk_unnamed(obj: Any) -> bool:
        if isinstance(obj, dict):
            if obj.get("ty") == "gr":
                items = obj.get("it", [])
                has_fl = _has_fill(items)
                n_direct_sh = sum(1 for x in items if x.get("ty") == "sh")
                has_letter_groups = any(_is_letter_container(x) for x in items)
                if has_fl and (has_letter_groups or n_direct_sh == 0):
                    def count_nested(it: list) -> int:
                        n = 0
                        for x in it:
                            if x.get("ty") == "sh":
                                n += 1
                            elif x.get("ty") == "gr":
                                n += count_nested(x.get("it", []))
                        return n
                    if count_nested(items) >= 3:
                        return _patch_list(items)
            for v in obj.values():
                if walk_unnamed(v):
                    return True
        elif isinstance(obj, list):
            for item in obj:
                if walk_unnamed(item):
                    return True
        return False

    if walk_unnamed(lottie):
        return True

    candidates = _find_letter_cluster_candidates(lottie)
    if candidates:
        candidates.sort(key=lambda c: len(c[0]), reverse=True)
        shapes_list = candidates[0][0]
        shapes_list[:] = new_path_shapes
        return True
    return False


def _find_named_groups(lottie: dict, prefixes: Tuple[str, ...]) -> List[Tuple[tuple, dict]]:
    found: List[Tuple[tuple, dict]] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            name = str(obj.get("nm") or "").strip().upper()
            if obj.get("ty") == "gr" and any(name.startswith(prefix) for prefix in prefixes):
                verts = _collect_path_verts(obj)
                b = _verts_to_bounds(verts)
                if b:
                    found.append((b, obj))
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(lottie)
    return found


def _find_username_bounds(lottie: dict) -> Optional[tuple]:
    found = _find_named_groups(lottie, ("USERNAME",))
    return found[0] if found else None


_OLD_USERNAME = "@emojicreationbot"
_NEW_USERNAME = "@freecreateemoji"


def _replace_group_text(grp: dict, bounds: tuple, new_text: str, font_path: str) -> bool:
    x1, y1, x2, y2 = bounds
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    height = max(abs(y2 - y1), 1.0)
    width = max(abs(x2 - x1), 1.0)
    new_shapes = _text_to_lottie_shapes(new_text, font_path, cx, cy, height, max_width=width)
    if not new_shapes:
        return False
    return _replace_group_shapes(grp, new_shapes)


def _replace_group_shapes(grp: dict, new_shapes: List[dict]) -> bool:
    if not new_shapes:
        return False

    def _has_fill(lst: list) -> bool:
        return any(x.get("ty") == "fl" for x in lst)

    def _is_letter_grp(x: dict) -> bool:
        if x.get("ty") != "gr":
            return False
        return not _has_fill(x.get("it", []))

    items = grp.setdefault("it", [])
    style = [
        x for x in items
        if x.get("ty") not in ("sh", "el", "rc", "sr") and not _is_letter_grp(x)
    ]
    items[:] = list(new_shapes) + style
    return True


def _replace_letter_container_shapes(grp: dict, new_shapes: List[dict]) -> bool:
    if not new_shapes:
        return False

    def _has_fill(lst: list) -> bool:
        return any(x.get("ty") == "fl" for x in lst)

    def _has_stroke(lst: list) -> bool:
        return any(x.get("ty") == "st" for x in lst)

    def _is_letter_container(item: dict) -> bool:
        if item.get("ty") != "gr":
            return False
        inner = item.get("it", [])
        return not _has_fill(inner) and not _has_stroke(inner)

    def _patch_list(lst: list) -> None:
        style = [
            item for item in lst
            if item.get("ty") not in ("sh", "el", "rc", "sr")
            and not _is_letter_container(item)
        ]
        lst[:] = list(new_shapes) + style

    def _walk(items: list) -> bool:
        for item in items:
            if _is_letter_container(item):
                _patch_list(item.setdefault("it", []))
                return True
            if item.get("ty") == "gr" and _walk(item.get("it", [])):
                return True
        return False

    items = grp.setdefault("it", [])
    if _walk(items):
        return True
    return _replace_group_shapes(grp, new_shapes)


def _replace_username(lottie: dict, new_text: str, font_path: str) -> bool:
    changed = False
    for bounds, grp in _find_named_groups(lottie, ("USERNAME",)):
        if _replace_group_text(grp, bounds, new_text, font_path):
            changed = True
    return changed


def _replace_logo_group(lottie: dict, svg_bytes: bytes) -> bool:
    changed = False
    for bounds, grp in _find_named_groups(lottie, ("LOGO",)):
        logo_shapes = _svg_to_lottie_shapes(svg_bytes, bounds)
        if _replace_group_shapes(grp, logo_shapes):
            changed = True
    return changed


def _replace_passport_username(lottie: dict, new_text: str, font_path: str) -> bool:
    username_groups = _find_named_groups(lottie, ("USERNAME",))
    if not username_groups:
        return False

    changed = False
    for bounds, grp in username_groups:
        username_shapes = _text_shapes_in_bounds(new_text, font_path, bounds)
        if _replace_letter_container_shapes(grp, username_shapes):
            changed = True
    return changed


def _passport_username_bounds(
    primary_bounds: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    x1, y1, x2, y2 = primary_bounds
    width = max(x2 - x1, 1.0)
    height = max(y2 - y1, 1.0)
    return (
        x1 - width * 0.08,
        y2 + height * 0.06,
        x1 + width * 0.68,
        y2 + height * 0.34,
    )


def _replace_named_shape_layer_text(
    lottie: dict,
    layer_prefix: str,
    new_text: str,
    font_path: str,
    target_bounds: Optional[Tuple[float, float, float, float]] = None,
) -> bool:
    layer_prefix = layer_prefix.strip().upper()
    changed = False

    def _patch_layer(layer: dict) -> bool:
        if layer.get("ty") != 4:
            return False
        name = str(layer.get("nm") or "").strip().upper()
        if not name.startswith(layer_prefix):
            return False

        bounds = target_bounds or _verts_to_bounds(_collect_path_verts({"shapes": layer.get("shapes", [])}))
        if bounds is None:
            return False

        x1, y1, x2, y2 = bounds
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        height = max(abs(y2 - y1), 1.0)
        width = max(abs(x2 - x1), 1.0)
        new_shapes = _text_to_lottie_shapes(new_text, font_path, cx, cy, height, max_width=width)
        if not new_shapes:
            return False

        for shape in layer.get("shapes", []):
            if shape.get("ty") == "gr":
                return _replace_group_shapes(shape, new_shapes)
        return False

    all_layer_lists = [lottie.get("layers", [])]
    for asset in lottie.get("assets", []):
        all_layer_lists.append(asset.get("layers", []))

    for layers in all_layer_lists:
        for layer in layers:
            if _patch_layer(layer):
                changed = True
    return changed


def _set_named_shape_layer_opacity(lottie: dict, layer_prefix: str, opacity: float) -> bool:
    layer_prefix = layer_prefix.strip().upper()
    changed = False
    all_layer_lists = [lottie.get("layers", [])]
    for asset in lottie.get("assets", []):
        all_layer_lists.append(asset.get("layers", []))

    for layers in all_layer_lists:
        for layer in layers:
            if layer.get("ty") != 4:
                continue
            name = str(layer.get("nm") or "").strip().upper()
            if not name.startswith(layer_prefix):
                continue
            layer.setdefault("ks", {}).setdefault("o", {"a": 0, "k": opacity})
            layer["ks"]["o"] = {"a": 0, "k": opacity}
            changed = True
    return changed


def _make_rect_shape(bounds: Tuple[float, float, float, float]) -> dict:
    x1, y1, x2, y2 = bounds
    return {
        "ty": "sh",
        "nm": "Rect",
        "ks": {
            "a": 0,
            "k": {
                "c": True,
                "v": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                "i": [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
                "o": [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
            },
        },
    }


def _make_fill_shape(rgb: Tuple[float, float, float]) -> dict:
    r, g, b = rgb
    return {
        "ty": "fl",
        "c": {"a": 0, "k": [r, g, b, 1]},
        "o": {"a": 0, "k": 100},
        "r": 1,
        "nm": "Fill",
    }


def _make_group_shape(name: str, path_shapes: List[dict], rgb: Tuple[float, float, float]) -> dict:
    return {
        "ty": "gr",
        "nm": name,
        "it": list(path_shapes) + [
            _make_fill_shape(rgb),
            {
                "ty": "tr",
                "p": {"a": 0, "k": [0, 0]},
                "a": {"a": 0, "k": [0, 0]},
                "s": {"a": 0, "k": [100, 100]},
                "r": {"a": 0, "k": 0},
                "o": {"a": 0, "k": 100},
                "sk": {"a": 0, "k": 0},
                "sa": {"a": 0, "k": 0},
                "nm": "Transform",
            },
        ],
    }


def _make_overlay_layer(
    name: str,
    groups: List[dict],
    op: float,
    index: int,
    template_layer: Optional[dict] = None,
) -> dict:
    if template_layer is not None:
        ks = json.loads(json.dumps(template_layer.get("ks", {})))
        ks["o"] = {"a": 0, "k": 100}
        ks["s"] = {"a": 0, "k": [100, 100]}
        sr = template_layer.get("sr", 1)
        ao = template_layer.get("ao", 0)
        ip = 0
        st = 0
        bm = template_layer.get("bm", 0)
        parent = template_layer.get("parent")
        hidden = template_layer.get("hd")
    else:
        ks = {
            "o": {"a": 0, "k": 100},
            "r": {"a": 0, "k": 0},
            "p": {"a": 0, "k": [0, 0]},
            "a": {"a": 0, "k": [0, 0]},
            "s": {"a": 0, "k": [100, 100]},
        }
        sr = 1
        ao = 0
        ip = 0
        st = 0
        bm = 0
        parent = None
        hidden = None

    layer = {
        "ddd": 0,
        "ind": index,
        "ty": 4,
        "nm": name,
        "sr": sr,
        "ks": ks,
        "ao": ao,
        "shapes": groups,
        "ip": ip,
        "op": template_layer.get("op", op) if template_layer is not None else op,
        "st": st,
        "bm": bm,
    }
    if parent is not None:
        layer["parent"] = parent
    if hidden is not None:
        layer["hd"] = hidden
    return layer


def _inject_passport_username_overlay(
    lottie: dict,
    new_text: str,
    font_path: str,
    primary_bounds: Tuple[float, float, float, float],
) -> bool:
    username_group = _find_username_bounds(lottie)
    if username_group is not None:
        username_bounds, _ = username_group
    else:
        username_bounds = _passport_username_bounds(primary_bounds)
    text_shapes = _text_shapes_in_bounds(new_text, font_path, username_bounds)
    if not text_shapes:
        return False

    overlay_groups = [
        _make_group_shape("Passport Username Text", text_shapes, (1.0, 1.0, 1.0)),
    ]

    changed = False
    for asset in lottie.get("assets", []):
        layers = asset.get("layers", [])
        username_layer = next(
            (
                layer for layer in layers
                if str(layer.get("nm") or "").strip().upper().startswith("USERNAME")
            ),
            None,
        )
        if username_layer is None:
            continue
        next_index = max((int(layer.get("ind", 0) or 0) for layer in layers), default=0) + 1
        username_index = layers.index(username_layer)
        layers.insert(
            username_index + 1,
            _make_overlay_layer(
                "USERNAME_CUSTOM",
                overlay_groups,
                float(lottie.get("op", 60) or 60),
                next_index,
                template_layer=username_layer,
            ),
        )
        changed = True

    if changed:
        _set_named_shape_layer_opacity(lottie, "USERNAME", 0)
    return changed


def replace_text_in_tgs(tgs_bytes: bytes, old_text: str, new_text: str) -> bytes:
    raw = gzip.decompress(tgs_bytes)
    lottie = json.loads(raw.decode("utf-8"))

    font_path = _ensure_font()
    if font_path is None:
        log.error("replace_text_in_tgs: no TTF font found")
        return tgs_bytes

    changed = False

    bounds = _get_textgroup_bounds(lottie)
    if bounds is not None:
        x1, y1, x2, y2 = bounds
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        height = max(abs(y2 - y1), 5.0)
        width = max(abs(x2 - x1), 5.0)
        new_shapes = _text_to_lottie_shapes(new_text, font_path, cx, cy, height, max_width=width)
        if new_shapes and _replace_textgroup(lottie, new_shapes):
            changed = True

    if _find_username_bounds(lottie):
        if _replace_username(lottie, _NEW_USERNAME, font_path):
            changed = True

    if not changed:
        return tgs_bytes
    return _encode_lottie_tgs(lottie)


def _union_bounds(
    current: Optional[Tuple[float, float, float, float]],
    new_bounds: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    if current is None:
        return new_bounds
    return (
        min(current[0], new_bounds[0]),
        min(current[1], new_bounds[1]),
        max(current[2], new_bounds[2]),
        max(current[3], new_bounds[3]),
    )


def _svg_point_xy(point: Any) -> Tuple[float, float]:
    return float(getattr(point, "x", 0.0)), float(getattr(point, "y", 0.0))


@lru_cache(maxsize=128)
def _parse_svg_paths(
    svg_bytes: bytes,
    max_paths: int = 120,
) -> Tuple[List[Any], Tuple[float, float, float, float]]:
    svg_classes = _load_svg_parser()
    if svg_classes is None:
        raise RuntimeError(get_svg_dependency_error() or "svgelements is unavailable")

    SVG, Path, _, _, _, _, _ = svg_classes

    try:
        svg_text = svg_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ValueError("SVG должен быть в UTF-8 кодировке.") from e

    try:
        svg = SVG.parse(io.StringIO(svg_text))
    except Exception as e:
        raise ValueError(f"Не удалось разобрать SVG: {e}") from e

    paths: List[Any] = []
    bounds: Optional[Tuple[float, float, float, float]] = None

    for element in svg.elements():
        if isinstance(element, SVG):
            continue

        values = getattr(element, "values", {}) or {}
        if str(values.get("display", "")).lower() == "none":
            continue

        try:
            path = Path(element)
        except Exception:
            continue

        if not len(path):
            continue

        path.approximate_arcs_with_cubics()
        box = path.bbox()
        if box is None:
            continue

        bounds = _union_bounds(bounds, box)
        paths.append(path)

        if len(paths) > max_paths:
            raise ValueError("SVG слишком сложный. Используйте логотип попроще.")

    if not paths or bounds is None:
        raise ValueError("SVG не содержит пригодных векторных контуров.")

    if abs(bounds[2] - bounds[0]) < 1e-6 or abs(bounds[3] - bounds[1]) < 1e-6:
        raise ValueError("SVG имеет пустую область рисования.")

    return paths, bounds


def validate_svg_logo(svg_bytes: bytes, max_bytes: int = 256 * 1024) -> None:
    if len(svg_bytes) > max_bytes:
        raise ValueError("SVG слишком большой. Используйте файл до 256 KB.")
    _parse_svg_paths(svg_bytes)


def extract_tgs_layout_info(
    tgs_bytes: bytes,
) -> Optional[Tuple[int, int, Tuple[float, float, float, float]]]:
    try:
        raw = gzip.decompress(tgs_bytes)
        lottie = json.loads(raw.decode("utf-8"))
    except Exception:
        return None

    bounds = _get_textgroup_bounds(lottie)
    if bounds is None:
        return None

    width = int(lottie.get("w", 512) or 512)
    height = int(lottie.get("h", 512) or 512)
    return width, height, bounds


def _fit_bounds(
    source_bounds: Tuple[float, float, float, float],
    target_bounds: Tuple[float, float, float, float],
    padding_ratio: float = 0.92,
) -> Tuple[float, float, float]:
    sx1, sy1, sx2, sy2 = source_bounds
    tx1, ty1, tx2, ty2 = target_bounds

    source_w = max(sx2 - sx1, 1.0)
    source_h = max(sy2 - sy1, 1.0)
    target_w = max(tx2 - tx1, 1.0) * padding_ratio
    target_h = max(ty2 - ty1, 1.0) * padding_ratio
    scale = min(target_w / source_w, target_h / source_h)

    offset_x = (tx1 + tx2) / 2.0 - (sx1 + sx2) / 2.0 * scale
    offset_y = (ty1 + ty2) / 2.0 - (sy1 + sy2) / 2.0 * scale
    return scale, offset_x, offset_y


def _path_to_lottie_shapes(path: Any, scale: float, offset_x: float, offset_y: float) -> List[dict]:
    svg_classes = _load_svg_parser()
    if svg_classes is None:
        return []

    _, _, Move, Line, Close, CubicBezier, QuadraticBezier = svg_classes

    shapes: List[dict] = []
    vertices: List[List[float]] = []
    incoming: List[List[float]] = []
    outgoing: List[List[float]] = []

    def _point(point: Any) -> List[float]:
        x, y = _svg_point_xy(point)
        return [x * scale + offset_x, y * scale + offset_y]

    def _flush() -> None:
        if len(vertices) >= 2:
            shapes.append({
                "ty": "sh",
                "nm": "logo",
                "ks": {
                    "a": 0,
                    "k": {
                        "c": True,
                        "v": [list(v) for v in vertices],
                        "i": [list(v) for v in incoming],
                        "o": [list(v) for v in outgoing],
                    },
                },
            })

    for segment in path:
        if isinstance(segment, Move):
            _flush()
            vertices = []
            incoming = []
            outgoing = []
            pt = _point(segment.end)
            vertices.append(pt)
            incoming.append([0.0, 0.0])
            outgoing.append([0.0, 0.0])
        elif isinstance(segment, Line):
            pt = _point(segment.end)
            vertices.append(pt)
            incoming.append([0.0, 0.0])
            outgoing.append([0.0, 0.0])
        elif isinstance(segment, CubicBezier):
            if not vertices:
                continue
            c1 = _point(segment.control1)
            c2 = _point(segment.control2)
            end = _point(segment.end)
            prev_x, prev_y = vertices[-1]
            outgoing[-1] = [c1[0] - prev_x, c1[1] - prev_y]
            vertices.append(end)
            incoming.append([c2[0] - end[0], c2[1] - end[1]])
            outgoing.append([0.0, 0.0])
        elif isinstance(segment, QuadraticBezier):
            if not vertices:
                continue
            ctrl = _point(segment.control)
            end = _point(segment.end)
            prev_x, prev_y = vertices[-1]
            c1 = [
                prev_x + 2.0 / 3.0 * (ctrl[0] - prev_x),
                prev_y + 2.0 / 3.0 * (ctrl[1] - prev_y),
            ]
            c2 = [
                end[0] + 2.0 / 3.0 * (ctrl[0] - end[0]),
                end[1] + 2.0 / 3.0 * (ctrl[1] - end[1]),
            ]
            outgoing[-1] = [c1[0] - prev_x, c1[1] - prev_y]
            vertices.append(end)
            incoming.append([c2[0] - end[0], c2[1] - end[1]])
            outgoing.append([0.0, 0.0])
        elif isinstance(segment, Close):
            _flush()
            vertices = []
            incoming = []
            outgoing = []

    _flush()
    return shapes


def _path_fill_rgb(path: Any) -> Optional[List[float]]:
    """Цвет заливки пути из SVG в виде долей единицы, как ждёт Lottie."""
    fill = getattr(path, "fill", None)
    if fill is None:
        return None
    try:
        red, green, blue = fill.red, fill.green, fill.blue
    except Exception:
        return None
    if red is None or green is None or blue is None:
        return None
    # Полностью прозрачную заливку рисовать нечем.
    alpha = getattr(fill, "alpha", 255)
    if alpha is not None and float(alpha) <= 0:
        return None
    return [round(float(red) / 255.0, 4), round(float(green) / 255.0, 4),
            round(float(blue) / 255.0, 4)]


@lru_cache(maxsize=256)
def _svg_to_lottie_shapes(
    svg_bytes: bytes,
    target_bounds: Tuple[float, float, float, float],
    padding_ratio: float = 0.92,
    keep_colors: bool = False,
) -> List[dict]:
    """Контуры SVG, вписанные в зону шаблона.

    По умолчанию отдаются голыми: цвет им даёт группа-владелец, и логотип
    получается одноцветным — так и задумано, монограмма должна попадать в
    палитру шаблона.

    keep_colors нужен для картинок, где цвет и есть содержание: каждый путь
    заворачивается в свою группу с собственной заливкой, взятой из SVG.
    Без этого разноцветное изображение схлопывалось в силуэт.
    """
    paths, source_bounds = _parse_svg_paths(svg_bytes)
    scale, offset_x, offset_y = _fit_bounds(source_bounds, target_bounds, padding_ratio=padding_ratio)

    if not keep_colors:
        # Решаем сами по содержимому. Монограмма нарисована одним цветом, и
        # ей правильно взять палитру шаблона — так было и остаётся. А если
        # в файле цветов несколько, значит цвет и есть содержание: это
        # картинка, и схлопывать её в силуэт нельзя.
        distinct = {tuple(rgb) for rgb in (_path_fill_rgb(p) for p in paths) if rgb}
        keep_colors = len(distinct) > 1

    shapes: List[dict] = []
    for path in paths:
        contours = _path_to_lottie_shapes(path, scale, offset_x, offset_y)
        if not contours:
            continue
        rgb = _path_fill_rgb(path) if keep_colors else None
        if rgb is None:
            shapes.extend(contours)
            continue
        shapes.append({
            "ty": "gr",
            "nm": "svg_layer",
            "it": contours + [
                {"ty": "fl", "nm": "svg_fill", "o": {"a": 0, "k": 100},
                 "c": {"a": 0, "k": rgb}, "r": 1},
                {"ty": "tr",
                 "p": {"a": 0, "k": [0, 0]}, "a": {"a": 0, "k": [0, 0]},
                 "s": {"a": 0, "k": [100, 100]}, "r": {"a": 0, "k": 0},
                 "o": {"a": 0, "k": 100}, "sk": {"a": 0, "k": 0},
                 "sa": {"a": 0, "k": 0}},
            ],
        })
    return shapes


def _split_two_lines(text: str) -> Tuple[str, str]:
    """Делит текст на 2 строки как можно ровнее, предпочитая разрыв по пробелу."""
    text = text.strip()
    if " " in text:
        best_idx, best_diff = None, None
        for i, ch in enumerate(text):
            if ch == " ":
                diff = abs(i - len(text) / 2.0)
                if best_diff is None or diff < best_diff:
                    best_diff, best_idx = diff, i
        if best_idx is not None:
            return text[:best_idx].strip(), text[best_idx + 1:].strip()
    mid = len(text) // 2
    return text[:mid], text[mid:]


def _text_shapes_in_bounds(
    text: str,
    font_path: str,
    target_bounds: Tuple[float, float, float, float],
) -> List[dict]:
    x1, y1, x2, y2 = target_bounds
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    height = max(y2 - y1, 5.0)
    width = max(x2 - x1, 5.0)

    text = text or ""
    # Длинный текст в одну строку пришлось бы сжимать по ширине до крошечного
    # размера — вместо этого переносим на 2 строки, так буквы остаются крупными.
    if len(text) > 10:
        line1, line2 = _split_two_lines(text)
        if line1 and line2:
            gap = height * 0.10
            line_h = (height - gap) / 2.0
            shapes: List[dict] = []
            shapes += _text_to_lottie_shapes(line1, font_path, cx, cy - line_h / 2.0 - gap / 2.0, line_h, max_width=width)
            shapes += _text_to_lottie_shapes(line2, font_path, cx, cy + line_h / 2.0 + gap / 2.0, line_h, max_width=width)
            return shapes

    return _text_to_lottie_shapes(text, font_path, cx, cy, height, max_width=width)


def _brand_logo_and_text_bounds(
    target_bounds: Tuple[float, float, float, float],
    text: str,
) -> Tuple[Tuple[float, float, float, float], Optional[Tuple[float, float, float, float]]]:
    x1, y1, x2, y2 = target_bounds
    width = max(x2 - x1, 1.0)
    height = max(y2 - y1, 1.0)
    has_text = bool(str(text or "").strip())

    if not has_text:
        return (
            (
                x1 + width * 0.04,
                y1 + height * 0.04,
                x2 - width * 0.04,
                y2 - height * 0.04,
            ),
            None,
        )

    logo_bounds = (
        x1 + width * 0.08,
        y1 + height * 0.04,
        x2 - width * 0.08,
        y1 + height * 0.70,
    )
    text_bounds = (
        x1 + width * 0.05,
        y1 + height * 0.72,
        x2 - width * 0.05,
        y2 - height * 0.03,
    )
    return logo_bounds, text_bounds


def _clamp_bounds(
    bounds: Tuple[float, float, float, float],
    canvas_size: Tuple[float, float],
    margin_ratio: float = 0.04,
    centered_origin: bool = False,
) -> Tuple[float, float, float, float]:
    canvas_w, canvas_h = canvas_size
    margin_x = canvas_w * margin_ratio
    margin_y = canvas_h * margin_ratio
    x1, y1, x2, y2 = bounds
    if centered_origin:
        min_x = -canvas_w / 2.0 + margin_x
        max_x = canvas_w / 2.0 - margin_x
        min_y = -canvas_h / 2.0 + margin_y
        max_y = canvas_h / 2.0 - margin_y
    else:
        min_x = margin_x
        max_x = canvas_w - margin_x
        min_y = margin_y
        max_y = canvas_h - margin_y
    return (
        max(min_x, x1),
        max(min_y, y1),
        min(max_x, x2),
        min(max_y, y2),
    )


def _resolve_brand_target_bounds(
    target_bounds: Tuple[float, float, float, float],
    canvas_size: Tuple[float, float],
    text: str,
    centered_origin: bool = False,
) -> Tuple[float, float, float, float]:
    x1, y1, x2, y2 = target_bounds
    width = max(x2 - x1, 1.0)
    height = max(y2 - y1, 1.0)
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    canvas_w, canvas_h = canvas_size
    has_text = bool(str(text or "").strip())

    if not has_text:
        side = max(width * 5.2, height * 5.2, min(canvas_w, canvas_h) * 0.54)
        side = min(side, min(canvas_w, canvas_h) * 0.68)
        half_side = side / 2.0
        return _clamp_bounds(
            (cx - half_side, cy - half_side, cx + half_side, cy + half_side),
            canvas_size,
            centered_origin=centered_origin,
        )

    desired_w = max(width * 2.8, canvas_w * 0.48)
    desired_h = max(height * 4.4, canvas_h * 0.42)
    desired_w = min(desired_w, canvas_w * 0.72)
    desired_h = min(desired_h, canvas_h * 0.62)
    top_shift = desired_h * 0.10
    return _clamp_bounds(
        (
            cx - desired_w / 2.0,
            cy - desired_h / 2.0 - top_shift,
            cx + desired_w / 2.0,
            cy + desired_h / 2.0 - top_shift,
        ),
        canvas_size,
        centered_origin=centered_origin,
    )


def _compose_brand_shapes(
    text: str,
    font_path: str,
    target_bounds: Tuple[float, float, float, float],
    logo_svg: Optional[bytes],
    canvas_size: Optional[Tuple[float, float]] = None,
) -> List[dict]:
    if not logo_svg:
        return _text_shapes_in_bounds(text, font_path, target_bounds)

    effective_bounds = (
        _resolve_brand_target_bounds(target_bounds, canvas_size, text, centered_origin=True)
        if canvas_size is not None else target_bounds
    )
    logo_bounds, text_bounds = _brand_logo_and_text_bounds(effective_bounds, text)
    logo_shapes = _svg_to_lottie_shapes(logo_svg, logo_bounds, padding_ratio=0.98)
    text_shapes = _text_shapes_in_bounds(text, font_path, text_bounds) if text_bounds is not None else []
    return logo_shapes + text_shapes


def customize_tgs_template(
    tgs_bytes: bytes,
    text: str,
    hex_color: str,
    font_id: str,
    logo_svg: Optional[bytes] = None,
    enforce_size_limit: bool = True,
    secondary_text: Optional[str] = None,
) -> bytes:
    raw = gzip.decompress(tgs_bytes)
    lottie = json.loads(raw.decode("utf-8"))
    canvas_size = (
        float(lottie.get("w", 512) or 512),
        float(lottie.get("h", 512) or 512),
    )

    font_path = get_font_path(font_id)
    if font_path is None:
        raise RuntimeError("Не удалось загрузить шрифт для генерации текста.")

    bounds = _get_textgroup_bounds(lottie)
    if bounds is None:
        raise ValueError("Шаблон не содержит редактируемой текстовой зоны.")

    new_shapes = _compose_brand_shapes(text, font_path, bounds, logo_svg, canvas_size=canvas_size)
    if not new_shapes:
        raise ValueError("Не удалось подготовить текст или логотип для шаблона.")

    tinted = tint_lottie(lottie, hex_color)
    main_replaced = _replace_textgroup(tinted, new_shapes)
    extra_replaced = _replace_username(tinted, text, font_path)
    if not main_replaced and not extra_replaced:
        raise ValueError("Не удалось встроить текст в шаблон.")

    out_data = _encode_lottie_tgs(tinted)
    if enforce_size_limit and len(out_data) > MAX_ANIMATED_STICKER_BYTES:
        raise ValueError(
            f"animated sticker is too big after customization: {len(out_data)} bytes > {MAX_ANIMATED_STICKER_BYTES}"
        )
    return out_data


def customize_tgs_template_with_secondary_text(
    tgs_bytes: bytes,
    text: str,
    secondary_text: str,
    hex_color: str,
    font_id: str,
    logo_svg: Optional[bytes] = None,
    enforce_size_limit: bool = True,
) -> bytes:
    raw = gzip.decompress(tgs_bytes)
    lottie = json.loads(raw.decode("utf-8"))

    font_path = get_font_path(font_id)
    if font_path is None:
        raise RuntimeError("Failed to load font for passport template rendering.")

    bounds = _get_textgroup_bounds(lottie)
    if bounds is None:
        raise ValueError("Template does not contain an editable primary text zone.")

    main_shapes = _text_shapes_in_bounds(text, font_path, bounds)
    if not main_shapes:
        raise ValueError("Failed to build passport nickname shapes.")

    tinted = tint_lottie(lottie, hex_color)
    main_replaced = _replace_textgroup(tinted, main_shapes)
    secondary_replaced = _replace_passport_username(tinted, secondary_text, font_path)
    if not secondary_replaced:
        secondary_replaced = _replace_named_shape_layer_text(
            tinted, "USERNAME", secondary_text, font_path, None,
        )
    if not secondary_replaced:
        secondary_replaced = _inject_passport_username_overlay(tinted, secondary_text, font_path, bounds)
    logo_replaced = _replace_logo_group(tinted, logo_svg) if logo_svg else False
    if not main_replaced and not secondary_replaced and not logo_replaced:
        raise ValueError("Failed to inject passport nickname, username, or logo.")

    out_data = _encode_lottie_tgs(tinted)
    if enforce_size_limit and len(out_data) > MAX_ANIMATED_STICKER_BYTES:
        raise ValueError(
            f"animated sticker is too big after customization: {len(out_data)} bytes > {MAX_ANIMATED_STICKER_BYTES}"
        )
    return out_data


def _sample_cubic(
    start: Tuple[float, float],
    control1: Tuple[float, float],
    control2: Tuple[float, float],
    end: Tuple[float, float],
    steps: int = 18,
) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    for idx in range(1, steps + 1):
        t = idx / float(steps)
        mt = 1.0 - t
        x = (
            mt ** 3 * start[0] + 3 * mt ** 2 * t * control1[0]
            + 3 * mt * t ** 2 * control2[0] + t ** 3 * end[0]
        )
        y = (
            mt ** 3 * start[1] + 3 * mt ** 2 * t * control1[1]
            + 3 * mt * t ** 2 * control2[1] + t ** 3 * end[1]
        )
        points.append((x, y))
    return points


def _sample_quadratic(
    start: Tuple[float, float],
    control: Tuple[float, float],
    end: Tuple[float, float],
    steps: int = 14,
) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    for idx in range(1, steps + 1):
        t = idx / float(steps)
        mt = 1.0 - t
        x = mt ** 2 * start[0] + 2 * mt * t * control[0] + t ** 2 * end[0]
        y = mt ** 2 * start[1] + 2 * mt * t * control[1] + t ** 2 * end[1]
        points.append((x, y))
    return points


@lru_cache(maxsize=256)
def _svg_to_preview_polygons(
    svg_bytes: bytes,
    target_bounds: Tuple[float, float, float, float],
    padding_ratio: float = 0.92,
) -> List[List[Tuple[float, float]]]:
    svg_classes = _load_svg_parser()
    if svg_classes is None:
        return []

    _, _, Move, Line, Close, CubicBezier, QuadraticBezier = svg_classes
    paths, source_bounds = _parse_svg_paths(svg_bytes)
    scale, offset_x, offset_y = _fit_bounds(source_bounds, target_bounds, padding_ratio=padding_ratio)

    polygons: List[List[Tuple[float, float]]] = []

    def _point(point: Any) -> Tuple[float, float]:
        x, y = _svg_point_xy(point)
        return (x * scale + offset_x, y * scale + offset_y)

    for path in paths:
        current: List[Tuple[float, float]] = []
        for segment in path:
            if isinstance(segment, Move):
                if len(current) >= 3:
                    polygons.append(current)
                current = [_point(segment.end)]
            elif isinstance(segment, Line):
                current.append(_point(segment.end))
            elif isinstance(segment, CubicBezier):
                if not current:
                    current = [_point(segment.start)]
                current.extend(
                    _sample_cubic(current[-1], _point(segment.control1), _point(segment.control2), _point(segment.end))
                )
            elif isinstance(segment, QuadraticBezier):
                if not current:
                    current = [_point(segment.start)]
                current.extend(_sample_quadratic(current[-1], _point(segment.control), _point(segment.end)))
            elif isinstance(segment, Close):
                if current and current[0] != current[-1]:
                    current.append(current[0])
                if len(current) >= 3:
                    polygons.append(current)
                current = []
        if len(current) >= 3:
            polygons.append(current)

    return polygons


def _fit_preview_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Optional[str],
    target_bounds: Tuple[float, float, float, float],
) -> Tuple[ImageFont.ImageFont, int]:
    x1, y1, x2, y2 = target_bounds
    box_w = max(int(x2 - x1), 1)
    box_h = max(int(y2 - y1), 1)

    max_size = max(12, int(box_h * 0.90))
    min_size = max(8, int(box_h * 0.28))
    font_key = font_path or ""
    best_size = min_size
    low = min_size
    high = max_size

    while low <= high:
        size = (low + high) // 2
        font = _load_preview_font(font_key, size)
        stroke = max(1, size // 14)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width <= box_w * 0.96 and height <= box_h * 0.96:
            best_size = size
            low = size + 1
        else:
            high = size - 1

    final_font = _load_preview_font(font_key, best_size)
    return final_font, max(1, best_size // 14)


@lru_cache(maxsize=256)
def _load_preview_font(font_path: str, size: int) -> ImageFont.ImageFont:
    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _preview_render_size_for_tile(tile_size: int) -> Tuple[int, int]:
    edge = min(512, max(256, int(tile_size * 2)))
    return edge, edge


def _draw_preview_text(
    image: Image.Image,
    text: str,
    hex_color: str,
    font_id: str,
    target_bounds: Tuple[float, float, float, float],
) -> None:
    if not text:
        return

    font_path = get_font_path(font_id)
    draw = ImageDraw.Draw(image, "RGBA")
    font, stroke = _fit_preview_font(draw, text, font_path, target_bounds)
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    x1, y1, x2, y2 = target_bounds
    text_x = x1 + ((x2 - x1) - (bbox[2] - bbox[0])) / 2.0 - bbox[0]
    text_y = y1 + ((y2 - y1) - (bbox[3] - bbox[1])) / 2.0 - bbox[1]
    r, g, b = hex_to_rgb(hex_color)
    draw.text(
        (text_x, text_y),
        text,
        font=font,
        fill=(r, g, b, 255),
        stroke_width=stroke,
        stroke_fill=(20, 20, 24, 180),
    )


def _draw_preview_logo(
    image: Image.Image,
    svg_bytes: bytes,
    hex_color: str,
    target_bounds: Tuple[float, float, float, float],
    padding_ratio: float = 0.92,
) -> None:
    polygons = _svg_to_preview_polygons(svg_bytes, target_bounds, padding_ratio=padding_ratio)
    if not polygons:
        return

    draw = ImageDraw.Draw(image, "RGBA")
    r, g, b = hex_to_rgb(hex_color)

    for polygon in polygons:
        shadow = [(x + 2.0, y + 2.0) for x, y in polygon]
        draw.polygon(shadow, fill=(18, 18, 22, 140))
        draw.polygon(polygon, fill=(r, g, b, 255))


def _project_layout_bounds(
    image_size: Tuple[int, int],
    layout_info: Tuple[int, int, Tuple[float, float, float, float]],
) -> Tuple[float, float, float, float]:
    image_w, image_h = image_size
    canvas_w, canvas_h, bounds = layout_info
    scale_x = image_w / float(max(canvas_w, 1))
    scale_y = image_h / float(max(canvas_h, 1))
    return (
        (bounds[0] + canvas_w / 2.0) * scale_x,
        (bounds[1] + canvas_h / 2.0) * scale_y,
        (bounds[2] + canvas_w / 2.0) * scale_x,
        (bounds[3] + canvas_h / 2.0) * scale_y,
    )


def _clear_preview_brand_area(
    image: Image.Image,
    target_bounds: Tuple[float, float, float, float],
) -> None:
    x1, y1, x2, y2 = target_bounds
    width = max(x2 - x1, 1.0)
    height = max(y2 - y1, 1.0)
    pad_x = width * 0.08
    pad_y = height * 0.14
    left = int(max(0.0, x1 - pad_x))
    top = int(max(0.0, y1 - pad_y))
    right = int(min(float(image.width), x2 + pad_x))
    bottom = int(min(float(image.height), y2 + pad_y))

    radius = max(6, int(min(right - left, bottom - top) * 0.22))
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=radius,
        fill=(22, 22, 26, 242),
    )


def render_tgs_preview_image(
    tgs_bytes: bytes,
    text: Optional[str] = None,
    hex_color: str = "#FFFFFF",
    font_id: str = "montserrat",
    layout_info: Optional[Tuple[int, int, Tuple[float, float, float, float]]] = None,
    logo_svg: Optional[bytes] = None,
    preview_bounds_override: Optional[Tuple[float, float, float, float]] = None,
    render_brand_overlay: Optional[bool] = None,
    render_size: Tuple[int, int] = (512, 512),
) -> Image.Image:
    lottie_animation = _load_rlottie()
    if lottie_animation is None:
        raise RuntimeError(_RLOTTIE_IMPORT_ERROR or "rlottie-python is not installed")

    raw = gzip.decompress(tgs_bytes).decode("utf-8")
    animation = lottie_animation.from_data(raw)
    try:
        source = animation.render_pillow_frame(
            frame_num=0,
            width=render_size[0],
            height=render_size[1],
        ).convert("RGBA")
    finally:
        try:
            animation.lottie_animation_destroy()
        except Exception:
            pass

    if text:
        if preview_bounds_override is not None:
            preview_bounds = preview_bounds_override
        elif layout_info is not None:
            preview_bounds = _project_layout_bounds((source.width, source.height), layout_info)
        else:
            preview_bounds = (
                source.width * 0.12,
                source.height * 0.62,
                source.width * 0.88,
                source.height * 0.90,
            )

        if render_brand_overlay is None:
            if layout_info is not None:
                auto_bounds = _project_layout_bounds((source.width, source.height), layout_info)
            else:
                auto_bounds = preview_bounds
            overlay_area = max(auto_bounds[2] - auto_bounds[0], 0.0) * max(auto_bounds[3] - auto_bounds[1], 0.0)
            render_brand_overlay = (layout_info is None or overlay_area < 1500.0)

        if render_brand_overlay:
            if logo_svg:
                brand_bounds = _resolve_brand_target_bounds(
                    preview_bounds, (float(source.width), float(source.height)), text or ""
                )
                _clear_preview_brand_area(source, brand_bounds)
                logo_bounds, text_bounds = _brand_logo_and_text_bounds(brand_bounds, text or "")
                _draw_preview_logo(source, logo_svg, hex_color, logo_bounds, padding_ratio=0.98)
                if text_bounds is not None:
                    _draw_preview_text(source, text, hex_color, font_id, text_bounds)
            else:
                _clear_preview_brand_area(source, preview_bounds)
                _draw_preview_text(source, text, hex_color, font_id, preview_bounds)

    return source


def build_tgs_preview_tile(
    tgs_bytes: bytes,
    text: Optional[str] = None,
    hex_color: str = "#FFFFFF",
    font_id: str = "montserrat",
    layout_info: Optional[Tuple[int, int, Tuple[float, float, float, float]]] = None,
    logo_svg: Optional[bytes] = None,
    preview_bounds_override: Optional[Tuple[float, float, float, float]] = None,
    tile_size: int = 220,
    render_brand_overlay: Optional[bool] = None,
) -> Image.Image:
    source = render_tgs_preview_image(
        tgs_bytes=tgs_bytes,
        text=text,
        hex_color=hex_color,
        font_id=font_id,
        layout_info=layout_info,
        logo_svg=logo_svg,
        preview_bounds_override=preview_bounds_override,
        render_brand_overlay=render_brand_overlay,
        render_size=_preview_render_size_for_tile(tile_size),
    )

    source.thumbnail((tile_size - 24, tile_size - 24), Image.LANCZOS)

    tile = Image.new("RGBA", (tile_size, tile_size), (34, 34, 38, 255))
    pos_x = (tile_size - source.width) // 2
    pos_y = (tile_size - source.height) // 2
    tile.alpha_composite(source, (pos_x, pos_y))
    return tile


def build_template_preview_tile(
    thumbnail_bytes: bytes,
    text: str,
    hex_color: str,
    font_id: str,
    layout_info: Optional[Tuple[int, int, Tuple[float, float, float, float]]] = None,
    logo_svg: Optional[bytes] = None,
    tile_size: int = 220,
) -> Image.Image:
    try:
        source = Image.open(io.BytesIO(thumbnail_bytes)).convert("RGBA")
    except Exception:
        source = Image.new("RGBA", (512, 512), (0, 0, 0, 0))

    source = tint_image(source, hex_color)

    if layout_info is not None:
        preview_bounds = _project_layout_bounds((source.width, source.height), layout_info)
    else:
        preview_bounds = (
            source.width * 0.12,
            source.height * 0.62,
            source.width * 0.88,
            source.height * 0.90,
        )

    if logo_svg:
        brand_bounds = _resolve_brand_target_bounds(
            preview_bounds, (float(source.width), float(source.height)), text
        )
        _clear_preview_brand_area(source, brand_bounds)
        logo_bounds, text_bounds = _brand_logo_and_text_bounds(brand_bounds, text)
        _draw_preview_logo(source, logo_svg, hex_color, logo_bounds, padding_ratio=0.98)
        if text_bounds is not None:
            _draw_preview_text(source, text, hex_color, font_id, text_bounds)
    else:
        _clear_preview_brand_area(source, preview_bounds)
        _draw_preview_text(source, text, hex_color, font_id, preview_bounds)

    source.thumbnail((tile_size - 24, tile_size - 24), Image.LANCZOS)

    tile = Image.new("RGBA", (tile_size, tile_size), (34, 34, 38, 255))
    pos_x = (tile_size - source.width) // 2
    pos_y = (tile_size - source.height) // 2
    tile.alpha_composite(source, (pos_x, pos_y))
    return tile


def validate_short_name(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name)) and "__" not in name




# ---------------------------------------------------------------------------
# Watermark helper (PIL-only, no bot dependency)
# ---------------------------------------------------------------------------

def stamp_watermark(image: Image.Image, text: str) -> Image.Image:
    """Stamp diagonal repeating watermark on an RGBA PIL image. Returns new image."""
    if not text:
        return image
    result = image.convert("RGBA")
    layer = Image.new("RGBA", result.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    font_size = max(14, min(26, result.width // 13))
    wm_font = _get_preview_font_cached(font_size)

    try:
        bbox = draw.textbbox((0, 0), text, font=wm_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        return result

    step_x = tw + 80
    step_y = th + 70
    if step_x <= 0 or step_y <= 0:
        return result

    y = -step_y * 2
    row = 0
    while y < result.height + step_y * 2:
        offset = (step_x // 2) if row % 2 else 0
        x = -step_x + offset
        while x < result.width + step_x:
            draw.text((x, y), text, font=wm_font, fill=(255, 255, 255, 46))
            x += step_x
        y += step_y
        row += 1

    rotated = layer.rotate(28, expand=False, resample=Image.BICUBIC)
    result.alpha_composite(rotated)
    return result


def _get_preview_font_cached(size: int) -> ImageFont.ImageFont:
    """Get a small font for watermarks/labels, cached."""
    # Try fonts in priority order
    candidates = []
    # Prefer montserrat if downloaded
    mono_path = os.path.join(_FONT_CACHE_DIR, "Montserrat-ExtraBold.ttf")
    if os.path.exists(mono_path):
        candidates.append(mono_path)
    noto_path = os.path.join(_FONT_CACHE_DIR, "NotoSans-Bold.ttf")
    if os.path.exists(noto_path):
        candidates.append(noto_path)
    candidates.extend([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
    ])
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Font preview grid for font selection screen
# ---------------------------------------------------------------------------

CATALOG_WATERMARK_TEXT = "Solutions"
CATALOG_PREVIEW_TEXT = "Solutions"


def generate_font_preview_image(
    sample_text: str,
    watermark_text: str = CATALOG_WATERMARK_TEXT,
    tile_size: int = 200,
    columns: int = 2,
    background: tuple = (18, 18, 22, 255),
) -> bytes:
    """
    Renders a grid of font preview tiles for all available font presets.
    Each tile shows sample_text in that font. Watermark stamped diagonally.
    Returns PNG bytes.
    """
    font_ids = [k for k in _FONT_PRESETS.keys() if k != "default"]
    tiles_data = []

    for idx, font_id in enumerate(font_ids):
        label = str(_FONT_PRESETS[font_id].get("label") or font_id)
        tile = _render_font_preview_tile(
            font_id=font_id,
            sample_text=sample_text or CATALOG_PREVIEW_TEXT,
            number=idx + 1,
            tile_size=tile_size,
        )
        tiles_data.append((idx + 1, font_id, label, tile))

    num_tiles = len(tiles_data)
    if num_tiles == 0:
        img = Image.new("RGBA", (400, 200), background)
        buf = io.BytesIO()
        img.save(buf, format="PNG", compress_level=1)
        return buf.getvalue()

    rows = (num_tiles + columns - 1) // columns
    gap = 10
    padding = 14
    label_h = 28

    img_w = padding * 2 + columns * tile_size + gap * max(columns - 1, 0)
    img_h = padding * 2 + rows * (tile_size + label_h) + gap * max(rows - 1, 0)

    image = Image.new("RGBA", (img_w, img_h), background)
    draw = ImageDraw.Draw(image)
    label_font = _get_preview_font_cached(15)

    for i, (number, font_id, label, tile) in enumerate(tiles_data):
        row = i // columns
        col = i % columns
        x = padding + col * (tile_size + gap)
        y = padding + row * (tile_size + label_h + gap)
        image.alpha_composite(tile, (x, y))
        # Label centered under tile
        try:
            draw.text(
                (x + tile_size // 2, y + tile_size + 4),
                f"{number}. {label}",
                font=label_font,
                fill=(200, 200, 220, 220),
                anchor="mt",
            )
        except Exception:
            draw.text((x + 4, y + tile_size + 4), f"{number}. {label}",
                      font=label_font, fill=(200, 200, 220, 220))

    # Stamp watermark
    image = stamp_watermark(image, watermark_text)

    buf = io.BytesIO()
    image.save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


def _render_font_preview_tile(
    font_id: str,
    sample_text: str,
    number: int,
    tile_size: int = 200,
) -> Image.Image:
    """Renders one font preview tile: dark bg, text in that font, number badge."""
    tile = Image.new("RGBA", (tile_size, tile_size), (28, 28, 34, 255))
    draw = ImageDraw.Draw(tile)

    font_path = get_font_path(font_id)
    text = sample_text or "Solutions"

    max_w = int(tile_size * 0.90)
    max_h = int(tile_size * 0.52)

    best_font = None
    if font_path and os.path.exists(font_path):
        lo, hi = 10, max_h
        best_size = lo
        while lo <= hi:
            mid = (lo + hi) // 2
            try:
                f = ImageFont.truetype(font_path, mid)
                bb = draw.textbbox((0, 0), text, font=f)
                w, h = bb[2] - bb[0], bb[3] - bb[1]
                if w <= max_w and h <= max_h:
                    best_size = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            except Exception:
                hi = mid - 1
        try:
            best_font = ImageFont.truetype(font_path, best_size)
        except Exception:
            best_font = None

    if best_font is None:
        best_font = ImageFont.load_default()

    # Draw text centered vertically (slightly above center to leave room for number)
    try:
        bb = draw.textbbox((0, 0), text, font=best_font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        tx = (tile_size - tw) // 2 - bb[0]
        ty = (tile_size - th) // 2 - bb[1] - tile_size // 14
        # Drop shadow
        draw.text((tx + 2, ty + 2), text, font=best_font, fill=(0, 0, 0, 160))
        # Main text — cyan glow colour
        draw.text((tx, ty), text, font=best_font, fill=(48, 213, 255, 255))
    except Exception:
        draw.text((8, tile_size // 2 - 10), text, fill=(48, 213, 255, 255))

    # Number badge top-left
    _draw_number_badge(draw, number, tile_size)

    return tile


def _draw_number_badge(draw: ImageDraw.ImageDraw, number: int, tile_size: int) -> None:
    label = str(number)
    fsize = max(11, min(18, tile_size // 10))
    font = _get_preview_font_cached(fsize)
    try:
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad_x, pad_y = 5, 3
        bx1, by1 = 6, 6
        bx2, by2 = bx1 + tw + pad_x * 2, by1 + th + pad_y * 2
        draw.rounded_rectangle([bx1, by1, bx2, by2], radius=4, fill=(10, 10, 14, 210))
        draw.text(
            (bx1 + pad_x - bbox[0], by1 + pad_y - bbox[1]),
            label, font=font, fill=(255, 255, 255, 240),
        )
    except Exception:
        pass


def add_global_watermark(tgs_bytes: bytes, watermark_text: str) -> bytes:
    """Pass-through stub — watermark is applied via pack title in bot.py."""
    return tgs_bytes