#!/usr/bin/env python
"""
全市场历史 K 线全量拉取（单进程，增量写入，断点续跑）

用法:
    python fetch_all_history.py                  # 全量（含分钟线）
    python fetch_all_history.py --no-minute      # 仅日/周/月线 + 指数
    python fetch_all_history.py 20260630         # 指定日期
"""
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from baostock_data.fetcher import BaoStockFetcher

if __name__ == "__main__":
    date_str = None
    with_minute = True
    for arg in sys.argv[1:]:
        if arg == "--no-minute":
            with_minute = False
        elif not arg.startswith("--"):
            date_str = arg

    print("═" * 60)
    print("  BaoStock 全市场历史 K 线")
    print("  日/周/月线: 1990-12-19 至今")
    if with_minute:
        print("  分钟线: 2019-01-02 至今 (5/15/30/60)")
    else:
        print("  分钟线: 跳过（--no-minute）")
    print("═" * 60)

    with BaoStockFetcher() as f:
        f.fetch_all(date_str, include_minute=with_minute)
