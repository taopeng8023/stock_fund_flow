"""网格搜索 — gap×turnover 最优参数组合"""
import os, sys, statistics, numpy as np, pandas as pd
PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DAILY = os.path.join(PROJECT, 'baostock_data', 'data', 'daily')
sys.path.insert(0, os.path.join(PROJECT, 'baostock_data', 'analysis'))
from stock_filter import load_main_board_files
from signal_scanner import compute_indicators

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

results = {}
for min_t in [2, 3, 5, 8]:
    for min_gap in [0, 0.3, 0.5, 0.8, 1.0]:
        for min_pos in [0.45, 0.55, 0.65]:
            trades = []
            for ds in dates:
                for code, (name, df) in stock_data.items():
                    idx = fidx(df, ds)
                    if idx is None or idx+2 >= len(df): continue
                    c = float(df["收盘"].values[idx]); v = float(df["成交量"].values[idx])
                    t = v * c / 1e8
                    if t < min_t or c < 8: continue
                    cv = df["收盘"].values; ov = df["开盘"].values
                    vv = df["成交量"].values
                    if not (cv[idx] > cv[idx-1] > cv[idx-2] and vv[idx] > vv[idx-1]): continue
                    pos = float(df["pos_60"].values[idx]) if not pd.isna(df["pos_60"].values[idx]) else 0.5
                    if pos < min_pos: continue
                    if idx > 0 and cv[idx-1] > 0:
                        gap = (ov[idx] - cv[idx-1]) / cv[idx-1] * 100
                        if gap < min_gap: continue
                    chg = float(df["pct_chg"].values[idx]*100) if not pd.isna(df["pct_chg"].values[idx]) else 0
                    if chg > 9.5: continue
                    ret = (float(cv[idx+2]) - c) / c * 100
                    trades.append(ret)
            if trades:
                wr = sum(1 for r in trades if r>0)/len(trades)*100
                avg = statistics.mean(trades)
                if avg >= 4.0:
                    results[(min_t, min_gap, min_pos)] = (len(trades), wr, avg)

print(f"{'t>亿':>5s} {'gap>%':>6s} {'pos>':>5s} {'笔数':>5} {'WR':>7s} {'均收益':>8s}")
print(f"{'─'*45}")
for (t, g, p), (n, wr, avg) in sorted(results.items(), key=lambda x: -x[1][2]):
    star = "🔥" if avg >= 5.0 else ("✅" if avg >= 4.5 else "")
    print(f"  {t:>5} {g:>5.1f}% {p:>4.2f} {n:>5} {wr:>6.1f}% {avg:>+7.2f}% {star}")
