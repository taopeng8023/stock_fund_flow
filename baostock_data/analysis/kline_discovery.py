#!/usr/bin/env python3
"""
K线形态胜率发现引擎 v2 — 确认日机制 + 多条件AND组合 → 迭代至胜率≥85%

核心改进:
    - 形态触发日 T → T+1 确认日（收阳+ 放量+ 不破低）→ T+1 收盘入场
    - 确认日过滤掉假突破，大幅提高胜率
    - 每个形态 5-7 个 AND 条件

用法:
    python kline_discovery.py --target 85 --min-stocks 50 --max-stocks 800 --seed 42
"""
import argparse
import os
import random
import sys
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Tuple, Optional

import numpy as np
import pandas as pd

try:
    from baostock_data.analysis.stock_filter import load_stock_files, print_filter_summary
except ImportError:
    from stock_filter import load_stock_files, print_filter_summary

try:
    from result_store import save_results
    HAS_RESULT_STORE = True
except ImportError:
    HAS_RESULT_STORE = False

warnings.filterwarnings("ignore")

HOLD_PERIODS = [1, 2, 3, 5, 10, 15]
MIN_DAYS = 80


@dataclass
class PatternResult:
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


def load_stock_csv(filepath: str) -> Optional[pd.DataFrame]:
    try:
        df = pd.read_csv(filepath)
        if len(df) < MIN_DAYS:
            return None
        df["日期"] = pd.to_datetime(df["日期"], format="%Y-%m-%d")
        df = df.sort_values("日期").reset_index(drop=True)
        df = df[df["成交量"] > 0].copy()
        return df if len(df) >= MIN_DAYS else None
    except Exception:
        return None


def load_random_stocks(data_dir: str, n: int) -> List[Tuple[str, str, pd.DataFrame]]:
    csv_files = load_stock_files(data_dir)
    if not csv_files:
        return []
    if n >= len(csv_files):
        sample = csv_files
    else:
        sample = random.sample(csv_files, n)
    results = []
    for fp in sample:
        df = load_stock_csv(fp)
        if df is not None:
            code = os.path.splitext(os.path.basename(fp))[0]
            name = df["名称"].iloc[0] if "名称" in df.columns else code
            results.append((code, name, df))
    return results


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df["收盘"]; o = df["开盘"]; h = df["最高"]; l = df["最低"]
    v = df["成交量"]; pc = df["前收盘"]
    n = len(df)

    for w in [5, 10, 20, 60]:
        df[f"ma{w}"] = c.rolling(w, min_periods=w).mean()
    for w in [5, 20]:
        df[f"vol_ma{w}"] = v.rolling(w, min_periods=w).mean()

    df["candle_body"] = c - o
    df["candle_range"] = h - l
    df["body_ratio"] = np.where(df["candle_range"] > 0,
                                np.abs(df["candle_body"]) / df["candle_range"], 0)
    df["upper_shadow"] = np.where(df["candle_range"] > 0,
                                  (h - np.maximum(o, c)) / df["candle_range"], 0)
    df["lower_shadow"] = np.where(df["candle_range"] > 0,
                                  (np.minimum(o, c) - l) / df["candle_range"], 0)
    df["is_yang"] = (c > o).astype(int)
    df["is_yin"] = (c < o).astype(int)
    df["amplitude"] = np.where(pc > 0, df["candle_range"] / pc, 0)
    df["change_pct"] = np.where(pc > 0, (c - pc) / pc, 0)

    df["vol_ratio_vs5"] = np.where(df["vol_ma5"] > 0, v / df["vol_ma5"], 1)
    df["vol_ratio_vs20"] = np.where(df["vol_ma20"] > 0, v / df["vol_ma20"], 1)

    df["ma_bull"] = ((df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"])).astype(int)

    for w in [5, 10, 20, 60]:
        ma_col = f"ma{w}"
        if ma_col in df.columns:
            df[f"dist_ma{w}"] = np.where(df[ma_col].notna() & (df[ma_col] > 0),
                                         (c - df[ma_col]) / df[ma_col], 0)

    for w in [10, 20, 60]:
        df[f"high_{w}d"] = h.rolling(w, min_periods=w).max()
        df[f"low_{w}d"] = l.rolling(w, min_periods=w).min()
        df[f"close_high_{w}d"] = c.rolling(w, min_periods=w).max()
        df[f"close_low_{w}d"] = c.rolling(w, min_periods=w).min()

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

    # Close rank within 20d range
    for j in range(20, n):
        rng = df["close_high_20d"].values[j] - df["close_low_20d"].values[j]
        df.loc[df.index[j], "close_rank_20"] = (
            (c.values[j] - df["close_low_20d"].values[j]) / rng if rng > 0 else 0.5)

    df["volatility_20"] = df["change_pct"].rolling(20, min_periods=20).std()
    return df


def pattern_signal_at(df: pd.DataFrame, i: int) -> Optional[str]:
    """Check if day i triggers any pattern. Return pattern name or None."""
    n = len(df)
    if i < 70 or i >= n - 1:
        return None

    c = df["收盘"].values; o = df["开盘"].values; h = df["最高"].values
    l = df["最低"].values; v = df["成交量"].values; pc = df["前收盘"].values
    is_yang = df["is_yang"].values; is_yin = df["is_yin"].values
    yang_streak = df["yang_streak"].values; yin_streak = df["yin_streak"].values
    vol_r5 = df["vol_ratio_vs5"].values; vol_r20 = df["vol_ratio_vs20"].values
    amp = df["amplitude"].values; body_r = df["body_ratio"].values
    lower_s = df["lower_shadow"].values; upper_s = df["upper_shadow"].values
    ma5 = df["ma5"].values; ma10 = df["ma10"].values
    ma20 = df["ma20"].values; ma60 = df["ma60"].values
    ma_bull = df["ma_bull"].values
    change_pct = df["change_pct"].values
    dist_ma5 = df["dist_ma5"].values; dist_ma10 = df["dist_ma10"].values
    dist_ma20 = df["dist_ma20"].values
    close_rank_20 = df["close_rank_20"].values

    # ── TYPE A: 超跌深度反弹（最容易达85%+的类别） ──

    # A1: 60日跌超35% + 涨停 + 巨量 + 突破MA20
    if (i >= 60 and not pd.isna(df["close_high_60d"].values[i]) and
        c[i] < df["close_high_60d"].values[i] * 0.65 and  # -35% from 60d high
        change_pct[i] > 0.095 and vol_r5[i] > 3.0 and
        not pd.isna(ma20[i]) and c[i] > ma20[i]):
        return "深跌35%_涨停_巨量_突破MA20"

    # A2: 60日跌超25% + 放量阳线+ 突破10日高+ 站上MA10+ 非涨停
    if (i >= 60 and not pd.isna(df["close_high_60d"].values[i]) and
        c[i] < df["close_high_60d"].values[i] * 0.75 and
        is_yang[i] and vol_r5[i] > 2.0 and amp[i] > 0.03 and
        not pd.isna(ma10[i]) and c[i] > ma10[i] and
        i >= 1 and c[i] > h[i-1] and change_pct[i] < 0.09):
        return "深跌25%_放量破高_站上MA10"

    # A3: 5日跌超12% + 长下影线+ 收阳+ 放量+ 低位
    if (i >= 5 and c[i-5] > 0 and (c[i] - c[i-5]) / c[i-5] < -0.12 and
        lower_s[i] > 0.6 and is_yang[i] and vol_r5[i] > 1.5 and
        not pd.isna(close_rank_20[i]) and close_rank_20[i] < 0.25):
        return "急跌12%_长下影_放量收阳_低位"

    # A4: 连阴缩量至地量 + 十字星 + 低位 + 次日收阳确认
    if (i >= 5 and all(is_yin[j] for j in range(i-5, i)) and
        vol_r20[i-1] < 0.3 and body_r[i] < 0.2 and amp[i] < 0.015 and
        not pd.isna(close_rank_20[i]) and close_rank_20[i] < 0.2 and
        i >= 1 and is_yang[i]):  # 今天阳线就是确认
        return "连阴地量_十字星_低位"

    # A5: 四连阴缩量 + 长下影末阴 + 今日阳线高开
    if (i >= 4 and all(is_yin[j] for j in range(i-4, i)) and
        v[i-3] > v[i-2] > v[i-1] and lower_s[i-1] > 0.6 and
        is_yang[i] and o[i] > c[i-1] and amp[i] > 0.015):
        return "四连阴缩量_长下影_次阳高开"

    # ── TYPE B: 均线多头回调（高胜率趋势跟随） ──

    # B1: 强多头 + 回踩MA20 + 极致缩量 + 十字星 + 今日收阳
    if (ma_bull[i] and not pd.isna(ma20[i]) and ma20[i] > 0 and
        np.abs(dist_ma20[i]) < 0.015 and vol_r5[i] < 0.45 and
        body_r[i] < 0.35 and amp[i] < 0.025 and is_yang[i] and
        c[i] > ma20[i]):
        return "强多头_回踩MA20_极致缩量_启稳"

    # B2: 连续5日多头 + 首日缩量阴线 + 不破MA5 + 次日高开
    if (i >= 4 and np.all(ma_bull[i-4:i+1] == 1) and
        vol_r5[i] < 0.5 and is_yin[i] and
        not pd.isna(ma5[i]) and c[i] > ma5[i] * 0.99 and
        c[i] > l[i-1] and i >= 1 and o[i] >= c[i-1] * 0.995):
        return "5日多头_缩量阴_不破MA5_高开"

    # B3: MA20上升 + 二连阴缩量 + 今日首阳 + 量增
    if (i >= 2 and not pd.isna(ma20[i]) and not pd.isna(ma20[i-1]) and
        ma20[i] > ma20[i-1] and c[i] > ma20[i] and is_yang[i] and
        is_yin[i-1] and is_yin[i-2] and
        vol_r5[i-2] < 0.7 and vol_r5[i-1] < 0.7 and vol_r5[i] > vol_r5[i-1]):
        return "MA20上升_二连阴缩_首阳量增"

    # B4: 10日涨>10% + 首次回踩MA10 + 缩量 + 今日收阳
    if (i >= 10 and c[i-10] > 0 and (c[i] - c[i-10]) / c[i-10] > 0.10 and
        not pd.isna(ma10[i]) and ma10[i] > 0 and
        np.abs(dist_ma10[i]) < 0.012 and vol_r5[i] < 0.6 and
        is_yang[i] and c[i] > c[i-1]):
        return "强势股_首踩MA10_缩量收阳"

    # B5: 10日涨>5% + 缩量阴线 + 回踩MA5 + 不破前低
    if (i >= 10 and c[i-10] > 0 and (c[i] - c[i-10]) / c[i-10] > 0.05 and
        is_yin[i] and vol_r5[i] < 0.55 and
        not pd.isna(ma5[i]) and np.abs(dist_ma5[i]) < 0.01 and
        i >= 2 and c[i] > l[i-2]):
        return "偏强股_回踩MA5_缩量阴_不破前低"

    # ── TYPE C: 放量突破（高胜率突破形态） ──

    # C1: 窄幅盘整+ 均线粘合+ 前缩量+ 今日放量阳突破
    if (i >= 5 and np.std(amp[i-5:i]) < 0.01 and
        not pd.isna(ma5[i]) and not pd.isna(ma10[i]) and not pd.isna(ma20[i]) and
        ma5[i] > 0 and
        max(np.abs(ma5[i]-ma10[i]), np.abs(ma10[i]-ma20[i]), np.abs(ma5[i]-ma20[i])) / ma5[i] < 0.015 and
        np.all(vol_r5[i-3:i] < 0.6) and is_yang[i] and vol_r5[i] > 1.8 and
        amp[i] > 0.02):
        return "窄幅均线粘合_缩量后放量突破"

    # C2: 突破60日高 + 放量 + 均线多头 + 非涨停 + 温和涨幅
    if (i >= 60 and not pd.isna(df["high_60d"].values[i]) and
        c[i] > df["high_60d"].values[i] * 1.005 and
        vol_r5[i] > 2.0 and ma_bull[i] and
        change_pct[i] > 0.03 and change_pct[i] < 0.09 and amp[i] > 0.03):
        return "突破60日高_放量_多头_温和"

    # C3: 缩量横盘3日 + 今日放量突破 + 多头 + 破前3日高
    if (i >= 3 and np.std(c[i-3:i]) / np.mean(c[i-3:i]) < 0.015 and
        np.all(vol_r5[i-3:i] < 0.55) and vol_r5[i] > 2.0 and
        is_yang[i] and c[i] > max(h[i-3:i]) and ma_bull[i]):
        return "缩量横盘_放量破前高_多头"

    # ── TYPE D: 反转组合（阳包阴类） ──

    # D1: 启明星：三连阴缩量 + 十字星 + 今日放量阳包回50%
    if (i >= 3 and yin_streak[i-1] >= 3 and v[i-3] > v[i-2] > v[i-1] and
        body_r[i-1] < 0.2 and is_yang[i] and vol_r5[i] > 1.1 and
        c[i] > (o[i-3] + c[i-3]) / 2):
        return "启明星_三连阴缩_十字星_放量阳"

    # D2: 反包：阴包阳→阳包阴 + 放量
    if (i >= 2 and is_yin[i-1] and is_yang[i-2] and
        c[i-1] < o[i-2] and o[i-1] > c[i-2] and  # 阴包阳
        is_yang[i] and c[i] > o[i-1] and o[i] < c[i-1] and  # 阳包阴
        vol_r5[i] > 1.3):
        return "反包_阳包阴包阳_放量"

    # D3: 双针探底 + 低位 + 第二针放量收阳 + 高于前针
    if (i >= 2 and lower_s[i] > 0.55 and lower_s[i-1] > 0.55 and
        not pd.isna(close_rank_20[i]) and close_rank_20[i] < 0.2 and
        is_yang[i] and v[i] > v[i-1] and c[i] > c[i-1]):
        return "双针探底_低位_量增收阳"

    # ── TYPE E: 缺口系列 ──

    # E1: 跳空高开不回补 + 放量 + 多头 + 非高位
    if (i >= 1 and o[i] > pc[i] * 1.02 and l[i] > pc[i] * 1.005 and
        vol_r5[i] > 1.2 and ma_bull[i] and
        not pd.isna(close_rank_20[i]) and close_rank_20[i] < 0.75 and
        amp[i] > 0.02):
        return "跳空不回补_放量_多头_非高位"

    # E2: 跳空缺口三日不补 + 缩量回踩 + 收阳
    if (i >= 3 and l[i-3] > h[i-4] and o[i-3] > c[i-4] * 1.02 and
        all(l[j] > l[i-3] for j in range(i-2, i+1)) and
        vol_r5[i] < 0.65 and is_yang[i] and c[i] > l[i-3] * 1.005):
        return "缺口三日不补_缩量回踩_收阳"

    # ── TYPE F: 涨停后续 ──

    # F1: 涨停次日放量横盘 + 三日缩量 + 多头 + 价在涨停上
    if (i >= 2 and change_pct[i-2] > 0.09 and
        np.abs(change_pct[i-1]) < 0.03 and v[i-1] > df["vol_ma5"].values[i-1] and
        vol_r5[i] < 0.6 and np.abs(change_pct[i]) < 0.02 and
        ma_bull[i] and c[i] > c[i-2]):
        return "涨停_放量横盘_缩量企稳_多头"

    # ── TYPE G: MA金叉系列 ──

    # G1: MA5金叉MA20 + 放量 + 收阳 + MA20走平或向上
    if (i >= 5 and not pd.isna(ma5[i]) and not pd.isna(ma20[i]) and
        not pd.isna(ma5[i-1]) and not pd.isna(ma20[i-1]) and
        ma5[i-1] <= ma20[i-1] and ma5[i] > ma20[i] and
        vol_r5[i] > 1.3 and is_yang[i] and ma20[i] >= ma20[i-5] and
        c[i] > c[i-1]):
        return "MA5金叉MA20_放量_MA20向上"

    # G2: MA5金叉MA10 + 三线收敛 + 放量阳
    if (i >= 1 and not pd.isna(ma5[i]) and not pd.isna(ma10[i]) and
        not pd.isna(ma20[i]) and not pd.isna(ma5[i-1]) and
        not pd.isna(ma10[i-1]) and
        ma5[i-1] <= ma10[i-1] and ma5[i] > ma10[i] and
        ma5[i] > 0 and
        max(np.abs(ma5[i]-ma10[i]), np.abs(ma10[i]-ma20[i]), np.abs(ma5[i]-ma20[i])) / ma5[i] < 0.025 and
        vol_r5[i] > 1.1 and is_yang[i]):
        return "MA5金叉MA10_三线收敛_放量阳"

    # ── TYPE H: 三连阳温和放量 ──

    # H1: 三连阳 + 量递增 + 温和涨幅 + 突破MA20
    if (yang_streak[i] >= 3 and v[i] > v[i-1] > v[i-2] and
        i >= 5 and c[i-5] > 0 and (c[i] - c[i-5]) / c[i-5] < 0.15 and
        not pd.isna(ma20[i]) and c[i] > ma20[i] and c[i-2] <= ma20[i-2]):
        return "三连阳_量递增_温和_突破MA20"

    # ── TYPE I: 趋势持续加速 ──

    # I1: 三日连涨量递增 + 逼近60日高 + 振幅放大
    if (i >= 2 and all(change_pct[j] > 0.015 for j in range(i-2, i+1)) and
        v[i] > v[i-1] > v[i-2] and
        not pd.isna(df["high_60d"].values[i]) and
        c[i] > df["high_60d"].values[i] * 0.97 and amp[i] > 0.03):
        return "三日连涨_量递增_逼60日高"

    # ── TYPE J: 强化组合（多条件叠加，目标胜率85%+） ──

    # J1: 强势回踩MA10 + 缩量 + 收阳 + 10日内涨10%+ + MA20上升 + 低位
    if (i >= 10 and c[i-10] > 0 and (c[i] - c[i-10]) / c[i-10] > 0.10 and
        not pd.isna(ma10[i]) and ma10[i] > 0 and
        np.abs(dist_ma10[i]) < 0.01 and vol_r5[i] < 0.5 and
        is_yang[i] and c[i] > c[i-1] and
        not pd.isna(ma20[i]) and not pd.isna(ma20[i-5]) and ma20[i] > ma20[i-5] and
        not pd.isna(close_rank_20[i]) and close_rank_20[i] < 0.7):
        return "强势回踩MA10_极致缩量_MA20上升_收阳"

    # J2: 强多头+ 连续3日缩量+ 回踩MA5不破+ 今日放量阳包阴
    if (i >= 3 and np.all(ma_bull[i-3:i+1] == 1) and
        v[i-2] < df["vol_ma5"].values[i-2] * 0.6 and
        v[i-1] < df["vol_ma5"].values[i-1] * 0.6 and
        is_yin[i-1] and is_yang[i] and
        c[i] > o[i-1] and o[i] < c[i-1] and
        vol_r5[i] > 1.3 and
        not pd.isna(ma5[i]) and c[i] > ma5[i] and
        c[i-1] > ma5[i-1] * 0.99):
        return "强多头_缩量回踩MA5_放量阳包阴"

    # J3: 深跌反弹型 + 60日跌>30% + 三重确认（下影+放量+突破）
    if (i >= 60 and not pd.isna(df["close_high_60d"].values[i]) and
        c[i] < df["close_high_60d"].values[i] * 0.70 and
        is_yang[i] and lower_s[i] > 0.4 and
        vol_r5[i] > 2.0 and vol_r5[i-1] < 0.7 and
        not pd.isna(close_rank_20[i]) and close_rank_20[i] < 0.15 and
        change_pct[i] > 0.02 and change_pct[i] < 0.09 and
        c[i] > o[i-1]):
        return "深跌30%_地量后放量阳_长下影_低位_破昨高"

    # J4: 三连阳温和放量 + MA20上穿 + 缩量蓄力前 + 中位启动
    if (yang_streak[i] >= 3 and v[i] > v[i-1] > v[i-2] and
        i >= 5 and c[i-5] > 0 and (c[i] - c[i-5]) / c[i-5] < 0.15 and
        not pd.isna(ma20[i]) and c[i] > ma20[i] and
        np.all(vol_r5[i-5:i-3] < 0.6) and  # 前2日缩量蓄力
        not pd.isna(close_rank_20[i]) and 0.2 < close_rank_20[i] < 0.7):
        return "缩量蓄力_三连阳温和放量_站上MA20_中位"

    # J5: 均线多头发散 + 缩量回踩MA10 + 十字星 + 低位翻转
    if (ma_bull[i] and not pd.isna(ma10[i]) and not pd.isna(ma5[i]) and
        ma5[i] > ma10[i] * 1.005 and ma10[i] > ma20[i] and  # 均线发散
        np.abs(dist_ma10[i]) < 0.008 and vol_r5[i] < 0.4 and
        body_r[i] < 0.3 and is_yang[i] and
        c[i] > ma10[i] and amp[i] < 0.025 and
        not pd.isna(close_rank_20[i]) and close_rank_20[i] < 0.5):
        return "均线发散_缩量回踩MA10_小阳启稳"

    # J6: 涨停后第3日缩量回踩 + 不破涨停日低 + MA多头
    if (i >= 3 and change_pct[i-3] > 0.09 and
        np.all(np.abs(change_pct[i-2:i]) < 0.03) and
        vol_r5[i] < 0.5 and l[i] > l[i-3] * 0.98 and
        ma_bull[i] and is_yang[i]):
        return "涨停后3日缩量回踩_不破低_多头"

    return None


def confirm_entry(df: pd.DataFrame, signal_i: int, strict: bool = True,
                  hyper: bool = False, regime: str = "any") -> bool:
    """
    Check if day signal_i+1 (confirmation day) validates the signal.

    strict=True: T+1 close > T high, T+1 yang, T+1 volume > T volume
    hyper=True:  strict + MA20上升 + 价在MA20上 + 波动率低 + 趋势健康
    regime="bull": only trade when MA20 rising AND price above MA20
    regime="any": no regime filter
    """
    n = len(df)
    j = signal_i + 1
    if j >= n:
        return False

    c = df["收盘"].values; h = df["最高"].values
    l = df["最低"].values; o = df["开盘"].values
    v = df["成交量"].values
    is_yang = df["is_yang"].values
    vol_r5 = df["vol_ratio_vs5"].values
    change_pct = df["change_pct"].values
    ma20 = df["ma20"].values
    ma5 = df["ma5"].values
    ma10 = df["ma10"].values
    amp = df["amplitude"].values

    # Basic sanity checks (all modes)
    if o[j] > 0 and (c[j] - o[j]) / o[j] < -0.06:
        return False
    if l[j] < l[signal_i] * 0.97:
        return False
    if vol_r5[j] < 0.25:
        return False

    # Regime filter
    if regime == "bull":
        if signal_i < 5 or pd.isna(ma20[signal_i]) or pd.isna(ma20[signal_i - 5]):
            return False
        if not (ma20[signal_i] > ma20[signal_i - 5]):  # MA20 rising
            return False
        if c[signal_i] < ma20[signal_i]:  # price above MA20
            return False

    # Hyper-strict requirements
    if hyper:
        # All strict requirements
        if c[j] <= h[signal_i]:  # close above signal high
            return False
        if not is_yang[j]:  # yang
            return False
        if v[j] <= v[signal_i]:  # volume expansion
            return False
        # Additional hyper filters
        if signal_i < 5 or pd.isna(ma20[signal_i]) or pd.isna(ma20[signal_i - 5]):
            return False
        if not (ma20[signal_i] > ma20[signal_i - 5]):  # MA20 rising
            return False
        if c[signal_i] < ma20[signal_i]:  # above MA20
            return False
        if v[j] < v[signal_i] * 1.2:  # stronger volume expansion
            return False
        if amp[j] > 0.06:  # not a wild day
            return False
        if signal_i >= 20 and not pd.isna(df["volatility_20"].values[signal_i]):
            if df["volatility_20"].values[signal_i] > 0.05:  # stable environment
                return False
        if pd.isna(ma5[signal_i]) or pd.isna(ma10[signal_i]):
            return False
        if ma5[signal_i] <= ma10[signal_i]:  # MA5 > MA10 (short-term uptrend)
            return False
        return True

    if strict:
        # ULTRA-STRICT
        if c[j] <= h[signal_i]:
            return False
        if not is_yang[j]:
            return False
        if v[j] <= v[signal_i]:
            return False
    else:
        # STANDARD
        if c[j] <= c[signal_i]:
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
             min_stocks: int, max_stocks: int) -> List:
    all_stock_files = load_stock_files(data_dir)
    total_available = len(all_stock_files)
    print(f"数据目录: {data_dir}")
    print_filter_summary(data_dir)
    print(f"策略: 形态触发T → T+1确认日 → T+1收盘入场")
    print()

    batch_sizes = []
    n = min_stocks
    while n <= min(max_stocks, total_available):
        batch_sizes.append(n)
        if n >= 500:
            n += 250
        elif n >= 200:
            n += 100
        else:
            n += 50

    all_batch_results = {}
    accumulated = {}  # (pname, hold) → (wr, total, wins, pname, hold, avg_r)
    found_qualifying = False

    WIN_THRESHOLD = 0.005  # 最小盈利阈值：0.5%，过滤噪音

    for batch_n in batch_sizes:
        print(f"── 批次: {batch_n} 只股票 ──")

        stocks = load_random_stocks(data_dir, batch_n)
        print(f"  有效加载: {len(stocks)} 只", flush=True)

        # Track: standard confirmation, strict confirmation, and pattern combinations
        raw_std: Dict[str, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
        raw_strict: Dict[str, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
        raw_hyper: Dict[str, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
        raw_combo: Dict[str, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))

        total_signals = 0
        for si, (code, name, df) in enumerate(stocks):
            df = compute_indicators(df)
            # 进度（每 50 只）
            if (si + 1) % 50 == 0 or si == 0:
                pct = (si + 1) / len(stocks) * 100
                print(f"  扫描: {si+1}/{len(stocks)} ({pct:.0f}%) 信号:{total_signals}", flush=True)
            n_days = len(df)

            # Collect all signals with their indices
            signals: List[Tuple[int, str]] = []  # [(index, pattern_name), ...]
            for i in range(70, n_days - 2):  # -2 for combo check
                pname = pattern_signal_at(df, i)
                if pname is not None:
                    signals.append((i, pname))

            total_signals += len(signals)
            for sig_i, pname in signals:
                # ── Market regime filter: only trade when MA20 is rising ──
                ma20 = df["ma20"].values
                if sig_i < 5 or pd.isna(ma20[sig_i]) or pd.isna(ma20[sig_i - 5]):
                    continue
                ma20_rising = ma20[sig_i] > ma20[sig_i - 5]
                price_above_ma20 = (df["收盘"].values[sig_i] > ma20[sig_i])

                # ── Standard confirmation ──
                if confirm_entry(df, sig_i, strict=False):
                    entry_idx = sig_i + 1
                    for hold in HOLD_PERIODS:
                        ret = compute_forward_returns(df, entry_idx, hold)
                        if ret is not None:
                            raw_std[pname][hold].append(ret)

                # ── Ultra-strict confirmation ──
                if confirm_entry(df, sig_i, strict=True):
                    entry_idx = sig_i + 1
                    for hold in HOLD_PERIODS:
                        ret = compute_forward_returns(df, entry_idx, hold)
                        if ret is not None:
                            raw_strict[f"▲{pname}"][hold].append(ret)
                            # ── Ultra-strict + bull regime only ──
                            if ma20_rising and price_above_ma20:
                                raw_strict[f"★{pname}"][hold].append(ret)

                # ── Hyper-strict confirmation ──
                if confirm_entry(df, sig_i, hyper=True):
                    entry_idx = sig_i + 1
                    for hold in HOLD_PERIODS:
                        ret = compute_forward_returns(df, entry_idx, hold)
                        if ret is not None:
                            raw_hyper[f"⬡{pname}"][hold].append(ret)

            # ── Pattern combinations (2 patterns within 3 days) ──
            for a_idx in range(len(signals)):
                for b_idx in range(a_idx + 1, len(signals)):
                    sa_i, sa_name = signals[a_idx]
                    sb_i, sb_name = signals[b_idx]
                    if sb_i - sa_i <= 3 and sa_name != sb_name:
                        # Use the later signal for confirmation
                        later_i = sb_i
                        if confirm_entry(df, later_i, strict=True):
                            entry_idx = later_i + 1
                            combo_name = f"◆{sa_name}+{sb_name}"
                            for hold in HOLD_PERIODS:
                                ret = compute_forward_returns(df, entry_idx, hold)
                                if ret is not None:
                                    raw_combo[combo_name][hold].append(ret)

        print(f"  扫描完成: {len(stocks)} 只, 总信号: {total_signals}", flush=True)

        # Compute win rates (with threshold: win if return > WIN_THRESHOLD)
        batch_results = []
        for source_dict, label_prefix in [(raw_std, ""), (raw_strict, "▲/★"), (raw_hyper, "⬡"), (raw_combo, "◆")]:
            for pname, hold_dict in source_dict.items():
                for hold, rets in hold_dict.items():
                    wins = sum(1 for r in rets if r > WIN_THRESHOLD)
                    total = len(rets)
                    wr = wins / total * 100 if total > 0 else 0
                    avg_r = np.mean(rets) * 100
                    if total >= 8:
                        batch_results.append((wr, total, wins, pname, hold, avg_r))

        batch_results.sort(reverse=True)
        # 累积所有批次的形态（去重：同形态+同周期保留最大的 n）
        for wr, total, wins, pname, hold, avg_r in batch_results:
            key = (pname, hold)
            if key not in accumulated or total > accumulated[key][1]:
                accumulated[key] = (wr, total, wins, pname, hold, avg_r)

        # Show top from each category
        shown = 0
        for wr, total, wins, pname, hold, avg_r in batch_results:
            if shown < 20 or wr >= target_win_rate:
                marker = " ★★★ DABIAO!" if wr >= target_win_rate else ""
                print(f"  {pname:45s} T+{hold:<2d}  wr={wr:5.1f}%  "
                      f"n={total:5d}  avgR={avg_r:+6.2f}%{marker}")
                shown += 1
        print()

        qualifying = [(wr, t, w, p, h, ar) for wr, t, w, p, h, ar in batch_results
                      if wr >= target_win_rate and t >= 8]
        if qualifying:
            print(f"✓ 发现 {len(qualifying)} 个达标形态 (胜率≥{target_win_rate}%, 样本≥8)")
            found_qualifying = True
        else:
            print(f"  未发现达标形态，扩大样本...")

    all_batch_results = sorted(accumulated.values(), reverse=True)
    return all_batch_results, found_qualifying


def print_final_report(batch_results, target_win_rate):
    print()
    print("═" * 75)
    print("  最终报告：K线形态胜率发现 v2（含确认日过滤）")
    print("═" * 75)
    print()

    qualifying = [(wr, t, w, p, h, ar) for wr, t, w, p, h, ar in batch_results
                  if wr >= target_win_rate and t >= 8]
    qualifying.sort(reverse=True)

    if qualifying:
        print(f"达标形态 (胜率≥{target_win_rate}%, 样本≥8): {len(qualifying)} 个")
        print()
        print(f"{'形态名称':40s} {'周期':6s} {'胜率':>7s} {'样本':>6s} {'均收益':>8s}")
        print("-" * 75)
        for wr, total, wins, pname, hold, avg_r in qualifying:
            print(f"{pname:40s} T+{hold:<4d} {wr:6.1f}% {total:5d}  {avg_r:+7.2f}%")
    else:
        print(f"未发现达标形态 (胜率≥{target_win_rate}%, 样本≥8)")
        print()
        print("TOP 15 形态 (按胜率):")
        print()
        shown = set()
        count = 0
        for wr, total, wins, pname, hold, avg_r in sorted(batch_results, reverse=True):
            if count >= 15:
                break
            key = pname
            if key not in shown:
                shown.add(key)
                print(f"  {pname:35s} T+{hold:<2d}  胜率={wr:.1f}%  样本={total}  均收={avg_r:+.2f}%")
                count += 1

    print()
    print("═" * 75)

    # ── 结果持久化 ──
    if HAS_RESULT_STORE:
        save_results("kline_discovery", {
            "date": datetime.now().strftime("%Y%m%d"),
            "target_wr": target_win_rate,
            "total_stocks": total_available,
            "qualifying_count": len(qualifying),
            "qualifying": [
                {"name": pname, "hold": int(hold), "wr": float(wr),
                 "n": int(total), "avg_r": float(avg_r)}
                for wr, total, wins, pname, hold, avg_r in qualifying
            ] if qualifying else [],
            "top15": [
                {"name": pname, "hold": int(hold), "wr": float(wr),
                 "n": int(total), "avg_r": float(avg_r)}
                for wr, total, wins, pname, hold, avg_r
                in sorted(batch_results, reverse=True)[:15]
            ] if batch_results else [],
        })


def main():
    parser = argparse.ArgumentParser(description="K线形态胜率发现引擎 v2")
    parser.add_argument("--date", default="", help="数据日期 YYYYMMDD（仅用于输出报告标识）")
    parser.add_argument("--target", type=float, default=85.0, help="目标胜率")
    parser.add_argument("--min-stocks", type=int, default=50, help="起始采样数")
    parser.add_argument("--max-stocks", type=int, default=800, help="最大采样数")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--hold", type=int, default=0, help="指定持仓周期 (0=全部)")
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    baostock_root = os.path.dirname(script_dir)
    data_dir = os.path.join(baostock_root, "data", "daily")

    if not os.path.isdir(data_dir):
        print(f"错误: 数据目录不存在: {data_dir}")
        sys.exit(1)

    global HOLD_PERIODS
    if args.hold > 0:
        HOLD_PERIODS = [args.hold]

    results, found = discover(data_dir, args.target, args.min_stocks, args.max_stocks)
    print_final_report(results, args.target)

    sys.exit(0 if found else 1)


if __name__ == "__main__":
    main()
