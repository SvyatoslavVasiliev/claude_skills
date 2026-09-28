#!/usr/bin/env bash
# Полный конвейер одной командой. Рассчитан на ЛОКАЛЬНЫЙ запуск.
#
#   ./run.sh              обычный прогон
#   ./run.sh --headful    первый раз: пройти капчу руками
#
# После первого прохождения капчи cookie лежит в .browser-profile/
# и дальше прогоны идут без участия человека — это то, что делает
# автоматизацию возможной.
set -euo pipefail
cd "$(dirname "$0")"

TOP="${TOP:-100}"
STAMP="$(date -u +%Y%m%d-%H%M)"

echo "[1/4] сбор объявлений"
python3 scrape.py --out items.jsonl "$@"

echo "[2/4] ранжирование по незнанию продавца"
python3 score.py items.jsonl --top "$TOP" > "shortlist-$STAMP.csv"
ln -sf "shortlist-$STAMP.csv" shortlist.csv

echo "[3/4] фото только для шортлиста"
python3 fetch_photos.py items.jsonl shortlist.csv --out photos

echo "[4/4] готово"
wc -l < items.jsonl | xargs echo "  лотов собрано:"
tail -n +2 "shortlist-$STAMP.csv" | wc -l | xargs echo "  в шортлисте:"
ls photos 2>/dev/null | wc -l | xargs echo "  фото скачано:"
echo
echo "Теперь скажи Claude: «разбери shortlist.csv и photos/ по vision_prompt.md и markers.yaml»"
