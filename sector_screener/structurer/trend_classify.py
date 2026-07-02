"""
趋势类型分类 — MA 层级定势 + ADX 趋势强度

判定逻辑:
  上涨趋势: MA5 > MA10 > MA20 > MA60 且 close > MA5
  下跌趋势: MA5 < MA10 < MA20 < MA60 且 close < MA5
  盘整: 均线缠绕
"""
import math
from dataclasses import dataclass

from .price_loader import DailyBar
from .indicators import calc_ma_family, calc_ema


@dataclass
class TrendClassification:
    type: str              # "uptrend" | "downtrend" | "range"
    strength: float        # 0-1 趋势综合强度
    ma_alignment: float    # 0-1 均线多头排列程度
    ma_slope: float        # 0-1 MA20 斜率得分
    adx: float             # 0-1 ADX 趋势强度
    signals: list[str]     # 关键信号描述


def _ma_alignment(closes: list[float], mas: dict) -> float:
    """
    均线排列程度: MA5/MA10/MA20/MA60 的层级关系。

    完美多头: MA5 > MA10 > MA20 > MA60 → 1.0
    完美空头: MA5 < MA10 < MA20 < MA60 → 0.0
    缠绕: 0.3-0.7
    """
    ma5, ma10, ma20, ma60 = mas["MA5"][-1], mas["MA10"][-1], mas["MA20"][-1], mas["MA60"][-1]
    price = closes[-1]

    if math.isnan(ma5) or math.isnan(ma10) or math.isnan(ma20) or math.isnan(ma60):
        return 0.5

    alignments = [ma5 > ma10, ma10 > ma20, ma20 > ma60, price > ma5]
    bullish_count = sum(alignments)
    bearish_count = sum(not a for a in [ma5 < ma10, ma10 < ma20, ma20 < ma60, price < ma5])

    if bullish_count == 4:
        return 1.0
    elif bearish_count == 4:
        return 0.0
    elif bullish_count >= 3:
        return 0.75
    elif bullish_count >= 2:
        return 0.55
    elif bearish_count >= 3:
        return 0.20
    elif bearish_count >= 2:
        return 0.40
    return 0.45  # 缠绕


def _ma_slope_score(ma_values: list[float], lookback: int = 5) -> float:
    """MA20 近 N 日斜率，归一化到 0-1"""
    valid = [v for v in ma_values[-lookback:] if not math.isnan(v)]
    if len(valid) < 2:
        return 0.5

    slope = (valid[-1] - valid[0]) / valid[0] if valid[0] > 0 else 0
    # 截断到 [-0.03, 0.03] → [0, 1]
    clipped = max(-0.03, min(0.03, slope))
    return (clipped + 0.03) / 0.06


def _calc_adx(bars: list[DailyBar], period: int = 14) -> float:
    """
    简化 ADX 计算。

    返回 0-1 趋势强度 (ADX/100)
    """
    n = len(bars)
    if n < period + 1:
        return 0.0

    tr = []
    plus_dm = []
    minus_dm = []

    for i in range(1, n):
        h, l = bars[i].high, bars[i].low
        h_prev, l_prev = bars[i - 1].high, bars[i - 1].low
        c_prev = bars[i - 1].close

        # True Range
        tr.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))

        # Directional Movement
        up = h - h_prev
        down = l_prev - l

        if up > down and up > 0:
            plus_dm.append(up)
        else:
            plus_dm.append(0)

        if down > up and down > 0:
            minus_dm.append(down)
        else:
            minus_dm.append(0)

    tr = [0.0] + tr  # align with bars
    plus_dm = [0.0] + plus_dm
    minus_dm = [0.0] + minus_dm

    # Wilder smoothing
    atr = sum(tr[1:period + 1])
    atr_pdm = sum(plus_dm[1:period + 1])
    atr_mdm = sum(minus_dm[1:period + 1])

    adx_values = []
    for i in range(period, n):
        atr = atr - atr / period + tr[i]
        atr_pdm = atr_pdm - atr_pdm / period + plus_dm[i]
        atr_mdm = atr_mdm - atr_mdm / period + minus_dm[i]

        pdi = (atr_pdm / atr) * 100 if atr > 0 else 0
        mdi = (atr_mdm / atr) * 100 if atr > 0 else 0

        dx = abs(pdi - mdi) / (pdi + mdi) * 100 if (pdi + mdi) > 0 else 0
        adx_values.append(dx)

    if not adx_values:
        return 0.0

    # 用 EMA 平滑 ADX
    adx_ema = calc_ema(adx_values, period)
    latest_adx = adx_ema[-1]

    return min(1.0, latest_adx / 100.0)


def classify_trend(closes: list[float], mas: dict, bars: list[DailyBar]) -> TrendClassification:
    """
    综合趋势分类。

    返回 TrendClassification:
      - type: "uptrend" | "downtrend" | "range"
      - strength: 0-1 趋势综合强度
      - ma_alignment: 均线排列得分
      - ma_slope: MA20 斜率得分
      - adx: ADX 趋势强度
    """
    alignment = _ma_alignment(closes, mas)
    slope = _ma_slope_score(mas.get("MA20", [0.0] * len(closes)))
    adx = _calc_adx(bars)
    price = closes[-1]

    signals = []

    # 趋势类型判定
    if math.isnan(mas["MA5"][-1]) or math.isnan(mas["MA20"][-1]):
        trend_type = "range"
    elif alignment >= 0.75:
        trend_type = "uptrend"
    elif alignment <= 0.25:
        trend_type = "downtrend"
    else:
        trend_type = "range"

    # 信号生成
    if trend_type == "uptrend":
        if adx > 0.25:
            signals.append(f"强上涨趋势(ADX={adx*100:.0f})")
        if slope > 0.7:
            signals.append("MA20加速上倾")
        if price > mas["MA20"][-1] and not math.isnan(mas["MA20"][-1]):
            signals.append("价格在MA20上方")
    elif trend_type == "downtrend":
        if adx > 0.25:
            signals.append(f"强下跌趋势(ADX={adx*100:.0f})")
        if slope < 0.3:
            signals.append("MA20加速下倾")
    else:
        if adx < 0.20:
            signals.append("低波动盘整")
        elif alignment > 0.50:
            signals.append("偏多盘整")
        else:
            signals.append("偏空盘整")

    # 综合强度
    strength = alignment * 0.4 + slope * 0.2 + adx * 0.4
    if trend_type == "downtrend":
        strength = 1.0 - strength  # 反转，使高分=好趋势

    return TrendClassification(
        type=trend_type,
        strength=round(strength, 3),
        ma_alignment=round(alignment, 3),
        ma_slope=round(slope, 3),
        adx=round(adx, 3),
        signals=signals,
    )
