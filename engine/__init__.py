"""Движок подстановки текста в TGS.

Внутри лежат два модуля, взятые из рабочего проекта без изменений:

* ``sticker_utils``  — низкоуровневые операции с Lottie (контуры букв,
  упаковка .tgs, работа со шрифтами);
* ``tgs_text_pack``  — поиск нарисованного контурами слова TEXT в шаблоне
  и его замена на надпись пользователя с сохранением анимации.

Оба модуля написаны как top-level (``import sticker_utils``), поэтому
папку добавляем в путь поиска — так их не нужно править.
"""

from __future__ import annotations

import os
import sys

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import sticker_utils  # noqa: E402
import tgs_text_pack  # noqa: E402

__all__ = ["sticker_utils", "tgs_text_pack"]
