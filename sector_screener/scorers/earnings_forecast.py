"""维度十七: 业绩预告 — 超预期=加分, 暴雷=减分"""
def score_earnings(stock, context):
    code = stock.get("f12", "")
    ef = context.get("earnings_forecast", {}).get(code, {})
    if not ef:
        return 0.50
    return ef.get("type_score", 0.50)
