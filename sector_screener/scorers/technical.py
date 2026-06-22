"""维度八: 技术面 — MA多头排列 + 突破(牛熊自适应)"""
from sector_screener.config import to_float


def _calc_technical(price, closes):
    """返回 (ma_align, breakout_score, breakout_20d)"""
    if not closes or len(closes) < 5:
        return 0.5, 0.5, False

    def ma(seq, n):
        return sum(seq[:n]) / n if len(seq) >= n else None

    ma5 = ma(closes, 5)
    ma10 = ma(closes, 10)
    ma20 = ma(closes, 20)

    align_score = 0.0
    if ma5 and ma10 and ma5 > ma10:
        align_score += 0.25
    if ma10 and ma20 and ma10 > ma20:
        align_score += 0.25
    if ma5 and ma20 and ma5 > ma20:
        align_score += 0.25
    if ma5 and price > ma5:
        align_score += 0.25

    breakout_20d = False
    if len(closes) >= 21:
        high_20d = max(closes[1:21])
        breakout_20d = price > high_20d * 0.98

    if len(closes) >= 60:
        high_60d = max(closes[1:61])
        if price > high_60d * 0.98:
            breakout_score = 1.0
        elif breakout_20d:
            breakout_score = 0.8
        else:
            breakout_score = 0.4
    elif breakout_20d:
        breakout_score = 0.8
    elif align_score >= 0.5:
        breakout_score = 0.6
    else:
        breakout_score = 0.3

    return round(align_score, 3), round(breakout_score, 3), breakout_20d


def score_technical(stock, context):
    """返回 0~1"""
    price = to_float(stock.get("f2"))
    code = stock.get("f12", "")
    closes = context.get("price_history", {}).get(code, [])
    regime = context.get("_regime", "range")

    ma_align, breakout_score, breakout_20d = _calc_technical(price, closes)

    # 附加属性 (供 p_factors 使用)
    stock["_ma_align"] = ma_align
    stock["_breakout_20d"] = breakout_20d

    # 回溯优化: MA排列corr=+0.037(最佳单因子), 突破信号噪音多
    return ma_align * 0.60 + breakout_score * 0.40
