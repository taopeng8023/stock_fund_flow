"""右向趋势硬过滤 — MA分析 + 短期趋势"""
from sector_screener.config import to_float


def check_uptrend(price, closes, chg_today, vol_ratio):
    """判断是否为右向（向上）趋势
    返回 (passed: bool, detail: str)
    """
    n = len(closes) if closes else 0

    if n >= 5:
        ma5 = sum(closes[:5]) / 5
        above_ma5 = price > ma5
        ma_ok = True
        if n >= 10:
            ma10 = sum(closes[:10]) / 10
            ma_ok = ma5 > ma10
        else:
            half = n // 2
            a = sum(closes[:half]) / half
            b = sum(closes[half:]) / (n - half)
            ma_ok = a > b
        chg_5d = (closes[0] - closes[4]) / closes[4] if closes[4] > 0 else 0
        reasons = []
        if not above_ma5:
            reasons.append("未站上MA5")
        if not ma_ok:
            reasons.append("均线未多头")
        if chg_5d < -0.08:
            reasons.append(f"近5日跌{chg_5d:.1%}")
        return (not reasons, "; ".join(reasons) if reasons else "")

    if n >= 2:
        mid = n // 2
        recent_avg = sum(closes[:mid]) / mid if mid > 0 else closes[0]
        older_avg = sum(closes[mid:]) / (n - mid) if n > mid else closes[-1]
        trending_up = recent_avg > older_avg
        if trending_up and chg_today > 0:
            return True, ""
        elif not trending_up:
            return False, "近N日未上行"
        else:
            return False, "今日未同步上涨"

    if chg_today > 0.5 and vol_ratio >= 1.2:
        return True, ""
    elif chg_today <= 0:
        return False, "当日未上涨"
    else:
        return False, f"量比不足({vol_ratio:.1f})"
