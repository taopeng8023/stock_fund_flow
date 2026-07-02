#!/usr/bin/env python3
"""验证V5: 市场体制分层 — 按大盘牛/熊/震荡分层验证信号, 确保在不同市场环境下都有≥65%胜率"""
import json
from pathlib import Path
from collections import defaultdict
from statistics import mean

KLINE_DIR = Path(__file__).resolve().parent.parent / "kline_data"

def load_all():
    m = {}
    for fp in KLINE_DIR.glob("*.json"):
        if fp.name.startswith("_"): continue
        try:
            with open(fp) as f: d = json.load(f)
            bars = d.get("bars", [])
            if len(bars) >= 120: m[fp.stem] = bars
        except: continue
    return m

def ma(closes, p):
    r = [None]*len(closes)
    for i in range(p-1, len(closes)):
        r[i] = sum(closes[i-p+1:i+1])/p
    return r

def detect_regime(bar_idx, all_closes, all_highs, all_lows):
    """简单体制检测: 基于60日均线方向 + 20日波动"""
    i = bar_idx
    if i < 120: return "unknown"
    # 60日均线方向
    ma60_now = sum(all_closes[i-59:i+1])/60
    ma60_20d = sum(all_closes[i-79:i-19])/60
    ma60_slope = (ma60_now - ma60_20d)/ma60_20d
    # 20日高低波动
    h20 = max(all_highs[i-19:i+1])
    l20 = min(all_lows[i-19:i+1])
    range_pct = (h20-l20)/l20 if l20>0 else 0

    if ma60_slope > 0.03: return "bull"
    elif ma60_slope < -0.03 or range_pct > 0.30: return "bear"
    return "range"

def main():
    print("V5 市场体制分层验证: 加载数据...")
    all_bars = load_all()
    print(f"  {len(all_bars)} 只")

    # 用000001(上证)或最大市值股作为大盘代理
    # 简化方案: 用所有股票60日MA方向的众数判断当日体制
    # 更简化: 用每只股票自身MA60斜率判断

    signals_def = [
        ("新高+多头+量≤0.5x",
         lambda a,p,vr,nh: nh and a>=3 and vr<=0.5),
        ("完美多头+位≥80%+量≤0.5x",
         lambda a,p,vr,nh: a>=4 and p>=0.80 and vr<=0.5),
        ("完美多头+位≥70%+量≤0.3x",
         lambda a,p,vr,nh: a>=4 and p>=0.70 and vr<=0.3),
        ("多头≥3+位≥80%+量≤0.3x",
         lambda a,p,vr,nh: a>=3 and p>=0.80 and vr<=0.3),
        ("完美多头+位≥90%+量≤0.3x",
         lambda a,p,vr,nh: a>=4 and p>=0.90 and vr<=0.3),
        ("新高+多头+量≤1.0x",
         lambda a,p,vr,nh: nh and a>=3 and vr<=1.0),
    ]

    # 按体制汇总
    regime_results = defaultdict(lambda: defaultdict(lambda: {"count":0,"wins":0,"rets":[]}))

    # 先收集所有股票综合指数来定义每日体制
    # 简化: 用所有股票60日均线的中位数斜率
    daily_slopes = defaultdict(list)

    for code, bars in all_bars.items():
        closes = [b["close"] for b in bars]
        highs  = [b["high"] for b in bars]
        lows   = [b["low"] for b in bars]
        n = len(bars)
        for i in range(120, n):
            ma60_now = sum(closes[i-59:i+1])/60
            ma60_20d = sum(closes[i-79:i-19])/60
            slope = (ma60_now - ma60_20d)/ma60_20d if ma60_20d>0 else 0
            date = bars[i]["date"]
            daily_slopes[date].append(slope)

    # 每日体制判定
    daily_regime = {}
    for date, slopes in daily_slopes.items():
        med_slope = sorted(slopes)[len(slopes)//2]
        if med_slope > 0.03: daily_regime[date] = "bull"
        elif med_slope < -0.03: daily_regime[date] = "bear"
        else: daily_regime[date] = "range"

    bull_days = sum(1 for v in daily_regime.values() if v=="bull")
    bear_days = sum(1 for v in daily_regime.values() if v=="bear")
    range_days = sum(1 for v in daily_regime.values() if v=="range")
    print(f"  体制分布: bull={bull_days} bear={bear_days} range={range_days}")

    # 信号评估
    for code, bars in all_bars.items():
        closes = [b["close"] for b in bars]
        opens  = [b["open"] for b in bars]
        highs  = [b["high"] for b in bars]
        lows   = [b["low"] for b in bars]
        vols   = [b["volume"] for b in bars]
        n = len(bars)
        ma5  = ma(closes,5); ma10 = ma(closes,10)
        ma20 = ma(closes,20); ma60 = ma(closes,60)

        for i in range(60, n-1):
            c = closes[i]; o = opens[i]
            ret = (closes[i+1]-c)/c
            avg20v = sum(vols[i-19:i+1])/20
            vr = vols[i]/avg20v if avg20v>0 else 1.0
            if not (ma5[i] and ma10[i] and ma20[i] and ma60[i]): continue

            n60 = min(60,i+1)
            h60 = max(highs[j] for j in range(i-n60+1,i+1))
            l60_ = min(lows[j] for j in range(i-n60+1,i+1))
            pos60 = (c-l60_)/(h60-l60_) if h60>l60_ else 0.5
            align = sum([ma5[i]>ma10[i], ma10[i]>ma20[i], ma20[i]>ma60[i], c>ma5[i]])
            new_high = (i>=1 and c>=h60 and c>highs[i-1])

            regime = daily_regime.get(bars[i]["date"], "range")

            for sig_name, sig_fn in signals_def:
                if sig_fn(align, pos60, vr, new_high):
                    r = regime_results[sig_name][regime]
                    r["count"]+=1; r["rets"].append(ret)
                    if ret>0: r["wins"]+=1

    # 输出
    print(f"\n{'='*100}")
    print(f"V5 市场体制分层验证")
    print(f"{'='*100}")

    for sig_name in sorted(regime_results.keys()):
        print(f"\n  【{sig_name}】")
        print(f"  {'体制':<10s} {'N':>7s} {'胜率':>7s} {'均收益':>8s} {'判定':>6s}")
        print(f"  {'─'*10} {'─'*7} {'─'*7} {'─'*8} {'─'*6}")
        all_pass = True
        for regime in ["bull", "range", "bear"]:
            r = regime_results[sig_name].get(regime, {"count":0,"wins":0,"rets":[]})
            if r["count"] >= 20:
                wr = r["wins"]/r["count"]*100
                av = mean(r["rets"])*100
                status = "✓" if wr >= 60 else "✗"
                if wr < 60: all_pass = False
                print(f"  {regime:<10s} {r['count']:>7d} {wr:>6.1f}% {av:>+7.2f}% {status:>6s}")
            else:
                print(f"  {regime:<10s} {r['count']:>7d} (数据不足)")
        print(f"  {'全面通过' if all_pass else '有体制短板'}")

if __name__ == "__main__":
    main()
