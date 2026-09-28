#!/usr/bin/env bash
# Полный конвейер одной командой. Рассчитан на ЛОКАЛЬНЫЙ запуск.
#
#   ./run.sh                       обычный прогон
#   ./run.sh --headful             первый раз: видеть окно браузера
#   ./run.sh --max-minutes 30      ограничить прогон по времени
#   ./run.sh --max-items 500       ограничить по числу лотов
#
# ПРЕРЫВАТЬ МОЖНО В ЛЮБОЙ МОМЕНТ. Ctrl+C останавливает сбор после текущей
# страницы, и конвейер всё равно доходит до шортлиста по тому, что успело
# собраться. Второй Ctrl+C — выход немедленно. Повторный запуск продолжает
# с места остановки: собранное не теряется, дубли отсекаются по item_id.
#
# Намеренно БЕЗ set -e: досрочная остановка и блокировка Avito — это
# нормальные исходы, а не ошибки. Частичный сбор надо отранжировать, а не
# выбросить. Раньше здесь стоял set -e, и блокировка на 4-й странице
# убивала прогон целиком, хотя items.jsonl был уже полон.
set -uo pipefail
cd "$(dirname "$0")"

TOP="${TOP:-100}"
STAMP="$(date -u +%Y%m%d-%H%M)"

# Даже если нас прибьют на середине — отранжировать собранное перед выходом.
finish() {
  echo
  if [[ ! -s items.jsonl ]]; then
    echo "items.jsonl пуст — ранжировать нечего."
    echo "Скорее всего не сработал разбор страницы: посмотрите debug/ и скажите Claude"
    echo "«поправь парсер в scrape.py по дампу из debug/»."
    exit 1
  fi

  echo "[2/3] ранжирование по незнанию продавца"
  python3 score.py items.jsonl --top "$TOP" --out "shortlist-$STAMP.csv" || {
    echo "ранжирование упало, но items.jsonl на месте — данные не потеряны"; exit 1; }
  ln -sf "shortlist-$STAMP.csv" shortlist.csv

  echo "[3/3] фото только для шортлиста"
  python3 fetch_photos.py items.jsonl shortlist.csv --out photos \
    || echo "  часть фото не скачалась — на разбор это не влияет"

  echo
  echo "  лотов собрано всего: $(wc -l < items.jsonl)"
  echo "  в шортлисте:         $(( $(wc -l < "shortlist-$STAMP.csv") - 1 ))"
  echo "  фото скачано:        $(ls photos 2>/dev/null | wc -l)"
  echo
  echo "Дальше скажи Claude:"
  echo "  «разбери shortlist.csv и photos/ по vision_prompt.md и markers.yaml»"
  exit 0
}
trap finish INT TERM

echo "[1/3] сбор объявлений (Ctrl+C — остановиться и сохранить)"
python3 scrape.py --out items.jsonl "$@"
rc=$?
if [[ $rc -ne 0 ]]; then
  echo "сбор вышел с кодом $rc — продолжаю с тем, что собрано"
fi

trap - INT TERM
finish
