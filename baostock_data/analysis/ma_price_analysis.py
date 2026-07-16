#!/usr/bin/env python3
"""
均线(MA5/10/15/30)与股价涨跌关系分析 — 向量化统计

三类关系:
  1. 状态类: 价格相对各均线位置 / 多头空头排列 → 后N日胜率+均收益
  2. 事件类: 价格上穿/下穿均线, 均线金叉/死叉 → 后N日胜率+均收益
  3. 乖离率: (close-MA30)/MA30 分桶 → 均值回归检验

用法:
    python baostock_data/analysis/ma_price_analysis.py --stocks 800
    python baostock_data/analysis/ma_price_analysis.py --stocks 0   # 全量

依赖: pandas + numpy
"""
import argparse
import os
import sys
import warnings
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from baostock_data.analysis.stock_filter import load_stock_files, print_filter_summary
except ImportError:
    from stock_filter import load_stock_files, print_filter_summary

warnings.filterwarnings("ignore")

MA_WINDOWS = (5, 10, 15, 30)
HORIZONS = (1, 3, 5, 10, 20)
MIN_DAYS = 60


def load_stock_csv(filepath: str, min_days: int = MIN_DAYS) -> Optional[pd.DataFrame]:
    """Load single stock daily CSV, return sorted DataFrame or None."""
    try:
        df = pd.read_csv(filepath, usecols=["日期", "收盘", "成交量"])
        if len(df) < min_days:
            return None
        df = df[df["成交量"] > 0].copy()
        df = df.sort_values("日期").reset_index(drop=True)
        return df if len(df) >= min_days else None
    except Exception:
        return None


def build_conditions(df: pd.DataFrame) -> Tuple[Dict[str, np.ndarray], Dict[int, np.ndarray]]:
    """
    Compute boolean condition masks and forward returns for one stock.
    Returns (conditions {name: bool array}, fwd_returns {N: float array}).
    """
    c = df["收盘"].values.astype(float)
    n = len(c)
    close = pd.Series(c)

    ma = {w: close.rolling(w, min_periods=w).mean().values for w in MA_WINDOWS}

    fwd: Dict[int, np.ndarray] = {}
    for N in HORIZONS:
        f = np.full(n, np.nan)
        if n > N:
            f[:-N] = c[N:] / c[:-N] - 1.0
        fwd[N] = f

    conds: Dict[str, np.ndarray] = {}

    valid = ~np.isnan(ma[30])
    conds["基准_所有交易日"] = valid

    # ── 状态类: 价格相对均线位置 ──
    for w in MA_WINDOWS:
        above = valid & (c > ma[w])
        conds[f"价格>MA{w}"] = above
        conds[f"价格<MA{w}"] = valid & (c < ma[w])

    bull = valid & (ma[5] > ma[10]) & (ma[10] > ma[15]) & (ma[15] > ma[30])
    bear = valid & (ma[5] < ma[10]) & (ma[10] < ma[15]) & (ma[15] < ma[30])
    conds["多头排列(5>10>15>30)"] = bull
    conds["空头排列(5<10<15<30)"] = bear
    conds["价格站上全部4条均线"] = valid & (c > ma[5]) & (c > ma[10]) & (c > ma[15]) & (c > ma[30])
    conds["价格跌破全部4条均线"] = valid & (c < ma[5]) & (c < ma[10]) & (c < ma[15]) & (c < ma[30])

    ma30_s = pd.Series(ma[30])
    ma30_rising = valid & (ma[30] > ma30_s.shift(5).values)
    conds["MA30向上(5日斜率)"] = ma30_rising
    conds["MA30向下(5日斜率)"] = valid & (ma[30] < ma30_s.shift(5).values)
    conds["多头排列+MA30向上"] = bull & ma30_rising
    conds["多头排列+价格回落MA10下"] = bull & (c < ma[10])

    # ── 事件类: 价格穿越均线 ──
    prev_c = np.roll(c, 1)
    prev_c[0] = np.nan
    for w in MA_WINDOWS:
        prev_ma = np.roll(ma[w], 1)
        prev_ma[0] = np.nan
        cross_up = valid & (prev_c <= prev_ma) & (c > ma[w])
        cross_dn = valid & (prev_c >= prev_ma) & (c < ma[w])
        conds[f"价格上穿MA{w}"] = cross_up
        conds[f"价格下穿MA{w}"] = cross_dn

    # ── 事件类: 均线交叉 ──
    for fast, slow in ((5, 10), (5, 30), (10, 30)):
        pf = np.roll(ma[fast], 1)
        ps = np.roll(ma[slow], 1)
        pf[0] = np.nan
        ps[0] = np.nan
        gc = valid & (pf <= ps) & (ma[fast] > ma[slow])
        dc = valid & (pf >= ps) & (ma[fast] < ma[slow])
        conds[f"金叉MA{fast}上穿MA{slow}"] = gc
        conds[f"死叉MA{fast}下穿MA{slow}"] = dc
        conds[f"金叉MA{fast}xMA{slow}+MA30向上"] = gc & ma30_rising

    # ── 乖离率分桶: (close-MA30)/MA30 ──
    bias = np.where(valid & (ma[30] > 0), (c - ma[30]) / ma[30], np.nan)
    buckets = [
        ("乖离MA30 < -20%", bias < -0.20),
        ("乖离MA30 -20%~-10%", (bias >= -0.20) & (bias < -0.10)),
        ("乖离MA30 -10%~0%", (bias >= -0.10) & (bias < 0)),
        ("乖离MA30 0%~+10%", (bias >= 0) & (bias < 0.10)),
        ("乖离MA30 +10%~+20%", (bias >= 0.10) & (bias < 0.20)),
        ("乖离MA30 > +20%", bias >= 0.20),
    ]
    for name, mask in buckets:
        conds[name] = valid & np.nan_to_num(mask, nan=False).astype(bool)

    return conds, fwd


def accumulate(stats: Dict[str, Dict[int, List[float]]],
               conds: Dict[str, np.ndarray],
               fwd: Dict[int, np.ndarray]) -> None:
    """Accumulate (count, wins, sum_ret) per condition per horizon."""
    for name, mask in conds.items():
        for N, f in fwd.items():
            m = mask & ~np.isnan(f)
            if not m.any():
                continue
            rets = f[m]
            cell = stats[name][N]
            cell[0] += len(rets)
            cell[1] += int((rets > 0).sum())
            cell[2] += float(rets.sum())


def print_report(stats: Dict[str, Dict[int, List[float]]], stock_count: int) -> None:
    def fmt(name: str) -> str:
        cells = stats.get(name)
        if not cells:
            return ""
        parts = []
        for N in HORIZONS:
            cnt, wins, s = cells.get(N, [0, 0, 0.0])
            if cnt == 0:
                parts.append(f"T+{N}: --")
                continue
            wr = wins / cnt * 100
            avg = s / cnt * 100
            parts.append(f"T+{N}: {wr:5.1f}%/{avg:+6.2f}%")
        n_total = cells.get(1, [0, 0, 0.0])[0]
        return f"  {name:28s} n={n_total:>9,d}  " + "  ".join(parts)

    sections = [
        ("【基准 — 全部交易日】", ["基准_所有交易日"]),
        ("【状态: 价格相对均线】", [f"价格>MA{w}" for w in MA_WINDOWS] + [f"价格<MA{w}" for w in MA_WINDOWS]),
        ("【状态: 均线排列】", [
            "多头排列(5>10>15>30)", "空头排列(5<10<15<30)",
            "价格站上全部4条均线", "价格跌破全部4条均线",
            "MA30向上(5日斜率)", "MA30向下(5日斜率)",
            "多头排列+MA30向上", "多头排列+价格回落MA10下",
        ]),
        ("【事件: 价格穿越均线】",
         [f"价格上穿MA{w}" for w in MA_WINDOWS] + [f"价格下穿MA{w}" for w in MA_WINDOWS]),
        ("【事件: 均线金叉/死叉】", [
            "金叉MA5上穿MA10", "死叉MA5下穿MA10",
            "金叉MA5上穿MA30", "死叉MA5下穿MA30",
            "金叉MA10上穿MA30", "死叉MA10下穿MA30",
            "金叉MA5xMA10+MA30向上", "金叉MA5xMA30+MA30向上", "金叉MA10xMA30+MA30向上",
        ]),
        ("【乖离率: (价-MA30)/MA30】", [
            "乖离MA30 < -20%", "乖离MA30 -20%~-10%", "乖离MA30 -10%~0%",
            "乖离MA30 0%~+10%", "乖离MA30 +10%~+20%", "乖离MA30 > +20%",
        ]),
    ]

    print()
    print("═" * 118)
    print(f"  均线(MA5/10/15/30)与股价涨跌关系 | 股票数: {stock_count} | 口径: 胜率%/平均收益%")
    print("═" * 118)
    for title, names in sections:
        print()
        print(title)
        for name in names:
            line = fmt(name)
            if line:
                print(line)
    print()
    print("═" * 118)


def main() -> None:
    parser = argparse.ArgumentParser(description="均线与股价涨跌关系分析")
    parser.add_argument("--stocks", type=int, default=800, help="分析股票数量上限 (0=全量)")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(os.path.dirname(script_dir), "data", "daily")
    if not os.path.isdir(data_dir):
        print(f"错误: 数据目录不存在: {data_dir}")
        sys.exit(1)

    csv_files = load_stock_files(data_dir)
    if not csv_files:
        print(f"错误: 目录下无个股 CSV 文件: {data_dir}")
        sys.exit(1)

    limit = args.stocks if args.stocks > 0 else len(csv_files)
    print(f"数据目录: {data_dir}")
    print_filter_summary(data_dir)
    print(f"将分析前 {limit} 只个股")

    stats: Dict[str, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0.0]))
    processed = 0
    for fp in csv_files:
        if processed >= limit:
            break
        df = load_stock_csv(fp)
        if df is None:
            continue
        conds, fwd = build_conditions(df)
        accumulate(stats, conds, fwd)
        processed += 1
        if processed % 100 == 0:
            print(f"  处理进度: {processed}/{limit}", flush=True)

    if processed == 0:
        print("错误: 没有有效股票数据")
        sys.exit(1)

    print(f"  处理完毕: {processed} 只股票")
    print_report(stats, processed)


if __name__ == "__main__":
    main()
