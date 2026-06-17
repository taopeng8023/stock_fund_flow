"""机构调研数据加载 → {code: research_count}"""
from collections import Counter
from data_collector.fetchers.base import load_json


def load_org_research(date_str):
    """汇总机构调研次数 → 热门调研股票"""
    rows = load_json(date_str, "org_research")
    if not rows:
        return {}

    counts = Counter(r.get("SECURITY_CODE") for r in rows if r.get("SECURITY_CODE"))
    hot = {code: c for code, c in counts.items() if c >= 2}

    print(f"  机构调研: {len(counts)} 只, 热门(>=2次){len(hot)}只")
    return dict(counts)
