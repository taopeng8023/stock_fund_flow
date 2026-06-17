"""主力占比排名数据加载"""
from fetchers.base import load_json


def load_ratio_rank(date_str):
    """→ {code: percentile_score}"""
    rows = load_json(date_str, "rank_ratio")
    if rows is None:
        print(f"  占比排名: 无数据")
        return {}
    ratio_rank = {}
    total = len(rows)
    for i, r in enumerate(rows):
        code = r.get("f12", "")
        ratio_rank[code] = round(1.0 - i / total, 4)
    print(f"  占比排名: {len(ratio_rank)} 条映射")
    return ratio_rank
