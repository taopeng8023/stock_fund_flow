"""分析师预测数据加载"""
from data_collector.fetchers.base import load_json


def load_analyst_data(date_str):
    """→ {code: {consensus, eps_growth, org_num}}"""
    rows = load_json(date_str, "analyst_forecast")
    if rows is None:
        print(f"  分析师数据不存在: data/{date_str}/analyst_forecast.json")
        return {}
    analyst = {}
    for r in rows:
        code = r.get("SECURITY_CODE", "")
        org_num = r.get("RATING_ORG_NUM") or 0
        buy = r.get("RATING_BUY_NUM") or 0
        add = r.get("RATING_ADD_NUM") or 0
        neutral = r.get("RATING_NEUTRAL_NUM") or 0
        reduce_ = r.get("RATING_REDUCE_NUM") or 0
        sale = r.get("RATING_SALE_NUM") or 0
        eps1 = r.get("EPS1") or 0
        eps2 = r.get("EPS2") or 0

        total = buy + add + neutral + reduce_ + sale
        if org_num >= 3 and total > 0:
            consensus = (buy * 1.0 + add * 0.75 + neutral * 0.25 + reduce_ * 0.0 + sale * (-0.5)) / total
        else:
            consensus = 0.5

        if eps1 and abs(eps1) > 0.01:
            eps_growth = (eps2 - eps1) / abs(eps1)
            eps_growth = max(-0.5, min(2.0, eps_growth))
        else:
            eps_growth = 0.0

        analyst[code] = {
            "consensus": round(consensus, 3),
            "eps_growth": round(eps_growth, 3),
            "org_num": org_num,
        }
    print(f"  分析师: {len(analyst)} 条映射")
    return analyst
