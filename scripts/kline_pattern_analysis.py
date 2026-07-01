#!/usr/bin/env python3
"""
K线形态 v3 — 扩展探索"高位多头+缩量"变体 + 更多组合
"""
import json
from pathlib import Path
from collections import defaultdict
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KLINE_DIR = PROJECT_ROOT / "kline_data"

def load_all_bars():
    bars_map = {}
    for fp in KLINE_DIR.glob("*.json"):
        if fp.name.startswith("_"): continue
        try:
            with open(fp) as f: data = json.load(f)
            bars = data.get("bars", [])
            if len(bars) >= 120: bars_map[fp.stem] = bars
        except: continue
    return bars_map

def calc_ma(closes, period):
    r = [None] * len(closes)
    for i in range(period-1, len(closes)):
        r[i] = sum(closes[i-period+1:i+1]) / period
    return r

def test(all_bars):
    results = defaultdict(lambda: {"count": 0, "wins": 0, "returns": []})

    for idx, (code, bars) in enumerate(all_bars.items()):
        if (idx+1) % 1000 == 0: print(f"  进度: {idx+1}/{len(all_bars)}")
        closes = [b["close"] for b in bars]
        opens = [b["open"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        volumes = [b["volume"] for b in bars]
        n = len(bars)
        ma5 = calc_ma(closes, 5)
        ma10 = calc_ma(closes, 10)
        ma20 = calc_ma(closes, 20)
        ma60 = calc_ma(closes, 60)
        for i in range(60, n-1):
            c = closes[i]; o = opens[i]; v = volumes[i]
            ret1 = (closes[i+1]-c)/c
            ret3 = (closes[i+3]-c)/c if i+3<n else None
            ret5 = (closes[i+5]-c)/c if i+5<n else None
            avg20v = sum(volumes[i-19:i+1])/20
            vol_r = v/avg20v if avg20v>0 else 1.0
            n60 = min(60,i+1)
            h60 = max(highs[j] for j in range(i-n60+1,i+1))
            l60 = min(lows[j] for j in range(i-n60+1,i+1))
            pos60 = (c-l60)/(h60-l60) if h60>l60 else 0.5
            align = sum([ma5[i]>ma10[i] if ma5[i] and ma10[i] else False,
                        ma10[i]>ma20[i] if ma10[i] and ma20[i] else False,
                        ma20[i]>ma60[i] if ma20[i] and ma60[i] else False,
                        c>ma5[i] if ma5[i] else False])
            perfect_ma = (align == 4)
            ma_slope = (ma5[i]-ma5[i-4])/ma5[i-4] if i>=4 and ma5[i] and ma5[i-4] else 0
            chg_5d = (c-closes[i-4])/closes[i-4] if i>=4 else 0

            # ============================================================
            # 系列A: "高位多头+缩量" 变体
            # ============================================================
            for p_min in [0.60, 0.70, 0.75, 0.80, 0.85, 0.90]:
                for v_max in [0.3, 0.5, 0.7, 1.0]:
                    if pos60 >= p_min and perfect_ma and vol_r <= v_max:
                        key = f"高位≥{int(p_min*100)}%+完美多头+量≤{v_max:.1f}x"
                        r = results[key]
                        r["count"] += 1; r["returns"].append(ret1)
                        if ret1 > 0: r["wins"] += 1

            # 系列B: 不限位置, 完美多头+缩量
            for v_max in [0.3, 0.5, 0.7]:
                if perfect_ma and vol_r <= v_max:
                    key = f"完美多头+量≤{v_max:.1f}x(不限位)"
                    r = results[key]
                    r["count"] += 1; r["returns"].append(ret1)
                    if ret1 > 0: r["wins"] += 1

            # 系列C: align>=3 (多头排列) + 缩量
            for p_min in [0.50, 0.60, 0.70, 0.80]:
                for v_max in [0.3, 0.5, 0.7]:
                    if pos60 >= p_min and align >= 3 and vol_r <= v_max:
                        key = f"多头排列≥3+高位≥{int(p_min*100)}%+量≤{v_max:.1f}x"
                        r = results[key]
                        r["count"] += 1; r["returns"].append(ret1)
                        if ret1 > 0: r["wins"] += 1

            # 系列D: MA强势+缩量 (不用完美排列)
            for p_min in [0.60, 0.70, 0.80]:
                for v_max in [0.5, 0.7]:
                    if pos60 >= p_min and ma_slope > 0.03 and c > ma5[i] and vol_r <= v_max:
                        key = f"高位≥{int(p_min*100)}%+MA5向上+站上MA5+量≤{v_max:.1f}x"
                        r = results[key]
                        r["count"] += 1; r["returns"].append(ret1)
                        if ret1 > 0: r["wins"] += 1

            # 系列E: 新高系列
            for v_max in [0.5, 1.0, 1.5]:
                if c >= h60 and perfect_ma and vol_r <= v_max:
                    key = f"60日新高+完美多头+量≤{v_max:.1f}x"
                    r = results[key]
                    r["count"] += 1; r["returns"].append(ret1)
                    if ret1 > 0: r["wins"] += 1

            # 系列F: 相对低位 + 金叉/反弹
            for p_max in [0.15, 0.20, 0.25, 0.30]:
                if pos60 <= p_max and ma5[i] and ma10[i] and ma5[i-1] and ma10[i-1]:
                    if ma5[i-1] <= ma10[i-1] and ma5[i] > ma10[i] and c > o:
                        key = f"低位<{int(p_max*100)}%+金叉+收阳"
                        r = results[key]
                        r["count"] += 1; r["returns"].append(ret1)
                        if ret1 > 0: r["wins"] += 1

            # 系列G: MA粘合→发散
            if i >= 5 and ma5[i] and ma20[i] and ma5[i-4] and ma20[i-4]:
                spread_now = abs(ma5[i] - ma20[i]) / ma20[i]
                spread_prev = abs(ma5[i-4] - ma20[i-4]) / ma20[i-4]
                for s_max in [0.01, 0.015, 0.02]:
                    if spread_prev <= s_max and spread_now > s_max*2 and ma5[i] > ma20[i]:
                        for v_cond in [(True, "收阳"), (vol_r > 1.2 and c > o, "放量阳")]:
                            if v_cond[0]:
                                key = f"粘合≤{int(s_max*1000)}‰→发散+{v_cond[1]}"
                                r = results[key]
                                r["count"] += 1; r["returns"].append(ret1)
                                if ret1 > 0: r["wins"] += 1

            # 系列H: 放量突破+MA
            for p_min in [0.50, 0.70]:
                if pos60 >= p_min and c > o and vol_r > 2.0 and c > ma20[i]:
                    key = f"高位≥{int(p_min*100)}%+放量>2x+站上MA20"
                    r = results[key]
                    r["count"] += 1; r["returns"].append(ret1)
                    if ret1 > 0: r["wins"] += 1

            # 系列I: 超跌反弹变体
            if chg_5d < -0.10 and c > o:
                for v_cond, v_name in [(vol_r < 0.5, "缩量<0.5x"), (vol_r > 1.5, "放量>1.5x"),
                                        (True, "不限量")]:
                    for p_max in [0.20, 0.30]:
                        if pos60 <= p_max and v_cond:
                            key = f"5日跌10%+低位<{int(p_max*100)}%+{v_name}+收阳"
                            r = results[key]
                            r["count"] += 1; r["returns"].append(ret1)
                            if ret1 > 0: r["wins"] += 1

    return results

def main():
    print("加载...")
    all_bars = load_all_bars()
    print(f"  {len(all_bars)} 只 (≥120根)")

    results = test(all_bars)

    # 找所有 ≥65% WR 的信号
    print(f"\n{'='*100}")
    print(f"胜率 ≥ 65% 的信号 (T+1), 按胜率排序")
    print(f"{'='*100}")
    found = []
    for name, r in results.items():
        if r["count"] >= 100:
            wr = r["wins"] / r["count"] * 100
            avg_r = mean(r["returns"]) * 100
            med_r = median(r["returns"]) * 100
            found.append((name, r["count"], wr, avg_r, med_r))
    found.sort(key=lambda x: -x[2])

    above_65 = [x for x in found if x[2] >= 65]
    above_60 = [x for x in found if 60 <= x[2] < 65]

    print(f"\n  {'信号':<55s} {'N':>6s} {'胜率':>7s} {'均收益':>8s} {'中位':>8s}")
    print(f"  {'─'*55} {'─'*6} {'─'*7} {'─'*8} {'─'*8}")
    for name, count, wr, avg_r, med_r in above_65:
        print(f"  ✓ {name:<53s} {count:>6d} {wr:>6.1f}% {avg_r:>+7.2f}% {med_r:>+7.2f}%")
    if above_60:
        print(f"\n  {'─'*55} {'─'*6} {'─'*7} {'─'*8} {'─'*8}")
        for name, count, wr, avg_r, med_r in above_60:
            print(f"  ~ {name:<53s} {count:>6d} {wr:>6.1f}% {avg_r:>+7.2f}% {med_r:>+7.2f}%")

    print(f"\n  ≥65%: {len(above_65)} 个, ≥60%: {len(above_60)} 个")

    # Top 10 全部
    print(f"\n{'='*100}")
    print(f"Top 20 信号")
    print(f"{'='*100}")
    for name, count, wr, avg_r, med_r in found[:20]:
        star = "★" if wr >= 65 else ("☆" if wr >= 60 else "")
        print(f"  {star} {name:<53s} {count:>6d} {wr:>6.1f}% {avg_r:>+7.2f}% {med_r:>+7.2f}%")

if __name__ == "__main__":
    main()
