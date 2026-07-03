#!/bin/bash
# 主力资金流向定时分析 — 包装脚本
# 用法: bash scripts/run_main_capital_analysis.sh [--date=YYYYMMDD] [--top=N] [--summary|--json]
# 默认: --summary（交易摘要模式）

set -e
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

source .venv/bin/activate

# 无参数时默认 --brief（简洁洞察）
if [ $# -eq 0 ]; then
    exec python scripts/analyze_main_capital.py --brief
else
    exec python scripts/analyze_main_capital.py "$@"
fi
