#!/usr/bin/env python3
"""验证V2: 跨板块验证 — 主板 vs 创业板/科创板, 验证信号在不同板块的一致性"""
import json, sys
from pathlib import Path
from collections import defaultdict
from statistics import mean, median

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

def board(code):
    if code.startswith("60") or code.startswith("68"): return "沪市"
    if code.startswith("00"): return "深主板"
    if code.startswith("30"): return "创业板"
    if code.startswith("83") or code.startswith("87") or code.startswith("92"): return "北交所"
    return "其他"

def ma(closes, p):
    r = [None]*len(closes)
    for i in range(p-1, len(closes)):
        r[i] = sum(closes[i-p+1:i+1])/p
    return r

def main():
    all_bars = load_all()
    print(f"V2 跨板块验证: {len(all_bars)} 只")

    # 按板块分组
    board_stocks = defaultdict(list)
    for code in all_bars:
        board_stocks[board(code)].append(code)
    print(f"  沪市: {len(board_stocks['沪市'])} 深主板: {len(board_stocks['深主板'])} "
          f"创业板: {len(board_stocks['创业板'])} 北交所: {len(board_stocks['北交所'])}")

    # 测试3个核心信号
    core_signals = [
        ("多头4+位≥80%+量≤0.5x",
         lambda align,pos,vr,nh: align==4 and pos>=0.80 and vr<=0.5),
        ("多头4+位≥70%+量≤0.3x",
         lambda align,pos,vr,nh: align==4 and pos>=0.70 and vr<=0.3),
        ("新高+多头+量≤0.5x",
         lambda align,pos,vr,nh: nh and align>=3 and vr<=0.5),
    ]

    results = {}
    for sig_name, sig_fn in core_signals:
        board_res = defaultdict(lambda: {"n":0,"wins":0,"rets":[]})
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
                new_high = (i>=1 and c>=h60 and c>highs[i-1])
                if sig_fn(align, pos60, vr, new_high):
                    brd = board(code)
                    r = board_res[brd]; r["n"]+=1; r["rets"].append(ret)
                    if ret>0: r["wins"]+=1

        results[sig_name] = dict(board_res)

    # 输出
    print(f"\n{'='*100}")
    print(f"V2 跨板块验证结果")
    print(f"{'='*100}")
    for sig_name, board_res in results.items():
        print(f"\n  【{sig_name}】")
        print(f"  {'板块':<10s} {'N':>7s} {'胜率':>7s} {'均收益':>8s} {'中位':>8s} {'一致性':>8s}")
        print(f"  {'─'*10} {'─'*7} {'─'*7} {'─'*8} {'─'*8} {'─'*8}")
        wrs = []
        for brd_name in ["沪市", "深主板", "创业板", "北交所"]:
            r = board_res.get(brd_name, {"n":0,"w":0,"rets":[]})
            if r["n"] >= 20:
                wr = r["wins"]/r["n"]*100
                av = mean(r["rets"])*100
                md = median(r["rets"])*100
                wrs.append(wr)
                status = "✓" if wr>=60 else "✗"
                print(f"  {brd_name:<10s} {r['n']:>7d} {wr:>6.1f}% {av:>+7.2f}% {md:>+7.2f}% {status:>8s}")
            else:
                print(f"  {brd_name:<10s} {r['n']:>7d} (数据不足)")
        if wrs:
            wr_range = max(wrs) - min(wrs)
            avg_wr = sum(wrs)/len(wrs)
            print(f"  {'─'*10} {'─'*7} {'─'*7} {'─'*8} {'─'*8} {'─'*8}")
            print(f"  {'均值':<10s} {'':>7s} {avg_wr:>6.1f}%  极差: {wr_range:.1f}% "
                  f"{'稳定' if wr_range<15 else '有分化'}")

if __name__ == "__main__":
    main()
