#!/usr/bin/env python3
"""Agent E: 多周期动量共振 — 短期(5日)+中期(20日)动量信号组合, 找最佳买入共振点
聚焦: 短动×中动×MA×量能 → 多周期共振买入时机, 胜率≥65%
"""
import json, random
from pathlib import Path
from collections import defaultdict
from statistics import mean, median

KLINE_DIR = Path(__file__).resolve().parent.parent / "kline_data"
random.seed(8080)

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
    results = defaultdict(lambda: {"count":0,"wins":0,"rets":[]})
    total_k = 0

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
            total_k += 1
            c = closes[i]; o = opens[i]
            ret = (closes[i+1]-c)/c
            avg20v = sum(vols[i-19:i+1])/20
            vr = vols[i]/avg20v if avg20v>0 else 1.0

            # ── MA确认 ──
            if not (ma5[i] and ma10[i] and ma20[i] and ma60[i]): continue
            perfect_align = (ma5[i]>ma10[i]>ma20[i]>ma60[i] and c>ma5[i])
            align_3 = sum([ma5[i]>ma10[i], ma10[i]>ma20[i], ma20[i]>ma60[i], c>ma5[i]]) >= 3

            # ── 多周期动量 ──
            ret_5d  = (c-closes[i-4])/closes[i-4] if i>=4 else 0    # 5日涨跌幅
            ret_10d = (c-closes[i-9])/closes[i-9] if i>=9 else 0    # 10日涨跌幅
            ret_20d = (c-closes[i-19])/closes[i-19] if i>=19 else 0  # 20日涨跌幅

            # 短动加速: 5日 > 10日均速
            short_accel = (ret_5d/5) > (ret_10d/10) if ret_10d != 0 else (ret_5d > 0)

            # ── 60日位置 ──
            n60 = min(60,i+1)
            h60 = max(highs[j] for j in range(i-n60+1,i+1))
            l60_ = min(lows[j] for j in range(i-n60+1,i+1))
            pos60 = (c-l60_)/(h60-l60_) if h60>l60_ else 0.5
            new_high = (i>=1 and c>=h60 and c>highs[i-1])

            # ═══════════════════════════════════
            # 系列A: 短中动量共振 × MA × 量能 (核心买入信号)
            # ═══════════════════════════════════
            for st_min, st_name in [(0.03, "5日>3%"), (0.05, "5日>5%"), (0.00, "5日>0%")]:
                if ret_5d >= st_min:
                    for mt_min, mt_name in [(0.05, "20日>5%"), (0.03, "20日>3%"), (0.00, "20日>0%")]:
                        if ret_20d >= mt_min:
                            for v_max in [0.3, 0.5, 0.7, 1.0]:
                                for ma_ok, ma_name in [(perfect_align, "完美多头"), (align_3, "多头≥3")]:
                                    if ma_ok and vr <= v_max:
                                        k = f"{st_name}+{mt_name}+{ma_name}+量≤{v_max:.1f}x"
                                        r = results[k]; r["count"]+=1; r["rets"].append(ret)
                                        if ret>0: r["wins"]+=1

            # ═══════════════════════════════════
            # 系列B: 短动加速 × MA (加速买入)
            # ═══════════════════════════════════
            if short_accel and ret_5d > 0:
                for v_max in [0.3, 0.5, 0.7]:
                    for ma_ok, ma_name in [(perfect_align, "完美多头"), (align_3, "多头≥3")]:
                        if ma_ok and vr <= v_max and pos60 >= 0.50:
                            k = f"短动加速+{ma_name}+位≥50%+量≤{v_max:.1f}x"
                            r = results[k]; r["count"]+=1; r["rets"].append(ret)
                            if ret>0: r["wins"]+=1

            # ═══════════════════════════════════
            # 系列C: 中期趋势确认后的短期回调买入 (回踩买点)
            # ═══════════════════════════════════
            if ret_20d >= 0.05 and -0.03 <= ret_5d <= 0.02:
                for v_max in [0.3, 0.5, 0.7]:
                    for ma_ok, ma_name in [(perfect_align, "完美多头"), (align_3, "多头≥3")]:
                        if ma_ok and vr <= v_max and pos60 >= 0.50:
                            k = f"20日>5%+5日回踩+{ma_name}+量≤{v_max:.1f}x"
                            r = results[k]; r["count"]+=1; r["rets"].append(ret)
                            if ret>0: r["wins"]+=1

            # ═══════════════════════════════════
            # 系列D: 中长动量背离后反转 (超跌+中期向好)
            # ═══════════════════════════════════
            if ret_20d >= 0.00 and ret_5d <= -0.03:
                for v_max in [0.3, 0.5, 0.7]:
                    if vr <= v_max and pos60 >= 0.30 and c > o:
                        k = f"20日>0%+5日跌>3%+阳线+量≤{v_max:.1f}x"
                        r = results[k]; r["count"]+=1; r["rets"].append(ret)
                        if ret>0: r["wins"]+=1

            # ═══════════════════════════════════
            # 系列E: 新高+动量持续 (突破买入)
            # ═══════════════════════════════════
            if new_high and ret_20d >= 0.03:
                for v_max in [0.3, 0.5, 0.7, 1.0]:
                    if vr <= v_max and perfect_align:
                        k = f"新高+20日>3%+完美多头+量≤{v_max:.1f}x"
                        r = results[k]; r["count"]+=1; r["rets"].append(ret)
                        if ret>0: r["wins"]+=1

    print(f"Agent E 处理: {total_k} 根K线, {len(results)} 个信号变体")
    return results

def report(results):
    scored = []
    for k,r in results.items():
        if r["count"]>=30:
            wr = r["wins"]/r["count"]*100
            av = mean(r["rets"])*100
            md = median(r["rets"])*100
            scored.append((k, r["count"], r["wins"], wr, av, md))
    scored.sort(key=lambda x:-x[3])

    above65 = [x for x in scored if x[3]>=65]
    print(f"\nAgent E 结果: ≥65%: {len(above65)}个")
    print(f"\n{'='*90}")
    print(f"  {'信号':<55s} {'N':>6s} {'胜率':>7s} {'均%':>8s} {'中位%':>8s}")
    print(f"  {'─'*55} {'─'*6} {'─'*7} {'─'*8} {'─'*8}")
    for name, cnt, wins, wr, av, md in above65:
        print(f"  ★ {name:<53s} {cnt:>6d} {wr:>6.1f}% {av:>+7.2f}% {md:>+7.2f}%")
    print(f"\n  Top 5:")
    for name, cnt, wins, wr, av, md in scored[:5]:
        print(f"    {name:<51s} {cnt:>6d} {wr:>6.1f}%")
    return {"above65": above65, "all_scored": scored}

def main():
    print("Agent E: 多周期动量共振分析")
    all_bars = load_all()
    print(f"  加载 {len(all_bars)} 只")
    results = analyze(all_bars)
    output = report(results)
    out_path = Path(__file__).resolve().parent.parent / "research_data/backtest/agent_e_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"above65": [(n,c,w,wr,av,md) for n,c,w,wr,av,md in output["above65"]]},
                  f, ensure_ascii=False, indent=2)
    print(f"\n  ✓ 保存: {out_path}")

if __name__ == "__main__":
    main()
