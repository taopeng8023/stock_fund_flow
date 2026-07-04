#!/usr/bin/env python3
"""
扫描器回测验证 — 对 scanner 的历史推荐做次日收益验证。

用法:
    python scanner_backtest.py --date 20260624  # 验证 0624 推荐的次日收益
    python scanner_backtest.py --date 20260620 --days 5  # 过去5天
"""
import argparse
import os
import statistics
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DAILY_DIR = os.path.join(PROJECT_ROOT, "baostock_data", "data", "daily")

# Import scanner
sys.path.insert(0, SCRIPT_DIR)
from signal_scanner import scan_stocks, MIN_DAYS

try:
    from stock_filter import load_stock_files
except ImportError:
    from baostock_data.analysis.stock_filter import load_stock_files

try:
    from result_store import save_results
    HAS_RESULT_STORE = True
except ImportError:
    HAS_RESULT_STORE = False


def get_next_day_close(code, scan_date_str, daily_dir):
    """获取 scan_date 之后最近交易日的收盘价。"""
    fp = os.path.join(daily_dir, f"{code}.csv")
    if not os.path.exists(fp):
        return None

    try:
        df = pd.read_csv(fp)
        df["日期"] = pd.to_datetime(df["日期"], format="%Y-%m-%d")
        df = df.sort_values("日期").reset_index(drop=True)
    except Exception:
        return None

    scan_d = datetime.strptime(scan_date_str, "%Y%m%d")
    for i in range(len(df)):
        if df["日期"].iloc[i] >= scan_d + timedelta(days=1):
            return float(df["收盘"].iloc[i])
    return None


def run_backtest(scan_date: str, top_n: int = 30,
                 min_consensus: int = 2, lookback: int = 5):
    """回测：扫描 → 跟踪次日收益。"""
    print(f"══ 扫描器回测验证 ══")
    print(f"扫描日: {scan_date} → 跟踪次日收盘收益")

    # 1. 扫描
    candidates = scan_stocks(
        DAILY_DIR, scan_date,
        min_consensus=min_consensus, top_n=top_n, lookback=lookback
    )
    if not candidates:
        print("  无候选，跳过回测")
        return

    # 2. 获取次日收盘价
    print(f"\n  跟踪 {len(candidates)} 只候选的次日收盘...")
    results = []
    for c in candidates:
        next_close = get_next_day_close(c["code"], scan_date, DAILY_DIR)
        if next_close is None or c["price"] <= 0:
            continue
        ret = (next_close - c["price"]) / c["price"] * 100
        results.append({
            **c,
            "next_close": round(next_close, 2),
            "next_return": round(ret, 2),
            "win": ret > 0,
        })

    if not results:
        print("  无价格数据，回测失败")
        return

    # 3. 统计
    n = len(results)
    wins = sum(1 for r in results if r["win"])
    win_rate = wins / n * 100
    avg_ret = statistics.mean(r["next_return"] for r in results)
    median_ret = statistics.median(r["next_return"] for r in results)

    print(f"\n  ══ 回测结果 ══")
    print(f"  候选数: {n} | 胜率: {win_rate:.1f}% | 均收益: {avg_ret:+.2f}% | 中位: {median_ret:+.2f}%")
    print(f"\n  {'#':<3} {'代码':<10} {'名称':<8} {'扫描价':>7} {'次日收盘':>8} {'收益':>8} {'胜负'}")
    print(f"  {'─'*50}")
    for i, r in enumerate(results):
        print(f"  {i+1:<3} {r['code']:<10} {str(r['name']):<8} "
              f"{r['price']:7.2f} {r['next_close']:8.2f} {r['next_return']:+7.2f}% "
              f"{'✅' if r['win'] else '❌'}")

    # Top 5
    top5 = results[:5]
    top5_wr = sum(1 for r in top5 if r["win"]) / len(top5) * 100 if top5 else 0
    top5_ret = statistics.mean(r["next_return"] for r in top5) if top5 else 0
    print(f"\n  Top 5: WR={top5_wr:.1f}% 均收益={top5_ret:+.2f}%")

    # 持久化
    if HAS_RESULT_STORE:
        save_results("scanner_backtest", {
            "scan_date": scan_date,
            "candidate_count": len(results),
            "win_rate": round(win_rate, 1),
            "avg_return": round(avg_ret, 2),
            "median_return": round(median_ret, 2),
            "top5_wr": round(top5_wr, 1),
            "top5_avg_return": round(top5_ret, 2),
            "results": [
                {"code": str(r["code"]), "name": str(r["name"]), "score": float(r["score"]),
                 "price": float(r["price"]), "next_close": float(r["next_close"]),
                 "return": float(r["next_return"]), "win": bool(r["win"])}
                for r in results
            ],
        })

    return results


def main():
    parser = argparse.ArgumentParser(description="扫描器回测验证")
    parser.add_argument("--date", required=True, help="扫描日期 YYYYMMDD")
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--min-consensus", type=int, default=1)
    parser.add_argument("--lookback", type=int, default=5)
    args = parser.parse_args()

    if not os.path.isdir(DAILY_DIR):
        print(f"错误: {DAILY_DIR}"); sys.exit(1)

    run_backtest(args.date, top_n=args.top,
                 min_consensus=args.min_consensus, lookback=args.lookback)


if __name__ == "__main__":
    main()
