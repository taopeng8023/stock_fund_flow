#!/bin/bash
# 每日自动运行 — 盘中每30分钟采集 + 收盘自动评分
# 用法: ./daily_pipeline/run.sh [date] [snapshot]
#   ./daily_pipeline/run.sh                    → 当天, 14:30买入模式
#   ./daily_pipeline/run.sh 20260622           → 指定日期
#   ./daily_pipeline/run.sh 20260622 1500      → 收盘模式(15:00截止)
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true

DATE="${1:-$(date +%Y%m%d)}"

# 采集时间点 (顺延1分钟避免冲突)  格式: HHMM
TRADING_TIMES=("0931" "1001" "1031" "1101" "1131" "1301" "1331" "1401" "1431")
SCORE_TIME="1431"      # 14:31 评分截止
CLOSE_TIME="1501"      # 15:01 收盘后补采一次

echo "============================================"
echo "  Daily Pipeline — $DATE"
echo "  盘中采集: ${TRADING_TIMES[0]} ~ $SCORE_TIME (每30分钟)"
echo "  评分时间: $SCORE_TIME"
echo "  收盘补采: $CLOSE_TIME"
echo "============================================"

COLLECTED=0
IS_TODAY=0
[ "$DATE" = "$(date +%Y%m%d)" ] && IS_TODAY=1

for TS in "${TRADING_TIMES[@]}"; do
    # 当天模式: 跳过已过去的时间点
    if [ $IS_TODAY -eq 1 ]; then
        NOW=$(date +%H%M)
        if [ "$NOW" \> "$TS" ]; then
            echo "[跳过] $TS (已过当前时间 $NOW)"
            continue
        fi
        # 等到达目标时间
        echo "[等待] $TS (当前 $NOW)..."
        while [ "$(date +%H%M)" \< "$TS" ]; do
            sleep 15
        done
    fi

    echo ""
    echo "[$(date +%H:%M:%S)] 采集 $TS ..."
    python -m daily_pipeline.main --mode=intraday --date="$DATE"
    COLLECTED=$((COLLECTED + 1))
done

# 当天且14:31已过 → 直接评分; 否则等到14:31
if [ $IS_TODAY -eq 1 ]; then
    NOW=$(date +%H%M)
    if [ "$NOW" \< "$SCORE_TIME" ]; then
        echo "[等待评分] 等到 $SCORE_TIME ..."
        while [ "$(date +%H%M)" \< "$SCORE_TIME" ]; do sleep 15; done
    fi
fi

# 14:31 采集完成后自动评分
echo ""
echo "============================================"
echo "  盘中采集完成 ($COLLECTED 次快照)"
echo "  自动评分 (截止=$SCORE_TIME)..."
echo "============================================"
python -m daily_pipeline.main --mode=score --date="$DATE" --snapshot="$SCORE_TIME"

echo ""
echo "=== 板块评分 ==="
python -m daily_pipeline.main --mode=sectors --date="$DATE" --snapshot="$SCORE_TIME"

# 收盘后补采一次 (为后续分析储备数据)
if [ "$DATE" = "$(date +%Y%m%d)" ]; then
    while [ "$(date +%H%M)" \< "$CLOSE_TIME" ]; do
        sleep 10
    done
fi

echo ""
echo "[$(date +%H:%M:%S)] 收盘补采 $CLOSE_TIME ..."
python -m daily_pipeline.main --mode=intraday --date="$DATE"

echo ""
echo "✓ 全部完成: research_data/$DATE/scores.csv"
