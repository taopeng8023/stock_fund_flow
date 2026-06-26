#!/bin/bash
# 批量评分 — 对 research_data 下所有日期的盘中数据进行评分
# 用法:
#   ./daily_pipeline/score_batch.sh              → 对所有日期评分（跳过已有scores.csv的）
#   ./daily_pipeline/score_batch.sh --force      → 强制重新评分（覆盖已有）
#   ./daily_pipeline/score_batch.sh --date 20260626  → 只评指定日期
#   ./daily_pipeline/score_batch.sh --dry-run    → 只列出会评分的日期，不执行
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RESEARCH_DIR="$PROJECT_DIR/research_data"

cd "$PROJECT_DIR"
source .venv/bin/activate 2>/dev/null || true

# ── 参数解析 ──────────────────────────────────────
FORCE=false
DRY_RUN=false
TARGET_DATE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)   FORCE=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        --date)    TARGET_DATE="$2"; shift 2 ;;
        -h|--help)
            echo "用法: $0 [选项]"
            echo ""
            echo "选项:"
            echo "  --force       强制重新评分，覆盖已有 scores.csv"
            echo "  --date DATE   只评分指定日期"
            echo "  --dry-run     只列出日期和状态，不执行"
            echo "  -h, --help    显示帮助"
            echo ""
            echo "示例:"
            echo "  $0                    # 增量评分（只评没有 scores.csv 的）"
            echo "  $0 --force            # 全量重评所有日期"
            echo "  $0 --date 20260626    # 只评今天"
            echo "  $0 --dry-run          # 预览"
            exit 0
            ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

# ── 收集日期 ──────────────────────────────────────
if [ -n "$TARGET_DATE" ]; then
    if [ ! -d "$RESEARCH_DIR/$TARGET_DATE/intraday" ]; then
        echo "❌ $TARGET_DATE 无 intraday 数据"
        exit 1
    fi
    DATES=("$TARGET_DATE")
else
    DATES=()
    for d in "$RESEARCH_DIR"/20*/; do
        dir_name=$(basename "$d")
        if [ -d "$d/intraday" ] && ls "$d/intraday"/fund_flow_*.csv &>/dev/null; then
            DATES+=("$dir_name")
        fi
    done
    # 排序
    IFS=$'\n' DATES=($(sort <<<"${DATES[*]}")); unset IFS
fi

# ── 分类：需评分 vs 跳过 ──
TO_SCORE=()
SKIPPED=()

for d in "${DATES[@]}"; do
    if [ "$FORCE" = true ]; then
        TO_SCORE+=("$d")
    elif [ -f "$RESEARCH_DIR/$d/scores.csv" ]; then
        SKIPPED+=("$d")
    else
        TO_SCORE+=("$d")
    fi
done

# ── 概览 ──
echo "============================================"
echo "  批量评分 — research_data"
echo "  共 ${#DATES[@]} 个日期 | 需评分: ${#TO_SCORE[@]} | 跳过: ${#SKIPPED[@]}"
echo "============================================"

if [ ${#SKIPPED[@]} -gt 0 ]; then
    echo ""
    echo "  已有 scores.csv (跳过):"
    for d in "${SKIPPED[@]}"; do
        stocks=$(wc -l < "$RESEARCH_DIR/$d/scores.csv" 2>/dev/null | tr -d ' ')
        echo "    $d  ($stocks 只)"
    done
fi

if [ ${#TO_SCORE[@]} -gt 0 ]; then
    echo ""
    echo "  待评分:"
    for d in "${TO_SCORE[@]}"; do
        snap_count=$(ls "$RESEARCH_DIR/$d/intraday"/fund_flow_*.csv 2>/dev/null | wc -l | tr -d ' ')
        echo "    $d  ($snap_count 快照)"
    done
else
    echo ""
    echo "  ✅ 所有日期已有评分数据，无需操作"
fi

if [ "$DRY_RUN" = true ]; then
    echo ""
    echo "  (dry-run 模式，不执行评分)"
    exit 0
fi

if [ ${#TO_SCORE[@]} -eq 0 ]; then
    exit 0
fi

# ── 逐日评分 ──────────────────────────────────────
echo ""
echo "────────────────────────────────────────────"

SUCCESS=0
FAILED=0
FAILED_DATES=()

for d in "${TO_SCORE[@]}"; do
    echo ""
    echo "=== [$d] 个股评分 ==="
    START_TS=$(date +%s)

    if python -m daily_pipeline.main --mode=score --date="$d"; then
        echo ""
        echo "=== [$d] 板块评分 ==="
        if python -m daily_pipeline.main --mode=sectors --date="$d"; then
            END_TS=$(date +%s)
            ELAPSED=$((END_TS - START_TS))
            echo ""
            echo "  ✅ $d 完成 (${ELAPSED}s)"

            # 显示结果统计
            if [ -f "$RESEARCH_DIR/$d/scores.csv" ]; then
                COUNT=$(($(wc -l < "$RESEARCH_DIR/$d/scores.csv") - 1))
                AVG=$(python3 -c "
import csv
with open('$RESEARCH_DIR/$d/scores.csv', encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f))
scores = [float(r['综合得分']) for r in rows if r.get('综合得分')]
print(f'{sum(scores)/len(scores):.3f}') if scores else print('N/A')
" 2>/dev/null || echo "?")
                echo "      股票: ${COUNT}只  均分: ${AVG}"
            fi
            if [ -f "$RESEARCH_DIR/$d/sector_scores.csv" ]; then
                SECT_COUNT=$(($(wc -l < "$RESEARCH_DIR/$d/sector_scores.csv") - 1))
                echo "      板块: ${SECT_COUNT}个"
            fi
            SUCCESS=$((SUCCESS + 1))
        else
            echo "  ❌ $d 板块评分失败"
            FAILED=$((FAILED + 1))
            FAILED_DATES+=("$d")
        fi
    else
        echo "  ❌ $d 个股评分失败"
        FAILED=$((FAILED + 1))
        FAILED_DATES+=("$d")
    fi
done

# ── 汇总 ──────────────────────────────────────────
echo ""
echo "============================================"
echo "  批量评分完成"
echo "  成功: $SUCCESS | 失败: $FAILED"
if [ ${#FAILED_DATES[@]} -gt 0 ]; then
    echo "  失败日期: ${FAILED_DATES[*]}"
fi
echo "============================================"
echo ""
echo "  输出目录: $RESEARCH_DIR/<date>/"
echo "  生成文件: scores.csv, sector_scores.csv"
echo ""
echo "  下一步:"
echo "    python -m portfolio.buy_engine --date=<date>  # 买入推荐"
echo "    python daily_pipeline/verify_analysis.py       # 盘中分析"
