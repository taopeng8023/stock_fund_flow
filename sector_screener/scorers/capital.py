"""维度二: 主力资金强度 — f62+f66+f72+f184+f69 五维 percentile + 超大单质量"""
from sector_screener.config import to_float, pct_rank


def score_capital(stock, context):
    """返回 0~1。上限 0.95（允许 P13 豁免阈值 0.92 可达）"""
    f62 = to_float(stock.get("f62"))
    f66 = to_float(stock.get("f66"))
    f72 = to_float(stock.get("f72"))
    f184 = to_float(stock.get("f184"))
    f69 = to_float(stock.get("f69"))

    f62_vals = context.get("_f62_vals", [f62])
    f66_vals = context.get("_f66_vals", [f66])
    f72_vals = context.get("_f72_vals", [f72])
    f184_vals = context.get("_f184_vals", [f184])
    f69_vals = context.get("_f69_vals", [f69])

    s_flow = pct_rank(f62_vals, f62)
    s_super_flow = pct_rank(f66_vals, f66)
    s_big_flow = pct_rank(f72_vals, f72)
    s_ratio = pct_rank(f184_vals, f184)
    s_super_ratio = pct_rank(f69_vals, f69)

    if f62 > 0:
        super_quality = max(0.0, min(1.0, f66 / f62))
    else:
        super_quality = 0.0

    raw = (s_flow * 0.30 + s_super_flow * 0.20 + s_big_flow * 0.15 +
           s_ratio * 0.20 + s_super_ratio * 0.10 + super_quality * 0.05)
    # 软上限: 0.95 以上用平方根压缩, 避免极端值主导同时允许 P13 豁免
    if raw > 0.95:
        raw = 0.95 + (raw - 0.95) * 0.2
    return min(1.0, raw)
