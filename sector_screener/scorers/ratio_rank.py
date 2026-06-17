"""维度十一: 主力占比排名 — 全市场 f184 排名"""
def score_ratio_rank(stock, context):
    """返回 0~1"""
    code = stock.get("f12", "")
    return context.get("ratio_rank", {}).get(code, 0.5)
