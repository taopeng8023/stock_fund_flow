"""
综合结构评分 — 汇总趋势/位置/背离 三个维度 → 0-1 分数

用法:
    from sector_screener.structurer.scorer import structure_score
    from sector_screener.structurer.price_loader import load_daily_bars

    bars = load_daily_bars("600519")
    result = structure_score("600519", bars)
    print(result["score"], result["trend_type"], result["signals"])
"""
import logging

from .price_loader import DailyBar
from .indicators import compute_all
from .trend_classify import classify_trend
from .position import calc_position_score
from .divergence import detect_all

logger = logging.getLogger(__name__)

# 维度权重（可回测调优）
DIM_WEIGHTS = {
    "trend_direction": 0.25,  # 趋势方向
    "trend_strength": 0.15,   # 趋势强度 (ADX/MA斜率)
    "structure_position": 0.25,  # 结构位置
    "divergence": 0.20,       # 背离信号
    "breakout": 0.10,         # 突破信号
    "deviation": 0.05,        # 乖离率惩罚
}


def structure_score(code: str, bars: list[DailyBar]) -> dict:
    """
    计算单股综合结构评分。

    Args:
        code: 股票代码
        bars: 日线 OHLCV 数据 (需 >= 60 根)

    Returns:
        {
            "score": 0.72,          # 0-1 综合结构评分
            "trend_type": "uptrend", # 趋势类型
            "trend_strength": 0.65,  # 趋势强度
            "position_score": 0.55,   # 位置得分
            "divergence_score": 0.60, # 背离得分
            "signals": [...],         # 所有信号
        }
    """
    if len(bars) < 60:
        logger.warning(f"{code}: K线不足60根({len(bars)}), 返回默认分")
        return {
            "score": 0.5,
            "trend_type": "range",
            "trend_strength": 0.5,
            "position_score": 0.5,
            "divergence_score": 0.5,
            "signals": ["数据不足"],
        }

    # 1. 预计算全部指标
    ind = compute_all(bars)
    closes = ind["closes"]

    # 2. 趋势分类
    trend = classify_trend(closes, ind["ma"], bars)

    # 3. 结构位置
    position = calc_position_score(bars, ind, trend.type)

    # 4. 背离检测
    divergence = detect_all(bars, ind)

    # 5. 汇总评分
    # 趋势方向得分
    trend_dir_score = trend.ma_alignment  # 多头=高分
    if trend.type == "downtrend":
        trend_dir_score = 1.0 - trend_dir_score  # 空头趋势反转, 做反向

    # 趋势强度得分 (取 ADX 和 MA 斜率的均值)
    trend_strength_score = (trend.adx + trend.ma_slope) / 2

    # 突破得分
    breakout_score = 0.75 if position.is_breakout else 0.5
    if position.breakout_signals:
        breakout_score = 0.8

    # 乖离率惩罚
    dev_penalty = 1.0 - min(0.5, abs(position.deviation_ma20) * 5 + abs(position.deviation_ma60) * 3)

    dim_scores = {
        "trend_direction": trend_dir_score,
        "trend_strength": trend_strength_score,
        "structure_position": position.score,
        "divergence": divergence.score,
        "breakout": breakout_score,
        "deviation": dev_penalty,
    }

    total = sum(DIM_WEIGHTS[k] * dim_scores[k] for k in DIM_WEIGHTS)
    total = round(total, 3)

    # 6. 汇总信号
    all_signals = trend.signals + position.signals + divergence.signals + position.breakout_signals
    if not all_signals:
        all_signals = ["无特殊信号"]

    return {
        "score": total,
        "trend_type": trend.type,
        "trend_strength": round(trend_strength_score, 3),
        "position_score": position.score,
        "divergence_score": divergence.score,
        "ma_alignment": trend.ma_alignment,
        "adx": trend.adx,
        "deviation_ma20": position.deviation_ma20,
        "deviation_ma60": position.deviation_ma60,
        "zhongshu_low": position.zhongshu_low,
        "zhongshu_high": position.zhongshu_high,
        "is_breakout": position.is_breakout,
        "signals": all_signals,
        "dim_scores": dim_scores,
    }


# ── CLI 入口 (用于单股测试) ──
if __name__ == "__main__":
    import sys
    from .price_loader import load_daily_bars

    code = sys.argv[1] if len(sys.argv) > 1 else "600519"
    print(f"=== 结构分析: {code} ===\n")

    bars = load_daily_bars(code)
    if not bars:
        print("数据加载失败")
        sys.exit(1)

    print(f"K线数: {len(bars)}, 数据源: {bars[-1].source}")
    result = structure_score(code, bars)

    print(f"\n📊 综合评分: {result['score']}")
    print(f"   趋势: {result['trend_type']} (强度={result['trend_strength']})")
    print(f"   ADX: {result['adx']}, MA排列: {result['ma_alignment']}")
    print(f"   位置: {result['position_score']} (乖离 MA20={result['deviation_ma20']*100:+.1f}%)")
    print(f"   背离: {result['divergence_score']}")
    print(f"   中枢: {result['zhongshu_low']} ~ {result['zhongshu_high']}")
    print(f"   突破: {'是' if result['is_breakout'] else '否'}")
    print(f"   信号: {', '.join(result['signals'])}")
