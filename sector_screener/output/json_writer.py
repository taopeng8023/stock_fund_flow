"""JSON 结果输出"""
import json
import os
from datetime import datetime
from data_collector.fetchers.base import DATA_ROOT
from sector_screener.config import to_float


def _top3_contributors(contributions):
    """取贡献度最高的前3个因子"""
    if not contributions:
        return []
    sorted_items = sorted(contributions.items(), key=lambda x: x[1], reverse=True)
    return [{"factor": k, "contribution": v} for k, v in sorted_items[:3]]


def _gen_reasons(s):
    """生成选中理由"""
    reasons = []
    if s.get("_score_start", 0) >= 0.70:
        reasons.append(f"板块刚启动信号强({s.get('_sector_name','')})")
    elif s.get("_score_start", 0) >= 0.50:
        reasons.append(f"资金温和放量({s.get('_sector_name','')})")
    if s.get("_score_capital", 0) >= 0.75:
        reasons.append(f"主力大幅介入(超大单{to_float(s.get('f66'))/1e8:.1f}亿)")
    elif s.get("_score_capital", 0) >= 0.55:
        reasons.append(f"主力持续流入({to_float(s.get('f62'))/1e4:.0f}万)")
    if s.get("_score_analyst", 0) >= 0.7:
        reasons.append(f"分析师强共识({s.get('_analyst_num', 0)}家覆盖)")
    if s.get("_s_dragon_tiger", 0) > 0.7:
        reasons.append("龙虎榜机构买入")
    if s.get("_breakout_20d", False):
        reasons.append("突破20日高点")
    while len(reasons) < 3:
        reasons.append("综合评分入选")
    return reasons[:3]


def save_json(scored, limit_up, date_str, top_n=10, weights=None, regime="range"):
    """保存 JSON 到 data/<date>/sector_enhanced_picks.json"""
    date_dir = os.path.join(DATA_ROOT, date_str)
    picks_dir = os.path.join(date_dir, "picks")
    os.makedirs(picks_dir, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S")

    output = {
        "date": date_str,
        "strategy": "板块增强选股（主板·板块共振·全数据源·14维度·模块化）",
        "regime": regime,
        "weights": weights or {},
        "candidates": [],
        "limit_up_observe": [],
    }

    for s in scored[:top_n]:
        output["candidates"].append({
            "rank": s.get("_rank", 0),
            "code": s.get("f12", ""), "name": s.get("f14", ""),
            "score": s.get("_score", 0),
            "chg_pct": round(to_float(s.get("f3")), 2),
            "main_flow": to_float(s.get("f62")),
            "main_ratio": round(to_float(s.get("f184")), 1),
            "price": round(to_float(s.get("f2")), 2),
            "turnover": round(to_float(s.get("f8")), 1),
            "mcap_yi": round(to_float(s.get("f20")) / 1e8, 2),
            "sub_scores": {
                "start": s.get("_score_start", 0),
                "capital": s.get("_score_capital", 0),
                "trend": s.get("_score_trend", 0),
                "sector": s.get("_score_sector", 0),
                "position": s.get("_score_position", 0),
                "analyst": s.get("_score_analyst", 0),
                "multiday": s.get("_score_multiday", 0),
                "technical": s.get("_score_technical", 0),
                "dragon_tiger": s.get("_s_dragon_tiger", 0),
                "north_flow": s.get("_s_north_flow", 0),
                "ratio_rank": s.get("_s_ratio_rank", 0),
                "intra_sector": s.get("_s_intra_sector", 0),
                "margin_net": s.get("_s_margin_net", 0),
                "flow_accel": s.get("_s_flow_accel", 0),
                "intraday_trend": s.get("_s_intraday_trend", 0),
                "price_momentum": s.get("_s_price_momentum", 0),
                "sector_price": s.get("_s_sector_price", 0),
                "limitup_proximity": s.get("_s_limitup_proximity", 0),
                "sector_diversity": s.get("_s_sector_diversity", 0),
            },
            "intraday": {
                "snapshots": s.get("_intraday_snapshots", 0),
                "rank_first": s.get("_intraday_rank_first", 0),
                "rank_last": s.get("_intraday_rank_last", 0),
                "rank_trend": s.get("_intraday_rank_trend", 0),
                "flow_trend": s.get("_intraday_flow_trend", 0),
                "flow_delta_pct": s.get("_intraday_flow_delta_pct", 0),
            },
            "sector_name": s.get("_sector_name", ""),
            "concept_names": s.get("_concept_names", []),
            "analyst_num": s.get("_analyst_num", 0),
            "breakout_20d": s.get("_breakout_20d", False),
            "reasons": _gen_reasons(s),
            "factor_contributions": s.get("_contributions", {}),   # 🆕 回溯优化
            "signals": s.get("_signals", []),                      # 🆕 信号触发
            "p_adjustment": s.get("_p_adjustment", 0),             # 🆕 P因子调整
            "top_contributors": _top3_contributors(s.get("_contributions", {})),
        })

    for s in limit_up:
        output["limit_up_observe"].append({
            "code": s.get("f12", ""), "name": s.get("f14", ""),
            "chg_pct": round(to_float(s.get("f3")), 2),
            "main_flow": to_float(s.get("f62")),
            "main_ratio": round(to_float(s.get("f184")), 1),
            "turnover": round(to_float(s.get("f8")), 1),
            "sector_name": s.get("_sector_name", ""),
        })

    output["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    json_path = os.path.join(date_dir, "sector_enhanced_picks.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    picks_json = os.path.join(picks_dir, f"enhanced_picks_{ts}.json")
    with open(picks_json, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  ✓ JSON: sector_enhanced_picks.json")
    print(f"  ✓ JSON: {os.path.basename(picks_json)}")
    return output
