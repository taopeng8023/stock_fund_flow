"""维度九: 龙虎榜 — 上榜 + 机构买入"""
def score_dragon_tiger(stock, context):
    """返回 0~1"""
    code = stock.get("f12", "")
    dt = context.get("dt_data", {}).get(code, {})
    if not dt.get("on_board"):
        return 0.5
    s = 0.6
    if dt.get("has_institution"):
        s += 0.25
    if dt.get("is_main_buy"):
        s += 0.15
    return min(1.0, s)
