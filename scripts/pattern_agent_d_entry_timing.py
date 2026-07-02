#!/usr/bin/env python3
"""Agent D: 日内买入时机 — 分析K线日内结构(开盘/收盘/最高/最低)与次日上涨的关系
聚焦: 缺口方向×日内位置×量能 → 找最佳买入时机信号, 胜率≥65%
"""
import json
import random
from pathlib import Path
from collections import defaultdict
from statistics import mean, median

KLINE_DIR = Path(__file__).resolve().parent.parent / "kline_data"
random.seed(2026)

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
    """测试 日内结构 × MA趋势 × 量能 三维网格"""
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
            c = closes[i]; o = opens[i]; h = highs[i]; l = lows[i]
            ret = (closes[i+1]-c)/c
            avg20v = sum(vols[i-19:i+1])/20
            vr = vols[i]/avg20v if avg20v>0 else 1.0

            # ── MA趋势确认 ──
            if not (ma5[i] and ma10[i] and ma20[i] and ma60[i]): continue
            perfect_align = (ma5[i]>ma10[i]>ma20[i]>ma60[i] and c>ma5[i])
            align_3 = sum([ma5[i]>ma10[i], ma10[i]>ma20[i], ma20[i]>ma60[i], c>ma5[i]]) >= 3

            # ── 日内结构特征 ──
            gap_pct = (o - closes[i-1])/closes[i-1] if i>=1 else 0  # 今开 vs 昨收
            intra_range = (h-l)/o if o>0 else 0  # 日内振幅
            close_pos = (c-l)/(h-l) if h>l else 0.5  # 收盘在日内位置
            is_green = (c > o)  # 真阳线
            is_red   = (c < o)  # 真阴线
            body_pct = abs(c-o)/o if o>0 else 0  # 实体大小
            upper_shadow = (h-max(c,o))/o if o>0 else 0  # 上影线
            lower_shadow = (min(c,o)-l)/o if o>0 else 0  # 下影线

            # ── 60日位置 ──
            n60 = min(60,i+1)
            h60 = max(highs[j] for j in range(i-n60+1,i+1))
            l60_ = min(lows[j] for j in range(i-n60+1,i+1))
            pos60 = (c-l60_)/(h60-l60_) if h60>l60_ else 0.5

            # ═══════════════════════════════════
            # 系列A: 缺口方向 × 量能 × MA (买入时机核心)
            # ═══════════════════════════════════
            for gap_min, gap_max, gap_name in [
                (-0.01, 0.01, "平开"),
                (0.00, 0.02, "小幅高开"),
                (0.02, 0.05, "中幅高开"),
                (-0.02, 0.00, "小幅低开"),
                (-0.05, -0.02, "中幅低开"),
            ]:
                if gap_min <= gap_pct < gap_max:
                    for v_max in [0.3, 0.5, 0.7, 1.0]:
                        for ma_ok, ma_name in [(perfect_align, "完美多头"),
                                                (align_3, "多头≥3")]:
                            if ma_ok and vr <= v_max:
                                k = f"{gap_name}+{ma_name}+量≤{v_max:.1f}x"
                                r = results[k]; r["count"]+=1; r["rets"].append(ret)
                                if ret>0: r["wins"]+=1

            # ═══════════════════════════════════
            # 系列B: 日内收盘位置 × 阳线 × 量能
            # ═══════════════════════════════════
            if is_green:
                for pos_min, pos_name in [(0.70, "光头阳"), (0.85, "强光头阳"), (0.50, "偏强阳")]:
                    if close_pos >= pos_min:
                        for v_max in [0.3, 0.5, 0.7]:
                            for ma_ok, ma_name in [(perfect_align, "完美多头"), (align_3, "多头≥3")]:
                                if ma_ok and vr <= v_max:
                                    k = f"{pos_name}+{ma_name}+量≤{v_max:.1f}x"
                                    r = results[k]; r["count"]+=1; r["rets"].append(ret)
                                    if ret>0: r["wins"]+=1

            # ═══════════════════════════════════
            # 系列C: 下影线支撑 × 低开反转
            # ═══════════════════════════════════
            if gap_pct < 0 and is_green and lower_shadow > 0.01:
                for v_max in [0.3, 0.5, 0.7]:
                    if vr <= v_max and pos60 >= 0.40:
                        k = f"低开反转+位≥40%+量≤{v_max:.1f}x"
                        r = results[k]; r["count"]+=1; r["rets"].append(ret)
                        if ret>0: r["wins"]+=1

            # ═══════════════════════════════════
            # 系列D: 缩量小实体 (蓄力形态)
            # ═══════════════════════════════════
            if body_pct < 0.02 and perfect_align:
                for v_max in [0.3, 0.5, 0.7]:
                    if vr <= v_max:
                        k = f"缩量小实体+完美多头+量≤{v_max:.1f}x"
                        r = results[k]; r["count"]+=1; r["rets"].append(ret)
                        if ret>0: r["wins"]+=1

            # ═══════════════════════════════════
            # 系列E: 突破日内高点 (盘中追涨信号)
            # ═══════════════════════════════════
            if close_pos >= 0.90 and is_green and c >= h*0.99:
                for v_max in [0.3, 0.5, 0.7]:
                    for ma_ok, ma_name in [(perfect_align, "完美多头"), (align_3, "多头≥3")]:
                        if ma_ok and vr <= v_max:
                            k = f"收于日高+{ma_name}+量≤{v_max:.1f}x"
                            r = results[k]; r["count"]+=1; r["rets"].append(ret)
                            if ret>0: r["wins"]+=1

    print(f"Agent D 处理: {total_k} 根K线, {len(results)} 个信号变体")
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
    print(f"\nAgent D 结果: ≥65%: {len(above65)}个")
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
    print("Agent D: 日内买入时机分析")
    all_bars = load_all()
    print(f"  加载 {len(all_bars)} 只")
    results = analyze(all_bars)
    output = report(results)
    out_path = Path(__file__).resolve().parent.parent / "research_data/backtest/agent_d_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"above65": [(n,c,w,wr,av,md) for n,c,w,wr,av,md in output["above65"]]},
                  f, ensure_ascii=False, indent=2)
    print(f"\n  ✓ 保存: {out_path}")

if __name__ == "__main__":
    import random
    main()
