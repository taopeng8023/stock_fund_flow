"""维度七: 多日累计 — 3/5/10日累计 + 连续性"""
from sector_screener.config import to_float, pct_rank


def score_multiday(stock, context):
    """返回 0~1"""
    code = stock.get("f12", "")
    md = context.get("stock_multiday", {}).get(code, {})
    f62 = to_float(stock.get("f62"))

    cum3 = f62 + md.get("f62_5d", 0)
    cum5 = f62 + md.get("f62_5d", 0)
    cum10 = f62 + md.get("f62_10d", 0)

    cum3_vals = context.get("_cum3_vals", [cum3])
    cum5_vals = context.get("_cum5_vals", [cum5])
    cum10_vals = context.get("_cum10_vals", [cum10])

    s_flow_3day = pct_rank(cum3_vals, cum3)
    s_flow_5day = pct_rank(cum5_vals, cum5)
    s_flow_10day = pct_rank(cum10_vals, cum10)

    # 连续性
    daily_all = [f62] + md.get("daily_f62", [])[:4]
    positive_days = sum(1 for v in daily_all if v > 0)
    s_flow_consistency = positive_days / len(daily_all) if daily_all else 0.5

    return s_flow_3day * 0.30 + s_flow_5day * 0.25 + s_flow_10day * 0.20 + s_flow_consistency * 0.25
