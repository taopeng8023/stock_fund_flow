"""维度二十: 融资力度 — f174/f175 融资买入=杠杆资金看多"""
from sector_screener.config import to_float

def score_margin_long(stock, context):
    """融资买入占比高=杠杆资金积极做多"""
    f174 = to_float(stock.get("f174"))  # 融资买入额
    f175 = to_float(stock.get("f175"))  # 融资买入占比(%)
    f168 = to_float(stock.get("f168"))  # 融资净买入
    f169 = to_float(stock.get("f169"))  # 融资净买入占比(%)
    f20 = to_float(stock.get("f20"))    # 总市值

    # 无融资数据 → 中性
    if f174 == 0 and f168 == 0:
        return 0.50

    # 融资买入占比(f175)直接反映杠杆资金参与度
    if f175 > 15:
        return 0.80   # 高融资买入占比, 杠杆资金积极
    elif f175 > 10:
        return 0.70
    elif f175 > 5:
        return 0.60

    # 融资净买入占比(f169)
    if f169 > 8:
        return 0.85
    elif f169 > 5:
        return 0.75
    elif f169 > 2:
        return 0.65

    # 融资净买入为负 = 杠杆资金出逃
    if f168 < -5000000:
        return 0.25
    elif f168 < 0:
        return 0.40

    return 0.50
