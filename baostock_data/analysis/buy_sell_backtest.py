#!/usr/bin/env python3
"""
买入-卖出信号回测系统 v5 — 三模式: 波段(v3) + 趋势跟踪(trend) + 增强(v5)

v5 增强模式核心改进:
  1. 重新入场: 出场后冷却N日继续扫描,单股可多笔交易
  2. 体制自适应参数: bull/bear/range 不同止盈止损
  3. 信号质量评分: 趋势+量价+位置+形态 综合过滤
  4. 回踩入场: 信号后等1-2日回踩,优化入场价
  5. 灵活确认: 高质量信号放宽 confirm 要求
  6. 双确认窗口: 3日→5日 (扩大有效信号范围)

用法:
  python buy_sell_backtest.py                          # v3 波段模式(默认,全量)
  python buy_sell_backtest.py --v5                     # v5 增强波段模式
  python buy_sell_backtest.py --trend                  # v4 趋势跟踪模式
  python buy_sell_backtest.py --v5 --trend             # v5 增强趋势模式
  python buy_sell_backtest.py --sample 2000            # 采样快速验证
  python buy_sell_backtest.py --main-board             # 仅主板个股
  python buy_sell_backtest.py --min-history 500        # 至少500个交易日
  python buy_sell_backtest.py --date-range 2024-01-01,2024-12-31  # 指定日期回测
  python buy_sell_backtest.py --target 20.0            # 目标收益20%
"""
import argparse, csv, json, os, sys, time, warnings
from collections import defaultdict
from typing import Optional, List, Dict, Tuple
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from stock_filter import load_stock_files, load_main_board_files
from kline_discovery import (compute_indicators, pattern_signal_at, confirm_entry,
                             load_stock_csv, exit_signal_at, exit_signal_confirm)

DAILY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "baostock_data", "data", "daily")
WEEKLY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "baostock_data", "data", "weekly")

# ── v3 波段模式参数 ──
TAKE_PROFIT = 0.08
STOP_LOSS = -0.05
TRAILING_STOP = -0.05
MAX_HOLD_DAYS = 200       # 安全兜底(非信号驱动,仅防僵尸仓)
MIN_SAMPLE = 10
DUAL_CONFIRM_WINDOW = 3

# ── v4 趋势跟踪模式参数 ──
TREND_TAKE_PROFIT = 0.50     # 50% (等于无止盈,几乎不可能触发)
TREND_STOP_LOSS = -0.08      # 宽止损,给趋势空间
TREND_TRAILING = -0.10       # 宽移动止损 (10% from peak)
TREND_MAX_HOLD = 250         # 安全兜底(信号驱动,此值仅防僵尸仓)
TREND_MIN_MOMENTUM = 0.03    # MA20近20日至少涨3%
TREND_TRAIL_START = 0.15     # 峰值15%+才启动移动止损
TREND_MA_EXIT_DAYS = 2       # MA20跌破需连续2日确认
TREND_MIN_SCORE = 0.40       # 趋势评分最低门槛 (放宽)

# ── v5 增强模式参数 ──
V5_DUAL_CONFIRM_WINDOW = 5      # 双确认窗口扩至5日
V5_COOLDOWN_BARS = 3            # 出场后冷却N日再入场 (基础值,实际用动态冷却)
V5_MIN_SIGNAL_SCORE = 0.40      # 信号质量最低分 (bull/range放宽,bear收紧)
V5_HIGH_QUALITY_THRESHOLD = 0.75  # 高于此分: 单信号可入场 (精英级)
V5_STRICT_CONFIRM_THRESHOLD = 0.60  # 低于此分: 必须strict confirm
V5_PULLBACK_MAX_WAIT = 2        # 回踩入场最多等2日
V5_COOLDOWN_AFTER_LOSS = 20     # 亏损出场后更长冷却 (避免连续亏损)
V5_COOLDOWN_AFTER_WIN = 5       # 盈利出场后冷却
V5_MAX_TRADES_PER_STOCK = 20    # 单股最多交易次数 (防过度交易)

# v5 体制门控: 仅在有利体制下交易,跳过熊市/偏熊
V5_ALLOWED_REGIMES = {"bull", "bull_bias", "range"}

# 体制自适应最低评分
REGIME_MIN_SCORE = {
    "bull": 0.40, "bull_bias": 0.45, "range": 0.50,
    "bear_bias": 0.60, "bear": 0.70,
}

# 体制自适应参数 (v5 波段模式)
# bull: 大止盈+小止损 → 让利润奔跑
# bear: 小止盈+紧止损 → 快进快出保本
REGIME_PARAMS_SWING = {
    # tp/sl/trail/max_hold/trail_start (峰值%才启动移动止损)
    "bull":       {"tp": 0.14, "sl": -0.05, "trail": -0.07, "max_hold": 30, "trail_start": 0.08},
    "bull_bias":  {"tp": 0.11, "sl": -0.05, "trail": -0.06, "max_hold": 25, "trail_start": 0.06},
    "range":      {"tp": 0.08, "sl": -0.05, "trail": -0.05, "max_hold": 18, "trail_start": 0.05},
    "bear_bias":  {"tp": 0.06, "sl": -0.05, "trail": -0.05, "max_hold": 12, "trail_start": 0.04},
    "bear":       {"tp": 0.05, "sl": -0.04, "trail": -0.04, "max_hold": 10, "trail_start": 0.03},
}

# 体制自适应参数 (v5 趋势模式)
REGIME_PARAMS_TREND = {
    "bull":       {"tp": 0.60, "sl": -0.10, "trail": -0.12, "max_hold": 300},
    "bull_bias":  {"tp": 0.50, "sl": -0.09, "trail": -0.11, "max_hold": 250},
    "range":      {"tp": 0.40, "sl": -0.08, "trail": -0.10, "max_hold": 200},
    "bear_bias":  {"tp": 0.25, "sl": -0.06, "trail": -0.07, "max_hold": 100},
    "bear":       {"tp": 0.15, "sl": -0.05, "trail": -0.05, "max_hold": 60},
}

# 趋势模式下优先的信号类型 (突破/趋势延续 > 超跌反弹)
TREND_PRIORITY_PATTERNS = (
    "突破60日高", "MA金叉", "MA5金叉", "三连阳", "三日连涨",
    "跳空", "缺口", "强多头", "强势回踩", "涨停后", "缩量横盘_放量破前高",
    "窄幅均线粘合", "强势股_首踩", "均线发散",
)

BEAR_SAFE_PREFIXES = ("深跌", "急跌", "连阴", "启明星", "双针", "反包")

# 周期划分基于实际数据覆盖: 1991~至今, 2010年后样本充足(>400只)
# 2010年前每周期样本<300只, 统计意义弱, 仅做参考
REGIMES = {
    "bear_2010_13":   ("2010-01-01", "2013-12-31"),   # 慢熊 (400-1000只)
    "bull_2014_15":   ("2014-01-01", "2015-06-12"),   # 杠杆牛
    "crash_2015":     ("2015-06-15", "2016-01-31"),   # 股灾
    "bull_2016_17":   ("2016-02-01", "2018-01-31"),   # 慢牛 (800-1500只)
    "bear_2018":      ("2018-02-01", "2018-12-31"),   # 熊市 (1500只)
    "bull_2019_20":   ("2019-01-01", "2020-12-31"),   # 结构牛 (1600-1900只)
    "range_2021":     ("2021-01-01", "2022-04-30"),   # 震荡 (1900只)
    "bear_2022":      ("2022-05-01", "2022-10-31"),   # 熊市
    "bull_2023_24":   ("2023-01-01", "2024-06-30"),   # 修复行情
    "bull_2024_h2":   ("2024-09-01", "2025-03-31"),   # 924行情
    "range_2025":     ("2025-04-01", "2025-08-31"),   # 震荡
    "bull_2025_h2":   ("2025-09-01", "2026-01-31"),   # 年末行情
    "range_2026":     ("2026-02-01", "2026-07-31"),   # 震荡
}

REGIME_LABELS = {
    "bear_2010_13": "🐻",
    "bull_2014_15": "🐂",
    "crash_2015": "💥",
    "bull_2016_17": "🐂",
    "bear_2018": "🐻",
    "bull_2019_20": "🐂",
    "range_2021": "📊",
    "bear_2022": "🐻",
    "bull_2023_24": "🐂",
    "bull_2024_h2": "🐂",
    "range_2025": "📊",
    "bull_2025_h2": "🐂",
    "range_2026": "📊",
}


# ═══════════════════════════════════════════════════════════════
# 通用工具函数
# ═══════════════════════════════════════════════════════════════

def detect_market_regime(df: pd.DataFrame, i: int) -> str:
    """
    检测个股所处的市场体制.
    bull: MA20上升 + 价在MA20上 + MA5>MA10
    bull_bias: MA20上升 + 价在MA20上
    range: 价在MA20附近震荡
    bear_bias: MA20下降 或 价在MA20下
    bear: MA20下降 + 价在MA20和MA60下 + MA60下降
    """
    if i < 60:
        return "range"
    c = df["收盘"].values
    ma5 = df["ma5"].values[i] if "ma5" in df.columns else np.nan
    ma10 = df["ma10"].values[i] if "ma10" in df.columns else np.nan
    ma20 = df["ma20"].values[i] if "ma20" in df.columns else np.nan
    ma60 = df["ma60"].values[i] if "ma60" in df.columns else np.nan
    ma20_20d = df["ma20"].values[i - 20] if "ma20" in df.columns and i >= 20 else np.nan
    ma60_20d = df["ma60"].values[i - 20] if "ma60" in df.columns and i >= 20 else np.nan

    if pd.isna(ma20) or pd.isna(ma60) or ma20 <= 0 or ma60 <= 0:
        return "range"

    ma20_rising = not pd.isna(ma20_20d) and ma20 > ma20_20d
    ma60_rising = not pd.isna(ma60_20d) and ma60 > ma60_20d
    above_ma20 = c[i] > ma20
    above_ma60 = c[i] > ma60
    ma_short_bull = not pd.isna(ma5) and not pd.isna(ma10) and ma5 > ma10

    if ma20_rising and above_ma20 and ma_short_bull:
        return "bull"
    elif ma20_rising and above_ma20:
        return "bull_bias"
    elif (not ma20_rising) and (not above_ma20) and (not above_ma60) and (not ma60_rising):
        return "bear"
    elif (not ma20_rising) or (not above_ma20):
        return "bear_bias"
    else:
        return "range"


def is_bear_regime(df: pd.DataFrame, i: int) -> bool:
    """判断是否处于不适合交易的熊市体制 (熊市+偏熊,但反转型信号豁免)"""
    regime = detect_market_regime(df, i)
    return regime in ("bear", "bear_bias")


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


def calc_atr_stop(df: pd.DataFrame, i: int, min_sl=-0.07, max_sl=-0.03, mult=1.5) -> float:
    """基于 ATR 的动态止损"""
    if "volatility_20" not in df.columns:
        return min_sl
    vol = df["volatility_20"].values[i]
    if pd.isna(vol) or vol <= 0:
        return min_sl
    atr_stop = -min(abs(min_sl), max(abs(max_sl), vol * mult))
    return atr_stop


def is_volume_quality_ok(df: pd.DataFrame, i: int) -> bool:
    """量价质量检查: 放量真实"""
    v = df["成交量"].values
    if i < 5:
        return True
    if "vol_ma5" in df.columns:
        vm5 = df["vol_ma5"].values[i]
        if not pd.isna(vm5) and vm5 > 0 and v[i] < vm5 * 0.5:
            return False
    close = df["收盘"].values[i]
    if "ma5" in df.columns and close > 0:
        ma5 = df["ma5"].values[i]
        if not pd.isna(ma5) and ma5 > 0 and close < ma5:
            return False
    return True


# ═══════════════════════════════════════════════════════════════
# v4 趋势检测
# ═══════════════════════════════════════════════════════════════

def is_strong_trend(df: pd.DataFrame, i: int) -> bool:
    """
    检测个股是否处于强趋势状态.
    条件: MA5 > MA10 > MA20, 价在MA20上, MA20 20日至少涨3%.
    """
    if i < 20:
        return False
    c = df["收盘"].values
    ma5 = df["ma5"].values[i] if "ma5" in df.columns else np.nan
    ma10 = df["ma10"].values[i] if "ma10" in df.columns else np.nan
    ma20 = df["ma20"].values[i] if "ma20" in df.columns else np.nan
    ma20_20d_ago = df["ma20"].values[i - 20] if "ma20" in df.columns else np.nan

    if pd.isna(ma5) or pd.isna(ma10) or pd.isna(ma20) or pd.isna(ma20_20d_ago):
        return False
    if ma20 <= 0:
        return False

    # MA多头排列
    if not (ma5 > ma10 > ma20):
        return False
    # 价在MA20上
    if c[i] < ma20:
        return False
    # MA20上升趋势
    if ma20 <= ma20_20d_ago:
        return False
    # MA20近20日涨幅
    if (ma20 - ma20_20d_ago) / ma20_20d_ago < TREND_MIN_MOMENTUM:
        return False
    return True


def is_weak_trend(df: pd.DataFrame, i: int) -> bool:
    """弱趋势: MA20上升 + 价在MA20上, 不强制MA5>MA10"""
    if i < 20:
        return False
    c = df["收盘"].values
    ma20 = df["ma20"].values[i] if "ma20" in df.columns else np.nan
    ma20_20d_ago = df["ma20"].values[i - 20] if "ma20" in df.columns else np.nan

    if pd.isna(ma20) or pd.isna(ma20_20d_ago) or ma20 <= 0:
        return False
    if c[i] < ma20:
        return False
    if ma20 <= ma20_20d_ago:
        return False
    return True


def is_trend_priority(pattern: str) -> bool:
    """趋势模式偏好: 突破/金叉/连涨 优于 超跌反弹"""
    return any(kw in pattern for kw in TREND_PRIORITY_PATTERNS)


def trend_momentum_score(df: pd.DataFrame, i: int) -> float:
    """趋势动量评分 0-1, 用于筛选最强趋势"""
    if i < 20:
        return 0.0
    score = 0.0
    c = df["收盘"].values
    ma5 = df["ma5"].values[i] if "ma5" in df.columns else np.nan
    ma10 = df["ma10"].values[i] if "ma10" in df.columns else np.nan
    ma20 = df["ma20"].values[i] if "ma20" in df.columns else np.nan
    ma60 = df["ma60"].values[i] if "ma60" in df.columns else np.nan

    # MA多头排列完整度
    if not pd.isna(ma5) and not pd.isna(ma10) and not pd.isna(ma20):
        if ma5 > ma10 > ma20:
            score += 0.25
        elif ma5 > ma10:  # 至少短期多头
            score += 0.10

    # MA60大趋势
    if not pd.isna(ma60) and not pd.isna(ma20) and ma20 > ma60 and c[i] > ma60:
        score += 0.20

    # 价在MA20上方距离合理 (1%-10%)
    if not pd.isna(ma20) and ma20 > 0:
        dist = (c[i] - ma20) / ma20
        if 0.01 < dist < 0.10:
            score += 0.15
        elif 0 < dist <= 0.01:
            score += 0.10

    # 近期涨幅合理性 (5日涨3%-15%, 非过度拉升)
    if i >= 5 and c[i - 5] > 0:
        chg_5d = (c[i] - c[i - 5]) / c[i - 5]
        if 0.03 < chg_5d < 0.15:
            score += 0.20
        elif 0 < chg_5d <= 0.03:
            score += 0.10

    # 量能健康
    if "vol_ma5" in df.columns:
        vr5 = df["vol_ratio_vs5"].values[i] if "vol_ratio_vs5" in df.columns else 1.0
        if 0.8 < vr5 < 2.5:
            score += 0.10

    # 波动率适中
    if "volatility_20" in df.columns:
        vol = df["volatility_20"].values[i]
        if not pd.isna(vol) and 0.015 < vol < 0.05:
            score += 0.10

    return min(score, 1.0)


def detect_trendiness(df: pd.DataFrame, i: int) -> Tuple[bool, float, str]:
    """
    检测趋势质量, 返回 (是否适合趋势跟踪, 趋势评分, 趋势等级).
    trendiness >= 0.5 → strong trend entry
    trendiness >= 0.3 → weak trend entry (needs hyper confirm)
    """
    if i < 20:
        return False, 0.0, "none"

    score = trend_momentum_score(df, i)
    is_strong = is_strong_trend(df, i)
    is_weak = is_weak_trend(df, i)

    if is_strong and score >= 0.6:
        return True, score, "strong"
    elif is_strong and score >= 0.4:
        return True, score, "good"
    elif is_weak and score >= 0.5:
        return True, score, "weak"
    else:
        return False, score, "none"


# ═══════════════════════════════════════════════════════════════
# v5 信号质量评分 & 回踩入场
# ═══════════════════════════════════════════════════════════════

def calc_signal_quality(df: pd.DataFrame, i: int, pattern: str, regime: str) -> float:
    """
    v5 信号质量综合评分 0-1.
    维度: 趋势对齐(0-0.30) + 量价确认(0-0.15) + 位置健康(0-0.15)
         + MA排列(0-0.20) + 形态类型(0-0.10) + 波动率(0-0.10)
    熊市体制自动打折,保护本金.
    """
    if i < 20:
        return 0.0
    score = 0.0
    c = df["收盘"].values

    # 1. 趋势对齐 (0-0.30)
    _, trend_score, _ = detect_trendiness(df, i)
    score += trend_score * 0.30

    # 2. 量价确认 (0-0.15)
    if "vol_ratio_vs5" in df.columns:
        vr = df["vol_ratio_vs5"].values[i]
        if not pd.isna(vr):
            if 1.2 <= vr <= 3.0:
                score += 0.15   # 温和放量,最佳
            elif 0.8 <= vr < 1.2:
                score += 0.08   # 平量,可接受
            elif 3.0 < vr <= 5.0:
                score += 0.05   # 过度放量,减分

    # 3. 位置健康 vs MA20 (0-0.15)
    ma20 = df["ma20"].values[i] if "ma20" in df.columns else np.nan
    if not pd.isna(ma20) and ma20 > 0:
        dist = (c[i] - ma20) / ma20
        if -0.02 <= dist <= 0.03:
            score += 0.15   # 在MA20附近,最佳入场区
        elif 0.03 < dist <= 0.08:
            score += 0.10   # 略高于MA20
        elif -0.05 <= dist < -0.02:
            score += 0.08   # 略低于MA20(超跌反弹)
        elif 0.08 < dist <= 0.15:
            score += 0.04   # 偏离较远,追高风险

    # 4. MA排列质量 (0-0.20)
    ma5 = df["ma5"].values[i] if "ma5" in df.columns else np.nan
    ma10 = df["ma10"].values[i] if "ma10" in df.columns else np.nan
    ma60 = df["ma60"].values[i] if "ma60" in df.columns else np.nan

    if not pd.isna(ma5) and not pd.isna(ma10) and not pd.isna(ma20):
        if ma5 > ma10 > ma20:
            score += 0.20   # 完美多头排列
        elif ma5 > ma10:
            score += 0.12   # 短期多头
        elif c[i] > ma20:
            score += 0.06   # 至少站上MA20

    # MA60大趋势确认
    if not pd.isna(ma60) and not pd.isna(ma20) and ma20 > ma60:
        score += 0.05   # 中长期趋势向上

    # 5. 形态类型 (0-0.10)
    if is_trend_priority(pattern):
        score += 0.10   # 趋势延续型信号
    elif is_safe_for_bear(pattern):
        score += 0.05   # 反转型信号

    # 6. 波动率健康度 (0-0.10)
    if "volatility_20" in df.columns:
        vol = df["volatility_20"].values[i]
        if not pd.isna(vol):
            if 0.015 <= vol <= 0.045:
                score += 0.10   # 适中波动,趋势稳定
            elif 0.045 < vol <= 0.06:
                score += 0.05   # 偏高但可接受

    # 熊市体制信号密度惩罚: 信号过多时降低得分 (避免频繁交易)
    if regime in ("bear", "bear_bias"):
        # 检查近20日信号密度,过多信号说明市场噪音大
        score *= 0.85

    return min(score, 1.0)


def find_pullback_entry(df: pd.DataFrame, signal_i: int, max_wait: int = 2) -> int:
    """
    v5 回踩入场: 信号日后等待1-2日,若回踩则更低价格入场.
    不回踩则信号日收盘价入场. 若跳空>2%则放弃等待.
    返回最优入场 bar index.
    """
    n = len(df)
    close_signal = float(df["收盘"].values[signal_i])
    best_i = signal_i
    best_price = close_signal

    for j in range(signal_i + 1, min(signal_i + max_wait + 1, n - 1)):
        close_j = float(df["收盘"].values[j])

        # 跳空追高>2% → 不等了,用信号日入场
        if close_j > close_signal * 1.02:
            break

        # 回踩 → 更低入场价
        if close_j < best_price:
            best_price = close_j
            best_i = j

    return best_i


# ═══════════════════════════════════════════════════════════════
# v4 趋势跟踪卖出逻辑
# ═══════════════════════════════════════════════════════════════

def simulate_trend_exit(df: pd.DataFrame, entry_idx: int,
                        stop_loss=TREND_STOP_LOSS,
                        trailing_stop=TREND_TRAILING,
                        max_hold=TREND_MAX_HOLD) -> Dict:
    """
    趋势跟踪卖出 v4.1 — Chandelier Exit + MA20双日确认.
    退出优先级:
      1. 止损 (stop_loss) — 任何时候
      2. Chandelier Exit: close < highest_high - 3×ATR(20)
      3. MA20趋势跌破: 连续2日收盘<MA20 + MA20转跌
      4. MA10死叉: MA5<MA10 + close<MA10 (短期趋势确认破坏)
      5. 移动止损: 峰值回落-trailing_stop (仅峰值≥15%后启动)
      6. 超时 (max_hold)
    """
    entry_price = float(df["收盘"].values[entry_idx])
    n = len(df)
    end_idx = min(entry_idx + max_hold + 1, n)
    peak_price = entry_price
    has_hl = "最高" in df.columns
    ma20_below_count = 0  # 连续跌破MA20天数

    warmup_days = 5
    highest_since_entry = entry_price

    for j in range(entry_idx + 1, end_idx):
        close = float(df["收盘"].values[j])
        high = float(df["最高"].values[j]) if has_hl else close
        low = float(df["最低"].values[j]) if has_hl else close
        ret = (close - entry_price) / entry_price

        if high > peak_price:
            peak_price = high
        if close > highest_since_entry:
            highest_since_entry = close

        # 1. 硬止损
        if (low - entry_price) / entry_price <= stop_loss:
            return {"exit_idx": j, "exit_date": str(df.index[j]),
                    "exit_price": round(entry_price * (1 + stop_loss), 2),
                    "exit_reason": "stop_loss",
                    "return_pct": round(stop_loss * 100, 2),
                    "peak_return": round((peak_price - entry_price) / entry_price * 100, 2),
                    "hold_days": j - entry_idx, "win": False}

        # 2. Chandelier Exit (3×ATR trailing from highest close)
        if j - entry_idx > warmup_days and "volatility_20" in df.columns:
            atr = df["volatility_20"].values[j]
            if not pd.isna(atr) and atr > 0:
                chandelier_stop = highest_since_entry * (1 - atr * 4.0)
                if close < chandelier_stop:
                    return {"exit_idx": j, "exit_date": str(df.index[j]),
                            "exit_price": close, "exit_reason": "chandelier_exit",
                            "return_pct": round(ret * 100, 2),
                            "peak_return": round((peak_price - entry_price) / entry_price * 100, 2),
                            "hold_days": j - entry_idx, "win": ret > 0}

        # 3. MA20趋势跌破 (双日确认)
        if j - entry_idx > warmup_days:
            ma20 = float(df["ma20"].values[j]) if "ma20" in df.columns else None
            if ma20 is not None and not pd.isna(ma20) and ma20 > 0:
                if close < ma20:
                    ma20_below_count += 1
                    # MA20是否转跌
                    ma20_5d_ago = float(df["ma20"].values[max(j - 5, 0)]) if "ma20" in df.columns else ma20
                    ma20_declining = ma20 < ma20_5d_ago

                    if ma20_below_count >= TREND_MA_EXIT_DAYS and ma20_declining:
                        return {"exit_idx": j, "exit_date": str(df.index[j]),
                                "exit_price": close, "exit_reason": "ma20_exit",
                                "return_pct": round(ret * 100, 2),
                                "peak_return": round((peak_price - entry_price) / entry_price * 100, 2),
                                "hold_days": j - entry_idx, "win": ret > 0}
                else:
                    ma20_below_count = 0  # 重新站上MA20,重置计数

                # 4. MA10死叉 (短期趋势确认破坏)
                ma10 = float(df["ma10"].values[j]) if "ma10" in df.columns else None
                ma5 = float(df["ma5"].values[j]) if "ma5" in df.columns else None
                if (ma10 is not None and ma5 is not None and
                    close < ma10 and ma5 < ma10 and j - entry_idx > 10):
                    return {"exit_idx": j, "exit_date": str(df.index[j]),
                            "exit_price": close, "exit_reason": "ma10_cross_exit",
                            "return_pct": round(ret * 100, 2),
                            "peak_return": round((peak_price - entry_price) / entry_price * 100, 2),
                            "hold_days": j - entry_idx, "win": ret > 0}

        # 5. 移动止损 (峰值≥TREND_TRAIL_START后启动)
        peak_ret = (peak_price - entry_price) / entry_price
        if peak_ret > TREND_TRAIL_START:
            dd = (close - peak_price) / peak_price
            if dd <= trailing_stop:
                return {"exit_idx": j, "exit_date": str(df.index[j]),
                        "exit_price": close, "exit_reason": "trailing_stop",
                        "return_pct": round(ret * 100, 2),
                        "peak_return": round(peak_ret * 100, 2),
                        "hold_days": j - entry_idx, "win": ret > 0}

        # 6. K线形态出场信号 (入场10日+已盈利5%+)
        if j - entry_idx >= 10 and ret > 0.05:
            exit_sig = exit_signal_at(df, entry_idx, j, entry_price)
            if exit_sig and exit_signal_confirm(df, j):
                return {"exit_idx": j, "exit_date": str(df.index[j]),
                        "exit_price": close,
                        "exit_reason": f"signal_{exit_sig}",
                        "return_pct": round(ret * 100, 2),
                        "peak_return": round((peak_price - entry_price) / entry_price * 100, 2),
                        "hold_days": j - entry_idx, "win": ret > 0}

    # 7. 安全兜底 (仅防僵尸仓, 正常趋势不会超250日)
    last_idx = end_idx - 1
    last_close = float(df["收盘"].values[last_idx])
    last_ret = (last_close - entry_price) / entry_price
    return {"exit_idx": last_idx, "exit_date": str(df.index[last_idx]),
            "exit_price": last_close, "exit_reason": "safety_timeout",
            "return_pct": round(last_ret * 100, 2),
            "peak_return": round((peak_price - entry_price) / entry_price * 100, 2),
            "hold_days": last_idx - entry_idx, "win": last_ret > 0}


# ═══════════════════════════════════════════════════════════════
# 通用卖出逻辑 (v3 波段)
# ═══════════════════════════════════════════════════════════════

def simulate_exit(df: pd.DataFrame, entry_idx: int,
                  take_profit=TAKE_PROFIT, stop_loss=STOP_LOSS,
                  trailing_stop=TRAILING_STOP, max_hold=MAX_HOLD_DAYS,
                  trail_start=0.05) -> Dict:
    entry_price = float(df["收盘"].values[entry_idx])
    n = len(df)
    end_idx = min(entry_idx + max_hold + 1, n)
    peak_price = entry_price
    has_hl = "最高" in df.columns

    dynamic_sl = calc_atr_stop(df, entry_idx)
    effective_sl = max(stop_loss, dynamic_sl)

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
        if peak_ret > trail_start:
            dd = (close - peak_price) / peak_price
            if dd <= trailing_stop:
                return {"exit_idx": j, "exit_date": str(df.index[j]),
                        "exit_price": close, "exit_reason": "trailing_stop",
                        "return_pct": round(ret * 100, 2),
                        "peak_return": round(peak_ret * 100, 2),
                        "hold_days": j - entry_idx, "win": ret > 0}

        # 出场信号: 入场5日+已盈利3%+ → K线形态出场
        if j - entry_idx >= 5 and ret > 0.03:
            exit_sig = exit_signal_at(df, entry_idx, j, entry_price)
            if exit_sig and exit_signal_confirm(df, j):
                return {"exit_idx": j, "exit_date": str(df.index[j]),
                        "exit_price": close,
                        "exit_reason": f"signal_{exit_sig}",
                        "return_pct": round(ret * 100, 2),
                        "peak_return": round((peak_price - entry_price) / entry_price * 100, 2),
                        "hold_days": j - entry_idx, "win": ret > 0}

    last_close = float(df["收盘"].values[end_idx - 1])
    last_ret = (last_close - entry_price) / entry_price
    return {"exit_idx": end_idx - 1, "exit_date": str(df.index[end_idx - 1]),
            "exit_price": last_close, "exit_reason": "safety_timeout",
            "return_pct": round(last_ret * 100, 2),
            "peak_return": round((peak_price - entry_price) / entry_price * 100, 2),
            "hold_days": end_idx - 1 - entry_idx, "win": last_ret > 0}


# ═══════════════════════════════════════════════════════════════
# 回测引擎
# ═══════════════════════════════════════════════════════════════

TRADE_RECORD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "baostock_data", "analysis", "trade_records")


class BuySellBacktest:
    def __init__(self, take_profit=TAKE_PROFIT, stop_loss=STOP_LOSS,
                 trailing=TRAILING_STOP, max_hold=MAX_HOLD_DAYS,
                 trend_mode=False, skip_bear=False, weekly=False,
                 v5_mode=False, label=""):
        self.take_profit = take_profit; self.stop_loss = stop_loss
        self.trailing_stop = trailing; self.max_hold = max_hold
        self.trend_mode = trend_mode
        self.skip_bear = skip_bear
        self.weekly = weekly
        self.v5_mode = v5_mode
        self.label = label
        self.trades: List[Dict] = []
        self.signals_by_pattern: Dict[str, List] = defaultdict(list)
        self.bear_filtered = 0
        self.quality_filtered = 0
        self.trend_filtered = 0
        self.regime_filtered = 0    # 体制过滤
        self.dual_confirmed = 0
        self.v5_score_filtered = 0  # v5 信号评分过滤
        self.v5_reentries = 0       # v5 重新入场计数

    def _run_stock_v5(self, filepath: str, df: pd.DataFrame,
                      date_range: Optional[Tuple[str, str]] = None) -> int:
        """
        v5 增强模式: 重新入场 + 体制自适应 + 信号评分 + 回踩入场 + 灵活确认.
        单只股票可产生多笔非重叠交易,最大化交易日利用率.
        """
        n = len(df)
        if n < 100:
            return 0
        if date_range:
            mask = (df["日期"] >= date_range[0]) & (df["日期"] <= date_range[1])
            df = df[mask].reset_index(drop=True)
            if len(df) < 30:
                return 0

        df = compute_indicators(df)
        code = os.path.splitext(os.path.basename(filepath))[0]
        count = 0

        signal_window: List[Tuple[int, str]] = []
        next_scan_from = 70  # 下次扫描起点
        confirm_window = 8 if self.weekly else V5_DUAL_CONFIRM_WINDOW
        trades_this_stock = 0

        for i in range(70, len(df) - 1):
            # 冷却期跳过
            if i < next_scan_from:
                continue

            # 单股交易上限
            if trades_this_stock >= V5_MAX_TRADES_PER_STOCK:
                break

            # 修剪过期信号
            signal_window = [(si, sp) for si, sp in signal_window if i - si <= confirm_window]

            pattern = pattern_signal_at(df, i)
            if pattern is None:
                continue

            # 检测体制
            regime = detect_market_regime(df, i)

            # v5 体制门控: 仅在有利体制下交易
            if regime not in V5_ALLOWED_REGIMES:
                self.regime_filtered += 1
                continue

            # 熊市过滤 (保留反转型信号)
            if is_bearish(df, i) and not is_safe_for_bear(pattern):
                self.bear_filtered += 1
                continue

            # 体制过滤 (--no-bear) — v5已使用regime gate,此选项无效但保留兼容
            if self.skip_bear and not self.trend_mode:
                if regime in ("bear", "bear_bias") and not is_safe_for_bear(pattern):
                    self.regime_filtered += 1
                    continue

            # ── 趋势模式增强 ──
            if self.trend_mode:
                trend_ok, trend_score, trend_level = detect_trendiness(df, i)

                if trend_score < TREND_MIN_SCORE:
                    self.trend_filtered += 1
                    continue

                if not is_trend_priority(pattern) and trend_level != "strong":
                    self.trend_filtered += 1
                    continue

                # v5: 趋势模式也用信号评分
                sig_score = trend_score  # 趋势模式用趋势评分
                regime_params = REGIME_PARAMS_TREND.get(regime, REGIME_PARAMS_TREND["range"])

                # 确认入场
                if trend_level == "strong":
                    if not confirm_entry(df, i, strict=True):
                        continue
                elif trend_level == "good":
                    if not confirm_entry(df, i, strict=True):
                        continue
                    signal_window.append((i, pattern))
                    unique_patterns = set(sp for _, sp in signal_window)
                    if len(unique_patterns) < 2:
                        continue
                elif trend_level == "weak":
                    if not confirm_entry(df, i, strict=False):
                        continue
                    signal_window.append((i, pattern))
                    unique_patterns = set(sp for _, sp in signal_window)
                    trend_signals = [sp for sp in unique_patterns if is_trend_priority(sp)]
                    if len(unique_patterns) < 2 or len(trend_signals) < 1:
                        continue

                self.dual_confirmed += 1
                entry_i = signal_window[-1][0] if signal_window and trend_level != "strong" else i
                if trend_level == "strong":
                    entry_i = i

                # v5: 回踩入场
                entry_i = find_pullback_entry(df, entry_i, V5_PULLBACK_MAX_WAIT)

                result = simulate_trend_exit(df, entry_i,
                                             regime_params.get("sl", self.stop_loss),
                                             regime_params.get("trail", self.trailing_stop),
                                             regime_params.get("max_hold", self.max_hold))
                result["trend_score"] = round(sig_score, 2)
                result["trend_level"] = trend_level
            else:
                # ── v5 波段模式增强 ──
                # 体制自适应参数
                params = REGIME_PARAMS_SWING.get(regime, REGIME_PARAMS_SWING["range"])

                # 信号质量评分
                sig_score = calc_signal_quality(df, i, pattern, regime)

                # 体制自适应最低评分: 熊市要求更高信号质量
                regime_min = REGIME_MIN_SCORE.get(regime, V5_MIN_SIGNAL_SCORE)
                if sig_score < regime_min:
                    self.v5_score_filtered += 1
                    continue

                # 量价质量
                if not is_volume_quality_ok(df, i):
                    self.quality_filtered += 1
                    continue

                # 确认入场: 精英级非严格确认, 高分严格确认, 其余拒绝
                if sig_score >= V5_HIGH_QUALITY_THRESHOLD:
                    # 精英级信号: 非严格确认即可
                    if not confirm_entry(df, i, strict=False):
                        continue
                elif sig_score >= V5_STRICT_CONFIRM_THRESHOLD:
                    # 高/中分信号: 严格确认
                    if not confirm_entry(df, i, strict=True):
                        continue
                else:
                    # 低于严格门槛: 拒绝 (regime_min已保证不低于0.40)
                    continue

                signal_window.append((i, pattern))

                # 双确认逻辑: 精英级单信号可入场, 其余需≥2个不同信号
                in_bear = is_bearish(df, i)
                unique_patterns = set(sp for _, sp in signal_window)

                if not self.weekly:
                    if sig_score >= V5_HIGH_QUALITY_THRESHOLD:
                        # 精英级: 单信号即可,但熊市技术面仍需双确认
                        if in_bear and len(unique_patterns) < 2:
                            continue
                    elif not in_bear and len(unique_patterns) < 2:
                        continue
                    # 熊市技术面+反转型信号+双确认满足 → 通过

                self.dual_confirmed += 1

                # v5: 回踩入场
                entry_i = find_pullback_entry(df, signal_window[-1][0], V5_PULLBACK_MAX_WAIT)

                result = simulate_exit(df, entry_i,
                                       params.get("tp", self.take_profit),
                                       params.get("sl", self.stop_loss),
                                       params.get("trail", self.trailing_stop),
                                       params.get("max_hold", self.max_hold),
                                       params.get("trail_start", 0.05))

            # ── 记录交易 ──
            unique_patterns_final = set(sp for _, sp in signal_window) if signal_window else {pattern}
            combined_name = "+".join(sorted(unique_patterns_final, key=lambda x: x[:20])[:3])

            result["pattern"] = combined_name
            result["code"] = code
            entry_date_str = str(df["日期"].values[entry_i])[:10] if "日期" in df.columns else str(df.index[entry_i])
            result["entry_date"] = entry_date_str
            result["entry_price"] = float(df["收盘"].values[entry_i])
            result["entry_regime"] = regime
            result["signal_score"] = round(sig_score, 2)
            result["entry_ma_bull"] = bool(df["ma_bull"].values[entry_i]) if "ma_bull" in df.columns else False
            result["entry_vol_ratio"] = round(float(df["vol_ratio_vs5"].values[entry_i]), 2) if "vol_ratio_vs5" in df.columns else 0

            self.trades.append(result)
            self.signals_by_pattern[combined_name].append(result)

            # ── v5 重新入场: 跳过出场点,清空信号窗,加冷却期 ──
            exit_idx = result["exit_idx"]
            is_loss = not result["win"]
            cooldown = V5_COOLDOWN_AFTER_LOSS if is_loss else V5_COOLDOWN_AFTER_WIN
            next_scan_from = max(exit_idx + cooldown, i + 1)
            signal_window = []
            if count > 0:
                self.v5_reentries += 1
            count += 1
            trades_this_stock += 1

        return count

    def run_stock(self, filepath: str, df: pd.DataFrame,
                  date_range: Optional[Tuple[str, str]] = None) -> int:
        # v5 增强模式路由
        if self.v5_mode:
            return self._run_stock_v5(filepath, df, date_range)

        n = len(df)
        if n < 100:
            return 0
        if date_range:
            mask = (df["日期"] >= date_range[0]) & (df["日期"] <= date_range[1])
            df = df[mask].reset_index(drop=True)
            if len(df) < 30:
                return 0

        df = compute_indicators(df)
        code = os.path.splitext(os.path.basename(filepath))[0]
        count = 0

        signal_window: List[Tuple[int, str]] = []

        for i in range(70, len(df) - 1):
            confirm_window = 8 if self.weekly else DUAL_CONFIRM_WINDOW  # 周线放宽至8周
            signal_window = [(si, sp) for si, sp in signal_window if i - si <= confirm_window]

            pattern = pattern_signal_at(df, i)
            if pattern is None:
                continue

            # 熊市过滤 (两种模式共用)
            if is_bearish(df, i) and not is_safe_for_bear(pattern):
                self.bear_filtered += 1
                continue

            # 体制过滤: 熊市体制不交易非反转型信号 (--no-bear)
            if self.skip_bear and not self.trend_mode:
                if is_bear_regime(df, i) and not is_safe_for_bear(pattern):
                    self.regime_filtered += 1
                    continue

            # ── v4 趋势模式: 强制动量 + 趋势检测 ──
            if self.trend_mode:
                trend_ok, trend_score, trend_level = detect_trendiness(df, i)

                # 趋势评分硬门槛
                if trend_score < TREND_MIN_SCORE:
                    self.trend_filtered += 1
                    continue

                # 必须是趋势型信号 (突破/金叉/连涨)
                if not is_trend_priority(pattern) and trend_level != "strong":
                    self.trend_filtered += 1
                    continue

                # 强趋势: strict confirm; 好趋势: 双确认+strict; 弱趋势: 双确认
                if trend_level == "strong":
                    if not confirm_entry(df, i, strict=True):
                        continue
                elif trend_level == "good":
                    if not confirm_entry(df, i, strict=True):
                        continue
                    signal_window.append((i, pattern))
                    unique_patterns = set(sp for _, sp in signal_window)
                    if len(unique_patterns) < 2:
                        continue
                elif trend_level == "weak":
                    # 弱趋势: 双信号互确认 + 至少一个是趋势型信号
                    if not confirm_entry(df, i, strict=True):
                        continue
                    signal_window.append((i, pattern))
                    unique_patterns = set(sp for _, sp in signal_window)
                    trend_signals = [sp for sp in unique_patterns if is_trend_priority(sp)]
                    if len(unique_patterns) < 2 or len(trend_signals) < 1:
                        continue

                self.dual_confirmed += 1
                entry_i = signal_window[-1][0] if signal_window else i
                if trend_level == "strong":
                    entry_i = i  # 强趋势直接用当日信号

                result = simulate_trend_exit(df, entry_i, self.stop_loss,
                                             self.trailing_stop, self.max_hold)
                result["trend_score"] = round(trend_score, 2)
                result["trend_level"] = trend_level
            else:
                # ── v3 波段模式 ──
                if not is_volume_quality_ok(df, i):
                    self.quality_filtered += 1
                    continue

                if not confirm_entry(df, i, strict=True):
                    continue

                signal_window.append((i, pattern))
                in_bear = is_bearish(df, i)
                unique_patterns = set(sp for _, sp in signal_window)
                # 周线模式放宽: 单信号即可入场 (周线信号稀疏,双确认不可行)
                if not self.weekly and not in_bear and len(unique_patterns) < 2:
                    continue

                self.dual_confirmed += 1
                entry_i = signal_window[-1][0]

                result = simulate_exit(df, entry_i, self.take_profit, self.stop_loss,
                                      self.trailing_stop, self.max_hold)

            # 用所有出现的信号名拼接
            unique_patterns_final = set(sp for _, sp in signal_window) if signal_window else {pattern}
            combined_name = "+".join(sorted(unique_patterns_final, key=lambda x: x[:20])[:3])

            result["pattern"] = combined_name
            result["code"] = code
            entry_date_str = str(df["日期"].values[entry_i])[:10] if "日期" in df.columns else str(df.index[entry_i])
            result["entry_date"] = entry_date_str
            result["entry_price"] = float(df["收盘"].values[entry_i])
            # 记录入场时的市场体制
            result["entry_regime"] = detect_market_regime(df, entry_i)
            result["entry_ma_bull"] = bool(df["ma_bull"].values[entry_i]) if "ma_bull" in df.columns else False
            result["entry_vol_ratio"] = round(float(df["vol_ratio_vs5"].values[entry_i]), 2) if "vol_ratio_vs5" in df.columns else 0
            self.trades.append(result)
            self.signals_by_pattern[combined_name].append(result)
            count += 1

        return count

    def summary(self) -> Dict:
        if not self.trades:
            return {"total_trades": 0, "win_rate": 0, "take_profit_rate": 0,
                    "avg_return": 0, "max_drawdown": 0, "avg_peak_return": 0,
                    "avg_hold_days": 0, "sharpe": 0, "profit_factor": 0,
                    "max_loss_streak": 0, "reason_dist": {}, "pattern_count": 0,
                    "hit_20pct": 0, "hit_10pct": 0}
        n = len(self.trades)
        returns = [t["return_pct"] for t in self.trades]
        wins = sum(1 for t in self.trades if t["win"])
        tp = sum(1 for t in self.trades if t["exit_reason"] == "take_profit")
        ma_exits = sum(1 for t in self.trades if "ma20_exit" in t.get("exit_reason", ""))
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
            sharpe = (np.mean(returns) / 100) / (np.std(returns) / 100) * np.sqrt(252 / max(avg_hold, 1))
        cumret = np.cumsum([r / 100 for r in returns])
        peak = np.maximum.accumulate(cumret)
        mdd = round(np.max(peak - cumret) * 100, 2) if len(cumret) > 0 else 0

        hit_20 = sum(1 for r in returns if r >= 20.0)
        hit_10 = sum(1 for r in returns if r >= 10.0)

        summary = {"total_trades": n, "win_rate": round(wins / n * 100, 1),
                "avg_return": round(float(np.mean(returns)), 2),
                "max_drawdown": mdd,
                "avg_peak_return": round(float(np.mean([t["peak_return"] for t in self.trades])), 2),
                "avg_hold_days": round(avg_hold, 1),
                "take_profit_rate": round(tp / n * 100, 1),
                "hit_20pct": round(hit_20 / n * 100, 1),
                "hit_10pct": round(hit_10 / n * 100, 1),
                "hit_20pct_n": hit_20, "hit_10pct_n": hit_10,
                "sharpe": round(sharpe, 2), "profit_factor": round(pf, 2),
                "max_loss_streak": max_streak, "reason_dist": dict(reason),
                "pattern_count": len(self.signals_by_pattern),
                "bear_filtered": self.bear_filtered,
                "quality_filtered": self.quality_filtered,
                "trend_filtered": self.trend_filtered,
                "regime_filtered": self.regime_filtered,
                "v5_score_filtered": self.v5_score_filtered,
                "v5_reentries": self.v5_reentries,
                "dual_confirmed": self.dual_confirmed}
        return summary

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
                         "hit_20pct": round(sum(1 for r in rets if r >= 20.0) / n * 100, 1),
                         "hit_10pct": round(sum(1 for r in rets if r >= 10.0) / n * 100, 1),
                         "avg_peak": round(np.mean([t["peak_return"] for t in trades]), 2),
                         "avg_hold": round(np.mean([t["hold_days"] for t in trades]), 1)})
        return pd.DataFrame(rows).sort_values("hit_20pct", ascending=False) if rows else pd.DataFrame()

    def run_analysis(self) -> dict:
        """内置分析: 持股天数/出场信号/入场出场配对."""
        if not self.trades:
            return {}
        trades = self.trades

        # ── 持股天数 vs 收益 ──
        hold_buckets = [
            (1, 3, "1-3天"), (4, 5, "4-5天"), (6, 10, "6-10天"),
            (11, 20, "11-20天"), (21, 40, "21-40天"), (41, 60, "41-60天"),
            (61, 100, "61-100天"), (101, 999, "100天+"),
        ]
        hold_analysis = []
        for lo, hi, label in hold_buckets:
            sub = [t for t in trades if lo <= t["hold_days"] <= hi]
            if len(sub) < 3:
                continue
            rets = [t["return_pct"] for t in sub]
            wr = sum(1 for t in sub if t["win"]) / len(sub) * 100
            hit20 = sum(1 for r in rets if r >= 20) / len(sub) * 100
            hold_analysis.append({
                "bucket": label, "n": len(sub), "wr": round(wr, 1),
                "avg_ret": round(np.mean(rets), 2), "hit20": round(hit20, 1),
                "avg_peak": round(np.mean([t["peak_return"] for t in sub]), 2),
                "avg_hold": round(np.mean([t["hold_days"] for t in sub]), 1),
            })

        # ── 出场信号有效性 ──
        def _simplify_exit(reason: str) -> str:
            for k, v in {"take_profit": "止盈30%", "trailing_stop": "移动止损",
                         "stop_loss": "硬止损", "safety_timeout": "安全兜底"}.items():
                if k in reason: return v
            if "射击之星" in reason: return "射击之星"
            if "跌破MA20" in reason: return "跌破MA20"
            if "MA5死叉" in reason: return "MA死叉"
            if "量价背离" in reason: return "量价背离"
            if "看跌吞没" in reason: return "看跌吞没"
            if "缩量滞涨" in reason: return "缩量滞涨"
            if "急涨" in reason: return "急涨低开"
            return "其他"

        exit_groups = defaultdict(list)
        for t in trades:
            exit_groups[_simplify_exit(t.get("exit_reason", "?"))].append(t)
        exit_analysis = []
        for sig, st in exit_groups.items():
            if len(st) < 3:
                continue
            rets = [t["return_pct"] for t in st]
            exit_analysis.append({
                "exit_signal": sig, "n": len(st),
                "avg_ret": round(np.mean(rets), 2),
                "wr": round(sum(1 for t in st if t["win"]) / len(st) * 100, 1),
                "hit20": round(sum(1 for r in rets if r >= 20) / len(st) * 100, 1),
                "avg_peak": round(np.mean([t["peak_return"] for t in st]), 2),
                "avg_hold": round(np.mean([t["hold_days"] for t in st]), 1),
            })
        exit_analysis.sort(key=lambda x: x["avg_ret"], reverse=True)

        # ── 入场×出场配对 ──
        def _simplify_entry(pattern: str) -> str:
            for kw in ["深跌35%", "深跌25%", "MA5金叉MA10", "MA5金叉MA20",
                       "三连阳", "三日连涨", "启明星", "双针探底",
                       "MA20上升", "二连阴缩", "涨停", "放量破高", "回踩MA5"]:
                if kw in pattern: return kw
            return pattern[:20]

        pairs = defaultdict(list)
        for t in trades:
            entry = _simplify_entry(t.get("pattern", "?"))
            exit_s = _simplify_exit(t.get("exit_reason", "?"))
            pairs[f"{entry} → {exit_s}"].append(t)
        pair_analysis = []
        for pk, pt in pairs.items():
            if len(pt) < 3:
                continue
            rets = [t["return_pct"] for t in pt]
            pair_analysis.append({
                "pair": pk, "n": len(pt),
                "avg_ret": round(np.mean(rets), 2),
                "wr": round(sum(1 for t in pt if t["win"]) / len(pt) * 100, 1),
                "hit20": round(sum(1 for r in rets if r >= 20) / len(pt) * 100, 1),
                "hit30": round(sum(1 for r in rets if r >= 30) / len(pt) * 100, 1),
            })
        pair_analysis.sort(key=lambda x: x["avg_ret"], reverse=True)

        return {
            "hold_analysis": hold_analysis,
            "exit_analysis": exit_analysis,
            "pair_analysis": pair_analysis[:20],
        }

    def print_analysis(self, analysis: dict):
        """打印内置分析结果."""
        if not analysis:
            return
        print(f"\n{'='*65}")
        print(f"  📊 内置分析")
        print(f"{'='*65}")

        ha = analysis.get("hold_analysis", [])
        if ha:
            print(f"\n  ── 持股天数 vs 收益率 ──")
            print(f"  {'区间':<10s} {'N':>5s} {'WR':>6s} {'均值':>7s} {'20%+':>6s} {'峰值':>7s} {'持仓':>5s}")
            for r in ha:
                best = " ⭐" if r["avg_ret"] == max(x["avg_ret"] for x in ha) else ""
                print(f"  {r['bucket']:<10s} {r['n']:>5d} {r['wr']:>5.1f}% {r['avg_ret']:>+6.2f}% {r['hit20']:>5.1f}% {r['avg_peak']:>+6.1f}% {r['avg_hold']:>4.1f}d{best}")

        ea = analysis.get("exit_analysis", [])
        if ea:
            print(f"\n  ── 出场信号有效性 ──")
            print(f"  {'出场信号':<12s} {'N':>5s} {'均值':>7s} {'WR':>5s} {'20%+':>6s} {'持仓':>5s}")
            for r in ea[:10]:
                grade = "🏆" if r["avg_ret"] > 10 else ("✅" if r["avg_ret"] > 5 else "👍")
                print(f"  {grade} {r['exit_signal']:<10s} {r['n']:>5d} {r['avg_ret']:>+6.2f}% {r['wr']:>4.1f}% {r['hit20']:>5.1f}% {r['avg_hold']:>4.1f}d")

        pa = analysis.get("pair_analysis", [])
        if pa:
            print(f"\n  ── 最优入场→出场配对 Top 10 ──")
            print(f"  {'入场 → 出场':<35s} {'N':>4s} {'均值':>7s} {'WR':>5s} {'20%+':>6s} {'30%+':>6s}")
            for r in pa[:10]:
                print(f"  {r['pair']:<35s} {r['n']:>4d} {r['avg_ret']:>+6.2f}% {r['wr']:>4.1f}% {r['hit20']:>5.1f}% {r['hit30']:>5.1f}%")

    def save_trades(self, date_range=None, regime_stats=None) -> str:
        """保存交易记录到 JSON + CSV 文件。

        Returns:
            输出目录路径
        """
        from datetime import datetime as dt
        ts = dt.now().strftime("%Y%m%d_%H%M%S")
        if self.v5_mode:
            mode = "v5"
            if self.trend_mode:
                mode = "v5_trend"
        elif self.trend_mode:
            mode = "trend"
        else:
            mode = "swing"
        label_slug = f"_{self.label}" if self.label else ""
        out_dir = os.path.join(TRADE_RECORD_DIR, f"backtest_{mode}{label_slug}_{ts}")
        os.makedirs(out_dir, exist_ok=True)

        if not self.trades:
            print(f"  ⚠ 无交易记录，跳过保存")
            return out_dir

        # ── JSON 导出 (含完整上下文) ──
        summary = self.summary()
        export = {
            "meta": {
                "mode": mode,
                "label": self.label,
                "take_profit": self.take_profit,
                "stop_loss": self.stop_loss,
                "trailing_stop": self.trailing_stop,
                "max_hold": self.max_hold,
                "weekly": self.weekly,
                "skip_bear": self.skip_bear,
                "v5_mode": self.v5_mode,
                "date_range": date_range,
                "generated_at": ts,
            },
            "summary": summary,
            "regime_stats": regime_stats or {},
            "trades": self.trades,
        }
        json_path = os.path.join(out_dir, "trades.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(export, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n  📁 交易记录已保存: {json_path}")

        # ── CSV 导出 (便于 Excel 分析) ──
        csv_path = os.path.join(out_dir, "trades.csv")
        csv_fields = [
            "entry_date", "code", "pattern", "entry_price", "exit_date",
            "exit_price", "exit_reason", "return_pct", "peak_return",
            "hold_days", "win",
        ]
        # v5 专属字段
        if self.v5_mode:
            csv_fields.insert(3, "entry_regime")
            csv_fields.insert(4, "signal_score")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(csv_fields)
            for t in self.trades:
                writer.writerow([t.get(k, "") for k in csv_fields])
        print(f"  📊 CSV 已导出: {csv_path} ({len(self.trades)} 条)")

        # ── 信号组合排名 ──
        rank_path = os.path.join(out_dir, "signal_ranking.csv")
        ps = self.pattern_summary(min_samples=5)
        if not ps.empty:
            ps.to_csv(rank_path, index=False, encoding="utf-8-sig")
            print(f"  🏆 信号排名: {rank_path} ({len(ps)} 个组合)")

        # ── v5 体制分析 ──
        if self.v5_mode and self.trades:
            regime_path = os.path.join(out_dir, "regime_analysis.csv")
            regime_rows = []
            for reg in ["bull", "bull_bias", "range", "bear_bias", "bear"]:
                rt = [t for t in self.trades if t.get("entry_regime") == reg]
                if not rt:
                    continue
                n_rt = len(rt)
                wr = sum(1 for t in rt if t["win"]) / n_rt * 100
                avg_ret = float(np.mean([t["return_pct"] for t in rt]))
                avg_score = float(np.mean([t.get("signal_score", 0) for t in rt]))
                regime_rows.append({
                    "regime": reg, "n": n_rt, "wr": round(wr, 1),
                    "avg_ret": round(avg_ret, 2), "avg_score": round(avg_score, 2),
                    "hit_10pct": round(sum(1 for t in rt if t["return_pct"] >= 10.0) / n_rt * 100, 1),
                })
            if regime_rows:
                pd.DataFrame(regime_rows).to_csv(regime_path, index=False, encoding="utf-8-sig")
                print(f"  📈 体制分析: {regime_path}")

        return out_dir


def big_winner_analysis(bt: BuySellBacktest, target=20.0) -> Dict:
    """大赢家分析: 峰值分布 + 不同止盈阈值效果"""
    if not bt.trades:
        return {}
    peaks = [t["peak_return"] for t in bt.trades]
    rets = [t["return_pct"] for t in bt.trades]
    n = len(bt.trades)

    tier_results = {}
    for tp_pct in [10, 15, 20, 25, 30, 50, 99]:
        tp = tp_pct / 100
        sim_rets = []
        sim_wins = 0
        sim_tp = 0
        for t in bt.trades:
            peak = t["peak_return"] / 100
            actual_ret = t["return_pct"] / 100
            if peak >= tp:
                sim_rets.append(tp)
                sim_wins += 1
                sim_tp += 1
            elif actual_ret > 0:
                sim_rets.append(actual_ret)
                sim_wins += 1
            else:
                sim_rets.append(actual_ret)
        avg = np.mean(sim_rets) * 100 if sim_rets else 0
        wr = sim_wins / n * 100
        tp_rate = sim_tp / n * 100
        tier_results[f"止盈{tp_pct}%"] = {"avg_ret": round(avg, 2), "wr": round(wr, 1), "tp_rate": round(tp_rate, 1)}

    peak_dist = {}
    for th in [5, 10, 15, 20, 25, 30, 50, 100]:
        cnt = sum(1 for p in peaks if p >= th)
        peak_dist[f"≥{th}%"] = {"count": cnt, "pct": round(cnt / n * 100, 1)}

    return {"tier_analysis": tier_results, "peak_distribution": peak_dist,
            "max_peak": round(max(peaks), 1), "avg_peak": round(np.mean(peaks), 2)}


def resample_daily_to_weekly(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """将日线数据重采样为周线, 扩大回测数据维度"""
    if "日期" not in df.columns:
        return None
    df = df.copy()
    df["日期"] = pd.to_datetime(df["日期"])
    df = df.set_index("日期")

    weekly = df.resample("W").agg({
        "开盘": "first", "最高": "max", "最低": "min", "收盘": "last",
        "成交量": "sum", "成交额": "sum",
    })
    weekly["前收盘"] = weekly["收盘"].shift(1)
    weekly = weekly.dropna(subset=["开盘", "收盘"])
    weekly = weekly[weekly["成交量"] > 0]

    if "名称" in df.columns:
        weekly["名称"] = df["名称"].iloc[0]

    weekly = weekly.reset_index()
    weekly["日期"] = weekly["日期"].dt.strftime("%Y-%m-%d")
    return weekly if len(weekly) >= 30 else None


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="买入-卖出信号回测 v5")
    parser.add_argument("--sample", type=int, default=0)
    parser.add_argument("--target", type=float, default=20.0)
    parser.add_argument("--trend", action="store_true", help="v4 趋势跟踪模式")
    parser.add_argument("--v5", action="store_true", help="v5 增强模式 (重新入场+体制自适应+信号评分+回踩入场)")
    parser.add_argument("--weekly", action="store_true", help="周线数据回测 (扩大数据维度)")
    parser.add_argument("--resample-weekly", action="store_true", help="日线重采样为周线回测 (无需下载)")
    parser.add_argument("--no-bear", action="store_true", help="跳过熊市体制 (提高胜率)")
    parser.add_argument("--main-board", action="store_true", help="仅主板个股 (排除科创/创业/北交所)")
    parser.add_argument("--all-stocks", action="store_true", help="全量个股 (含科创/创业/北交所)")
    parser.add_argument("--min-history", type=int, default=200, help="最少交易日数 (默认200)")
    parser.add_argument("--date-range", type=str, default="", help="限定回测日期范围, 如 2024-01-01,2024-12-31")
    parser.add_argument("--analysis", action="store_true", default=True, help="输出内置分析 (默认开启)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    # 解析日期范围
    date_range = None
    if args.date_range:
        parts = args.date_range.split(",")
        if len(parts) == 2:
            date_range = (parts[0].strip(), parts[1].strip())

    t0 = time.time()
    trend_mode = args.trend
    v5_mode = args.v5
    weekly_mode = args.weekly
    resample_mode = args.resample_weekly

    if weekly_mode:
        data_dir = WEEKLY_DIR
        base_max_hold = max(MAX_HOLD_DAYS, int(args.target * 0.7))
        freq_label = "周线"
    elif resample_mode:
        data_dir = DAILY_DIR  # 用日线数据, 运行时重采样
        base_max_hold = max(MAX_HOLD_DAYS, int(args.target * 0.7))
        freq_label = "周线(重采样)"
    else:
        data_dir = DAILY_DIR
        base_max_hold = max(MAX_HOLD_DAYS, int(args.target * 3))
        freq_label = "日线"

    if trend_mode:
        tp_level = TREND_TAKE_PROFIT
        sl_level = TREND_STOP_LOSS
        tr_level = TREND_TRAILING
        max_hold = TREND_MAX_HOLD if not weekly_mode else TREND_MAX_HOLD // 5
        mode_label = f"v4 趋势跟踪({freq_label})"
        if v5_mode:
            mode_label = f"v5 增强趋势({freq_label})"
    else:
        tp_level = args.target / 100
        sl_level = STOP_LOSS
        tr_level = -(args.target / 100 * 0.6)
        max_hold = base_max_hold
        bear_tag = " 跳过熊市" if args.no_bear else ""
        if v5_mode:
            mode_label = f"v5 增强波段({freq_label} 目标{int(args.target)}%{bear_tag})"
        else:
            mode_label = f"v3 波段({freq_label} 目标{int(args.target)}%{bear_tag})"

    # 加载股票文件
    if args.all_stocks:
        stock_files = load_stock_files(data_dir)  # 全量 (含科创/创业/北交所)
    elif args.main_board:
        stock_files = load_main_board_files(data_dir)  # 仅主板
    else:
        stock_files = load_main_board_files(data_dir)  # 默认主板

    # 按最小交易日数过滤 + 日期范围预检查
    min_history = args.min_history
    valid_files = []
    skipped_short = 0
    skipped_date = 0
    for fpath in stock_files:
        df = load_stock_csv(fpath)
        if df is None:
            skipped_short += 1
            continue
        if len(df) < min_history:
            skipped_short += 1
            continue
        if date_range:
            # 检查股票数据是否覆盖目标日期范围
            dates = df["日期"]
            if dates.iloc[0] > pd.Timestamp(date_range[1]) or dates.iloc[-1] < pd.Timestamp(date_range[0]):
                skipped_date += 1
                continue
        valid_files.append(fpath)

    if skipped_short or skipped_date:
        print(f"  过滤: {skipped_short} 只不足{min_history}日, {skipped_date} 只日期不匹配")

    stock_files = valid_files

    if args.sample > 0 and args.sample < len(stock_files):
        np.random.seed(args.seed)
        stock_files = list(np.random.choice(stock_files, min(args.sample, len(stock_files)), replace=False))
    elif args.sample == 0:
        args.sample = len(stock_files)

    max_hold_desc = f"{max_hold}周" if weekly_mode else f"{max_hold}日"
    board_tag = " 主板" if args.main_board else ""
    date_tag = f" 日期{date_range[0]}~{date_range[1]}" if date_range else ""

    print(f"\n{'═' * 70}")
    print(f"  买入-卖出回测 {mode_label} | {freq_label}{board_tag} | {len(stock_files)} 只个股"
          f"{date_tag}")
    if trend_mode:
        print(f"  趋势跟踪: MA20跌破退出 | 止损{int(abs(sl_level)*100)}% | 移动止盈{int(abs(tr_level)*100)}%")
        print(f"  持仓≤{max_hold_desc} | 强制动量过滤 | 突破型信号优先")
    else:
        if weekly_mode or resample_mode:
            print(f"  周线单信号入场 | ATR动态止损 | 量价质量检查")
        else:
            if v5_mode:
                print(f"  双确认{V5_DUAL_CONFIRM_WINDOW}日≥2(高质量单信号可入) | 体制自适应TP/SL | 回踩优化入场")
            else:
                print(f"  双信号互确认({DUAL_CONFIRM_WINDOW}日≥2) | ATR动态止损 | 量价质量检查")
        if v5_mode:
            print(f"  止盈: 体制自适应(bull+12%/bear+5%) | 重新入场+冷却 | 信号评分≥{V5_MIN_SIGNAL_SCORE}")
        else:
            print(f"  止盈 +{int(args.target)}% | 移动止损 {int(tr_level*100)}% | 持仓≤{max_hold_desc}")
        if args.no_bear:
            print(f"  体制过滤: 跳过熊市/偏熊体制")
    if v5_mode:
        print(f"  v5增强: 回踩入场≤{V5_PULLBACK_MAX_WAIT}日 | 高分阈值{V5_HIGH_QUALITY_THRESHOLD} | "
              f"盈利冷却{V5_COOLDOWN_AFTER_WIN}d/亏损冷却{V5_COOLDOWN_AFTER_LOSS}d")
    if args.min_history > MIN_SAMPLE:
        print(f"  数据要求: 最少{args.min_history}个交易日")
    print(f"{'═' * 70}")

    label_parts = []
    if args.main_board:
        label_parts.append("主板")
    if date_range:
        label_parts.append(f"{date_range[0][:7]}_{date_range[1][:7]}")
    if v5_mode:
        label_parts.insert(0, "v5")
    label = "_".join(label_parts) if label_parts else ""

    bt = BuySellBacktest(take_profit=tp_level, stop_loss=sl_level,
                         trailing=tr_level, max_hold=max_hold,
                         trend_mode=trend_mode, skip_bear=args.no_bear,
                         weekly=(weekly_mode or resample_mode),
                         v5_mode=v5_mode, label=label)
    total_sig = 0
    for i, fpath in enumerate(stock_files):
        df = load_stock_csv(fpath)
        if args.resample_weekly and df is not None and len(df) >= 100:
            df = resample_daily_to_weekly(df)
        min_len = 30 if (weekly_mode or resample_mode) else 100
        if df is None or len(df) < min_len:
            continue
        try:
            total_sig += bt.run_stock(fpath, df, date_range=date_range)
        except Exception:
            continue
        progress_interval = 200 if weekly_mode else 500
        if (i + 1) % progress_interval == 0:
            e = time.time() - t0
            rate = (i + 1) / e if e > 0 else 1
            eta = (len(stock_files) - i - 1) / rate
            extra = f"趋势{bt.trend_filtered}" if trend_mode else f"质{bt.quality_filtered}"
            if v5_mode:
                extra += f" 评分{bt.v5_score_filtered} 重入{bt.v5_reentries}"
            if args.no_bear:
                extra += f" 体制{bt.regime_filtered}"
            print(f"  [{i+1}/{len(stock_files)}] {bt.dual_confirmed}确认 {total_sig}交易 "
                  f"过滤熊{bt.bear_filtered} {extra} | {e:.0f}s | ETA {eta:.0f}s", flush=True)

    elapsed = time.time() - t0
    f_extra = f" 趋势{bt.trend_filtered}" if trend_mode else f" 质{bt.quality_filtered}"
    if v5_mode:
        f_extra += f" 评分{bt.v5_score_filtered} 重入{bt.v5_reentries}"
    if args.no_bear:
        f_extra += f" 体制{bt.regime_filtered}"
    print(f"\n  ✅ {total_sig}笔交易 | 双确认{bt.dual_confirmed}次 "
          f"| 过滤: 熊{bt.bear_filtered}{f_extra} | {elapsed:.0f}s")

    s = bt.summary()
    print(f"\n{'─' * 60}")
    if trend_mode:
        print(f"  📊 汇总: {s['total_trades']}笔 | 胜率{s['win_rate']}% | "
              f"20%+ {s['hit_20pct_n']}次({s['hit_20pct']}%) | 10%+ {s['hit_10pct_n']}次({s['hit_10pct']}%)")
    else:
        print(f"  📊 汇总: {s['total_trades']}笔 | 胜率{s['win_rate']}% | 止盈率{s['take_profit_rate']}%")
    print(f"  均值{s['avg_return']}% | 峰值{s['avg_peak_return']}% | 最大回撤{s['max_drawdown']}%")
    print(f"  夏普{s['sharpe']} | 盈亏比{s['profit_factor']} | 连亏{s['max_loss_streak']} | 持仓{s['avg_hold_days']}d")
    print(f"  退出: {s['reason_dist']}")
    if v5_mode:
        print(f"  v5: 重新入场{s['v5_reentries']}次 | 评分过滤{s['v5_score_filtered']}次")

    ps = bt.pattern_summary(5)
    if not ps.empty:
        sort_col = "hit_20pct" if trend_mode else "hit_10pct"
        print(f"\n{'─' * 60}")
        print(f"  🏆 Top 15 信号组合")
        print(f"{'─' * 60}")
        for _, row in ps.head(15).iterrows():
            pname = row['pattern'][:50]
            print(f"  {pname:<50s} N={row['n']:>3d} WR={row['wr']:>5.1f}% "
                  f"Avg={row['avg_ret']:>+5.2f}% 20%+={row['hit_20pct']:>4.1f}% "
                  f"10%+={row['hit_10pct']:>4.1f}% Peak={row['avg_peak']:>5.1f}%")

    # ── 跨周期 ──
    print(f"\n{'─' * 60}")
    print(f"  🔬 跨周期稳定性")
    print(f"{'─' * 60}")
    regime_stats = {}
    for reg, (start, end) in REGIMES.items():
        rt = [t for t in bt.trades
              if len(t["entry_date"]) >= 10 and start <= t["entry_date"][:10] <= end]
        label = REGIME_LABELS.get(reg, "")
        if not rt:
            print(f"  {label} {reg:<15s} N=   0")
            regime_stats[reg] = {"n": 0, "wr": 0, "tp": 0, "avg": 0, "peak": 0, "hit20": 0, "hit10": 0}
            continue
        n_rt = len(rt)
        wr = sum(1 for t in rt if t["win"]) / n_rt * 100
        tp = sum(1 for t in rt if t["exit_reason"] == "take_profit") / n_rt * 100
        avg = float(np.mean([t["return_pct"] for t in rt]))
        peak = float(np.mean([t["peak_return"] for t in rt]))
        hit20 = sum(1 for t in rt if t["return_pct"] >= 20.0) / n_rt * 100
        hit10 = sum(1 for t in rt if t["return_pct"] >= 10.0) / n_rt * 100
        if trend_mode:
            marker = " ✅" if hit20 >= 15 else (" ⚠" if hit20 >= 10 else " ❌")
            print(f"  {label} {reg:<15s} N={n_rt:>4d} WR={wr:>5.1f}% "
                  f"Avg={avg:>+5.2f}% 20%+={hit20:>4.1f}% Peak={peak:>+5.2f}%{marker}")
        else:
            marker = " ✅" if tp >= 30 else (" ⚠" if tp >= 20 else " ❌")
            print(f"  {label} {reg:<15s} {start[:7]}~{end[:7]} N={n_rt:>4d} WR={wr:>5.1f}% TP={tp:>4.1f}% "
                  f"Avg={avg:>+5.2f}% 10%+={hit10:>4.1f}% Peak={peak:>+5.2f}%{marker}")
        regime_stats[reg] = {"n": n_rt, "wr": round(wr, 1), "tp": round(tp, 1),
                             "avg": round(avg, 2), "peak": round(peak, 2),
                             "hit20": round(hit20, 1), "hit10": round(hit10, 1)}

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

    print(f"\n{'─' * 60}")
    if trend_mode:
        avg_hit20 = float(np.mean([r.get("hit20", 0) for r in regime_stats.values() if r["n"] >= 10]))
        print(f"  📈 WR均值={wr_mean}% | 20%+命中均值={avg_hit20:.1f}% | WR波动={wr_std}%")
        print(f"  稳定性评分: {stability}/100")
    else:
        print(f"  📈 WR均值={wr_mean}% | TP均值={tp_mean}% | WR波动={wr_std}%")
        print(f"  最低TP={min_tp}% | 全周期TP≥25%: {'是' if all_above_25 else '否'}")
        print(f"  稳定性评分: {stability}/100")

    # ── 大赢家分析 ──
    bw = big_winner_analysis(bt, args.target)
    if bw:
        print(f"\n{'─' * 60}")
        print(f"  💰 大赢家分析")
        print(f"{'─' * 60}")
        print(f"  最大峰值: +{bw['max_peak']}% | 平均峰值: +{bw['avg_peak']}%")
        print(f"")
        print(f"  峰值分布:")
        for label, data in bw["peak_distribution"].items():
            bar = "█" * int(data["pct"] / 2)
            print(f"    {label:<8s} {data['count']:>5d}笔 ({data['pct']:>5.1f}%) {bar}")
        print(f"")
        print(f"  不同止盈阈值的效果模拟:")
        print(f"    {'策略':<12s} {'平均收益':>8s} {'胜率':>6s} {'止盈率':>6s}")
        print(f"    {'─'*40}")
        for strategy, data in bw["tier_analysis"].items():
            print(f"    {strategy:<12s} {data['avg_ret']:>+7.2f}% {data['wr']:>5.1f}% {data['tp_rate']:>5.1f}%")

    # ── 内置分析 ──
    if args.analysis:
        analysis = bt.run_analysis()
        bt.print_analysis(analysis)

    # ── v5 体制收益分析 ──
    if v5_mode and bt.trades:
        print(f"\n{'─' * 60}")
        print(f"  📈 v5 体制自适应收益分析")
        print(f"{'─' * 60}")
        print(f"  {'体制':<12s} {'笔数':>5s} {'胜率':>6s} {'均收益':>7s} {'均分':>5s} {'10%+':>5s}")
        print(f"  {'─'*45}")
        for reg in ["bull", "bull_bias", "range", "bear_bias", "bear"]:
            rt = [t for t in bt.trades if t.get("entry_regime") == reg]
            if not rt:
                continue
            n_rt = len(rt)
            wr = sum(1 for t in rt if t["win"]) / n_rt * 100
            avg_ret = float(np.mean([t["return_pct"] for t in rt]))
            avg_score = float(np.mean([t.get("signal_score", 0) for t in rt]))
            hit10 = sum(1 for t in rt if t["return_pct"] >= 10.0) / n_rt * 100
            params = REGIME_PARAMS_SWING.get(reg, {})
            tp_str = f"{int(params.get('tp', 0)*100)}%"
            print(f"  {reg:<12s} {n_rt:>5d} {wr:>5.1f}% {avg_ret:>+6.2f}% {avg_score:>4.2f} {hit10:>4.1f}%  "
                  f"TP={tp_str}")

    # ── 保存交易记录 ──
    bt.save_trades(date_range=date_range, regime_stats=regime_stats)

    print(f"\n{'═' * 70}")
    if trend_mode:
        print(f"  v4 趋势跟踪 | 20%+命中率: {s['hit_20pct']}% ({s['hit_20pct_n']}/{s['total_trades']})")
    else:
        regime_str = f" | 体制 {s.get('regime_filtered',0)}" if args.no_bear else ""
        v5_str = f" | v5重入{s.get('v5_reentries',0)} 评分过滤{s.get('v5_score_filtered',0)}" if v5_mode else ""
        print(f"  v3 波段过滤: 熊市 {s.get('bear_filtered',0)} | 量质 {s.get('quality_filtered',0)}{regime_str}{v5_str} | 双确认 {s.get('dual_confirmed',0)}")
    print(f"  总耗时 {elapsed:.0f}s")
    print(f"{'═' * 70}")


if __name__ == "__main__":
    main()
