#!/usr/bin/env python3
"""
Скачать фото ТОЛЬКО для шортлиста.

Смысл в экономии: полная выгрузка — десятки тысяч лотов по 5-10 фото.
Качать всё бессмысленно и незачем. Текстовый фильтр уже отсеял массу,
качаем сотню верхних, и именно их смотрит vision-проход.

    python fetch_photos.py items.jsonl shortlist.csv --out photos/
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
import time
import urllib.request


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("items", help="items.jsonl от scrape.py (там URL фото)")
    ap.add_argument("shortlist", help="shortlist.csv от score.py (там какие лоты нужны)")
    ap.add_argument("--out", default="photos")
    ap.add_argument("--per-item", type=int, default=6, help="сколько фото на лот")
    ap.add_argument("--delay", type=float, default=0.4)
    args = ap.parse_args()

    wanted: list[str] = []
    with open(args.shortlist, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("item_id"):
                wanted.append(row["item_id"])
    wanted_set = set(wanted)
    if not wanted_set:
        print("шортлист пуст — нечего качать", file=sys.stderr)
        return 1

    photos: dict[str, list[str]] = {}
    with open(args.items, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            it = json.loads(line)
            iid = str(it.get("item_id"))
            if iid in wanted_set:
                photos[iid] = (it.get("photos") or [])[: args.per_item]

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ok = fail = skip = 0
    # порядок как в шортлисте: сначала самые интересные
    for iid in wanted:
        for n, url in enumerate(photos.get(iid, []), 1):
            dest = out / f"{iid}_{n:02d}.jpg"
            if dest.exists():
                skip += 1
                continue
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
                    "Referer": "https://www.avito.ru/",
                })
                with urllib.request.urlopen(req, timeout=30) as r:
                    dest.write_bytes(r.read())
                ok += 1
            except Exception as exc:
                print(f"[warn] {iid} #{n}: {exc}", file=sys.stderr)
                fail += 1
            time.sleep(args.delay)

    print(f"скачано {ok}, пропущено {skip}, ошибок {fail} -> {out}/")
    print("дальше: покажи Claude папку и vision_prompt.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
