"""涨停/候选 分流 — 涨停→观察池 / 候选→评分"""
from sector_screener.config import LIMIT_UP_PCT, to_float, is_main_board
from sector_screener.filters.basic import basic_filter
from sector_screener.filters.trend import check_uptrend


def split_stocks(stocks, price_history):
    """将成分股分为三组: limit_up_pool / candidate_pool / excluded_pool"""
    limit_up_pool = []
    candidate_pool = []
    excluded_pool = []

    for s in stocks:
        code = s.get("f12", "")
        chg = to_float(s.get("f3"))
        price = to_float(s.get("f2"))
        vol_ratio = to_float(s.get("f10"))

        if not is_main_board(code):
            continue

        # 涨停 → 观察池
        if chg >= LIMIT_UP_PCT:
            s["_pool"] = "limit_up_observe"
            s["_exclude_reason"] = ""
            limit_up_pool.append(s)
            continue

        # 基本面风控
        passed, reasons = basic_filter(s)
        if not passed:
            s["_pool"] = "excluded"
            s["_exclude_reason"] = "; ".join(reasons)
            excluded_pool.append(s)
            continue

        # 右向趋势硬过滤
        closes = price_history.get(code, [])
        trend_ok, trend_detail = check_uptrend(price, closes, chg, vol_ratio)
        if not trend_ok:
            s["_pool"] = "excluded"
            s["_exclude_reason"] = f"趋势不符({trend_detail})"
            excluded_pool.append(s)
            continue

        s["_pool"] = "candidate"
        s["_exclude_reason"] = ""
        candidate_pool.append(s)

    return limit_up_pool, candidate_pool, excluded_pool
