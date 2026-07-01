#!/usr/bin/env python3
"""Agent B: 超跌反转+底部形态分析 — 找胜率≥65%的信号"""
import json, sys
from pathlib import Path
from collections import defaultdict
from statistics import mean, median
import random

KLINE_DIR = Path(__file__).resolve().parent.parent / "kline_data"
random.seed(99)

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
    """测试 超跌×反弹力度×量价确认 的三维网格"""
    results = defaultdict(lambda: {"count":0,"wins":0,"rets":[]})
    total_k = 0
    for code, bars in all_bars.items():
        closes = [b["close"] for b in bars]
        opens = [b["open"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        vols = [b["volume"] for b in bars]
        n = len(bars)
        ma5 = ma(closes,5); ma20 = ma(closes,20)
        for i in range(60, n-1):
            total_k += 1
            c = closes[i]; o = opens[i]; h = highs[i]; l = lows[i]
            ret = (closes[i+1]-c)/c
            avg20v = sum(vols[i-19:i+1])/20
            vr = vols[i]/avg20v if avg20v>0 else 1.0
            n60 = min(60,i+1)
            h60 = max(highs[j] for j in range(i-n60+1,i+1))
            l60_ = min(lows[j] for j in range(i-n60+1,i+1))
            pos60 = (c - l60_)/(h60 - l60_) if h60>l60_ else 0.5

            # 跌幅计算
            for lookback, lb_name in [(3, "3d"), (5, "5d"), (10, "10d")]:
                if i < lookback: continue
                chg = (c - closes[i-lookback])/closes[i-lookback]

                # 超跌阈值
                for chg_max in [-0.05, -0.08, -0.10, -0.12, -0.15]:
                    if chg <= chg_max:
                        # 今日反弹强度
                        for reb_min, reb_name in [(0.0, "收阳"), (0.01, ">1%"), (0.02, ">2%"), (0.03, ">3%")]:
                            reb = (c-o)/o
                            if reb >= reb_min:
                                # 量价确认
                                for v_cond, v_name in [(vr<0.5, "缩量<0.5x"), (vr>1.5, "放量>1.5x"),
                                                        (1.2<=vr<=2.0, "温和放量"), (True, "不限量")]:
                                    # 位置确认
                                    for p_max, p_name in [(0.20, "极低位<20%"), (0.30, "低位<30%"), (0.40, "中低<40%")]:
                                        if pos60 <= p_max and v_cond:
                                            k = f"{lb_name}跌>{abs(int(chg_max*100))}%+{reb_name}+{p_name}+{v_name}"
                                            r = results[k]; r["count"]+=1; r["rets"].append(ret)
                                            if ret>0: r["wins"]+=1

            # MA金叉+超跌
            if ma5[i] and ma5[i-1] and i>=5:
                chg_5d = (c - closes[i-4])/closes[i-4]
                if chg_5d <= -0.05 and ma5[i-1]<=ma20[i-1] and ma5[i]>ma20[i] and c>o:
                    for p_max in [0.20, 0.25, 0.30, 0.40]:
                        if pos60 <= p_max:
                            k = f"5日跌>5%+MA5金叉MA20+位<{int(p_max*100)}%"
                            r = results[k]; r["count"]+=1; r["rets"].append(ret)
                            if ret>0: r["wins"]+=1

            # 锤子线+超跌
            body = abs(c-o)
            low_shadow = min(c,o)-l
            if body>0 and low_shadow>body*2:
                for chg_max in [-0.05, -0.08, -0.10]:
                    chg_3d = (c-closes[i-2])/closes[i-2] if i>=2 else 0
                    if chg_3d <= chg_max:
                        for p_max in [0.20, 0.25, 0.30]:
                            if pos60 <= p_max:
                                for v_cond, v_name in [(vr<0.5, "缩量"), (True, "")]:
                                    if v_cond:
                                        k = f"3日跌>{abs(int(chg_max*100))}%+锤子线+位<{int(p_max*100)}%+{v_name}"
                                        r = results[k]; r["count"]+=1; r["rets"].append(ret)
                                        if ret>0: r["wins"]+=1

            # 60日低位+成交量萎缩至极+收阳
            if pos60 <= 0.15 and vr <= 0.3 and c > o:
                k = f"极低位<15%+地量<0.3x+收阳"
                r = results[k]; r["count"]+=1; r["rets"].append(ret)
                if ret>0: r["wins"]+=1

    print(f"Agent B 处理: {total_k} 根K线, {len(results)} 个信号变体")
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
    print(f"\nAgent B 结果: ≥65%: {len(above65)}个, ≥60%: {len(above60)}个")
    print(f"\n{'='*90}")
    print(f"  {'信号':<55s} {'N':>6s} {'胜率':>7s} {'均%':>8s} {'中位%':>8s}")
    print(f"  {'─'*55} {'─'*6} {'─'*7} {'─'*8} {'─'*8}")
    for name, cnt, wins, wr, av, md in above65:
        print(f"  ★ {name:<53s} {cnt:>6d} {wr:>6.1f}% {av:>+7.2f}% {md:>+7.2f}%")
    for name, cnt, wins, wr, av, md in above60:
        print(f"  ☆ {name:<53s} {cnt:>6d} {wr:>6.1f}% {av:>+7.2f}% {md:>+7.2f}%")

    # Top 10
    print(f"\n  Top 10:")
    for name, cnt, wins, wr, av, md in scored[:10]:
        print(f"    {name:<51s} {cnt:>6d} {wr:>6.1f}%")
    return {"above65": above65, "above60": above60, "all_scored": scored}

def main():
    print("Agent B: 超跌反转+底部形态分析")
    all_bars = load_all()
    print(f"  加载 {len(all_bars)} 只")
    results = analyze(all_bars)
    output = report(results)
    import json as j
    out_path = Path(__file__).resolve().parent.parent / "research_data/backtest/agent_b_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        j.dump({"above65": [(n,c,w,wr,av,md) for n,c,w,wr,av,md in output["above65"]]}, f, ensure_ascii=False, indent=2)
    print(f"\n  ✓ 保存: {out_path}")
    return output

if __name__ == "__main__":
    main()
