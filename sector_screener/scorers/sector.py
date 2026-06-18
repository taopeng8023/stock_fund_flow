"""维度四: 板块共振 — 单日排名 + 持续性 + 概念板块叠加 + 板块日内轨迹"""
import csv
import glob
import os
from data_collector.fetchers.base import DATA_ROOT
from sector_screener.config import pct_rank, to_float


def _load_concept_flow_latest(date_str):
    """从 concept_flow_*.csv 最新快照加载概念板块资金流，替代不存在的 concept_flow.json"""
    pattern = os.path.join(DATA_ROOT, date_str, "concept_flow_*.csv")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not files:
        return {}
    concept_map = {}
    with open(files[0], "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = row.get("名称", "")
            flow = to_float(row.get("主力净流入"))
            if name:
                concept_map[name] = flow
    return concept_map


def score_sector(stock, context):
    """返回 0~1
    组成:
      - sector_flows: 行业板块今日排名得分 (70%)
      - concept overlay: 概念板块资金共振 (30% × 0.3 = 9%)
      - intraday sector momentum: 板块日内资金加速 (bonus/penalty, ±0.05)
    """
    sector_code = stock.get("_sector_code", "")
    date_str = context.get("_date_str", "")

    # ── 行业排名得分 ──
    score = context.get("sector_flows", {}).get(sector_code, 0.5)

    # ── 板块持续性降权 ──
    sector_persistence = context.get("sector_persistence", {})
    if sector_persistence:
        persist = sector_persistence.get(sector_code, 0.7)
        score = score * persist

    # ── 概念板块叠加 ──
    # 个股可能属于多个概念板块，取其资金流排名最高的（最强势的概念）
    s_concept = 0.5
    try:
        concept_names = stock.get("_concept_names", [])
        if concept_names:
            concept_map = _load_concept_flow_latest(date_str)
            if concept_map:
                all_flows = list(concept_map.values())
                if all_flows and max(all_flows) > min(all_flows):
                    best_pct = 0.5
                    for cname in concept_names:
                        if cname in concept_map:
                            p = pct_rank(all_flows, concept_map[cname])
                            if p > best_pct:
                                best_pct = p
                    s_concept = best_pct
    except Exception:
        pass

    # ── 板块日内轨迹（行业级资金加速/减速）──
    sector_trajectory = context.get("sector_intraday", {})
    intra_bonus = 0.0
    if sector_trajectory and sector_code in sector_trajectory:
        traj = sector_trajectory[sector_code]
        flow_trend = traj.get("flow_trend", 0)       # +1=加速流入
        snaps = traj.get("snapshots", 1)
        confidence = min(1.0, snaps / 7.0)
        intra_bonus = flow_trend * 0.05 * confidence  # ±0.05

    base = score * 0.70 + s_concept * 0.30
    return round(max(0.0, min(1.0, base + intra_bonus)), 4)
