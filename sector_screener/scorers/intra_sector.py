"""维度十二: 行业内相对强度 — f100 分组 percentile"""
def score_intra_sector(stock, context):
    """返回 0~1"""
    code = stock.get("f12", "")
    return context.get("intra_sector_rank", {}).get(code, 0.5)
