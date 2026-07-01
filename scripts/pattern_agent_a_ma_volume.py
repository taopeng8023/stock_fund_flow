#!/usr/bin/env python3
"""Agent A: MA趋势+量价共振模式分析 — 找胜率≥65%的信号"""
import json, sys
from pathlib import Path
from collections import defaultdict
from statistics import mean, median
import random

KLINE_DIR = Path(__file__).resolve().parent.parent / "kline_data"
random.seed(42)

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

def analyze(all_bars):
    """测试 MA排列 × 量比 × 位置 的三维网格"""
    results = defaultdict(lambda: {"count":0,"wins":0,"rets":[]})
    total_k = 0
    for code, bars in all_bars.items():
        closes = [b["close"] for b in bars]
        opens = [b["open"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        vols = [b["volume"] for b in bars]
        n = len(bars)
        ma5 = ma(closes,5); ma10 = ma(closes,10)
        ma20 = ma(closes,20); ma60 = ma(closes,60)
        for i in range(60, n-1):
            total_k += 1
            c = closes[i]; o = opens[i]
            ret = (closes[i+1]-c)/c
            avg20v = sum(vols[i-19:i+1])/20
            vr = vols[i]/avg20v if avg20v>0 else 1.0
            n60 = min(60,i+1)
            h60 = max(highs[j] for j in range(i-n60+1,i+1))
            l60_ = min(lows[j] for j in range(i-n60+1,i+1))
            pos60 = (c - l60_)/(h60 - l60_) if h60>l60_ else 0.5
            align = sum([ma5[i]>ma10[i] if ma5[i] and ma10[i] else False,
                        ma10[i]>ma20[i] if ma10[i] and ma20[i] else False,
                        ma20[i]>ma60[i] if ma20[i] and ma60[i] else False,
                        c>ma5[i] if ma5[i] else False])
            perfect = (align==4)
            ma3 = (align>=3)
            slp = (ma5[i]-ma5[i-4])/ma5[i-4] if i>=4 and ma5[i] and ma5[i-4] else 0
            new_high = (i>=1 and c>=h60 and c>highs[i-1])

            for a_type, a_ok in [("完美多头", perfect), ("多头≥3", ma3)]:
                for pos_min in [0.0, 0.50, 0.60, 0.70, 0.80, 0.90]:
                    for v_max in [0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.70, 1.0]:
                        if a_ok and pos60>=pos_min and vr<=v_max:
                            k = f"{a_type}+位≥{int(pos_min*100)}%+量≤{v_max:.2f}x"
                            r = results[k]; r["count"]+=1; r["rets"].append(ret)
                            if ret>0: r["wins"]+=1

            for pos_min in [0.60, 0.70, 0.80]:
                for v_max in [0.3, 0.5, 0.7]:
                    if slp>0.03 and c>ma5[i] and pos60>=pos_min and vr<=v_max:
                        k = f"MA5向上+站上MA5+位≥{int(pos_min*100)}%+量≤{v_max:.1f}x"
                        r = results[k]; r["count"]+=1; r["rets"].append(ret)
                        if ret>0: r["wins"]+=1

            if new_high and perfect:
                for v_max in [0.3, 0.5, 0.7, 1.0]:
                    if vr<=v_max:
                        k = f"新高+完美多头+量≤{v_max:.1f}x"
                        r = results[k]; r["count"]+=1; r["rets"].append(ret)
                        if ret>0: r["wins"]+=1

    print(f"Agent A 处理: {total_k} 根K线, {len(results)} 个信号变体")
    return results

def report(results):
    scored = []
    for k,r in results.items():
        if r["count"]>=50:
            wr = r["wins"]/r["count"]*100
            av = mean(r["rets"])*100
            md = median(r["rets"])*100
            scored.append((k, r["count"], r["wins"], wr, av, md))
    scored.sort(key=lambda x:-x[3])

    above65 = [x for x in scored if x[3]>=65]
    above60 = [x for x in scored if 60<=x[3]<65]
    print(f"\nAgent A 结果: ≥65%: {len(above65)}个, ≥60%: {len(above60)}个")
    print(f"\n{'='*90}")
    print(f"  {'信号':<50s} {'N':>6s} {'胜率':>7s} {'均%':>8s} {'中位%':>8s}")
    print(f"  {'─'*50} {'─'*6} {'─'*7} {'─'*8} {'─'*8}")
    for name, cnt, wins, wr, av, md in above65:
        print(f"  ★ {name:<48s} {cnt:>6d} {wr:>6.1f}% {av:>+7.2f}% {md:>+7.2f}%")
    for name, cnt, wins, wr, av, md in above60:
        print(f"  ☆ {name:<48s} {cnt:>6d} {wr:>6.1f}% {av:>+7.2f}% {md:>+7.2f}%")
    return {"above65": above65, "above60": above60, "all_scored": scored}

def main():
    print("Agent A: MA趋势+量价共振分析")
    all_bars = load_all()
    print(f"  加载 {len(all_bars)} 只")
    results = analyze(all_bars)
    output = report(results)

    # 保存结果供验证用
    import json as j
    out_path = Path(__file__).resolve().parent.parent / "research_data/backtest/agent_a_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        j.dump({"above65": [(n,c,w,wr,av,md) for n,c,w,wr,av,md in output["above65"]]}, f, ensure_ascii=False, indent=2)
    print(f"\n  ✓ 保存: {out_path}")
    return output

if __name__ == "__main__":
    main()
