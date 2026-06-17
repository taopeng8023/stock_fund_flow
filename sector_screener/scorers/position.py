"""维度五: 位置健康 — 相对60日位置 + MA站上"""
from sector_screener.config import to_float


def score_position(stock, context):
    """返回 0~1"""
    price = to_float(stock.get("f2"))
    code = stock.get("f12", "")
    closes = context.get("price_history", {}).get(code, [])

    if not closes or len(closes) < 5:
        return 0.5

    high_60 = max(closes[:60]) if len(closes) >= 20 else max(closes)
    low_60 = min(closes[:60]) if len(closes) >= 20 else min(closes)
    if high_60 <= low_60:
        return 0.5

    position = (price - low_60) / (high_60 - low_60)
    position = max(0.0, min(1.0, position))

    if position < 0.10:
        score = 0.15
    elif 0.10 <= position < 0.25:
        score = 0.40
    elif 0.25 <= position < 0.40:
        score = 0.70
    elif 0.40 <= position < 0.65:
        score = 0.85
    elif 0.65 <= position < 0.85:
        score = 0.50
    else:
        score = 0.20

    if len(closes) >= 5:
        ma5 = sum(closes[:5]) / 5
        if price > ma5:
            score = min(1.0, score + 0.05)
        if len(closes) >= 10:
            ma10 = sum(closes[:10]) / 10
            if ma5 > ma10:
                score = min(1.0, score + 0.05)

    return round(score, 3)
