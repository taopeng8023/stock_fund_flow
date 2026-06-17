"""维度十八: 限售解禁风险 — 大额解禁=回避"""
def score_lockup(stock, context):
    code = stock.get("f12", "")
    le = context.get("lockup_expiry", {}).get(code, {})
    if not le:
        return 0.50  # 无解禁，中性

    days = le.get("days", 999)
    ratio = le.get("ratio", 0)

    # 7日内大额解禁 → 强烈回避
    if days <= 7 and ratio > 10:
        return 0.05
    elif days <= 7 and ratio > 5:
        return 0.15
    elif days <= 30 and ratio > 10:
        return 0.25
    elif days <= 30 and ratio > 5:
        return 0.35

    return 0.50
