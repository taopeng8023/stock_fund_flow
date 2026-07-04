"""趋势强度分层测试 — MA完美多头 + 信号 → 能否达5%均收益"""
import os, statistics, sys
import numpy as np, pandas as pd

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DAILY = os.path.join(PROJECT, 'baostock_data', 'data', 'daily')
sys.path.insert(0, os.path.join(PROJECT, 'baostock_data', 'analysis'))
from stock_filter import load_main_board_files
from signal_scanner import compute_indicators, PATTERN_DETECTORS, check_price_vol_signal

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

ref = pd.read_csv(os.path.join(DAILY, "sh.600000.csv"))
ref["日期"] = pd.to_datetime(ref["日期"], format="%Y-%m-%d")
dates = [d.strftime("%Y%m%d") for d in ref["日期"]
         if pd.to_datetime("2024-01-01") <= d <= pd.to_datetime("2026-07-01")][::5]

def fidx(df, ds):
    kd = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"
    for j in range(len(df)-1, max(len(df)-20,-1),-1):
        if str(df["日期"].iloc[j].date()) in (ds, kd): return j
    return None

results = {"all": [], "trend": [], "trend_signal": [], "trend_signal_mid": []}
for ds in dates:
    for code, (name, df) in stock_data.items():
        idx = fidx(df, ds)
        if idx is None or idx+1 >= len(df): continue
        c = float(df["收盘"].values[idx]); v = float(df["成交量"].values[idx])
        t = v * c / 1e8
        if t < 2 or c < 5: continue
        pos = float(df["pos_60"].values[idx]) if not pd.isna(df["pos_60"].values[idx]) else 0.5
        chg = float(df["pct_chg"].values[idx]*100) if not pd.isna(df["pct_chg"].values[idx]) else 0
        mb = bool(df["ma_bull"].values[idx])
        ma5 = df["ma5"].values[idx]; ma10 = df["ma10"].values[idx]; ma20 = df["ma20"].values[idx]
        perfect_ma = (not pd.isna(ma5) and not pd.isna(ma10) and not pd.isna(ma20)
                      and ma5 > ma10 > ma20 and c > ma5)
        has_sig = any(det(df,idx) for pn,(det,_) in PATTERN_DETECTORS.items())

        ret = (float(df["收盘"].values[idx+1])-c)/c*100

        # Tier 0: all stocks passing basic filter
        if 0.2 < pos < 0.85:
            results["all"].append(ret)
        # Tier 1: perfect MA trend (no signal needed)
        if perfect_ma and 0.2 < pos < 0.85:
            results["trend"].append(ret)
        # Tier 2: trend + signal
        if perfect_ma and has_sig and 0.2 < pos < 0.85:
            results["trend_signal"].append(ret)
        # Tier 3: trend + signal + mid position (not chasing)
        if perfect_ma and has_sig and 0.3 < pos < 0.8:
            results["trend_signal_mid"].append(ret)

print(f"扫描: {len(dates)}天 x {len(stock_data)}只\n")
for label, rets in results.items():
    if rets:
        wr = sum(1 for r in rets if r>0)/len(rets)*100
        avg = statistics.mean(rets); med = statistics.median(rets)
        m5 = sum(1 for r in rets if r>=5); m10 = sum(1 for r in rets if r>=10)
        print(f"{label:30s} {len(rets):>6}笔 WR={wr:5.1f}% 均收={avg:+6.2f}% 中位={med:+6.2f}% ≥5%:{m5:>3}({m5/len(rets)*100:4.1f}%) ≥10%:{m10:>3}")
