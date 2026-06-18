"""
板块增强选股 — 模块化入口
用法:
  python -m sector_screener.main                    默认今天
  python -m sector_screener.main --date=20260617    指定日期
  python -m sector_screener.main --sectors=5 --top=10  参数调整
"""
import sys
import json
from datetime import datetime
from data_collector.fetchers.base import DATA_ROOT, BJS_TZ, load_json

from sector_screener.config import WEIGHTS_BASE, WEIGHTS_BULL, WEIGHTS_BEAR
from sector_screener.loaders import (
    load_sector_top_codes, load_sector_stocks, load_sector_multiday, load_sector_intraday,
    load_fund_flow_cross_ref, load_analyst_data, load_dragon_tiger_data,
    load_north_flow_data, load_ratio_rank, load_stock_multiday, load_past_closes,
)
from sector_screener.loaders.block_trade import load_block_trade
from sector_screener.loaders.org_research import load_org_research
from sector_screener.loaders.earnings_forecast import load_earnings_forecast
from sector_screener.loaders.lockup_expiry import load_lockup_expiry
from sector_screener.filters import split_stocks
from sector_screener.engine import build_context, score_candidates
from sector_screener.output import print_results, print_diagnosis, save_json, save_csv


def detect_market_regime(date_str):
    """检测牛/熊/震荡（3日平滑避免单日噪声），返回 (regime, weights)"""
    try:
        from market_diagnosis import get_diagnosis
        diag = get_diagnosis(date_str)
        if diag:
            regime = diag["regime"]["regime"]
            wmap = {"bull": WEIGHTS_BULL, "bear": WEIGHTS_BEAR, "range": WEIGHTS_BASE}
            return regime, wmap.get(regime, WEIGHTS_BASE)
    except Exception:
        pass

    # 取最近 3 个交易日的数据做平滑
    from datetime import datetime, timedelta
    all_chgs = []
    all_flows = []
    d = datetime.strptime(date_str, "%Y%m%d")
    for offset in range(3):
        ds = (d - timedelta(days=offset)).strftime("%Y%m%d")
        rows = load_json(ds, "fund_flow")
        if rows:
            all_chgs.extend(r.get("f3") for r in rows if isinstance(r.get("f3"), (int, float)))
            all_flows.extend(r.get("f62") for r in rows if isinstance(r.get("f62"), (int, float)))

    if not all_chgs:
        return "range", WEIGHTS_BASE

    up_ratio = sum(1 for c in all_chgs if c > 0) / len(all_chgs)
    chgs_sorted = sorted(all_chgs)
    median_ret = chgs_sorted[len(chgs_sorted) // 2]
    flow_pos = sum(1 for f in all_flows if f > 0) / len(all_flows) if all_flows else 0.5
    bull_score = sum([up_ratio > 0.55, median_ret > 0.3, flow_pos > 0.50])
    bear_score = sum([up_ratio < 0.35, median_ret < -0.5, flow_pos < 0.35])

    if bull_score >= 3:
        regime = "bull"
    elif bear_score >= 3:
        regime = "bear"
    else:
        regime = "range"

    n_days = len(all_chgs) // (len(rows) if rows else 1) if rows else 1
    print(f"  市场环境: {regime} ({n_days}日平滑, 涨跌比{up_ratio:.0%}, 中位{median_ret:+.1f}%, 主力正{flow_pos:.0%})")
    wmap = {"bull": WEIGHTS_BULL, "bear": WEIGHTS_BEAR, "range": WEIGHTS_BASE}
    return regime, wmap.get(regime, WEIGHTS_BASE)


def _calc_market_sentiment(date_str):
    """市场情绪 → (bonus, label)"""
    rows = load_json(date_str, "fund_flow")
    if not rows or len(rows) < 1000:
        return 0.0, "unknown"
    limit_up = sum(1 for r in rows if isinstance(r.get("f3"), (int, float)) and r["f3"] >= 9.8)
    limit_down = sum(1 for r in rows if isinstance(r.get("f3"), (int, float)) and r["f3"] <= -9.8)
    total = len(rows)
    up_ratio = limit_up / total * 100
    down_ratio = limit_down / total * 100
    zha_ban_rate = down_ratio / max(up_ratio, 0.01)

    if up_ratio > 3.0 and zha_ban_rate < 0.3:
        return -0.03, "高潮"
    elif up_ratio > 1.5 and zha_ban_rate < 0.5:
        return 0.04, "发酵"
    elif up_ratio < 0.5 and down_ratio > 2.0:
        return 0.06, "冰点"
    elif up_ratio > 1.0 and zha_ban_rate > 0.6:
        return -0.05, "退潮"
    return 0.0, "震荡"


def run_pipeline(date_str=None, top_sectors=5, top_picks=10):
    """执行全流程选股"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    afternoon = datetime.now().hour >= 13
    if afternoon:
        print(f"  下午模式: 高涨幅惩罚收紧")

    # ── 市场环境 ──
    regime, weights = detect_market_regime(date_str)

    # [1] Top N 行业板块
    print(f"\n[1/5] 获取 Top {top_sectors} 行业板块...")
    sector_codes = load_sector_top_codes(date_str, top_sectors)
    if not sector_codes:
        print("  ✗ 无板块数据, 请先运行 python -m data_collector.main")
        return None

    # [2] 成分股 + 板块数据
    print(f"\n[2/5] 加载成分股 + 板块多日数据...")
    stocks = load_sector_stocks(sector_codes, date_str)
    if not stocks:
        print("  ✗ 无成分股数据")
        return None
    sector_freshness, _, sector_persistence = load_sector_multiday(date_str)
    sector_intraday = load_sector_intraday(date_str)
    sector_flows = {code: 0.5 + (top_sectors - i) * 0.1 for i, code in enumerate(sector_codes)}

    # [3] 全数据源
    print(f"\n[3/5] 加载全数据源...")
    analyst_data = load_analyst_data(date_str)
    dt_data = load_dragon_tiger_data(date_str)
    north_data = load_north_flow_data(date_str)
    ratio_rank = load_ratio_rank(date_str)
    stock_multiday = load_stock_multiday(date_str)
    intra_sector_rank, margin_net_map = load_fund_flow_cross_ref(date_str)

    # 新数据源
    block_trade = load_block_trade(date_str)
    org_research = load_org_research(date_str)
    earnings_data = load_earnings_forecast(date_str)
    lockup_data = load_lockup_expiry(date_str)

    # [4] 历史价格 + 选股分流
    print(f"\n[4/5] 加载历史价格 + 选股分流...")
    codes_set = {s.get("f12", "") for s in stocks}
    price_history = load_past_closes(date_str, codes_set)
    limit_up, candidates, excluded = split_stocks(stocks, price_history)

    # [5] 评分
    print(f"\n[5/5] 多因子增强评分 ({len(candidates)} 候选)...")
    sentiment_bonus, sentiment_label = _calc_market_sentiment(date_str)
    print(f"  市场情绪: {sentiment_label} ({sentiment_bonus:+.2f})")

    context = build_context(
        candidates, price_history, sector_freshness, sector_persistence,
        sector_flows, stock_multiday, analyst_data, dt_data,
        north_data, ratio_rank, intra_sector_rank, margin_net_map,
        regime, sentiment_bonus, date_str, afternoon,
        block_trade=block_trade, org_research=org_research,
        earnings_forecast=earnings_data, lockup_expiry=lockup_data,
        sector_intraday=sector_intraday,
    )
    context["_weights"] = weights

    scored = score_candidates(candidates, context)

    # 输出
    print_results(limit_up, scored, excluded, top_picks, regime, weights)
    print_diagnosis(scored[:5])

    # 保存
    save_json(scored, limit_up, date_str, top_picks, weights, regime)
    save_csv(scored, limit_up, date_str, top_picks)

    return {"limit_up": limit_up, "scored": scored, "excluded": excluded}


def get_enhanced_picks(date_str=None, top_sectors=5, top_picks=10):
    """程序化接口 — 兼容 sector_enhanced_picks.get_enhanced_picks"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    try:
        from market_diagnosis import get_diagnosis
        diag = get_diagnosis(date_str)
        if diag and diag.get("risks", {}).get("level") in ("high", "critical"):
            return {"error": f"市场风险过高，暂停选股", "date": date_str}
    except Exception:
        pass

    # 全流程
    result = run_pipeline(date_str, top_sectors, top_picks)
    if result is None:
        return {"error": "数据不可用", "date": date_str}

    scored = result["scored"]
    limit_up = result["limit_up"]

    # 构建 picks 列表
    picks = []
    for s in scored[:top_picks]:
        picks.append({
            "rank": s.get("_rank", 0),
            "code": s.get("f12", ""), "name": s.get("f14", ""),
            "score": s.get("_score", 0),
            "chg_pct": round(float(s.get("f3", 0) or 0), 2),
            "price": round(float(s.get("f2", 0) or 0), 2),
            "main_flow": s.get("f62"),
            "main_ratio": round(float(s.get("f184", 0) or 0), 1),
            "mcap_yi": s.get("_mcap_yi"),
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
            },
            "sector_name": s.get("_sector_name", ""),
            "analyst_num": s.get("_analyst_num", 0),
            "breakout_20d": s.get("_breakout_20d", False),
        })

    limit_up_list = []
    for s in sorted(limit_up, key=lambda x: float(x.get("f62", 0) or 0), reverse=True):
        limit_up_list.append({
            "code": s.get("f12", ""), "name": s.get("f14", ""),
            "chg_pct": round(float(s.get("f3", 0) or 0), 2),
            "main_flow": s.get("f62"),
            "main_ratio": round(float(s.get("f184", 0) or 0), 1),
            "turnover": round(float(s.get("f8", 0) or 0), 1),
            "sector_name": s.get("_sector_name", ""),
        })

    regime, weights = detect_market_regime(date_str)

    return {
        "date": date_str,
        "regime": regime,
        "weights": {k: round(v, 3) for k, v in weights.items()},
        "candidates_count": len(scored),
        "limit_up_count": len(limit_up),
        "picks": picks,
        "scored": scored,
        "limit_up": limit_up_list,
    }


# 兼容 stock_picker.get_picks 调用方
def get_picks(date_str=None, top_n=5):
    return get_enhanced_picks(date_str=date_str, top_sectors=5, top_picks=top_n)


def main():
    date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")
    top_sectors = 5
    top_picks = 10

    for arg in sys.argv:
        if arg.startswith("--date="):
            date_str = arg.split("=")[1]
        if arg.startswith("--sectors="):
            top_sectors = int(arg.split("=")[1])
        if arg.startswith("--top="):
            top_picks = int(arg.split("=")[1])

    run_pipeline(date_str=date_str, top_sectors=top_sectors, top_picks=top_picks)


if __name__ == "__main__":
    main()
