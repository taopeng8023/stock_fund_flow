"""
K线形态评分器 v2 — 连续评分模型（非二值模式匹配）

6个子维度, 每维度 0-1 连续评分, 加权合成:
  A: 趋势质量 (0.25) — MA多头排列+斜率+位置
  B: 反转形态接近度 (0.20) — 启明星/反包/长下影 接近程度
  C: 量价配合 (0.20) — 放量阳线/缩量阴线 质量
  D: 突破强度 (0.15) — 突破均线/前高的力度
  E: 支撑/阻力 (0.10) — 回踩均线/双底形态
  F: 极端反转 (0.10) — 深跌反弹/恐慌底部

用法:
  from daily_pipeline.kline_scorer import score_kline_pattern
  result = score_kline_pattern(code, bars, date_str)
  result["score"]  # 0-1 continuous
"""
import os
import warnings
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


def score_kline_pattern(code: str, bars: list[dict],
                        date_str: str = None) -> dict:
    """
    连续K线质量评分。非二值检测, 而是对K线结构质量做0-1连续评分。

    Returns: {score, sub_scores, active_patterns, confirmed}
    """
    if not bars or len(bars) < 30:
        return {"score": 0.5, "sub_scores": {}, "active_patterns": [],
                "confirmed": False}

    df = pd.DataFrame(bars)
    df = df.sort_values("date").reset_index(drop=True)

    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            return {"score": 0.5, "sub_scores": {}, "active_patterns": [],
                    "confirmed": False}

    # Truncate to target date
    if date_str:
        target = date_str.replace("-", "")
        idx = None
        for j in range(len(df)):
            bar_date = str(df["date"].iloc[j]).replace("-", "")
            if bar_date == target:
                idx = j
                break
        if idx is not None and idx >= 30:
            df = df.iloc[:idx + 1].copy()
        elif idx is not None and idx < 30:
            return {"score": 0.5, "sub_scores": {}, "active_patterns": [],
                    "confirmed": False}

    return _compute_score(df)


def _sigmoid(x: float, midpoint: float = 0.5, steepness: float = 6.0) -> float:
    """Smooth 0-1 scoring function."""
    return 1.0 / (1.0 + np.exp(-steepness * (x - midpoint)))


def _compute_score(df: pd.DataFrame) -> dict:
    n = len(df)
    i = n - 1  # last day index
    c = df["close"].values
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    v = df["volume"].values

    # ── Precompute indicators ──
    ma5_vals = c.copy()
    ma10_vals = c.copy()
    ma20_vals = c.copy()
    for w, arr in [(5, ma5_vals), (10, ma10_vals), (20, ma20_vals)]:
        for j in range(w - 1, n):
            arr[j] = np.mean(c[j - w + 1:j + 1])

    vol_ma5 = v.copy()
    for j in range(4, n):
        vol_ma5[j] = np.mean(v[j - 4:j + 1])

    high_20 = np.array([np.max(h[max(0, j - 19):j + 1]) for j in range(n)])
    low_20 = np.array([np.min(l[max(0, j - 19):j + 1]) for j in range(n)])
    high_60 = np.array([np.max(h[max(0, j - 59):j + 1]) for j in range(n)])
    low_60 = np.array([np.min(l[max(0, j - 59):j + 1]) for j in range(n)])

    body = np.abs(c - o)
    candle_range = h - l
    body_ratio = np.where(candle_range > 0, body / candle_range, 0)
    lower_shadow = np.where(candle_range > 0,
                            (np.minimum(o, c) - l) / candle_range, 0)
    upper_shadow = np.where(candle_range > 0,
                            (h - np.maximum(o, c)) / candle_range, 0)
    amplitude = np.zeros(n)
    for j in range(1, n):
        prev_c = c[j - 1]
        amplitude[j] = candle_range[j] / prev_c if prev_c > 0 else 0

    change_pct = np.zeros(n)
    for j in range(1, n):
        change_pct[j] = (c[j] - c[j - 1]) / c[j - 1] if c[j - 1] > 0 else 0

    vol_ratio = np.ones(n)
    for j in range(5, n):
        vol_ratio[j] = v[j] / vol_ma5[j] if vol_ma5[j] > 0 else 1.0

    is_yang = (c > o).astype(int)
    is_yin = (c < o).astype(int)

    # ── A: 趋势质量 (0.25) ──
    ma5_i = ma5_vals[i]
    ma10_i = ma10_vals[i]
    ma20_i = ma20_vals[i]

    # MA alignment
    align_score = 0.0
    if not np.isnan(ma5_i) and not np.isnan(ma10_i) and not np.isnan(ma20_i):
        aligns = 0
        if ma5_i > ma10_i: aligns += 0.3
        if ma10_i > ma20_i: aligns += 0.3
        if ma5_i > ma20_i: aligns += 0.2
        if c[i] > ma5_i: aligns += 0.2
        align_score = min(1.0, aligns)

    # MA5 slope
    slope_score = 0.5
    if i >= 10:
        ma5_prev = np.mean(c[i - 9:i - 4])
        if ma5_prev > 0:
            slope = (ma5_i - ma5_prev) / ma5_prev * 100
            slope_score = _sigmoid(slope, midpoint=1.0, steepness=0.4)

    # Position in 60d range
    pos_score = 0.5
    rng_60 = high_60[i] - low_60[i]
    if rng_60 > 0:
        pos = (c[i] - low_60[i]) / rng_60
        # Best: 60-80% (strong but not overbought)
        if 0.6 <= pos <= 0.85:
            pos_score = 0.85
        elif 0.4 <= pos < 0.6:
            pos_score = 0.60
        elif pos > 0.85:
            pos_score = 0.40  # extended
        else:
            pos_score = 0.30  # weak

    sub_a = align_score * 0.35 + slope_score * 0.30 + pos_score * 0.35

    # ── B: 反转形态接近度 (0.20) ──
    # Morning star proximity: 3-day decline + small body + today yang
    morning_score = 0.3
    if i >= 3:
        decline_3d = (c[i] - c[i - 3]) / c[i - 3] if c[i - 3] > 0 else 0
        body_small = 1.0 - body_ratio[i - 1] if body_ratio[i - 1] < 0.3 else 0.2
        today_yang_bonus = 0.3 if is_yang[i] else 0
        vol_expand = min(1.0, vol_ratio[i] / 1.5) if vol_ratio[i] > 0.8 else 0.3
        morning_score = (max(0, -decline_3d / 0.10) * 0.3 +
                         body_small * 0.25 + today_yang_bonus * 0.25 + vol_expand * 0.2)
        morning_score = min(1.0, morning_score)

    # Lower shadow quality
    shadow_score = 0.0
    if lower_shadow[i] > 0.4 and amplitude[i] > 0.01:
        shadow_score = min(1.0, lower_shadow[i] * 1.2 + amplitude[i] * 5)

    # Engulfing proximity
    engulf_score = 0.3
    if i >= 1 and is_yin[i - 1] and is_yang[i]:
        engulf_ratio = (c[i] - o[i - 1]) / o[i - 1] if o[i - 1] > 0 else 0
        engulf_score = _sigmoid(engulf_ratio, midpoint=0.01, steepness=200)

    sub_b = morning_score * 0.35 + shadow_score * 0.30 + engulf_score * 0.20 + 0.15

    # ── C: 量价配合 (0.20) ──
    # Volume expansion with yang = bullish
    vol_yang_score = 0.5
    if is_yang[i]:
        vol_yang_score = _sigmoid(vol_ratio[i], midpoint=1.0, steepness=3.0)
    elif is_yin[i] and vol_ratio[i] < 0.6:
        vol_yang_score = 0.55  # shrinking volume on down day = OK

    # Volume trend (3-day)
    vol_trend_score = 0.5
    if i >= 3 and all(v[j] > 0 for j in range(i - 2, i + 1)):
        if v[i] > v[i - 1] > v[i - 2]:
            vol_trend_score = 0.75
        elif v[i] < v[i - 1] < v[i - 2]:
            vol_trend_score = 0.35

    # Amplitude quality (not too high, not too low)
    amp_score = 0.5
    if 0.015 <= amplitude[i] <= 0.06:
        amp_score = 0.75
    elif amplitude[i] > 0.10:
        amp_score = 0.30  # too volatile

    sub_c = vol_yang_score * 0.40 + vol_trend_score * 0.30 + amp_score * 0.30

    # ── D: 突破强度 (0.15) ──
    # Break above MA20
    ma20_break = 0.3
    if i >= 1 and not np.isnan(ma20_i):
        if c[i] > ma20_i and c[i - 1] <= ma20_i:
            ma20_break = 0.80
        elif c[i] > ma20_i * 1.02:
            ma20_break = 0.55
        elif c[i] > ma20_i:
            ma20_break = 0.45

    # Break above 20d high
    high_break = 0.3
    if c[i] > high_20[i - 1] * 0.98:
        high_break = 0.70

    # Breakout volume confirmation
    break_vol = _sigmoid(vol_ratio[i], midpoint=1.2, steepness=4.0)

    sub_d = ma20_break * 0.35 + high_break * 0.35 + break_vol * 0.30

    # ── E: 支撑/阻力质量 (0.10) ──
    # Distance from MA5/10/20 (closer = better support)
    dist_ma5 = abs(c[i] - ma5_i) / ma5_i if ma5_i > 0 else 0.05
    dist_ma20 = abs(c[i] - ma20_i) / ma20_i if ma20_i > 0 else 0.05

    support_score = 0.5
    if dist_ma5 < 0.02 and dist_ma20 < 0.05:
        support_score = 0.75  # near support
    elif c[i] < ma5_i and c[i] < ma20_i:
        support_score = 0.25  # broke support

    # Double bottom proximity
    double_bot_score = 0.3
    if i >= 5:
        close_to_20d_low = (c[i] - low_20[i]) / low_20[i] if low_20[i] > 0 else 0.1
        if close_to_20d_low < 0.03 and is_yang[i]:
            double_bot_score = 0.70

    sub_e = support_score * 0.55 + double_bot_score * 0.45

    # ── F: 极端反转潜力 (0.10) ──
    # How far from 60d high (deeper = more reversal potential)
    drawdown_score = 0.3
    if high_60[i] > 0:
        dd = (c[i] - high_60[i]) / high_60[i]
        if dd < -0.30:
            drawdown_score = 0.85  # deep oversold
        elif dd < -0.20:
            drawdown_score = 0.70
        elif dd < -0.10:
            drawdown_score = 0.55

    # Oversold + yang day = bullish reversal
    oversold_bonus = 0.0
    if i >= 5 and change_pct[i - 5:i].mean() < -0.02 and is_yang[i] and vol_ratio[i] > 1.2:
        oversold_bonus = 1.0

    sub_f = drawdown_score * 0.5 + oversold_bonus * 0.5

    # ── Composite ──
    score = (sub_a * 0.25 + sub_b * 0.20 + sub_c * 0.20 +
             sub_d * 0.15 + sub_e * 0.10 + sub_f * 0.10)

    # Detect which binary patterns are active (for signal labeling)
    active_patterns = []
    if morning_score > 0.65 or (i >= 3 and is_yang[i] and lower_shadow[i] > 0.5):
        active_patterns.append("reversal_signal")
    if engulf_score > 0.6:
        active_patterns.append("engulf_signal")
    if vol_ratio[i] > 1.5 and is_yang[i] and sub_d > 0.6:
        active_patterns.append("breakout_signal")
    if drawdown_score > 0.7 and is_yang[i] and vol_ratio[i] > 1.0:
        active_patterns.append("oversold_bounce")
    if sub_a > 0.7 and sub_e > 0.6:
        active_patterns.append("trend_pullback")

    confirmed = is_yang[i] and vol_ratio[i] > 0.8

    return {
        "score": round(min(1.0, max(0.15, score)), 3),
        "sub_scores": {
            "trend_quality": round(sub_a, 3),
            "reversal": round(sub_b, 3),
            "vol_price": round(sub_c, 3),
            "breakout": round(sub_d, 3),
            "support": round(sub_e, 3),
            "extreme": round(sub_f, 3),
        },
        "active_patterns": active_patterns,
        "confirmed": confirmed,
        "num_patterns": len(active_patterns),
    }


def score_batch(code_bars_map: dict) -> dict:
    """Batch score. {code: [bars]} → {code: result}"""
    results = {}
    for code, bars in code_bars_map.items():
        results[code] = score_kline_pattern(code, bars)
    return results
