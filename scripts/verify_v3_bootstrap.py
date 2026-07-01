#!/usr/bin/env python3
"""验证V3: Bootstrap稳健性 — 100次随机重采样, 验证胜率稳定性"""
import json, sys, random
from pathlib import Path
from collections import defaultdict
from statistics import mean, stdev, median

KLINE_DIR = Path(__file__).resolve().parent.parent / "kline_data"
BOOTSTRAP_ROUNDS = 100
random.seed(12345)

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

def collect_signals(all_bars):
    """收集所有信号的回报数据"""
    signals = defaultdict(list)

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

            # 核心信号
            checks = [
                ("多头4+位≥80%+量≤0.5x", align==4 and pos60>=0.80 and vr<=0.5),
                ("多头4+位≥70%+量≤0.3x", align==4 and pos60>=0.70 and vr<=0.3),
                ("多头4+位≥90%+量≤0.3x", align==4 and pos60>=0.90 and vr<=0.3),
                ("新高+多头+量≤0.5x", new_high and align>=3 and vr<=0.5),
                ("新高+多头+量≤1.0x", new_high and align>=3 and vr<=1.0),
                ("多头4+量≤0.5x不限位", align==4 and vr<=0.5),
            ]
            for name, ok in checks:
                if ok:
                    signals[name].append(ret)

    return signals

def bootstrap(signals):
    results = {}
    for sig_name, returns in signals.items():
        n_orig = len(returns)
        if n_orig < 50:
            continue
        wr_orig = sum(1 for r in returns if r > 0) / n_orig * 100
        wr_boot = []
        for _ in range(BOOTSTRAP_ROUNDS):
            sample = random.choices(returns, k=n_orig)
            wr = sum(1 for r in sample if r > 0) / n_orig * 100
            wr_boot.append(wr)
        results[sig_name] = {
            "n": n_orig, "wr_orig": wr_orig,
            "wr_mean": mean(wr_boot), "wr_std": stdev(wr_boot),
            "wr_p5": sorted(wr_boot)[4], "wr_p95": sorted(wr_boot)[94],
            "returns": returns,
        }
    return results

def main():
    print("V3 Bootstrap验证: 加载数据...")
    all_bars = load_all()
    print(f"  {len(all_bars)} 只")

    print("收集信号回报...")
    signals = collect_signals(all_bars)
    print(f"  {len(signals)} 个信号")

    print(f"Bootstrap {BOOTSTRAP_ROUNDS}轮重采样...")
    results = bootstrap(signals)

    print(f"\n{'='*100}")
    print(f"V3 Bootstrap稳健性验证 ({BOOTSTRAP_ROUNDS}轮)")
    print(f"{'='*100}")
    print(f"  {'信号':<35s} {'N':>6s} {'原始WR':>7s} {'Bootstrap均值WR':>13s} {'标准差':>7s} {'95%CI':>15s} {'判定':>6s}")
    print(f"  {'─'*35} {'─'*6} {'─'*7} {'─'*13} {'─'*7} {'─'*15} {'─'*6}")

    sorted_names = sorted(results.keys(), key=lambda x: -results[x]["wr_orig"])
    robust = 0; unstable = 0
    for name in sorted_names:
        r = results[name]
        ci_lo = r["wr_p5"]; ci_hi = r["wr_p95"]
        status = "ROBUST" if ci_lo >= 60 else ("OK" if ci_lo >= 55 else "WEAK")
        if status == "ROBUST": robust += 1
        elif status == "WEAK": unstable += 1
        print(f"  {name:<35s} {r['n']:>6d} {r['wr_orig']:>6.1f}% "
              f"{r['wr_mean']:>8.1f}%±{r['wr_std']:>4.1f}% "
              f"[{ci_lo:.1f}%, {ci_hi:.1f}%] {status:>6s}")

    print(f"\n  ROBUST(95%CI≥60%): {robust} | OK(≥55%): 其他 | WEAK(<55%): {unstable}")

if __name__ == "__main__":
    main()
