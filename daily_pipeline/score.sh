#!/bin/bash
# 收盘评分 — 对当天全市场股票打分
# 用法: ./daily_pipeline/score.sh [date]
#   ./daily_pipeline/score.sh              → 当天
#   ./daily_pipeline/score.sh 20260622     → 指定日期
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true
DATE="${1:-$(date +%Y%m%d)}"
python -m daily_pipeline.main --mode=score --date="$DATE"
