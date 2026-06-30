#!/usr/bin/env python3
"""
MA5/MA10/MA15 与 K线走势相关性分析
目标: 找出提高隔夜选股胜率的关键MA因子
"""
import json
import sys
from pathlib import Path
from statistics import mean, stdev

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KLINE_DIR = PROJECT_ROOT / "kline_data"
BACKTEST_FILE = PROJECT_ROOT / "research_data/backtest/overnight_backtest.json"

def safe_mean(data, default=0.0):
    return mean(data) if data else default

def safe_stdev(data, default=0.0):
    return stdev(data) if len(data) >= 2 else default

def load_kline(code: str) -> list[dict]:
    fp = KLINE_DIR / f"{code}.json"
    if not fp.exists():
        return []
    with open(fp) as f:
        data = json.load(f)
    return data.get("bars", [])

def calc_ma(bars: list[dict], period: int) -> list[float]:
    """计算移动均线, 返回与bars等长的list(前period-1个为None)"""
    result = [None] * len(bars)
    for i in range(period - 1, len(bars)):
        result[i] = sum(b["close"] for b in bars[i - period + 1:i + 1]) / period
    return result

def analyze_trade(trade: dict) -> dict | None:
    """分析单笔交易的MA指标"""
    code = trade["code"]
    pick_date = trade["pick_date"]
    entry = trade.get("entry")
    win = trade.get("win")
    ret = trade.get("ret")

    if win is None or ret is None:
        return None
    if entry is None or entry == 0:
        return None

    bars = load_kline(code)
    if len(bars) < 60:
        return None

    # 找到pick_date在bars中的位置(取该日及之前的K线)
    idx = None
    for i, b in enumerate(bars):
        if b["date"] == pick_date:
            idx = i
            break
    if idx is None:
        # 找最近的交易日
        for i, b in enumerate(bars):
            if b["date"] > pick_date:
                idx = i - 1
                break
        if idx is None:
            idx = len(bars) - 1

    if idx < 30:  # 至少需要30根K线计算指标
        return None

    # 截取到pick_date的K线
    hist = bars[:idx + 1]
    close = hist[-1]["close"]

    # 计算均线
    ma5 = calc_ma(hist, 5)
    ma10 = calc_ma(hist, 10)
    ma15 = calc_ma(hist, 15)
    ma20 = calc_ma(hist, 20)
    ma60 = calc_ma(hist, 60)

    last = len(hist) - 1

    # === 1. MA多头排列评分 ===
    # 5>10>15>20 满分3, 完全空头-3
    alignment = 0
    if ma5[last] and ma10[last]:
        alignment += 1 if ma5[last] > ma10[last] else -1
    if ma10[last] and ma15[last]:
        alignment += 1 if ma10[last] > ma15[last] else -1
    if ma15[last] and ma20[last]:
        alignment += 1 if ma15[last] > ma20[last] else -1

    # === 2. 价格 vs 均线位置 ===
    pos_vs_ma5 = (close - ma5[last]) / ma5[last] * 100 if ma5[last] else 0
    pos_vs_ma10 = (close - ma10[last]) / ma10[last] * 100 if ma10[last] else 0
    pos_vs_ma15 = (close - ma15[last]) / ma15[last] * 100 if ma15[last] else 0
    pos_vs_ma20 = (close - ma20[last]) / ma20[last] * 100 if ma20[last] else 0
    pos_vs_ma60 = (close - ma60[last]) / ma60[last] * 100 if ma60[last] else 0

    # === 3. MA斜率(近5日MA5变化) ===
    ma5_slope = 0
    if last >= 5 and ma5[last] and ma5[last - 4]:
        ma5_slope = (ma5[last] - ma5[last - 4]) / ma5[last - 4] * 100

    ma10_slope = 0
    if last >= 5 and ma10[last] and ma10[last - 4]:
        ma10_slope = (ma10[last] - ma10[last - 4]) / ma10[last - 4] * 100

    ma15_slope = 0
    if last >= 5 and ma15[last] and ma15[last - 4]:
        ma15_slope = (ma15[last] - ma15[last - 4]) / ma15[last - 4] * 100

    # === 4. MA间距(粘合度) ===
    ma5_10_spread = abs(ma5[last] - ma10[last]) / ma10[last] * 100 if ma5[last] and ma10[last] else 0
    ma5_15_spread = abs(ma5[last] - ma15[last]) / ma15[last] * 100 if ma5[last] and ma15[last] else 0
    ma10_15_spread = abs(ma10[last] - ma15[last]) / ma15[last] * 100 if ma10[last] and ma15[last] else 0

    # 均线粘合度(越小越粘合)
    avg_spread = (ma5_10_spread + ma5_15_spread + ma10_15_spread) / 3

    # === 5. K线相对60日位置 ===
    high_60 = max(b["high"] for b in hist[-60:])
    low_60 = min(b["low"] for b in hist[-60:])
    pos_60 = (close - low_60) / (high_60 - low_60) * 100 if high_60 != low_60 else 50

    # === 6. 近5日量比 ===
    vol_last5 = [b["volume"] for b in hist[-5:]]
    vol_prev5 = [b["volume"] for b in hist[-10:-5]]
    vol_ratio = safe_mean(vol_last5) / safe_mean(vol_prev5) if safe_mean(vol_prev5) > 0 else 1.0

    # === 7. 近3日涨跌幅 ===
    chg_1d = (hist[-1]["close"] - hist[-2]["close"]) / hist[-2]["close"] * 100 if len(hist) >= 2 else 0
    chg_3d = (hist[-1]["close"] - hist[-4]["close"]) / hist[-4]["close"] * 100 if len(hist) >= 4 else 0
    chg_5d = (hist[-1]["close"] - hist[-6]["close"]) / hist[-6]["close"] * 100 if len(hist) >= 6 else 0

    # === 8. 收盘价在日K线中的位置 ===
    candle_pos = (close - hist[-1]["low"]) / (hist[-1]["high"] - hist[-1]["low"]) if hist[-1]["high"] != hist[-1]["low"] else 0.5

    return {
        "code": code,
        "name": trade.get("name", ""),
        "tier": trade.get("tier", ""),
        "pick_date": pick_date,
        "entry": entry,
        "ret": ret,
        "win": win,
        # MA指标
        "alignment": alignment,
        "pos_vs_ma5": pos_vs_ma5,
        "pos_vs_ma10": pos_vs_ma10,
        "pos_vs_ma15": pos_vs_ma15,
        "pos_vs_ma20": pos_vs_ma20,
        "pos_vs_ma60": pos_vs_ma60,
        "ma5_slope": ma5_slope,
        "ma10_slope": ma10_slope,
        "ma15_slope": ma15_slope,
        "ma5_10_spread": ma5_10_spread,
        "ma5_15_spread": ma5_15_spread,
        "ma10_15_spread": ma10_15_spread,
        "avg_spread": avg_spread,
        "pos_60": pos_60,
        "vol_ratio": vol_ratio,
        "chg_1d": chg_1d,
        "chg_3d": chg_3d,
        "chg_5d": chg_5d,
        "candle_pos": candle_pos,
    }


def print_group_stats(label: str, group: list[dict], field: str, fmt: str = ".2f"):
    """安全打印分组统计"""
    vals = [r[field] for r in group if r.get(field) is not None]
    if not vals:
        print(f"  │ {label}: (无数据)")
        return None, None
    avg = mean(vals)
    sd = safe_stdev(vals)
    print(f"  │ {label}: 均值={avg:{fmt}}  标准差={sd:{fmt}}  n={len(vals)}")
    return avg, sd


def main():
    with open(BACKTEST_FILE) as f:
        data = json.load(f)

    # 收集所有有效交易
    trades = []
    for day in data["days"]:
        for pick in day["picks"]:
            pick["pick_date"] = day["pick_date"]
            pick["eval_date"] = day["eval_date"]
            result = analyze_trade(pick)
            if result:
                trades.append(result)

    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]

    print(f"{'='*70}")
    print(f"MA5/MA10/MA15 与隔夜收益相关性分析")
    print(f"{'='*70}")
    print(f"有效交易: {len(trades)} 笔 (胜 {len(wins)} / 负 {len(losses)})")
    print(f"总胜率: {len(wins)/len(trades)*100:.1f}%")
    print()

    # ============================================================
    # 1. MA多头排列
    # ============================================================
    print("【1】MA 多头排列评分 (5>10>15>20, +3 ~ -3)")
    print("  ─────────────────────────────────────────")
    w_avg, w_sd = print_group_stats("胜组", wins, "alignment", ".1f")
    l_avg, l_sd = print_group_stats("负组", losses, "alignment", ".1f")
    if w_avg is not None and l_avg is not None:
        diff = w_avg - l_avg
        print(f"  │ 差值: {diff:+.1f}  → {'✓ 胜组更偏多头' if diff > 0 else '✗ 负组更偏多头'}")
    print()

    # ============================================================
    # 2. 价格 vs 均线位置
    # ============================================================
    print("【2】收盘价相对均线位置 (%)")
    print("  ─────────────────────────────────────────")
    for ma_name, field in [("vs MA5", "pos_vs_ma5"), ("vs MA10", "pos_vs_ma10"),
                           ("vs MA15", "pos_vs_ma15"), ("vs MA20", "pos_vs_ma20"),
                           ("vs MA60", "pos_vs_ma60")]:
        w_avg, _ = print_group_stats(f"胜组 {ma_name}", wins, field)
        l_avg, _ = print_group_stats(f"负组 {ma_name}", losses, field)
        if w_avg is not None and l_avg is not None:
            print(f"  │ 差值: {w_avg - l_avg:+.2f}pp")
    print()

    # ============================================================
    # 3. MA斜率
    # ============================================================
    print("【3】MA 5日斜率 (%)")
    print("  ─────────────────────────────────────────")
    for ma_name, field in [("MA5 斜率", "ma5_slope"), ("MA10 斜率", "ma10_slope"),
                           ("MA15 斜率", "ma15_slope")]:
        w_avg, _ = print_group_stats(f"胜组 {ma_name}", wins, field)
        l_avg, _ = print_group_stats(f"负组 {ma_name}", losses, field)
        if w_avg is not None and l_avg is not None:
            print(f"  │ 差值: {w_avg - l_avg:+.2f}pp")
    print()

    # ============================================================
    # 4. MA间距/粘合度
    # ============================================================
    print("【4】均线间距 — 粘合 vs 发散 (%)")
    print("  ─────────────────────────────────────────")
    for name, field in [("MA5-MA10", "ma5_10_spread"), ("MA5-MA15", "ma5_15_spread"),
                        ("MA10-MA15", "ma10_15_spread"), ("平均间距", "avg_spread")]:
        w_avg, _ = print_group_stats(f"胜组 {name}", wins, field)
        l_avg, _ = print_group_stats(f"负组 {name}", losses, field)
        if w_avg is not None and l_avg is not None:
            print(f"  │ 差值: {w_avg - l_avg:+.2f}pp")
    print()

    # ============================================================
    # 5. 60日位置
    # ============================================================
    print("【5】60日高低位 (0=最低, 100=最高)")
    print("  ─────────────────────────────────────────")
    w_avg, _ = print_group_stats("胜组", wins, "pos_60")
    l_avg, _ = print_group_stats("负组", losses, "pos_60")
    if w_avg is not None and l_avg is not None:
        print(f"  │ 差值: {w_avg - l_avg:+.1f}%")
    print()

    # ============================================================
    # 6. 量比 & 涨跌幅
    # ============================================================
    print("【6】近5日量比 & 近期涨跌幅")
    print("  ─────────────────────────────────────────")
    for name, field in [("近5日量比", "vol_ratio"), ("近1日涨跌%", "chg_1d"),
                        ("近3日涨跌%", "chg_3d"), ("近5日涨跌%", "chg_5d")]:
        w_avg, _ = print_group_stats(f"胜组 {name}", wins, field)
        l_avg, _ = print_group_stats(f"负组 {name}", losses, field)
        if w_avg is not None and l_avg is not None:
            print(f"  │ 差值: {w_avg - l_avg:+.2f}")
    print()

    # ============================================================
    # 7. 日K线实体位置
    # ============================================================
    print("【7】收盘在日K线中的位置 (0=最低, 1=最高)")
    print("  ─────────────────────────────────────────")
    w_avg, _ = print_group_stats("胜组", wins, "candle_pos")
    l_avg, _ = print_group_stats("负组", losses, "candle_pos")
    if w_avg is not None and l_avg is not None:
        print(f"  │ 差值: {w_avg - l_avg:+.2f}")

    # ============================================================
    # 综合: 找区分度最高的因子
    # ============================================================
    print()
    print("=" * 70)
    print("【综合评估】因子区分度排名 (|胜组均值 - 负组均值| / max(标准差, 0.01))")
    print("=" * 70)

    factors = [
        ("MA多头排列", "alignment", ".1f"),
        ("价格vsMA5", "pos_vs_ma5", ".2f"),
        ("价格vsMA10", "pos_vs_ma10", ".2f"),
        ("价格vsMA15", "pos_vs_ma15", ".2f"),
        ("价格vsMA20", "pos_vs_ma20", ".2f"),
        ("价格vsMA60", "pos_vs_ma60", ".2f"),
        ("MA5斜率", "ma5_slope", ".2f"),
        ("MA10斜率", "ma10_slope", ".2f"),
        ("MA15斜率", "ma15_slope", ".2f"),
        ("MA5-10间距", "ma5_10_spread", ".2f"),
        ("MA5-15间距", "ma5_15_spread", ".2f"),
        ("MA10-15间距", "ma10_15_spread", ".2f"),
        ("均线粘合度", "avg_spread", ".2f"),
        ("60日位置", "pos_60", ".1f"),
        ("近5日量比", "vol_ratio", ".2f"),
        ("近1日涨跌", "chg_1d", ".2f"),
        ("近3日涨跌", "chg_3d", ".2f"),
        ("近5日涨跌", "chg_5d", ".2f"),
        ("K线实体位置", "candle_pos", ".2f"),
    ]

    scored = []
    for name, field, _ in factors:
        w_vals = [r[field] for r in wins if r.get(field) is not None]
        l_vals = [r[field] for r in losses if r.get(field) is not None]
        if not w_vals or not l_vals:
            continue
        w_m = mean(w_vals)
        l_m = mean(l_vals)
        diff = abs(w_m - l_m)
        pooled_sd = max(safe_stdev(w_vals + l_vals, 0.01), 0.01)
        score = diff / pooled_sd
        scored.append((name, w_m, l_m, diff, score))

    scored.sort(key=lambda x: x[4], reverse=True)

    print(f"  {'因子':<16s} {'胜组均值':>8s} {'负组均值':>8s} {'差值':>8s} {'区分度':>8s}")
    print(f"  {'─'*16} {'─'*8} {'─'*8} {'─'*8} {'─'*8}")
    for name, w_m, l_m, diff, score in scored:
        print(f"  {name:<16s} {w_m:>+8.2f} {l_m:>+8.2f} {diff:>8.2f} {score:>8.3f}")

    print()
    print("结论:")
    if scored:
        top3 = scored[:3]
        print(f"  区分度最高的3个因子:")
        for i, (name, w_m, l_m, diff, score) in enumerate(top3, 1):
            direction = "偏高" if w_m > l_m else "偏低"
            print(f"    {i}. {name}: 胜组{direction} (差值{diff:+.2f}, 区分度{score:.3f})")

    # 打印每笔交易明细
    print()
    print("=" * 70)
    print("【交易明细】")
    print("=" * 70)
    print(f"  {'代码':<8s} {'名称':<10s} {'收益%':>6s} {'胜负':>4s} {'排列':>4s} {'vsMA5':>7s} {'60位':>6s} {'量比':>6s}")
    print(f"  {'─'*8} {'─'*10} {'─'*6} {'─'*4} {'─'*4} {'─'*7} {'─'*6} {'─'*6}")
    for t in sorted(trades, key=lambda x: x["ret"] or -999, reverse=True):
        print(f"  {t['code']:<8s} {t['name']:<10s} {t['ret']:>+5.2f}% {'胜' if t['win'] else '负':>4s} "
              f"{t['alignment']:>+4.0f} {t['pos_vs_ma5']:>+6.2f}% {t['pos_60']:>5.0f}% {t['vol_ratio']:>5.2f}")


if __name__ == "__main__":
    main()
