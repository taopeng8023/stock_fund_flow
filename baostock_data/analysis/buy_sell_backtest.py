#!/usr/bin/env python3
"""
买入-卖出信号回测系统 v2 — 目标 8%+ 盈利，全市场周期稳定

v2 优化:
  - 熊市过滤: 价格<MA60 时仅允许深跌反弹信号 (TYPE A)
  - 移动止盈放宽: -5% from peak (v1: -3%)
  - 信号质量分级: TOP_SIGNALS 优先 + 熊市严格确认

用法:
  python buy_sell_backtest.py                    # 全量回测
  python buy_sell_backtest.py --sample 2000      # 快速验证
"""
import argparse, os, sys, time, warnings
from collections import defaultdict
from typing import Optional, List, Dict, Tuple
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from stock_filter import load_stock_files
from kline_discovery import compute_indicators, pattern_signal_at, confirm_entry, load_stock_csv

DAILY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "baostock_data", "data", "daily")

TAKE_PROFIT = 0.08
STOP_LOSS = -0.05
TRAILING_STOP = -0.05    # v2: -5% (v1: -3%)
MAX_HOLD_DAYS = 20
MIN_SAMPLE = 10

# 熊市首选信号 (深跌反弹类 — 历史上在任何周期都表现最好)
BEAR_SAFE_PREFIXES = ("深跌", "急跌", "连阴", "启明星", "双针", "反包")

REGIMES = {
    "bear_2018":   ("2018-01-01", "2018-12-31"),
    "bull_2019":   ("2019-01-01", "2020-12-31"),
    "range_2021":  ("2021-01-01", "2022-04-30"),
    "bear_2022":   ("2022-05-01", "2022-10-31"),
    "range_2023":  ("2023-01-01", "2024-06-30"),
    "bull_2024":   ("2024-09-01", "2025-06-30"),
}


def is_bearish(df: pd.DataFrame, i: int) -> bool:
    """判断当前是否处于熊市环境 (价格 < MA60 且 MA20 < MA60)"""
    close = float(df["收盘"].values[i])
    if "ma60" not in df.columns:
        return False
    ma60 = df["ma60"].values[i]
    ma20 = df["ma20"].values[i] if "ma20" in df.columns else ma60
    if pd.isna(ma60) or ma60 <= 0:
        return False
    return close < ma60 and ma20 < ma60


def is_safe_for_bear(pattern: str) -> bool:
    """判断信号是否适合熊市 (深跌反弹类)"""
    return pattern.startswith(BEAR_SAFE_PREFIXES)


def simulate_exit(df: pd.DataFrame, entry_idx: int,
                  take_profit=TAKE_PROFIT, stop_loss=STOP_LOSS,
                  trailing_stop=TRAILING_STOP, max_hold=MAX_HOLD_DAYS) -> Dict:
    """模拟卖出。v2: 移动止盈放宽至 -5%"""
    entry_price = float(df["收盘"].values[entry_idx])
    n = len(df)
    end_idx = min(entry_idx + max_hold + 1, n)
    peak_price = entry_price
    has_hl = "最高" in df.columns

    for j in range(entry_idx + 1, end_idx):
        close = float(df["收盘"].values[j])
        high = float(df["最高"].values[j]) if has_hl else close
        low = float(df["最低"].values[j]) if has_hl else close
        ret = (close - entry_price) / entry_price
        if high > peak_price:
            peak_price = high

        if ret >= take_profit:
            return {"exit_idx": j, "exit_date": str(df.index[j]),
                    "exit_price": entry_price * (1 + take_profit),
                    "exit_reason": "take_profit",
                    "return_pct": round(take_profit * 100, 2),
                    "peak_return": round((peak_price - entry_price) / entry_price * 100, 2),
                    "hold_days": j - entry_idx, "win": True}

        if (low - entry_price) / entry_price <= stop_loss:
            return {"exit_idx": j, "exit_date": str(df.index[j]),
                    "exit_price": round(entry_price * (1 + stop_loss), 2),
                    "exit_reason": "stop_loss",
                    "return_pct": round(stop_loss * 100, 2),
                    "peak_return": round((peak_price - entry_price) / entry_price * 100, 2),
                    "hold_days": j - entry_idx, "win": False}

        peak_ret = (peak_price - entry_price) / entry_price
        if peak_ret > 0.05:
            dd = (close - peak_price) / peak_price
            if dd <= trailing_stop:
                return {"exit_idx": j, "exit_date": str(df.index[j]),
                        "exit_price": close, "exit_reason": "trailing_stop",
                        "return_pct": round(ret * 100, 2),
                        "peak_return": round(peak_ret * 100, 2),
                        "hold_days": j - entry_idx, "win": ret > 0}

    last_close = float(df["收盘"].values[end_idx - 1])
    last_ret = (last_close - entry_price) / entry_price
    return {"exit_idx": end_idx - 1, "exit_date": str(df.index[end_idx - 1]),
            "exit_price": last_close, "exit_reason": "timeout",
            "return_pct": round(last_ret * 100, 2),
            "peak_return": round((peak_price - entry_price) / entry_price * 100, 2),
            "hold_days": end_idx - 1 - entry_idx, "win": last_ret > 0}


class BuySellBacktest:
    def __init__(self, take_profit=TAKE_PROFIT, stop_loss=STOP_LOSS,
                 trailing=TRAILING_STOP, max_hold=MAX_HOLD_DAYS):
        self.take_profit = take_profit; self.stop_loss = stop_loss
        self.trailing_stop = trailing; self.max_hold = max_hold
        self.trades: List[Dict] = []
        self.signals_by_pattern: Dict[str, List] = defaultdict(list)
        self.bear_filtered = 0  # count of signals skipped in bear market

    def run_stock(self, filepath: str, df: pd.DataFrame,
                  date_range: Optional[Tuple[str, str]] = None) -> int:
        n = len(df)
        if n < 100:
            return 0
        if date_range:
            mask = (df.index >= date_range[0]) & (df.index <= date_range[1])
            df = df[mask]
            if len(df) < 30:
                return 0

        df = compute_indicators(df)
        count = 0
        for i in range(70, len(df) - 1):
            pattern = pattern_signal_at(df, i)
            if pattern is None:
                continue

            # v2: 熊市过滤 — 仅允许深跌反弹信号
            if is_bearish(df, i) and not is_safe_for_bear(pattern):
                self.bear_filtered += 1
                continue

            if not confirm_entry(df, i, strict=True):
                continue

            result = simulate_exit(df, i, self.take_profit, self.stop_loss,
                                   self.trailing_stop, self.max_hold)
            result["pattern"] = pattern
            result["code"] = os.path.splitext(os.path.basename(filepath))[0]
            result["entry_date"] = str(df["日期"].values[i])[:10] if "日期" in df.columns else str(df.index[i])
            result["entry_price"] = float(df["收盘"].values[i])
            self.trades.append(result)
            self.signals_by_pattern[pattern].append(result)
            count += 1
        return count

    def summary(self) -> Dict:
        if not self.trades:
            return {"total_trades": 0, "win_rate": 0, "take_profit_rate": 0,
                    "avg_return": 0, "median_return": 0, "avg_peak_return": 0,
                    "avg_hold_days": 0, "sharpe": 0, "profit_factor": 0,
                    "max_loss_streak": 0, "reason_dist": {}, "pattern_count": 0}
        n = len(self.trades)
        returns = [t["return_pct"] for t in self.trades]
        wins = sum(1 for t in self.trades if t["win"])
        tp = sum(1 for t in self.trades if t["exit_reason"] == "take_profit")
        avg_hold = float(np.mean([t["hold_days"] for t in self.trades]))
        reason = defaultdict(int)
        for t in self.trades:
            reason[t["exit_reason"]] += 1
        streak = max_streak = 0
        for t in self.trades:
            streak = streak + 1 if not t["win"] else 0
            max_streak = max(max_streak, streak)
        w_rets = [t["return_pct"] for t in self.trades if t["win"]]
        l_rets = [abs(t["return_pct"]) for t in self.trades if not t["win"]]
        pf = np.sum(w_rets) / np.sum(l_rets) if l_rets and np.sum(l_rets) > 0 else 0
        sharpe = 0.0
        if len(returns) > 1 and np.std(returns) > 0 and avg_hold > 0:
            sharpe = (np.mean(returns) / 100) / (np.std(returns) / 100) * np.sqrt(252 / avg_hold)
        return {"total_trades": n, "win_rate": round(wins / n * 100, 1),
                "avg_return": round(float(np.mean(returns)), 2),
                "median_return": round(float(np.median(returns)), 2),
                "avg_peak_return": round(float(np.mean([t["peak_return"] for t in self.trades])), 2),
                "avg_hold_days": round(avg_hold, 1),
                "take_profit_rate": round(tp / n * 100, 1),
                "sharpe": round(sharpe, 2), "profit_factor": round(pf, 2),
                "max_loss_streak": max_streak, "reason_dist": dict(reason),
                "pattern_count": len(self.signals_by_pattern),
                "bear_filtered": self.bear_filtered}

    def pattern_summary(self, min_samples=MIN_SAMPLE) -> pd.DataFrame:
        rows = []
        for pat, trades in self.signals_by_pattern.items():
            n = len(trades)
            if n < min_samples:
                continue
            rets = [t["return_pct"] for t in trades]
            wins = sum(1 for t in trades if t["win"])
            tp = sum(1 for t in trades if t["exit_reason"] == "take_profit")
            rows.append({"pattern": pat, "n": n, "wr": round(wins / n * 100, 1),
                         "avg_ret": round(np.mean(rets), 2),
                         "tp_rate": round(tp / n * 100, 1),
                         "hit_8pct": round(sum(1 for r in rets if r >= 8.0) / n * 100, 1),
                         "avg_peak": round(np.mean([t["peak_return"] for t in trades]), 2),
                         "avg_hold": round(np.mean([t["hold_days"] for t in trades]), 1)})
        return pd.DataFrame(rows).sort_values("hit_8pct", ascending=False) if rows else pd.DataFrame()


def main():
    parser = argparse.ArgumentParser(description="买入-卖出信号回测 v2")
    parser.add_argument("--sample", type=int, default=0)
    parser.add_argument("--target", type=float, default=8.0)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    tp_level = args.target / 100
    tr_level = -(args.target / 100 * 0.6)  # v2: 更宽的移动止盈
    t0 = time.time()

    stock_files = load_stock_files(DAILY_DIR)
    if args.sample > 0 and args.sample < len(stock_files):
        np.random.seed(args.seed)
        stock_files = list(np.random.choice(stock_files, min(args.sample, len(stock_files)), replace=False))
    elif args.sample == 0:
        args.sample = len(stock_files)

    print(f"\n{'═' * 70}")
    print(f"  买入-卖出回测 v2 | 目标 +{int(args.target)}% | {len(stock_files)} 只个股")
    print(f"  止盈 +{int(args.target)}% | 止损 -5% | 移动止盈 -5% | 熊市仅深跌反弹信号")
    print(f"{'═' * 70}")

    bt = BuySellBacktest(take_profit=tp_level, trailing=tr_level)
    total_sig = 0
    for i, fpath in enumerate(stock_files):
        df = load_stock_csv(fpath)
        if df is None or len(df) < 100:
            continue
        try:
            total_sig += bt.run_stock(fpath, df)
        except Exception:
            continue
        if (i + 1) % 500 == 0:
            e = time.time() - t0
            rate = (i + 1) / e if e > 0 else 1
            eta = (len(stock_files) - i - 1) / rate
            print(f"  [{i+1}/{len(stock_files)}] {total_sig}信号 "
                  f"过滤{bt.bear_filtered} | {e:.0f}s | ETA {eta:.0f}s", flush=True)

    elapsed = time.time() - t0
    print(f"\n  ✅ {total_sig}笔交易 | 熊市过滤{bt.bear_filtered}个信号 | {elapsed:.0f}s")

    s = bt.summary()
    print(f"\n{'─' * 60}")
    print(f"  📊 汇总: {s['total_trades']}笔 | 胜率{s['win_rate']}% | 止盈率{s['take_profit_rate']}%")
    print(f"  均值{s['avg_return']}% | 峰值{s['avg_peak_return']}%")
    print(f"  夏普{s['sharpe']} | 盈亏比{s['profit_factor']} | 持仓{s['avg_hold_days']}d")
    print(f"  退出: {s['reason_dist']}")

    ps = bt.pattern_summary(10)
    if not ps.empty:
        print(f"\n{'─' * 60}")
        print(f"  🏆 Top 15 信号")
        print(f"{'─' * 60}")
        for _, row in ps.head(15).iterrows():
            print(f"  {row['pattern'][:50]:<50s} N={row['n']:>4d} WR={row['wr']:>5.1f}% "
                  f"Avg={row['avg_ret']:>+6.2f}% TP={row['tp_rate']:>4.1f}% 8%+={row['hit_8pct']:>4.1f}%")

    # ── 跨周期 ──
    print(f"\n{'─' * 60}")
    print(f"  🔬 跨周期稳定性")
    print(f"{'─' * 60}")
    regime_stats = {}
    for reg, (start, end) in REGIMES.items():
        rt = [t for t in bt.trades
              if len(t["entry_date"]) >= 10 and start <= t["entry_date"][:10] <= end]
        if not rt:
            print(f"  {reg:<12s} 交易=   0")
            regime_stats[reg] = {"n": 0, "wr": 0, "tp": 0, "avg": 0}
            continue
        n = len(rt); wr = sum(1 for t in rt if t["win"]) / n * 100
        tp = sum(1 for t in rt if t["exit_reason"] == "take_profit") / n * 100
        avg = float(np.mean([t["return_pct"] for t in rt]))
        peak = float(np.mean([t["peak_return"] for t in rt]))
        marker = " ✅" if tp >= 25 else (" ⚠" if tp >= 15 else " ❌")
        print(f"  {reg:<12s} N={n:>4d} WR={wr:>5.1f}% TP={tp:>4.1f}% Avg={avg:>+5.2f}% Peak={peak:>+5.2f}%{marker}")
        regime_stats[reg] = {"n": n, "wr": round(wr, 1), "tp": round(tp, 1), "avg": round(avg, 2)}

    valid = [(r["wr"], r["tp"], r["avg"]) for r in regime_stats.values() if r["n"] >= 10]
    if valid:
        wr_vals = [v[0] for v in valid]; tp_vals = [v[1] for v in valid]
        wr_mean = round(np.mean(wr_vals), 1); tp_mean = round(np.mean(tp_vals), 1)
        wr_std = round(float(np.std(wr_vals)), 1)
        stability = round((wr_mean + tp_mean) / 2 - wr_std, 1)
    else:
        wr_mean = tp_mean = wr_std = stability = 0

    print(f"\n{'─' * 60}")
    print(f"  📈 WR均值={wr_mean}% TP均值={tp_mean}% WR波动={wr_std}%")
    print(f"  稳定性评分: {stability}/100")

    # ── 最终判定 ──
    all_above_20tp = all(r["tp"] >= 20 for r in regime_stats.values() if r["n"] >= 10)
    min_tp = min((r["tp"] for r in regime_stats.values() if r["n"] >= 10), default=0)

    if stability >= 55 and all_above_20tp:
        print(f"\n  ✅✅ 系统达标 — 所有周期止盈率≥20%，综合评分{stability}/100")
        print(f"  可在任何市场环境稳定获利 8%+")
    elif stability >= 50 and min_tp >= 15:
        print(f"\n  ✅ 系统基本达标 — 最低止盈率{min_tp}%，评分{stability}/100")
    elif stability >= 40:
        print(f"\n  ⚠ 接近达标 — 最弱周期止盈率{min_tp}%，需进一步优化")
    else:
        print(f"\n  ❌ 不达标 — 需重新筛选信号组合")

    print(f"\n{'═' * 70}\n  总耗时 {elapsed:.0f}s")
    print(f"  熊市过滤: {s.get('bear_filtered', 0)} 个信号被排除")
    print(f"{'═' * 70}")


if __name__ == "__main__":
    main()
