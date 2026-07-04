"""质量分层测试 — T+1下不同质量级别的信号收益"""
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
print(f"有效: {len(stock_data)}")

ref = pd.read_csv(os.path.join(DAILY, "sh.600000.csv"))
ref["日期"] = pd.to_datetime(ref["日期"], format="%Y-%m-%d")
from_dt = pd.to_datetime("2024-01-01"); to_dt = pd.to_datetime("2026-07-01")
dates = [d.strftime("%Y%m%d") for d in ref["日期"] if from_dt <= d <= to_dt][::5]

def fidx(df, ds):
    kd = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"
    for j in range(len(df)-1, max(len(df)-20,-1),-1):
        if str(df["日期"].iloc[j].date()) in (ds, kd): return j
    return None

all_t, high_t, ultra_t = [], [], []
for di, ds in enumerate(dates):
    for code, (name, df) in stock_data.items():
        idx = fidx(df, ds)
        if idx is None: continue
        c = float(df["收盘"].values[idx]); v = float(df["成交量"].values[idx])
        t = v * c / 1e8
        if t < 1 or c < 5: continue
        pos = float(df["pos_60"].values[idx]) if not pd.isna(df["pos_60"].values[idx]) else 0.5
        if pos > 0.92: continue
        mb = bool(df["ma_bull"].values[idx])
        chg = float(df["pct_chg"].values[idx]*100) if not pd.isna(df["pct_chg"].values[idx]) else 0
        signals = []; [signals.append(("p",pn)) for pn,(det,_) in PATTERN_DETECTORS.items() if det(df,idx)]
        [signals.append(("v",sn)) for sn,_,_ in check_price_vol_signal(df,idx)]
        if not signals: continue
        ret = (float(df["收盘"].values[idx+1])-c)/c*100 if idx+1<len(df) else None
        if ret is None: continue
        e = {"code":code,"ret":ret,"pos":pos,"mb":mb,"t":t,"chg":chg,"ns":len(signals)}
        all_t.append(e)
        if mb and 0.2<pos<0.85 and t>2:
            high_t.append(e)
        if mb and 0.25<pos<0.75 and t>3 and 0<chg<5 and len(signals)>=2:
            ultra_t.append(e)

for label, trades in [("全部",all_t), ("高质量(MA+中位+>2亿)",high_t), ("极品(MA+中位+>3亿+温和涨+≥2信号)",ultra_t)]:
    if trades:
        rs = [t["ret"] for t in trades]
        wr = sum(1 for r in rs if r>0)/len(rs)*100
        avg = statistics.mean(rs); m5 = sum(1 for r in rs if r>=5)
        print(f"{label}: {len(trades)}笔 WR={wr:.1f}% 均收={avg:+.2f}% ≥5%={m5}({m5/len(trades)*100:.1f}%)")
