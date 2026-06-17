"""维度二十一: 成交额质量 — f5/f6 量能确认"""
from sector_screener.config import to_float, pct_rank

def score_volume_quality(stock, context):
    """成交额配合涨跌幅=真实资金力度"""
    f5 = to_float(stock.get("f5"))   # 成交量(股)
    f6 = to_float(stock.get("f6"))   # 成交额(元)
    f3 = to_float(stock.get("f3"))   # 涨跌幅
    f20 = to_float(stock.get("f20")) # 总市值

    if f6 <= 0 or f20 <= 0:
        return 0.50

    # 换手金额率 = 成交额/总市值, 反映真实换手力度
    turnover_amt_ratio = (f6 / f20) * 100  # %

    # 温和放量上涨最佳
    if f3 > 1 and 3 < turnover_amt_ratio < 15:
        return 0.80
    elif f3 > 0 and 2 < turnover_amt_ratio < 20:
        return 0.70

    # 无量上涨 = 虚假
    if f3 > 2 and turnover_amt_ratio < 1:
        return 0.30
    # 放量下跌 = 出货
    if f3 < -2 and turnover_amt_ratio > 10:
        return 0.20

    return 0.50
