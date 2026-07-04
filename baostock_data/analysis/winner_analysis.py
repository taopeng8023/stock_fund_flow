"""大赢家特征分析 — 5%+收益交易的共同特征"""
import os, sys, numpy as np, pandas as pd
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

trades = []
for ds in dates:
    for code, (name, df) in stock_data.items():
        idx = fidx(df, ds)
        if idx is None or idx+2 >= len(df): continue
        c = float(df["收盘"].values[idx]); v = float(df["成交量"].values[idx])
        t = v * c / 1e8
        if t < 2 or c < 8: continue
        cv = df["收盘"].values; vv = df["成交量"].values
        if not (cv[idx] > cv[idx-1] > cv[idx-2] and vv[idx] > vv[idx-1]): continue
        pos = float(df["pos_60"].values[idx]) if not pd.isna(df["pos_60"].values[idx]) else 0.5
        if pos < 0.65: continue
        chg = float(df["pct_chg"].values[idx]*100) if not pd.isna(df["pct_chg"].values[idx]) else 0
        if chg > 8: continue
        ret = (float(cv[idx+2]) - c) / c * 100
        vol_r = float(df["vol_ratio"].values[idx]) if not pd.isna(df["vol_ratio"].values[idx]) else 1
        up_s = float(df["up_streak"].values[idx])
        gap = (float(df["开盘"].values[idx]) - float(cv[idx-1])) / cv[idx-1] * 100 if idx>0 and cv[idx-1]>0 else 0
        ma5, ma10, ma20 = df["ma5"].values[idx], df["ma10"].values[idx], df["ma20"].values[idx]
        trades.append({"ret": ret, "pos": pos, "chg": chg, "vol_r": vol_r,
                       "up_s": up_s, "gap": gap, "t": t,
                       "ma_align": not pd.isna(ma5) and not pd.isna(ma20) and ma5>ma10>ma20})

big = [t for t in trades if t["ret"] >= 5]
small = [t for t in trades if t["ret"] < 5]
def avg(l, key): return np.mean([t[key] for t in l]) if l else 0

print(f"大赢家(≥5%): {len(big)}笔 | 其他: {len(small)}笔\n")
for key, label in [("pos","60日位置"), ("chg","当日涨%"), ("vol_r","量比"),
                    ("up_s","连涨天数"), ("gap","开盘缺口%"), ("t","成交额亿")]:
    a=avg(big,key); b=avg(small,key)
    print(f"  {label:<13s} {a:>8.2f} vs {b:>8.2f}  差{a-b:>+7.2f}")

ma_big = sum(1 for t in big if t["ma_align"])/len(big)*100 if big else 0
ma_small = sum(1 for t in small if t["ma_align"])/len(small)*100 if small else 0
print(f"  MA完美多头%   {ma_big:>8.1f}% vs {ma_small:>8.1f}%  差{ma_big-ma_small:>+7.1f}%")

# New filter: use best features
print(f"\n── 最佳过滤组合 ──")
for gap_th in [0, 0.5, 1.0]:
    for up_th in [2, 3, 4]:
        filtered = [t for t in big if t["gap"]>gap_th and t["up_s"]>=up_th]
        all_f = [t for t in trades if t["gap"]>gap_th and t["up_s"]>=up_th]
        if all_f:
            avg_r = np.mean([t["ret"] for t in all_f])
            wr = sum(1 for t in all_f if t["ret"]>0)/len(all_f)*100
            if avg_r >= 5:
                print(f"  🔥 gap>{gap_th} + 连涨≥{up_th}: {len(all_f)}笔 WR={wr:.0f}% 均收={avg_r:+.2f}%")
