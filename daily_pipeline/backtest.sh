#!/bin/bash
# 次日回测 — 上一个交易日评分 vs 当前交易日收益
# 用法: ./daily_pipeline/backtest.sh
#   自动找上一个有 scores.csv 的交易日作为 pick_date
#   当前日期作为 eval_date
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true

TODAY=$(date +%Y%m%d)

# 找上一个有 scores.csv 的交易日
RESEARCH_DIR="research_data"
PREV_DATE=""
for d in $(ls -d "$RESEARCH_DIR"/*/ 2>/dev/null | sed 's|/$||' | xargs -n1 basename | sort -r); do
    if [[ "$d" =~ ^[0-9]{8}$ ]] && [ "$d" -lt "$TODAY" ] && [ -f "$RESEARCH_DIR/$d/scores.csv" ]; then
        PREV_DATE="$d"
        break
    fi
done

if [ -z "$PREV_DATE" ]; then
    echo "错误: 找不到上一个有 scores.csv 的交易日"
    echo "请先运行 ./daily_pipeline/score.sh 完成收盘评分"
    exit 1
fi

echo "回测: $PREV_DATE → $TODAY"
python -m daily_pipeline.main --mode=backtest --date="$PREV_DATE" --eval="$TODAY"
