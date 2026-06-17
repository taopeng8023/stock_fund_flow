"""维度四: 板块共振 — 单日排名 + 持续性 + 概念板块叠加"""
from data_collector.fetchers.base import load_json


def score_sector(stock, context):
    """返回 0~1"""
    sector_code = stock.get("_sector_code", "")
    date_str = context.get("_date_str", "")

    score = context.get("sector_flows", {}).get(sector_code, 0.5)

    # 板块持续性降权
    sector_persistence = context.get("sector_persistence", {})
    if sector_persistence:
        persist = sector_persistence.get(sector_code, 0.7)
        score = score * persist

    # 概念板块叠加
    s_concept = 0.5
    try:
        concept_rows = load_json(date_str, "concept_flow")
        if concept_rows:
            concept_map = {r.get("f14", ""): r.get("f62", 0) or 0 for r in concept_rows}
            all_flows = list(concept_map.values())
            if all_flows and max(all_flows) > min(all_flows):
                from sector_screener.config import pct_rank
                concept_name = stock.get("_sector_name", "")
                if concept_name and concept_name in concept_map:
                    s_concept = pct_rank(all_flows, concept_map[concept_name])
    except Exception:
        pass

    return score * 0.7 + s_concept * 0.3
