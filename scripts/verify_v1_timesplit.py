#!/usr/bin/env python3
"""验证V1: 时间分割 — 前80%训练, 后20%验证, 验证Agent A/B/C的Top信号"""
import json, sys
from pathlib import Path
from collections import defaultdict
from statistics import mean, median

KLINE_DIR = Path(__file__).resolve().parent.parent / "kline_data"

# 定义需要验证的核心信号
SIGNALS = {
    # Agent A 代表信号
    "A:完美多头+位≥80%+量≤0.5x": lambda c,o,h,l,vr,pos60,align,ma5_ok,new_high: align==4 and pos60>=0.80 and vr<=0.5,
    "A:完美多头+位≥70%+量≤0.3x": lambda c,o,h,l,vr,pos60,align,ma5_ok,new_high: align==4 and pos60>=0.70 and vr<=0.3,
    "A:完美多头+量≤0.5x不限位": lambda c,o,h,l,vr,pos60,align,ma5_ok,new_high: align==4 and vr<=0.5,
    "A:多头≥3+位≥80%+量≤0.3x": lambda c,o,h,l,vr,pos60,align,ma5_ok,new_high: align>=3 and pos60>=0.80 and vr<=0.3,
    # Agent B 代表信号
    "B:3日跌>10%+反转>1%+位<40%+缩量": lambda c,o,h,l,vr,pos60,align,ma5_ok,new_high: False,  # 需额外参数, 单独处理
    # Agent C 代表信号
    "C:新高+多头+量≤0.5x": lambda c,o,h,l,vr,pos60,align,ma5_ok,new_high: new_high and align>=3 and vr<=0.5,
    "C:MA5倾>5%+缩量+位≥70%": lambda c,o,h,l,vr,pos60,align,ma5_ok,new_high: False,  # 需MA斜率
    "C:连阳≥2+高位≥85%+缩量": lambda c,o,h,l,vr,pos60,align,ma5_ok,new_high: False,  # 需连阳
}

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

def main():
    all_bars = load_all()
    print(f"V1 时间分割验证: {len(all_bars)} 只")

    results = defaultdict(lambda: {"train_n":0,"train_w":0,"test_n":0,"test_w":0,
                                    "train_r":[],"test_r":[]})

    for code, bars in all_bars.items():
        closes = [b["close"] for b in bars]
        opens = [b["open"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        vols = [b["volume"] for b in bars]
        n = len(bars)
        ma5 = ma(closes,5); ma10 = ma(closes,10)
        ma20 = ma(closes,20); ma60 = ma(closes,60)

        # 时间分割点: 前80%作为训练, 后20%作为验证
        split = int(n * 0.80)

        for i in range(60, n-1):
            c = closes[i]; o = opens[i]; h = highs[i]; l = lows[i]
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
            ma5_slope = (ma5[i]-ma5[i-4])/ma5[i-4] if i>=4 and ma5[i] and ma5[i-4] else 0
            chg_3d = (c-closes[i-2])/closes[i-2] if i>=2 else 0
            streak = 0
            for j in range(i, max(i-5,-1),-1):
                if closes[j]>opens[j]: streak+=1
                else: break

            is_train = (i < split)

            # Agent A 信号
            for sig_name, sig_fn in [
                ("A:完美多头+位≥80%+量≤0.5x",
                 align==4 and pos60>=0.80 and vr<=0.5),
                ("A:完美多头+位≥70%+量≤0.3x",
                 align==4 and pos60>=0.70 and vr<=0.3),
                ("A:完美多头+量≤0.5x不限位",
                 align==4 and vr<=0.5),
                ("A:多头≥3+位≥80%+量≤0.3x",
                 align>=3 and pos60>=0.80 and vr<=0.3),
                # Agent B 信号
                ("B:3日跌>10%+反转>1%+位<40%+缩量",
                 chg_3d<=-0.10 and (c-o)/o>=0.01 and pos60<0.40 and vr<0.5),
                # Agent C 信号
                ("C:新高+多头+量≤0.5x",
                 new_high and align>=3 and vr<=0.5),
                ("C:MA5倾>5%+缩量+位≥70%",
                 ma5_slope>0.05 and vr<0.5 and pos60>=0.70 and not new_high),
                ("C:连阳≥2+高位≥85%+缩量",
                 streak>=2 and pos60>=0.85 and vr<0.5),
            ]:
                if sig_fn:
                    r = results[sig_name]
                    if is_train:
                        r["train_n"] += 1; r["train_r"].append(ret)
                        if ret>0: r["train_w"] += 1
                    else:
                        r["test_n"] += 1; r["test_r"].append(ret)
                        if ret>0: r["test_w"] += 1

    # 输出
    print(f"\n{'='*100}")
    print(f"V1 时间分割验证结果 (前80%训练 | 后20%验证)")
    print(f"{'='*100}")
    print(f"  {'信号':<40s} {'训练N':>7s} {'训练WR':>8s} {'验证N':>7s} {'验证WR':>8s} {'ΔWR':>7s} {'判定':>6s}")
    print(f"  {'─'*40} {'─'*7} {'─'*8} {'─'*7} {'─'*8} {'─'*7} {'─'*6}")

    passed = 0; failed = 0
    for name in sorted(results.keys()):
        r = results[name]
        if r["train_n"] < 30 or r["test_n"] < 30: continue
        tr_wr = r["train_w"]/r["train_n"]*100
        ts_wr = r["test_w"]/r["test_n"]*100
        tr_av = mean(r["train_r"])*100
        ts_av = mean(r["test_r"])*100
        delta = ts_wr - tr_wr
        status = "PASS" if ts_wr >= 60 else "FAIL"
        if status == "PASS": passed += 1
        else: failed += 1
        print(f"  {name:<40s} {r['train_n']:>7d} {tr_wr:>7.1f}% {r['test_n']:>7d} {ts_wr:>7.1f}% {delta:>+6.1f}% {status:>6s}")

    print(f"\n  通过: {passed} | 未通过: {failed}")
    print(f"  标准: 验证集胜率 ≥ 60%")

if __name__ == "__main__":
    main()
