#!/bin/bash
# 收盘评分 — 对当天全市场股票+板块打分
# 用法: ./daily_pipeline/score.sh [date] [snapshot]
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true
DATE="${1:-$(date +%Y%m%d)}"
SNAPSHOT="${2:-}"
SNAP_ARG=""
[ -n "$SNAPSHOT" ] && SNAP_ARG="--snapshot=$SNAPSHOT"

echo "=== 个股评分 ==="
python -m daily_pipeline.main --mode=score --date="$DATE" $SNAP_ARG

echo ""
echo "=== 板块评分 ==="
python -m daily_pipeline.main --mode=sectors --date="$DATE" $SNAP_ARG
