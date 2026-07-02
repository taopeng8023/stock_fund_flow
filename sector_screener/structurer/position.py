"""
结构位置判定 — 中枢检测 + 位置评分 + 突破检测 + 乖离率

核心概念:
  中枢 = 多段走势的价格重叠区间（市场共识价格）
  位置 = 当前价在中枢/均线结构中的相对位置
"""
import math
from dataclasses import dataclass

from .price_loader import DailyBar


@dataclass
class StructurePosition:
    score: float                # 0-1 位置得分
    position_pct: float         # 在60日区间的位置百分比
    zhongshu_low: float         # 中枢下沿
    zhongshu_high: float        # 中枢上沿
    deviation_ma20: float       # 乖离率 (vs MA20)
    deviation_ma60: float       # 乖离率 (vs MA60)
    bb_position: float          # Bollinger 位置 (0=下轨, 0.5=中轨, 1=上轨)
    is_breakout: bool           # 是否突破中枢上沿
    breakout_signals: list[str] # 突破信号
    signals: list[str]          # 关键信号


def _find_local_extrema(prices: list[float], window: int = 5) -> tuple[list[int], list[int]]:
    """找局部高点和低点的索引"""
    n = len(prices)
    highs, lows = [], []
    half = window // 2

    for i in range(half, n - half):
        seg = prices[i - half:i + half + 1]
        if prices[i] == max(seg) and seg.count(prices[i]) == 1:
            highs.append(i)
        if prices[i] == min(seg) and seg.count(prices[i]) == 1:
            lows.append(i)

    return highs, lows


def detect_consolidation(bars: list[DailyBar], lookback: int = 60) -> dict:
    """
    检测近期中枢（价格重叠区间）。

    算法:
      1. 找 lookback 内的局部高点和低点
      2. 取 lookback/2 之后的极值做重叠分析
      3. 中枢 = 后续走势段低点的最小值 ~ 高点的最大值 的重叠区间

    返回:
      {"low": float, "high": float, "width_pct": float, "bars_in_zone": int}
    """
    n = min(lookback, len(bars))
    segment = bars[-n:]
    prices = [b.close for b in segment]

    highs_idx, lows_idx = _find_local_extrema(prices, window=7)

    if len(highs_idx) < 2 or len(lows_idx) < 2:
        # 退化为60日高低区间
        return {
            "low": min(b.low for b in segment),
            "high": max(b.high for b in segment),
            "width_pct": 0.0,
            "bars_in_zone": n,
        }

    # 只用后半段的极值找中枢重叠
    mid = n // 2
    recent_highs = [prices[i] for i in highs_idx if i >= mid]
    recent_lows = [prices[i] for i in lows_idx if i >= mid]

    if not recent_highs or not recent_lows:
        return {
            "low": min(b.low for b in segment),
            "high": max(b.high for b in segment),
            "width_pct": 0.0,
            "bars_in_zone": n,
        }

    # 中枢: 最近几个高点的低值 ~ 最近几个低点的高值（重叠区）
    zhongshu_high = sorted(recent_highs)[len(recent_highs) // 2]  # 中位数高点
    zhongshu_low = sorted(recent_lows, reverse=True)[len(recent_lows) // 2]  # 中位数低点

    if zhongshu_low > zhongshu_high:
        zhongshu_low, zhongshu_high = zhongshu_high, zhongshu_low

    width_pct = (zhongshu_high - zhongshu_low) / zhongshu_low if zhongshu_low > 0 else 0

    # 统计中枢内 K 线数
    bars_in_zone = sum(1 for p in prices if zhongshu_low <= p <= zhongshu_high)

    return {
        "low": round(zhongshu_low, 2),
        "high": round(zhongshu_high, 2),
        "width_pct": round(width_pct, 4),
        "bars_in_zone": bars_in_zone,
    }


def calc_position_score(
    bars: list[DailyBar],
    indicators: dict,
    trend_type: str,
) -> StructurePosition:
    """
    综合结构位置评分。

    评分逻辑 (0-1):
      0.00-0.30: 中枢下方/超跌区域 → 低吸机会 (高分)
      0.30-0.50: 中枢下沿附近/均线支撑 → 较好的买点
      0.50-0.65: 中枢内部 → 中性
      0.65-0.80: 中枢上方未远离 → 略高
      0.80-1.00: 远离中枢/上轨外 → 追高风险 (低分)

    同时对上涨趋势放宽、下跌趋势收紧。
    """
    closes = indicators["closes"]
    price = closes[-1]
    signals = []

    # 1. 中枢检测
    zhongshu = detect_consolidation(bars)
    zl, zh = zhongshu["low"], zhongshu["high"]

    # 2. 60日区间位置 (类似现有 position scorer)
    high_60 = max(b.high for b in bars[-60:])
    low_60 = min(b.low for b in bars[-60:])
    if high_60 > low_60:
        position_pct = (price - low_60) / (high_60 - low_60)
    else:
        position_pct = 0.5

    # 3. 乖离率
    ma20 = indicators["ma"]["MA20"][-1]
    ma60 = indicators["ma"]["MA60"][-1]
    deviation_ma20 = (price - ma20) / ma20 if ma20 and not math.isnan(ma20) else 0
    deviation_ma60 = (price - ma60) / ma60 if ma60 and not math.isnan(ma60) else 0

    # 4. Bollinger 位置
    bb_lower = indicators["bb_lower"][-1]
    bb_upper = indicators["bb_upper"][-1]
    if bb_upper and not math.isnan(bb_upper) and bb_upper > bb_lower:
        bb_position = (price - bb_lower) / (bb_upper - bb_lower)
    else:
        bb_position = 0.5

    # 5. 位置评分
    if position_pct < 0.15:
        pos_score = 0.85  # 极低位 — 高评分
        signals.append("极低位(>85%分位)")
    elif position_pct < 0.30:
        pos_score = 0.75
        signals.append("低位区间")
    elif position_pct < 0.50:
        pos_score = 0.60  # 中枢下方/中低位
    elif position_pct < 0.70:
        pos_score = 0.50  # 中枢/中位
    elif position_pct < 0.85:
        pos_score = 0.35  # 偏高
        signals.append("高位区间")
    else:
        pos_score = 0.15  # 极高位 — 低评分
        signals.append("极高位(追高风险)")

    # 乖离率修正 (趋势感知)
    if abs(deviation_ma20) > 0.15:
        if trend_type == "uptrend" and deviation_ma20 > 0:
            pos_score -= 0.03  # 上涨趋势中乖离正常, 只轻微扣分
        elif trend_type == "downtrend" and deviation_ma20 < 0:
            pos_score -= 0.03  # 下跌趋势中负乖离也正常
        else:
            pos_score -= 0.08
        direction = "上" if deviation_ma20 > 0 else "下"
        signals.append(f"MA20大幅乖离({deviation_ma20*100:+.1f}%)")

    # Bollinger 修正 (趋势感知)
    if bb_position > 1.0:
        if trend_type == "uptrend":
            pos_score -= 0.02  # 上涨趋势突破上轨 = 强势, 不重罚
        else:
            pos_score -= 0.08
        signals.append("突破布林上轨")
    elif bb_position < 0.0:
        pos_score += 0.10
        signals.append("跌破布林下轨(超跌)")

    # 趋势适配
    if trend_type == "uptrend":
        pos_score += 0.08  # 上涨趋势中允许稍高位
    elif trend_type == "downtrend":
        pos_score -= 0.05  # 下跌趋势中低位更谨慎

    pos_score = max(0.0, min(1.0, pos_score))

    # 6. 突破检测
    breakout_signals = []
    is_breakout = False
    volume_ma = sum(b.volume for b in bars[-20:]) / 20 if len(bars) >= 20 else bars[-1].volume

    if price > zh and bars[-1].volume > volume_ma * 1.5:
        is_breakout = True
        breakout_signals.append(f"放量突破中枢上沿({zh:.2f})")
    if price > ma20 and indicators["macd_dif"][-1] > indicators["macd_dea"][-1] \
            and indicators["macd_dif"][-2] <= indicators["macd_dea"][-2]:
        breakout_signals.append("MACD金叉")
        is_breakout = True

    if price < zl and bars[-1].volume > volume_ma * 1.5:
        breakout_signals.append(f"放量跌破中枢下沿({zl:.2f})")

    return StructurePosition(
        score=round(pos_score, 3),
        position_pct=round(position_pct, 3),
        zhongshu_low=round(zl, 2),
        zhongshu_high=round(zh, 2),
        deviation_ma20=round(deviation_ma20, 4),
        deviation_ma60=round(deviation_ma60, 4),
        bb_position=round(min(1.0, max(0.0, bb_position)), 3),
        is_breakout=is_breakout,
        breakout_signals=breakout_signals,
        signals=signals,
    )
