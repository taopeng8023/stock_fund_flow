"""基本面风控过滤 — 股价/ST/市值/换手率/量比/主力流入"""
from sector_screener.config import (
    MIN_PRICE, MAX_PRICE, MIN_MAIN_FLOW, MIN_MAIN_RATIO,
    MIN_TURNOVER, MAX_TURNOVER, MIN_VOL_RATIO,
    MIN_MCAP_YI, MAX_MCAP_YI, CANDIDATE_MAX_CHG,
    to_float, is_main_board,
)


def basic_filter(stock):
    """检查单只股票是否通过基本面风控
    返回 (passed: bool, reasons: list[str])
    """
    code = stock.get("f12", "")
    name = stock.get("f14", "")
    chg = to_float(stock.get("f3"))
    price = to_float(stock.get("f2"))
    main_flow = to_float(stock.get("f62"))
    main_ratio = to_float(stock.get("f184"))
    turnover = to_float(stock.get("f8"))
    vol_ratio = to_float(stock.get("f10"))
    mcap = to_float(stock.get("f20"))

    reasons = []

    if not is_main_board(code):
        reasons.append("非主板")

    if chg > CANDIDATE_MAX_CHG:
        reasons.append(f"涨跌幅 {chg:+.1f}% > {CANDIDATE_MAX_CHG}")
    if chg < -5.0:
        reasons.append(f"跌超5% ({chg:+.1f}%)")
    if price < MIN_PRICE:
        reasons.append(f"股价 {price:.2f} < {MIN_PRICE}")
    if price > MAX_PRICE:
        reasons.append(f"股价 {price:.2f} > {MAX_PRICE}")
    if main_flow < MIN_MAIN_FLOW and main_flow >= 0:
        reasons.append(f"主力净流入 {main_flow/1e4:.0f}万 < {MIN_MAIN_FLOW/1e4:.0f}万")
    if main_ratio < MIN_MAIN_RATIO and main_ratio >= 0:
        reasons.append(f"主力占比 {main_ratio:.1f}% < {MIN_MAIN_RATIO}%")
    if turnover > 0 and turnover < MIN_TURNOVER:
        reasons.append(f"换手率 {turnover:.1f}% < {MIN_TURNOVER}%")
    if turnover > MAX_TURNOVER:
        reasons.append(f"换手率 {turnover:.1f}% > {MAX_TURNOVER}%")
    if vol_ratio > 0 and vol_ratio < MIN_VOL_RATIO:
        reasons.append(f"量比 {vol_ratio:.2f} < {MIN_VOL_RATIO}")
    if mcap > 0:
        mcap_yi = mcap / 1e8
        if mcap_yi < MIN_MCAP_YI:
            reasons.append(f"市值 {mcap_yi:.1f}亿 < {MIN_MCAP_YI}亿")
        if mcap_yi > MAX_MCAP_YI:
            reasons.append(f"市值 {mcap_yi:.1f}亿 > {MAX_MCAP_YI}亿")
    if "ST" in str(name) or "*ST" in str(name):
        reasons.append("ST股")

    return (len(reasons) == 0, reasons)
