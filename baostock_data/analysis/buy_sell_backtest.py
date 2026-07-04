#!/usr/bin/env python3
"""
买入-卖出信号回测系统 — 目标 8%+ 盈利，跨市场周期稳定

整合 kline_discovery.py 的 35+ K线形态买入信号，添加：
  卖出规则: 止盈 +8% | 止损 -5% | 移动止盈 -3% | 超时 20日
  跨周期验证: 牛/熊/震荡市独立回测 + 稳定性评分

用法:
  python buy_sell_backtest.py                    # 全量回测 + 报告
  python buy_sell_backtest.py --sample 500       # 采样500只快速验证
  python buy_sell_backtest.py --target 8.0       # 目标收益 8%
"""
import argparse
import os
import sys
import warnings
import time
from collections import defaultdict
from typing import Optional, List, Dict, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from stock_filter import load_stock_files
from kline_discovery import compute_indicators, pattern_signal_at, confirm_entry, load_stock_csv

# 直接构建路径，避免触发 baostock_data.__init__ 的 baostock 依赖
DAILY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "baostock_data", "data", "daily"
)

# ============================================================
# 配置
# ============================================================
TAKE_PROFIT = 0.08
STOP_LOSS = -0.05
TRAILING_STOP = -0.03
MAX_HOLD_DAYS = 20
MIN_SAMPLE = 10

REGIMES = {
    "bear_2018":   ("2018-01-01", "2018-12-31"),
    "bull_2019":   ("2019-01-01", "2020-12-31"),
    "range_2021":  ("2021-01-01", "2022-04-30"),
    "bear_2022":   ("2022-05-01", "2022-10-31"),
    "range_2023":  ("2023-01-01", "2024-06-30"),
    "bull_2024":   ("2024-09-01", "2025-06-30"),
}


# ============================================================
# 卖出模拟
# ============================================================
def simulate_exit(
    df: pd.DataFrame, entry_idx: int,
    take_profit: float = TAKE_PROFIT,
    stop_loss: float = STOP_LOSS,
    trailing_stop: float = TRAILING_STOP,
    max_hold: int = MAX_HOLD_DAYS,
) -> Dict:
    """从 entry_idx 模拟卖出，返回最早触发的退出条件。"""
    entry_price = float(df["收盘"].values[entry_idx])
    n = len(df)
    end_idx = min(entry_idx + max_hold + 1, n)
    peak_price = entry_price

    for j in range(entry_idx + 1, end_idx):
        close = float(df["收盘"].values[j])
        high = float(df["最高"].values[j]) if "最高" in df.columns else close
        low = float(df["最低"].values[j]) if "最低" in df.columns else close
        ret = (close - entry_price) / entry_price

        if high > peak_price:
            peak_price = high

        # 1. 止盈
        if ret >= take_profit:
            exit_price = entry_price * (1 + take_profit)
            return {"exit_idx": j, "exit_date": str(df.index[j]),
                    "exit_price": exit_price, "exit_reason": "take_profit",
                    "return_pct": round(take_profit * 100, 2),
                    "peak_return": round((peak_price - entry_price) / entry_price * 100, 2),
                    "hold_days": j - entry_idx, "win": True}

        # 2. 止损（最低价检测）
        low_ret = (low - entry_price) / entry_price
        if low_ret <= stop_loss:
            return {"exit_idx": j, "exit_date": str(df.index[j]),
                    "exit_price": round(entry_price * (1 + stop_loss), 2),
                    "exit_reason": "stop_loss",
                    "return_pct": round(stop_loss * 100, 2),
                    "peak_return": round((peak_price - entry_price) / entry_price * 100, 2),
                    "hold_days": j - entry_idx, "win": False}

        # 3. 移动止盈
        peak_ret = (peak_price - entry_price) / entry_price
        if peak_ret > 0.05:
            drawdown = (close - peak_price) / peak_price
            if drawdown <= trailing_stop:
                return {"exit_idx": j, "exit_date": str(df.index[j]),
                        "exit_price": close, "exit_reason": "trailing_stop",
                        "return_pct": round(ret * 100, 2),
                        "peak_return": round(peak_ret * 100, 2),
                        "hold_days": j - entry_idx, "win": ret > 0}

    # 4. 超时
    last_close = float(df["收盘"].values[end_idx - 1])
    last_ret = (last_close - entry_price) / entry_price
    return {"exit_idx": end_idx - 1, "exit_date": str(df.index[end_idx - 1]),
            "exit_price": last_close, "exit_reason": "timeout",
            "return_pct": round(last_ret * 100, 2),
            "peak_return": round((peak_price - entry_price) / entry_price * 100, 2),
            "hold_days": end_idx - 1 - entry_idx, "win": last_ret > 0}


# ============================================================
# 回测引擎
# ============================================================
class BuySellBacktest:
    """买入-卖出回测引擎"""

    def __init__(self, take_profit=TAKE_PROFIT, stop_loss=STOP_LOSS,
                 trailing=TRAILING_STOP, max_hold=MAX_HOLD_DAYS):
        self.take_profit = take_profit
        self.stop_loss = stop_loss
        self.trailing_stop = trailing
        self.max_hold = max_hold
        self.trades: List[Dict] = []
        self.signals_by_pattern: Dict[str, List] = defaultdict(list)

    def run_stock(self, filepath: str, df: pd.DataFrame,
                  date_range: Optional[Tuple[str, str]] = None) -> int:
        """单只股票回测，返回信号数。"""
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
        for i in range(70, n - 1):
            pattern = pattern_signal_at(df, i)
            if pattern is None:
                continue
            if not confirm_entry(df, i, strict=True):
                continue

            result = simulate_exit(df, i, self.take_profit, self.stop_loss,
                                   self.trailing_stop, self.max_hold)
            result["pattern"] = pattern
            result["code"] = os.path.splitext(os.path.basename(filepath))[0]
            result["entry_date"] = str(df.index[i])
            result["entry_price"] = float(df["收盘"].values[i])
            self.trades.append(result)
            self.signals_by_pattern[pattern].append(result)
            count += 1
        return count

    def summary(self) -> Dict:
        """回测汇总。"""
        if not self.trades:
            return {"total_trades": 0, "message": "无交易信号"}
        n = len(self.trades)
        wins = sum(1 for t in self.trades if t["win"])
        wr = wins / n * 100 if n else 0
        returns = [t["return_pct"] for t in self.trades]
        avg_ret = float(np.mean(returns))
        median_ret = float(np.median(returns))
        avg_peak = float(np.mean([t["peak_return"] for t in self.trades]))
        avg_hold = float(np.mean([t["hold_days"] for t in self.trades]))
        tp_count = sum(1 for t in self.trades if t["exit_reason"] == "take_profit")
        tp_rate = tp_count / n * 100
        reason_dist = defaultdict(int)
        for t in self.trades:
            reason_dist[t["exit_reason"]] += 1
        streak = max_streak = 0
        for t in self.trades:
            streak = streak + 1 if not t["win"] else 0
            max_streak = max(max_streak, streak)
        win_rets = [t["return_pct"] for t in self.trades if t["win"]]
        loss_rets = [abs(t["return_pct"]) for t in self.trades if not t["win"]]
        pf = (np.sum(win_rets) / np.sum(loss_rets)) if loss_rets and np.sum(loss_rets) > 0 else 0
        sharpe = 0.0
        if len(returns) > 1 and np.std(returns) > 0 and avg_hold > 0:
            sharpe = (avg_ret / 100) / (np.std(returns) / 100) * np.sqrt(252 / avg_hold)

        return {"total_trades": n, "win_rate": round(wr, 1),
                "avg_return": round(avg_ret, 2), "median_return": round(median_ret, 2),
                "avg_peak_return": round(avg_peak, 2), "avg_hold_days": round(avg_hold, 1),
                "take_profit_rate": round(tp_rate, 1), "sharpe": round(sharpe, 2),
                "profit_factor": round(pf, 2), "max_loss_streak": max_streak,
                "reason_dist": dict(reason_dist), "pattern_count": len(self.signals_by_pattern)}

    def pattern_summary(self, min_samples=MIN_SAMPLE) -> pd.DataFrame:
        """信号模式统计。"""
        rows = []
        for pattern, trades in self.signals_by_pattern.items():
            n = len(trades)
            if n < min_samples:
                continue
            rets = [t["return_pct"] for t in trades]
            wins = sum(1 for t in trades if t["win"])
            tp = sum(1 for t in trades if t["exit_reason"] == "take_profit")
            hit_8 = sum(1 for r in rets if r >= 8.0) / n * 100
            rows.append({"pattern": pattern, "n": n,
                         "wr": round(wins / n * 100, 1),
                         "avg_ret": round(np.mean(rets), 2),
                         "tp_rate": round(tp / n * 100, 1),
                         "hit_8pct": round(hit_8, 1),
                         "avg_peak": round(np.mean([t["peak_return"] for t in trades]), 2),
                         "avg_hold": round(np.mean([t["hold_days"] for t in trades]), 1)})
        return pd.DataFrame(rows).sort_values("hit_8pct", ascending=False) if rows else pd.DataFrame()

    def regime_analysis(self) -> pd.DataFrame:
        """分周期分析。"""
        rows = []
        top_patterns = list(self.signals_by_pattern.keys())[:15]
        for reg, (start, end) in REGIMES.items():
            rt = [t for t in self.trades if start <= t["entry_date"][:10] <= end]
            for p in top_patterns:
                pt = [t for t in rt if t["pattern"] == p]
                if len(pt) >= 5:
                    rows.append({"regime": reg, "pattern": p[:45],
                                 "n": len(pt),
                                 "wr": round(sum(t["win"] for t in pt) / len(pt) * 100, 1),
                                 "avg_ret": round(np.mean([t["return_pct"] for t in pt]), 2),
                                 "tp": round(sum(t["exit_reason"] == "take_profit" for t in pt) / len(pt) * 100, 1)})
        return pd.DataFrame(rows) if rows else pd.DataFrame()


# ============================================================
# 主流程
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="买入-卖出信号回测")
    parser.add_argument("--sample", type=int, default=0, help="采样股票数(0=全量)")
    parser.add_argument("--target", type=float, default=8.0, help="目标收益%")
    parser.add_argument("--min-wr", type=float, default=50, help="最低胜率")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    tp_level = args.target / 100
    tr_level = -(args.target / 100 * 0.4)

    t0 = time.time()
    stock_files = load_stock_files(DAILY_DIR)
    if args.sample > 0 and args.sample < len(stock_files):
        np.random.seed(args.seed)
        stock_files = list(np.random.choice(stock_files, min(args.sample, len(stock_files)), replace=False))

    print(f"\n{'═' * 70}")
    print(f"  买入-卖出回测 | 目标 +{int(args.target)}% | {len(stock_files)} 只个股")
    print(f"  止盈 +{int(args.target)}% | 止损 -5% | 移动止盈 -3% | 持仓≤20日")
    print(f"{'═' * 70}")

    # ── 回测 ──
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
        if (i + 1) % 200 == 0:
            e = time.time() - t0
            rate = (i + 1) / e if e > 0 else 1
            eta = (len(stock_files) - i - 1) / rate
            print(f"  [{i+1}/{len(stock_files)}] {total_sig}信号 | {e:.0f}s | ETA {eta:.0f}s", flush=True)

    elapsed = time.time() - t0
    print(f"\n  ✅ {total_sig}笔交易 | {elapsed:.0f}s")

    # ── 汇总 ──
    s = bt.summary()
    print(f"\n{'─' * 60}")
    print(f"  📊 汇总: {s['total_trades']}笔 | 胜率{s['win_rate']}% | 止盈率{s['take_profit_rate']}%")
    print(f"  均值{s['avg_return']}% | 中位数{s['median_return']}% | 峰值{s['avg_peak_return']}%")
    print(f"  夏普{s['sharpe']} | 盈亏比{s['profit_factor']} | 连亏{s['max_loss_streak']} | 持仓{s['avg_hold_days']}d")
    print(f"  退出: {s['reason_dist']}")

    # ── Top 信号 ──
    ps = bt.pattern_summary(10)
    if not ps.empty:
        print(f"\n{'─' * 60}")
        print(f"  🏆 Top 15 信号 (按8%+止盈率)")
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
        r_bt = BuySellBacktest(take_profit=tp_level, trailing=tr_level)
        for fpath in stock_files:
            df = load_stock_csv(fpath)
            if df is not None and len(df) >= 100:
                try:
                    r_bt.run_stock(fpath, df, date_range=(start, end))
                except Exception:
                    continue
        rs = r_bt.summary()
        regime_stats[reg] = rs
        print(f"  {reg:<12s} 交易={rs.get('total_trades',0):>4d} 胜率={rs.get('win_rate',0):>5.1f}% "
              f"止盈={rs.get('take_profit_rate',0):>5.1f}% 均值={rs.get('avg_return',0):>+5.2f}%")

    # ── 稳定性评分 ──
    valid = [(r["win_rate"], r["take_profit_rate"], r["avg_return"])
             for r in regime_stats.values() if r["total_trades"] >= 20]
    if valid:
        wr_vals = [v[0] for v in valid]
        tp_vals = [v[1] for v in valid]
        ret_vals = [v[2] for v in valid]
        wr_std = float(np.std(wr_vals))
        tp_std = float(np.std(tp_vals))
        stability = (np.mean(wr_vals) + np.mean(tp_vals)) / 2 - wr_std
    else:
        stability = 0
        wr_std = 0
        ret_vals = [0]

    print(f"\n{'─' * 60}")
    print(f"  📈 综合评分: 胜率均值={np.mean(wr_vals) if valid else 0:.1f}% "
          f"止盈均值={np.mean(tp_vals) if valid else 0:.1f}% "
          f"稳定性={100-wr_std:.1f}")
    print(f"  综合评价: {stability:.1f}/100")

    if stability >= 55 and s.get("take_profit_rate", 0) >= 30:
        print(f"\n  ✅ 系统达标: 跨周期稳定止盈 ≥{int(args.target)}%")
        print(f"  建议: 部署到 picks.py scan 流程，每日自动选股")
    elif stability >= 45:
        print(f"\n  ⚠ 系统部分达标，建议优化:")
        print(f"     - 增加 confirm_entry(strict=True) 过滤")
        print(f"     - 聚焦 Top 3 高胜率信号")
    else:
        print(f"\n  ❌ 不达标 — 信号组合需要重新筛选")
        print(f"  建议: 提高止盈目标、限制市场环境、增加多信号确认")

    print(f"\n{'═' * 70}\n  总耗时 {elapsed:.0f}s\n{'═' * 70}")


if __name__ == "__main__":
    main()
