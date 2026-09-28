#!/usr/bin/env python3
"""
Ранжирование лотов ПО НЕЗНАНИЮ ПРОДАВЦА, а не по заявленной ценности.

Логика перевёрнута относительно наивной. Наивный фильтр ищет «редкая
модель за копейки» — таких объявлений практически нет: рынок русского
шахматного антиквариата мал и связан, дилеры вычищают явное быстро.
Остаточная неэффективность сидит в НЕОПОЗНАННЫХ лотах: человек пишет
«старые шахматы, дерево, СССР, 3000 ₽» и не знает, что у него довоенный
Берёзовский.

Отсюда целевая функция:
    score = ignorance x substrate - dealer_penalty - repro_penalty

    ignorance       насколько мало продавец сказал (чем меньше, тем лучше нам)
    substrate       есть ли вообще шанс, что под этим лежит старый предмет
    dealer_penalty  признаки осведомлённого продавца или магазина
    repro_penalty   текстовые признаки новодела

ВЕСА НЕ ОТКАЛИБРОВАНЫ. Это априорные догадки. Перед тем как им доверять,
прогоните `--calibrate` и разметьте руками первые 50 лотов.

    python score.py items.jsonl --top 100 > shortlist.csv
    python score.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent

# --- словари ----------------------------------------------------------------

# Продавец знает, что продаёт -> цена уже рыночная, нам не интересно.
DEALER_WORDS = [
    "антиквар", "коллекцион", "раритет", "редкие", "редкий", "ценн",
    "инвестиц", "клеймо", "экспертиз", "оценк", "аукцион", "провенанс",
    "мордов", "гулаг", "берёзовск", "березовск", "данько", "агитацион",
    "jaques", "жак", "staunton", "стаунтон", "selenus", "лфз", "гфз", "ифз",
    "каслинс", "холмогор", "тобольск", "артель", "древпром",
]

# Текстовые признаки новодела.
REPRO_WORDS = [
    "реплика", "копия", "новодел", "под старину", "состаренн", "в стиле",
    "ручная работа", "handmade", "сувенир", "подарочн", "новые", "новый",
    "запечатан", "в упаковке",
]

# Намёк, что предмет вообще может быть старым.
ERA_WORDS = [
    "ссср", "советск", "старые", "старинн", "винтаж", "довоенн", "послевоенн",
    "трофейн", "дореволюц", "царск", "40-х", "50-х", "60-х", "30-х",
    "195", "194", "193", "192", "19 век", "xix",
]

# Материал, на котором вообще бывает ценность.
MATERIAL_WORDS = [
    "кость", "костян", "слонов", "фарфор", "бронз", "чугун", "самшит",
    "палисандр", "эбен", "карельск", "янтар", "серебр", "латун", "бук", "дуб",
]

# Язык расчистки наследства — самый сильный положительный сигнал.
ESTATE_WORDS = [
    "дедушк", "бабушк", "от деда", "с дачи", "из гаража", "нашли",
    "досталось", "по наследству", "разбира", "переезд", "освобожда",
    "на чердаке", "лежали", "не знаю", "не разбираюсь", "как есть",
]

# Родная коробка/этикетка — датирующее свидетельство, сильно поднимает шанс.
BOX_WORDS = ["родная коробка", "в коробке", "с коробкой", "этикетк", "наклейк", "паспорт"]


# --- жёсткие датирующие токены -----------------------------------------------
# Токен в тексте, который ставит НИЖНЮЮ границу даты изготовления. Если эта
# граница позже COLLECTOR_CUTOFF, лот выпадает независимо от всех остальных
# сигналов: коллекционный интерес сосредоточен в допороговом периоде.
#
# Найдено на первом же реальном объявлении: «шахматы СССР со знаком качества»
# набирало 0.38 и проходило порог, хотя знак качества введён 20.04.1967
# (ГОСТ 1.9-67) и им маркировали СЕРИЙНУЮ продукцию гражданского назначения.
# То есть токен, который продавец подаёт как признак ценности, — прямое
# свидетельство массовости и поздней даты. Фильтр этого не видел вообще.

COLLECTOR_CUTOFF = 1950      # ниже этой даты сидит основной интерес коллекционеров

HARD_DATING = [
    # (токены, год-пол, обоснование, confidence)
    (["знак качества", "знаком качества", "знака качества", "знак кач-ва"],
     1967, "Гос. знак качества СССР введён 20.04.1967 (ГОСТ 1.9-67); ставился на серийную продукцию", "grounded"),
    (["олимпиада-80", "олимпиада 80", "олимпиада'80", "москва-80", "москва 80", "олимпийск"],
     1980, "Олимпийская символика Москвы-80", "grounded"),
    (["штрих-код", "штрихкод", "штриховой код"],
     1990, "Штриховое кодирование на потребтоваре в РФ — не ранее 1990-х", "memory"),
    (["made in russia", "сделано в россии", "рф,", "россия, "],
     1992, "Маркировка РФ, а не СССР", "memory"),
]


def hard_date_floor(text: str) -> tuple[int | None, str, str]:
    """Вернуть (год-пол, обоснование, confidence) по самому позднему сработавшему токену."""
    best = (None, "", "")
    for tokens, year, why, conf in HARD_DATING:
        if any(t in text for t in tokens):
            if best[0] is None or year > best[0]:
                best = (year, why, conf)
    return best


def _hits(text: str, words: list[str]) -> list[str]:
    return [w for w in words if w in text]


def score_item(it: dict) -> dict:
    title = (it.get("title") or "").lower()
    desc = (it.get("description") or "").lower()
    text = f"{title} {desc}"

    dealer = _hits(text, DEALER_WORDS)
    repro = _hits(text, REPRO_WORDS)
    era = _hits(text, ERA_WORDS)
    material = _hits(text, MATERIAL_WORDS)
    estate = _hits(text, ESTATE_WORDS)
    box = _hits(text, BOX_WORDS)

    # --- ignorance: чем беднее описание, тем выше ---------------------------
    # Короткое описание и голый заголовок = продавец не разбирается.
    desc_len = len(desc)
    len_score = math.exp(-desc_len / 250.0)          # 0 симв -> 1.0; 250 -> 0.37; 800 -> 0.04
    generic_title = 1.0 if len(title.split()) <= 3 else 0.4
    ignorance = 0.6 * len_score + 0.4 * generic_title
    if estate:
        ignorance = min(1.0, ignorance + 0.25)

    # --- substrate: шанс, что предмет вообще старый -------------------------
    substrate = 0.15                                  # базовый шанс для случайных шахмат
    if era:
        substrate += 0.35
    if material:
        substrate += 0.25
    if box:
        substrate += 0.15
    substrate = min(1.0, substrate)

    # --- штрафы -------------------------------------------------------------
    dealer_penalty = min(0.9, 0.3 * len(dealer)) + (0.4 if it.get("is_shop") else 0.0)
    repro_penalty = min(0.8, 0.35 * len(repro))

    # Слишком мало фото — нечего анализировать; слишком много и студийных —
    # обычно магазин. Оптимум 2-6.
    n = it.get("photo_count") or 0
    photo_factor = 0.3 if n == 0 else (0.7 if n == 1 else (1.0 if n <= 6 else 0.8))

    raw = ignorance * substrate * photo_factor - dealer_penalty - repro_penalty
    score = max(0.0, raw)

    # Жёсткое датирование перебивает всё остальное.
    floor_year, floor_why, floor_conf = hard_date_floor(text)
    excluded_by = ""
    if floor_year is not None and floor_year >= COLLECTOR_CUTOFF:
        score = 0.0
        excluded_by = f"не ранее {floor_year}: {floor_why}"

    return {
        **it,
        "score": round(score, 4),
        "date_floor": floor_year or "",
        "excluded_by": excluded_by or "-",
        "date_floor_confidence": floor_conf or "-",
        "ignorance": round(ignorance, 3),
        "substrate": round(substrate, 3),
        "dealer_penalty": round(dealer_penalty, 3),
        "repro_penalty": round(repro_penalty, 3),
        "signals": ";".join(estate + era + material + box) or "-",
        "dealer_hits": ";".join(dealer) or "-",
        "repro_hits": ";".join(repro) or "-",
    }


# --- самопроверка -----------------------------------------------------------

SELFTEST = [
    # (описание случая, лот, ожидание)
    ("наивный клад: голый заголовок + намёк на эпоху + наследство",
     {"item_id": 1, "title": "Шахматы старые", "photo_count": 3,
      "description": "Дедушкины, лежали на чердаке, не разбираюсь. Дерево."}, "high"),
    ("дилер: всё названо своими именами",
     {"item_id": 2, "title": "Антикварные коллекционные шахматы Мордовия 1930-е",
      "photo_count": 8, "is_shop": True,
      "description": "Редкий довоенный набор, клеймо, экспертиза, " + "подробно " * 80}, "low"),
    ("новодел",
     {"item_id": 3, "title": "Шахматы под старину реплика", "photo_count": 5,
      "description": "Ручная работа, состаренные, сувенир."}, "low"),
    ("пустышка без зацепок",
     {"item_id": 4, "title": "Шахматы пластик детские", "photo_count": 2,
      "description": "Новые в упаковке"}, "low"),
    ("РЕГРЕССИЯ (реальный лот 8480938693): знак качества => не ранее 1967",
     {"item_id": 8480938693, "title": "Шахматы СССР со знаком качества", "photo_count": 3,
      "description": ""}, "low"),
    ("олимпийская символика => 1980",
     {"item_id": 6, "title": "Шахматы старые", "photo_count": 3,
      "description": "Дедушкины, с дачи, Олимпиада-80, не разбираюсь"}, "low"),
    ("кость + родная коробка, описание короткое",
     {"item_id": 5, "title": "Шахматы костяные", "photo_count": 4,
      "description": "В родной коробке, СССР. Как есть."}, "high"),
]


def selftest() -> int:
    rows = [(d, score_item(it), exp) for d, it, exp in SELFTEST]
    rows_sorted = sorted(rows, key=lambda r: -r[1]["score"])
    print(f"{'score':>7}  {'ожид':<5} {'итог':<5} описание")
    print("-" * 78)
    ok = True
    for desc, r, exp in rows_sorted:
        got = "high" if r["score"] >= 0.15 else "low"
        mark = "OK " if got == exp else "FAIL"
        if got != exp:
            ok = False
        print(f"{r['score']:>7.4f}  {exp:<5} {mark:<5} {desc}")
    print("-" * 78)
    print("порог отсечки 0.15 |", "все кейсы прошли" if ok else "ЕСТЬ РАСХОЖДЕНИЯ")
    return 0 if ok else 1


# --- основной путь ----------------------------------------------------------

FIELDS = ["verdict", "score", "band", "price", "title", "signals",
          "excluded_by", "dealer_hits", "repro_hits", "ignorance", "substrate",
          "photo_count", "item_id", "url"]


def band_of(rank: int, total: int) -> str:
    """В какой трети рейтинга сидит лот."""
    if total <= 1:
        return "верх"
    q = rank / (total - 1)
    return "верх" if q < 0.15 else ("середина" if q < 0.6 else "низ")


def stratified(scored: list[dict], n: int = 60) -> list[dict]:
    """Выборка для калибровки: сверху, из середины и снизу.

    Размечать только топ бессмысленно: так видно ложноположительные, но не
    ложноотрицательные — а пропущенная жемчужина стоит дороже лишнего
    кандидата. Поэтому берём из всех трёх полос.
    """
    import random
    random.seed(0)
    ordered = sorted(scored, key=lambda r: -r["score"])
    total = len(ordered)
    for i, r in enumerate(ordered):
        r["band"] = band_of(i, total)
    buckets: dict[str, list[dict]] = {"верх": [], "середина": [], "низ": []}
    for r in ordered:
        buckets[r["band"]].append(r)
    # 40% сверху, 30% середина, 30% снизу
    plan = [("верх", int(n * 0.4)), ("середина", int(n * 0.3)), ("низ", n - int(n * 0.4) - int(n * 0.3))]
    out: list[dict] = []
    for band, k in plan:
        pool = buckets[band]
        out.extend(random.sample(pool, min(k, len(pool))))
    out.sort(key=lambda r: -r["score"])
    return out


def write_rows(rows: list[dict], dest: str | None) -> None:
    """CSV с BOM: иначе Excel ломает кириллицу. Numbers и LibreOffice тоже ок."""
    if dest:
        fh = open(dest, "w", encoding="utf-8-sig", newline="")
    else:
        fh = sys.stdout
    try:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r.setdefault("verdict", "")
            r.setdefault("band", "")
            w.writerow(r)
    finally:
        if dest:
            fh.close()


def evaluate(scored: list[dict], labeled_path: str) -> int:
    """Сверить текущие баллы с вашей разметкой и подобрать порог.

    Честная граница: 50-60 меток позволяют настроить ПОРОГ, но не
    переобучить шесть весов — данных слишком мало, получится подгонка.
    """
    marks: dict[str, str] = {}
    with open(labeled_path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            v = (row.get("verdict") or "").strip().lower()
            if v and row.get("item_id"):
                marks[row["item_id"]] = v
    if not marks:
        print("в файле нет заполненной колонки verdict", file=sys.stderr)
        return 1

    by_id = {str(r["item_id"]): r for r in scored}
    pairs = [(by_id[i]["score"], v) for i, v in marks.items() if i in by_id]
    if not pairs:
        print("ни один размеченный item_id не найден в items.jsonl", file=sys.stderr)
        return 1

    good = {"gem", "maybe"}
    print(f"размечено {len(pairs)} лотов: "
          f"gem {sum(1 for _, v in pairs if v == 'gem')}, "
          f"maybe {sum(1 for _, v in pairs if v == 'maybe')}, "
          f"no {sum(1 for _, v in pairs if v == 'no')}\n")

    print(f"{'порог':>7} {'найдено':>8} {'точность':>9} {'полнота':>8} {'F1':>6}")
    print("-" * 44)
    total_good = sum(1 for _, v in pairs if v in good)
    best = (0.0, -1.0)
    for th in [i / 20 for i in range(0, 21)]:
        tp = sum(1 for sc, v in pairs if sc >= th and v in good)
        fp = sum(1 for sc, v in pairs if sc >= th and v not in good)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / total_good if total_good else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        if f1 > best[1]:
            best = (th, f1)
        print(f"{th:>7.2f} {tp + fp:>8} {prec:>9.2f} {rec:>8.2f} {f1:>6.2f}")
    print("-" * 44)
    print(f"лучший порог по F1: {best[0]:.2f} (сейчас 0.15)")

    missed = [(sc, v) for sc, v in pairs if v == "gem" and sc < 0.15]
    if missed:
        print(f"\nВАЖНО: {len(missed)} лотов с вердиктом gem фильтр отсеял "
              f"(баллы {', '.join(f'{sc:.2f}' for sc, _ in missed)}).")
        print("Это ложноотрицательные — они дороже лишних кандидатов.")
        print("Пришлите файл Claude: по ним видно, каких признаков не хватает словарям.")
    else:
        print("\nЛожноотрицательных нет: ни один gem не отсеян.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("infile", nargs="?", help="items.jsonl от scrape.py")
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--min-score", type=float, default=0.15)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out", metavar="FILE", help="записать в файл, а не в stdout")
    ap.add_argument("--calibrate", action="store_true",
                    help="расслоённая выборка для ручной разметки: верх, середина и низ")
    ap.add_argument("--calibrate-n", type=int, default=60, help="размер калибровочной выборки")
    ap.add_argument("--eval", metavar="FILE",
                    help="сверить баллы с вашей разметкой и подобрать порог")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.infile:
        ap.error("нужен items.jsonl (или --selftest)")

    items = []
    with open(args.infile, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                items.append(json.loads(line))

    scored = [score_item(it) for it in items]

    if args.eval:
        return evaluate(scored, args.eval)

    if args.calibrate:
        rows = stratified(scored, args.calibrate_n)
        dest = args.out or "calibrate.csv"
        write_rows(rows, dest)
        print(f"\n{len(rows)} лотов -> {dest}", file=sys.stderr)
        print("Проставьте в колонке verdict: gem / maybe / no.", file=sys.stderr)
        print("Выборка расслоённая (верх, середина, низ) — так видны не только", file=sys.stderr)
        print("лишние кандидаты, но и пропущенные жемчужины.", file=sys.stderr)
        print(f"Потом: python3 score.py items.jsonl --eval {dest}", file=sys.stderr)
        return 0

    ordered = sorted(scored, key=lambda r: -r["score"])
    for i, r in enumerate(ordered):
        r["band"] = band_of(i, len(ordered))
    rows = [r for r in ordered if r["score"] >= args.min_score][: args.top]
    write_rows(rows, args.out)
    if args.out:
        print(f"топ-{len(rows)} -> {args.out}", file=sys.stderr)

    print(f"всего лотов {len(items)}, прошли порог {sum(1 for r in scored if r['score'] >= args.min_score)}, "
          f"выведено {len(rows)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
