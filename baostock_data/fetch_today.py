#!/usr/bin/env python3
"""
当日增量数据拉取（日常更新用，几分钟完成）

仅拉取最近几个交易日的数据，适合每日收盘后运行。
首次运行请先用 fetch_all_history.py 拉全量历史。

用法:
    python fetch_today.py              # 拉取近5日
    python fetch_today.py 3            # 近3日
"""
import sys
import os

# auto-detect venv
_script = os.path.abspath(__file__)
_project = os.path.dirname(os.path.dirname(_script))
_venv_py = os.path.join(_project, ".venv", "bin", "python3")
if not os.path.exists(_venv_py):
    _venv_py = os.path.join(_project, ".venv", "bin", "python")
if os.path.exists(_venv_py) and _venv_py != sys.executable:
    try:
        import baostock  # noqa: F401
    except ImportError:
        os.execv(_venv_py, [_venv_py, _script] + sys.argv[1:])

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from baostock_data.fetcher import BaoStockFetcher

if __name__ == "__main__":
    days_back = 5

    for arg in sys.argv[1:]:
        if arg.isdigit():
            days_back = int(arg)
        # 忽略旧的日期参数（向后兼容）
        elif not arg.startswith("--"):
            pass

    print("═" * 60)
    print(f"  BaoStock 增量更新 — 近 {days_back} 个交易日")
    print("═" * 60)

    with BaoStockFetcher() as f:
        f.fetch_incremental(days_back=days_back)
