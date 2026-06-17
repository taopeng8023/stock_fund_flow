"""维度十四: 资金流入加速度 — 3日均 vs 10日均流入比"""
from sector_screener.config import to_float, range_score


def score_flow_accel(stock, context):
    """返回 0~1"""
    code = stock.get("f12", "")
    md = context.get("stock_multiday", {}).get(code, {})
    f62 = to_float(stock.get("f62"))

    if not md.get("daily_f62"):
        return 0.5

    daily = md["daily_f62"]
    d1 = daily[0] if len(daily) >= 1 else 0
    d2 = daily[1] if len(daily) >= 2 else 0
    avg_3d = (f62 + d1 + d2) / 3 if (f62 + d1 + d2) != 0 else 0
    all_days = [f62] + daily[:9]
    avg_10d = sum(all_days) / len(all_days) if all_days else 0

    if avg_10d > 0 and avg_3d > 0:
        accel_ratio = avg_3d / avg_10d
        accel_ratio = max(0.1, min(4.0, accel_ratio))
    elif avg_3d > 0:
        accel_ratio = 2.0
    else:
        accel_ratio = 0.5

    stock["_accel_ratio"] = round(accel_ratio, 2)
    return range_score(accel_ratio, 1.3, 2.5, 0.3, 4.0)
