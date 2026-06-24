"""
因子 #24: 行业板块价格共振 — 从个股价格聚合计算板块多日涨幅

当前 sector 因子（#4）只看资金流排名，不看板块价格表现。
本因子互补：同一板块资金流入 + 价格不涨 = 假信号。

数据来源: 个股 price_history（已加载），按 _sector_code 聚合计算板块均回报。
         无需新 API。板块价格 = 板块内个股中位 N 日回报。
"""
from collections import defaultdict
from sector_screener.config import to_float, pct_rank


def _compute_stock_ret(closes, days):
    """从收盘价序列计算 N 日回报。closes[0]=最新价。"""
    if len(closes) > days and closes[days] and closes[days] > 0:
        return (closes[0] - closes[days]) / closes[days]
    return None


def _build_sector_price_returns(candidates, price_history):
    """从候选池个股的 price_history 聚合板块价格回报。

    每个板块取个股回报的中位数（抗极端值）。

    Returns:
        {sector_code: {"name": "", "ret_5d": 0.05, "ret_10d": 0.12}, ...}
    """
    sector_rets = defaultdict(lambda: {"ret5d_list": [], "ret10d_list": []})

    for s in candidates:
        code = s.get("f12", "")
        sector_code = s.get("_sector_code", "")
        if not sector_code:
            continue
        closes = price_history.get(code, [])
        if not closes:
            continue

        r5 = _compute_stock_ret(closes, 4)
        r10 = _compute_stock_ret(closes, 9)
        name = s.get("_sector_name", sector_code)

        if r5 is not None:
            sector_rets[sector_code]["ret5d_list"].append(r5)
            sector_rets[sector_code]["name"] = name
        if r10 is not None:
            sector_rets[sector_code]["ret10d_list"].append(r10)

    result = {}
    for code, data in sector_rets.items():
        r5_list = data["ret5d_list"]
        r10_list = data["ret10d_list"]
        if not r5_list and not r10_list:
            continue
        r5_list.sort()
        r10_list.sort()
        mid5 = r5_list[len(r5_list) // 2] if r5_list else 0.0
        mid10 = r10_list[len(r10_list) // 2] if r10_list else 0.0
        result[code] = {
            "name": data["name"],
            "ret_5d": round(mid5, 4),
            "ret_10d": round(mid10, 4),
        }
    return result


def score_sector_price(stock, context):
    """返回 0~1

    板块价格数据由 engine.build_context 预计算到 context["sector_price_returns"]。
    """
    sector_code = stock.get("_sector_code", "")
    spr = context.get("sector_price_returns", {})

    if not spr or sector_code not in spr:
        return 0.5

    sec = spr[sector_code]
    ret5d_vals = context.get("_sector_ret5d_vals", [sec.get("ret_5d", 0)])
    ret10d_vals = context.get("_sector_ret10d_vals", [sec.get("ret_10d", 0)])

    s5 = pct_rank(ret5d_vals, sec.get("ret_5d", 0))
    s10 = pct_rank(ret10d_vals, sec.get("ret_10d", 0))

    return round(s5 * 0.50 + s10 * 0.50, 4)
