#!/usr/bin/env python3
"""
买入-卖出信号深度分析 v1
  1. 持股天数 vs 收益率关系
  2. 入场信号 × 出场信号 配对分析
  3. 最优入场→出场组合 (按收益率排序)
  4. 按体制/量比/MA状态交叉分析

用法:
  python trade_analysis.py                              # 分析最新交易记录
  python trade_analysis.py --dir trade_records/xxx      # 指定目录
"""
import argparse, json, os, sys
from collections import defaultdict
from typing import Optional
import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TRADE_RECORDS_DIR = os.path.join(SCRIPT_DIR, "trade_records")


def find_latest_trades() -> Optional[str]:
    """找到最新的交易记录JSON"""
    if not os.path.isdir(TRADE_RECORDS_DIR):
        return None
    dirs = sorted([
        d for d in os.listdir(TRADE_RECORDS_DIR)
        if os.path.isdir(os.path.join(TRADE_RECORDS_DIR, d))
    ], reverse=True)
    for d in dirs:
        path = os.path.join(TRADE_RECORDS_DIR, d, "trades.json")
        if os.path.exists(path):
            return path
    return None


def load_trades(json_path: str) -> list[dict]:
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("trades", [])


def analyze_hold_days_vs_return(trades: list[dict]):
    """持股天数 vs 收益率分析"""
    print(f"\n{'='*60}")
    print(f"  1. 持股天数 vs 收益率分析")
    print(f"{'='*60}")

    # 按持仓天数分桶
    buckets = [
        (1, 3, "1-3天"),
        (4, 5, "4-5天"),
        (6, 10, "6-10天"),
        (11, 20, "11-20天"),
        (21, 40, "21-40天"),
        (41, 60, "41-60天"),
        (61, 100, "61-100天"),
        (101, 999, "100天+"),
    ]

    print(f"\n  {'持仓区间':<12s} {'笔数':>6s} {'胜率':>6s} {'均值':>7s} {'20%+':>6s} {'30%+':>6s} {'峰值均值':>7s} {'盈亏比':>6s}")
    print(f"  {'-'*60}")

    best_bucket = None
    best_avg = -999

    for lo, hi, label in buckets:
        subset = [t for t in trades if lo <= t["hold_days"] <= hi]
        if len(subset) < 5:
            continue
        n = len(subset)
        rets = [t["return_pct"] for t in subset]
        wins = sum(1 for t in subset if t["win"])
        hit20 = sum(1 for r in rets if r >= 20)
        hit30 = sum(1 for r in rets if r >= 30)
        peaks = [t["peak_return"] for t in subset]
        avg_ret = np.mean(rets)
        wr = wins / n * 100
        w_rets = [r for r in rets if r > 0]
        l_rets = [abs(r) for r in rets if r <= 0]
        pf = sum(w_rets) / sum(l_rets) if l_rets and sum(l_rets) > 0 else 0

        marker = " ⭐" if avg_ret > best_avg else ""
        if avg_ret > best_avg:
            best_avg = avg_ret
            best_bucket = label

        print(f"  {label:<12s} {n:>6d} {wr:>5.1f}% {avg_ret:>+6.2f}% {hit20/n*100:>5.1f}% {hit30/n*100:>5.1f}% {np.mean(peaks):>+6.1f}% {pf:>5.2f}{marker}")

    print(f"\n  🏆 最优持仓区间: {best_bucket} (均值 {best_avg:+.2f}%)")

    # 散点图数据 (用文本模拟)
    print(f"\n  📊 收益分布 (每20%一档):")
    bins = [(-100, -10), (-10, -5), (-5, 0), (0, 5), (5, 10), (10, 20), (20, 30), (30, 50), (50, 999)]
    for lo, hi in bins:
        cnt = sum(1 for t in trades if lo <= t["return_pct"] < hi)
        pct = cnt / len(trades) * 100
        bar = "█" * max(1, int(pct))
        label = f"{lo:+d}%~{hi:+d}%" if hi < 999 else f"{lo:+d}%+"
        print(f"  {label:<10s} {cnt:>5d} ({pct:>5.1f}%) {bar}")


def analyze_entry_exit_pairs(trades: list[dict]):
    """入场信号 × 出场信号 配对分析 — 找最高收益组合"""
    print(f"\n{'='*60}")
    print(f"  2. 入场信号 × 出场信号 配对分析 (收益最高)")
    print(f"{'='*60}")

    # 提取简化信号关键词
    def simplify_signal(sig: str) -> str:
        keywords = ["深跌35%", "深跌25%", "MA5金叉MA10", "MA5金叉MA20",
                    "三连阳", "三日连涨", "启明星", "双针探底",
                    "MA20上升", "二连阴缩", "涨停", "放量破高", "回踩MA5"]
        for kw in keywords:
            if kw in sig:
                return kw
        return sig[:20]

    def simplify_exit(reason: str) -> str:
        if "射击之星" in reason: return "射击之星"
        if "跌破MA20" in reason: return "跌破MA20"
        if "MA5死叉" in reason: return "MA死叉"
        if "量价背离" in reason: return "量价背离"
        if "看跌吞没" in reason: return "看跌吞没"
        if "缩量滞涨" in reason: return "缩量滞涨"
        if "急涨后" in reason: return "急涨低开"
        if "trailing" in reason: return "移动止损"
        if "stop_loss" in reason: return "硬止损"
        if "take_profit" in reason: return "止盈30%"
        if "safety" in reason: return "安全兜底"
        return reason[:15]

    pairs = defaultdict(list)
    for t in trades:
        entry = simplify_signal(t.get("pattern", "?"))
        exit_sig = simplify_exit(t.get("exit_reason", "?"))
        pair_key = f"{entry} → {exit_sig}"
        pairs[pair_key].append(t)

    # 按平均收益排序
    results = []
    for pair_key, pair_trades in pairs.items():
        if len(pair_trades) < 3:
            continue
        rets = [t["return_pct"] for t in pair_trades]
        peaks = [t["peak_return"] for t in pair_trades]
        avg_ret = np.mean(rets)
        wins = sum(1 for t in pair_trades if t["win"])
        hit20 = sum(1 for r in rets if r >= 20)
        hit30 = sum(1 for r in rets if r >= 30)
        n = len(pair_trades)
        results.append({
            "pair": pair_key, "n": n,
            "avg_ret": avg_ret, "wr": wins / n * 100,
            "hit20": hit20 / n * 100, "hit30": hit30 / n * 100,
            "avg_peak": np.mean(peaks),
            "avg_hold": np.mean([t["hold_days"] for t in pair_trades]),
        })

    results.sort(key=lambda x: x["avg_ret"], reverse=True)

    print(f"\n  {'入场 → 出场':<40s} {'N':>4s} {'WR':>5s} {'均值':>7s} {'20%+':>6s} {'30%+':>6s} {'峰值':>7s} {'持仓':>5s}")
    print(f"  {'-'*90}")
    for r in results[:25]:
        label = " ⭐" if r["avg_ret"] > 8 and r["hit20"] > 15 else ""
        print(f"  {r['pair']:<40s} {r['n']:>4d} {r['wr']:>4.1f}% {r['avg_ret']:>+6.2f}% {r['hit20']:>5.1f}% {r['hit30']:>5.1f}% {r['avg_peak']:>+6.1f}% {r['avg_hold']:>4.1f}d{label}")

    return results


def analyze_exit_signals(trades: list[dict]):
    """出场信号有效性分析 — 哪些信号能锁定最高收益"""
    print(f"\n{'='*60}")
    print(f"  3. 出场信号有效性 (哪个信号锁定的收益最高)")
    print(f"{'='*60}")

    def simplify_exit(reason: str) -> str:
        mapping = {
            "take_profit": "止盈30%",
            "trailing_stop": "移动止损",
            "stop_loss": "硬止损",
            "safety_timeout": "安全兜底",
        }
        for k, v in mapping.items():
            if k in reason:
                return v
        if "射击之星" in reason: return "K线:射击之星"
        if "跌破MA20" in reason: return "K线:跌破MA20"
        if "MA5死叉" in reason: return "K线:MA死叉"
        if "量价背离" in reason: return "K线:量价背离"
        if "看跌吞没" in reason: return "K线:看跌吞没"
        if "缩量滞涨" in reason: return "K线:缩量滞涨"
        if "急涨" in reason: return "K线:急涨低开"
        return f"其他:{reason[:15]}"

    exit_groups = defaultdict(list)
    for t in trades:
        exit_type = simplify_exit(t.get("exit_reason", "?"))
        exit_groups[exit_type].append(t)

    print(f"\n  {'出场信号':<20s} {'N':>5s} {'均值':>7s} {'WR':>5s} {'20%+':>6s} {'30%+':>6s} {'峰值':>7s} {'持仓':>5s} {'评价'}")
    print(f"  {'-'*80}")

    results = []
    for sig, sig_trades in exit_groups.items():
        if len(sig_trades) < 2:
            continue
        rets = [t["return_pct"] for t in sig_trades]
        peaks = [t["peak_return"] for t in sig_trades]
        n = len(sig_trades)
        avg_ret = np.mean(rets)
        wr = sum(1 for t in sig_trades if t["win"]) / n * 100
        hit20 = sum(1 for r in rets if r >= 20) / n * 100
        hit30 = sum(1 for r in rets if r >= 30) / n * 100

        # 评价
        if avg_ret > 10:
            grade = "🏆 最佳"
        elif avg_ret > 5:
            grade = "✅ 优秀"
        elif avg_ret > 0:
            grade = "👍 正收益"
        elif avg_ret > -5:
            grade = "⚠ 需优化"
        else:
            grade = "❌ 差"

        results.append({
            "signal": sig, "n": n, "avg_ret": avg_ret, "wr": wr,
            "hit20": hit20, "hit30": hit30,
            "avg_peak": np.mean(peaks),
            "avg_hold": np.mean([t["hold_days"] for t in sig_trades]),
            "grade": grade,
        })

    results.sort(key=lambda x: x["avg_ret"], reverse=True)
    for r in results:
        print(f"  {r['signal']:<20s} {r['n']:>5d} {r['avg_ret']:>+6.2f}% {r['wr']:>4.1f}% {r['hit20']:>5.1f}% {r['hit30']:>5.1f}% {r['avg_peak']:>+6.1f}% {r['avg_hold']:>4.1f}d {r['grade']}")

    return results


def analyze_regime_performance(trades: list[dict]):
    """按入场体制分析"""
    print(f"\n{'='*60}")
    print(f"  4. 入场体制 vs 收益率")
    print(f"{'='*60}")

    regimes = defaultdict(list)
    for t in trades:
        regime = t.get("entry_regime", "unknown")
        regimes[regime].append(t)

    print(f"\n  {'体制':<12s} {'N':>5s} {'WR':>5s} {'均值':>7s} {'20%+':>6s} {'峰值':>7s} {'持仓':>5s}")
    print(f"  {'-'*55}")
    for reg in ["bull", "bull_bias", "range", "bear_bias", "bear"]:
        if reg not in regimes:
            continue
        rt = regimes[reg]
        if len(rt) < 5:
            continue
        rets = [t["return_pct"] for t in rt]
        n = len(rt)
        wr = sum(1 for t in rt if t["win"]) / n * 100
        hit20 = sum(1 for r in rets if r >= 20) / n * 100
        avg_ret = np.mean(rets)
        avg_peak = np.mean([t["peak_return"] for t in rt])
        avg_hold = np.mean([t["hold_days"] for t in rt])
        emoji = {"bull": "🐂", "bull_bias": "📈", "range": "📊", "bear_bias": "📉", "bear": "🐻"}.get(reg, "")
        print(f"  {emoji} {reg:<10s} {n:>5d} {wr:>4.1f}% {avg_ret:>+6.2f}% {hit20:>5.1f}% {avg_peak:>+6.1f}% {avg_hold:>4.1f}d")

    # 量比分析
    if any("entry_vol_ratio" in t for t in trades):
        print(f"\n  {'量比区间':<12s} {'N':>5s} {'WR':>5s} {'均值':>7s} {'20%+':>6s} {'峰值':>7s}")
        print(f"  {'-'*50}")
        vol_buckets = [(0, 0.5, "缩量<0.5"), (0.5, 1.0, "正常0.5-1"), (1.0, 2.0, "放量1-2"),
                       (2.0, 5.0, "暴量2-5"), (5.0, 999, "天量5+")]
        for lo, hi, label in vol_buckets:
            subset = [t for t in trades if lo <= t.get("entry_vol_ratio", 0) < hi]
            if len(subset) < 5:
                continue
            rets = [t["return_pct"] for t in subset]
            n = len(subset)
            wr = sum(1 for t in subset if t["win"]) / n * 100
            hit20 = sum(1 for r in rets if r >= 20) / n * 100
            print(f"  {label:<12s} {n:>5d} {wr:>4.1f}% {np.mean(rets):>+6.2f}% {hit20:>5.1f}% {np.mean([t['peak_return'] for t in subset]):>+6.1f}%")


def analyze_best_strategies(trades: list[dict]) -> list[dict]:
    """综合最优策略推荐"""
    print(f"\n{'='*60}")
    print(f"  5. 🎯 综合最优策略推荐")
    print(f"{'='*60}")

    recommendations = []

    # 1. 最高收益入场+出场组合
    from collections import Counter

    # 找20%+命中率最高的入场信号
    entry_stats = defaultdict(list)
    for t in trades:
        entry = t.get("pattern", "?")[:40]
        entry_stats[entry].append(t)

    print(f"\n  📌 推荐入场信号 (20%+命中率 ≥15%, N≥10):")
    for entry, et in sorted(entry_stats.items(), key=lambda x: sum(1 for r in [t["return_pct"] for t in x[1]] if r >= 20)/len(x[1]), reverse=True):
        n = len(et)
        if n < 10:
            continue
        rets = [t["return_pct"] for t in et]
        hit20 = sum(1 for r in rets if r >= 20) / n * 100
        hit30 = sum(1 for r in rets if r >= 30) / n * 100
        avg_ret = np.mean(rets)
        if hit20 >= 15:
            print(f"    ✅ {entry[:55]:<55s} N={n:>3d} avg={avg_ret:>+5.1f}% 20%+={hit20:>4.1f}% 30%+={hit30:>4.1f}%")

    # 2. 最优出场时机
    print(f"\n  📌 出场策略建议:")
    # 止盈 vs 移动止损 vs K线信号
    tp_trades = [t for t in trades if "take_profit" in t.get("exit_reason", "")]
    trail_trades = [t for t in trades if "trailing" in t.get("exit_reason", "")]
    signal_trades = [t for t in trades if "signal_" in t.get("exit_reason", "")]

    for name, subset in [("止盈30%", tp_trades), ("移动止损", trail_trades), ("K线出场信号", signal_trades)]:
        if len(subset) < 2:
            continue
        rets = [t["return_pct"] for t in subset]
        print(f"    {name:<12s} N={len(subset):>4d} 均值={np.mean(rets):>+5.2f}% 持仓={np.mean([t['hold_days'] for t in subset]):>4.1f}d")

    # 3. 持仓天数建议
    print(f"\n  📌 最优持仓天数建议:")
    for days in [5, 10, 15, 20, 30, 60]:
        subset = [t for t in trades if t["hold_days"] <= days and t["exit_reason"] != "stop_loss"]
        if len(subset) < 10:
            continue
        rets = [t["return_pct"] for t in subset]
        hit20 = sum(1 for r in rets if r >= 20) / len(subset) * 100
        wr = sum(1 for t in subset if t["win"]) / len(subset) * 100
        print(f"    ≤{days:>2d}日卖出 (非止损): N={len(subset):>4d} WR={wr:>4.1f}% avg={np.mean(rets):>+5.2f}% 20%+={hit20:>4.1f}%")

    return recommendations


def main():
    parser = argparse.ArgumentParser(description="买入-卖出信号深度分析")
    parser.add_argument("--dir", type=str, help="交易记录目录路径")
    args = parser.parse_args()

    if args.dir:
        json_path = os.path.join(args.dir, "trades.json")
    else:
        json_path = find_latest_trades()

    if not json_path or not os.path.exists(json_path):
        print("❌ 未找到交易记录, 请先运行 full_backtest.py")
        sys.exit(1)

    print(f"📂 加载: {json_path}")
    trades = load_trades(json_path)
    print(f"📊 {len(trades)} 笔交易")

    analyze_hold_days_vs_return(trades)
    analyze_entry_exit_pairs(trades)
    analyze_exit_signals(trades)
    analyze_regime_performance(trades)
    analyze_best_strategies(trades)

    print(f"\n{'='*60}")
    print(f"  ✅ 分析完成")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
