"""评分引擎 — 编排所有评分维度 + P因子调整"""
from datetime import datetime
from sector_screener.config import to_float, WEIGHTS_BASE
from sector_screener.scorers import (
    score_start_signal, score_capital, score_trend, score_sector,
    score_position, score_analyst, score_multiday, score_technical,
    score_dragon_tiger, score_north_flow, score_ratio_rank,
    score_intra_sector, score_margin_net, score_flow_accel,
    score_block_trade, score_org_research, score_earnings, score_lockup,
    score_margin_short, score_margin_long, score_volume_quality,
    score_intraday_trend, score_price_momentum, score_sector_price,
    apply_p_factors, get_tracker,
)


def build_context(candidates, price_history, sector_freshness, sector_persistence,
                  sector_flows, stock_multiday, analyst_data, dt_data,
                  north_data, ratio_rank, intra_sector_rank, margin_net_map,
                  regime, sentiment_bonus, date_str, afternoon,
                  block_trade=None, org_research=None,
                  earnings_forecast=None, lockup_expiry=None,
                  sector_intraday=None, sector_momentum=None, rotation_signals=None):
    """构建评分上下文 — 预计算所有候选池的 percentile 数组"""
    # 因子 #24: 从个股价格聚合板块价格回报
    from sector_screener.scorers.sector_price import _build_sector_price_returns
    sector_price_returns = _build_sector_price_returns(candidates, price_history)
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
        "_cum3_vals": _build_cum3_vals(candidates, stock_multiday),
        "_cum5_vals": _build_cum_vals(candidates, stock_multiday, "f62_5d"),
        "_cum10_vals": _build_cum_vals(candidates, stock_multiday, "f62_10d"),
        # 新数据源
        "block_trade": block_trade or {},
        "org_research": org_research or {},
        "earnings_forecast": earnings_forecast or {},
        "lockup_expiry": lockup_expiry or {},
        "sector_intraday": sector_intraday or {},
        "sector_momentum": sector_momentum or {},
        "_rotation_signals": rotation_signals or {},
        # 因子 #23: 多日价格回报
        "_ret5d_vals": _build_price_ret_vals(candidates, price_history, 4),
        "_ret10d_vals": _build_price_ret_vals(candidates, price_history, 9),
        "_ret20d_vals": _build_price_ret_vals(candidates, price_history, 19),
        # 因子 #24: 行业板块价格
        "sector_price_returns": sector_price_returns or {},
        "_sector_ret5d_vals": [s.get("ret_5d", 0) for s in (sector_price_returns or {}).values()],
        "_sector_ret10d_vals": [s.get("ret_10d", 0) for s in (sector_price_returns or {}).values()],
    }
    return ctx


def _build_price_ret_vals(candidates, price_history, offset):
    """计算候选池中每只股票的 N 日价格回报，返回列表。"""
    vals = []
    for s in candidates:
        code = s.get("f12", "")
        closes = price_history.get(code, [])
        if len(closes) > offset and closes[offset] and closes[offset] > 0:
            vals.append((closes[0] - closes[offset]) / closes[offset])
        else:
            vals.append(0.0)
    return vals


def _build_cum_vals(candidates, stock_multiday, key):
    return [to_float(s.get("f62")) + stock_multiday.get(s.get("f12", ""), {}).get(key, 0)
            for s in candidates]


def _build_cum3_vals(candidates, stock_multiday):
    """3日累计 = 今日 f62 + 最近2个交易日的 f62 之和"""
    vals = []
    for s in candidates:
        code = s.get("f12", "")
        md = stock_multiday.get(code, {})
        daily = md.get("daily_f62", [])
        cum3 = to_float(s.get("f62")) + sum(daily[:2])
        vals.append(cum3)
    return vals


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
        s_block    = score_block_trade(s, context)
        s_org      = score_org_research(s, context)
        s_earn     = score_earnings(s, context)
        s_lockup   = score_lockup(s, context)
        s_mshort   = score_margin_short(s, context)
        s_mlong    = score_margin_long(s, context)
        s_vq       = score_volume_quality(s, context)
        s_intraday = score_intraday_trend(s, context)
        s_pm       = score_price_momentum(s, context)
        s_sp       = score_sector_price(s, context)

        # 加权求和 (24维)
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
            + s_block    * weights.get("block_trade", 0)
            + s_org      * weights.get("org_research", 0)
            + s_earn     * weights.get("earnings", 0)
            + s_lockup   * weights.get("lockup_expiry", 0)
            + s_mshort   * weights.get("margin_short", 0)
            + s_mlong    * weights.get("margin_long", 0)
            + s_vq       * weights.get("volume_quality", 0)
            + s_intraday * weights.get("intraday_trend", 0)
            + s_pm       * weights.get("price_momentum", 0)
            + s_sp       * weights.get("sector_price", 0)
        )

        # P因子调整
        total_before_p = total
        total = apply_p_factors(s, s_start, s_capital, s_trend, total, context)
        p_adjustment = round(total - total_before_p, 4)

        # 因子贡献度 (weight × sub_score)
        contributions = {
            "start_signal":  round(s_start    * weights.get("start_signal", 0), 4),
            "capital":       round(s_capital  * weights.get("capital", 0), 4),
            "trend":         round(s_trend    * weights.get("trend", 0), 4),
            "sector":        round(s_sector   * weights.get("sector", 0), 4),
            "position":      round(s_position * weights.get("position", 0), 4),
            "analyst":       round(s_analyst  * weights.get("analyst", 0), 4),
            "multiday":      round(s_multiday * weights.get("multiday", 0), 4),
            "technical":     round(s_tech     * weights.get("technical", 0), 4),
            "dragon_tiger":  round(s_dt       * weights.get("dragon_tiger", 0), 4),
            "north_flow":    round(s_north    * weights.get("north_flow", 0), 4),
            "ratio_rank":    round(s_rr       * weights.get("ratio_rank", 0), 4),
            "intra_sector":  round(s_intra    * weights.get("intra_sector", 0), 4),
            "margin_net":    round(s_margin   * weights.get("margin_net", 0), 4),
            "flow_accel":    round(s_accel    * weights.get("flow_accel", 0), 4),
            "block_trade":   round(s_block    * weights.get("block_trade", 0), 4),
            "org_research":  round(s_org      * weights.get("org_research", 0), 4),
            "earnings":      round(s_earn     * weights.get("earnings", 0), 4),
            "lockup_expiry": round(s_lockup   * weights.get("lockup_expiry", 0), 4),
            "margin_short":  round(s_mshort   * weights.get("margin_short", 0), 4),
            "margin_long":   round(s_mlong    * weights.get("margin_long", 0), 4),
            "volume_quality":round(s_vq       * weights.get("volume_quality", 0), 4),
            "intraday_trend":round(s_intraday * weights.get("intraday_trend", 0), 4),
        }

        # 信号触发明细
        signals = _detect_signals(s, s_start, s_capital, s_trend, s_analyst,
                                  s_intra, s_margin, s_dt, s_north, context)

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
            "_s_intraday_trend": round(s_intraday, 3),
            "_f62_5d": md.get("f62_5d", 0),
            "_f62_10d": md.get("f62_10d", 0),
            "_cum3": round(cum3 / 1e8, 2),
            "_cum5": round(cum5 / 1e8, 2),
            "_cum10": round(cum10 / 1e8, 2),
            "_mcap_yi": round(to_float(s.get("f20")) / 1e8, 1),
            "_analyst_num": a.get("org_num", 0),
            "_s_consensus": round(a.get("consensus", 0.5), 3),
            "_s_eps_growth": round(a.get("eps_growth", 0), 3),
            "_contributions": contributions,     # 因子贡献度（回溯优化关键数据）
            "_signals": signals,                 # 信号触发明细
            "_p_adjustment": p_adjustment,       # P因子调整量
        })

    scored.sort(key=lambda x: x["_score"], reverse=True)

    # P10+P14: 记录排名到日内追踪器（日期+代码 维度）
    now = datetime.now()
    tracker = get_tracker()
    date_str = context.get("_date_str", now.strftime("%Y%m%d"))
    for i, s in enumerate(scored):
        s["_rank"] = i + 1
        code = s.get("f12", "")
        if code:
            tracker_key = f"{date_str}:{code}"
            if tracker_key not in tracker:
                tracker[tracker_key] = {"ranks": [], "times": [], "first_hour": now.hour}
            tracker[tracker_key]["ranks"].append(i + 1)
            tracker[tracker_key]["times"].append(now.strftime("%H:%M"))

    return scored


def _detect_signals(s, s_start, s_capital, s_trend, s_analyst,
                    s_intra, s_margin, s_dt, s_north, context):
    """检测触发的量化信号，返回结构化列表供回溯优化"""
    signals = []
    f3 = to_float(s.get("f3"))
    f62 = to_float(s.get("f62"))
    f184 = to_float(s.get("f184"))
    f66 = to_float(s.get("f66"))
    f10 = to_float(s.get("f10"))
    f8 = to_float(s.get("f8"))
    f87 = to_float(s.get("f87"))
    code = s.get("f12", "")
    closes = context.get("price_history", {}).get(code, [])
    breakout = s.get("_breakout_20d", False)
    ma_align = s.get("_ma_align", 0)

    # 启动信号
    if s_start > 0.7:
        signals.append({"factor": "start_signal", "strength": "strong", "value": s_start,
                        "desc": f"启动信号强({s_start:.0%})"})
    elif s_start > 0.5:
        signals.append({"factor": "start_signal", "strength": "moderate", "value": s_start,
                        "desc": f"启动信号中等({s_start:.0%})"})

    # 主力资金
    if s_capital > 0.75:
        signals.append({"factor": "capital", "strength": "strong", "value": s_capital,
                        "desc": f"主力大幅介入(超大单{f66/1e8:.1f}亿)"})
    elif s_capital > 0.6:
        signals.append({"factor": "capital", "strength": "moderate", "value": s_capital,
                        "desc": f"主力持续流入({f62/1e4:.0f}万)"})

    # 趋势确认
    if s_trend > 0.7:
        signals.append({"factor": "trend", "strength": "strong", "value": s_trend,
                        "desc": f"放量趋势确立(量比{f10:.1f})"})

    # 分析师
    if s_analyst > 0.7:
        a_num = context.get("analyst_data", {}).get(code, {}).get("org_num", 0)
        signals.append({"factor": "analyst", "strength": "strong", "value": s_analyst,
                        "desc": f"分析师强共识({a_num}家)"})

    # 行业内强度
    if s_intra > 0.7:
        signals.append({"factor": "intra_sector", "strength": "strong", "value": s_intra,
                        "desc": f"行业内排名前{round((1-s_intra)*100)}%"})

    # 融资
    if s_margin > 0.7:
        signals.append({"factor": "margin_net", "strength": "strong", "value": s_margin,
                        "desc": "融资净买入排名靠前"})

    # 日内轨迹动量
    rank_improve = s.get("_intraday_rank_first", 0) - s.get("_intraday_rank_last", 0)
    if rank_improve > 15:
        signals.append({"factor": "intraday_trend", "strength": "strong", "value": rank_improve,
                        "desc": f"日内排名飙升{rank_improve}位(板块内资金集中)"})
    elif rank_improve > 8:
        signals.append({"factor": "intraday_trend", "strength": "moderate", "value": rank_improve,
                        "desc": f"日内排名上升{rank_improve}位"})

    # 龙虎榜
    if s_dt > 0.6:
        signals.append({"factor": "dragon_tiger", "strength": "strong", "value": s_dt,
                        "desc": "龙虎榜机构买入"})

    # 技术面
    if breakout:
        signals.append({"factor": "technical", "strength": "strong", "value": 1.0,
                        "desc": "突破20日高点"})
    elif ma_align >= 0.5:
        signals.append({"factor": "technical", "strength": "moderate", "value": ma_align,
                        "desc": "均线多头排列"})

    # 主力占比
    if f184 > 8:
        signals.append({"factor": "main_ratio", "strength": "strong", "value": f184,
                        "desc": f"主力占比{f184:.1f}%，资金高度集中"})

    # 小单情绪（反向指标）
    if f87 < 5 and f62 > 0:
        signals.append({"factor": "retail_sentiment", "strength": "positive", "value": f87,
                        "desc": f"小单占比{f87:.1f}%，主力完全控盘"})

    return signals

