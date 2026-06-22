#!/bin/bash
# 累积优化报告 — 从最早采集日到今天的回测汇总
# 用法: ./daily_pipeline/optimize.sh
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true

TODAY=$(date +%Y%m%d)

# 找最早有 scores.csv 的日期
RESEARCH_DIR="research_data"
FIRST_DATE="$TODAY"
for d in $(ls -d "$RESEARCH_DIR"/*/ 2>/dev/null | sed 's|/$||' | xargs -n1 basename | sort); do
    if [[ "$d" =~ ^[0-9]{8}$ ]] && [ -f "$RESEARCH_DIR/$d/scores.csv" ]; then
        FIRST_DATE="$d"
        break
    fi
done

echo "累积分析: $FIRST_DATE → $TODAY"
echo ""

python -m daily_pipeline.main --mode=optimize
