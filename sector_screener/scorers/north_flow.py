"""维度十: 北向资金 — 外资方向环境修正"""
def score_north_flow(stock, context):
    """返回 0~1"""
    north_data = context.get("north_data", {})
    net_north = north_data.get("net_north", 0)
    if north_data.get("masked", False):
        return 0.5
    if net_north > 50:
        return 0.85
    elif net_north > 20:
        return 0.70
    elif net_north > 0:
        return 0.60
    elif net_north > -20:
        return 0.40
    elif net_north > -50:
        return 0.25
    return 0.15
