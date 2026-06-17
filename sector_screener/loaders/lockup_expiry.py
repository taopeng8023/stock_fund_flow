"""限售解禁数据加载 → {code: {days_until, ratio}}"""
from data_collector.fetchers.base import load_json
from sector_screener.config import to_float
from datetime import datetime


def load_lockup_expiry(date_str):
    """加载未来限售解禁 → 最近解禁日期和占比"""
    rows = load_json(date_str, "lockup_expiry")
    if not rows:
        return {}

    today = datetime.strptime(date_str, "%Y%m%d")
    result = {}

    for r in rows:
        code = r.get("SECURITY_CODE", "")
        if not code:
            continue
        free_date = r.get("FREE_DATE", "")
        if not free_date:
            continue
        try:
            fd = datetime.strptime(free_date[:10], "%Y-%m-%d")
        except ValueError:
            continue
        days = (fd - today).days
        ratio = to_float(r.get("ADD_LISTSHARES_RATIO"))

        # 保留最早/最大的解禁
        if code not in result or days < result[code]["days"]:
            result[code] = {
                "days": days,
                "ratio": ratio,
                "free_date": free_date[:10],
            }

    # 统计近7日大额解禁
    week_danger = sum(1 for v in result.values() if v["days"] <= 7 and v["ratio"] > 5)
    print(f"  限售解禁: {len(result)} 只, 近7日大额解禁(>5%){week_danger}只")
    return result
