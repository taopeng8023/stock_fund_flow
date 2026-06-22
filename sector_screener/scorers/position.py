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

    # 回溯优化: 中位区(0.25-0.65)实际表现与次日收益负相关, 低位区反而正相关
    # 低位=均值回归机会, 中位=方向不明, 高位=回调风险
    if position < 0.10:
        score = 0.25   # 极低位: 有反弹潜力但风险大
    elif 0.10 <= position < 0.25:
        score = 0.80   # 低位区: 均值回归最优 (回溯corr正向)
    elif 0.25 <= position < 0.40:
        score = 0.55   # 中低位: 中性偏正
    elif 0.40 <= position < 0.65:
        score = 0.45   # 中位区: 方向不明 (回溯corr负向, 原0.85→0.45)
    elif 0.65 <= position < 0.85:
        score = 0.35   # 偏高位: 回调风险
    else:
        score = 0.15   # 极高位: 强回调风险

    if len(closes) >= 5:
        ma5 = sum(closes[:5]) / 5
        if price > ma5:
            score = min(1.0, score + 0.05)
        if len(closes) >= 10:
            ma10 = sum(closes[:10]) / 10
            if ma5 > ma10:
                score = min(1.0, score + 0.05)

    return round(score, 3)
