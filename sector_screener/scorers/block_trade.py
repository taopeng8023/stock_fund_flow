"""维度十五: 大宗交易信号 — 溢价=看多, 深折价=回避"""
def score_block_trade(stock, context):
    code = stock.get("f12", "")
    bt = context.get("block_trade", {}).get(code, {})
    if not bt:
        return 0.5  # 无大宗交易，中性

    # 溢价买入 = 强烈看多
    if bt.get("has_premium_buy"):
        avg_p = bt["avg_premium"]
        if avg_p > 5:
            return 0.85
        elif avg_p > 2:
            return 0.75
        return 0.65

    # 深折价 = 利空
    if bt.get("has_deep_discount"):
        return 0.20

    # 机构买方
    if bt.get("inst_buy_count", 0) > 0:
        return 0.60

    return 0.50
