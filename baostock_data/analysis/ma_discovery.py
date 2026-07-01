#!/usr/bin/env python3
"""
MA均线胜率发现引擎 v2 — 确认日机制 + 多条件AND组合 → 目标胜率≥85%

核心改进:
  - 信号触发日 T → T+1 确认日 → T+1 收盘入场
  - 确认日过滤假信号
  - 聚焦 MA 排列/交叉/位置 与 K 线形态的多条件共振

用法:
  python ma_discovery.py --date 20260701 --target 85 --min-stocks 200 --max-stocks 2000 --seed 42
"""
import argparse
import os
import random
import sys
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from glob import glob
from typing import List, Dict, Tuple, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

HOLD_PERIODS = [1, 2, 3, 5, 10, 15]
MIN_DAYS = 60


@dataclass
class SignalResult:
    name: str
    holding_days: int
    total: int = 0
    wins: int = 0
    returns: List[float] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.wins / self.total * 100 if self.total > 0 else 0

    @property
    def avg_return(self) -> float:
        return np.mean(self.returns) * 100 if self.returns else 0

    @property
    def median_return(self) -> float:
        return np.median(self.returns) * 100 if self.returns else 0


def load_stock_csv(filepath: str) -> Optional[pd.DataFrame]:
    try:
        df = pd.read_csv(filepath)
        if len(df) < MIN_DAYS:
            return None
        df.columns = [c.strip().replace('﻿', '') for c in df.columns]
        df["日期"] = pd.to_datetime(df["日期"], format="%Y-%m-%d")
        df = df.sort_values("日期").reset_index(drop=True)
        df = df[df["成交量"] > 0].copy()
        if "是否ST" in df.columns and df["是否ST"].iloc[-1] == 1:
            return None
        return df if len(df) >= MIN_DAYS else None
    except Exception:
        return None


def load_all_stocks(data_dir: str, max_stocks: int) -> List[Tuple[str, str, pd.DataFrame]]:
    csv_files = sorted(glob(os.path.join(data_dir, "sh.*.csv")) +
                       glob(os.path.join(data_dir, "sz.*.csv")))
    if not csv_files:
        return []
    if max_stocks < len(csv_files):
        csv_files = random.sample(csv_files, max_stocks)
    results = []
    for fp in csv_files:
        df = load_stock_csv(fp)
        if df is not None:
            code = os.path.splitext(os.path.basename(fp))[0]
            name = df["名称"].iloc[0] if "名称" in df.columns else code
            results.append((code, name, df))
    return results


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all MA indicators + candle basics."""
    c = df["收盘"]; o = df["开盘"]; h = df["最高"]; l = df["最低"]
    v = df["成交量"]; pc = df["前收盘"]
    n = len(df)

    # MAs
    for w in [5, 10, 15, 20, 60]:
        df[f"ma{w}"] = df["收盘"].rolling(w, min_periods=w).mean()
    for w in [5, 20]:
        df[f"vol_ma{w}"] = df["成交量"].rolling(w, min_periods=w).mean()

    df["is_yang"] = (c > o).astype(int)
    df["is_yin"] = (c < o).astype(int)
    df["change_pct"] = np.where(pc > 0, (c - pc) / pc, 0)
    df["candle_body"] = c - o
    df["candle_range"] = h - l
    df["body_ratio"] = np.where(df["candle_range"] > 0,
                                np.abs(df["candle_body"]) / df["candle_range"], 0)
    df["upper_shadow"] = np.where(df["candle_range"] > 0,
                                  (h - np.maximum(o, c)) / df["candle_range"], 0)
    df["lower_shadow"] = np.where(df["candle_range"] > 0,
                                  (np.minimum(o, c) - l) / df["candle_range"], 0)
    df["amplitude"] = np.where(pc > 0, df["candle_range"] / pc, 0)
    df["vol_ratio_vs5"] = np.where(df["vol_ma5"] > 0, v / df["vol_ma5"], 1)

    # MA alignment
    ma5 = df["ma5"].values; ma10 = df["ma10"].values
    ma15 = df["ma15"].values; ma20 = df["ma20"].values; ma60 = df["ma60"].values

    df["align_4bull"] = ((ma5 > ma10) & (ma10 > ma15) & (ma15 > ma20)).astype(int)
    df["align_3bull"] = ((ma5 > ma10) & (ma10 > ma20)).astype(int)
    df["align_super_bull"] = (df["align_4bull"] & (c > ma60) & (ma20 > ma60)).astype(int)

    # Distance from MAs
    for w in [5, 10, 15, 20]:
        df[f"dist_ma{w}"] = np.where(df[f"ma{w}"].notna() & (df[f"ma{w}"] > 0),
                                     (c - df[f"ma{w}"]) / df[f"ma{w}"], 0)

    # MA slopes (5-day change)
    for w in [5, 10, 15, 20]:
        df[f"slope_{w}"] = df[f"ma{w}"].diff(5) / df[f"ma{w}"].shift(5)

    # MA spread
    for i in range(n):
        if i < 20:
            continue
        mas = [ma5[i], ma10[i], ma15[i], ma20[i]]
        if all(not pd.isna(m) and m > 0 for m in mas):
            df.loc[df.index[i], "ma_spread"] = (max(mas) - min(mas)) / c.values[i]
    df["ma_spread"] = df.get("ma_spread", 0.0).fillna(0.0)

    # Position within range
    for w in [20, 30, 60]:
        df[f"high_{w}d"] = df["最高"].rolling(w, min_periods=w).max()
        df[f"low_{w}d"] = df["最低"].rolling(w, min_periods=w).min()
        df[f"close_high_{w}d"] = df["收盘"].rolling(w, min_periods=w).max()
        df[f"close_low_{w}d"] = df["收盘"].rolling(w, min_periods=w).min()
    for w in [20, 60]:
        hc = df[f"close_high_{w}d"].values
        lc = df[f"close_low_{w}d"].values
        df[f"pos_{w}d"] = np.where(hc - lc > 0, (c - lc) / (hc - lc), 0.5)

    # Cross signals
    for fast, slow in [(5, 10), (5, 20), (10, 20)]:
        fma = df[f"ma{fast}"].values; sma = df[f"ma{slow}"].values
        cross_up = np.zeros(n, dtype=int)
        for i in range(1, n):
            if (not pd.isna(fma[i-1]) and not pd.isna(sma[i-1]) and
                not pd.isna(fma[i]) and not pd.isna(sma[i])):
                if fma[i-1] <= sma[i-1] and fma[i] > sma[i]:
                    cross_up[i] = 1
        df[f"cross_up_{fast}_{slow}"] = cross_up

    # Streaks
    df["yang_streak"] = 0; df["yin_streak"] = 0
    ys = 0; ns = 0
    for i in range(n):
        if df["is_yang"].iloc[i]:
            ys += 1; ns = 0
        else:
            ns += 1; ys = 0
        df.iloc[i, df.columns.get_loc("yang_streak")] = ys
        df.iloc[i, df.columns.get_loc("yin_streak")] = ns

    # Bull days count
    bull4 = df["align_4bull"].values
    bull_days = np.zeros(n, dtype=int)
    cnt = 0
    for i in range(n):
        cnt = cnt + 1 if bull4[i] else 0
        bull_days[i] = cnt
    df["bull4_days"] = bull_days

    df["volatility_20"] = df["change_pct"].rolling(20, min_periods=20).std()
    return df


def signal_at(df: pd.DataFrame, i: int) -> Optional[str]:
    """Signal detection at day T. Returns signal name or None."""
    n = len(df)
    if i < 40 or i >= n - 16:  # need room for T+1 confirm + T+1 entry + T+15 hold
        return None

    c = df["收盘"].values; o = df["开盘"].values
    h = df["最高"].values; l = df["最低"].values
    pc = df["前收盘"].values; v = df["成交量"].values
    is_yang = df["is_yang"].values; is_yin = df["is_yin"].values
    chg = df["change_pct"].values
    vol_r5 = df["vol_ratio_vs5"].values; amp = df["amplitude"].values
    body_r = df["body_ratio"].values; lower_s = df["lower_shadow"].values
    upper_s = df["upper_shadow"].values

    align_4bull = df["align_4bull"].values
    align_3bull = df["align_3bull"].values
    align_super = df["align_super_bull"].values
    bull4_days = df["bull4_days"].values

    dist5 = df["dist_ma5"].values; dist10 = df["dist_ma10"].values
    dist15 = df["dist_ma15"].values; dist20 = df["dist_ma20"].values
    slope5 = df["slope_5"].values; slope10 = df["slope_10"].values
    slope20 = df["slope_20"].values
    spread = df["ma_spread"].values

    pos20 = df["pos_20d"].values; pos60 = df["pos_60d"].values
    vol20 = df["volatility_20"].values

    # ═══════════════════════════════════════════
    # STRICT MA signals — multi-condition AND
    # Each signal: name + all conditions AND'd
    # ═══════════════════════════════════════════

    # --- S1: 超跌深度反弹（验证过的高胜率类别）---

    # S1a: 60日跌超30% + 远离MA20 + 放量阳 + 实体 >50% + 非涨停
    if (i >= 60 and not pd.isna(df["close_high_60d"].values[i]) and
        c[i] < df["close_high_60d"].values[i] * 0.70 and
        not pd.isna(dist20[i]) and dist20[i] < -0.04 and
        is_yang[i] and vol_r5[i] > 1.5 and body_r[i] > 0.5 and chg[i] < 0.09):
        return "S1a_60日跌30%_远MA20_放量实体阳"

    # S1b: 30日跌超20% + 长下影 + 收阳 + 放量 + 低位
    if (i >= 30 and not pd.isna(df["close_high_30d"].values[i]) and
        c[i] < df["close_high_30d"].values[i] * 0.80 and
        lower_s[i] > 0.5 and is_yang[i] and vol_r5[i] > 1.3 and
        not pd.isna(pos60[i]) and pos60[i] < 0.25):
        return "S1b_30日跌20%_长下影_低位放量阳"

    # S1c: 急跌后缩量启稳 + 收复MA5 + 站上MA10 + 非高位
    if (i >= 10 and c[i-10] > 0 and (c[i] - c[i-10]) / c[i-10] < -0.12 and
        vol_r5[i] < 0.6 and not pd.isna(dist5[i]) and dist5[i] > -0.01 and
        not pd.isna(dist10[i]) and dist10[i] > -0.02 and
        not pd.isna(pos60[i]) and pos60[i] < 0.3):
        return "S1c_急跌缩量_收复MA5_近MA10_低位"

    # --- S2: 趋势回踩（多头中的缩量回调）---

    # S2a: 4线多头 + 回踩MA20 + 缩量 + 收阳 + 下影
    if (align_4bull[i] and np.abs(dist20[i]) < 0.015 and
        vol_r5[i] < 0.5 and is_yang[i] and lower_s[i] > 0.3 and
        c[i] > df["ma20"].values[i]):
        return "S2a_完美多头_回踩MA20_缩量下影阳"

    # S2b: 3线多头 + 缩量阴 + 不破MA10 + 近MA10 + 非高位
    if (align_3bull[i] and vol_r5[i] < 0.45 and is_yin[i] and
        dist10[i] > -0.01 and not pd.isna(pos20[i]) and pos20[i] < 0.6):
        return "S2b_3线多头_缩量阴_守MA10_中低位"

    # S2c: 超多头 + 缩量 + 回踩MA5 + 收阳 + 振幅小
    if (align_super[i] and vol_r5[i] < 0.45 and is_yang[i] and
        np.abs(dist5[i]) < 0.008 and amp[i] < 0.02 and c[i] > df["ma5"].values[i]):
        return "S2c_超多头_回踩MA5_缩量小阳"

    # S2d: 4线多头持续5日+ + 缩量 + 阴线 + 不破MA15
    if (align_4bull[i] and bull4_days[i] >= 5 and
        vol_r5[i] < 0.4 and is_yin[i] and dist15[i] > -0.015):
        return "S2d_多头持续_缩量阴_守MA15"

    # --- S3: 金叉共振 ---

    # S3a: MA5金叉MA10 + 站上MA20 + MA20上升 + 放量阳 + 实体
    if (df["cross_up_5_10"].values[i] and dist20[i] > 0 and
        not pd.isna(slope20[i]) and slope20[i] > 0 and vol_r5[i] > 1.2 and
        is_yang[i] and body_r[i] > 0.4):
        return "S3a_MA5金叉MA10_站上MA20_MA20升_放量阳"

    # S3b: MA5金叉MA20 + 站上MA10 + 放量阳 + 非高位
    if (df["cross_up_5_20"].values[i] and dist10[i] > 0 and
        vol_r5[i] > 1.0 and is_yang[i] and not pd.isna(pos60[i]) and pos60[i] < 0.7):
        return "S3b_MA5金叉MA20_站上MA10_放量阳"

    # S3c: MA10金叉MA20 + 收阳 + 放量 + 实体
    if (df["cross_up_10_20"].values[i] and is_yang[i] and vol_r5[i] > 1.1 and
        body_r[i] > 0.4):
        return "S3c_MA10金叉MA20_放量实体阳"

    # --- S4: 均线收敛突破 ---

    # S4a: 均线粘合 + 多头首日 + 放量阳 + 实体 + 振幅大
    if (not pd.isna(spread[i]) and spread[i] < 0.015 and
        bull4_days[i] == 1 and vol_r5[i] > 1.5 and is_yang[i] and
        body_r[i] > 0.5 and amp[i] > 0.02):
        return "S4a_粘合_多头首日_放量实体阳"

    # S4b: 均线粘合 + 收阳 + 放量 + 实体 + 低振幅 (温和突破)
    if (not pd.isna(spread[i]) and spread[i] < 0.012 and is_yang[i] and
        vol_r5[i] > 1.0 and body_r[i] > 0.5 and amp[i] < 0.02):
        return "S4b_粘合_放量温和_实体小阳"

    # S4c: 收敛后MA5向上突破 + 站上全均线 + 放量
    above_all = (dist5[i] > 0 and dist10[i] > 0 and dist15[i] > 0 and dist20[i] > 0)
    if (i >= 1 and df["ma_spread"].values[i-1] < 0.015 and
        not pd.isna(spread[i]) and spread[i] > 0.018 and
        not pd.isna(slope5[i]) and slope5[i] > 0.003 and
        above_all and is_yang[i] and vol_r5[i] > 1.3):
        return "S4c_收敛发散_MA5向上_全均线站上_放量"

    # --- S5: 多头排列+量价 ---

    # S5a: 3线多头 + MA5上升 + 缩量阴 + 不破MA5 + 近MA5
    if (align_3bull[i] and not pd.isna(slope5[i]) and slope5[i] > 0.002 and
        vol_r5[i] < 0.5 and is_yin[i] and np.abs(dist5[i]) < 0.01):
        return "S5a_3线多头_MA5升_缩量阴_守MA5"

    # S5b: 4线多头 + MA20上升 + 首日缩量 + 站上MA5 + 低振幅
    if (align_4bull[i] and not pd.isna(slope20[i]) and slope20[i] > 0.001 and
        vol_r5[i] < 0.5 and is_yang[i] and dist5[i] > 0 and amp[i] < 0.02):
        return "S5b_多头_MA20升_缩量阳_站上MA5"

    # S5c: 4线多头 + MA5加速 + 收阳 + 温和放量 + 中低位
    if (align_4bull[i] and not pd.isna(slope5[i]) and not pd.isna(slope10[i]) and
        slope5[i] > slope10[i] and slope5[i] > 0.004 and is_yang[i] and
        1.0 < vol_r5[i] < 2.5 and not pd.isna(pos60[i]) and pos60[i] < 0.6):
        return "S5c_多头_MA5加速_温和放量_中低"

    # --- S6: 空转多（趋势反转）---

    # S6a: 4线空头转3线多头 + 放量阳 + 实体
    align_score_i = (df["ma5"].values[i] > df["ma10"].values[i] and
                     df["ma10"].values[i] > df["ma20"].values[i] and
                     df["ma20"].values[i] > df["ma60"].values[i])
    align_score_prev = (i >= 1 and
                        df["ma5"].values[i-1] > df["ma10"].values[i-1] and
                        df["ma10"].values[i-1] > df["ma20"].values[i-1] and
                        df["ma20"].values[i-1] > df["ma60"].values[i-1])
    if align_score_i and not align_score_prev and is_yang[i] and vol_r5[i] > 1.5:
        return "S6a_空转多_放量阳"

    # S6b: 4线空头 + 收复MA10 + MA10金叉MA20 + 放量阳
    four_bear = ((df["ma5"].values[i] < df["ma10"].values[i]) and
                 (df["ma10"].values[i] < df["ma15"].values[i]) and
                 (df["ma15"].values[i] < df["ma20"].values[i]))
    if (four_bear and dist10[i] > -0.005 and
        df["cross_up_10_20"].values[i] and is_yang[i] and vol_r5[i] > 1.2):
        return "S6b_空头_收复MA10_金叉MA20_放量阳"

    # --- S7: 极端位置 ---

    # S7a: 60日超低位 + 收阳 + 放量 + 大振幅 + 突破MA5
    if (not pd.isna(pos60[i]) and pos60[i] < 0.15 and is_yang[i] and
        vol_r5[i] > 1.5 and amp[i] > 0.03 and dist5[i] > 0.005):
        return "S7a_60日极低位_放量阳_突破MA5"

    # S7b: 60日超低位 + 十字星 + 缩量 + 次日可能反弹
    if (not pd.isna(pos60[i]) and pos60[i] < 0.12 and
        body_r[i] < 0.2 and vol_r5[i] < 0.4 and amp[i] < 0.015):
        return "S7b_60日极低位_缩量十字"

    # S7c: 远离MA20超卖 + 放量阳 + 站上MA5 + 低振幅
    if (dist20[i] < -0.06 and is_yang[i] and vol_r5[i] > 1.2 and
        dist5[i] > -0.005 and amp[i] < 0.025):
        return "S7c_远MA20超卖_站上MA5_放量阳"

    return None


def confirm_entry(df: pd.DataFrame, sig_i: int, strict: bool = True) -> bool:
    """
    T+1 confirmation check.
    strict=True:  close > signal day high, yang, volume expansion
    strict=False: close > signal day close, yang
    """
    n = len(df)
    j = sig_i + 1
    if j >= n:
        return False

    c = df["收盘"].values; h = df["最高"].values
    l = df["最低"].values; o = df["开盘"].values
    v = df["成交量"].values
    is_yang = df["is_yang"].values
    vol_r5 = df["vol_ratio_vs5"].values

    # Safety filters (both modes)
    if o[j] > 0 and (c[j] - o[j]) / o[j] < -0.05:
        return False
    if l[j] < l[sig_i] * 0.97:
        return False
    if vol_r5[j] < 0.25:
        return False

    if strict:
        if c[j] <= h[sig_i]:
            return False
        if not is_yang[j]:
            return False
        if v[j] <= v[sig_i]:
            return False
    else:
        if c[j] <= c[sig_i]:
            return False

    return True


def compute_forward_returns(df: pd.DataFrame, entry_idx: int,
                            holding_days: int) -> Optional[float]:
    """Entry at close of entry_idx. Return over holding_days."""
    n = len(df)
    c = df["收盘"].values
    exit_idx = entry_idx + holding_days
    if exit_idx < n and c[entry_idx] > 0:
        return (c[exit_idx] - c[entry_idx]) / c[entry_idx]
    return None


def discover(data_dir: str, target_win_rate: float,
             min_stocks: int, max_stocks: int, seed: int) -> Tuple[List, bool]:
    random.seed(seed); np.random.seed(seed)

    print(f"═══ MA均线胜率发现引擎 v2 (确认日机制) ═══")
    print(f"数据目录: {data_dir}")
    print(f"目标胜率: ≥{target_win_rate}%")
    print(f"策略: T触发 → T+1确认 → T+1收盘入场")
    print()

    WIN_THRESHOLD = 0.003

    batch_sizes = []
    n = min_stocks
    while n <= max_stocks:
        batch_sizes.append(n)
        n += 100 if n < 500 else 250

    all_results = []
    found = False

    for batch_n in batch_sizes:
        print(f"── 批次: {batch_n} 只 ──")
        stocks = load_all_stocks(data_dir, batch_n)
        print(f"  有效加载: {len(stocks)} 只")

        raw_std: Dict[str, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
        raw_strict: Dict[str, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))

        for code, name, df in stocks:
            df = compute_indicators(df)
            nd = len(df)

            for i in range(30, nd - 2):
                sig = signal_at(df, i)
                if sig is None:
                    continue

                # Standard confirmation
                if confirm_entry(df, i, strict=False):
                    entry_idx = i + 1
                    for hold in HOLD_PERIODS:
                        ret = compute_forward_returns(df, entry_idx, hold)
                        if ret is not None:
                            raw_std[sig][hold].append(ret)

                # Strict confirmation
                if confirm_entry(df, i, strict=True):
                    entry_idx = i + 1
                    for hold in HOLD_PERIODS:
                        ret = compute_forward_returns(df, entry_idx, hold)
                        if ret is not None:
                            raw_strict[f"▲{sig}"][hold].append(ret)

        # Aggregate
        batch_results = []
        for src, prefix in [(raw_std, ""), (raw_strict, "▲")]:
            for sig, hold_dict in src.items():
                for hold, rets in hold_dict.items():
                    total = len(rets)
                    wins = sum(1 for r in rets if r > WIN_THRESHOLD)
                    wr = wins / total * 100 if total > 0 else 0
                    avg_r = np.mean(rets) * 100
                    med_r = np.median(rets) * 100
                    if total >= 8:
                        batch_results.append((wr, total, wins, sig, hold, avg_r, med_r))

        batch_results.sort(reverse=True)
        all_results = batch_results

        # Show top 30
        shown = 0
        for wr, total, wins, sig, hold, avg_r, med_r in batch_results:
            if shown < 30 or wr >= target_win_rate:
                marker = " ★★★ 达标!" if wr >= target_win_rate else ""
                print(f"  {sig:50s} T+{hold:<2d} wr={wr:5.1f}% n={total:5d}"
                      f" avgR={avg_r:+6.2f}% medR={med_r:+6.2f}%{marker}")
                shown += 1
        print()

        qualifying = [(wr, t, w, s, h, ar, mr) for wr, t, w, s, h, ar, mr in batch_results
                      if wr >= target_win_rate and t >= 15]
        if qualifying:
            print(f"✓ 发现 {len(qualifying)} 个达标信号!")
            found = True
            break
        else:
            print(f"  未达标，扩大样本...")

    return all_results, found


def print_final_report(results, target_win_rate):
    print()
    print("═" * 90)
    print("  最终报告: MA均线 v2 (含确认日过滤)")
    print("═" * 90)
    print()

    qualifying = [(wr, t, w, s, h, ar, mr) for wr, t, w, s, h, ar, mr in results
                  if wr >= target_win_rate and t >= 15]
    qualifying.sort(reverse=True)

    if qualifying:
        print(f"达标信号 (胜率≥{target_win_rate}%, 样本≥15): {len(qualifying)} 个")
        print()
        print(f"{'信号名称':55s} {'持有':5s} {'胜率':>7s} {'样本':>6s} {'均收益':>8s} {'中位':>7s}")
        print("-" * 95)
        for wr, total, wins, sig, hold, avg_r, med_r in qualifying:
            print(f"{sig:55s} T+{hold:<2d}  {wr:5.1f}% {total:5d}  {avg_r:+7.2f}% {med_r:+6.2f}%")
    else:
        print(f"未发现达标信号 (≥{target_win_rate}%, n≥15)")
        print()
        print("TOP 30:")
        shown = set(); count = 0
        for wr, total, wins, sig, hold, avg_r, med_r in sorted(results, reverse=True):
            if count >= 30:
                break
            key = f"{sig}_{hold}"
            if key not in shown:
                shown.add(key)
                print(f"  {sig:55s} T+{hold:<2d} wr={wr:.1f}% n={total} "
                      f"avgR={avg_r:+.2f}% medR={med_r:+.2f}%")
                count += 1

    print()
    print("═" * 90)


def main():
    parser = argparse.ArgumentParser(description="MA均线胜率发现引擎 v2")
    parser.add_argument("--date", default="20260701", help="数据日期 YYYYMMDD")
    parser.add_argument("--target", type=float, default=85.0, help="目标胜率(%)")
    parser.add_argument("--min-stocks", type=int, default=200, help="起始采样数")
    parser.add_argument("--max-stocks", type=int, default=2000, help="最大采样数")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--data-dir", type=str, default="", help="数据目录")
    args = parser.parse_args()

    if args.data_dir:
        data_dir = args.data_dir
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        baostock_root = os.path.dirname(script_dir)
        data_dir = os.path.join(baostock_root, "data", args.date, "daily")

    if not os.path.isdir(data_dir):
        print(f"错误: 数据目录不存在: {data_dir}")
        sys.exit(1)

    results, found = discover(data_dir, args.target, args.min_stocks, args.max_stocks, args.seed)
    print_final_report(results, args.target)
    sys.exit(0 if found else 1)


if __name__ == "__main__":
    main()
