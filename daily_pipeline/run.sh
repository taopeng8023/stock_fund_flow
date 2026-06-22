#!/bin/bash
# 每日自动运行 — 盘中每30分钟采集 + 收盘自动评分
# 用法: ./daily_pipeline/run.sh [date] [snapshot]
#   ./daily_pipeline/run.sh                    → 当天, 14:30买入模式
#   ./daily_pipeline/run.sh 20260622           → 指定日期
#   ./daily_pipeline/run.sh 20260622 1500      → 收盘模式(15:00截止)
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true

DATE="${1:-$(date +%Y%m%d)}"
SNAPSHOT="${2:-1430}"  # 默认14:30买入模式

echo "============================================"
echo "  Daily Pipeline — $DATE"
echo "  采集时间: 09:30 ~ ${SNAPSHOT:0:2}:${SNAPSHOT:2:2}"
echo "  采集间隔: 30分钟"
echo "============================================"

# 采集时间点 (北京时间)
TIMES=(
    "0930" "1000" "1030" "1100" "1130"
    "1300" "1330" "1400" "1430"
)

COLLECTED=0
for TS in "${TIMES[@]}"; do
    # 如果已过截止时间，停止采集
    if [ "$TS" \> "$SNAPSHOT" ] || [ "$TS" = "$SNAPSHOT" ]; then
        # 到达截止时间: 跑最后一次采集
        if [ "$TS" \> "$SNAPSHOT" ]; then
            break
        fi
    fi

    NOW=$(date +%H%M 2>/dev/null || echo "0000")

    # 等到达目标时间 (仅在当天运行时等待)
    if [ "$DATE" = "$(date +%Y%m%d)" ]; then
        while [ "$(date +%H%M)" \< "$TS" ]; do
            sleep 10
        done
    fi

    echo ""
    echo "[$(date +%H:%M:%S)] 采集 $TS ..."
    python -m daily_pipeline.main --mode=intraday --date="$DATE"
    COLLECTED=$((COLLECTED + 1))

    if [ "$TS" = "$SNAPSHOT" ]; then
        break
    fi
done

echo ""
echo "============================================"
echo "  采集完成 ($COLLECTED 次快照)"
echo "  开始评分 (截止=$SNAPSHOT)..."
echo "============================================"

python -m daily_pipeline.main --mode=score --date="$DATE" --snapshot="$SNAPSHOT"

echo ""
echo "✓ 评分完成: research_data/$DATE/scores.csv"
