"""Разогрев кэша превью: рисует PNG для всех шаблонов заранее.

Бот умеет считать превью на лету, но первое открытие каталога тогда
занимает пару секунд на страницу. Скрипт прогоняет все шаблоны один раз
после деплоя, и каталог открывается мгновенно.

Надпись на превью должна совпадать с той, что бот рисует в каталоге, —
это его собственный юзернейм. Без аргумента считается кэш «как есть»,
со словом TEXT из шаблона.

Запуск:  python tools/build_previews.py [@my_bot] [--force]
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tgs_engine as engine  # noqa: E402

args = [a for a in sys.argv[1:] if a != "--force"]
force = "--force" in sys.argv
sample = args[0] if args else ""

numbers = engine.available_templates()
print(f"Шаблонов найдено: {len(numbers)}; надпись: {sample or 'без подстановки'}")

started = time.time()
failed = []
for number in numbers:
    path = engine.preview_path(number, sample)
    if force and os.path.exists(path):
        os.remove(path)
    try:
        engine.ensure_preview(number, sample)
        print(f"  {number:03d} ok")
    except Exception as exc:
        failed.append((number, exc))
        print(f"  {number:03d} ОШИБКА: {exc}")

print(f"\nГотово за {time.time() - started:.1f} с, ошибок: {len(failed)}")
if failed:
    sys.exit(1)
