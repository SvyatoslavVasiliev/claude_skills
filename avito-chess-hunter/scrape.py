#!/usr/bin/env python3
"""
Сбор объявлений с Avito через реальный браузер.

ЗАПУСКАТЬ ЛОКАЛЬНО. Из облачной среды Avito недоступен (egress-блокировка,
curl отдаёт code=000), поэтому селекторы ниже НЕ ПРОВЕРЕНЫ на живом сайте.
Расчёт на это заложен: при неудаче разбора скрипт сохраняет сырой HTML в
debug/ и продолжает. Пришлите один такой файл — поправлю разбор точно.

Установка:
    pip install playwright pyyaml
    playwright install chromium

Первый запуск (обязательно с окном, чтобы пройти капчу руками):
    python scrape.py --headful --max-pages 2

Дальше:
    python scrape.py --out items.jsonl
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
import sys
import time
from typing import Any, Iterator

import yaml

HERE = pathlib.Path(__file__).parent
DEBUG_DIR = HERE / "debug"
PROFILE_DIR = HERE / ".browser-profile"   # сюда ляжет cookie после ручной капчи

ITEM_ID_RE = re.compile(r"_(\d{6,})(?:\?|$)")


def load_cfg() -> dict:
    with open(HERE / "queries.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def build_url(base: str, category: str, location: str, query: str,
              page: int, sort: str) -> str:
    path = f"{base}/{location}"
    if category:
        path += f"/{category}"
    params = [f"q={query}", f"p={page}"]
    if sort == "date":
        params.append("s=104")        # Avito: сортировка по дате
    return f"{path}?" + "&".join(params)


# --- разбор -----------------------------------------------------------------
# Две независимые стратегии. JSON-состояние надёжнее, но Avito его переименовывает,
# поэтому DOM оставлен запасным путём.

STATE_KEYS = ("__initialData__", "__INITIAL_STATE__", "__initial_state__")


def parse_from_state(html: str) -> list[dict]:
    """Достать лоты из встроенного JSON-состояния страницы."""
    for key in STATE_KEYS:
        m = re.search(rf'window\.{key}\s*=\s*(["\'])(.*?)\1;', html, re.S)
        if not m:
            m = re.search(rf'window\.{key}\s*=\s*(\{{.*?\}});\s*\n', html, re.S)
            if not m:
                continue
            blob = m.group(1)
        else:
            import urllib.parse
            blob = urllib.parse.unquote(m.group(2))
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        found = list(_walk_for_items(data))
        if found:
            return found
    return []


def _walk_for_items(node: Any) -> Iterator[dict]:
    """Avito прячет лоты на разной глубине; ищем по форме объекта, не по пути."""
    if isinstance(node, dict):
        if "id" in node and ("title" in node or "urlPath" in node):
            rec = _normalise(node)
            if rec:
                yield rec
        for v in node.values():
            yield from _walk_for_items(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_for_items(v)


def _normalise(node: dict) -> dict | None:
    try:
        item_id = int(node["id"])
    except (KeyError, TypeError, ValueError):
        return None
    if item_id < 100000:
        return None
    url_path = node.get("urlPath") or node.get("url") or ""
    price = node.get("priceDetailed") or {}
    images: list[str] = []
    for img in (node.get("images") or []):
        if isinstance(img, dict):
            # у Avito словарь ширина->url; берём самый большой
            urls = [v for k, v in img.items() if isinstance(v, str) and v.startswith("http")]
            if urls:
                images.append(max(urls, key=len))
        elif isinstance(img, str):
            images.append(img)
    return {
        "item_id": item_id,
        "url": ("https://www.avito.ru" + url_path) if url_path.startswith("/") else url_path,
        "title": node.get("title", ""),
        "description": node.get("description", "") or "",
        "price": price.get("value") if isinstance(price, dict) else node.get("price"),
        "price_string": (price.get("string") if isinstance(price, dict) else None) or "",
        "location": (node.get("location") or {}).get("name", "") if isinstance(node.get("location"), dict) else "",
        "seller_name": (node.get("seller") or {}).get("name", "") if isinstance(node.get("seller"), dict) else "",
        "is_shop": bool(node.get("isShop") or node.get("shop")),
        "photos": images,
        "photo_count": len(images),
    }


def parse_from_dom(page) -> list[dict]:
    """Запасной разбор по data-marker, если JSON-состояние не нашлось."""
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('[data-marker="item"]')).map(el => {
            const a = el.querySelector('[itemprop="url"], a[href*="_"]');
            const t = el.querySelector('[itemprop="name"], h3');
            const p = el.querySelector('[itemprop="price"], [data-marker="item-price"]');
            const d = el.querySelector('[class*="description"]');
            const imgs = Array.from(el.querySelectorAll('img')).map(i => i.src).filter(Boolean);
            return {
                item_id: parseInt(el.getAttribute('data-item-id') || '0', 10),
                url: a ? a.href : '',
                title: t ? t.textContent.trim() : '',
                description: d ? d.textContent.trim() : '',
                price_string: p ? (p.getAttribute('content') || p.textContent.trim()) : '',
                photos: imgs,
                photo_count: imgs.length,
            };
        })"""
    )


def looks_blocked(html: str) -> bool:
    low = html.lower()
    return any(s in low for s in (
        "доступ ограничен", "подтвердите, что вы не робот", "firewall",
        "captcha", "ваш ip", "too many requests",
    ))


# --- основной цикл ----------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="items.jsonl")
    ap.add_argument("--headful", action="store_true",
                    help="показать окно браузера (нужно на первом запуске, чтобы пройти капчу)")
    ap.add_argument("--max-pages", type=int, default=None, help="перекрыть значение из queries.yaml")
    ap.add_argument("--delay", type=float, default=4.0, help="базовая пауза между страницами, сек")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    cfg = load_cfg()
    s = cfg["search"]
    max_pages = args.max_pages or s["max_pages"]
    DEBUG_DIR.mkdir(exist_ok=True)

    seen: set[int] = set()
    out_path = HERE / args.out
    # дозапись: прогон можно прервать и продолжить, дубли отсекаются по item_id
    if out_path.exists():
        with open(out_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    seen.add(json.loads(line)["item_id"])
                except Exception:
                    pass
        print(f"продолжаю: уже собрано {len(seen)} лотов", file=sys.stderr)

    written = 0
    with sync_playwright() as pw, open(out_path, "a", encoding="utf-8") as sink:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=not args.headful,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1440, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
        )
        page = ctx.new_page()

        for category in cfg["categories"]:
            for query in s["queries"]:
                empty_streak = 0
                for pno in range(1, max_pages + 1):
                    url = build_url(s["base"], category, s["location"], query, pno, s["sort"])
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    except Exception as exc:
                        print(f"[warn] {url}: {exc}", file=sys.stderr)
                        break
                    page.wait_for_timeout(1500)
                    html = page.content()

                    if looks_blocked(html):
                        print("\n!! Avito показал капчу или блок.", file=sys.stderr)
                        if args.headful:
                            input("   Пройдите её в окне браузера и нажмите Enter...")
                            html = page.content()
                        else:
                            print("   Перезапустите с --headful и пройдите капчу один раз.",
                                  file=sys.stderr)
                            return 2

                    items = parse_from_state(html)
                    if not items:
                        try:
                            items = [i for i in parse_from_dom(page) if i.get("item_id")]
                        except Exception:
                            items = []
                    if not items:
                        tag = f"{category or 'all'}_{query}_{pno}".replace(" ", "_").replace("/", "_")
                        (DEBUG_DIR / f"{tag}.html").write_text(html, encoding="utf-8")
                        empty_streak += 1
                        print(f"[miss] разбор не дал лотов, HTML в debug/{tag}.html", file=sys.stderr)
                        if empty_streak >= 2:
                            break          # либо кончились страницы, либо сломались селекторы
                        continue
                    empty_streak = 0

                    fresh = 0
                    for it in items:
                        if it["item_id"] in seen:
                            continue
                        seen.add(it["item_id"])
                        it["query"] = query
                        it["category"] = category
                        it["scraped_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                        sink.write(json.dumps(it, ensure_ascii=False) + "\n")
                        fresh += 1
                    sink.flush()
                    written += fresh
                    print(f"[{category or 'all'}] «{query}» стр.{pno}: "
                          f"{len(items)} на странице, {fresh} новых, всего {written}")

                    if fresh == 0 and pno > 3:
                        break              # дальше идут одни повторы
                    time.sleep(args.delay * random.uniform(0.7, 1.5))
        ctx.close()

    print(f"\nГотово: {written} новых лотов -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
