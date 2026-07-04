#!/usr/bin/env python3
"""
最优持仓回测 — 使用训练报告验证的形态专属持仓周期。

假设: 每个形态在训练中有最优持仓天数, 如果匹配持仓, 均收益可达 4-12%

用法:
  python optimal_hold_test.py --from 20240101 --to 20260701
"""
import argparse
import os
import statistics
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DAILY_DIR = os.path.join(PROJECT_ROOT, "baostock_data", "data", "daily")

# 形态→最优持仓天数+参考收益 (来自 TRAINING_REPORT_20260703)
OPTIMAL_HOLD = {
    "三日连涨_量递增_逼60日高":  (2, 4.65),
    "深跌35%_涨停_巨量_突破MA20": (5, 7.18),
    "启明星_MA金叉收敛_放量阳":   (3, 4.01),
    "涨停_放量横盘_缩量企稳_多头":  (10, 12.90),
    "急跌12%_长下影_放量收阳_低位": (10, 8.89),
    "低位缩量微涨":              (5, 4.36),
    "深跌放量反弹":              (3, 0.54),
    "中涨爆量追高":              (5, 3.56),
}

MIN_DAYS = 80

try:
    from stock_filter import load_main_board_files, is_main_board
except ImportError:
    from baostock_data.analysis.stock_filter import load_main_board_files, is_main_board

from signal_scanner import compute_indicators, PATTERN_DETECTORS, check_price_vol_signal


def find_date_idx(df, date_str):
    kdate = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    for j in range(len(df)-1, max(len(df)-20, -1), -1):
        if str(df["日期"].iloc[j].date()) in (date_str, kdate):
            return j
    return None


def run(from_date, to_date, step=5):
    stock_files = load_main_board_files(DAILY_DIR)
    print(f"主板: {len(stock_files)} 只 | {from_date}→{to_date} 步长{step}天")
    print(f"策略: 形态专属最优持仓 (训练报告验证)\n")

    # 加载+预计算
    stock_data = {}
    for fp in stock_files:
        try:
            df = pd.read_csv(fp)
            df["日期"] = pd.to_datetime(df["日期"], format="%Y-%m-%d")
            df = df.sort_values("日期").reset_index(drop=True)
            df = df[df["成交量"] > 0].copy()
            if len(df) < MIN_DAYS: continue
            code = os.path.splitext(os.path.basename(fp))[0]
            name = str(df["名称"].iloc[0]) if "名称" in df.columns else code
            df = compute_indicators(df)
            stock_data[code] = (name, df)
        except: pass
    print(f"有效股票: {len(stock_data)} 只\n")

    # 生成交易日
    ref_df = pd.read_csv(os.path.join(DAILY_DIR, "sh.600000.csv"))
    ref_df["日期"] = pd.to_datetime(ref_df["日期"], format="%Y-%m-%d")
    from_dt = pd.to_datetime(f"{from_date[:4]}-{from_date[4:6]}-{from_date[6:8]}")
    to_dt = pd.to_datetime(f"{to_date[:4]}-{to_date[4:6]}-{to_date[6:8]}")
    dates = [d.strftime("%Y%m%d") for d in ref_df["日期"]
             if from_dt <= d <= to_dt][::step]

    # 按形态分组跟踪
    pattern_trades = defaultdict(list)  # pattern_name -> [{ret, win}]
    all_trades = []

    for di, date_str in enumerate(dates):
        for code, (name, df) in stock_data.items():
            idx = find_date_idx(df, date_str)
            if idx is None: continue

            c = float(df["收盘"].values[idx])
            v = float(df["成交量"].values[idx])
            turnover = v * c / 1e8
            if turnover < 1 or c < 5: continue
            pos = float(df["pos_60"].values[idx]) if not pd.isna(df["pos_60"].values[idx]) else 0.5
            if pos > 0.92: continue

            # 检测形态
            for pname, (detector, _) in PATTERN_DETECTORS.items():
                try:
                    if detector(df, idx):
                        hold_days, ref_r = OPTIMAL_HOLD.get(pname, (1, 0))
                        # 计算最优持仓收益
                        exit_idx = idx + hold_days
                        if exit_idx >= len(df): continue
                        exit_p = float(df["收盘"].values[exit_idx])
                        if c <= 0: continue
                        ret = (exit_p - c) / c * 100
                        pattern_trades[pname].append(ret)
                        all_trades.append({"ret": ret, "pattern": pname, "hold": hold_days})
                except: pass

            # 涨跌量价信号
            for sig_name, _, _ in check_price_vol_signal(df, idx):
                hold_days, ref_r = OPTIMAL_HOLD.get(sig_name, (1, 0))
                exit_idx = idx + hold_days
                if exit_idx >= len(df): continue
                exit_p = float(df["收盘"].values[exit_idx])
                if c <= 0: continue
                ret = (exit_p - c) / c * 100
                pattern_trades[sig_name].append(ret)
                all_trades.append({"ret": ret, "pattern": sig_name, "hold": hold_days})

        if (di+1) % 50 == 0:
            print(f"  {di+1}/{len(dates)} {date_str} 累计{len(all_trades)}笔", flush=True)

    # 输出
    print(f"\n{'═'*70}")
    print(f"  最优持仓回测结果")
    print(f"{'═'*70}")
    print(f"  总交易: {len(all_trades)} 笔\n")
    print(f"  {'形态':<30s} {'持仓':>4s} {'笔数':>5} {'胜率':>7s} {'均收益':>8s} {'参考收益':>8s}")
    print(f"  {'─'*65}")

    for pname, (hold, ref_r) in OPTIMAL_HOLD.items():
        rets = pattern_trades.get(pname, [])
        if rets:
            wr = sum(1 for r in rets if r > 0) / len(rets) * 100
            avg = statistics.mean(rets)
            star = "🔥" if avg >= 5.0 else ("✅" if avg >= 3.0 else "")
            print(f"  {pname:<30s} T+{hold:<3d} {len(rets):>5} {wr:>6.1f}% {avg:>+7.2f}% {ref_r:>+7.2f}% {star}")

    if all_trades:
        all_rets = [t["ret"] for t in all_trades]
        wr = sum(1 for r in all_rets if r > 0) / len(all_rets) * 100
        avg = statistics.mean(all_rets)
        print(f"\n  {'─'*65}")
        print(f"  综合: {len(all_trades)} 笔 | WR={wr:.1f}% | 均收益={avg:+.2f}%")
        print(f"  最大: {max(all_rets):+.2f}%  最小: {min(all_rets):+.2f}%  中位: {statistics.median(all_rets):+.2f}%")

        # 5%+ 命中率
        big_wins = sum(1 for r in all_rets if r >= 5.0)
        print(f"  收益≥5%: {big_wins}/{len(all_rets)} ({big_wins/len(all_rets)*100:.1f}%)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_date", default="20240101")
    parser.add_argument("--to", dest="to_date", default="20260701")
    parser.add_argument("--step", type=int, default=5)
    args = parser.parse_args()
    run(args.from_date, args.to_date, args.step)
