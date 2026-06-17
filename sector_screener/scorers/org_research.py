"""维度十六: 机构调研热度 — 密集调研=机构关注"""
def score_org_research(stock, context):
    code = stock.get("f12", "")
    count = context.get("org_research", {}).get(code, 0)
    if count >= 5:
        return 0.90
    elif count >= 3:
        return 0.75
    elif count >= 2:
        return 0.65
    elif count >= 1:
        return 0.55
    return 0.50  # 无调研
