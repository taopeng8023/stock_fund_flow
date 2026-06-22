#!/bin/bash
# 收盘评分 — 对当天全市场股票打分
# 用法: ./daily_pipeline/score.sh [date] [snapshot]
#   ./daily_pipeline/score.sh                  → 当天收盘
#   ./daily_pipeline/score.sh 20260622          → 指定日期收盘
#   ./daily_pipeline/score.sh 20260622 1430     → 14:30快照评分
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true
DATE="${1:-$(date +%Y%m%d)}"
SNAPSHOT="${2:-}"
if [ -n "$SNAPSHOT" ]; then
    python -m daily_pipeline.main --mode=score --date="$DATE" --snapshot="$SNAPSHOT"
else
    python -m daily_pipeline.main --mode=score --date="$DATE"
fi
