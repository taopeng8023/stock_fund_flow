"""维度十九: 融券压力 — f170/f172 融券净卖出=做空信号"""
from sector_screener.config import to_float

def score_margin_short(stock, context):
    """融券净卖出越大=做空压力越大, 评分越低"""
    f170 = to_float(stock.get("f170"))  # 融券净卖出
    f172 = to_float(stock.get("f172"))  # 融券卖出额
    f62 = to_float(stock.get("f62"))    # 主力净流入

    # 无融券数据 → 中性
    if f170 == 0 and f172 == 0:
        return 0.50

    # 融券净卖出>0 = 做空力量
    if f170 > 0:
        # 融券卖出相对于主力流入的比例
        ratio = abs(f170) / max(abs(f62), 1)
        if ratio > 2:
            return 0.10   # 融券远超主力流入, 强烈做空
        elif ratio > 1:
            return 0.25
        elif ratio > 0.5:
            return 0.40

    # 融券净买入(负值) = 空头回补, 偏多
    if f170 < -5000000:
        return 0.60
    elif f170 < 0:
        return 0.55

    return 0.50
