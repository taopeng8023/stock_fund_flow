"""业绩预告数据加载 → {code: {type, growth_lower, growth_upper}}"""
from data_collector.fetchers.base import load_json
from sector_screener.config import to_float


TYPE_MAP = {
    "预增": 1.0, "扭亏": 0.9, "略增": 0.7, "续盈": 0.5,
    "不确定": 0.5, "略减": 0.3, "预减": 0.1, "首亏": 0.0, "续亏": 0.0,
}


def load_earnings_forecast(date_str):
    """加载最新业绩预告 → 每只股票的预告类型 + 增长幅度"""
    rows = load_json(date_str, "earnings_forecast")
    if not rows:
        return {}

    result = {}
    for r in rows:
        code = r.get("SECURITY_CODE", "")
        if not code:
            continue
        # 只保留最新一条
        if code in result:
            continue
        pred_type = r.get("PREDICT_TYPE", "")
        result[code] = {
            "type": pred_type,
            "type_score": TYPE_MAP.get(pred_type, 0.5),
            "growth_lower": to_float(r.get("ADD_AMP_LOWER")),
            "growth_upper": to_float(r.get("ADD_AMP_UPPER")),
            "notice_date": r.get("NOTICE_DATE", ""),
        }

    good = sum(1 for v in result.values() if v["type_score"] >= 0.7)
    bad = sum(1 for v in result.values() if v["type_score"] <= 0.3)
    print(f"  业绩预告: {len(result)} 只 (利好{good}, 利空{bad})")
    return result
