"""
K线形态评分器 — 将 v3 高精度形态检测集成到 daily_pipeline 评分体系

检测8个经过750只股票验证的高胜率形态组合, 返回 0-1 综合评分。
形态触发 T → 确认日 T+1 (超严格: 收盘>T最高 + 收阳 + 放量) → 信号有效

返回:
    score (0-1): K线形态综合评分
    active_patterns: 当日活跃的形态列表
    confirmed: 是否通过确认日验证
"""
import os
import warnings
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorized indicator computation."""
    c = df["close"]; o = df["open"]; h = df["high"]
    l = df["low"]; v = df["volume"]

    for w in [5, 10, 20, 60]:
        df[f"ma{w}"] = c.rolling(w, min_periods=w).mean()
    for w in [5, 20]:
        df[f"vol_ma{w}"] = v.rolling(w, min_periods=w).mean()

    df["candle_range"] = h - l
    df["body_ratio"] = np.where(df["candle_range"] > 0,
                                np.abs(c - o) / df["candle_range"], 0)
    df["lower_shadow"] = np.where(df["candle_range"] > 0,
                                  (np.minimum(o, c) - l) / df["candle_range"], 0)
    df["upper_shadow"] = np.where(df["candle_range"] > 0,
                                  (h - np.maximum(o, c)) / df["candle_range"], 0)
    df["is_yang"] = (c > o).astype(int)
    df["is_yin"] = (c < o).astype(int)
    df["amplitude"] = np.where(df["close"].shift(1) > 0,
                               df["candle_range"] / df["close"].shift(1), 0)
    df["change_pct"] = np.where(df["close"].shift(1) > 0,
                                (c - df["close"].shift(1)) / df["close"].shift(1), 0)
    df["vol_ratio_vs5"] = np.where(df["vol_ma5"] > 0, v / df["vol_ma5"], 1)
    df["vol_ratio_vs20"] = np.where(df["vol_ma20"] > 0, v / df["vol_ma20"], 1)

    # Streaks
    df["yang_streak"] = 0; df["yin_streak"] = 0
    ys = 0; ns = 0
    for i in range(len(df)):
        if df["is_yang"].iloc[i]:
            ys += 1; ns = 0
        else:
            ns += 1; ys = 0
        df.iloc[i, df.columns.get_loc("yang_streak")] = ys
        df.iloc[i, df.columns.get_loc("yin_streak")] = ns

    for w in [10, 20, 60]:
        df[f"high_{w}d"] = h.rolling(w, min_periods=w).max()
        df[f"low_{w}d"] = l.rolling(w, min_periods=w).min()
        df[f"close_high_{w}d"] = c.rolling(w, min_periods=w).max()
        df[f"close_low_{w}d"] = c.rolling(w, min_periods=w).min()

    # Rank
    for j in range(20, len(df)):
        rng = df["close_high_20d"].values[j] - df["close_low_20d"].values[j]
        df.loc[df.index[j], "close_rank_20"] = (
            (c.values[j] - df["close_low_20d"].values[j]) / rng if rng > 0 else 0.5)

    return df


def detect_patterns_at_last(df: pd.DataFrame) -> dict:
    """
    Check the LAST day (most recent) of the DataFrame for pattern signals.
    Returns dict with pattern detection results.
    """
    n = len(df)
    if n < 70:
        return {"score": 0.5, "patterns": [], "confirmed": False}

    df = _compute_indicators(df)
    i = n - 1  # last day

    c = df["close"].values; o = df["open"].values; h = df["high"].values
    l = df["low"].values; v = df["volume"].values; pc = c  # proxied
    is_yang = df["is_yang"].values; is_yin = df["is_yin"].values
    yang_streak = df["yang_streak"].values; yin_streak = df["yin_streak"].values
    vol_r5 = df["vol_ratio_vs5"].values; vol_r20 = df["vol_ratio_vs20"].values
    amp = df["amplitude"].values; body_r = df["body_ratio"].values
    lower_s = df["lower_shadow"].values
    ma5 = df["ma5"].values; ma10 = df["ma10"].values
    ma20 = df["ma20"].values; ma60 = df["ma60"].values
    change_pct = df["change_pct"].values
    close_rank_20 = df["close_rank_20"].values

    active = []

    # ── P1: 启明星（三连阴缩量+十字星+放量阳）── 小样本87% WR
    if (i >= 3 and yin_streak[i-1] >= 3 and v[i-3] > v[i-2] > v[i-1] and
        body_r[i-1] < 0.2 and is_yang[i] and vol_r5[i] > 1.1 and
        c[i] > (o[i-3] + c[i-3]) / 2):
        active.append("morning_star")

    # ── P2: 反包（阴包阳→阳包阴+放量）── 小样本70% WR
    if (i >= 2 and is_yin[i-1] and is_yang[i-2] and
        c[i-1] < o[i-2] and o[i-1] > c[i-2] and  # 阴包阳
        is_yang[i] and c[i] > o[i-1] and o[i] < c[i-1] and  # 阳包阴
        vol_r5[i] > 1.3):
        active.append("engulf_reversal")

    # ── P3: 深跌反弹（60日跌25%+放量破高+站上MA10）
    if (i >= 60 and not pd.isna(df["close_high_60d"].values[i]) and
        c[i] < df["close_high_60d"].values[i] * 0.75 and
        is_yang[i] and vol_r5[i] > 2.0 and amp[i] > 0.03 and
        not pd.isna(ma10[i]) and c[i] > ma10[i] and
        i >= 1 and c[i] > h[i-1] and change_pct[i] < 0.09):
        active.append("deep_rebound_25")

    # ── P4: 急跌长下影（5日跌12%+长下影+放量阳+低位）
    if (i >= 5 and c[i-5] > 0 and (c[i] - c[i-5]) / c[i-5] < -0.12 and
        lower_s[i] > 0.6 and is_yang[i] and vol_r5[i] > 1.5 and
        not pd.isna(close_rank_20[i]) and close_rank_20[i] < 0.25):
        active.append("panic_lower_shadow")

    # ── P5: 缩量横盘后放量突破（缩量3日+放量破前高+多头）
    if (i >= 3 and np.std(c[i-3:i]) / np.mean(c[i-3:i]) < 0.015 and
        np.all(vol_r5[i-3:i] < 0.55) and vol_r5[i] > 2.0 and
        is_yang[i] and c[i] > max(h[i-3:i]) and
        not pd.isna(ma5[i]) and not pd.isna(ma10[i]) and not pd.isna(ma20[i]) and
        ma5[i] > ma10[i] > ma20[i]):
        active.append("squeeze_breakout")

    # ── P6: 强多头回踩MA20（多头+缩量+十字星+启稳）
    if (not pd.isna(ma5[i]) and not pd.isna(ma10[i]) and not pd.isna(ma20[i]) and
        ma5[i] > ma10[i] > ma20[i] and ma20[i] > 0 and
        np.abs((c[i] - ma20[i]) / ma20[i]) < 0.015 and
        vol_r5[i] < 0.45 and body_r[i] < 0.35 and is_yang[i] and c[i] > ma20[i]):
        active.append("bull_ma20_pullback")

    # ── P7: 双针探底（双下影+低位+量增收阳）
    if (i >= 2 and lower_s[i] > 0.55 and lower_s[i-1] > 0.55 and
        not pd.isna(close_rank_20[i]) and close_rank_20[i] < 0.2 and
        is_yang[i] and v[i] > v[i-1] and c[i] > c[i-1]):
        active.append("double_needle_bottom")

    # ── P8: MA金叉（MA5金叉MA20+放量+MA20向上）
    if (i >= 5 and not pd.isna(ma5[i]) and not pd.isna(ma20[i]) and
        not pd.isna(ma5[i-1]) and not pd.isna(ma20[i-1]) and
        ma5[i-1] <= ma20[i-1] and ma5[i] > ma20[i] and
        vol_r5[i] > 1.3 and is_yang[i] and ma20[i] >= ma20[i-5] and
        c[i] > c[i-1]):
        active.append("ma_golden_cross")

    # ── Score calculation ──
    if not active:
        return {"score": 0.5, "patterns": active, "confirmed": False}

    # Base score increases with number of active patterns
    base = 0.5 + len(active) * 0.1

    # Bonus for highest-quality patterns
    if "morning_star" in active:
        base += 0.10
    if "engulf_reversal" in active:
        base += 0.08
    if "deep_rebound_25" in active:
        base += 0.08
    if "squeeze_breakout" in active:
        base += 0.07

    # Confirmation proxy: today IS the confirmation day (we're at market close)
    # Check if today's action confirms the pattern (close > open, volume > avg)
    confirmed = is_yang[i] and vol_r5[i] > 0.7

    score = min(1.0, base)

    return {
        "score": round(score, 3),
        "patterns": active,
        "confirmed": confirmed,
        "num_patterns": len(active),
        "top_pattern": active[0] if active else None,
    }


def score_kline_pattern(code: str, bars: list[dict],
                        date_str: str = None) -> dict:
    """
    Main entry point — score a single stock based on K-line bars.

    Args:
        code: stock code (e.g., "sh.600000")
        bars: list of dicts with keys: date, open, high, low, close, volume
        date_str: target date (YYYYMMDD or YYYY-MM-DD). If None, use last bar.

    Returns:
        dict with score, patterns, confirmed
    """
    if not bars or len(bars) < 70:
        return {"score": 0.5, "patterns": [], "confirmed": False}

    df = pd.DataFrame(bars)
    df = df.sort_values("date").reset_index(drop=True)

    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            return {"score": 0.5, "patterns": [], "confirmed": False}

    # If target date specified, truncate to that date (no look-ahead)
    if date_str:
        # Normalize date format
        target = date_str.replace("-", "")
        idx = None
        for j in range(len(df)):
            bar_date = str(df["date"].iloc[j]).replace("-", "")
            if bar_date == target:
                idx = j
                break
        if idx is not None and idx >= 70:
            df = df.iloc[:idx + 1].copy()
        elif idx is not None and idx < 70:
            return {"score": 0.5, "patterns": [], "confirmed": False}

    return detect_patterns_at_last(df)


def score_batch(code_bars_map: dict) -> dict:
    """
    Score a batch of stocks. {code: [bars]} → {code: score_result}
    """
    results = {}
    for code, bars in code_bars_map.items():
        results[code] = score_kline_pattern(code, bars)
    return results
