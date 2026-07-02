"""
背驰检测 — MACD/RSI/量价背离

核心原理:
  顶背离: 价格创新高, 但指标不创新高 → 上涨动能衰竭 → 0.0
  底背离: 价格创新低, 但指标不创新低 → 下跌动能衰竭 → 1.0

这里的 "背驰" 概念与缠论一致: 比较两段走势的价格与动能变化
"""
import math
from dataclasses import dataclass

from .price_loader import DailyBar


@dataclass
class DivergenceResult:
    score: float                # 0-1 背离得分 (0=顶背离/0.5=无/1=底背离)
    macd_divergence: float      # MACD 背离得分
    rsi_divergence: float       # RSI 背离得分
    volume_divergence: float    # 量价背离得分
    signals: list[str]


def _find_swing_points(values: list[float], lookback: int = 40) -> tuple[int, int]:
    """
    在 lookback 窗口内找两个摆动极值点。

    返回 (first_peak_idx, second_peak_idx) 或 (first_trough_idx, second_trough_idx)
    """
    n = len(values)
    start = max(0, n - lookback)
    segment = values[start:]

    if len(segment) < 20:
        return start, n - 1

    mid = len(segment) // 2
    # 前半段和后半段分别找极值
    first_idx = start + max(range(mid), key=lambda i: segment[i])
    second_idx = start + mid + max(range(len(segment) - mid), key=lambda i: segment[mid + i])

    # 确保间隔 > 5 个 K 线
    if abs(second_idx - first_idx) < 5:
        second_idx = min(first_idx + 5, n - 1)

    return first_idx, second_idx


def detect_macd_divergence(closes: list[float], macd_hist: list[float], lookback: int = 40) -> float:
    """
    MACD 柱背离检测。

    顶背离: 价格前低后高, MACD 柱前高后低 → 0.0
    底背离: 价格前高后低, MACD 柱前低后高 → 1.0
    无背离 → 0.5

    返回 0-1 得分
    """
    n = len(closes)
    if n < lookback:
        return 0.5

    # 找两个摆动高点
    start = max(0, n - lookback)
    segment_closes = closes[start:]
    segment_hist = macd_hist[start:]

    # 找价格的两个局部高点
    def _find_peaks(arr):
        peaks = []
        for i in range(5, len(arr) - 5):
            if arr[i] == max(arr[i - 5:i + 6]):
                peaks.append(i)
        return peaks

    close_peaks = _find_peaks(segment_closes)
    hist_peaks = _find_peaks(segment_hist)

    if len(close_peaks) < 2:
        return 0.5

    # 取最近两个价格高点
    p1_idx, p2_idx = close_peaks[-2], close_peaks[-1]
    price_rising = segment_closes[p2_idx] > segment_closes[p1_idx]

    if not price_rising:
        return 0.5  # 价格没新高，不构成顶背离

    # 找对应时间段的 MACD 柱高点
    if len(hist_peaks) >= 2:
        # 找 p1 附近和 p2 附近的 MACD 柱峰值
        h1 = max(
            (segment_hist[i] for i in hist_peaks if abs(i - p1_idx) <= 10),
            default=segment_hist[p1_idx],
        )
        h2 = max(
            (segment_hist[i] for i in hist_peaks if abs(i - p2_idx) <= 10),
            default=segment_hist[p2_idx],
        )

        if h2 < h1:
            return 0.0  # 顶背离

    # 底背离检测
    def _find_troughs(arr):
        troughs = []
        for i in range(5, len(arr) - 5):
            if arr[i] == min(arr[i - 5:i + 6]):
                troughs.append(i)
        return troughs

    close_troughs = _find_troughs(segment_closes)
    hist_troughs = _find_troughs(segment_hist)

    if len(close_troughs) < 2:
        return 0.5

    t1_idx, t2_idx = close_troughs[-2], close_troughs[-1]
    price_falling = segment_closes[t2_idx] < segment_closes[t1_idx]

    if not price_falling:
        return 0.5  # 价格没新低

    if len(hist_troughs) >= 2:
        h1 = min(
            (segment_hist[i] for i in hist_troughs if abs(i - t1_idx) <= 10),
            default=segment_hist[t1_idx],
        )
        h2 = min(
            (segment_hist[i] for i in hist_troughs if abs(i - t2_idx) <= 10),
            default=segment_hist[t2_idx],
        )
        if h2 > h1:
            return 1.0  # 底背离

    return 0.5


def detect_rsi_divergence(closes: list[float], rsi_values: list[float], lookback: int = 40) -> float:
    """
    RSI 背离检测。

    在 RSI 序列中找背离，逻辑同 MACD 柱背离。
    额外考虑: RSI < 30 区域底背离更强, RSI > 70 区域顶背离更强。
    """
    n = len(closes)
    if n < lookback:
        return 0.5

    # 清洗 nan
    valid = [(closes[i], rsi_values[i]) for i in range(n)
             if not math.isnan(rsi_values[i]) and not math.isnan(closes[i])]
    if len(valid) < lookback:
        return 0.5

    clean_closes = [v[0] for v in valid[-lookback:]]
    clean_rsi = [v[1] for v in valid[-lookback:]]

    mid = len(clean_closes) // 2
    first_half_close = clean_closes[:mid]
    second_half_close = clean_closes[mid:]
    first_half_rsi = clean_rsi[:mid]
    second_half_rsi = clean_rsi[mid:]

    price_rising = max(second_half_close) > max(first_half_close)
    price_falling = min(second_half_close) < min(first_half_close)

    latest_rsi = clean_rsi[-1]

    if price_rising and max(second_half_rsi) < max(first_half_rsi):
        if latest_rsi > 70:
            return 0.05  # 强顶背离
        return 0.10  # 顶背离

    if price_falling and min(second_half_rsi) > min(first_half_rsi):
        if latest_rsi < 30:
            return 0.95  # 强底背离
        return 0.85  # 底背离

    return 0.5


def detect_volume_divergence(bars: list[DailyBar], lookback: int = 20) -> float:
    """
    量价背离。

    价涨量缩(最近5日 vs 前5日) → 上涨乏力 → 0.3
    价跌量缩(最近5日 vs 前5日) → 下跌衰竭 → 0.7
    正常 → 0.5
    """
    n = len(bars)
    if n < lookback:
        return 0.5

    recent_n = min(5, n // 2)
    recent = bars[-recent_n:]
    prior = bars[-lookback:-recent_n] if n > recent_n + 5 else bars[-lookback:-1]

    if not prior:
        return 0.5

    recent_price_change = (recent[-1].close - recent[0].close) / recent[0].close
    prior_price_change = (prior[-1].close - prior[0].close) / prior[0].close

    recent_avg_vol = sum(b.volume for b in recent) / len(recent)
    prior_avg_vol = sum(b.volume for b in prior) / len(prior)
    vol_change = (recent_avg_vol - prior_avg_vol) / prior_avg_vol if prior_avg_vol > 0 else 0

    # 价涨量缩
    if recent_price_change > 0.02 and vol_change < -0.15:
        return 0.30  # 上涨乏力
    # 价跌量缩
    if recent_price_change < -0.02 and vol_change < -0.15:
        return 0.70  # 下跌衰竭
    # 价涨量增
    if recent_price_change > 0.02 and vol_change > 0.15:
        return 0.65  # 良性放量上涨

    return 0.5


def detect_all(bars: list[DailyBar], indicators: dict) -> DivergenceResult:
    """综合背离分析"""
    closes = indicators["closes"]
    macd_hist = indicators["macd_hist"]
    rsi_values = indicators["rsi"]

    macd_div = detect_macd_divergence(closes, macd_hist)
    rsi_div = detect_rsi_divergence(closes, rsi_values)
    vol_div = detect_volume_divergence(bars)

    # 加权汇总: MACD 40% + RSI 35% + 量价 25%
    score = macd_div * 0.40 + rsi_div * 0.35 + vol_div * 0.25

    signals = []
    if macd_div < 0.3:
        signals.append("MACD顶背离(上涨衰竭)")
    elif macd_div > 0.7:
        signals.append("MACD底背离(下跌衰竭)")
    if rsi_div < 0.2:
        signals.append("RSI顶背离")
    elif rsi_div > 0.8:
        signals.append("RSI底背离(超卖)")
    if vol_div < 0.35:
        signals.append("量价背离(上涨乏力)")
    elif vol_div > 0.65:
        signals.append("量价背离(下跌衰竭)")

    return DivergenceResult(
        score=round(score, 3),
        macd_divergence=round(macd_div, 3),
        rsi_divergence=round(rsi_div, 3),
        volume_divergence=round(vol_div, 3),
        signals=signals,
    )
