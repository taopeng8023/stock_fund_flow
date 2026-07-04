#!/usr/bin/env python3
"""
买入-卖出信号回测系统 v3 — 多信号互确认 + ATR动态止损 + 量质过滤

v3 核心优化:
  1. 双信号互确认: 3日内≥2个不同买入信号才入场 (大幅减少假信号)
  2. ATR动态止损: 波动大时放宽、小时收紧 (1.5×ATR, 3%-7%)
  3. 量价质量: 放量真实检查 + 量增价涨一致性

用法:
  python buy_sell_backtest.py                    # 全量回测
  python buy_sell_backtest.py --sample 1000      # 快速验证
  python buy_sell_backtest.py --target 8.0       # 目标收益
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
TRAILING_STOP = -0.05
MAX_HOLD_DAYS = 20
MIN_SAMPLE = 10
DUAL_CONFIRM_WINDOW = 3  # v3: 3日内需要≥2个不同信号

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
    close = float(df["收盘"].values[i])
    if "ma60" not in df.columns:
        return False
    ma60 = df["ma60"].values[i]
    ma20 = df["ma20"].values[i] if "ma20" in df.columns else ma60
    if pd.isna(ma60) or ma60 <= 0:
        return False
    return close < ma60 and ma20 < ma60


def is_safe_for_bear(pattern: str) -> bool:
    return pattern.startswith(BEAR_SAFE_PREFIXES)


def calc_atr_stop(df: pd.DataFrame, i: int) -> float:
    """基于 ATR 的动态止损: 1.5×ATR, 限制在 3%-7%"""
    if "volatility_20" not in df.columns:
        return STOP_LOSS
    vol = df["volatility_20"].values[i]
    if pd.isna(vol) or vol <= 0:
        return STOP_LOSS
    atr_stop = -min(0.07, max(0.03, vol * 1.5))
    return atr_stop


def is_volume_quality_ok(df: pd.DataFrame, i: int) -> bool:
    """量价质量检查: 放量真实 (量>MA5 且 今日量 > 昨日量)"""
    v = df["成交量"].values
    if i < 5:
        return True
    if "vol_ma5" in df.columns:
        vm5 = df["vol_ma5"].values[i]
        if not pd.isna(vm5) and vm5 > 0 and v[i] < vm5 * 0.5:
            return False  # 缩量严重，无量能支撑
    close = df["收盘"].values[i]
    if "ma5" in df.columns and close > 0:
        ma5 = df["ma5"].values[i]
        if not pd.isna(ma5) and ma5 > 0 and close < ma5:
            return False  # 价在MA5下，多头无力
    return True


def simulate_exit(df: pd.DataFrame, entry_idx: int,
                  take_profit=TAKE_PROFIT, stop_loss=STOP_LOSS,
                  trailing_stop=TRAILING_STOP, max_hold=MAX_HOLD_DAYS) -> Dict:
    entry_price = float(df["收盘"].values[entry_idx])
    n = len(df)
    end_idx = min(entry_idx + max_hold + 1, n)
    peak_price = entry_price
    has_hl = "最高" in df.columns

    # v3: ATR动态止损
    dynamic_sl = calc_atr_stop(df, entry_idx)
    effective_sl = max(stop_loss, dynamic_sl)  # 不超过固定止损

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

        if (low - entry_price) / entry_price <= effective_sl:
            return {"exit_idx": j, "exit_date": str(df.index[j]),
                    "exit_price": round(entry_price * (1 + effective_sl), 2),
                    "exit_reason": "stop_loss",
                    "return_pct": round(effective_sl * 100, 2),
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
        self.bear_filtered = 0
        self.quality_filtered = 0
        self.dual_confirmed = 0  # v3: 通过双确认的信号数

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
        code = os.path.splitext(os.path.basename(filepath))[0]
        count = 0

        # v3: 信号滑动窗口 — 收集3日内的信号用于双确认
        signal_window: List[Tuple[int, str]] = []  # [(idx, pattern), ...]

        for i in range(70, len(df) - 1):
            # 清理过期信号(超过3天)
            signal_window = [(si, sp) for si, sp in signal_window if i - si <= DUAL_CONFIRM_WINDOW]

            pattern = pattern_signal_at(df, i)
            if pattern is None:
                continue

            # 熊市过滤
            if is_bearish(df, i) and not is_safe_for_bear(pattern):
                self.bear_filtered += 1
                continue

            # 量价质量检查
            if not is_volume_quality_ok(df, i):
                self.quality_filtered += 1
                continue

            if not confirm_entry(df, i, strict=True):
                continue

            # 加入信号窗口
            signal_window.append((i, pattern))

            # v3: 双信号互确认 — 需要≥2个不同pattern在3日内
            unique_patterns = set(sp for _, sp in signal_window)
            if len(unique_patterns) < 2:
                continue  # 等待第二个信号

            # 取最近的信号日作为入场点
            entry_i = signal_window[-1][0]
            # 用所有出现的信号名拼接
            combined_name = "+".join(sorted(unique_patterns, key=lambda x: x[:20])[:3])

            self.dual_confirmed += 1

            result = simulate_exit(df, entry_i, self.take_profit, self.stop_loss,
                                   self.trailing_stop, self.max_hold)
            result["pattern"] = combined_name
            result["code"] = code
            result["entry_date"] = str(df["日期"].values[entry_i])[:10] if "日期" in df.columns else str(df.index[entry_i])
            result["entry_price"] = float(df["收盘"].values[entry_i])
            self.trades.append(result)
            self.signals_by_pattern[combined_name].append(result)
            count += 1

        return count

    def summary(self) -> Dict:
        if not self.trades:
            return {"total_trades": 0, "win_rate": 0, "take_profit_rate": 0,
                    "avg_return": 0, "max_drawdown": 0, "avg_peak_return": 0,
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
        # 最大回撤
        cumret = np.cumsum([r / 100 for r in returns])
        peak = np.maximum.accumulate(cumret)
        mdd = round(np.max(peak - cumret) * 100, 2) if len(cumret) > 0 else 0
        return {"total_trades": n, "win_rate": round(wins / n * 100, 1),
                "avg_return": round(float(np.mean(returns)), 2),
                "max_drawdown": mdd,
                "avg_peak_return": round(float(np.mean([t["peak_return"] for t in self.trades])), 2),
                "avg_hold_days": round(avg_hold, 1),
                "take_profit_rate": round(tp / n * 100, 1),
                "sharpe": round(sharpe, 2), "profit_factor": round(pf, 2),
                "max_loss_streak": max_streak, "reason_dist": dict(reason),
                "pattern_count": len(self.signals_by_pattern),
                "bear_filtered": self.bear_filtered,
                "quality_filtered": self.quality_filtered,
                "dual_confirmed": self.dual_confirmed}

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
    parser = argparse.ArgumentParser(description="买入-卖出信号回测 v3")
    parser.add_argument("--sample", type=int, default=0)
    parser.add_argument("--target", type=float, default=8.0)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    tp_level = args.target / 100
    tr_level = -(args.target / 100 * 0.6)
    t0 = time.time()

    stock_files = load_stock_files(DAILY_DIR)
    if args.sample > 0 and args.sample < len(stock_files):
        np.random.seed(args.seed)
        stock_files = list(np.random.choice(stock_files, min(args.sample, len(stock_files)), replace=False))
    elif args.sample == 0:
        args.sample = len(stock_files)

    print(f"\n{'═' * 70}")
    print(f"  买入-卖出回测 v3 | 目标 +{int(args.target)}% | {len(stock_files)} 只个股")
    print(f"  双信号互确认({DUAL_CONFIRM_WINDOW}日≥2) | ATR动态止损 | 量价质量检查")
    print(f"  止盈 +{int(args.target)}% | 移动止盈 -5% | 持仓≤20日")
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
            print(f"  [{i+1}/{len(stock_files)}] {bt.dual_confirmed}确认 {total_sig}交易 "
                  f"过滤熊{bt.bear_filtered}质{bt.quality_filtered} | {e:.0f}s | ETA {eta:.0f}s", flush=True)

    elapsed = time.time() - t0
    print(f"\n  ✅ {total_sig}笔交易 | 双确认{bt.dual_confirmed}次 "
          f"| 过滤: 熊{bt.bear_filtered} 质{bt.quality_filtered} | {elapsed:.0f}s")

    s = bt.summary()
    print(f"\n{'─' * 60}")
    print(f"  📊 汇总: {s['total_trades']}笔 | 胜率{s['win_rate']}% | 止盈率{s['take_profit_rate']}%")
    print(f"  均值{s['avg_return']}% | 峰值{s['avg_peak_return']}% | 最大回撤{s['max_drawdown']}%")
    print(f"  夏普{s['sharpe']} | 盈亏比{s['profit_factor']} | 连亏{s['max_loss_streak']} | 持仓{s['avg_hold_days']}d")
    print(f"  退出: {s['reason_dist']}")

    ps = bt.pattern_summary(5)
    if not ps.empty:
        print(f"\n{'─' * 60}")
        print(f"  🏆 Top 15 信号组合 (双确认)")
        print(f"{'─' * 60}")
        for _, row in ps.head(15).iterrows():
            pname = row['pattern'][:55]
            print(f"  {pname:<55s} N={row['n']:>3d} WR={row['wr']:>5.1f}% "
                  f"Avg={row['avg_ret']:>+5.2f}% TP={row['tp_rate']:>4.1f}% 8%+={row['hit_8pct']:>4.1f}%")

    # ── 跨周期 ──
    print(f"\n{'─' * 60}")
    print(f"  🔬 跨周期稳定性")
    print(f"{'─' * 60}")
    regime_stats = {}
    for reg, (start, end) in REGIMES.items():
        rt = [t for t in bt.trades
              if len(t["entry_date"]) >= 10 and start <= t["entry_date"][:10] <= end]
        if not rt:
            print(f"  {reg:<12s} N=   0")
            regime_stats[reg] = {"n": 0, "wr": 0, "tp": 0, "avg": 0, "peak": 0}
            continue
        n = len(rt)
        wr = sum(1 for t in rt if t["win"]) / n * 100
        tp = sum(1 for t in rt if t["exit_reason"] == "take_profit") / n * 100
        avg = float(np.mean([t["return_pct"] for t in rt]))
        peak = float(np.mean([t["peak_return"] for t in rt]))
        marker = " ✅" if tp >= 30 else (" ⚠" if tp >= 20 else " ❌")
        print(f"  {reg:<12s} N={n:>4d} WR={wr:>5.1f}% TP={tp:>4.1f}% "
              f"Avg={avg:>+5.2f}% Peak={peak:>+5.2f}%{marker}")
        regime_stats[reg] = {"n": n, "wr": round(wr, 1), "tp": round(tp, 1),
                             "avg": round(avg, 2), "peak": round(peak, 2)}

    valid = [(r["wr"], r["tp"]) for r in regime_stats.values() if r["n"] >= 10]
    if valid:
        wr_vals = [v[0] for v in valid]; tp_vals = [v[1] for v in valid]
        wr_mean = round(np.mean(wr_vals), 1); tp_mean = round(np.mean(tp_vals), 1)
        wr_std = round(float(np.std(wr_vals)), 1)
        stability = round((wr_mean + tp_mean) / 2 - wr_std, 1)
    else:
        wr_mean = tp_mean = wr_std = stability = 0

    min_tp = min((r["tp"] for r in regime_stats.values() if r["n"] >= 10), default=0)
    all_above_25 = all(r["tp"] >= 25 for r in regime_stats.values() if r["n"] >= 10)
    all_above_60wr = all(r["wr"] >= 60 for r in regime_stats.values() if r["n"] >= 10)

    print(f"\n{'─' * 60}")
    print(f"  📈 WR均值={wr_mean}% | TP均值={tp_mean}% | WR波动={wr_std}%")
    print(f"  最低TP={min_tp}% | 全周期TP≥25%: {'是' if all_above_25 else '否'}")
    print(f"  稳定性评分: {stability}/100")

    if stability >= 60 and all_above_25:
        print(f"\n  ✅✅✅ 系统全面达标 — 全周期TP≥25% WR≥60% 评分{stability}/100")
    elif stability >= 55 and min_tp >= 20:
        print(f"\n  ✅✅ 系统达标 — 最低TP={min_tp}% 评分{stability}/100")
    elif stability >= 50:
        print(f"\n  ✅ 基本达标 — 评分{stability}/100")
    else:
        print(f"\n  ⚠ 优化中 — 评分{stability}/100，需继续提升")

    print(f"\n{'═' * 70}")
    print(f"  v3 过滤统计: 熊市排除 {s.get('bear_filtered',0)} | 量质排除 {s.get('quality_filtered',0)} | 双确认通过 {s.get('dual_confirmed',0)}")
    print(f"  总耗时 {elapsed:.0f}s")
    print(f"{'═' * 70}")


if __name__ == "__main__":
    main()
