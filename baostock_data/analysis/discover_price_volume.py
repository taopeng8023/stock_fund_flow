#!/usr/bin/env python3
"""
涨跌幅 × 成交量 复合信号发现引擎 v2 — 向量化高效版
核心: 涨跌幅分位 × 量比分位 × 位置分位 → 胜率≥85%

用法:
    python discover_price_volume.py --sample 2000 --target 85
"""
import argparse, os, random, sys, warnings
from collections import defaultdict
import numpy as np, pandas as pd

try:
    from baostock_data.analysis.stock_filter import load_stock_files, print_filter_summary
except ImportError:
    from stock_filter import load_stock_files, print_filter_summary

warnings.filterwarnings("ignore")
MIN_DAYS, WIN_THRESHOLD = 80, 0.005


def compute_indicators(df):
    c = df["收盘"].values; o = df["开盘"].values
    h = df["最高"].values; l = df["最低"].values
    v = df["成交量"].values; pc = df["前收盘"].values
    n = len(df)
    s = pd.Series(c); sv = pd.Series(v)

    for w in [5, 10, 20, 60]:
        df[f"ma{w}"] = s.rolling(w, min_periods=w).mean().values
    df["vol_ma5"] = sv.rolling(5, min_periods=5).mean().values
    df["vol_ma20"] = sv.rolling(20, min_periods=20).mean().values

    # Price changes
    df["chg_1d"] = np.where(pc > 0, (c - pc) / pc, 0)
    for w in [3, 5, 10]:
        shifted = s.shift(w).values
        df[f"chg_{w}d"] = np.where((shifted > 0) & (np.arange(n) >= w),
                                    (c - shifted) / shifted, 0)
    # Volume ratios
    mv5 = df["vol_ma5"].values; mv20 = df["vol_ma20"].values
    df["vr5"] = np.where(mv5 > 0, v / mv5, 1.0)
    df["vr20"] = np.where(mv20 > 0, v / mv20, 1.0)

    # Price position
    hh20 = pd.Series(h).rolling(20, min_periods=20).max().values
    ll20 = pd.Series(l).rolling(20, min_periods=20).min().values
    rng20 = hh20 - ll20
    df["pos20"] = np.where(rng20 > 0, (c - ll20) / rng20, 0.5)

    # Basic
    df["body_ratio"] = np.where(h - l > 0, np.abs(c - o) / (h - l), 0)
    df["is_yang"] = (c > o).astype(int)
    df["amplitude"] = np.where(pc > 0, (h - l) / pc, 0)

    # MA
    ma5v, ma10v, ma20v = df["ma5"].values, df["ma10"].values, df["ma20"].values
    df["ma_bull"] = ((ma5v > ma10v) & (ma10v > ma20v)).astype(int)
    df["dist_ma20"] = np.where((ma20v > 0) & ~np.isnan(ma20v), (c - ma20v) / ma20v, 0)
    ma20_shifted = pd.Series(ma20v).shift(5).fillna(pd.Series(ma20v)).values
    df["ma20_slope"] = np.where((ma20v > 0) & ~np.isnan(ma20v),
                                 (ma20v - ma20_shifted) / ma20v, 0)
    df["volatility"] = pd.Series(df["chg_1d"].values).rolling(20, min_periods=20).std().fillna(0.03).values
    return df


def load_stock_csv(fp):
    try:
        df = pd.read_csv(fp)
        if len(df) < MIN_DAYS: return None
        df["日期"] = pd.to_datetime(df["日期"], format="%Y-%m-%d")
        df = df.sort_values("日期").reset_index(drop=True)
        df = df[df["成交量"] > 0].copy()
        return df if len(df) >= MIN_DAYS else None
    except: return None


def fwd_ret(df, entry_idx, hold):
    n = len(df); c = df["收盘"].values
    e = entry_idx + hold
    return (c[e] - c[entry_idx]) / c[entry_idx] if e < n and c[entry_idx] > 0 else None


def confirm_ultra(df, si):
    n = len(df); j = si + 1
    if j >= n: return False
    cv, hv = df["收盘"].values, df["最高"].values
    lv, ov = df["最低"].values, df["开盘"].values
    vv = df["成交量"].values; is_y = df["is_yang"].values
    vr5 = df["vr5"].values
    if ov[j] > 0 and (cv[j] - ov[j]) / ov[j] < -0.06: return False
    if lv[j] < lv[si] * 0.97: return False
    if vr5[j] < 0.25: return False
    return cv[j] > hv[si] and is_y[j] and vv[j] > vv[si]


# ═══════════════════════════════════════
# 向量化信号发现（核心）
# ═══════════════════════════════════════
def discover(data_dir, target_wr=85.0, sample=2000, seed=42):
    random.seed(seed); np.random.seed(seed)
    stock_files = load_stock_files(data_dir)
    files = random.sample(stock_files, min(sample, len(stock_files))) if sample > 0 else stock_files
    print_filter_summary(data_dir)
    print(f"训练样本: {len(files)} 只 | 目标: ≥{target_wr}% | seed={seed}")

    HOLD_PERIODS = [1, 2, 3, 5, 10, 15]

    # ── 涨跌幅×量比×位置的网格区间 ──
    chg_bins = [(-0.30, -0.15), (-0.15, -0.10), (-0.10, -0.07), (-0.07, -0.05),
                (-0.05, -0.03), (-0.03, -0.01), (-0.01, 0.01), (0.01, 0.02),
                (0.02, 0.03), (0.03, 0.05), (0.05, 0.08), (0.08, 0.12), (0.12, 0.50)]
    vol_bins = [(0, 0.3), (0.3, 0.5), (0.5, 0.8), (0.8, 1.0), (1.0, 1.2),
                (1.2, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 10)]
    pos_bins = [(0, 0.1), (0.1, 0.2), (0.2, 0.35), (0.35, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.0)]

    # 信号: (chg_bin, vol_bin, pos_bin, require_yang, confirm_required, ma_bull_required)
    # 只生成最有前景的组合
    signal_defs = []

    # 超跌反弹类: 跌 + 放量 + 低位 + 收阳 + ▲确认
    for chg_i, (cl, ch) in enumerate(chg_bins):
        if ch >= -0.01: continue  # only negative/oversold
        for vol_i, (vl, vh) in enumerate(vol_bins):
            if vl < 1.0: continue  # only above-average volume
            for pos_i, (pl, ph) in enumerate(pos_bins):
                if ph > 0.35: continue  # only low position
                signal_defs.append((f"A_chg{cl}_{ch}_vol{vl}_{vh}_pos{pl}_{ph}",
                                    cl, ch, vl, vh, pl, ph, True, True, False))

    # 趋势加速类: 涨 + 放量 + 中高位 + 收阳 + 多头
    for chg_i, (cl, ch) in enumerate(chg_bins):
        if cl <= 0.01: continue  # only positive
        for vol_i, (vl, vh) in enumerate(vol_bins):
            if vl < 0.8: continue
            for pos_i, (pl, ph) in enumerate(pos_bins):
                if pl < 0.3: continue  # mid-to-high
                signal_defs.append((f"B_chg{cl}_{ch}_vol{vl}_{vh}_pos{pl}_{ph}",
                                    cl, ch, vl, vh, pl, ph, True, False, True))

    # 止跌企稳类: 小幅波动 + 缩量 + 低位
    for chg_i, (cl, ch) in enumerate(chg_bins):
        if cl < -0.03 or ch > 0.03: continue  # narrow range
        for vol_i, (vl, vh) in enumerate(vol_bins):
            if vh > 0.8: continue  # low volume
            for pos_i, (pl, ph) in enumerate(pos_bins):
                if ph > 0.4: continue  # low position
                signal_defs.append((f"C_chg{cl}_{ch}_vol{vl}_{vh}_pos{pl}_{ph}",
                                    cl, ch, vl, vh, pl, ph, False, False, False))

    # 突破类: 中涨 + 放量 + 中位 + 收阳
    for chg_i, (cl, ch) in enumerate(chg_bins):
        if cl < 0.02 or ch > 0.12: continue
        for vol_i, (vl, vh) in enumerate(vol_bins):
            if vl < 1.2: continue
            for pos_i, (pl, ph) in enumerate(pos_bins):
                if pl < 0.2 or ph > 0.8: continue
                signal_defs.append((f"D_chg{cl}_{ch}_vol{vl}_{vh}_pos{pl}_{ph}",
                                    cl, ch, vl, vh, pl, ph, True, True, False))

    # 温和上涨类: 小涨 + 适中量 + 任意位置
    for chg_i, (cl, ch) in enumerate(chg_bins):
        if cl < 0.01 or ch > 0.05: continue
        for vol_i, (vl, vh) in enumerate(vol_bins):
            if vl < 0.8 or vh > 2.0: continue
            for pos_i, (pl, ph) in enumerate(pos_bins):
                if ph < 0.3 or pl > 0.8: continue
                signal_defs.append((f"E_chg{cl}_{ch}_vol{vl}_{vh}_pos{pl}_{ph}",
                                    cl, ch, vl, vh, pl, ph, True, False, False))

    print(f"信号定义: {len(signal_defs)} 个\n")

    # ── 逐只股票扫描 ──
    results = {}  # {(name, hold): [returns]}
    processed = 0

    for fp in files:
        df = load_stock_csv(fp)
        if df is None: continue
        df = compute_indicators(df)
        nd = len(df)

        # 预取所有需要的数组（向量化关键）
        chg_3d = df["chg_3d"].values
        chg_5d = df["chg_5d"].values
        vr5 = df["vr5"].values
        vr20 = df["vr20"].values
        pos20 = df["pos20"].values
        is_yang = df["is_yang"].values
        ma_bull = df["ma_bull"].values
        ma20_slope = df["ma20_slope"].values
        volatility = df["volatility"].values
        body_ratio = df["body_ratio"].values

        # 对每天评估所有信号（向量化批量检查）
        for i in range(60, nd - 2):
            # 基础过滤
            if volatility[i] > 0.08 or pd.isna(volatility[i]): continue
            if ma20_slope[i] < -0.07: continue  # MA20暴跌不做多

            chg_val = chg_5d[i]  # 使用5日涨跌幅作为主特征
            vol_val = vr5[i]
            pos_val = pos20[i]
            yang = is_yang[i]
            bull = ma_bull[i]

            for name, cl, ch, vl, vh, pl, ph, need_yang, need_confirm, need_bull in signal_defs:
                # 快速检查区间
                if not (cl <= chg_val < ch): continue
                if not (vl <= vol_val < vh): continue
                if not (pl <= pos_val < ph): continue
                if need_yang and not yang: continue
                if need_bull and not bull: continue
                if need_confirm and not confirm_ultra(df, i): continue

                entry_idx = i + 1
                for hold in HOLD_PERIODS:
                    ret = fwd_ret(df, entry_idx, hold)
                    if ret is not None:
                        key = (f"{name}_▲" if need_confirm else name, hold)
                        if key not in results: results[key] = []
                        results[key].append(ret)

        processed += 1
        if processed % 200 == 0:
            total = sum(len(v) for v in results.values())
            print(f"  {processed}/{len(files)} 样本: {total}", flush=True)

    print(f"  完成: {processed} 只, 总样本: {sum(len(v) for v in results.values())}\n")

    # ── 分析结果 ──
    summary = []
    for (name, hold), rets in results.items():
        if len(rets) < 8: continue
        wins = sum(1 for r in rets if r > WIN_THRESHOLD)
        wr = wins / len(rets) * 100
        avg_r = np.mean(rets) * 100
        summary.append({"name": name, "hold": hold, "wr": wr, "n": len(rets),
                        "wins": wins, "avg_r": avg_r})

    summary.sort(key=lambda x: x["wr"], reverse=True)

    qualifying = [r for r in summary if r["wr"] >= target_wr and r["n"] >= 8]
    near = [r for r in summary if target_wr - 5 <= r["wr"] < target_wr and r["n"] >= 8]
    near.sort(key=lambda x: x["wr"], reverse=True)

    print("═" * 85)
    print(f"  涨跌幅×成交量 复合信号发现报告 v2")
    print("═" * 85)

    if qualifying:
        print(f"\n  ✅ 达标组合 (胜率≥{target_wr}%, n≥8): {len(qualifying)} 个\n")
        print(f"  {'信号名称':55s} {'持仓':>4s} {'胜率':>7s} {'样本':>6s} {'均收益':>8s}")
        print("-" * 85)
        for r in qualifying[:30]:
            print(f"  {r['name']:55s} T+{r['hold']:<3d} {r['wr']:6.1f}% {r['n']:5d}  {r['avg_r']:+7.2f}%")
    else:
        print(f"\n  ❌ 无达标组合 (胜率≥{target_wr}%, n≥8)")

    if near:
        print(f"\n  ⚠ 接近达标: {len(near)} 个\n")
        print(f"  {'信号名称':55s} {'持仓':>4s} {'胜率':>7s} {'样本':>6s} {'均收益':>8s}")
        print("-" * 85)
        for r in near[:15]:
            print(f"  {r['name']:55s} T+{r['hold']:<3d} {r['wr']:6.1f}% {r['n']:5d}  {r['avg_r']:+7.2f}%")

    # ── 全局 TOP 30 ──
    print(f"\n── 全局 TOP 30 ──")
    print(f"  {'信号名称':55s} {'持仓':>4s} {'胜率':>7s} {'样本':>6s} {'均收益':>8s}")
    print("-" * 90)
    for r in summary[:30]:
        print(f"  {r['name']:55s} T+{r['hold']:<3d} {r['wr']:6.1f}% {r['n']:5d}  {r['avg_r']:+7.2f}%")

    print(f"\n{'═' * 85}")
    return qualifying, summary


def main():
    p = argparse.ArgumentParser(description="涨跌幅×成交量 复合信号发现 v2")
    p.add_argument("--target", type=float, default=85.0)
    p.add_argument("--sample", type=int, default=2000)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(os.path.dirname(script_dir), "data", "daily")
    if not os.path.isdir(data_dir): print(f"错误: {data_dir}"); sys.exit(1)

    qualify, _ = discover(data_dir, args.target, args.sample, args.seed)
    sys.exit(0 if qualify else 1)


if __name__ == "__main__":
    main()
