"""维度六: 分析师共识 — 评级共识 + EPS增长"""
def score_analyst(stock, context):
    """返回 0~1"""
    code = stock.get("f12", "")
    a = context.get("analyst_data", {}).get(code, {})
    s_consensus = a.get("consensus", 0.5)
    eps_growth = a.get("eps_growth", 0.0)

    if eps_growth != 0:
        s_eps_growth = max(0.0, min(1.0, (eps_growth + 0.2) / 0.4))
    else:
        s_eps_growth = 0.5

    return s_consensus * 0.65 + s_eps_growth * 0.35
