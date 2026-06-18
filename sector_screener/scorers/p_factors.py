"""P0-P27 调整因子 — 对综合分进行微调 (来自 sector_enhanced_picks 回溯优化)"""
import json
import os
import statistics
from datetime import datetime, timedelta
from data_collector.fetchers.base import DATA_ROOT
from sector_screener.config import to_float


# 日内多次选股追踪
_stability_tracker = {}


def apply_p_factors(stock, score_start, score_capital, score_trend, total, context):
    """对 total 进行 P0-P27 微调，返回调整后的 total"""
    code = stock.get("f12", "")
    f3 = to_float(stock.get("f3"))
    f62 = to_float(stock.get("f62"))
    f184 = to_float(stock.get("f184"))
    f15 = to_float(stock.get("f15"))
    f16 = to_float(stock.get("f16"))
    f17 = to_float(stock.get("f17"))
    f18 = to_float(stock.get("f18"))
    f87 = to_float(stock.get("f87"))
    f20 = to_float(stock.get("f20"))
    f8 = to_float(stock.get("f8"))
    f10 = to_float(stock.get("f10"))
    price = to_float(stock.get("f2"))
    price_history = context.get("price_history", {})
    closes = price_history.get(code, [])
    md = context.get("stock_multiday", {}).get(code, {})
    date_str = context.get("_date_str", "")
    afternoon = context.get("_afternoon", False)
    sentiment_bonus = context.get("_sentiment_bonus", 0.0)
    candidates = context.get("_candidates", [])

    # P0: 高资金低启动 = 一日游脉冲
    if score_capital > 0.75 and score_start < 0.45:
        total -= 0.12

    # P1: 买卖比
    f164 = to_float(stock.get("f164"))
    f166 = to_float(stock.get("f166"))
    if f166 > 0 and f164 > 0:
        ratio = f164 / f166
        if ratio > 2.0:
            total += 0.05
        elif ratio < 1.2:
            total -= 0.08

    # P3: 资金连续性
    pos_days = md.get("pos_days_3d", 0)
    if pos_days < 2:
        total -= 0.06

    # P5: 残差动量
    if closes and len(closes) >= 5:
        stock_chg_5d = (closes[0] - closes[4]) / closes[4] * 100 if closes[4] > 0 else 0
        market_chg_5d = _market_median(candidates)
        residual = stock_chg_5d - market_chg_5d
        if 2.0 < residual < 8.0:
            total += 0.05
        elif residual < -3.0:
            total -= 0.08

    # P6: 散户情绪反向
    if f87 > 30 and f3 < 3:
        total -= 0.08
    elif f87 < 10 and f3 > 2:
        total += 0.04

    # P7: 开盘缺口
    if f18 > 0 and f17 > 0:
        gap = (f17 - f18) / f18 * 100
        intraday = (price - f17) / f17 * 100 if f17 > 0 else 0
        if gap < -1 and intraday > 2 and f3 > 1:
            total += 0.05
        elif gap > 3 and intraday < 0:
            total -= 0.08

    # P8: 振幅洗盘
    if f18 > 0 and f15 > 0 and f16 > 0:
        amplitude = (f15 - f16) / f18 * 100
        if 5 < amplitude < 12 and f3 > 1 and f62 > 0:
            total += 0.04

    # P9: 市值分组
    mcap_yi = f20 / 1e8
    if mcap_yi > 500:
        total += 0.02
    elif mcap_yi < 100:
        total -= 0.02

    # P10+P14+P15: 盘中追踪
    tracker = _stability_tracker.get(code, {})
    prev_ranks = tracker.get("ranks", [])
    appearances = len(prev_ranks)
    if prev_ranks:
        total -= 0.10
    if appearances >= 5:
        all_ranks = prev_ranks + [stock.get("_rank", 99)]
        try:
            std = statistics.stdev(all_ranks)
            if std < 2.0:
                total += 0.08
            elif std < 3.0:
                total += 0.04
        except statistics.StatisticsError:
            pass
    elif appearances >= 3:
        all_ranks = prev_ranks + [stock.get("_rank", 99)]
        try:
            if statistics.stdev(all_ranks) < 2.5:
                total += 0.06
        except statistics.StatisticsError:
            pass
    elif appearances == 0 and tracker.get("first_hour", 9) > 14:
        total -= 0.04
    if appearances >= 5:
        recent = prev_ranks[-5:]
        rank_trend = (recent[0] - recent[-1]) / 5
        if rank_trend > 0.5:
            total += 0.04
        elif rank_trend < -0.5:
            total -= 0.06

    # P11: 高启动低资金
    if score_start > 0.80 and score_capital < 0.70:
        total -= 0.08

    # P12: 中涨跌弱资金
    if f3 > 5.0 and score_capital < 0.70:
        total -= 0.06

    # P13: 高涨幅透支渐变惩罚 (回溯优化: 3%起扣, 增速更快)
    chg_floor = 3.0 if afternoon else 4.0
    if f3 > chg_floor and score_capital < 0.92:
        overextension = (f3 - chg_floor) / (10.0 - chg_floor)
        penalty = overextension * 0.30  # 回溯: 0.25→0.30 惩罚力度加大
        total -= min(0.30, penalty)
        if f3 > 7.0 and f184 > 15.0:
            total -= 0.06  # 0.05→0.06

    # P13b: 均值回归检测 (回溯: 昨日涨>3%且无分析师覆盖=次日回调高发)
    a_num = context.get("analyst_data", {}).get(code, {}).get("org_num", 0)
    if f3 > 3.0 and a_num < 3:
        total -= 0.04  # 高涨幅无基本面背书 = 资金驱动, 次日回归概率高
    elif f3 > 3.0 and a_num >= 5:
        total += 0.02  # 有分析师覆盖的高涨幅 = 基本面驱动, 持续性更强

    # P16: 涨停基因
    limit_up_gene = _check_limit_up_gene(code, date_str)
    if limit_up_gene >= 0.5:
        total += 0.05
    elif limit_up_gene >= 0.2:
        total += 0.02

    # P17: 情绪周期
    total += sentiment_bonus

    # P18-P20: 三周期共振
    total_stocks = max(len(candidates), 1)
    rank_5d = to_float(stock.get("_rank_5d"))
    rank_10d = to_float(stock.get("_rank_10d"))
    pct_5d = rank_5d / total_stocks if rank_5d > 0 else 0.5
    pct_10d = rank_10d / total_stocks if rank_10d > 0 else 0.5
    pct_today = stock.get("_rank", 99) / total_stocks
    if pct_today < 0.30 and pct_5d < 0.30 and pct_10d < 0.30:
        total += 0.06
    elif pct_today < 0.30 and pct_5d < 0.30:
        total += 0.08
    elif pct_today < 0.30 and pct_5d > 0.50 and pct_10d > 0.50:
        total -= 0.10
    elif pct_5d < 0.30:
        total += 0.03

    # P21: BIAS乖离率
    if closes and len(closes) >= 5:
        ma5 = sum(closes[:5]) / 5
        bias = (price - ma5) / ma5 * 100
        if -3 < bias < 0 and f3 > 0:
            total += 0.04
        elif bias < -8:
            total += 0.06 if f62 > 0 else 0.02

    # P22: 残差动量增强
    if closes and len(closes) >= 10:
        stock_chg_10d = (closes[0] - closes[9]) / closes[9] * 100 if closes[9] > 0 else 0
        if 3 < stock_chg_10d < 15:
            total += 0.04

    # P23: 小单极端情绪
    if f87 > 40 and f3 < 2:
        total -= 0.06
    elif f87 < 5 and f62 > 0 and f3 > 0:
        total += 0.03

    # P24: 环境自适应
    if sentiment_bonus > 0.03:
        total += 0.02
    elif sentiment_bonus < -0.03:
        total -= 0.03

    # P25: 隔夜-日内分解
    if f18 > 0 and f17 > 0:
        overnight = (f17 - f18) / f18 * 100
        intraday = (price - f17) / f17 * 100
        if overnight > 1 and intraday > -1:
            total += 0.05
        elif overnight < -2 and intraday > 2:
            total += 0.04
        elif overnight > 3 and intraday < -1:
            total -= 0.06

    # P26: VWAP错杀检测
    if f15 > 0 and f16 > 0:
        vwap_approx = (f15 + f16 + price) / 3
        if price < vwap_approx * 0.98 and f62 > 0:
            total += 0.04
        elif price > vwap_approx * 1.03 and f3 > 5:
            total -= 0.04

    # P27: 换手率质量
    if 5 <= f8 <= 15 and f10 > 1.5:
        total += 0.03

    # ── 特殊调整 ──
    if f3 < 3.0 and score_capital > 0.6:
        total += 0.05  # 沉默吸筹
    if score_capital > 0.80 and score_start > 0.70:
        total -= 0.08  # 资金 vs 启动对立

    return total


def _market_median(candidates):
    """候选池中位数涨跌"""
    if not candidates:
        return 0.0
    chgs = [to_float(s.get("f3")) for s in candidates[:20]]
    if chgs:
        chgs.sort()
        return chgs[len(chgs) // 2]
    return 0.0


def _check_limit_up_gene(code, date_str):
    """近30日涨停基因"""
    d = datetime.strptime(date_str, "%Y%m%d")
    had_limit_up = False
    limit_up_day = None
    cursor = d - timedelta(days=1)
    days_back = 0
    while days_back < 30:
        prev_str = cursor.strftime("%Y%m%d")
        path = os.path.join(DATA_ROOT, prev_str, "fund_flow.json")
        cursor -= timedelta(days=1)
        days_back += 1
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)
        for r in rows:
            if r.get("f12") != code:
                continue
            if to_float(r.get("f3")) >= 9.8:
                had_limit_up = True
                limit_up_day = prev_str
                break
        if had_limit_up:
            break
    if not had_limit_up:
        return 0.0
    if not limit_up_day:
        return 0.3

    limit_d = datetime.strptime(limit_up_day, "%Y%m%d")
    check_d = limit_d + timedelta(days=1)
    volume_after = []
    for _ in range(5):
        ds = check_d.strftime("%Y%m%d")
        path = os.path.join(DATA_ROOT, ds, "fund_flow.json")
        check_d += timedelta(days=1)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)
        for r in rows:
            if r.get("f12") == code:
                vol = to_float(r.get("f8"))
                chg = to_float(r.get("f3"))
                if vol > 0:
                    volume_after.append((vol, chg))
                break
    if volume_after:
        avg_vol = sum(v for v, _ in volume_after) / len(volume_after)
        max_drop = min(chg for _, chg in volume_after) if volume_after else 0
        if avg_vol < 10 and max_drop > -5:
            return 0.5
    return 0.2


def get_tracker():
    """获取日内追踪器 (供 engine 写入排名历史)"""
    return _stability_tracker
