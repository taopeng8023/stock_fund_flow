"""控制台输出 — 选股结果表格 + 逐只诊断"""
from collections import defaultdict
from sector_screener.config import fmt_yi, to_float


def print_results(limit_up_pool, scored_candidates, excluded_pool,
                  top_n=10, regime="range", weights=None):
    """格式化输出精选结果"""
    from datetime import datetime
    from data_collector.fetchers.base import BJS_TZ
    date_str = datetime.now(BJS_TZ).strftime("%Y-%m-%d")
    regime_labels = {"bull": "🐂 牛市", "bear": "🐻 熊市", "range": "📊 震荡市"}
    regime_label = regime_labels.get(regime, regime)

    print(f"\n{'═' * 100}")
    print(f"  🔥 板块增强选股（主板 · 板块共振 · 全数据源 · 14维度）")
    print(f"  {date_str} | 市场: {regime_label} | P0-P27 因子调整")
    print(f"{'═' * 100}")
    header = (f"  {'排名':<4} {'代码':<8} {'名称':<8} {'得分':<6} {'涨跌%':<7} "
              f"{'主力流入':<12} {'占比%':<6} {'启动':<6} {'资金':<6} "
              f"{'分析师':<6} {'行业内':<6} {'融资':<6} {'日内':<6}")
    print(header)
    print(f"  {'─' * 100}")

    for i, s in enumerate(scored_candidates[:top_n], 1):
        code = s.get("f12", "")
        name = s.get("f14", "")
        score = s.get("_score", 0)
        chg = to_float(s.get("f3"))
        f62 = to_float(s.get("f62"))
        f184 = to_float(s.get("f184"))
        start_s = s.get("_score_start", 0)
        capital_s = s.get("_score_capital", 0)
        analyst_s = s.get("_score_analyst", 0)
        intra_s = s.get("_s_intra_sector", 0)
        margin_s = s.get("_s_margin_net", 0)
        intraday_s = s.get("_s_intraday_trend", 0)

        print(f"  {i:<4} {code:<8} {name:<8s} {score:.4f} "
              f"{chg:>+6.2f}% {fmt_yi(f62):>12} {f184:>5.1f}% "
              f"{start_s:.2f}  {capital_s:.2f}  "
              f"{analyst_s:.2f}  {intra_s:.2f}  {margin_s:.2f}  {intraday_s:.2f}")

    if not scored_candidates:
        print(f"    无符合条件的候选股")

    # 涨停观察池
    if limit_up_pool:
        print(f"\n  {'─' * 100}")
        print(f"  👀 涨停观察池（持续跟踪，等回调/开板机会）")
        print(f"  {'代码':<8} {'名称':<8} {'涨跌%':<7} {'主力流入':<12} {'主力占比%':<7} {'换手%':<7} {'封板力度':<8} {'行业'}")
        limit_up_sorted = sorted(limit_up_pool, key=lambda s: to_float(s.get("f62")), reverse=True)
        for s in limit_up_sorted[:15]:
            code = s.get("f12", "")
            name = s.get("f14", "")
            chg = to_float(s.get("f3"))
            f62 = to_float(s.get("f62"))
            f184 = to_float(s.get("f184"))
            f8 = to_float(s.get("f8"))
            sector = s.get("_sector_name", "")
            seal = "强🔒" if f184 > 8 and f8 < 10 else ("中🔓" if f184 > 4 else "弱⚠")
            print(f"  {code:<8} {name:<8s} {chg:>+6.2f}% {fmt_yi(f62):>12} "
                  f"{f184:>6.1f}% {f8:>6.1f}% {seal:<8} {sector}")

    # 统计
    print(f"\n  {'─' * 100}")
    print(f"  候选池: {len(scored_candidates)} 只 | 涨停观察: {len(limit_up_pool)} 只 | 排除: {len(excluded_pool)} 只")

    if excluded_pool:
        reason_counts = defaultdict(int)
        for s in excluded_pool:
            for r in s.get("_exclude_reason", "").split("; "):
                if r:
                    reason_counts[r.split("(")[0].strip()] += 1
        print(f"  主要排除原因:")
        for reason, cnt in sorted(reason_counts.items(), key=lambda x: -x[1])[:5]:
            print(f"    - {reason}: {cnt}只")

    if weights:
        print(f"\n  📊 因子权重 (14维):")
        for k, v in sorted(weights.items(), key=lambda x: -x[1]):
            bar = "█" * int(v * 100)
            print(f"    {k:<18} {v*100:>4.1f}% {bar}")


def print_diagnosis(top_stocks):
    """逐只诊断 Top N"""
    if not top_stocks:
        return
    print(f"\n  📋 逐只诊断 (Top {len(top_stocks)}):\n")
    for i, s in enumerate(top_stocks):
        code = s.get("f12", "")
        name = s.get("f14", "")
        score = s.get("_score", 0)
        chg = to_float(s.get("f3"))
        mcap = s.get("_mcap_yi", 0)
        sector = s.get("_sector_name", "")
        a_num = s.get("_analyst_num", 0)
        breakout = s.get("_breakout_20d", False)

        signals = []
        risks = []

        if s.get("_score_start", 0) > 0.7:
            signals.append(f"启动信号强({s.get('_score_start', 0):.0%})")
        if s.get("_score_capital", 0) > 0.7:
            signals.append(f"主力资金强度高({s.get('_score_capital', 0):.0%})")
        if s.get("_score_analyst", 0) > 0.6:
            signals.append(f"分析师共识强({s.get('_score_analyst', 0):.0%}, {a_num}家)")
        if s.get("_s_intra_sector", 0) > 0.7:
            signals.append(f"行业内排名前{100-s.get('_s_intra_sector', 0)*100:.0f}%")
        if s.get("_s_margin_net", 0) > 0.7:
            signals.append(f"融资净买入排名靠前")
        if breakout:
            signals.append("突破20日高点")
        if s.get("_ma_align", 0) >= 0.5:
            signals.append(f"均线多头排列({s.get('_ma_align', 0):.0%})")
        if s.get("_cum3", 0) > 0:
            signals.append(f"3日累计净流入 {s.get('_cum3', 0):+.1f}亿")
        rank_imp = s.get("_intraday_rank_first", 0) - s.get("_intraday_rank_last", 0)
        if rank_imp > 10:
            signals.append(f"日内排名飙升{rank_imp}位(资金集中)")
        elif rank_imp > 5:
            signals.append(f"日内排名上升{rank_imp}位")

        if chg > 8:
            risks.append(f"已近涨停({chg:.1f}%)")
        if to_float(s.get("f8")) > 20:
            risks.append(f"换手率偏高({to_float(s.get('f8')):.1f}%)")

        print(f"  {i+1}. {code} {name} | {mcap:.0f}亿 | {sector} | 分析师{a_num}家 | 综合分 {score:.4f}")
        for sig in signals:
            print(f"     ✅ {sig}")
        for risk in risks:
            print(f"     ⚠️  {risk}")
        print()
