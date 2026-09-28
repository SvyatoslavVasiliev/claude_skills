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
import signal
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

# Выход в моменте с сохранением. Данные пишутся в items.jsonl построчно с
# flush после каждой страницы, поэтому собранное не теряется никогда — но
# нужен способ остановиться, не убивая процесс на полуслове.
_STOP = {"requested": False}


def _install_sigint() -> None:
    def handler(signum, frame):
        if _STOP["requested"]:
            # второй Ctrl+C — выходим жёстко
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            raise KeyboardInterrupt
        _STOP["requested"] = True
        print("\n[stop] остановлюсь после текущей страницы, собранное сохранено.\n"
              "       ещё раз Ctrl+C — выход немедленно.", file=sys.stderr)
    signal.signal(signal.SIGINT, handler)


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


# Маркеры ищем ТОЛЬКО в видимом тексте страницы. Прежняя версия скармливала
# сюда page.content(), то есть весь HTML вместе с инлайн-скриптами, — а в
# бандле антибота Avito слово "captcha" присутствует всегда. Детектор
# срабатывал на каждой странице. Классический ложноположительный.
BLOCK_MARKERS = (
    "доступ ограничен",
    "подтвердите, что вы не робот",
    "вы не робот",
    "слишком много запросов",
    "превышено количество запросов",
    "ваш ip-адрес заблокирован",
)


def visible_text(page, limit: int = 4000) -> str:
    """Видимый текст body, без script/style."""
    try:
        return (page.inner_text("body", timeout=5000) or "")[:limit].lower()
    except Exception:
        return ""


def detect_block(page, status: int | None, n_items: int) -> str:
    """Вернуть причину блокировки или пустую строку.

    Блокировкой считаем только то, что реально ею является:
      - HTTP 403/429 от сервера, либо
      - лотов не разобрано И в ВИДИМОМ тексте есть маркер блокировки.
    Наличие маркера при живых лотах блокировкой не считается: значит
    это текст внутри страницы, а не заглушка вместо неё.
    """
    if status in (403, 429):
        return f"HTTP {status}"
    if n_items == 0:
        vis = visible_text(page)
        for m in BLOCK_MARKERS:
            if m in vis:
                return f"на странице: «{m}»"
        # Пустая страница без маркеров — либо капча в iframe, либо конец выдачи.
        if len(vis) < 200:
            return "страница пуста"
    return ""


# --- основной цикл ----------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="items.jsonl")
    ap.add_argument("--cdp", metavar="URL", default="",
                    help="подключиться к уже запущенному Chrome, например http://localhost:9222 "
                         "(лучший режим: настоящий отпечаток, ваши куки, капча уже пройдена)")
    ap.add_argument("--channel", default="", metavar="NAME",
                    help="использовать настоящий браузер вместо сборки Chromium: chrome | msedge")
    ap.add_argument("--headful", action="store_true",
                    help="показать окно браузера (нужно на первом запуске, чтобы пройти капчу)")
    ap.add_argument("--max-pages", type=int, default=None, help="перекрыть значение из queries.yaml")
    ap.add_argument("--delay", type=float, default=20.0,
                    help="базовая пауза между страницами, сек (меньше 15 — быстрая блокировка)")
    ap.add_argument("--max-minutes", type=float, default=0,
                    help="остановиться через N минут и сохранить собранное (0 = без лимита)")
    ap.add_argument("--max-items", type=int, default=0,
                    help="остановиться, набрав N новых лотов (0 = без лимита)")
    ap.add_argument("--pages-per-query", type=int, default=3,
                    help="страниц на запрос за один проход; глубокая пагинация = главный признак бота")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    _install_sigint()

    cfg = load_cfg()
    s_cfg = cfg["search"]
    if args.max_pages:
        args.pages_per_query = args.max_pages
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
        # --- три режима запуска браузера -----------------------------------
        # Avito различает не частоту, а ОТПЕЧАТОК. Симптом: в личном Chrome
        # всё открывается, в Playwright-Chromium с того же IP — блокировка.
        # Значит сбавлять паузы бесполезно, надо перестать выглядеть ботом.
        #
        # Раньше здесь подставлялся user_agent с "Windows NT 10.0" на macOS —
        # при том, что navigator.platform, WebGL-рендерер и Client Hints
        # говорили Mac. Такое противоречие само выдаёт автоматизацию.
        # Поэтому UA больше не подставляется вообще: настоящий браузер
        # представляется сам, и это согласованно.
        browser = None
        if args.cdp:
            # Лучший режим: подключаемся к ВАШЕМУ уже запущенному Chrome.
            # Отпечаток настоящий, потому что браузер настоящий; куки и
            # пройденная капча уже на месте.
            print(f"подключаюсь к Chrome по CDP: {args.cdp}", file=sys.stderr)
            browser = pw.chromium.connect_over_cdp(args.cdp)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
        else:
            launch_kw = dict(
                headless=not args.headful,
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                args=["--disable-blink-features=AutomationControlled"],
                viewport=None,          # окно как у человека, без фиксированной рамки
            )
            if args.channel:
                # Настоящий Chrome вместо Playwright-сборки Chromium:
                # другой набор кодеков, Widevine, UA — заметно меньше подозрений.
                launch_kw["channel"] = args.channel
            ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), **launch_kw)
            page = ctx.new_page()

        # Снять самые грубые маркеры автоматизации. Помогает не всегда —
        # это гонка вооружений, а не решение. Решение — режим --cdp.
        try:
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                "window.chrome=window.chrome||{runtime:{}};"
                "Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru','en-US']});"
            )
        except Exception:
            pass

        # --- форма обхода -------------------------------------------------
        # Раньше шли глубокой пагинацией по одному запросу: 1,2,3...50.
        # Это и есть самый яркий признак бота, и именно на этом Avito
        # закрывался к 4-5 странице.
        #
        # Заодно такая форма собирала наименее полезное. Сортировка по дате,
        # лоты уходят за часы — значит страница 40 старого запроса мертва,
        # а ценность сидит в первых 2-3 страницах МНОГИХ запросов. То есть
        # мелко-широкий обход и безопаснее, и полезнее. Редкий случай, когда
        # ограничение и польза указывают в одну сторону.
        tasks = [(c, q) for c in cfg["categories"] for q in s_cfg["queries"]]
        random.shuffle(tasks)

        consecutive_blocks = 0
        backoff = [120, 300, 900]      # сек: отступаем, а не падаем

        deadline = (time.time() + args.max_minutes * 60) if args.max_minutes else None

        for category, query in tasks:
            if _STOP["requested"]:
                break
            for pno in range(1, args.pages_per_query + 1):
                if _STOP["requested"]:
                    break
                if deadline and time.time() > deadline:
                    print(f"[stop] лимит {args.max_minutes} мин исчерпан", file=sys.stderr)
                    _STOP["requested"] = True
                    break
                if args.max_items and written >= args.max_items:
                    print(f"[stop] набрано {written} лотов, лимит достигнут", file=sys.stderr)
                    _STOP["requested"] = True
                    break
                url = build_url(s_cfg["base"], category, s_cfg["location"], query, pno, s_cfg["sort"])
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    status = resp.status if resp else None
                except Exception as exc:
                    print(f"[warn] {url}: {exc}", file=sys.stderr)
                    break

                # немного человеческого поведения перед разбором
                page.wait_for_timeout(random.randint(1200, 3000))
                try:
                    page.mouse.wheel(0, random.randint(300, 1200))
                    page.wait_for_timeout(random.randint(400, 1200))
                except Exception:
                    pass

                html = page.content()
                items = parse_from_state(html)
                if not items:
                    try:
                        items = [i for i in parse_from_dom(page) if i.get("item_id")]
                    except Exception:
                        items = []

                reason = detect_block(page, status, len(items))
                if reason:
                    consecutive_blocks += 1
                    print(f"\n!! блокировка ({reason}), подряд: {consecutive_blocks}", file=sys.stderr)
                    if args.headful:
                        print("   Enter — продолжить (если капча, сначала пройдите её в окне)",
                              file=sys.stderr)
                        print("   s + Enter — остановиться СЕЙЧАС и сохранить собранное",
                              file=sys.stderr)
                        try:
                            if (input("   > ").strip().lower() or "")[:1] == "s":
                                _STOP["requested"] = True
                                break
                        except (EOFError, KeyboardInterrupt):
                            _STOP["requested"] = True
                            break
                    if consecutive_blocks > len(backoff):
                        print("   Avito закрылся всерьёз. Останавливаюсь — продолжите позже,\n"
                              "   собранное уже в items.jsonl, повторный запуск продолжит с места.",
                              file=sys.stderr)
                        _STOP["requested"] = True
                        break
                    pause = backoff[consecutive_blocks - 1]
                    print(f"   пауза {pause} сек", file=sys.stderr)
                    time.sleep(pause)
                    break          # этот запрос бросаем, идём к следующему
                consecutive_blocks = 0

                if not items:
                    tag = f"{category or 'all'}_{query}_{pno}".replace(" ", "_").replace("/", "_")
                    (DEBUG_DIR / f"{tag}.html").write_text(html, encoding="utf-8")
                    print(f"[miss] разбор не дал лотов, HTML в debug/{tag}.html", file=sys.stderr)
                    break

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

                time.sleep(args.delay * random.uniform(0.6, 1.8))
        if browser is not None:
            try:
                page.close()          # браузер ВАШ, закрывать его мы не вправе
            except Exception:
                pass
        else:
            ctx.close()

    if _STOP["requested"]:
        print(f"\nОстановлено досрочно. Сохранено {written} новых лотов -> {out_path}")
        print("Повторный запуск продолжит с этого места.")
    else:
        print(f"\nГотово: {written} новых лотов -> {out_path}")
    # Код 0 даже при досрочной остановке: частичный сбор — это результат,
    # и пайплайн должен идти к ранжированию, а не падать.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
