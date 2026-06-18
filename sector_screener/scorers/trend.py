"""维度三: 趋势确认 — 量比 + 换手率 + 动量 + 短期斜率"""
from sector_screener.config import to_float, range_score


def _calc_short_trend(closes):
    """近5日短期趋势 (-1 ~ 1)"""
    if len(closes) < 5:
        return 0.0
    recent = closes[:5]
    if len(recent) < 2:
        return 0.0
    changes = [(recent[i] - recent[i+1]) / recent[i+1] for i in range(len(recent) - 1)]
    avg_chg = sum(changes) / len(changes)
    return max(-1.0, min(1.0, avg_chg * 50))


def _detect_oversold_bounce(closes, today_chg):
    """超跌反弹检测 → 做多信号"""
    if not closes or len(closes) < 5:
        return False
    if today_chg > 3.0 and len(closes) >= 4:
        cum3 = (closes[0] - closes[3]) / closes[3] if closes[3] > 0 else 0
        if cum3 < -0.03:
            return True
    return False


def score_trend(stock, context):
    """返回 0~1"""
    f10 = to_float(stock.get("f10"))
    f8 = to_float(stock.get("f8"))
    f3 = to_float(stock.get("f3"))
    code = stock.get("f12", "")
    closes = context.get("price_history", {}).get(code, [])

    s_vol_ratio = range_score(f10, 1.5, 4.0, 0.8, 8.0)
    s_turnover = range_score(f8, 5.0, 18.0, 2.0, 25.0)
    s_momentum = range_score(f3, 2.5, 7.0, -2.0, 9.5)
    short_trend = _calc_short_trend(closes)
    # 乘数从 25 降至 3: 避免退化为二值开关, 保留趋势信号的连续分辨力
    s_short = max(0.0, min(1.0, short_trend * 3 + 0.5))

    score = s_vol_ratio * 0.35 + s_turnover * 0.25 + s_momentum * 0.25 + s_short * 0.15
    # 超跌反弹 = 均值回归做多信号, 加分而非扣分
    if _detect_oversold_bounce(closes, f3):
        score = min(1.0, score + 0.08)
    return max(0.0, min(1.0, score))
