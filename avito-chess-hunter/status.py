#!/usr/bin/env python3
"""
Промежуточный срез: что собрано на данный момент.

Читает items.jsonl только на чтение, поэтому запускать можно параллельно
работающему сбору — данные пишутся построчно с flush, недописанная
последняя строка просто пропускается.

    python3 status.py                 срез
    python3 status.py --top 20        плюс топ-20 кандидатов прямо сейчас
    python3 status.py --watch         обновлять каждые 30 сек
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import time
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
from score import score_item  # noqa: E402


def load(path: pathlib.Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass          # последняя строка могла не дописаться — не беда
    return rows


def age_minutes(ts: str) -> float | None:
    try:
        t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 60
    except Exception:
        return None


def snapshot(path: pathlib.Path, top: int) -> None:
    rows = load(path)
    if not rows:
        print(f"{path} пуст или отсутствует — сбор ещё ничего не записал.")
        return

    scored = [score_item(r) for r in rows]
    passing = [r for r in scored if r["score"] >= 0.15]
    excluded = [r for r in scored if r.get("excluded_by", "-") != "-"]

    ages = [a for a in (age_minutes(r.get("scraped_at", "")) for r in rows) if a is not None]
    last5 = sum(1 for a in ages if a <= 5)
    last30 = sum(1 for a in ages if a <= 30)

    prices = [r["price"] for r in rows if isinstance(r.get("price"), (int, float))]

    print("=" * 62)
    print(f"СРЕЗ на {time.strftime('%H:%M:%S')}")
    print("=" * 62)
    print(f"  собрано всего:            {len(rows):>6}")
    print(f"  за последние 5 мин:       {last5:>6}   <- растёт = сбор жив")
    print(f"  за последние 30 мин:      {last30:>6}")
    print(f"  проходят порог 0.15:      {len(passing):>6}   ({100*len(passing)//max(len(rows),1)}%)")
    print(f"  отсеяно по датировке:     {len(excluded):>6}   (знак качества, Олимпиада-80 и т.п.)")
    if prices:
        prices.sort()
        print(f"  цена: медиана {prices[len(prices)//2]:,} | "
              f"мин {prices[0]:,} | макс {prices[-1]:,} ₽".replace(",", " "))

    by_q = collections.Counter(r.get("query", "?") for r in rows)
    print("\n  по запросам:")
    for q, n in by_q.most_common(10):
        print(f"    {n:>5}  {q}")

    if top:
        print(f"\n  ТОП-{top} кандидатов на текущий момент:")
        print(f"  {'score':>6}  {'цена':>9}  заголовок")
        print("  " + "-" * 58)
        for r in sorted(passing, key=lambda x: -x["score"])[:top]:
            price = f"{r['price']:,}".replace(",", " ") if isinstance(r.get("price"), (int, float)) else "—"
            print(f"  {r['score']:>6.2f}  {price:>9}  {(r.get('title') or '')[:44]}")
        if not passing:
            print("    пока никого — либо рано, либо фильтр надо калибровать")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("infile", nargs="?", default="items.jsonl")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--watch", action="store_true", help="обновлять каждые 30 сек")
    args = ap.parse_args()

    path = pathlib.Path(args.infile)
    if not args.watch:
        snapshot(path, args.top)
        return 0
    try:
        while True:
            print("\033[2J\033[H", end="")     # очистить экран
            snapshot(path, args.top)
            print("  (Ctrl+C — выйти; на сбор это не влияет)")
            time.sleep(30)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
