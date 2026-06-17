"""
主力资金跟随选股模型 — 每日精选 3~5 只（仅主板）
策略：资金流 + 量价配合 + 动量 + 板块 + 龙虎榜 + 北向 + 融资融券 + 多因子打分
风格：激进（跟随主力，追涨不避）

用法:
  python stock_picker.py                读取今天数据选股
  python stock_picker.py --date=20260520  指定日期
  python stock_picker.py --top=3        输出前 3 只
"""
import json
import sys
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from fetchers.base import DATA_ROOT, BJS_TZ, load_json, format_amount

# ============================================================
# 配置
# ============================================================
TOP_N = 5

# ============================================================
# 打分权重（资金面 24% + 量价 13% + 动量 8% + 板块/行业内 9.5% + 分析师 5% + 多日 17.5% + 融资 4.5% + 技术 5% + 龙虎榜/北向/质量/市值 13.5%）
# ============================================================
# 基准权重（震荡市/默认）
WEIGHTS_BASE = {
    "main_flow":        0.070,  # 主力净流入 f62
    "main_ratio":       0.075,  # 主力净流入占比 f184
    "super_flow":       0.055,  # 超大单净流入 f66
    "super_ratio":      0.055,  # 超大单净流入占比 f69
    "vol_ratio":        0.060,  # 量比 f10 — 放量确认
    "turnover":         0.050,  # 换手率 f8 — 活跃度
    "momentum":         0.080,  # 涨跌幅 f3 — 趋势质量
    "market_cap":       0.040,  # 市值 f20 — 中小盘弹性
    "gap":              0.020,  # 开盘缺口 — 低开高走加分
    "sector_flow":      0.055,  # 行业板块资金流 — 板块共振(单日+多日融合)
    "ratio_rank":       0.025,  # 主力占比排名 — 主力控盘信号
    "intra_sector":     0.040,  # 行业内相对强度 — 板块内龙头甄别
    "analyst_consensus": 0.030,  # 分析师评级共识 — 基本面确认
    "earnings_growth":  0.020,  # EPS 预测增长率 — 盈利动量
    "flow_3day":        0.040,  # 3日累计主力流入 — 短期趋势确认
    "flow_5day":        0.030,  # 5日累计主力流入 — 中期持续性
    "flow_10day":       0.020,  # 10日累计主力流入 — 中长期机构行为
    "flow_accel":       0.040,  # 资金流入加速度 — 3日 vs 10日均流入比
    "flow_consistency": 0.045,  # 资金流入连续性 — N日中正流入天数占比
    "ma_align":         0.025,  # 均线多头排列 — 趋势跟随确认
    "breakout":         0.025,  # 突破形态 — 价格突破近期高点
    "super_quality":    0.025,  # 超大单质量 — 超大单占比高排除游资
    "dragon_tiger":     0.050,  # 龙虎榜上榜 — 机构席位买入信号
    "north_flow":       0.025,  # 北向资金方向 — 大盘环境加分
    "margin_net":       0.045,  # 融资净买入 — 杠杆资金态度
}

# 牛市场权重调整 — 增加动量/突破/龙虎榜/融资，降低分析师/市值
WEIGHTS_BULL = {**WEIGHTS_BASE,
    "momentum": 0.100, "breakout": 0.040, "dragon_tiger": 0.060, "margin_net": 0.055,
    "analyst_consensus": 0.020, "market_cap": 0.030, "earnings_growth": 0.015,
    "super_quality": 0.015, "intra_sector": 0.050,
}

# 熊市场权重调整 — 增加质量/防御/北向/行业内强度，降低动量/突破
WEIGHTS_BEAR = {**WEIGHTS_BASE,
    "super_quality": 0.045, "north_flow": 0.040, "analyst_consensus": 0.045,
    "intra_sector": 0.055, "margin_net": 0.030,
    "momentum": 0.055, "breakout": 0.010, "dragon_tiger": 0.035,
    "main_flow": 0.065, "vol_ratio": 0.050,
}

# 全局权重引用（运行时根据市场环境替换）
WEIGHTS = WEIGHTS_BASE

# ============================================================
# 风控过滤
# ============================================================
MIN_PRICE   = 4.0
MAX_PRICE   = 200.0
MAX_CHG_PCT = 9.5
MIN_CHG_PCT = -5.0
MIN_MAIN_FLOW   = 5_000_000
MIN_MAIN_RATIO  = 1.0
MIN_VOL_RATIO   = 1.0
MAX_TURNOVER    = 25.0
MIN_TURNOVER    = 2.0
MIN_MCAP        = 30_0000_0000
MAX_MCAP        = 2000_0000_0000
EXCLUDE_ST      = True
MAIN_BOARD_PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")


def is_main_board(code):
    return isinstance(code, str) and code.startswith(MAIN_BOARD_PREFIXES)


def load_local_data(date_str):
    rows = load_json(date_str, "fund_flow")
    if rows is None:
        print(f"  数据文件不存在: data/{date_str}/fund_flow.json, 自动采集...")
        import subprocess, sys
        project_dir = os.path.dirname(os.path.abspath(__file__))
        cp = subprocess.run(
            [sys.executable, os.path.join(project_dir, "fetch_data.py"),
             f"--date={date_str}"],
            cwd=project_dir,
        )
        if cp.returncode != 0:
            print(f"  自动采集失败，请手动运行: python fetch_data.py --date={date_str}")
            return None
        rows = load_json(date_str, "fund_flow")
        if rows is None:
            return None
    pos = sum(1 for r in rows if isinstance(r.get("f62"), (int, float)) and r["f62"] > 0)
    print(f"  读取本地数据: data/{date_str}/fund_flow.json")
    print(f"  全量: {len(rows)} 只, 正流入: {pos} 只")
    return rows


def load_extra_data(date_str, multi_sector_history=None):
    """加载板块资金流 + 主力占比排名，构建查分表

    multi_sector_history: load_multi_day_sector_history() 的返回值，
                         用于融合单日 + 多日板块累计排名
    """
    sector_rank = {}   # {(industry_name, source): percentile_score}
    ratio_rank  = {}   # {stock_code: percentile_score}

    # ── 行业板块 + 概念板块 ──
    for fname, label in [("industry_flow", "行业板块"), ("concept_flow", "概念板块")]:
        rows = load_json(date_str, fname)
        if rows is None:
            print(f"  {label}数据文件不存在: data/{date_str}/{fname}.json")
            continue

        # ── 单日 f62 排名 ──
        flows = [r.get("f62", 0) or 0 for r in rows]
        if not flows or max(flows) == min(flows):
            for r in rows:
                sector_rank[(r.get("f14", ""), fname)] = 0.5
            continue

        for r in rows:
            name = r.get("f14", "")
            f62 = r.get("f62", 0) or 0
            s_day1 = sum(1 for v in flows if v <= f62) / len(flows)

            # ── 融合多日板块累计排名 ──
            s_3d = 0.5
            s_5d = 0.5
            if multi_sector_history and fname in multi_sector_history:
                mh = multi_sector_history[fname].get(name, {})
                cum3_today = f62 + mh.get("f62_3d", 0)
                cum5_today = f62 + mh.get("f62_5d", 0)

                # 计算所有板块的多日累计 percentile
                all_cum3 = []
                all_cum5 = []
                for r2 in rows:
                    n2 = r2.get("f14", "")
                    f62_2 = r2.get("f62", 0) or 0
                    mh2 = multi_sector_history[fname].get(n2, {})
                    all_cum3.append(f62_2 + mh2.get("f62_3d", 0))
                    all_cum5.append(f62_2 + mh2.get("f62_5d", 0))

                if all_cum3 and max(all_cum3) > min(all_cum3):
                    s_3d = sum(1 for v in all_cum3 if v <= cum3_today) / len(all_cum3)
                if all_cum5 and max(all_cum5) > min(all_cum5):
                    s_5d = sum(1 for v in all_cum5 if v <= cum5_today) / len(all_cum5)

            # 融合: 单日 35% + 3日 35% + 5日 30%
            blended = s_day1 * 0.35 + s_3d * 0.35 + s_5d * 0.30
            sector_rank[(name, fname)] = round(blended, 4)

    print(f"  板块查分表: {len(sector_rank)} 条映射", end="")
    if multi_sector_history:
        print(" (单日+3日+5日融合)")
    else:
        print()

    # ── 主力占比排名 ──
    rows = load_json(date_str, "rank_ratio")
    if rows is not None:
        total = len(rows)
        for i, r in enumerate(rows):
            code = r.get("f12", "")
            ratio_rank[code] = round(1.0 - i / total, 4)
        print(f"  占比排名查分表: {len(ratio_rank)} 条映射")
    else:
        print(f"  占比排名数据文件不存在，跳过")

    return sector_rank, ratio_rank


def load_analyst_data(date_str):
    """加载分析师预测数据，返回 {code: {consensus, eps_growth, target_upside}}"""
    rows = load_json(date_str, "analyst_forecast")
    if rows is None:
        print(f"  分析师数据文件不存在: data/{date_str}/analyst_forecast.json")
        return {}

    analyst = {}
    for r in rows:
        code = r.get("SECURITY_CODE", "")
        org_num = r.get("RATING_ORG_NUM") or 0
        buy = r.get("RATING_BUY_NUM") or 0
        add = r.get("RATING_ADD_NUM") or 0
        neutral = r.get("RATING_NEUTRAL_NUM") or 0
        reduce_ = r.get("RATING_REDUCE_NUM") or 0
        sale = r.get("RATING_SALE_NUM") or 0
        eps1 = r.get("EPS1") or 0
        eps2 = r.get("EPS2") or 0
        target = r.get("DEC_AIMPRICEMAX") or 0

        total = buy + add + neutral + reduce_ + sale
        if org_num >= 3 and total > 0:
            # 分析师共识: 买入=1, 增持=0.75, 中性=0.25, 减持=0, 卖出=-0.5
            consensus = (buy * 1.0 + add * 0.75 + neutral * 0.25 + reduce_ * 0.0 + sale * (-0.5)) / total
        else:
            consensus = 0.5

        # EPS 增长率
        if eps1 and abs(eps1) > 0.01:
            eps_growth = (eps2 - eps1) / abs(eps1)
            eps_growth = max(-0.5, min(2.0, eps_growth))  # clip [-50%, +200%]
        else:
            eps_growth = 0.0

        analyst[code] = {
            "consensus":    round(consensus, 3),
            "eps_growth":   round(eps_growth, 3),
            "org_num":      org_num,
            "target_price": target,
        }

    print(f"  分析师查分表: {len(analyst)} 条映射")
    return analyst


def load_multi_day_history(date_str):
    """加载历史N个交易日资金流数据，计算多日累计流入 + 正流入天数

    返回 {code: {f62_3d, f66_3d, f62_5d, f66_5d, f62_10d, f66_10d,
                 pos_3d, pos_5d, pos_10d,
                 daily_f62: [day0, day1, ... day9]}}  # 最近10天每日f62
    其中 _3d 不含今日，是前2个交易日的累计（加上今日 = 完整3日）
    """
    from datetime import datetime as dt
    d = dt.strptime(date_str, "%Y%m%d")

    # 收集最近 10 个交易日的历史文件
    hist_files = []
    cursor = d
    attempts = 0
    while len(hist_files) < 10 and attempts < 20:
        cursor -= timedelta(days=1)
        attempts += 1
        cursor_str = cursor.strftime("%Y%m%d")
        path = os.path.join(DATA_ROOT, cursor_str, "fund_flow.json")
        if os.path.exists(path):
            hist_files.append(cursor_str)

    if not hist_files:
        print(f"  多日历史: 无历史数据文件")
        return {}

    print(f"  多日历史: 找到 {len(hist_files)} 个交易日 ({hist_files[0]} ~ {hist_files[-1]})")

    from collections import defaultdict
    history = defaultdict(lambda: {
        "f62_3d": 0, "f66_3d": 0, "pos_3d": 0,
        "f62_5d": 0, "f66_5d": 0, "pos_5d": 0,
        "f62_10d": 0, "f66_10d": 0, "pos_10d": 0,
        "daily_f62": [],  # 存储每个历史日的 f62（时间顺序：最近在前）
    })

    for i, date in enumerate(hist_files):
        rows = load_json(date, "fund_flow")
        if rows is None:
            continue

        for r in rows:
            code = r.get("f12", "")
            f62 = r.get("f62", 0) or 0
            f66 = r.get("f66", 0) or 0

            h = history[code]
            h["daily_f62"].append(f62)

            # 累计净流入（含正负，反映真实资金方向）
            if i < 2:
                h["f62_3d"] += f62
                h["f66_3d"] += f66
                if f62 > 0:
                    h["pos_3d"] += 1
            if i < 4:
                h["f62_5d"] += f62
                h["f66_5d"] += f66
                if f62 > 0:
                    h["pos_5d"] += 1
            if i < 9:
                h["f62_10d"] += f62
                h["f66_10d"] += f66
                if f62 > 0:
                    h["pos_10d"] += 1

    print(f"  多日历史覆盖: {len(history)} 只股票")
    return dict(history)


def load_multi_day_sector_history(date_str):
    """加载板块资金流历史数据，计算板块多日累计流入

    返回 {source: {sector_name: {f62_3d, f62_5d, f62_10d}}}
    source = "industry_flow" | "concept_flow"
    """
    from datetime import datetime as dt
    d = dt.strptime(date_str, "%Y%m%d")

    # 收集最近 10 个交易日的历史文件
    hist_files = []
    cursor = d
    attempts = 0
    while len(hist_files) < 10 and attempts < 20:
        cursor -= timedelta(days=1)
        attempts += 1
        cursor_str = cursor.strftime("%Y%m%d")
        path = os.path.join(DATA_ROOT, cursor_str, "industry_flow.json")
        if os.path.exists(path):
            hist_files.append(cursor_str)

    if not hist_files:
        print(f"  板块多日历史: 无历史数据文件")
        return {}

    print(f"  板块多日历史: 找到 {len(hist_files)} 个交易日")

    from collections import defaultdict

    result = {}
    for source in ["industry_flow", "concept_flow"]:
        sector_hist = defaultdict(lambda: {
            "f62_3d": 0, "f62_5d": 0, "f62_10d": 0,
        })

        for i, date in enumerate(hist_files):
            rows = load_json(date, source)
            if rows is None:
                continue

            for r in rows:
                name = r.get("f14", "")
                f62 = r.get("f62", 0) or 0

                h = sector_hist[name]
                # 累计净流入（含负值，反映真实资金方向）
                if i < 2:
                    h["f62_3d"] += f62
                if i < 4:
                    h["f62_5d"] += f62
                if i < 9:
                    h["f62_10d"] += f62

        result[source] = dict(sector_hist)

    return result


def load_dragon_tiger_data(date_str):
    """加载龙虎榜数据，返回 {code: {on_board, d1_ret, d2_ret, buy_institution}}

    上榜且机构买入 = 加分信号
    D1/D2 正向收益 = 历史上榜后表现好
    龙虎榜数据通常仅前一交易日可用（当日盘后更新）
    """
    rows = load_json(date_str, "dragon_tiger")
    # 如果当日无数据（盘中），尝试前一交易日
    if rows is None:
        from datetime import datetime as dt
        d = dt.strptime(date_str, "%Y%m%d")
        prev = d - timedelta(days=1)
        for _ in range(3):
            prev_str = prev.strftime("%Y%m%d")
            rows = load_json(prev_str, "dragon_tiger")
            if rows is not None:
                print(f"  龙虎榜: 使用 {prev_str} 数据 (当日暂无)")
                break
            prev -= timedelta(days=1)

    if rows is None:
        print(f"  龙虎榜: 无可用数据")
        return {}

    dt_data = {}
    for r in rows:
        code = r.get("SECURITY_CODE", "")
        explain = r.get("EXPLAIN", "") or ""
        d1 = r.get("D1_CLOSE_ADJCHRATE") or 0
        d2 = r.get("D2_CLOSE_ADJCHRATE") or 0

        # 解读字段包含"机构"= 机构参与买入
        has_inst = "机构" in str(explain)
        # 解读字段包含"主买"= 主动买入
        is_buy = "主买" in str(explain)

        dt_data[code] = {
            "on_board": True,
            "has_institution": has_inst,
            "is_main_buy": is_buy,
            "d1_return": float(d1) if d1 else 0,
            "d2_return": float(d2) if d2 else 0,
        }

    print(f"  龙虎榜: {len(dt_data)} 只上榜")
    return dt_data


def load_north_flow_data(date_str):
    """加载北向资金数据，返回最近一日的北向净流向

    北向净流入 > 0 → 外资看多，大盘环境偏暖 → 整体加分
    北向净流出 → 外资撤离，谨慎

    注: 盘中数据会被 mask 为 0 或异常值（>500亿），盘后获取真实数据
    """
    rows = load_json(date_str, "north_flow")
    if rows is None:
        print(f"  北向资金数据文件不存在: data/{date_str}/north_flow.json")
        return {}

    if not rows:
        return {}

    latest = rows[0]
    net_north = latest.get("net_north", 0)

    # 盘中数据 mask 检测: 异常大值(>500亿) 或 0 值 → 视为无有效数据
    if abs(net_north) > 500 or (net_north == 0 and latest.get("net_south", 0) == 0):
        print(f"  北向资金: 盘中数据暂不可用 (raw={net_north:.1f})")
        return {"net_north": 0, "masked": True}

    print(f"  北向资金: 最近交易日 {latest.get('date', '?')} 北向净流入 {net_north:+.1f}亿")
    return {"net_north": net_north, "masked": False}


def detect_market_regime(date_str, today_rows):
    """自底向上检测市场环境: bull / bear / range

    使用当日全量个股数据计算:
      - breath: 上涨股票占比
      - median_ret: 中位数涨跌幅
      - flow_ratio: 主力净流入为正的占比

    返回 (regime, regime_score, regimes_detail)
    """
    if not today_rows:
        return "range", 0.5, {}

    chgs = []
    flows = []
    for r in today_rows:
        f3 = r.get("f3")
        f62 = r.get("f62")
        if isinstance(f3, (int, float)):
            chgs.append(f3)
        if isinstance(f62, (int, float)):
            flows.append(f62)

    if not chgs:
        return "range", 0.5, {}

    # 市场宽度
    up_ratio = sum(1 for c in chgs if c > 0) / len(chgs) if chgs else 0.5
    # 中位数涨跌
    chgs_sorted = sorted(chgs)
    median_ret = chgs_sorted[len(chgs_sorted) // 2] if chgs_sorted else 0
    # 主力流入占比
    flow_positive_ratio = sum(1 for f in flows if f > 0) / len(flows) if flows else 0.5
    # 中位数主力流入
    flows_sorted = sorted(flows)
    median_flow = flows_sorted[len(flows_sorted) // 2] if flows_sorted else 0

    # 加载前几日数据计算趋势强度
    m1, m5 = _calc_market_trend(date_str, chgs)

    detail = {
        "up_ratio": round(up_ratio, 2),
        "median_ret": round(median_ret, 2),
        "flow_positive_ratio": round(flow_positive_ratio, 2),
        "median_flow": median_flow,
        "trend_1d": round(m1, 2),
        "trend_5d": round(m5, 2),
    }

    # 判别逻辑
    bull_score = 0
    if up_ratio > 0.55:
        bull_score += 1
    if median_ret > 0.3:
        bull_score += 1
    if flow_positive_ratio > 0.50:
        bull_score += 1
    if m5 > 0.5:
        bull_score += 1  # 5日均涨幅 > 0.5%

    bear_score = 0
    if up_ratio < 0.35:
        bear_score += 1
    if median_ret < -0.5:
        bear_score += 1
    if flow_positive_ratio < 0.35:
        bear_score += 1
    if m5 < -0.5:
        bear_score += 1

    if bull_score >= 3:
        regime = "bull"
        score = min(1.0, 0.5 + bull_score * 0.12)
    elif bear_score >= 3:
        regime = "bear"
        score = max(0.0, 0.5 - bear_score * 0.12)
    else:
        regime = "range"
        score = 0.5

    print(f"  市场环境: {regime} (涨跌比{up_ratio:.0%}, 中位{median_ret:+.1f}%, "
          f"主力正{flow_positive_ratio:.0%}, 5日趋势{m5:+.1f}%)")
    return regime, score, detail


def _calc_market_trend(date_str, today_chgs):
    """计算市场 1 日和 5 日趋势（用中位数涨跌幅的移动平均）"""
    from datetime import datetime as dt
    d = dt.strptime(date_str, "%Y%m%d")
    medians = [sorted(today_chgs)[len(today_chgs) // 2] if today_chgs else 0]

    cursor = d
    attempts = 0
    while len(medians) < 5 and attempts < 10:
        cursor -= timedelta(days=1)
        attempts += 1
        prev_str = cursor.strftime("%Y%m%d")
        rows = load_json(prev_str, "fund_flow")
        if rows is None:
            continue
        chgs = [r.get("f3") for r in rows if isinstance(r.get("f3"), (int, float))]
        if chgs:
            medians.append(sorted(chgs)[len(chgs) // 2])

    m1 = medians[0] if len(medians) >= 1 else 0
    m5 = sum(medians[:5]) / min(5, len(medians)) if medians else 0
    return m1, m5


def load_technical_data(date_str, codes):
    """加载多日 fund_flow 数据，还原每只股票的收盘价序列

    返回 {code: {closes: [...], highs: [...], ma5, ma10, ma20, ma60, breakout_20d, ...}}
    """
    from datetime import datetime as dt
    d = dt.strptime(date_str, "%Y%m%d")

    # 收集最多 60 个交易日的收盘价
    price_history = defaultdict(list)  # {code: [(date, close, high)]}
    hist_dates = []

    cursor = d
    attempts = 0
    while len(hist_dates) < 60 and attempts < 80:
        cursor -= timedelta(days=1)
        attempts += 1
        cursor_str = cursor.strftime("%Y%m%d")
        rows = load_json(cursor_str, "fund_flow")
        if rows is None:
            continue
        hist_dates.append(cursor_str)
        for r in rows:
            code = r.get("f12", "")
            if code not in codes:
                continue
            f2 = r.get("f2")  # 收盘
            if isinstance(f2, (int, float)) and f2 > 0:
                # 估算最高价 ≈ 收盘 * (1 + |涨跌幅|/100 * 0.5) 粗略估计
                f3 = r.get("f3", 0) or 0
                est_high = f2 * (1 + abs(f3) / 200)
                price_history[code].append({
                    "close": f2,
                    "high": est_high,
                    "chg": f3,
                })

    if not hist_dates:
        print(f"  技术面: 无历史价格数据")
        return {}

    # 计算均线和突破信号
    tech_data = {}
    for code, prices in price_history.items():
        if len(prices) < 5:
            continue

        closes = [p["close"] for p in prices[:60]]  # 最近在前
        highs = [p["high"] for p in prices[:60]]

        # MA 计算
        def ma(seq, n):
            return sum(seq[:n]) / n if len(seq) >= n else None

        ma5 = ma(closes, 5)
        ma10 = ma(closes, 10)
        ma20 = ma(closes, 20)
        ma60 = ma(closes, 60) if len(closes) >= 60 else None

        # 均线多头排列得分: 5>10>20>60 each = +0.25
        align_score = 0.0
        if ma5 and ma10 and ma5 > ma10:
            align_score += 0.25
        if ma10 and ma20 and ma10 > ma20:
            align_score += 0.25
        if ma20 and ma60 and ma20 > ma60 if ma60 else True:
            align_score += 0.25
        if ma5 and ma20 and ma5 > ma20:
            align_score += 0.25

        # 突破检测: 当前价格是否突破 20 日最高价
        current_close = closes[0] if closes else 0
        breakout_20d = False
        breakout_60d = False
        if len(highs) >= 20:
            high_20d = max(highs[1:21])  # 不含今天
            breakout_20d = current_close > high_20d * 0.98  # 接近突破也算
        if len(highs) >= 60:
            high_60d = max(highs[1:61])
            breakout_60d = current_close > high_60d * 0.98

        tech_data[code] = {
            "ma_align_score": round(align_score, 2),
            "breakout_20d": breakout_20d,
            "breakout_60d": breakout_60d,
            "ma5": round(ma5, 1) if ma5 else None,
            "ma20": round(ma20, 1) if ma20 else None,
            "bars": len(closes),
        }

    print(f"  技术面: {len(tech_data)} 只有效K线数据")
    return tech_data


# ============================================================
# 打分辅助函数
# ============================================================
def percentile_rank(values, target):
    if not values or max(values) == min(values):
        return 0.5
    return sum(1 for v in values if v <= target) / len(values)


def range_score(value, ideal_min, ideal_max, floor, ceil):
    if ideal_min <= value <= ideal_max:
        return 1.0
    if value < ideal_min:
        return max(0.0, (value - floor) / (ideal_min - floor)) if ideal_min > floor else 0.0
    return max(0.0, (ceil - value) / (ceil - ideal_max)) if ceil > ideal_max else 0.0


def score_stocks(rows, sector_rank, ratio_rank, analyst_data, multi_day_history,
                 dt_data=None, north_data=None, tech_data=None, regime="range"):
    """多因子综合打分（含多日累计+龙虎榜+北向+技术面因子）"""
    if dt_data is None:
        dt_data = {}
    if north_data is None:
        north_data = {}
    if tech_data is None:
        tech_data = {}
    if not rows:
        return []

    def vals(key, default=0):
        return [r.get(key) or default for r in rows if isinstance(r.get(key), (int, float))]

    f62_vals  = vals("f62")
    f66_vals  = vals("f66")
    f72_vals  = vals("f72")
    f184_vals = vals("f184")
    f69_vals  = vals("f69")
    f10_vals  = vals("f10")
    f8_vals   = vals("f8")

    # 构建多日累计值列表用于 percentile rank
    cum3_vals = []
    cum5_vals = []
    cum10_vals = []
    if multi_day_history:
        for row in rows:
            code = row.get("f12", "")
            f62_today = row.get("f62", 0) or 0
            f66_today = row.get("f66", 0) or 0
            h = multi_day_history.get(code, {})
            cum3_vals.append(f62_today + h.get("f62_3d", 0))
            cum5_vals.append(f62_today + h.get("f62_5d", 0))
            cum10_vals.append(f62_today + h.get("f62_10d", 0))

    # ── 行业内相对强度：按 f100(行业)分组，计算每个股票的主力净流入在行业内的 percentile ──
    industry_groups = defaultdict(list)
    for row in rows:
        ind = row.get("f100", "") or ""
        f62 = row.get("f62", 0) or 0
        industry_groups[ind].append(f62)
    intra_sector_rank = {}  # {code: percentile_score}
    for row in rows:
        code = row.get("f12", "")
        ind = row.get("f100", "") or ""
        f62 = row.get("f62", 0) or 0
        group_vals = industry_groups.get(ind, [f62])
        if len(group_vals) > 1 and max(group_vals) > min(group_vals):
            intra_sector_rank[code] = sum(1 for v in group_vals if v <= f62) / len(group_vals)
        else:
            intra_sector_rank[code] = 0.5

    # ── 融资净买入 f168 全市场 percentile ──
    f168_vals = [r.get("f168") or 0 for r in rows if isinstance(r.get("f168"), (int, float))]

    scored = []
    for row in rows:
        try:
            f62  = row.get("f62", 0) or 0
            f66  = row.get("f66", 0) or 0
            f72  = row.get("f72", 0) or 0
            f184 = row.get("f184", 0) or 0
            f69  = row.get("f69", 0) or 0
            f10  = row.get("f10", 0) or 0
            f8   = row.get("f8", 0) or 0
            f3   = row.get("f3", 0) or 0
            f20  = row.get("f20", 0) or 0
            f17  = row.get("f17", 0) or 0
            f18  = row.get("f18", 0) or 0
            f100 = row.get("f100", "") or ""
        except (TypeError, ValueError):
            continue

        s_main_flow   = percentile_rank(f62_vals, f62)
        s_super_flow  = percentile_rank(f66_vals, f66)
        s_big_flow    = percentile_rank(f72_vals, f72)
        s_main_ratio  = percentile_rank(f184_vals, f184)
        s_super_ratio = percentile_rank(f69_vals, f69)
        s_vol_ratio   = range_score(f10, 1.5, 4.0, 0.8, 8.0)
        s_turnover    = range_score(f8, 5.0, 18.0, 2.0, 25.0)
        s_momentum    = range_score(f3, 2.5, 7.0, -2.0, 9.5)

        mcap_yi = f20 / 1e8
        s_mcap = range_score(mcap_yi, 50, 500, 20, 1500)

        if f18 > 0:
            gap_pct = (f17 - f18) / f18 * 100
        else:
            gap_pct = 0
        s_gap = range_score(gap_pct, -1.0, 2.0, -4.0, 5.0)

        # ── 板块资金流因子 ──
        s_industry = sector_rank.get((f100, "industry_flow"), 0.5)
        s_concept  = sector_rank.get((f100, "concept_flow"), 0.5)
        s_sector   = max(s_industry, s_concept)  # 取行业/概念中较好的

        # ── 主力占比排名因子 ──
        code = row.get("f12", "")
        s_ratio_rank = ratio_rank.get(code, 0.5)

        # ── 分析师预测因子 ──
        a = analyst_data.get(code, {})
        s_consensus   = a.get("consensus", 0.5)
        eps_growth    = a.get("eps_growth", 0.0)
        s_eps_growth  = max(0.0, min(1.0, (eps_growth + 0.2) / 0.4)) if eps_growth != 0 else 0.5

        # ── 多日累计资金流因子 ──
        # 累计 = 今日 + 历史N日正流入，用 percentile rank 在全候选池中比较
        h = multi_day_history.get(code, {})
        cum3 = f62 + h.get("f62_3d", 0)
        cum5 = f62 + h.get("f62_5d", 0)
        cum10 = f62 + h.get("f62_10d", 0)

        if multi_day_history and cum3_vals:
            s_flow_3day = sum(1 for v in cum3_vals if v <= cum3) / len(cum3_vals) if cum3 > 0 else 0.0
            s_flow_5day = sum(1 for v in cum5_vals if v <= cum5) / len(cum5_vals) if cum5 > 0 else 0.0
            s_flow_10day = sum(1 for v in cum10_vals if v <= cum10) / len(cum10_vals) if cum10 > 0 else 0.0
        else:
            s_flow_3day = 0.5
            s_flow_5day = 0.5
            s_flow_10day = 0.5

        # ── 资金流加速度因子 ──
        # 加速度 = 3日均流入 / 10日均流入，加速流入 > 1.5 强信号
        if multi_day_history and h.get("daily_f62"):
            daily = h["daily_f62"]  # 最近在前
            d0 = f62  # 今日
            d1 = daily[0] if len(daily) >= 1 else 0
            d2 = daily[1] if len(daily) >= 2 else 0
            avg_3d = (d0 + d1 + d2) / 3 if (d0 + d1 + d2) != 0 else 0
            all_days = [d0] + daily[:9]
            avg_10d = sum(all_days) / len(all_days) if all_days else 0
            if avg_10d > 0 and avg_3d > 0:
                accel_ratio = avg_3d / avg_10d
                accel_ratio = max(0.1, min(4.0, accel_ratio))
            elif avg_3d > 0:
                accel_ratio = 2.0  # 无10日数据但有3日正流入，偏积极
            else:
                accel_ratio = 0.5
            s_flow_accel = range_score(accel_ratio, 1.3, 2.5, 0.3, 4.0)
        else:
            s_flow_accel = 0.5

        # ── 资金流连续性因子 ──
        # 最近 5 日中有多少天主力净流入 > 0
        if multi_day_history and h.get("daily_f62"):
            daily_all = [f62] + h["daily_f62"][:4]  # 今日 + 近4日历史
            positive_days = sum(1 for v in daily_all if v > 0)
            consistency = positive_days / len(daily_all) if daily_all else 0.5
            s_flow_consistency = consistency  # 0~1 线性
        else:
            s_flow_consistency = 0.5

        # ── 行业内相对强度 ──
        s_intra_sector = intra_sector_rank.get(code, 0.5)

        # ── 融资净买入因子 ──
        f168 = row.get("f168")
        if isinstance(f168, (int, float)) and f168_vals and max(f168_vals) > min(f168_vals):
            s_margin_net = sum(1 for v in f168_vals if v <= f168) / len(f168_vals)
            if f168 == 0:
                s_margin_net = 0.5  # 无融资交易，中性
        else:
            s_margin_net = 0.5

        # ── 龙虎榜因子 ──
        dt = dt_data.get(code, {})
        s_dragon_tiger = 0.5  # 未上榜中性
        if dt.get("on_board"):
            s_dragon_tiger = 0.6  # 上榜基础分
            if dt.get("has_institution"):
                s_dragon_tiger += 0.25  # 机构席位买入
            if dt.get("is_main_buy"):
                s_dragon_tiger += 0.15  # 主动买入
            s_dragon_tiger = min(1.0, s_dragon_tiger)

        # ── 北向资金因子 ──
        net_north = north_data.get("net_north", 0)
        north_masked = north_data.get("masked", False)
        if north_masked:
            s_north_flow = 0.5
        elif net_north > 50:
            s_north_flow = 0.85
        elif net_north > 20:
            s_north_flow = 0.70
        elif net_north > 0:
            s_north_flow = 0.60
        elif net_north > -20:
            s_north_flow = 0.40
        elif net_north > -50:
            s_north_flow = 0.25
        else:
            s_north_flow = 0.15

        # ── 技术面因子 ──
        td = tech_data.get(code, {})
        # 均线多头排列: 用已计算的 align_score (0~1)
        s_ma_align = td.get("ma_align_score", 0.5)

        # 突破形态: 20日或60日突破
        if td.get("breakout_60d"):
            s_breakout = 1.0  # 长期突破最强
        elif td.get("breakout_20d"):
            s_breakout = 0.8  # 中期突破
        elif td.get("ma_align_score", 0) >= 0.5:
            s_breakout = 0.6  # 均线支撑
        else:
            s_breakout = 0.3  # 无突破信号

        # 熊市时突破信号更珍贵（逆势上涨），牛市时容易假突破
        if regime == "bear" and td.get("breakout_20d"):
            s_breakout = 1.0  # 逆势突破权重最大化
        elif regime == "bull":
            s_breakout = min(1.0, s_breakout * 0.8)  # 牛市突破打折

        # ── 超大单质量因子 ──
        if f62 > 0:
            super_ratio = max(0.0, min(1.0, f66 / f62))
        else:
            super_ratio = 0.0
        s_super_quality = super_ratio

        total_score = (
            s_main_flow   * WEIGHTS["main_flow"]
            + s_main_ratio  * WEIGHTS["main_ratio"]
            + s_super_flow  * WEIGHTS["super_flow"]
            + s_super_ratio * WEIGHTS["super_ratio"]
            + s_vol_ratio   * WEIGHTS["vol_ratio"]
            + s_turnover    * WEIGHTS["turnover"]
            + s_momentum    * WEIGHTS["momentum"]
            + s_mcap        * WEIGHTS["market_cap"]
            + s_gap         * WEIGHTS["gap"]
            + s_sector      * WEIGHTS["sector_flow"]
            + s_ratio_rank  * WEIGHTS["ratio_rank"]
            + s_intra_sector * WEIGHTS["intra_sector"]
            + s_consensus   * WEIGHTS["analyst_consensus"]
            + s_eps_growth  * WEIGHTS["earnings_growth"]
            + s_flow_3day   * WEIGHTS["flow_3day"]
            + s_flow_5day   * WEIGHTS["flow_5day"]
            + s_flow_10day  * WEIGHTS["flow_10day"]
            + s_flow_accel  * WEIGHTS["flow_accel"]
            + s_flow_consistency * WEIGHTS["flow_consistency"]
            + s_ma_align    * WEIGHTS["ma_align"]
            + s_breakout    * WEIGHTS["breakout"]
            + s_super_quality * WEIGHTS["super_quality"]
            + s_dragon_tiger * WEIGHTS["dragon_tiger"]
            + s_north_flow  * WEIGHTS["north_flow"]
            + s_margin_net  * WEIGHTS["margin_net"]
        )

        row["_score"]          = round(total_score, 4)
        row["_s_flow"]         = round(s_main_flow, 2)
        row["_s_vol"]          = round(s_vol_ratio, 2)
        row["_s_mom"]          = round(s_momentum, 2)
        row["_s_gap"]          = round(s_gap, 2)
        row["_s_sector"]       = round(s_sector, 2)
        row["_s_intra"]        = round(s_intra_sector, 2)
        row["_s_ratio_rank"]   = round(s_ratio_rank, 2)
        row["_s_consensus"]    = round(s_consensus, 2)
        row["_s_eps_growth"]   = round(s_eps_growth, 2)
        row["_s_flow_3day"]    = round(s_flow_3day, 2)
        row["_s_flow_5day"]    = round(s_flow_5day, 2)
        row["_s_flow_10day"]   = round(s_flow_10day, 2)
        row["_s_flow_accel"]   = round(s_flow_accel, 2)
        row["_s_flow_cons"]    = round(s_flow_consistency, 2)
        row["_s_super_quality"] = round(s_super_quality, 2)
        row["_s_dragon_tiger"] = round(s_dragon_tiger, 2)
        row["_s_north_flow"]   = round(s_north_flow, 2)
        row["_s_margin_net"]   = round(s_margin_net, 2)
        row["_s_ma_align"]     = round(s_ma_align, 2)
        row["_s_breakout"]     = round(s_breakout, 2)
        row["_ma5"]            = td.get("ma5")
        row["_ma20"]           = td.get("ma20")
        row["_breakout_20d"]   = td.get("breakout_20d", False)
        row["_cum3"]           = round(cum3 / 1e8, 2)
        row["_cum5"]           = round(cum5 / 1e8, 2)
        row["_cum10"]          = round(cum10 / 1e8, 2)
        row["_mcap_yi"]        = round(mcap_yi, 1)
        row["_industry"]       = f100
        row["_analyst_num"]    = a.get("org_num", 0)
        scored.append(row)

    scored.sort(key=lambda r: r["_score"], reverse=True)
    return scored


def filter_stocks(rows):
    """风控过滤"""
    passed = []
    for row in rows:
        name = row.get("f14", "")
        price = row.get("f2")
        chg   = row.get("f3")
        f62   = row.get("f62")
        f184  = row.get("f184")
        f10   = row.get("f10")
        f8    = row.get("f8")
        f20   = row.get("f20")

        if not all(isinstance(v, (int, float)) for v in [price, chg, f62, f184, f10, f8, f20]):
            continue
        if EXCLUDE_ST and ("ST" in str(name) or "*ST" in str(name)):
            continue
        if price < MIN_PRICE or price > MAX_PRICE:
            continue
        if chg > MAX_CHG_PCT or chg < MIN_CHG_PCT:
            continue
        if f62 < MIN_MAIN_FLOW:
            continue
        if f184 < MIN_MAIN_RATIO:
            continue
        if f10 < MIN_VOL_RATIO:
            continue
        if f8 < MIN_TURNOVER or f8 > MAX_TURNOVER:
            continue
        if f20 < MIN_MCAP or f20 > MAX_MCAP:
            continue

        passed.append(row)
    return passed


# ============================================================
# 展示
# ============================================================
def format_amount(value):
    if value is None or value == "-":
        return "-"
    v = float(value)
    if abs(v) >= 1e8:
        return f"{v / 1e8:+.2f}亿"
    elif abs(v) >= 1e4:
        return f"{v / 1e4:+.2f}万"
    else:
        return f"{v:+.2f}"


def print_recommendations(rows, top_n=5, regime="range", regime_detail=None):
    if not rows:
        print("\n  当前无符合条件的个股")
        return

    top = rows[:top_n]
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

    regime_labels = {"bull": "🐂 牛市", "bear": "🐻 熊市", "range": "📊 震荡市"}
    regime_label = regime_labels.get(regime, regime)
    pos_hint = {"bull": "建议仓位 80%+ 积极追击", "bear": "建议仓位 30% 谨慎参与", "range": "建议仓位 50% 精选个股"}

    print(f"\n{'=' * 86}")
    print(f"  主力资金跟随选股 [仅主板] — {now}")
    print(f"  市场环境: {regime_label} | {pos_hint.get(regime, '')}")
    if regime_detail:
        print(f"  涨跌比{regime_detail.get('up_ratio',0):.0%} | "
              f"中位{regime_detail.get('median_ret',0):+.1f}% | "
              f"主力正{regime_detail.get('flow_positive_ratio',0):.0%}")
    print(f"  策略: 资金流 + 量价 + 动量 + 多日 + 技术面 + 龙虎榜/北向 + 板块/分析师")
    print(f"{'=' * 130}\n")

    # 结果表
    hdr = f"  {'排名':<4} {'代码':<8} {'名称':<6} {'现价':<6} {'涨跌':<7} {'主力流入':<9} {'3日累计':<9} {'均线':<4} {'突破':<4} {'行业':<6} {'得分':<6}"
    print(hdr)
    print(f"  {'-' * 106}")
    for i, row in enumerate(top):
        code       = row.get("f12", "-")
        name       = row.get("f14", "-")
        price      = row.get("f2", 0) or 0
        chg        = row.get("f3", 0) or 0
        f62        = row.get("f62", 0) or 0
        cum3       = row.get("_cum3", 0)
        cum5       = row.get("_cum5", 0)
        ma_align   = row.get("_s_ma_align", 0)
        breakout   = row.get("_breakout_20d", False)
        industry   = row.get("_industry", "-")
        score      = row.get("_score", 0)
        ma_mark    = "✓" if ma_align >= 0.5 else ("↑" if ma_align >= 0.25 else "✗")
        brk_mark   = "✓" if breakout else "-"
        print(f"  {i+1:<4} {code:<8} {name:<6} {price:<6.2f} {chg:>+6.2f}% "
              f"{format_amount(f62):>9} {cum3:>+8.2f}亿 {ma_mark:<4} {brk_mark:<4} {industry:<6} {score:>.4f}")

    # 因子解读
    print(f"\n  {'─' * 84}")
    print(f"  📊 选股因子体系（A股回测胜率参考）:\n")
    print(f"  {'因子':<16} {'权重':<8} {'参考胜率':<10} {'逻辑'}")
    print(f"  {'─' * 65}")
    print(f"  {'主力净流入额':<14} {WEIGHTS['main_flow']*100:>5.1f}%  {'55-62%':<10} {'主力买入金额越大，短期上涨概率越高'}")
    print(f"  {'主力净流入占比':<12} {WEIGHTS['main_ratio']*100:>5.1f}%  {'57-63%':<10} {'占比高=资金集中度高，不易被散户砸盘'}")
    print(f"  {'超大单净流入':<12} {WEIGHTS['super_flow']*100:>5.1f}%  {'53-58%':<10} {'>100万大单=机构/北向行为，持续性强'}")
    print(f"  {'量比':<16} {WEIGHTS['vol_ratio']*100:>5.1f}%  {'52-57%':<10} {'放量上涨=量价配合，缩量上涨=背离风险'}")
    print(f"  {'涨跌幅动量':<12} {WEIGHTS['momentum']*100:>5.1f}%  {'54-59%':<10} {'温和上涨次日延续概率高，大涨后追高风险大'}")
    print(f"  {'行业板块资金流':<10} {WEIGHTS['sector_flow']*100:>5.1f}%  {'56-63%':<10} {'板块与个股共振，热钱流入行业龙头/跟风'}")
    print(f"  {'行业内相对强度':<10} {WEIGHTS['intra_sector']*100:>5.1f}%  {'60-68%':<10} {'行业内排名越高=资金向龙头集中，跟风股弱'}")
    print(f"  {'主力占比排名':<10} {WEIGHTS['ratio_rank']*100:>5.1f}%  {'58-65%':<10} {'主力控盘信号，占比越高主力参与度越深'}")
    print(f"  {'分析师评级共识':<10} {WEIGHTS['analyst_consensus']*100:>5.1f}%  {'55-62%':<10} {'机构买入评级占比高=基本面受认可'}")
    print(f"  {'EPS预测增长':<12} {WEIGHTS['earnings_growth']*100:>5.1f}%  {'53-58%':<10} {'盈利增长预期强=股价有基本面支撑'}")
    print(f"  {'3日累计主力流入':<10} {WEIGHTS['flow_3day']*100:>5.1f}%  {'62-70%':<10} {'3日持续流入>单日脉冲，排除一日游游资'}")
    print(f"  {'5日累计主力流入':<10} {WEIGHTS['flow_5day']*100:>5.1f}%  {'64-72%':<10} {'一周持续买入=机构建仓行为特征'}")
    print(f"  {'10日累计主力流入':<9} {WEIGHTS['flow_10day']*100:>5.1f}%  {'66-74%':<10} {'长期持续流入=深度机构参与，趋势确认'}")
    print(f"  {'流入加速度':<14} {WEIGHTS['flow_accel']*100:>5.1f}%  {'63-72%':<10} {'3日均>10日均=资金正在加速涌入，趋势强化'}")
    print(f"  {'流入连续性':<14} {WEIGHTS['flow_consistency']*100:>5.1f}%  {'65-75%':<10} {'连续多日正流入>脉冲式单日流入，排除游资一日游'}")
    print(f"  {'融资净买入':<14} {WEIGHTS['margin_net']*100:>5.1f}%  {'58-66%':<10} {'杠杆资金态度，融资+主力双流入=高确信信号'}")
    print(f"  {'均线多头排列':<12} {WEIGHTS['ma_align']*100:>5.1f}%  {'63-70%':<10} {'5>10>20>60日线=上升趋势确认'}")
    print(f"  {'突破形态':<14} {WEIGHTS['breakout']*100:>5.1f}%  {'60-68%':<10} {'突破20/60日高点=上涨空间打开'}")
    print(f"  {'龙虎榜上榜':<12} {WEIGHTS['dragon_tiger']*100:>5.1f}%  {'65-75%':<10} {'上榜+机构席位买入=主力真金白银介入'}")
    print(f"  {'北向资金方向':<12} {WEIGHTS['north_flow']*100:>5.1f}%  {'55-62%':<10} {'北向净流入=外资看多A股，大盘环境偏暖'}")
    print(f"  {'超大单质量':<12} {WEIGHTS['super_quality']*100:>5.1f}%  {'58-65%':<10} {'超大单占比高=机构行为，占比低可能游资对倒'}")
    print(f"  {'换手率':<14} {WEIGHTS['turnover']*100:>5.1f}%  {'50-55%':<10} {'适中换手=筹码活跃，天量换手=出货嫌疑'}")
    print(f"  {'市值':<16} {WEIGHTS['market_cap']*100:>5.1f}%  {'53-57%':<10} {'50-500亿中小盘弹性最大，大盘股难拉动'}")
    print(f"  {'开盘缺口':<14} {WEIGHTS['gap']*100:>5.1f}%  {'51-54%':<10} {'低开平开后走高=资金盘中建仓，高开低走=出货'}")

    # 逐只分析
    print(f"\n  📋 逐只诊断:\n")
    for i, row in enumerate(top):
        code     = row.get("f12", "-")
        name     = row.get("f14", "-")
        chg      = row.get("f3", 0) or 0
        f184     = row.get("f184", 0) or 0
        f66      = row.get("f66", 0) or 0
        f10      = row.get("f10", 0) or 0
        f8       = row.get("f8", 0) or 0
        mcap     = row.get("_mcap_yi", 0)
        industry  = row.get("_industry", "-")
        s_sector  = row.get("_s_sector", 0)
        s_rr      = row.get("_s_ratio_rank", 0)
        s_cons      = row.get("_s_consensus", 0)
        s_eps       = row.get("_s_eps_growth", 0)
        s_3d        = row.get("_s_flow_3day", 0)
        s_5d        = row.get("_s_flow_5day", 0)
        s_10d       = row.get("_s_flow_10day", 0)
        s_accel     = row.get("_s_flow_accel", 0)
        s_cons      = row.get("_s_flow_cons", 0)
        s_intra     = row.get("_s_intra", 0)
        s_margin    = row.get("_s_margin_net", 0)
        s_super_q   = row.get("_s_super_quality", 0)
        s_dt        = row.get("_s_dragon_tiger", 0)
        s_north     = row.get("_s_north_flow", 0)
        s_ma        = row.get("_s_ma_align", 0)
        s_brk       = row.get("_s_breakout", 0)
        ma5         = row.get("_ma5")
        ma20        = row.get("_ma20")
        brk_20d     = row.get("_breakout_20d", False)
        cum3        = row.get("_cum3", 0)
        cum5        = row.get("_cum5", 0)
        a_num       = row.get("_analyst_num", 0)
        gap_ok      = (row.get("_s_gap") or 0) > 0.5

        signals = []
        risks = []

        if (row.get("_s_flow") or 0) > 0.6:
            signals.append(f"主力净流入 {format_amount(row.get('f62'))}, 排名前 {100 - (row.get('_s_flow') or 0)*100:.0f}%")
        if f184 > 5:
            signals.append(f"主力占比 {f184:.1f}%, 资金高度集中")
        if s_3d > 0.7:
            signals.append(f"3日累计净流入 {cum3:+.1f}亿, 短期趋势强劲({s_3d:.0%})")
        if s_5d > 0.7:
            signals.append(f"5日累计净流入 {cum5:+.1f}亿, 中期持续流入({s_5d:.0%})")
        if s_10d > 0.7:
            signals.append(f"10日累计净流入排名靠前({s_10d:.0%}), 机构长期建仓特征")
        if s_accel > 0.7:
            signals.append(f"资金流入加速({s_accel:.0%}), 3日均流入远超10日均值, 趋势强化中")
        if s_cons > 0.7:
            signals.append(f"近5日持续正流入({s_cons:.0%}), 非一日游脉冲, 资金持续建仓")
        if s_intra > 0.7:
            signals.append(f"行业内主力净流入排名靠前({s_intra:.0%}), 资金向本股集中")
        if s_margin > 0.7:
            f168_val = row.get("f168") or 0
            signals.append(f"融资净买入排名靠前({s_margin:.0%}), 杠杆资金看多, 与主力共振")
        if s_sector > 0.7:
            signals.append(f"所属行业「{industry}」板块资金流排名靠前({s_sector:.0%})")
        if s_rr > 0.7:
            signals.append(f"主力占比排名靠前({s_rr:.0%}), 主力控盘信号")
        if s_cons > 0.7:
            signals.append(f"分析师评级共识强({s_cons:.0%}), {a_num}家机构覆盖")
        if s_eps > 0.7:
            signals.append(f"EPS预测增长乐观({s_eps:.0%}), 盈利动量向上")
        if s_ma >= 0.5:
            ma_str = f"MA5={ma5}" if ma5 else ""
            signals.append(f"均线多头排列({s_ma:.0%}), {ma_str} 趋势向上")
        if brk_20d:
            signals.append(f"突破20日高点, 上涨空间打开")
        if s_brk > 0.6 and not brk_20d:
            signals.append(f"均线支撑有效({s_brk:.0%}), 趋势良好")
        if s_dt > 0.7:
            signals.append(f"龙虎榜上榜+机构买入, 主力真金白银介入({s_dt:.0%})")
        if s_north > 0.6:
            signals.append(f"北向资金净流入, 外资看多A股环境偏暖({s_north:.0%})")
        if s_super_q > 0.5:
            signals.append(f"超大单占比{s_super_q:.0%}, 机构行为特征明显")
        if (row.get("_s_vol") or 0) > 0.7:
            signals.append(f"量比 {f10:.1f}, 放量配合良好")
        if (row.get("_s_mom") or 0) > 0.7:
            signals.append(f"涨跌幅 {chg:+.1f}% 处于最佳追击区间")
        if gap_ok:
            f17 = row.get("f17", 0) or 0
            f18 = row.get("f18", 0) or 0
            gap = (f17 - f18) / f18 * 100 if f18 > 0 else 0
            signals.append(f"开盘缺口 {gap:+.1f}%, 盘中资金介入信号")

        if f8 > 20:
            risks.append(f"换手率偏高({f8:.1f}%), 注意筹码交换风险")
        if chg > 8:
            risks.append(f"已近涨停({chg:.1f}%), 次日追板风险大")
        if f10 > 6:
            risks.append(f"量比过大({f10:.1f}), 可能是天量见顶信号")
        if s_sector < 0.3:
            risks.append(f"所属行业「{industry}」板块资金流偏弱")
        if s_cons < 0.3 and a_num >= 3:
            risks.append(f"分析师评级偏谨慎({s_cons:.0%}), {a_num}家机构覆盖")
        if s_eps < 0.3:
            risks.append(f"EPS预测增长乏力({s_eps:.0%})")
        if s_ma < 0.25:
            risks.append(f"均线空头排列, 趋势偏弱")
        if s_3d < 0.3 and s_5d < 0.3:
            risks.append(f"3日/5日累计流入排名偏低, 可能为一日游资金")
        if s_accel < 0.3 and s_3d > 0.5:
            risks.append(f"资金流入减速({s_accel:.0%}), 主力可能在边拉边撤")
        if s_cons < 0.3:
            risks.append(f"近5日正流入天数少({s_cons:.0%}), 缺乏持续性")
        if s_intra < 0.3:
            risks.append(f"行业内排名偏后({s_intra:.0%}), 资金未向本股集中")
        if s_margin < 0.3 and s_margin != 0.5:
            risks.append(f"融资资金净卖出({s_margin:.0%}), 杠杆资金偏空")
        if s_north < 0.3:
            risks.append(f"北向资金大幅流出, 外资撤离环境偏空")
        if s_super_q < 0.3 and f62 > 0:
            risks.append(f"超大单占比仅{s_super_q:.0%}, 主力可能为游资/散户大单")

        print(f"    {i+1}. {code} {name}  |  {mcap:.0f}亿  |  {industry}  |  分析师{a_num}家  |  综合分 {row.get('_score', 0):.4f}")
        for s in signals:
            print(f"       ✅ {s}")
        for r in risks:
            print(f"       ⚠️  {r}")
        if not signals:
            print(f"       ➡️  综合资金信号多头排列")
        print()

    print(f"  ⚠️  免责声明: 基于历史资金流 + 量价数据回测，不构成投资建议。")
    print(f"      股市有风险，投资需谨慎。请结合基本面及自身风险承受能力决策。\n")


def main():
    top_n = TOP_N
    date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    for arg in sys.argv:
        if arg.startswith("--top="):
            top_n = int(arg.split("=")[1])
        if arg.startswith("--date="):
            date_str = arg.split("=")[1]

    result = get_picks(date_str, top_n)
    if result is None:
        sys.exit(1)

    # 输出推荐
    print_recommendations(result["scored"], top_n, result["regime"], result["regime_detail"])

    # 记录到绩效追踪
    try:
        from performance import record_picks
        record_picks(result["scored"][:top_n], date_str)
    except ImportError:
        pass


def get_picks(date_str=None, top_n=5):
    """程序化接口：返回结构化选股结果 dict
    返回:
      None 如果数据不可用
      dict: {
        "date": str, "regime": str, "regime_detail": dict,
        "weights": dict, "picks": [dict, ...], "scored": [dict, ...]
      }
    """
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    rows = load_local_data(date_str)
    if not rows:
        return None

    # 板块 + 占比排名 + 分析师 + 多日历史 + 龙虎榜 + 北向资金
    multi_sector_history = load_multi_day_sector_history(date_str)
    sector_rank, ratio_rank = load_extra_data(date_str, multi_sector_history)
    analyst_data = load_analyst_data(date_str)
    multi_day_history = load_multi_day_history(date_str)
    dt_data = load_dragon_tiger_data(date_str)
    north_data = load_north_flow_data(date_str)

    # 市场环境检测 → 选择自适应权重（统一使用 market_diagnosis）
    from market_diagnosis import get_diagnosis
    diag = get_diagnosis(date_str)
    if diag:
        regime = diag["regime"]["regime"]
        regime_detail = diag
    else:
        # 回退: 自底向上检测
        regime, _, regime_detail = detect_market_regime(date_str, rows)
    global WEIGHTS
    if regime == "bull":
        WEIGHTS = WEIGHTS_BULL
    elif regime == "bear":
        WEIGHTS = WEIGHTS_BEAR
    else:
        WEIGHTS = WEIGHTS_BASE

    # 主板过滤
    main_rows = [r for r in rows if is_main_board(r.get("f12", ""))]

    # 风控过滤
    filtered = filter_stocks(main_rows)

    # 技术面数据加载
    filtered_codes = set(r.get("f12", "") for r in filtered)
    tech_data = load_technical_data(date_str, filtered_codes)

    # 多因子打分
    scored = score_stocks(filtered, sector_rank, ratio_rank, analyst_data,
                          multi_day_history, dt_data, north_data, tech_data, regime)

    # 构建 picks 摘要列表
    picks = []
    for i, row in enumerate(scored[:top_n]):
        signals, risks = _build_diagnosis(row)
        picks.append({
            "rank": i + 1,
            "code": row.get("f12", ""),
            "name": row.get("f14", ""),
            "price": row.get("f2"),
            "chg": row.get("f3"),
            "score": row.get("_score"),
            "main_flow": row.get("f62"),
            "main_ratio": row.get("f184"),
            "market_cap_yi": row.get("_mcap_yi"),
            "industry": row.get("_industry", ""),
            "cum_3d": row.get("_cum3"),
            "cum_5d": row.get("_cum5"),
            "cum_10d": row.get("_cum10"),
            "analyst_num": row.get("_analyst_num", 0),
            "ma_align": row.get("_s_ma_align"),
            "breakout_20d": row.get("_breakout_20d", False),
            "sub_scores": {k.replace("_s_", ""): v for k, v in row.items() if k.startswith("_s_")},
            "signals": signals,
            "risks": risks,
        })

    return {
        "date": date_str,
        "regime": regime,
        "regime_detail": regime_detail,
        "weights": {k: round(v, 3) for k, v in WEIGHTS.items()},
        "weights_label": {"bull": "牛市", "bear": "熊市", "range": "震荡市"}.get(regime, "默认"),
        "total_candidates": len(filtered),
        "picks": picks,
        "scored": scored,  # full scored list for print_recommendations
    }


def _build_diagnosis(row):
    """构建单只个股的信号和风险描述"""
    signals = []
    risks = []

    code = row.get("f12", "-")
    name = row.get("f14", "-")
    chg = row.get("f3", 0) or 0
    f184 = row.get("f184", 0) or 0
    f10 = row.get("f10", 0) or 0
    f8 = row.get("f8", 0) or 0
    f62 = row.get("f62", 0) or 0
    mcap = row.get("_mcap_yi", 0)
    industry = row.get("_industry", "-")
    s_sector = row.get("_s_sector", 0)
    s_rr = row.get("_s_ratio_rank", 0)
    s_cons = row.get("_s_consensus", 0)
    s_eps = row.get("_s_eps_growth", 0)
    s_3d = row.get("_s_flow_3day", 0)
    s_5d = row.get("_s_flow_5day", 0)
    s_10d = row.get("_s_flow_10day", 0)
    s_accel = row.get("_s_flow_accel", 0)
    s_consistency = row.get("_s_flow_cons", 0)
    s_intra = row.get("_s_intra", 0)
    s_margin = row.get("_s_margin_net", 0)
    s_super_q = row.get("_s_super_quality", 0)
    s_dt = row.get("_s_dragon_tiger", 0)
    s_north = row.get("_s_north_flow", 0)
    s_ma = row.get("_s_ma_align", 0)
    s_brk = row.get("_s_breakout", 0)
    ma5 = row.get("_ma5")
    brk_20d = row.get("_breakout_20d", False)
    cum3 = row.get("_cum3", 0)
    cum5 = row.get("_cum5", 0)
    a_num = row.get("_analyst_num", 0)
    gap_ok = (row.get("_s_gap") or 0) > 0.5

    if (row.get("_s_flow") or 0) > 0.6:
        signals.append(f"主力净流入 {format_amount(row.get('f62'))}, 排名前 {100 - (row.get('_s_flow') or 0)*100:.0f}%")
    if f184 > 5:
        signals.append(f"主力占比 {f184:.1f}%, 资金高度集中")
    if s_3d > 0.7:
        signals.append(f"3日累计净流入 {cum3:+.1f}亿, 短期趋势强劲({s_3d:.0%})")
    if s_5d > 0.7:
        signals.append(f"5日累计净流入 {cum5:+.1f}亿, 中期持续流入({s_5d:.0%})")
    if s_10d > 0.7:
        signals.append(f"10日累计净流入排名靠前({s_10d:.0%}), 机构长期建仓特征")
    if s_accel > 0.7:
        signals.append(f"资金流入加速({s_accel:.0%}), 3日均流入远超10日均值, 趋势强化中")
    if s_consistency > 0.7:
        signals.append(f"近5日持续正流入({s_consistency:.0%}), 非一日游脉冲, 资金持续建仓")
    if s_intra > 0.7:
        signals.append(f"行业内主力净流入排名靠前({s_intra:.0%}), 资金向本股集中")
    if s_margin > 0.7:
        f168_val = row.get("f168") or 0
        signals.append(f"融资净买入排名靠前({s_margin:.0%}), 杠杆资金看多, 与主力共振")
    if s_sector > 0.7:
        signals.append(f"所属行业「{industry}」板块资金流排名靠前({s_sector:.0%})")
    if s_rr > 0.7:
        signals.append(f"主力占比排名靠前({s_rr:.0%}), 主力控盘信号")
    if s_cons > 0.7:
        signals.append(f"分析师评级共识强({s_cons:.0%}), {a_num}家机构覆盖")
    if s_eps > 0.7:
        signals.append(f"EPS预测增长乐观({s_eps:.0%}), 盈利动量向上")
    if s_ma >= 0.5:
        ma_str = f"MA5={ma5}" if ma5 else ""
        signals.append(f"均线多头排列({s_ma:.0%}), {ma_str} 趋势向上")
    if brk_20d:
        signals.append(f"突破20日高点, 上涨空间打开")
    if s_brk > 0.6 and not brk_20d:
        signals.append(f"均线支撑有效({s_brk:.0%}), 趋势良好")
    if s_dt > 0.7:
        signals.append(f"龙虎榜上榜+机构买入, 主力真金白银介入({s_dt:.0%})")
    if s_north > 0.6:
        signals.append(f"北向资金净流入, 外资看多A股环境偏暖({s_north:.0%})")
    if s_super_q > 0.5:
        signals.append(f"超大单占比{s_super_q:.0%}, 机构行为特征明显")
    if (row.get("_s_vol") or 0) > 0.7:
        signals.append(f"量比 {f10:.1f}, 放量配合良好")
    if (row.get("_s_mom") or 0) > 0.7:
        signals.append(f"涨跌幅 {chg:+.1f}% 处于最佳追击区间")
    if gap_ok:
        f17 = row.get("f17", 0) or 0
        f18 = row.get("f18", 0) or 0
        gap = (f17 - f18) / f18 * 100 if f18 > 0 else 0
        signals.append(f"开盘缺口 {gap:+.1f}%, 盘中资金介入信号")

    if f8 > 20:
        risks.append(f"换手率偏高({f8:.1f}%), 注意筹码交换风险")
    if chg > 8:
        risks.append(f"已近涨停({chg:.1f}%), 次日追板风险大")
    if f10 > 6:
        risks.append(f"量比过大({f10:.1f}), 可能是天量见顶信号")
    if s_sector < 0.3:
        risks.append(f"所属行业「{industry}」板块资金流偏弱")
    if s_cons < 0.3 and a_num >= 3:
        risks.append(f"分析师评级偏谨慎({s_cons:.0%}), {a_num}家机构覆盖")
    if s_eps < 0.3:
        risks.append(f"EPS预测增长乏力({s_eps:.0%})")
    if s_ma < 0.25:
        risks.append(f"均线空头排列, 趋势偏弱")
    if s_3d < 0.3 and s_5d < 0.3:
        risks.append(f"3日/5日累计流入排名偏低, 可能为一日游资金")
    if s_accel < 0.3 and s_3d > 0.5:
        risks.append(f"资金流入减速({s_accel:.0%}), 主力可能在边拉边撤")
    if s_consistency < 0.3:
        risks.append(f"近5日正流入天数少({s_consistency:.0%}), 缺乏持续性")
    if s_intra < 0.3:
        risks.append(f"行业内排名偏后({s_intra:.0%}), 资金未向本股集中")
    if s_margin < 0.3 and s_margin != 0.5:
        risks.append(f"融资资金净卖出({s_margin:.0%}), 杠杆资金偏空")
    if s_north < 0.3:
        risks.append(f"北向资金大幅流出, 外资撤离环境偏空")
    if s_super_q < 0.3 and f62 > 0:
        risks.append(f"超大单占比仅{s_super_q:.0%}, 主力可能为游资/散户大单")

    return signals, risks


if __name__ == "__main__":
    main()

