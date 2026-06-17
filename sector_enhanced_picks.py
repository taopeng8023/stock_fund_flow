"""
板块增强选股 — 板块共振过滤 + 全数据源多因子评分
策略: 仅从主力资金最热的 Top N 行业板块中选股，综合分析师/龙虎榜/北向/技术面等全维度打分

与 sector_picks.py 的差异:
  - 保留板块优先过滤 + P0-P27 精华因子
  - 新增分析师评级/EPS增长因子 (来自 analyst_forecast.json)
  - 新增龙虎榜上榜/机构买入因子 (来自 dragon_tiger.json)
  - 新增北向资金环境因子 (来自 north_flow.json)
  - 新增主力占比排名因子 (来自 rank_ratio.json)
  - 新增技术面 MA/突破因子 (来自多日历史收盘价)
  - 新增多日累计流入/连续性/加速度因子
  - 新增牛/熊/震荡三市自适应权重

与 stock_picker.py 的差异:
  - 只在 Top N 行业板块成分股中选股（板块共振），候选池从 4000+ → ~300 只
  - 评分体系以启动信号为核心（35% → 25%），更适合次日溢价策略
  - 保留涨停观察池（sector_picks 特色）

用法:
  python sector_enhanced_picks.py                      默认读取今天数据
  python sector_enhanced_picks.py --date=20260617      指定日期
  python sector_enhanced_picks.py --sectors=5           取前 N 个行业板块
  python sector_enhanced_picks.py --top=10              输出前 N 只精选股
"""
import sys
import os
import csv
import json
import statistics
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from fetchers.base import DATA_ROOT, BJS_TZ, load_json, save_data

# ============================================================
# 配置 — 风控阈值
# ============================================================
MIN_PRICE       = 4.0
MAX_PRICE       = 200.0
LIMIT_UP_PCT    = 9.8
CANDIDATE_MAX_CHG = 9.5
MIN_MAIN_FLOW   = 3000_0000   # 3000万
MIN_MAIN_RATIO  = 1.0         # 1%
MIN_TURNOVER    = 2.0
MAX_TURNOVER    = 25.0
MIN_VOL_RATIO   = 1.0
MIN_MCAP_YI     = 30          # 30亿
MAX_MCAP_YI     = 2000        # 2000亿

MAIN_BOARD_PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")

# ============================================================
# 权重配置 — 三市场景自适应
# ============================================================
WEIGHTS_BASE = {
    "start_signal":  0.25,   # 启动信号（板块新鲜度+资金加速度）
    "capital":       0.20,   # 主力资金强度（净流入+占比+超大单质量）
    "trend":         0.15,   # 趋势确认（量比+换手率+短期斜率）
    "sector":        0.10,   # 板块共振（排名+持续性+概念板块）
    "position":      0.10,   # 位置健康（相对60日位置+MA站上）
    "analyst":       0.05,   # 分析师共识（评级+EPS增长）
    "multiday":      0.05,   # 多日累计（3/5/10日+连续性）
    "technical":     0.04,   # 技术面（MA多头+突破）
    "dragon_tiger":  0.03,   # 龙虎榜（上榜+机构买入）
    "north_flow":    0.02,   # 北向资金环境
    "ratio_rank":    0.01,   # 主力占比排名
}

WEIGHTS_BULL = {**WEIGHTS_BASE,
    "trend": 0.18, "dragon_tiger": 0.05, "analyst": 0.03, "position": 0.07,
    "start_signal": 0.22, "capital": 0.22,
}

WEIGHTS_BEAR = {**WEIGHTS_BASE,
    "analyst": 0.08, "north_flow": 0.04, "position": 0.13, "start_signal": 0.20,
    "trend": 0.12, "dragon_tiger": 0.02, "capital": 0.18,
}

WEIGHTS = WEIGHTS_BASE  # 运行时根据 market_diagnosis 替换


# ============================================================
# 辅助函数
# ============================================================

def _to_float(val):
    if val is None or val == "-" or val == "":
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _pct_rank(values, target):
    """percentile rank: 0~1"""
    if not values or max(values) == min(values):
        return 0.5
    return sum(1 for v in values if v <= target) / len(values)


def _range_score(value, ideal_min, ideal_max, floor, ceil):
    """区间评分：理想区间内=1.0，越远越低"""
    if ideal_min <= value <= ideal_max:
        return 1.0
    if value < ideal_min:
        return max(0.0, (value - floor) / (ideal_min - floor)) if ideal_min > floor else 0.0
    return max(0.0, (ceil - value) / (ceil - ideal_max)) if ceil > ideal_max else 0.0


def _fmt_yi(v):
    if abs(v) >= 1e8:
        return f"{v/1e8:+.2f}亿"
    return f"{v/1e4:+.0f}万"


def is_main_board(code):
    return isinstance(code, str) and code.startswith(MAIN_BOARD_PREFIXES)


# ============================================================
# 数据加载 — 板块层
# ============================================================

def load_sector_top_codes(date_str, top_n=5):
    """从 industry_flow_*.csv 获取主力净流入 Top N 板块代码"""
    import glob
    patterns = [
        os.path.join(DATA_ROOT, date_str, "industry_flow_*.csv"),
        os.path.join(DATA_ROOT, date_str, "industry_flow.csv"),
    ]
    all_matches = []
    for pat in patterns:
        all_matches.extend(glob.glob(pat))
    csv_path = sorted(all_matches, key=os.path.getmtime, reverse=True)[0] if all_matches else ""
    codes = []
    if csv_path and os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        sorted_rows = sorted(rows, key=lambda r: _to_float(r.get("主力净流入")), reverse=True)
        for r in sorted_rows[:top_n]:
            code = r.get("代码", "")
            codes.append(code)
            flow_yi = _to_float(r.get("主力净流入")) / 1e8
            print(f"  Top板块: {code} {r.get('名称','')} 主力{flow_yi:+.2f}亿")
    else:
        print(f"  ✗ 未找到行业板块CSV: data/{date_str}/industry_flow_*.csv")
    return codes


def _load_sector_names(date_str):
    """从 industry_flow_*.csv 加载 BK代码→板块名称 映射"""
    import glob
    patterns = [
        os.path.join(DATA_ROOT, date_str, "industry_flow_*.csv"),
        os.path.join(DATA_ROOT, date_str, "industry_flow.csv"),
    ]
    all_matches = []
    for pat in patterns:
        all_matches.extend(glob.glob(pat))
    csv_path = sorted(all_matches, key=os.path.getmtime, reverse=True)[0] if all_matches else ""
    if csv_path and os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            return {r["代码"]: r["名称"] for r in csv.DictReader(f)}
    return {}


def load_sector_stocks(sector_codes, date_str=None):
    """实时从东方财富 API 拉取板块成分股 + 5日/10日排名"""
    from fetchers.sector_flow import fetch_sector_stocks
    all_stocks = []
    seen = set()
    sector_names = _load_sector_names(date_str) if date_str else {}

    for code in sector_codes:
        print(f"  实时拉取 {code} 成分股...", end=" ", flush=True)
        stocks = fetch_sector_stocks(code, "", None)
        if not stocks:
            print("无数据")
            continue
        for s in stocks:
            stock_code = s.get("f12", "")
            if stock_code and stock_code not in seen:
                seen.add(stock_code)
                s["_sector_code"] = code
                s["_sector_name"] = sector_names.get(code, code)
                all_stocks.append(s)
        print(f"{len(stocks)} 只")
    print(f"  去重后共计 {len(all_stocks)} 只个股（{len(sector_codes)} 个板块）")
    return all_stocks


def load_sector_multiday(date_str):
    """加载行业板块今日/5日/10日排名 → 板块新鲜度 + 持续性"""
    import glob
    patterns = [
        os.path.join(DATA_ROOT, date_str, "industry_flow_*.csv"),
        os.path.join(DATA_ROOT, date_str, "industry_flow.csv"),
    ]
    all_matches = []
    for pat in patterns:
        all_matches.extend(glob.glob(pat))
    csv_path = sorted(all_matches, key=os.path.getmtime, reverse=True)[0] if all_matches else ""
    if not csv_path:
        return {}, {}, {}
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {}, {}, {}

    by_f62 = sorted(rows, key=lambda r: _to_float(r.get("主力净流入")), reverse=True)
    by_5d = sorted(rows, key=lambda r: _to_float(r.get("5日主力净流入")), reverse=True)
    by_10d = sorted(rows, key=lambda r: _to_float(r.get("10日主力净流入")), reverse=True)

    rank_today = {r.get("代码"): i + 1 for i, r in enumerate(by_f62)}
    rank_5d = {r.get("代码"): i + 1 for i, r in enumerate(by_5d)}
    rank_10d = {r.get("代码"): i + 1 for i, r in enumerate(by_10d)}
    total = len(rows)

    # 板块启动信号: 今日排名相比5日/10日提升越多，越新鲜
    sector_freshness = {}
    sector_persistence = {}
    for r in rows:
        code = r.get("代码", "")
        r_today = rank_today.get(code, total)
        r_5d = rank_5d.get(code, total)
        r_10d = rank_10d.get(code, total)
        jump_5d = (r_5d - r_today) / total
        jump_10d = (r_10d - r_today) / total
        freshness = max(0.0, min(1.0, (jump_5d * 0.6 + jump_10d * 0.4 + 0.3)))
        sector_freshness[code] = round(freshness, 3)
        persistence = 1.0 if r_5d <= total * 0.3 else (0.7 if r_5d <= total * 0.5 else 0.4)
        sector_persistence[code] = persistence

    return sector_freshness, rank_today, sector_persistence


# ============================================================
# 数据加载 — 全市场增强数据（来自 fetch_data.py 采集结果）
# ============================================================

def load_analyst_data(date_str):
    """加载分析师预测数据 → {code: {consensus, eps_growth, org_num}}"""
    rows = load_json(date_str, "analyst_forecast")
    if rows is None:
        print(f"  分析师数据不存在: data/{date_str}/analyst_forecast.json")
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

        total = buy + add + neutral + reduce_ + sale
        if org_num >= 3 and total > 0:
            consensus = (buy * 1.0 + add * 0.75 + neutral * 0.25 + reduce_ * 0.0 + sale * (-0.5)) / total
        else:
            consensus = 0.5

        if eps1 and abs(eps1) > 0.01:
            eps_growth = (eps2 - eps1) / abs(eps1)
            eps_growth = max(-0.5, min(2.0, eps_growth))
        else:
            eps_growth = 0.0

        analyst[code] = {
            "consensus": round(consensus, 3),
            "eps_growth": round(eps_growth, 3),
            "org_num": org_num,
        }
    print(f"  分析师: {len(analyst)} 条映射")
    return analyst


def load_dragon_tiger_data(date_str):
    """加载龙虎榜数据 → {code: {on_board, has_institution, is_main_buy}}"""
    rows = load_json(date_str, "dragon_tiger")
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
        has_inst = "机构" in str(explain)
        is_buy = "主买" in str(explain)
        dt_data[code] = {
            "on_board": True,
            "has_institution": has_inst,
            "is_main_buy": is_buy,
        }
    print(f"  龙虎榜: {len(dt_data)} 只上榜")
    return dt_data


def load_north_flow_data(date_str):
    """加载北向资金数据 → {net_north, masked}"""
    rows = load_json(date_str, "north_flow")
    if rows is None:
        print(f"  北向资金: 无数据")
        return {"net_north": 0, "masked": True}
    if not rows:
        return {"net_north": 0, "masked": True}
    latest = rows[0]
    net_north = latest.get("net_north", 0)
    if abs(net_north) > 500 or (net_north == 0 and latest.get("net_south", 0) == 0):
        print(f"  北向资金: 盘中数据暂不可用")
        return {"net_north": 0, "masked": True}
    print(f"  北向资金: 北向净流入 {net_north:+.1f}亿")
    return {"net_north": net_north, "masked": False}


def load_ratio_rank(date_str):
    """加载主力占比排名 → {code: percentile_score}"""
    rows = load_json(date_str, "rank_ratio")
    if rows is None:
        print(f"  占比排名: 无数据")
        return {}
    ratio_rank = {}
    total = len(rows)
    for i, r in enumerate(rows):
        code = r.get("f12", "")
        ratio_rank[code] = round(1.0 - i / total, 4)
    print(f"  占比排名: {len(ratio_rank)} 条映射")
    return ratio_rank


def load_stock_multiday(date_str):
    """从历史 fund_flow.json 计算个股 5日/10日累计 + 连续正流入天数"""
    d = datetime.strptime(date_str, "%Y%m%d")
    result = {}

    cursor = d - timedelta(days=1)
    days_found = 0
    attempts = 0
    while days_found < 10 and attempts < 60:
        attempts += 1
        prev_str = cursor.strftime("%Y%m%d")
        path = os.path.join(DATA_ROOT, prev_str, "fund_flow.json")
        cursor -= timedelta(days=1)
        if not os.path.exists(path):
            continue
        days_found += 1
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)
        for r in rows:
            code = r.get("f12", "")
            f62 = _to_float(r.get("f62"))
            if code not in result:
                result[code] = {"f62_5d": 0.0, "f62_10d": 0.0,
                                "pos_days_3d": 0, "pos_days_5d": 0,
                                "daily_f62": []}
            if days_found <= 5:
                result[code]["f62_5d"] += f62
                if days_found <= 3 and f62 > 0:
                    result[code]["pos_days_3d"] += 1
                if f62 > 0:
                    result[code]["pos_days_5d"] += 1
            result[code]["f62_10d"] += f62
            if days_found <= 10:
                result[code]["daily_f62"].append(f62)

    print(f"  个股多日累计: {len(result)} 只, 历史{days_found}天")
    return result


def load_past_closes(date_str, codes, days=60):
    """加载历史收盘价序列，用于计算相对位置和技术面"""
    price_history = defaultdict(list)
    d = datetime.strptime(date_str, "%Y%m%d")
    cursor = d - timedelta(days=1)
    attempts = 0
    while attempts < 80:
        attempts += 1
        cursor_str = cursor.strftime("%Y%m%d")
        rows = load_json(cursor_str, "fund_flow")
        cursor -= timedelta(days=1)
        if rows is None:
            continue
        for r in rows:
            code = r.get("f12", "")
            if code in codes:
                close = r.get("f2")
                if isinstance(close, (int, float)) and close > 0:
                    price_history[code].append(close)
    return price_history


# ============================================================
# 趋势检测 + 位置评分
# ============================================================

def _check_uptrend(price, closes, chg_today, vol_ratio):
    """硬过滤: 判断是否为右向（向上）趋势"""
    n = len(closes) if closes else 0

    if n >= 5:
        ma5 = sum(closes[:5]) / 5
        above_ma5 = price > ma5
        ma_ok = True
        if n >= 10:
            ma10 = sum(closes[:10]) / 10
            ma_ok = ma5 > ma10
        else:
            half = n // 2
            a = sum(closes[:half]) / half
            b = sum(closes[half:]) / (n - half)
            ma_ok = a > b
        chg_5d = (closes[0] - closes[4]) / closes[4] if closes[4] > 0 else 0
        reasons = []
        if not above_ma5:
            reasons.append("未站上MA5")
        if not ma_ok:
            reasons.append("均线未多头")
        if chg_5d < -0.08:
            reasons.append(f"近5日跌{chg_5d:.1%}")
        return (not reasons, "; ".join(reasons) if reasons else "")

    if n >= 2:
        mid = n // 2
        recent_avg = sum(closes[:mid]) / mid if mid > 0 else closes[0]
        older_avg = sum(closes[mid:]) / (n - mid) if n > mid else closes[-1]
        trending_up = recent_avg > older_avg
        if trending_up and chg_today > 0:
            return True, ""
        elif not trending_up:
            return False, "近N日未上行"
        else:
            return False, "今日未同步上涨"

    if chg_today > 0.5 and vol_ratio >= 1.2:
        return True, ""
    elif chg_today <= 0:
        return False, "当日未上涨"
    else:
        return False, f"量比不足({vol_ratio:.1f})"


def _calc_position_score(price, closes):
    """位置健康度: 0~1，中段最优"""
    if not closes or len(closes) < 5:
        return 0.5
    high_60 = max(closes[:60]) if len(closes) >= 20 else max(closes)
    low_60 = min(closes[:60]) if len(closes) >= 20 else min(closes)
    if high_60 <= low_60:
        return 0.5
    position = (price - low_60) / (high_60 - low_60)
    position = max(0.0, min(1.0, position))

    if position < 0.10:
        score = 0.15
    elif 0.10 <= position < 0.25:
        score = 0.40
    elif 0.25 <= position < 0.40:
        score = 0.70
    elif 0.40 <= position < 0.65:
        score = 0.85
    elif 0.65 <= position < 0.85:
        score = 0.50
    else:
        score = 0.20

    if len(closes) >= 5:
        ma5 = sum(closes[:5]) / 5
        if price > ma5:
            score = min(1.0, score + 0.05)
        if len(closes) >= 10:
            ma10 = sum(closes[:10]) / 10
            if ma5 > ma10:
                score = min(1.0, score + 0.05)
    return round(score, 3)


def _calc_short_trend(closes):
    """近5日短期趋势强度 (-1 ~ 1)"""
    if len(closes) < 5:
        return 0.0
    recent = closes[:5]
    if len(recent) < 2:
        return 0.0
    changes = [(recent[i] - recent[i+1]) / recent[i+1] for i in range(len(recent) - 1)]
    avg_chg = sum(changes) / len(changes)
    return max(-1.0, min(1.0, avg_chg * 50))


def _detect_oversold_bounce(closes, today_chg):
    """检测超跌反弹"""
    if not closes or len(closes) < 5:
        return False
    if today_chg > 3.0 and len(closes) >= 4:
        cum3 = (closes[0] - closes[3]) / closes[3] if closes[3] > 0 else 0
        if cum3 < -0.03:
            return True
    return False


# ============================================================
# 涨停/候选 分流
# ============================================================

def split_stocks(stocks, price_history):
    """将成分股分为: 涨停观察池 / 候选池 / 排除池"""
    limit_up_pool = []
    candidate_pool = []
    excluded_pool = []

    for s in stocks:
        code = s.get("f12", "")
        name = s.get("f14", "")
        chg = _to_float(s.get("f3"))
        price = _to_float(s.get("f2"))
        main_flow = _to_float(s.get("f62"))
        main_ratio = _to_float(s.get("f184"))
        turnover = _to_float(s.get("f8"))
        vol_ratio = _to_float(s.get("f10"))
        mcap = _to_float(s.get("f20"))

        if not is_main_board(code):
            continue

        # 涨停 → 观察池
        if chg >= LIMIT_UP_PCT:
            s["_pool"] = "limit_up_observe"
            s["_exclude_reason"] = ""
            limit_up_pool.append(s)
            continue

        # 基本面风控
        reasons = []
        if chg > CANDIDATE_MAX_CHG:
            reasons.append(f"涨跌幅 {chg:+.1f}% > {CANDIDATE_MAX_CHG}")
        if chg < -5.0:
            reasons.append(f"跌超5% ({chg:+.1f}%)")
        if price < MIN_PRICE:
            reasons.append(f"股价 {price:.2f} < {MIN_PRICE}")
        if price > MAX_PRICE:
            reasons.append(f"股价 {price:.2f} > {MAX_PRICE}")
        if main_flow < MIN_MAIN_FLOW and main_flow >= 0:
            reasons.append(f"主力净流入 {main_flow/1e4:.0f}万 < {MIN_MAIN_FLOW/1e4:.0f}万")
        if main_ratio < MIN_MAIN_RATIO and main_ratio >= 0:
            reasons.append(f"主力占比 {main_ratio:.1f}% < {MIN_MAIN_RATIO}%")
        if turnover > 0 and turnover < MIN_TURNOVER:
            reasons.append(f"换手率 {turnover:.1f}% < {MIN_TURNOVER}%")
        if turnover > MAX_TURNOVER:
            reasons.append(f"换手率 {turnover:.1f}% > {MAX_TURNOVER}%")
        if vol_ratio > 0 and vol_ratio < MIN_VOL_RATIO:
            reasons.append(f"量比 {vol_ratio:.2f} < {MIN_VOL_RATIO}")
        if mcap > 0:
            mcap_yi = mcap / 1e8
            if mcap_yi < MIN_MCAP_YI:
                reasons.append(f"市值 {mcap_yi:.1f}亿 < {MIN_MCAP_YI}亿")
            if mcap_yi > MAX_MCAP_YI:
                reasons.append(f"市值 {mcap_yi:.1f}亿 > {MAX_MCAP_YI}亿")
        if "ST" in str(name) or "*ST" in str(name):
            reasons.append("ST股")

        if reasons:
            s["_pool"] = "excluded"
            s["_exclude_reason"] = "; ".join(reasons)
            excluded_pool.append(s)
            continue

        # 右向趋势硬过滤
        closes = price_history.get(code, [])
        trend_ok, trend_detail = _check_uptrend(price, closes, chg, vol_ratio)
        if not trend_ok:
            s["_pool"] = "excluded"
            s["_exclude_reason"] = f"趋势不符({trend_detail})"
            excluded_pool.append(s)
            continue

        s["_pool"] = "candidate"
        s["_exclude_reason"] = ""
        candidate_pool.append(s)

    return limit_up_pool, candidate_pool, excluded_pool


# ============================================================
# 技术面评分
# ============================================================

def _calc_technical_score(price, closes):
    """计算技术面得分: MA多头排列 + 突破信号"""
    if not closes or len(closes) < 5:
        return 0.5, 0.5, False

    # MA 计算
    def ma(seq, n):
        return sum(seq[:n]) / n if len(seq) >= n else None

    ma5 = ma(closes, 5)
    ma10 = ma(closes, 10)
    ma20 = ma(closes, 20)

    # 均线多头排列得分
    align_score = 0.0
    if ma5 and ma10 and ma5 > ma10:
        align_score += 0.25
    if ma10 and ma20 and ma10 > ma20:
        align_score += 0.25
    if ma5 and ma20 and ma5 > ma20:
        align_score += 0.25
    if ma5 and price > ma5:
        align_score += 0.25

    # 突破检测: 收盘价是否接近或突破 20 日最高价
    breakout_20d = False
    if len(closes) >= 21:
        high_20d = max(closes[1:21])
        breakout_20d = price > high_20d * 0.98

    # 突破得分
    if len(closes) >= 60:
        high_60d = max(closes[1:61])
        if price > high_60d * 0.98:
            breakout_score = 1.0
        elif breakout_20d:
            breakout_score = 0.8
        else:
            breakout_score = 0.4
    elif breakout_20d:
        breakout_score = 0.8
    elif align_score >= 0.5:
        breakout_score = 0.6
    else:
        breakout_score = 0.3

    return round(align_score, 3), round(breakout_score, 3), breakout_20d


# ============================================================
# P16: 涨停基因
# ============================================================

def _check_limit_up_gene(code, date_str):
    """检查近30日涨停基因: 有涨停 + 缩量回调 = 强基因"""
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
            f3 = _to_float(r.get("f3"))
            if f3 >= 9.8:
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
                vol = _to_float(r.get("f8"))
                chg = _to_float(r.get("f3"))
                if vol > 0:
                    volume_after.append((vol, chg))
                break
    if volume_after:
        avg_vol = sum(v for v, _ in volume_after) / len(volume_after)
        max_drop = min(chg for _, chg in volume_after) if volume_after else 0
        if avg_vol < 10 and max_drop > -5:
            return 0.5  # 涨停+缩量回调=强基因
    return 0.2


# ============================================================
# P17: 市场情绪
# ============================================================

def _calc_market_sentiment(date_str):
    """评估市场情绪 → (bonus, label)"""
    rows = load_json(date_str, "fund_flow")
    if not rows or len(rows) < 1000:
        return 0.0, "unknown"

    limit_up_count = 0
    limit_down_count = 0
    total = len(rows)
    for r in rows:
        f3 = _to_float(r.get("f3"))
        if f3 >= 9.8:
            limit_up_count += 1
        elif f3 <= -9.8:
            limit_down_count += 1

    up_ratio = limit_up_count / total * 100
    down_ratio = limit_down_count / total * 100
    zha_ban_rate = down_ratio / max(up_ratio, 0.01)

    if up_ratio > 3.0 and zha_ban_rate < 0.3:
        sentiment, bonus = "高潮", -0.03
    elif up_ratio > 1.5 and zha_ban_rate < 0.5:
        sentiment, bonus = "发酵", 0.04
    elif up_ratio < 0.5 and down_ratio > 2.0:
        sentiment, bonus = "冰点", 0.06
    elif up_ratio > 1.0 and zha_ban_rate > 0.6:
        sentiment, bonus = "退潮", -0.05
    else:
        sentiment, bonus = "震荡", 0.0

    return bonus, sentiment


# ============================================================
# 市场环境检测
# ============================================================

def detect_market_regime(date_str):
    """检测牛/熊/震荡，返回 (regime, weights)"""
    # 优先使用 market_diagnosis
    try:
        from market_diagnosis import get_diagnosis
        diag = get_diagnosis(date_str)
        if diag:
            regime = diag["regime"]["regime"]
            if regime == "bull":
                return "bull", WEIGHTS_BULL
            elif regime == "bear":
                return "bear", WEIGHTS_BEAR
            else:
                return "range", WEIGHTS_BASE
    except Exception:
        pass

    # 回退: 自底向上检测
    rows = load_json(date_str, "fund_flow")
    if not rows:
        return "range", WEIGHTS_BASE

    chgs = [r.get("f3") for r in rows if isinstance(r.get("f3"), (int, float))]
    flows = [r.get("f62") for r in rows if isinstance(r.get("f62"), (int, float))]
    if not chgs:
        return "range", WEIGHTS_BASE

    up_ratio = sum(1 for c in chgs if c > 0) / len(chgs)
    chgs_sorted = sorted(chgs)
    median_ret = chgs_sorted[len(chgs_sorted) // 2]
    flow_positive_ratio = sum(1 for f in flows if f > 0) / len(flows) if flows else 0.5

    bull_score = sum([up_ratio > 0.55, median_ret > 0.3, flow_positive_ratio > 0.50])
    bear_score = sum([up_ratio < 0.35, median_ret < -0.5, flow_positive_ratio < 0.35])

    if bull_score >= 3:
        regime = "bull"
    elif bear_score >= 3:
        regime = "bear"
    else:
        regime = "range"

    print(f"  市场环境: {regime} (涨跌比{up_ratio:.0%}, 中位{median_ret:+.1f}%, 主力正{flow_positive_ratio:.0%})")

    if regime == "bull":
        return regime, WEIGHTS_BULL
    elif regime == "bear":
        return regime, WEIGHTS_BEAR
    return regime, WEIGHTS_BASE


# ============================================================
# 核心: 增强多因子评分
# ============================================================

# 同日多次选股追踪（P10 + P14 共用）
_stability_tracker = {}


def score_candidates_enhanced(candidates, price_history, sector_flows,
                              sector_freshness, sector_persistence,
                              stock_multiday, analyst_data, dt_data,
                              north_data, ratio_rank,
                              sentiment_bonus=0.0, date_str="",
                              afternoon=False):
    """
    多因子综合评分（板块+全数据源增强版）:

    维度一: 启动信号 (25%)  — 板块新鲜度 + 资金加速度 + 5日加速
    维度二: 主力资金 (20%)  — 净流入 + 占比 + 超大单质量
    维度三: 趋势确认 (15%)  — 量比 + 换手率 + 短期斜率
    维度四: 板块共振 (10%)  — 板块排名 + 持续性 + 概念板块
    维度五: 位置健康 (10%)  — 相对60日位置 + MA站上
    维度六: 分析师   (5%)   — 评级共识 + EPS增长
    维度七: 多日累计 (5%)   — 3/5/10日流入 + 连续性
    维度八: 技术面   (4%)   — MA多头 + 突破
    维度九: 龙虎榜   (3%)   — 上榜 + 机构买入
    维度十: 北向资金 (2%)   — 外资方向
    维度十一: 占比排名(1%)  — 全市场主力占比排名

    + P0-P27 调整因子
    """
    if not candidates:
        return []

    f62_vals = [_to_float(s.get("f62")) for s in candidates]
    f184_vals = [_to_float(s.get("f184")) for s in candidates]

    scored = []
    for s in candidates:
        code = s.get("f12", "")
        md = stock_multiday.get(code, {})
        f204_calc = md.get("f62_5d", 0.0)
        f205_calc = md.get("f62_10d", 0.0)
        f3 = _to_float(s.get("f3"))
        f62 = _to_float(s.get("f62"))
        f184 = _to_float(s.get("f184"))
        f66 = _to_float(s.get("f66"))
        f8 = _to_float(s.get("f8"))
        f10 = _to_float(s.get("f10"))
        price = _to_float(s.get("f2"))
        f15 = _to_float(s.get("f15"))
        f16 = _to_float(s.get("f16"))
        f17 = _to_float(s.get("f17"))
        f18 = _to_float(s.get("f18"))
        f87 = _to_float(s.get("f87"))
        f20 = _to_float(s.get("f20"))
        sector_code = s.get("_sector_code", "")
        closes = price_history.get(code, [])

        # ═══════════════════════════════════════════
        # 维度一: 启动信号 (25%)
        # ═══════════════════════════════════════════
        score_start = 0.0

        # 1.1 板块新鲜度 (10%)
        sect_fresh = sector_freshness.get(sector_code, 0.5)
        score_start += sect_fresh * 0.40

        # 1.2 个股资金加速度 (10%)
        if f62 > 0:
            total_5d = abs(f204_calc) + f62
            if total_5d > 0:
                today_ratio_5d = f62 / total_5d
                if 0.35 <= today_ratio_5d <= 0.55:
                    score_start += 0.35
                elif 0.25 <= today_ratio_5d < 0.35:
                    score_start += 0.25
                elif 0.55 < today_ratio_5d <= 0.70:
                    score_start += 0.20
                elif today_ratio_5d > 0.70:
                    score_start += 0.10
            else:
                score_start += 0.15
        else:
            score_start += 0.15

        # 1.3 5日加速 (5%)
        total_10d = abs(f205_calc) + f62 + abs(f204_calc)
        if total_10d > 0:
            ratio_5d_10d = abs(f62 + f204_calc) / total_10d
            if ratio_5d_10d > 0.50 and (f62 + f204_calc) > 0:
                score_start += 0.25

        score_start = max(0.1, min(1.0, score_start))

        # ═══════════════════════════════════════════
        # 维度二: 主力资金强度 (20%)
        # ═══════════════════════════════════════════
        s_flow = _pct_rank(f62_vals, f62)
        s_ratio = _pct_rank(f184_vals, f184)
        if f62 > 0:
            super_ratio = max(0.0, min(1.0, f66 / f62))
        else:
            super_ratio = 0.0
        score_capital_raw = s_flow * 0.40 + s_ratio * 0.35 + super_ratio * 0.25
        score_capital = min(0.85, score_capital_raw)

        # P1: 买卖比
        f164 = _to_float(s.get("f164"))
        f166 = _to_float(s.get("f166"))
        buy_sell_bonus = 0.0
        if f166 > 0 and f164 > 0:
            ratio = f164 / f166
            if ratio > 2.0:
                buy_sell_bonus = 0.05
            elif ratio < 1.2:
                buy_sell_bonus = -0.08

        # ═══════════════════════════════════════════
        # 维度三: 趋势确认 (15%)
        # ═══════════════════════════════════════════
        score_trend = 0.0
        if f10 >= 2.5:
            score_trend += 0.40
        elif f10 >= 1.5:
            score_trend += 0.30
        elif f10 >= 1.2:
            score_trend += 0.18
        if 5.0 <= f8 <= 15.0:
            score_trend += 0.20
        elif 3.0 <= f8 < 5.0:
            score_trend += 0.12
        short_trend = _calc_short_trend(closes)
        if short_trend > 0.02:
            score_trend += 0.15
        elif short_trend > 0:
            score_trend += 0.08
        if _detect_oversold_bounce(closes, f3):
            score_trend -= 0.25
        score_trend = max(0.0, min(1.0, score_trend))

        # ═══════════════════════════════════════════
        # 维度四: 板块共振 (10%)
        # ═══════════════════════════════════════════
        score_sector = sector_flows.get(sector_code, 0.5)
        if sector_persistence:
            persist = sector_persistence.get(sector_code, 0.7)
            score_sector = score_sector * persist

        # 概念板块叠加
        s_concept = 0.5
        try:
            concept_rows = load_json(date_str, "concept_flow")
            if concept_rows:
                concept_map = {}
                for r in concept_rows:
                    concept_map[r.get("f14", "")] = r.get("f62", 0) or 0
                all_concept_flows = list(concept_map.values())
                if all_concept_flows and max(all_concept_flows) > min(all_concept_flows):
                    concept_name = s.get("_sector_name", "")
                    if concept_name and concept_name in concept_map:
                        s_concept = _pct_rank(all_concept_flows, concept_map[concept_name])
        except Exception:
            pass
        score_sector = score_sector * 0.7 + s_concept * 0.3

        # ═══════════════════════════════════════════
        # 维度五: 位置健康 (10%)
        # ═══════════════════════════════════════════
        score_position = _calc_position_score(price, closes)

        # ═══════════════════════════════════════════
        # 维度六: 分析师共识 (5%)
        # ═══════════════════════════════════════════
        a = analyst_data.get(code, {})
        s_consensus = a.get("consensus", 0.5)
        eps_growth = a.get("eps_growth", 0.0)
        if eps_growth != 0:
            s_eps_growth = max(0.0, min(1.0, (eps_growth + 0.2) / 0.4))
        else:
            s_eps_growth = 0.5
        score_analyst = s_consensus * 0.65 + s_eps_growth * 0.35

        # ═══════════════════════════════════════════
        # 维度七: 多日累计 (5%)
        # ═══════════════════════════════════════════
        cum3 = f62 + md.get("f62_5d", 0)
        cum5 = f62 + md.get("f62_5d", 0)
        cum10 = f62 + md.get("f62_10d", 0)

        # 构建候选池的累计值用于 percentile rank
        cum3_vals = []
        cum5_vals = []
        cum10_vals = []
        for sc in candidates:
            c = sc.get("f12", "")
            h = stock_multiday.get(c, {})
            f62_c = _to_float(sc.get("f62"))
            cum3_vals.append(f62_c + h.get("f62_5d", 0))
            cum5_vals.append(f62_c + h.get("f62_5d", 0))
            cum10_vals.append(f62_c + h.get("f62_10d", 0))

        s_flow_3day = _pct_rank(cum3_vals, cum3)
        s_flow_5day = _pct_rank(cum5_vals, cum5)
        s_flow_10day = _pct_rank(cum10_vals, cum10)

        # 连续性: 近5日正流入天数占比
        daily_all = [f62] + md.get("daily_f62", [])[:4]
        positive_days = sum(1 for v in daily_all if v > 0)
        s_flow_consistency = positive_days / len(daily_all) if daily_all else 0.5

        score_multiday = (s_flow_3day * 0.30 + s_flow_5day * 0.25 +
                          s_flow_10day * 0.20 + s_flow_consistency * 0.25)

        # ═══════════════════════════════════════════
        # 维度八: 技术面 (4%)
        # ═══════════════════════════════════════════
        ma_align, breakout_score, breakout_20d = _calc_technical_score(price, closes)
        score_technical = ma_align * 0.5 + breakout_score * 0.5

        # ═══════════════════════════════════════════
        # 维度九: 龙虎榜 (3%)
        # ═══════════════════════════════════════════
        dt = dt_data.get(code, {})
        s_dragon_tiger = 0.5
        if dt.get("on_board"):
            s_dragon_tiger = 0.6
            if dt.get("has_institution"):
                s_dragon_tiger += 0.25
            if dt.get("is_main_buy"):
                s_dragon_tiger += 0.15
            s_dragon_tiger = min(1.0, s_dragon_tiger)

        # ═══════════════════════════════════════════
        # 维度十: 北向资金 (2%)
        # ═══════════════════════════════════════════
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

        # ═══════════════════════════════════════════
        # 维度十一: 主力占比排名 (1%)
        # ═══════════════════════════════════════════
        s_ratio_rank = ratio_rank.get(code, 0.5)

        # ═══════════════════════════════════════════
        # 综合加权
        # ═══════════════════════════════════════════
        total = (
            score_start      * WEIGHTS["start_signal"]
            + score_capital  * WEIGHTS["capital"]
            + score_trend    * WEIGHTS["trend"]
            + score_sector   * WEIGHTS["sector"]
            + score_position * WEIGHTS["position"]
            + score_analyst  * WEIGHTS["analyst"]
            + score_multiday * WEIGHTS["multiday"]
            + score_technical * WEIGHTS["technical"]
            + s_dragon_tiger * WEIGHTS["dragon_tiger"]
            + s_north_flow   * WEIGHTS["north_flow"]
            + s_ratio_rank   * WEIGHTS["ratio_rank"]
        )

        # ═══════════════════════════════════════════
        # P 因子调整
        # ═══════════════════════════════════════════

        # P0: 高资金低启动 = 一日游脉冲
        if score_capital > 0.75 and score_start < 0.45:
            total -= 0.12

        # P1: 买卖比
        total += buy_sell_bonus

        # P3: 资金连续性 — 近3天仅今日正流入
        pos_days = md.get("pos_days_3d", 0)
        if pos_days < 2:
            total -= 0.06

        # P4: 板块持续性已在 sector 维度中通过乘法体现，此处不重复

        # P5: 残差动量 — 个股独立于大盘的走势
        if closes and len(closes) >= 5:
            stock_chg_5d = (closes[0] - closes[4]) / closes[4] * 100 if closes[4] > 0 else 0
            market_chg_5d = _calc_market_median_chg(candidates, closes)
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

        # P10+P14+P15: 盘中追踪 — 重复衰减 + 稳定性 + 排名动量
        tracker = _stability_tracker.get(code, {})
        prev_ranks = tracker.get("ranks", [])
        appearances = len(prev_ranks)

        if prev_ranks:
            total -= 0.10

        if appearances >= 5:
            all_ranks = prev_ranks + [s.get("_rank", 99)]
            try:
                std = statistics.stdev(all_ranks)
                if std < 2.0:
                    total += 0.08
                elif std < 3.0:
                    total += 0.04
            except statistics.StatisticsError:
                pass
        elif appearances >= 3:
            all_ranks = prev_ranks + [s.get("_rank", 99)]
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

        # P13: 高涨幅透支渐变惩罚
        chg_floor = 4.0 if afternoon else 5.0
        if f3 > chg_floor and score_capital < 0.92:
            overextension = (f3 - chg_floor) / (10.0 - chg_floor)
            penalty = overextension * 0.25
            total -= min(0.25, penalty)
            if f3 > 7.0 and f184 > 15.0:
                total -= 0.05

        # P16: 涨停基因
        limit_up_gene = _check_limit_up_gene(code, date_str)
        if limit_up_gene >= 0.5:
            total += 0.05
        elif limit_up_gene >= 0.2:
            total += 0.02

        # P17: 情绪周期
        total += sentiment_bonus

        # P18+P19+P20: 三周期共振
        total_stocks = max(len(candidates), 1)
        rank_5d = _to_float(s.get("_rank_5d"))
        rank_10d = _to_float(s.get("_rank_10d"))
        pct_5d = rank_5d / total_stocks if rank_5d > 0 else 0.5
        pct_10d = rank_10d / total_stocks if rank_10d > 0 else 0.5
        pct_today = s.get("_rank", 99) / total_stocks

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

        # P22: 残差动量增强（10日独立涨幅）
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
        # 沉默吸筹: chg < 3% + 资金强
        if f3 < 3.0 and score_capital > 0.6:
            total += 0.05

        # 资金 vs 启动对立惩罚
        if score_capital > 0.80 and score_start > 0.70:
            total -= 0.08

        # ═══════════════════════════════════════════
        # 附加属性
        # ═══════════════════════════════════════════
        scored.append({
            **s,
            "_score": round(total, 4),
            "_score_start": round(score_start, 3),
            "_score_capital": round(score_capital, 3),
            "_score_trend": round(score_trend, 3),
            "_score_sector": round(score_sector, 3),
            "_score_position": round(score_position, 3),
            "_score_analyst": round(score_analyst, 3),
            "_score_multiday": round(score_multiday, 3),
            "_score_technical": round(score_technical, 3),
            "_s_dragon_tiger": round(s_dragon_tiger, 3),
            "_s_north_flow": round(s_north_flow, 3),
            "_s_ratio_rank": round(s_ratio_rank, 3),
            "_f62_5d": f204_calc,
            "_f62_10d": f205_calc,
            "_cum3": round(cum3 / 1e8, 2),
            "_cum5": round(cum5 / 1e8, 2),
            "_cum10": round(cum10 / 1e8, 2),
            "_ma_align": round(ma_align, 3),
            "_breakout_20d": breakout_20d,
            "_mcap_yi": round(mcap_yi, 1),
            "_analyst_num": a.get("org_num", 0),
            "_s_consensus": round(s_consensus, 3),
            "_s_eps_growth": round(s_eps_growth, 3),
            "_s_flow_consistency": round(s_flow_consistency, 3),
        })

    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored


def _calc_market_median_chg(candidates, closes):
    """用候选池平均近似大盘中位数涨跌"""
    if not candidates:
        return 0.0
    chgs = []
    for s in candidates[:20]:
        c = s.get("f12", "")
        f3 = _to_float(s.get("f3"))
        chgs.append(f3)
    if chgs:
        chgs.sort()
        return chgs[len(chgs) // 2]
    return 0.0


# ============================================================
# 输出
# ============================================================

def print_results(limit_up_pool, scored_candidates, excluded_pool,
                  top_n=10, regime="range", weights=None):
    """格式化输出精选结果"""
    date_str = datetime.now(BJS_TZ).strftime("%Y-%m-%d")
    regime_labels = {"bull": "🐂 牛市", "bear": "🐻 熊市", "range": "📊 震荡市"}
    regime_label = regime_labels.get(regime, regime)

    print(f"\n{'═' * 100}")
    print(f"  🔥 板块增强选股（主板 · 板块共振 · 全数据源 · 次日溢价）")
    print(f"  {date_str} | 市场: {regime_label} | 因子: 11维度 + P0-P27")
    print(f"{'═' * 100}")
    header = (f"  {'排名':<4} {'代码':<8} {'名称':<8} {'得分':<6} {'涨跌%':<7} "
              f"{'主力流入':<12} {'占比%':<6} {'启动':<6} {'资金':<6} "
              f"{'分析师':<6} {'龙虎':<4} {'北向':<4}")
    print(header)
    print(f"  {'─' * 95}")

    for i, s in enumerate(scored_candidates[:top_n], 1):
        code = s.get("f12", "")
        name = s.get("f14", "")
        score = s.get("_score", 0)
        chg = _to_float(s.get("f3"))
        f62 = _to_float(s.get("f62"))
        f184 = _to_float(s.get("f184"))
        start_s = s.get("_score_start", 0)
        capital_s = s.get("_score_capital", 0)
        analyst_s = s.get("_score_analyst", 0)
        dt_s = s.get("_s_dragon_tiger", 0)
        north_s = s.get("_s_north_flow", 0)

        dt_icon = "🐉" if dt_s > 0.6 else ("·" if dt_s > 0.5 else " ")
        north_icon = "🇳" if north_s > 0.6 else ("·" if north_s > 0.5 else " ")

        print(f"  {i:<4} {code:<8} {name:<8s} {score:.4f} "
              f"{chg:>+6.2f}% {_fmt_yi(f62):>12} {f184:>5.1f}% "
              f"{start_s:.2f}  {capital_s:.2f}  "
              f"{analyst_s:.2f}  {dt_icon:<4} {north_icon:<4}")

    if not scored_candidates:
        print(f"    无符合条件的候选股")

    # ── 涨停观察池 ──
    if limit_up_pool:
        print(f"\n  {'─' * 100}")
        print(f"  👀 涨停观察池（持续跟踪，等回调/开板机会）")
        print(f"  {'代码':<8} {'名称':<8} {'涨跌%':<7} {'主力流入':<12} {'主力占比%':<7} {'换手%':<7} {'封板力度':<8} {'行业'}")
        limit_up_sorted = sorted(limit_up_pool, key=lambda s: _to_float(s.get("f62")), reverse=True)
        for s in limit_up_sorted[:15]:
            code = s.get("f12", "")
            name = s.get("f14", "")
            chg = _to_float(s.get("f3"))
            f62 = _to_float(s.get("f62"))
            f184 = _to_float(s.get("f184"))
            f8 = _to_float(s.get("f8"))
            sector = s.get("_sector_name", "")
            seal = "强🔒" if f184 > 8 and f8 < 10 else ("中🔓" if f184 > 4 else "弱⚠")
            print(f"  {code:<8} {name:<8s} {chg:>+6.2f}% {_fmt_yi(f62):>12} "
                  f"{f184:>6.1f}% {f8:>6.1f}% {seal:<8} {sector}")

    # ── 统计 ──
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

    # 因子权重
    if weights:
        print(f"\n  📊 因子权重:")
        for k, v in weights.items():
            bar = "█" * int(v * 100)
            print(f"    {k:<16} {v*100:>4.1f}% {bar}")


def _gen_reasons(s):
    """根据评分生成选中理由"""
    reasons = []
    capital = s.get("_score_capital", 0)
    start = s.get("_score_start", 0)
    trend = s.get("_score_trend", 0)
    analyst = s.get("_score_analyst", 0)
    dt = s.get("_s_dragon_tiger", 0)
    f62 = _to_float(s.get("f62"))
    f184 = _to_float(s.get("f184"))
    f3 = _to_float(s.get("f3"))
    f10 = _to_float(s.get("f10"))
    sect_name = s.get("_sector_name", "")
    breakout = s.get("_breakout_20d", False)
    ma_align = s.get("_ma_align", 0)

    if start >= 0.70:
        reasons.append(f"板块刚启动信号强({sect_name})")
    elif start >= 0.50:
        reasons.append(f"资金温和放量({sect_name})")

    if capital >= 0.75:
        reasons.append(f"主力大幅介入(超大单{_to_float(s.get('f66'))/1e8:.1f}亿)")
    elif capital >= 0.55:
        reasons.append(f"主力持续流入({_fmt_yi(f62)})")

    if trend >= 0.75:
        reasons.append(f"放量趋势确立(量比{f10:.1f})")
    elif trend >= 0.55:
        reasons.append(f"量价配合上行(涨{f3:.1f}%)")

    if analyst >= 0.7:
        reasons.append(f"分析师强共识({s.get('_analyst_num', 0)}家覆盖)")

    if dt > 0.7:
        reasons.append("龙虎榜机构买入")

    if breakout:
        reasons.append("突破20日高点")

    if ma_align >= 0.75:
        reasons.append("均线多头排列")

    if not reasons:
        reasons.append("综合评分入选")
    return reasons[:3]


def save_results(scored, limit_up, date_str, top_n=10, weights=None, regime="range"):
    """保存筛选结果 JSON + CSV（双格式）"""
    date_dir = os.path.join(DATA_ROOT, date_str)
    picks_dir = os.path.join(date_dir, "picks")
    os.makedirs(picks_dir, exist_ok=True)

    ts = datetime.now().strftime("%H%M%S")

    output = {
        "date": date_str,
        "strategy": "板块增强选股（主板·板块共振·全数据源·次日溢价）",
        "regime": regime,
        "weights": weights or {},
        "candidates": [],
        "limit_up_observe": [],
    }

    for s in scored[:top_n]:
        output["candidates"].append({
            "rank": s.get("_rank", 0),
            "code": s.get("f12", ""),
            "name": s.get("f14", ""),
            "score": s.get("_score", 0),
            "chg_pct": round(_to_float(s.get("f3")), 2),
            "main_flow": _to_float(s.get("f62")),
            "main_ratio": round(_to_float(s.get("f184")), 1),
            "large_flow": _to_float(s.get("f72")),
            "price": round(_to_float(s.get("f2")), 2),
            "turnover": round(_to_float(s.get("f8")), 1),
            "vol_ratio": round(_to_float(s.get("f10")), 2),
            "mcap_yi": round(_to_float(s.get("f20")) / 1e8, 2),
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
            },
            "sector_code": s.get("_sector_code", ""),
            "sector_name": s.get("_sector_name", ""),
            "analyst_num": s.get("_analyst_num", 0),
            "breakout_20d": s.get("_breakout_20d", False),
            "ma_align": s.get("_ma_align", 0),
            "reasons": _gen_reasons(s),
        })

    for s in limit_up:
        output["limit_up_observe"].append({
            "code": s.get("f12", ""),
            "name": s.get("f14", ""),
            "chg_pct": round(_to_float(s.get("f3")), 2),
            "main_flow": _to_float(s.get("f62")),
            "main_ratio": round(_to_float(s.get("f184")), 1),
            "turnover": round(_to_float(s.get("f8")), 1),
            "mcap_yi": round(_to_float(s.get("f20")) / 1e8, 2),
            "sector_code": s.get("_sector_code", ""),
            "sector_name": s.get("_sector_name", ""),
        })

    # JSON 输出
    output["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    json_path = os.path.join(date_dir, "sector_enhanced_picks.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    picks_json = os.path.join(picks_dir, f"enhanced_picks_{ts}.json")
    with open(picks_json, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # CSV 输出
    _save_picks_csv(scored[:top_n], picks_dir, ts)
    _save_limit_csv(limit_up, picks_dir, ts)

    print(f"\n  ✓ JSON: {os.path.basename(picks_json)}")
    print(f"  ✓ JSON: sector_enhanced_picks.json")


# ── CSV 字段 ──

_ENHANCED_PICKS_FIELDS = [
    "排名", "代码", "名称", "综合得分",
    "涨跌幅", "最新价",
    "主力净流入", "主力占比",
    "5日主力净流入", "10日主力净流入",
    "超大单净流入", "大单净流入",
    "换手率", "量比", "总市值",
    "启动得分", "资金得分", "趋势得分", "板块得分", "位置得分",
    "分析师得分", "多日得分", "技术面得分", "龙虎榜得分", "北向得分", "占比排名得分",
    "分析师家数", "均线排列", "突破20日",
    "所属板块",
    "选中理由1", "选中理由2", "选中理由3",
]

_ENHANCED_LIMIT_FIELDS = [
    "代码", "名称",
    "涨跌幅", "最新价",
    "主力净流入", "主力占比",
    "5日主力净流入", "10日主力净流入",
    "超大单净流入", "大单净流入",
    "换手率", "量比", "总市值",
    "封板力度", "所属板块", "观察要点",
]


def _save_picks_csv(candidates, picks_dir, ts):
    path = os.path.join(picks_dir, f"enhanced_picks_{ts}.csv")
    rows = []
    for s in candidates:
        reasons = _gen_reasons(s)
        while len(reasons) < 3:
            reasons.append("")
        rows.append({
            "排名": s.get("_rank", ""),
            "代码": s.get("f12", ""),
            "名称": s.get("f14", ""),
            "综合得分": s.get("_score", ""),
            "涨跌幅": _to_float(s.get("f3")),
            "最新价": _to_float(s.get("f2")),
            "主力净流入": _to_float(s.get("f62")),
            "主力占比": _to_float(s.get("f184")),
            "5日主力净流入": s.get("_f62_5d", 0),
            "10日主力净流入": s.get("_f62_10d", 0),
            "超大单净流入": _to_float(s.get("f66")),
            "大单净流入": _to_float(s.get("f72")),
            "换手率": _to_float(s.get("f8")),
            "量比": _to_float(s.get("f10")),
            "总市值": _to_float(s.get("f20")),
            "启动得分": s.get("_score_start", ""),
            "资金得分": s.get("_score_capital", ""),
            "趋势得分": s.get("_score_trend", ""),
            "板块得分": s.get("_score_sector", ""),
            "位置得分": s.get("_score_position", ""),
            "分析师得分": s.get("_score_analyst", ""),
            "多日得分": s.get("_score_multiday", ""),
            "技术面得分": s.get("_score_technical", ""),
            "龙虎榜得分": s.get("_s_dragon_tiger", ""),
            "北向得分": s.get("_s_north_flow", ""),
            "占比排名得分": s.get("_s_ratio_rank", ""),
            "分析师家数": s.get("_analyst_num", ""),
            "均线排列": s.get("_ma_align", ""),
            "突破20日": "是" if s.get("_breakout_20d") else "",
            "所属板块": s.get("_sector_name", ""),
            "选中理由1": reasons[0],
            "选中理由2": reasons[1],
            "选中理由3": reasons[2],
        })
    _write_csv(path, _ENHANCED_PICKS_FIELDS, _ENHANCED_PICKS_FIELDS, rows)


def _save_limit_csv(limit_up, picks_dir, ts):
    path = os.path.join(picks_dir, f"enhanced_limit_up_{ts}.csv")
    rows = []
    for s in sorted(limit_up, key=lambda x: _to_float(x.get("f62")), reverse=True):
        f184 = _to_float(s.get("f184"))
        f72 = _to_float(s.get("f72"))
        f8 = _to_float(s.get("f8"))
        if f184 > 8 and f8 < 10:
            seal, note = "强", "封板坚决,关注次日高开"
        elif f184 > 4:
            seal, note = "中", "主力有分歧,等开板回踩"
        else:
            seal, note = "弱", "封板力度弱,谨慎追高"
        note += " | 大单净流入" if f72 > 0 else " | 大单流出,注意承接"
        rows.append({
            "代码": s.get("f12", ""),
            "名称": s.get("f14", ""),
            "涨跌幅": _to_float(s.get("f3")),
            "最新价": _to_float(s.get("f2")),
            "主力净流入": _to_float(s.get("f62")),
            "主力占比": f184,
            "5日主力净流入": s.get("_f62_5d", 0),
            "10日主力净流入": s.get("_f62_10d", 0),
            "超大单净流入": _to_float(s.get("f66")),
            "大单净流入": f72,
            "换手率": f8,
            "量比": _to_float(s.get("f10")),
            "总市值": _to_float(s.get("f20")),
            "封板力度": seal,
            "所属板块": s.get("_sector_name", ""),
            "观察要点": note,
        })
    _write_csv(path, _ENHANCED_LIMIT_FIELDS, _ENHANCED_LIMIT_FIELDS, rows)


def _write_csv(path, fields, headers, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row.get(k, "") for k in fields])


# ============================================================
# 程序化接口
# ============================================================

def get_enhanced_picks(date_str=None, top_sectors=5, top_picks=10):
    """程序化接口：返回精选结果 dict（供 pipeline/web_api 调用）"""
    global _stability_tracker
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    # ── 市场环境门控 ──
    try:
        from market_diagnosis import get_diagnosis
        diag = get_diagnosis(date_str)
        if diag:
            risk = diag.get("risks", {}).get("level", "low")
            if risk in ("high", "critical"):
                return {"error": f"市场风险过高({risk})，暂停选股", "date": date_str}
    except Exception:
        pass

    # ── 市场环境 → 权重 ──
    regime, weights = detect_market_regime(date_str)
    global WEIGHTS
    WEIGHTS = weights

    afternoon = datetime.now().hour >= 13

    # [1] 获取 Top N 行业板块
    print(f"\n[1/5] 获取 Top {top_sectors} 行业板块...")
    sector_codes = load_sector_top_codes(date_str, top_sectors)
    if not sector_codes:
        return {"error": "无板块数据", "date": date_str}

    # [2] 加载成分股 + 板块数据
    print(f"\n[2/5] 加载成分股 + 板块多日数据...")
    stocks = load_sector_stocks(sector_codes, date_str)
    if not stocks:
        return {"error": "无成分股数据", "date": date_str}

    sector_freshness, _, sector_persistence = load_sector_multiday(date_str)

    # 板块资金流强度
    sector_flows = {}
    for i, code in enumerate(sector_codes):
        sector_flows[code] = 0.5 + (top_sectors - i) * 0.1

    # [3] 加载全数据源
    print(f"\n[3/5] 加载全数据源（分析师/龙虎榜/北向/占比排名/多日历史）...")
    analyst_data = load_analyst_data(date_str)
    dt_data = load_dragon_tiger_data(date_str)
    north_data = load_north_flow_data(date_str)
    ratio_rank = load_ratio_rank(date_str)
    stock_multiday = load_stock_multiday(date_str)

    # [4] 加载历史价格 + 分流
    print(f"\n[4/5] 加载历史价格 + 选股分流...")
    codes_set = {s.get("f12", "") for s in stocks}
    price_history = load_past_closes(date_str, codes_set)
    limit_up, candidates, excluded = split_stocks(stocks, price_history)

    # [5] 评分
    print(f"\n[5/5] 多因子增强评分 ({len(candidates)} 候选)...")
    sentiment_bonus, sentiment_label = _calc_market_sentiment(date_str)
    print(f"  市场情绪: {sentiment_label} ({sentiment_bonus:+.2f})")
    print(f"  权重: {', '.join(f'{k}={v*100:.0f}%' for k, v in weights.items())}")

    scored = score_candidates_enhanced(
        candidates, price_history, sector_flows,
        sector_freshness, sector_persistence,
        stock_multiday, analyst_data, dt_data,
        north_data, ratio_rank,
        sentiment_bonus=sentiment_bonus, date_str=date_str,
        afternoon=afternoon,
    )

    # P10+P14: 记录排名历史
    now = datetime.now()
    for i, s in enumerate(scored):
        s["_rank"] = i + 1
        code = s.get("f12", "")
        if code:
            if code not in _stability_tracker:
                _stability_tracker[code] = {"ranks": [], "times": [], "first_hour": now.hour}
            _stability_tracker[code]["ranks"].append(i + 1)
            _stability_tracker[code]["times"].append(now.strftime("%H:%M"))

    # 构建返回
    picks = []
    for s in scored[:top_picks]:
        picks.append({
            "rank": s.get("_rank", 0),
            "code": s.get("f12", ""),
            "name": s.get("f14", ""),
            "score": s.get("_score", 0),
            "chg_pct": round(_to_float(s.get("f3")), 2),
            "price": round(_to_float(s.get("f2")), 2),
            "main_flow": _to_float(s.get("f62")),
            "main_ratio": round(_to_float(s.get("f184")), 1),
            "large_flow": _to_float(s.get("f72")),
            "turnover": round(_to_float(s.get("f8")), 1),
            "vol_ratio": round(_to_float(s.get("f10")), 2),
            "mcap_yi": round(_to_float(s.get("f20")) / 1e8, 2),
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
            },
            "sector_name": s.get("_sector_name", ""),
            "analyst_num": s.get("_analyst_num", 0),
            "breakout_20d": s.get("_breakout_20d", False),
            "reasons": _gen_reasons(s),
        })

    limit_up_list = []
    for s in sorted(limit_up, key=lambda x: _to_float(x.get("f62")), reverse=True):
        limit_up_list.append({
            "code": s.get("f12", ""),
            "name": s.get("f14", ""),
            "chg_pct": round(_to_float(s.get("f3")), 2),
            "main_flow": _to_float(s.get("f62")),
            "main_ratio": round(_to_float(s.get("f184")), 1),
            "turnover": round(_to_float(s.get("f8")), 1),
            "sector_name": s.get("_sector_name", ""),
        })

    return {
        "date": date_str,
        "regime": regime,
        "weights": {k: round(v, 3) for k, v in weights.items()},
        "top_sectors": sector_codes,
        "candidates_count": len(scored),
        "limit_up_count": len(limit_up),
        "excluded_count": len(excluded),
        "picks": picks,
        "limit_up": limit_up_list,
    }


def run(date_str=None, top_sectors=5, top_picks=10):
    """执行全流程（CLI 模式，含文件输出）"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    print(f"\n{'═' * 80}")
    print(f"  板块增强选股 [{date_str}]")
    print(f"  策略: 板块共振 + 全数据源 (分析师/龙虎榜/北向/占比排名/技术面)")
    print(f"{'═' * 80}")

    # ── 市场环境 ──
    regime, weights = detect_market_regime(date_str)
    global WEIGHTS
    WEIGHTS = weights

    afternoon = datetime.now().hour >= 13
    if afternoon:
        print(f"  下午模式: 高涨幅惩罚收紧")

    # [1] Top 板块
    print(f"\n[1/5] 获取 Top {top_sectors} 行业板块...")
    sector_codes = load_sector_top_codes(date_str, top_sectors)
    if not sector_codes:
        print("  ✗ 无板块数据, 请先运行 python fetch_data.py")
        return None

    # [2] 成分股 + 板块数据
    print(f"\n[2/5] 加载成分股 + 板块多日数据...")
    stocks = load_sector_stocks(sector_codes, date_str)
    if not stocks:
        print("  ✗ 无成分股数据")
        return None

    sector_freshness, _, sector_persistence = load_sector_multiday(date_str)
    sector_flows = {}
    for i, code in enumerate(sector_codes):
        sector_flows[code] = 0.5 + (top_sectors - i) * 0.1

    # [3] 全数据源
    print(f"\n[3/5] 加载全数据源...")
    analyst_data = load_analyst_data(date_str)
    dt_data = load_dragon_tiger_data(date_str)
    north_data = load_north_flow_data(date_str)
    ratio_rank = load_ratio_rank(date_str)
    stock_multiday = load_stock_multiday(date_str)

    # [4] 历史价格 + 分流
    print(f"\n[4/5] 加载历史价格 + 选股分流...")
    codes_set = {s.get("f12", "") for s in stocks}
    price_history = load_past_closes(date_str, codes_set)
    limit_up, candidates, excluded = split_stocks(stocks, price_history)

    # [5] 评分
    print(f"\n[5/5] 多因子增强评分 ({len(candidates)} 候选)...")
    sentiment_bonus, sentiment_label = _calc_market_sentiment(date_str)
    print(f"  市场情绪: {sentiment_label} ({sentiment_bonus:+.2f})")

    scored = score_candidates_enhanced(
        candidates, price_history, sector_flows,
        sector_freshness, sector_persistence,
        stock_multiday, analyst_data, dt_data,
        north_data, ratio_rank,
        sentiment_bonus=sentiment_bonus, date_str=date_str,
        afternoon=afternoon,
    )

    # P10+P14: 记录排名
    now = datetime.now()
    for i, s in enumerate(scored):
        s["_rank"] = i + 1
        code = s.get("f12", "")
        if code:
            if code not in _stability_tracker:
                _stability_tracker[code] = {"ranks": [], "times": [], "first_hour": now.hour}
            _stability_tracker[code]["ranks"].append(i + 1)
            _stability_tracker[code]["times"].append(now.strftime("%H:%M"))

    # 输出
    print_results(limit_up, scored, excluded, top_picks, regime, weights)

    # 逐只诊断（前5）
    _print_diagnosis(scored[:5])

    # 保存
    save_results(scored, limit_up, date_str, top_picks, weights, regime)

    return {"limit_up": limit_up, "scored": scored, "excluded": excluded}


def _print_diagnosis(top_stocks):
    """逐只诊断前 N 只"""
    if not top_stocks:
        return
    print(f"\n  📋 逐只诊断 (Top {len(top_stocks)}):\n")
    for i, s in enumerate(top_stocks):
        code = s.get("f12", "")
        name = s.get("f14", "")
        score = s.get("_score", 0)
        chg = _to_float(s.get("f3"))
        f62 = _to_float(s.get("f62"))
        f184 = _to_float(s.get("f184"))
        mcap = s.get("_mcap_yi", 0)
        sector = s.get("_sector_name", "")
        a_num = s.get("_analyst_num", 0)
        breakout = s.get("_breakout_20d", False)
        dt_s = s.get("_s_dragon_tiger", 0)

        signals = []
        risks = []

        # 信号
        if s.get("_score_start", 0) > 0.7:
            signals.append(f"板块刚启动信号强({s.get('_score_start', 0):.0%})")
        if s.get("_score_capital", 0) > 0.7:
            signals.append(f"主力资金强度高({s.get('_score_capital', 0):.0%})")
        if s.get("_score_analyst", 0) > 0.6:
            signals.append(f"分析师共识强({s.get('_score_analyst', 0):.0%}, {a_num}家)")
        if breakout:
            signals.append("突破20日高点")
        if s.get("_ma_align", 0) >= 0.5:
            signals.append(f"均线多头排列({s.get('_ma_align', 0):.0%})")
        if dt_s > 0.6:
            signals.append(f"龙虎榜上榜+机构买入({dt_s:.0%})")
        if s.get("_s_north_flow", 0) > 0.6:
            signals.append(f"北向资金净流入环境偏暖")
        if s.get("_score_multiday", 0) > 0.6:
            signals.append(f"多日持续流入({s.get('_score_multiday', 0):.0%})")
        if s.get("_cum3", 0) > 0:
            signals.append(f"3日累计净流入 {s.get('_cum3', 0):+.1f}亿")

        # 风险
        if chg > 8:
            risks.append(f"已近涨停({chg:.1f}%), 次日追板风险大")
        if _to_float(s.get("f8")) > 20:
            risks.append(f"换手率偏高({_to_float(s.get('f8')):.1f}%)")
        if s.get("_s_ratio_rank", 0) < 0.3:
            risks.append("主力占比排名偏低")
        if s.get("_score_analyst", 0) < 0.3 and a_num >= 3:
            risks.append(f"分析师评级偏谨慎({s.get('_score_analyst', 0):.0%})")

        print(f"  {i+1}. {code} {name} | {mcap:.0f}亿 | {sector} | 分析师{a_num}家 | 综合分 {score:.4f}")
        for sig in signals:
            print(f"     ✅ {sig}")
        for risk in risks:
            print(f"     ⚠️  {risk}")
        if not signals:
            print(f"     ➡️  综合信号多头排列")
        print()


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

    run(date_str=date_str, top_sectors=top_sectors, top_picks=top_picks)


if __name__ == "__main__":
    main()
