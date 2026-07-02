#!/usr/bin/env python3
"""
K线形态涨跌规律分析 — 从每日K线CSV统计形态的次日/后N日涨跌概率

用法:
    python baostock_data/analysis/kline_pattern.py --stocks 100

依赖: pandas (纯Python+pandas, 无其他依赖)
"""
import argparse
import os
import sys
import warnings
from collections import defaultdict
from glob import glob

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

# ---- data loading ---------------------------------------------------------


def load_stock_csv(filepath, min_days=20):
    """Load single stock daily CSV, return sorted DataFrame or None."""
    try:
        df = pd.read_csv(filepath)
        if len(df) < min_days:
            return None
        df["日期"] = pd.to_datetime(df["日期"], format="%Y-%m-%d")
        df = df.sort_values("日期").reset_index(drop=True)
        df = df[df["成交量"] > 0].copy()  # skip suspended days
        return df if len(df) >= min_days else None
    except Exception:
        return None


# ---- pattern detection ----------------------------------------------------


def _rolling_ma(series, window):
    return series.rolling(window=window, min_periods=window).mean()


def detect_patterns(df):
    """
    Scan single stock DataFrame for all patterns.
    Returns list of (pattern_name, is_up) tuples.
    """
    n = len(df)
    c = df["收盘"].values
    o = df["开盘"].values
    h = df["最高"].values
    l = df["最低"].values
    v = df["成交量"].values
    pc = df["前收盘"].values

    results = []

    if n < 22:
        return results

    # precompute helpers
    pos_day = (c > o).astype(int)   # 阳线
    neg_day = (c < o).astype(int)   # 阴线
    vol_ma5 = _rolling_ma(df["成交量"], 5).values
    ma5 = _rolling_ma(df["收盘"], 5).values
    ma20 = _rolling_ma(df["收盘"], 20).values

    # ---- 连阳 (consecutive positive candles) ----
    for k in (2, 3):
        for i in range(k - 1, n - 1):
            if np.all(pos_day[i - k + 1 : i + 1] == 1):
                results.append((f"{k}连阳", c[i + 1] > c[i]))

    # ---- 连阴 (consecutive negative candles) ----
    for k in (2, 3):
        for i in range(k - 1, n - 1):
            if np.all(neg_day[i - k + 1 : i + 1] == 1):
                results.append((f"{k}连阴", c[i + 1] > c[i]))

    # ---- 放量上涨 (volume breakout + positive day) ----
    for i in range(5, n - 1):
        if pd.notna(vol_ma5[i]) and v[i] > vol_ma5[i] * 1.5 and c[i] > o[i]:
            results.append(("放量上涨", c[i + 1] > c[i]))

    # ---- 缩量下跌 (volume shrink + negative day) ----
    for i in range(5, n - 1):
        if pd.notna(vol_ma5[i]) and v[i] < vol_ma5[i] * 0.5 and c[i] < o[i]:
            results.append(("缩量下跌", c[i + 1] > c[i]))

    # ---- 跳空高开 (gap up) ----
    for i in range(1, n):
        if o[i] > pc[i] * 1.02:
            # same day: close > preclose ?
            results.append(("跳空高开_当日", c[i] > pc[i]))
            # next day
            if i < n - 1:
                results.append(("跳空高开_次日", c[i + 1] > c[i]))

    # ---- 跳空低开 (gap down) ----
    for i in range(1, n):
        if o[i] < pc[i] * 0.98:
            results.append(("跳空低开_当日", c[i] > pc[i]))
            if i < n - 1:
                results.append(("跳空低开_次日", c[i + 1] > c[i]))

    # ---- 长下影线 (long lower shadow) ----
    for i in range(n - 1):
        candle_range = h[i] - l[i]
        if candle_range <= 0:
            continue
        body_low = min(o[i], c[i])
        amplitude = candle_range / pc[i] if pc[i] > 0 else 0
        lower_shadow = (body_low - l[i]) / candle_range
        if lower_shadow > 0.6 and amplitude > 0.02:
            results.append(("长下影线", c[i + 1] > c[i]))

    # ---- 长上影线 (long upper shadow) ----
    for i in range(n - 1):
        candle_range = h[i] - l[i]
        if candle_range <= 0:
            continue
        body_high = max(o[i], c[i])
        amplitude = candle_range / pc[i] if pc[i] > 0 else 0
        upper_shadow = (h[i] - body_high) / candle_range
        if upper_shadow > 0.6 and amplitude > 0.02:
            results.append(("长上影线", c[i + 1] > c[i]))

    # ---- MA5/MA20 金叉 (golden cross) ----
    for i in range(21, n):
        if pd.isna(ma5[i]) or pd.isna(ma20[i]) or pd.isna(ma5[i - 1]) or pd.isna(ma20[i - 1]):
            continue
        if ma5[i - 1] <= ma20[i - 1] and ma5[i] > ma20[i]:
            for N in (1, 3, 5):
                if i + N < n:
                    results.append((f"MA金叉_{N}日", c[i + N] > c[i]))

    # ---- MA5/MA20 死叉 (death cross) ----
    for i in range(21, n):
        if pd.isna(ma5[i]) or pd.isna(ma20[i]) or pd.isna(ma5[i - 1]) or pd.isna(ma20[i - 1]):
            continue
        if ma5[i - 1] >= ma20[i - 1] and ma5[i] < ma20[i]:
            for N in (1, 3, 5):
                if i + N < n:
                    results.append((f"MA死叉_{N}日", c[i + N] > c[i]))

    return results


# ---- aggregation ----------------------------------------------------------


def aggregate(results_list):
    """
    Aggregate list of (name, is_up) into {name: {up, down}}.
    """
    stats = defaultdict(lambda: {"up": 0, "down": 0})
    for name, is_up in results_list:
        if is_up:
            stats[name]["up"] += 1
        else:
            stats[name]["down"] += 1
    return stats


# ---- report ---------------------------------------------------------------


def print_report(stats, date_str, stock_count):
    total = lambda d: d["up"] + d["down"]
    pct = lambda d: d["up"] / total(d) * 100 if total(d) > 0 else 0

    print()
    print("═" * 47)
    print("  K线形态涨跌规律分析")
    print(f"  数据日期: {date_str} | 分析股票: {stock_count} 只")
    print("═" * 47)
    print()

    sections = [
        ("【连阳形态】", ["2连阳", "3连阳"]),
        ("【连阴形态】", ["2连阴", "3连阴"]),
        ("【放量上涨】", ["放量上涨"]),
        ("【缩量下跌】", ["缩量下跌"]),
        ("【跳空高开】", ["跳空高开_当日", "跳空高开_次日"]),
        ("【跳空低开】", ["跳空低开_当日", "跳空低开_次日"]),
        ("【长下影线】", ["长下影线"]),
        ("【长上影线】", ["长上影线"]),
        ("【MA5/MA20 金叉】", ["MA金叉_1日", "MA金叉_3日", "MA金叉_5日"]),
        ("【MA5/MA20 死叉】", ["MA死叉_1日", "MA死叉_3日", "MA死叉_5日"]),
    ]

    for section_title, keys in sections:
        printed = False
        for k in keys:
            if k not in stats or total(stats[k]) == 0:
                continue
            if not printed:
                print(section_title)
                printed = True
            print(f"  {k} → 次日上涨概率: {pct(stats[k]):.1f}% (样本: {total(stats[k])})")
        if printed:
            print()


# ---- main -----------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="K线形态涨跌规律分析")
    parser.add_argument("--date", type=str, default="", help="数据日期 YYYYMMDD（仅用于输出报告标识）")
    parser.add_argument("--stocks", type=int, default=20, help="分析股票数量上限")
    args = parser.parse_args()

    # resolve data directory relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    baostock_root = os.path.dirname(script_dir)  # baostock_data/
    data_dir = os.path.join(baostock_root, "data", "daily")

    if not os.path.isdir(data_dir):
        print(f"错误: 数据目录不存在: {data_dir}")
        sys.exit(1)

    csv_files = sorted(glob(os.path.join(data_dir, "sh.*.csv")) + glob(os.path.join(data_dir, "sz.*.csv")))
    if not csv_files:
        print(f"错误: 目录下无 CSV 文件: {data_dir}")
        sys.exit(1)

    print(f"数据目录: {data_dir}")
    print(f"发现 {len(csv_files)} 个 CSV 文件, 将分析前 {args.stocks} 只")

    all_results = []
    processed = 0
    for fp in csv_files:
        if processed >= args.stocks:
            break
        df = load_stock_csv(fp)
        if df is None:
            continue
        stock_results = detect_patterns(df)
        all_results.extend(stock_results)
        processed += 1
        if processed % 20 == 0:
            print(f"  处理进度: {processed}/{args.stocks}")

    if processed == 0:
        print("错误: 没有有效股票数据（至少需要20日历史）")
        sys.exit(1)

    print(f"  处理完毕: {processed} 只股票, 共 {len(all_results)} 条形态记录")

    stats = aggregate(all_results)
    print_report(stats, args.date, processed)


if __name__ == "__main__":
    main()
