"""评分引擎 — 编排所有评分维度 + P因子调整"""
from datetime import datetime
from sector_screener.config import to_float, WEIGHTS_BASE
from sector_screener.scorers import (
    score_start_signal, score_capital, score_trend, score_sector,
    score_position, score_analyst, score_multiday, score_technical,
    score_dragon_tiger, score_north_flow, score_ratio_rank,
    score_intra_sector, score_margin_net, score_flow_accel,
    apply_p_factors, get_tracker,
)


def build_context(candidates, price_history, sector_freshness, sector_persistence,
                  sector_flows, stock_multiday, analyst_data, dt_data,
                  north_data, ratio_rank, intra_sector_rank, margin_net_map,
                  regime, sentiment_bonus, date_str, afternoon):
    """构建评分上下文 — 预计算所有候选池的 percentile 数组"""
    ctx = {
        "sector_freshness": sector_freshness,
        "sector_persistence": sector_persistence,
        "sector_flows": sector_flows,
        "stock_multiday": stock_multiday,
        "analyst_data": analyst_data,
        "dt_data": dt_data,
        "north_data": north_data,
        "ratio_rank": ratio_rank,
        "intra_sector_rank": intra_sector_rank,
        "margin_net_map": margin_net_map,
        "price_history": price_history,
        "_regime": regime,
        "_sentiment_bonus": sentiment_bonus,
        "_date_str": date_str,
        "_afternoon": afternoon,
        "_candidates": candidates,
        # 预计算 percentile 数组
        "_f62_vals": [to_float(s.get("f62")) for s in candidates],
        "_f66_vals": [to_float(s.get("f66")) for s in candidates],
        "_f72_vals": [to_float(s.get("f72")) for s in candidates],
        "_f184_vals": [to_float(s.get("f184")) for s in candidates],
        "_f69_vals": [to_float(s.get("f69")) for s in candidates],
        "_cum3_vals": _build_cum_vals(candidates, stock_multiday, "f62_5d"),
        "_cum5_vals": _build_cum_vals(candidates, stock_multiday, "f62_5d"),
        "_cum10_vals": _build_cum_vals(candidates, stock_multiday, "f62_10d"),
    }
    return ctx


def _build_cum_vals(candidates, stock_multiday, key):
    return [to_float(s.get("f62")) + stock_multiday.get(s.get("f12", ""), {}).get(key, 0)
            for s in candidates]


def score_candidates(candidates, context):
    """对所有候选股进行 14 维度 + P因子综合评分
    返回按 _score 降序排列的列表
    """
    weights = context.get("_weights", WEIGHTS_BASE)

    scored = []
    for s in candidates:
        code = s.get("f12", "")

        # 14 维度评分
        s_start    = score_start_signal(s, context)
        s_capital  = score_capital(s, context)
        s_trend    = score_trend(s, context)
        s_sector   = score_sector(s, context)
        s_position = score_position(s, context)
        s_analyst  = score_analyst(s, context)
        s_multiday = score_multiday(s, context)
        s_tech     = score_technical(s, context)
        s_dt       = score_dragon_tiger(s, context)
        s_north    = score_north_flow(s, context)
        s_rr       = score_ratio_rank(s, context)
        s_intra    = score_intra_sector(s, context)
        s_margin   = score_margin_net(s, context)
        s_accel    = score_flow_accel(s, context)

        # 加权求和
        total = (
            s_start    * weights.get("start_signal", 0)
            + s_capital * weights.get("capital", 0)
            + s_trend   * weights.get("trend", 0)
            + s_sector  * weights.get("sector", 0)
            + s_position * weights.get("position", 0)
            + s_analyst * weights.get("analyst", 0)
            + s_multiday * weights.get("multiday", 0)
            + s_tech     * weights.get("technical", 0)
            + s_dt       * weights.get("dragon_tiger", 0)
            + s_north    * weights.get("north_flow", 0)
            + s_rr       * weights.get("ratio_rank", 0)
            + s_intra    * weights.get("intra_sector", 0)
            + s_margin   * weights.get("margin_net", 0)
            + s_accel    * weights.get("flow_accel", 0)
        )

        # P因子调整
        total = apply_p_factors(s, s_start, s_capital, s_trend, total, context)

        # 附加属性
        f62 = to_float(s.get("f62"))
        md = context.get("stock_multiday", {}).get(code, {})
        cum3 = f62 + md.get("f62_5d", 0)
        cum5 = f62 + md.get("f62_5d", 0)
        cum10 = f62 + md.get("f62_10d", 0)
        a = context.get("analyst_data", {}).get(code, {})
        mn = context.get("margin_net_map", {}).get(code, {})

        scored.append({
            **s,
            "_score": round(total, 4),
            "_score_start": round(s_start, 3),
            "_score_capital": round(s_capital, 3),
            "_score_trend": round(s_trend, 3),
            "_score_sector": round(s_sector, 3),
            "_score_position": round(s_position, 3),
            "_score_analyst": round(s_analyst, 3),
            "_score_multiday": round(s_multiday, 3),
            "_score_technical": round(s_tech, 3),
            "_s_dragon_tiger": round(s_dt, 3),
            "_s_north_flow": round(s_north, 3),
            "_s_ratio_rank": round(s_rr, 3),
            "_s_intra_sector": round(s_intra, 3),
            "_s_margin_net": round(s_margin, 3),
            "_s_flow_accel": round(s_accel, 3),
            "_f62_5d": md.get("f62_5d", 0),
            "_f62_10d": md.get("f62_10d", 0),
            "_cum3": round(cum3 / 1e8, 2),
            "_cum5": round(cum5 / 1e8, 2),
            "_cum10": round(cum10 / 1e8, 2),
            "_mcap_yi": round(to_float(s.get("f20")) / 1e8, 1),
            "_analyst_num": a.get("org_num", 0),
            "_s_consensus": round(a.get("consensus", 0.5), 3),
            "_s_eps_growth": round(a.get("eps_growth", 0), 3),
        })

    scored.sort(key=lambda x: x["_score"], reverse=True)

    # P10+P14: 记录排名到日内追踪器
    now = datetime.now()
    tracker = get_tracker()
    for i, s in enumerate(scored):
        s["_rank"] = i + 1
        code = s.get("f12", "")
        if code:
            if code not in tracker:
                tracker[code] = {"ranks": [], "times": [], "first_hour": now.hour}
            tracker[code]["ranks"].append(i + 1)
            tracker[code]["times"].append(now.strftime("%H:%M"))

    return scored
