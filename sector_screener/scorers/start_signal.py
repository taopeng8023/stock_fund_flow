"""维度一: 启动信号 — 板块新鲜度 + 资金加速度 + 5日加速"""
from sector_screener.config import to_float


def score_start_signal(stock, context):
    """返回 0~1"""
    code = stock.get("f12", "")
    sector_code = stock.get("_sector_code", "")
    md = context.get("stock_multiday", {}).get(code, {})
    f62 = to_float(stock.get("f62"))
    f204_calc = md.get("f62_5d", 0.0)
    f205_calc = md.get("f62_10d", 0.0)

    score = 0.0

    # 板块新鲜度 (40%)
    sect_fresh = context.get("sector_freshness", {}).get(sector_code, 0.5)
    score += sect_fresh * 0.40

    # 个股资金加速度 (35%)
    if f62 > 0:
        total_5d = abs(f204_calc) + f62
        if total_5d > 0:
            today_ratio_5d = f62 / total_5d
            if 0.35 <= today_ratio_5d <= 0.55:
                score += 0.35
            elif 0.25 <= today_ratio_5d < 0.35:
                score += 0.25
            elif 0.55 < today_ratio_5d <= 0.70:
                score += 0.20
            elif today_ratio_5d > 0.70:
                score += 0.10
        else:
            score += 0.15
    else:
        score += 0.15

    # 5日加速 (25%)
    total_10d = abs(f205_calc) + f62 + abs(f204_calc)
    if total_10d > 0:
        ratio_5d_10d = abs(f62 + f204_calc) / total_10d
        if ratio_5d_10d > 0.50 and (f62 + f204_calc) > 0:
            score += 0.25

    return max(0.1, min(1.0, score))
