#!/usr/bin/env python3
"""
Что на самом деле в дампе из debug/: блокировка или сломанный парсер?

Это два разных диагноза с разным лечением, и по строке «[miss] разбор не
дал лотов» их не различить. Скрипт отвечает прямо.

    python3 inspect_dump.py debug/*.html
"""
from __future__ import annotations

import pathlib
import re
import sys

BLOCK_MARKERS = (
    "доступ ограничен", "подтвердите, что вы не робот", "вы не робот",
    "слишком много запросов", "превышено количество запросов",
    "ваш ip-адрес заблокирован", "к сожалению, доступ",
)
# Признаки того, что страница с выдачей пришла нормально.
ITEM_MARKERS = ('data-marker="item"', 'itemprop="offers"', '"priceDetailed"', 'data-item-id')
STATE_MARKERS = ("__initialData__", "__INITIAL_STATE__", "__initial_state__")

TAGS = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)
ANY_TAG = re.compile(r"<[^>]+>")


def visible(html: str) -> str:
    s = TAGS.sub(" ", html)
    s = ANY_TAG.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def verdict(path: pathlib.Path) -> None:
    html = path.read_text(encoding="utf-8", errors="replace")
    vis = visible(html)
    vis_low = vis.lower()

    blocks = [m for m in BLOCK_MARKERS if m in vis_low]
    items = [m for m in ITEM_MARKERS if m in html]
    states = [m for m in STATE_MARKERS if m in html]

    print(f"\n=== {path.name}")
    print(f"  размер: {len(html):>9,} байт | видимого текста: {len(vis):>7,} симв.")

    if blocks:
        print(f"  ВЕРДИКТ: БЛОКИРОВКА — в видимом тексте: {', '.join(blocks)}")
        print("  Лечение: сбавить темп (--delay 40 --pages-per-query 2), подождать.")
        print("           Парсер трогать НЕ надо.")
    elif len(vis) < 300:
        print("  ВЕРДИКТ: пустая страница — вероятно капча в iframe или редирект.")
        print("  Лечение: запустить с --headful и посмотреть глазами.")
    elif items or states:
        print(f"  ВЕРДИКТ: СТРАНИЦА С ВЫДАЧЕЙ ПРИШЛА, но парсер её не разобрал.")
        print(f"           найдено в HTML: {', '.join(items + states)}")
        print("  Лечение: это настоящая работа для Claude — скажите ему")
        print(f"           «поправь разбор в scrape.py по дампу {path.name}».")
    else:
        print("  ВЕРДИКТ: не выдача и не явная блокировка.")
        print(f"  Начало видимого текста: {vis[:200]}")
        print("  Лечение: откройте файл в браузере и посмотрите, что это.")


def main() -> int:
    args = sys.argv[1:]
    if not args:
        d = pathlib.Path("debug")
        args = [str(p) for p in sorted(d.glob("*.html"))] if d.is_dir() else []
    if not args:
        print("нечего смотреть: передайте файлы или положите их в debug/", file=sys.stderr)
        return 1
    for a in args:
        verdict(pathlib.Path(a))
    print("\nЕсли вердикт БЛОКИРОВКА у всех — парсер в порядке, вопрос только в темпе.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
