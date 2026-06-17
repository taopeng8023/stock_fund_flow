"""维度十三: 融资净买入 — f168 全市场 percentile"""
def score_margin_net(stock, context):
    """返回 0~1"""
    code = stock.get("f12", "")
    mn = context.get("margin_net_map", {}).get(code, {})
    f168 = mn.get("f168", 0)
    if f168 == 0:
        return 0.5
    return mn.get("percentile", 0.5)
