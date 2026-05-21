#!/bin/bash
# ============================================================================
# 每日定时任务 — 收盘后(15:30+)自动执行全流程
# 用法:
#   ./daily_run.sh             立即执行
#   ./daily_run.sh --schedule  启动定时调度器(后台常驻)
#
# crontab 方式:
#   30 15 * * 1-5  cd /Users/taopeng/PycharmProjects/stock_fund_flow && ./daily_run.sh >> logs/cron_$(date +\%Y\%m\%d).log 2>&1
# ============================================================================
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

PYTHON="${PROJECT_DIR}/.venv/bin/python3"
[ -f "$PYTHON" ] || PYTHON="python3"

LOGDIR="${PROJECT_DIR}/logs"
mkdir -p "$LOGDIR"

TIMESTAMP=$(TZ='Asia/Shanghai' date '+%Y-%m-%d %H:%M:%S')
DATE_STR=$(TZ='Asia/Shanghai' date '+%Y%m%d')
LOGFILE="${LOGDIR}/daily_${DATE_STR}.log"

log() {
    echo "[$(TZ='Asia/Shanghai' date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOGFILE"
}

# ============================================================================
# 调度模式 — 周一到周五 15:30 触发
# ============================================================================
if [ "$1" = "--schedule" ]; then
    log "启动定时调度器 周一~周五 15:30 执行"
    while true; do
        HOUR=$(TZ='Asia/Shanghai' date '+%H')
        MIN=$(TZ='Asia/Shanghai' date '+%M')
        DOW=$(TZ='Asia/Shanghai' date '+%u')  # 1=Mon 5=Fri

        if [ "$DOW" -le 5 ] && [ "$HOUR" = "15" ] && [ "$MIN" = "32" ]; then
            log "=== 触发每日任务 ==="
            bash "$0" --run
            sleep 120  # 避免同一分钟重复执行
        fi
        sleep 30
    done
    exit 0
fi

# ============================================================================
# 执行模式
# ============================================================================
log "=========================================="
log "每日数据采集 + 选股任务开始"
log "=========================================="

# ── Step 1: 数据采集 ──
log ""
log "[1/4] 全量数据采集..."
$PYTHON fetch_data.py --date="$DATE_STR" 2>&1 | tee -a "$LOGFILE"
RC1=${PIPESTATUS[0]}
if [ $RC1 -ne 0 ]; then
    log "数据采集失败 (exit=$RC1)"
    exit $RC1
fi
log "数据采集完成 ✓"

# ── Step 2: 盘面诊断 ──
log ""
log "[2/4] 盘面诊断..."
$PYTHON market_diagnosis.py --date="$DATE_STR" 2>&1 | tee -a "$LOGFILE"
log "盘面诊断完成 ✓"

# ── Step 3: 选股分析 ──
log ""
log "[3/4] 多因子选股分析..."
$PYTHON stock_picker.py --top=5 --date="$DATE_STR" 2>&1 | tee -a "$LOGFILE"
RC2=${PIPESTATUS[0]}
if [ $RC2 -ne 0 ]; then
    log "选股分析失败 (exit=$RC2)"
fi
log "选股分析完成 ✓"

# ── Step 4: 绩效更新 ──
log ""
log "[4/4] 绩效追踪更新..."
$PYTHON performance.py --update --date="$DATE_STR" 2>&1 | tee -a "$LOGFILE"
$PYTHON performance.py --report 2>&1 | tee -a "$LOGFILE"
log "绩效更新完成 ✓"

log ""
log "=========================================="
log "每日任务完成 [$DATE_STR]"
log "=========================================="

# 清理 30 天前的日志
find "$LOGDIR" -name "daily_*.log" -mtime +30 -delete 2>/dev/null || true
