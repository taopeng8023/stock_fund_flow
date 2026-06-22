#!/bin/bash
# 盘中采集 — 每30分钟运行一次
# 用法: ./daily_pipeline/intraday.sh
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true
python -m daily_pipeline.main --mode=intraday
