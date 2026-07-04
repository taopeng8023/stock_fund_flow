#!/usr/bin/env python3
"""
高收益选股 — 形态专属严格过滤 + 专属最优持仓 → 目标单笔≥5%

策略: 每个形态只在"极品条件"下触发, 匹配训练报告的最优持仓周期
"""
import os, statistics, sys
from collections import defaultdict
import numpy as np, pandas as pd

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DAILY = os.path.join(PROJECT, 'baostock_data', 'data', 'daily')
sys.path.insert(0, os.path.join(PROJECT, 'baostock_data', 'analysis'))
from stock_filter import load_main_board_files
from signal_scanner import compute_indicators

# 极品形态定义: (检测函数, 最优持仓, 品质过滤器)
def is_3day_surge_ultra(df, i):
    """三日连涨极品: 高开 + 连涨≥4天 + MA多头 + 逼60日高 + 大盘股"""
    c = df["收盘"].values; o = df["开盘"].values; v = df["成交量"].values
    if i < 5: return False
    if not (c[i] > c[i-1] > c[i-2] and v[i] > v[i-1]): return False
    pos = df["pos_60"].values[i]
    if pd.isna(pos) or pos < 0.70: return False
    # 高开≥0.5% (大赢家特征: 强势开盘)
    if i > 0 and c[i-1] > 0:
        gap_pct = (o[i] - c[i-1]) / c[i-1] * 100
        if gap_pct < 0.5: return False
    # 连涨≥3天
    up_s = df["up_streak"].values[i]
    if pd.isna(up_s) or up_s < 3: return False
    ma5, ma10, ma20 = df["ma5"].values[i], df["ma10"].values[i], df["ma20"].values[i]
    if pd.isna(ma5) or pd.isna(ma20) or not (ma5 > ma10 > ma20): return False
    chg = df["pct_chg"].values[i] * 100 if not pd.isna(df["pct_chg"].values[i]) else 0
    if chg > 8: return False
    # 大盘股 (大赢家均值40亿 vs 其他19亿)
    turnover_yi = v[i] * c[i] / 1e8
    if turnover_yi < 4: return False
    return True

def is_deep_fall_ultra(df, i):
    """深跌反弹极品: 跌>30% + 涨停 + 巨量 + 低位"""
    if i < 60: return False
    c = df["收盘"].values; h = df["最高"].values
    v = df["成交量"].values; vol_ma20 = df["vol_ma20"].values
    high_60 = max(h[i-60:i])
    ret_60 = (c[i] - high_60) / high_60 if high_60 > 0 else 0
    if ret_60 > -0.30: return False
    chg = (c[i] - c[i-1]) / c[i-1] if i > 0 and c[i-1] > 0 else 0
    if chg < 0.095: return False
    if vol_ma20[i] <= 0 or v[i] < vol_ma20[i] * 2.5: return False
    pos = df["pos_60"].values[i]
    if not pd.isna(pos) and pos > 0.3: return False  # 必须是极低位
    return c[i] > df["ma20"].values[i] if not pd.isna(df["ma20"].values[i]) else False

def is_morning_star_ultra(df, i):
    """启明星极品: 深回调(>15%) + 放量阳 + MA金叉 + 低位"""
    if i < 20: return False
    c = df["收盘"].values; o = df["开盘"].values; v = df["成交量"].values
    h = df["最高"].values
    # 前期跌幅 >15%
    peak = max(h[i-20:i])
    drop = (c[i-2] - peak) / peak if peak > 0 else 0
    if drop > -0.12: return False
    # 启明星形态
    if not (c[i-2] < o[i-2]): return False
    body_t1 = abs(c[i-1] - o[i-1]); body_t0 = abs(c[i] - o[i])
    if not (body_t1 < body_t0 * 0.5): return False
    if not (c[i] > o[i] and v[i] > v[i-1] * 1.5): return False
    ma5, ma10 = df["ma5"].values[i], df["ma10"].values[i]
    if pd.isna(ma5) or pd.isna(ma10) or ma5 <= ma10: return False
    pos = df["pos_60"].values[i]
    return not pd.isna(pos) and pos < 0.4

def is_limit_up_ultra(df, i):
    """涨停企稳极品: 涨停后5日横盘+缩量+MA多头+中位"""
    if i < 15: return False
    c = df["收盘"].values; v = df["成交量"].values; l = df["最低"].values
    # 找15日内涨停日
    lu = None
    for j in range(i-5, i-15, -1):
        if j < 0: break
        chg_j = (c[j] - c[j-1]) / c[j-1] if j > 0 and c[j-1] > 0 else 0
        if chg_j >= 0.095 and v[j] > df["vol_ma20"].values[j] * 2:
            lu = j; break
    if lu is None or i - lu < 5: return False
    # 横盘: 不破涨停低点
    if min(l[lu+1:i]) < l[lu] * 0.97: return False
    # 缩量至涨停量30%以下
    if v[i] > v[lu] * 0.3: return False
    pos = df["pos_60"].values[i]
    if pd.isna(pos) or pos < 0.3 or pos > 0.75: return False
    return bool(df["ma_bull"].values[i])

# 极品形态注册表: name → (detector, hold_days)
ULTRA_PATTERNS = [
    ("三日连涨_量递增_逼60日高", is_3day_surge_ultra, 2, 4.65),
    ("深跌反弹_涨停巨量", is_deep_fall_ultra, 5, 7.18),
    ("启明星_MA金叉_深回调", is_morning_star_ultra, 3, 4.01),
    ("涨停横盘_缩量企稳", is_limit_up_ultra, 10, 12.90),
]

MIN_TURNOVER = 2.0  # 亿
MIN_PRICE = 8.0


def run(from_date="20240101", to_date="20260701", step=5):
    stock_files = load_main_board_files(DAILY)
    stock_data = {}
    for fp in stock_files:
        try:
            df = pd.read_csv(fp); df["日期"] = pd.to_datetime(df["日期"], format="%Y-%m-%d")
            df = df.sort_values("日期").reset_index(drop=True)
            df = df[df["成交量"] > 0].copy()
            if len(df) < 80: continue
            df = compute_indicators(df)
            code = os.path.splitext(os.path.basename(fp))[0]
            stock_data[code] = (str(df["名称"].iloc[0]), df)
        except: pass
    print(f"主板有效: {len(stock_data)} 只")

    ref = pd.read_csv(os.path.join(DAILY, "sh.600000.csv"))
    ref["日期"] = pd.to_datetime(ref["日期"], format="%Y-%m-%d")
    fd = pd.to_datetime(f"{from_date[:4]}-{from_date[4:6]}-{from_date[6:8]}")
    td = pd.to_datetime(f"{to_date[:4]}-{to_date[4:6]}-{to_date[6:8]}")
    dates = [d.strftime("%Y%m%d") for d in ref["日期"] if fd <= d <= td][::step]

    def fidx(df, ds):
        kd = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"
        for j in range(len(df)-1, max(len(df)-20,-1),-1):
            if str(df["日期"].iloc[j].date()) in (ds, kd): return j
        return None

    all_trades = []
    pattern_stats = defaultdict(list)

    for ds in dates:
        for code, (name, df) in stock_data.items():
            idx = fidx(df, ds)
            if idx is None: continue
            c = float(df["收盘"].values[idx]); v = float(df["成交量"].values[idx])
            t = v * c / 1e8
            if t < MIN_TURNOVER or c < MIN_PRICE: continue

            for pname, detector, hold_days, ref_r in ULTRA_PATTERNS:
                try:
                    if not detector(df, idx): continue
                except: continue

                exit_idx = idx + hold_days
                if exit_idx >= len(df): continue
                exit_p = float(df["收盘"].values[exit_idx])
                if c <= 0: continue
                ret = (exit_p - c) / c * 100
                pattern_stats[pname].append(ret)
                all_trades.append({"ret": ret, "pattern": pname, "hold": hold_days, "code": code})

    # 输出
    print(f"\n{'═'*65}")
    print(f"  极品形态高收益回测 ({from_date}→{to_date})")
    print(f"{'═'*65}")
    print(f"  {'形态':<25s} {'持仓':>4s} {'笔数':>5} {'胜率':>7s} {'均收益':>8s} {'≥5%':>5s} {'≥10%':>5s}")
    print(f"  {'─'*60}")

    for pname, detector, hold, ref_r in ULTRA_PATTERNS:
        rets = pattern_stats.get(pname, [])
        if rets:
            wr = sum(1 for r in rets if r > 0) / len(rets) * 100
            avg = statistics.mean(rets)
            m5 = sum(1 for r in rets if r >= 5)
            m10 = sum(1 for r in rets if r >= 10)
            star = "🔥" if avg >= 5.0 else ("✅" if avg >= 3.0 else "")
            print(f"  {pname:<25s} T+{hold:<3d} {len(rets):>5} {wr:>6.1f}% {avg:>+7.2f}% {m5:>4} {m10:>4} {star}")

    if all_trades:
        rs = [t["ret"] for t in all_trades]
        wr = sum(1 for r in rs if r>0)/len(rs)*100
        avg = statistics.mean(rs)
        m5 = sum(1 for r in rs if r>=5); m10 = sum(1 for r in rs if r>=10)
        print(f"\n  {'─'*60}")
        print(f"  综合: {len(all_trades)}笔 WR={wr:.1f}% 均收={avg:+.2f}% ≥5%:{m5}({m5/len(all_trades)*100:.0f}%) ≥10%:{m10}({m10/len(all_trades)*100:.0f}%)")
        print(f"  最大: {max(rs):+.2f}%  最小: {min(rs):+.2f}%  中位: {statistics.median(rs):+.2f}%")

        if avg >= 5.0:
            print(f"\n  🔥🔥🔥 均收益 {avg:+.2f}% ≥ 5% — 目标达成! 🔥🔥🔥")

    return all_trades


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="fr", default="20240101")
    p.add_argument("--to", dest="to", default="20260701")
    p.add_argument("--step", type=int, default=5)
    args = p.parse_args()
    run(args.fr, args.to, args.step)
