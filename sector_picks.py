"""
板块成分股精选 — 基于行业资金流 Top N 板块成分股，筛选主板次日溢价标的
策略定位: 板块共振 + 主力介入 + 低位待涨 + 排除涨停 + 涨停观察池

用法:
  python sector_picks.py                    默认读取今天数据
  python sector_picks.py --date=20260616    指定日期
  python sector_picks.py --sectors=5         取前 N 个行业板块
  python sector_picks.py --top=10            输出前 N 只精选股
"""
import sys
import os
import csv
import json
from datetime import datetime
from collections import defaultdict

from fetchers.base import DATA_ROOT, BJS_TZ, load_json, save_data

# ============================================================
# 选股约束
# ============================================================
MIN_PRICE       = 4.0       # 最低股价
MAX_PRICE       = 200.0     # 最高股价
LIMIT_UP_PCT    = 9.8       # 涨停阈值（主板10%留0.2%容差）
CANDIDATE_MAX_CHG = 9.5     # 候选股最高涨跌（剔除涨停）
MIN_MAIN_FLOW   = 3000_0000 # 最小主力净流入 3000万
MIN_MAIN_RATIO  = 1.0       # 最小主力占比 1%
MIN_TURNOVER    = 2.0       # 最小换手率
MAX_TURNOVER    = 25.0      # 最大换手率（排除异常放量）
MIN_VOL_RATIO   = 1.0       # 最小量比（放量确认）
MIN_MCAP_YI     = 30        # 最小市值 30亿
MAX_MCAP_YI     = 2000      # 最大市值 2000亿
MAX_POSITION_PCT = 60.0     # 相对位置上限（距近60日高点跌幅不超过此值则认为相对低位）

# 主板代码前缀
MAIN_BOARD_PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")


def is_main_board(code):
    return isinstance(code, str) and code.startswith(MAIN_BOARD_PREFIXES)


# ============================================================
# 数据加载
# ============================================================

def load_sector_stocks(date_str, sector_codes):
    """加载指定板块的成分股明细数据（兼容 sector_detail_{code}_{date}.json 和 _None 后缀）"""
    all_stocks = []
    seen = set()
    sector_names = _load_sector_names(date_str)  # BK代码→中文名映射

    # 优先从 sectors/ 子目录读取，取最新时间戳的文件
    sector_root = os.path.join(DATA_ROOT, date_str, "sectors")
    if not os.path.isdir(sector_root):
        sector_root = os.path.join(DATA_ROOT, date_str)

    for code in sector_codes:
        import glob
        # 查找所有匹配文件，按时间戳降序取最新
        patterns = [
            os.path.join(sector_root, f"sector_detail_{code}_*.json"),
            os.path.join(DATA_ROOT, date_str, f"sector_detail_{code}_*.json"),
        ]
        all_matches = []
        for pat in patterns:
            all_matches.extend(glob.glob(pat))
        # 去重 + 按修改时间降序
        all_matches = sorted(set(all_matches), key=os.path.getmtime, reverse=True)
        path = all_matches[0] if all_matches else None

        if path is None:
            print(f"  ⚠ 板块成分股文件不存在: sector_detail_{code}_*.json")
            continue
        with open(path, "r", encoding="utf-8") as f:
            stocks = json.load(f)
        for s in stocks:
            stock_code = s.get("f12", "")
            if stock_code and stock_code not in seen:
                seen.add(stock_code)
                s["_sector_code"] = code
                s["_sector_name"] = sector_names.get(code, code)
                all_stocks.append(s)
        print(f"  加载 {os.path.basename(path)}: {len(stocks)} 只成分股 ({sector_names.get(code, code)})")

    print(f"  去重后共计 {len(all_stocks)} 只个股（{len(sector_codes)} 个板块）")
    return all_stocks


def _load_sector_names(date_str):
    """从 industry_flow.json 加载 BK代码→板块名称 映射"""
    rows = load_json(date_str, "industry_flow")
    if rows:
        return {r.get("f12", ""): r.get("f14", "") for r in rows}
    # 回退: 从 sector_top5_detail.json
    summary_path = os.path.join(DATA_ROOT, date_str, "sector_top5_detail.json")
    if os.path.exists(summary_path):
        with open(summary_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {s["code"]: s["name"] for s in data.get("top_sectors", [])}
    return {}


def load_sector_top_codes(date_str, top_n=5):
    """从 sector_top5_detail.json 或 industry_flow.json 获取主力净流入 Top N 板块代码"""
    # 找最新的 sector_top5_detail_*.json
    import glob
    patterns = [
        os.path.join(DATA_ROOT, date_str, "sectors", "sector_top5_detail_*.json"),
        os.path.join(DATA_ROOT, date_str, "sector_top5_detail_*.json"),
        os.path.join(DATA_ROOT, date_str, "sectors", "sector_top5_detail.json"),
        os.path.join(DATA_ROOT, date_str, "sector_top5_detail.json"),
    ]
    all_matches = []
    for pat in patterns:
        all_matches.extend(glob.glob(pat))
    summary_path = sorted(all_matches, key=os.path.getmtime, reverse=True)[0] if all_matches else ""
    codes = []
    if summary_path and os.path.exists(summary_path):
        with open(summary_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for s in data.get("top_sectors", [])[:top_n]:
            codes.append(s["code"])
            print(f"  Top板块: {s['code']} {s['name']} 主力{s['sector_main_flow_yi']}亿")
    else:
        # 回退: 从 industry_flow.json 按 f62 排序取 top N
        rows = load_json(date_str, "industry_flow")
        if rows:
            sorted_rows = sorted(rows, key=lambda r: _to_float(r.get("f62")), reverse=True)
            for r in sorted_rows[:top_n]:
                code = r.get("f12", "")
                codes.append(code)
                print(f"  Top板块: {code} {r.get('f14','')} "
                      f"主力{_to_float(r.get('f62'))/1e8:.2f}亿")

    return codes


def load_past_closes(date_str, codes, days=60):
    """加载历史收盘价序列，用于计算相对位置"""
    from datetime import datetime as dt, timedelta
    price_history = defaultdict(list)

    d = dt.strptime(date_str, "%Y%m%d")
    cursor = d - timedelta(days=1)
    attempts = 0
    while len(price_history.get(next(iter(codes)), [])) < days and attempts < 80:
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


# ============================================================
# 右向趋势检测
# ============================================================

def _check_uptrend(price, closes, chg_today, vol_ratio):
    """硬过滤: 判断是否为右向（向上）趋势
    >=5日数据: MA5站上 + 均线多头 + 近5日非单边跌
    2-4日数据: 近N日累计上涨 + 今日放量
    <2日数据: 当日量价确认（涨>0.5% + 量比>1.2）
    """
    n = len(closes) if closes else 0

    # ── 充分数据: MA 分析 ──
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

    # ── 有限数据: 短期趋势 + 今日确认 ──
    if n >= 2:
        # 比较前半段 vs 后半段，判断方向
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

    # ── 极少数据: 当日量价确认 ──
    if chg_today > 0.5 and vol_ratio >= 1.2:
        return True, ""
    elif chg_today <= 0:
        return False, "当日未上涨"
    else:
        return False, f"量比不足({vol_ratio:.1f})"


# ============================================================
# 涨停 / 候选 分流
# ============================================================

def split_stocks(stocks, price_history):
    """
    将成分股分为三组:
      - limit_up:    涨停股 → 观察池
      - candidates:  主板 + 基本面OK + 右向趋势
      - excluded:    风控/趋势不符
    """
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

        # 涨停 → 观察池（不检查趋势）
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
# 候选股评分（次日溢价潜力）
# ============================================================

def load_stock_multiday(date_str):
    """从历史 fund_flow.json 计算每只个股的 5日/10日累计 + 连续正流入天数
    返回 {code: {f62_5d, f62_10d, pos_days_3d}}
    """
    from datetime import datetime as dt, timedelta
    d = dt.strptime(date_str, "%Y%m%d")
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
                result[code] = {"f62_5d": 0.0, "f62_10d": 0.0, "pos_days_3d": 0}
            if days_found <= 5:
                result[code]["f62_5d"] += f62
                if days_found <= 3 and f62 > 0:
                    result[code]["pos_days_3d"] += 1
            result[code]["f62_10d"] += f62

    print(f"  个股多日累计: {len(result)} 只, 历史{min(days_found, 10)}天")
    return result


def load_sector_multiday(date_str):
    """加载行业板块今日/5日/10日主力净流入，计算排名变化"""
    rows = load_json(date_str, "industry_flow")
    if not rows:
        return {}, {}

    # 按 f62 排名（今日）、f204 排名（5日累计）、f205 排名（10日累计）
    by_f62 = sorted(rows, key=lambda r: _to_float(r.get("f62")), reverse=True)
    by_f204 = sorted(rows, key=lambda r: _to_float(r.get("f204")), reverse=True)
    by_f205 = sorted(rows, key=lambda r: _to_float(r.get("f205")), reverse=True)

    rank_today = {r.get("f12"): i + 1 for i, r in enumerate(by_f62)}
    rank_5d = {r.get("f12"): i + 1 for i, r in enumerate(by_f204)}
    rank_10d = {r.get("f12"): i + 1 for i, r in enumerate(by_f205)}
    total = len(rows)

    # 板块启动信号 + 持续性
    sector_freshness = {}
    sector_persistence = {}
    for r in rows:
        code = r.get("f12", "")
        r_today = rank_today.get(code, total)
        r_5d = rank_5d.get(code, total)
        r_10d = rank_10d.get(code, total)
        # 提升幅度: 5日排名 - 今日排名（正值=今天排名上升）
        jump_5d = (r_5d - r_today) / total
        jump_10d = (r_10d - r_today) / total
        freshness = max(0.0, min(1.0, (jump_5d * 0.6 + jump_10d * 0.4 + 0.3)))
        sector_freshness[code] = round(freshness, 3)
        # P4: 持续性 — 5日排名也在前30% = 持续热，否则一日热
        persistence = 1.0 if r_5d <= total * 0.3 else (0.7 if r_5d <= total * 0.5 else 0.4)
        sector_persistence[code] = persistence

    return sector_freshness, rank_today, sector_persistence


def score_candidates(candidates, price_history, sector_flows,
                     sector_freshness, stock_multiday,
                     sector_persistence=None, afternoon=False):
    """
    评分维度（回溯优化版）:
      1. 启动信号 (35%): 板块刚启动 + 个股资金加速度 — 回溯最强预测因子
      2. 主力资金强度 (22%): 净流入+占比+超大单(cap 0.85防均值回归)
      3. 趋势确认 (20%): 量比+换手率+短期斜率+超跌检测
      4. 板块共振 (13%): 所属板块今日资金强度
      5. 位置健康 (10%): 不在超跌区也不在高位
      趋势确认仅用量比+换手率+短期斜率, f184 独占资金维度
      特殊调整: 沉默吸筹 +0.05 / 高涨幅透支渐变惩罚(5%起扣)
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
        f15 = _to_float(s.get("f15"))   # 最高价
        f16 = _to_float(s.get("f16"))   # 最低价
        f17 = _to_float(s.get("f17"))   # 开盘价
        f18 = _to_float(s.get("f18"))   # 昨收
        f84 = _to_float(s.get("f84"))   # 小单净流入
        f87 = _to_float(s.get("f87"))   # 小单占比
        f20 = _to_float(s.get("f20"))   # 总市值
        sector_code = s.get("_sector_code", "")
        closes = price_history.get(code, [])

        # ── 1. 启动信号 (35%, 回溯最强预测因子) ──
        score_start = 0.0

        # 板块刚启动
        sect_fresh = sector_freshness.get(sector_code, 0.5)
        score_start += sect_fresh * 0.40

        # 个股资金加速度
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

        # 5日加速
        total_10d = abs(f205_calc) + f62 + abs(f204_calc)
        if total_10d > 0:
            ratio_5d_10d = abs(f62 + f204_calc) / total_10d
            if ratio_5d_10d > 0.50 and (f62 + f204_calc) > 0:
                score_start += 0.25

        score_start = max(0.1, min(1.0, score_start))

        # ── 2. 主力资金强度 (30%) ──
        s_flow = _pct_rank(f62_vals, f62)
        s_ratio = _pct_rank(f184_vals, f184)
        if f62 > 0:
            super_ratio = max(0.0, min(1.0, f66 / f62))
        else:
            super_ratio = 0.0
        score_capital_raw = s_flow * 0.40 + s_ratio * 0.35 + super_ratio * 0.25
        score_capital = min(0.85, score_capital_raw)  # 极端值均值回归

        # ── P1: 买卖比 — 区分真买盘 vs 多空博弈 ──
        f164 = _to_float(s.get("f164"))  # 主力买入
        f166 = _to_float(s.get("f166"))  # 主力卖出
        buy_sell_bonus = 0.0
        if f166 > 0 and f164 > 0:
            ratio = f164 / f166
            if ratio > 2.0:
                buy_sell_bonus = 0.05   # 买入远超卖出，真买盘
            elif ratio < 1.2:
                buy_sell_bonus = -0.08  # 买卖接近，多空博弈非真趋势

        # ── 3. 趋势确认 (15%, 仅量价确认) ──
        score_trend = 0.0
        if f10 >= 2.5:
            score_trend += 0.40
        elif f10 >= 1.5:
            score_trend += 0.30
        elif f10 >= 1.2:
            score_trend += 0.18
        # 换手率质量: 5-15% 最佳（活跃但非过度换手）
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

        # ── 4. 板块共振 (10%) + P4 板块持续性 ──
        score_sector = sector_flows.get(sector_code, 0.5)
        # P4: 板块持续性 — 持续热 vs 一日热
        if sector_persistence:
            persist = sector_persistence.get(sector_code, 0.7)
            score_sector = score_sector * persist  # 一日热板块降权

        # ── 5. 位置健康 (10%) ──
        score_position = _calc_position_score(price, closes)

        # ── 综合（资金权重 30%→22%, 趋势 15%→20%, 板块 10%→13%）──
        total = (score_start * 0.35 + score_capital * 0.22 +
                 score_trend * 0.20 + score_sector * 0.13 +
                 score_position * 0.10)

        # P1: 买卖比调整
        total += buy_sell_bonus

        # ── 3.2 资金 vs 启动对立惩罚 ──
        # 资金极端(>0.80) + 启动刚启动(>0.70) = 矛盾信号
        if score_capital > 0.80 and score_start > 0.70:
            total -= 0.08

        # ── P10: 同股同日重复入选衰减 — 奥士康/泰晶/宸展均重复亏损 ──
        if code in _same_day_rankings:
            prev_rank = _same_day_rankings[code]
            if prev_rank > _same_day_rankings.get("_best_rank", 99):
                total -= 0.10  # 排位下降 = 信号减弱

        # ── P11: 高启动低资金 — 宸展光电start0.87 capital0.58均亏 ──
        if score_start > 0.80 and score_capital < 0.70:
            total -= 0.08

        # ── P12: 中涨跌弱资金 — 奥士康chg5.75% capital0.63次日-2.56% ──
        if f3 > 5.0 and score_capital < 0.70:
            total -= 0.06

        # ── P0: 高资金低启动 = 一日游脉冲(回溯4输家全命中此模式) ──
        if score_capital > 0.75 and score_start < 0.45:
            total -= 0.12

        # ── P3: 资金连续性 — 近3天仅今日正流入 = 一日游 ──
        pos_days = md.get("pos_days_3d", 0)
        if pos_days < 2:
            total -= 0.06

        # ── 特殊调整 ──
        # 沉默吸筹: chg < 3% + 资金强 → 主力悄悄进货
        if f3 < 3.0 and score_capital > 0.6:
            total += 0.05

        # 高涨幅透支: 渐变惩罚 (上午5%起扣, 下午4%起扣, 越接近涨停扣越多)
        chg_floor = 4.0 if afternoon else 5.0  # P13: 下午收紧
        if f3 > chg_floor and score_capital < 0.92:
            overextension = (f3 - chg_floor) / (10.0 - chg_floor)
            penalty = overextension * 0.25
            total -= min(0.25, penalty)
            # 超高超大单占比 + 高涨幅: 可能是出货
            if f3 > 7.0 and f184 > 15.0:
                total -= 0.05

        # ── P5: 残差动量 — 剔除大盘Beta后的独立走势 ──
        market_chg_5d = _calc_market_median_chg(closes)
        if price_history and len(closes) >= 5:
            stock_chg_5d = (closes[0] - closes[4]) / closes[4] * 100 if closes[4] > 0 else 0
            residual = stock_chg_5d - market_chg_5d
            if 2.0 < residual < 8.0:
                total += 0.05
            elif residual < -3.0:
                total -= 0.08

        # ── P6: 散户情绪反向 — 小单占比高但股价不涨 = 散户接盘 ──
        if f87 > 30 and f3 < 3:
            total -= 0.08
        elif f87 < 10 and f3 > 2:
            total += 0.04

        # ── P7: 开盘缺口 — 低开高走 = 主力介入 ──
        if f18 > 0 and f17 > 0:
            gap = (f17 - f18) / f18 * 100
            intraday = (price - f17) / f17 * 100 if f17 > 0 else 0
            if gap < -1 and intraday > 2 and f3 > 1:  # 低开>1% 且日内拉回>2%
                total += 0.05
            elif gap > 3 and intraday < 0:  # 高开>3% 但日内回落
                total -= 0.08

        # ── P8: 振幅洗盘 — 5-12%振幅+收阳+主力流入 = 洗盘吸筹 ──
        if f18 > 0 and f15 > 0 and f16 > 0:
            amplitude = (f15 - f16) / f18 * 100
            if 5 < amplitude < 12 and f3 > 1 and f62 > 0:
                total += 0.04

        # ── P9: 市值分组排名 — 按市值分三组内重排（消除大市值偏差）──
        mcap_yi = f20 / 1e8  # 已有 f20 提取
        if mcap_yi > 500:
            total += 0.02  # 大市值 + 已有信号 = 更可靠
        elif mcap_yi < 100:
            total -= 0.02  # 微小市值 + 流动性风险

        scored.append({
            **s,
            "_score": round(total, 4),
            "_score_start": round(score_start, 3),
            "_score_capital": round(score_capital, 3),
            "_score_trend": round(score_trend, 3),
            "_score_sector": round(score_sector, 3),
            "_score_position": round(score_position, 3),
            "_f62_5d": f204_calc,
            "_f62_10d": f205_calc,
        })

    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored


def _calc_market_median_chg(closes):
    """估算近5日市场中位数涨跌幅（用候选池平均替代）"""
    if not closes or len(closes) < 5:
        return 0.0
    # 简化: 用近5日每日涨跌的均值近似
    changes = [(closes[i] - closes[i+1]) / closes[i+1] * 100
               for i in range(min(5, len(closes) - 1))]
    return sum(changes) / len(changes) if changes else 0.0


def _detect_oversold_bounce(closes, today_chg):
    """检测是否为超跌反弹: 前几天大跌 + 今天大涨"""
    if not closes or len(closes) < 5:
        return False
    # 今天涨 > 3% 且近3日累计仍是跌的 → 可能为反弹
    if today_chg > 3.0 and len(closes) >= 4:
        cum3 = (closes[0] - closes[3]) / closes[3] if closes[3] > 0 else 0
        if cum3 < -0.03:
            return True
    return False


def _calc_position_score(price, closes):
    """
    位置健康度: 不在超跌区(0~0.15)也不在高位(>0.85)
    最佳位置是趋势中段 (0.30~0.70)，既非深跌反弹也非高位接盘
    """
    if not closes or len(closes) < 5:
        return 0.5

    high_60 = max(closes[:60]) if len(closes) >= 20 else max(closes)
    low_60 = min(closes[:60]) if len(closes) >= 20 else min(closes)

    if high_60 <= low_60:
        return 0.5

    position = (price - low_60) / (high_60 - low_60)
    position = max(0.0, min(1.0, position))

    # 理想: 趋势中段 (0.30-0.70)，非超跌反弹也非高位追涨
    if position < 0.10:
        score = 0.15   # 深跌区 → 超跌反弹嫌疑，低分
    elif 0.10 <= position < 0.25:
        score = 0.40   # 偏低，可能还在筑底
    elif 0.25 <= position < 0.40:
        score = 0.70   # 中低位，起涨确认
    elif 0.40 <= position < 0.65:
        score = 0.85   # 趋势中段（主力介入 + 趋势向上叠加后最优）
    elif 0.65 <= position < 0.85:
        score = 0.50   # 中高位，注意风险
    else:
        score = 0.20   # 高位，不追

    # 短期均线确认
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
    """计算近5日短期趋势强度 (-1 ~ 1)"""
    if len(closes) < 5:
        return 0.0
    recent = closes[:5]
    if len(recent) < 2:
        return 0.0
    # 简单斜率
    changes = [(recent[i] - recent[i+1]) / recent[i+1] for i in range(len(recent) - 1)]
    avg_chg = sum(changes) / len(changes)
    trend = max(-1.0, min(1.0, avg_chg * 50))  # 缩放
    return trend


# ============================================================
# 输出
# ============================================================

def _fmt_yi(v):
    if abs(v) >= 1e8:
        return f"{v/1e8:+.2f}亿"
    return f"{v/1e4:+.0f}万"


def print_results(limit_up_pool, scored_candidates, excluded_pool, top_n=10):
    """格式化输出精选结果"""
    date_str = datetime.now(BJS_TZ).strftime("%Y-%m-%d")

    # ── 精选候选 ──
    print(f"\n{'═' * 90}")
    print(f"  🔥 板块成分股精选（主板 · 次日溢价 · 非涨停）")
    print(f"  {date_str}")
    print(f"{'═' * 90}")
    print(f"  {'排名':<4} {'代码':<8} {'名称':<8} {'得分':<6} {'涨跌%':<7} {'主力流入':<12} {'占比%':<6} {'启动':<6} {'资金':<6} {'板块':<6}")
    print(f"  {'─' * 85}")

    for i, s in enumerate(scored_candidates[:top_n], 1):
        code = s.get("f12", "")
        name = s.get("f14", "")
        score = s.get("_score", 0)
        chg = _to_float(s.get("f3"))
        f62 = _to_float(s.get("f62"))
        f184 = _to_float(s.get("f184"))
        start_s = s.get("_score_start", 0)
        capital_s = s.get("_score_capital", 0)
        sector_s = s.get("_score_sector", 0)

        start_icon = "🔥" if start_s > 0.6 else ("✨" if start_s > 0.4 else "  ")

        print(f"  {i:<4} {code:<8} {name:<8s} {score:.4f} "
              f"{chg:>+6.2f}% {_fmt_yi(f62):>12} {f184:>5.1f}% "
              f"{start_s:.2f}{start_icon:<4} {capital_s:.2f} {sector_s:.3f}")

    if not scored_candidates:
        print(f"    无符合条件的候选股")

    # ── 涨停观察池 ──
    if limit_up_pool:
        print(f"\n  {'─' * 90}")
        print(f"  👀 涨停观察池（持续跟踪，等回调/开板机会）")
        print(f"  {'代码':<8} {'名称':<8} {'涨跌%':<7} {'主力流入':<12} {'主力占比%':<7} {'换手%':<7} {'封板力度':<8} {'行业'}")
        print(f"  {'─' * 55}")
        limit_up_sorted = sorted(limit_up_pool, key=lambda s: _to_float(s.get("f62")), reverse=True)
        for s in limit_up_sorted[:15]:
            code = s.get("f12", "")
            name = s.get("f14", "")
            chg = _to_float(s.get("f3"))
            f62 = _to_float(s.get("f62"))
            f184 = _to_float(s.get("f184"))
            f8 = _to_float(s.get("f8"))
            sector = s.get("_sector_name", "")
            # 封板力度: 主力占比高 + 换手率低 = 封板强
            seal = "强🔒" if f184 > 8 and f8 < 10 else ("中🔓" if f184 > 4 else "弱⚠")
            print(f"  {code:<8} {name:<8s} {chg:>+6.2f}% {_fmt_yi(f62):>12} "
                  f"{f184:>6.1f}% {f8:>6.1f}% {seal:<8} {sector}")

    # ── 统计 ──
    print(f"\n  {'─' * 90}")
    print(f"  候选池: {len(scored_candidates)} 只 | 涨停观察: {len(limit_up_pool)} 只 | 排除: {len(excluded_pool)} 只")
    print(f"  主板总计: {len(scored_candidates) + len(limit_up_pool)} 只（已过滤创业板/科创板）")

    # 排除原因分布
    if excluded_pool:
        reason_counts = defaultdict(int)
        for s in excluded_pool:
            for r in s.get("_exclude_reason", "").split("; "):
                if r:
                    reason_counts[r.split("(")[0].strip()] += 1
        print(f"  主要排除原因:")
        for reason, cnt in sorted(reason_counts.items(), key=lambda x: -x[1])[:5]:
            print(f"    - {reason}: {cnt}只")


def _pos_bar(score):
    """位置分数可视化"""
    if score >= 0.8:
        return " ▌"  # 低位
    elif score >= 0.5:
        return " ▐"  # 中低位
    elif score >= 0.3:
        return " ░"  # 中高位
    return " ·"       # 高位


def save_results(scored, limit_up, date_str, top_n=10):
    """保存筛选结果 JSON + CSV（双格式）"""
    date_dir = os.path.join(DATA_ROOT, date_str)
    picks_dir = os.path.join(date_dir, "picks")
    os.makedirs(picks_dir, exist_ok=True)

    output = {
        "date": date_str,
        "strategy": "板块成分股精选（主板·次日溢价·非涨停）",
        "candidates": [],
        "limit_up_observe": [],
    }

    for s in scored[:top_n]:
        output["candidates"].append({
            "rank": s.get("_rank", 0),
            "code": s.get("f12", ""),
            "name": s.get("f14", ""),
            "score": s.get("_score", 0),
            "chg_pct": _to_float(s.get("f3")),
            "main_flow": _to_float(s.get("f62")),
            "main_ratio": _to_float(s.get("f184")),
            "large_flow": _to_float(s.get("f72")),
            "price": _to_float(s.get("f2")),
            "turnover": _to_float(s.get("f8")),
            "vol_ratio": _to_float(s.get("f10")),
            "mcap_yi": round(_to_float(s.get("f20")) / 1e8, 2),
            "position_score": s.get("_score_position", 0),
            "trend_score": s.get("_score_trend", 0),
            "sector_code": s.get("_sector_code", ""),
            "sector_name": s.get("_sector_name", ""),
        })

    for s in limit_up:
        output["limit_up_observe"].append({
            "code": s.get("f12", ""),
            "name": s.get("f14", ""),
            "chg_pct": _to_float(s.get("f3")),
            "main_flow": _to_float(s.get("f62")),
            "main_ratio": _to_float(s.get("f184")),
            "turnover": _to_float(s.get("f8")),
            "sector_code": s.get("_sector_code", ""),
            "sector_name": s.get("_sector_name", ""),
        })

    json_path = os.path.join(date_dir, "sector_picks.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # ── 输出（picks/ 子目录，带时间戳）──
    ts = datetime.now().strftime("%H%M%S")
    picks_csv = os.path.join(picks_dir, f"picks_{ts}.csv")
    limit_csv = os.path.join(picks_dir, f"limit_up_{ts}.csv")
    picks_json = os.path.join(picks_dir, f"filter_{ts}.json")

    _save_picks_csv(scored[:top_n], picks_csv)
    _save_limit_up_csv(limit_up, limit_csv)

    # JSON 也带时间戳
    output["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(picks_json, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  ✓ JSON: {os.path.basename(picks_json)}")
    print(f"  ✓ CSV:  {os.path.basename(picks_csv)}")
    print(f"  ✓ CSV:  {os.path.basename(limit_csv)}")


# ── CSV 输出（调用 base.save_data，与 industry_flow.csv 完全一致的生成方式）──

_PICKS_CSV_FIELDS = [
    "排名", "代码", "名称", "综合得分",
    "涨跌幅", "最新价",
    "主力净流入", "主力占比",
    "5日主力净流入", "10日主力净流入",
    "超大单净流入", "大单净流入",
    "换手率", "量比", "总市值",
    "资金得分", "趋势得分", "启动得分", "板块得分",
    "所属板块",
    "选中理由1", "选中理由2", "选中理由3",
]

_LIMIT_UP_CSV_FIELDS = [
    "代码", "名称",
    "涨跌幅", "最新价",
    "主力净流入", "主力占比",
    "5日主力净流入", "10日主力净流入",
    "超大单净流入", "大单净流入",
    "换手率", "量比", "总市值",
    "封板力度", "所属板块", "观察要点",
]


def _save_picks_csv(candidates, path):
    rows = []
    for s in candidates:
        reasons = _gen_reasons_list(s)
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
            "资金得分": s.get("_score_capital", ""),
            "趋势得分": s.get("_score_trend", ""),
            "启动得分": s.get("_score_start", ""),
            "板块得分": s.get("_score_sector", ""),
            "所属板块": s.get("_sector_name", ""),
            "选中理由1": reasons[0],
            "选中理由2": reasons[1],
            "选中理由3": reasons[2],
        })
    _write_csv(path, _PICKS_CSV_FIELDS, _PICKS_CSV_FIELDS, rows)


def _save_limit_up_csv(limit_up, path):
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
            "5日主力净流入": _to_float(s.get("f204")),
            "10日主力净流入": _to_float(s.get("f205")),
            "超大单净流入": _to_float(s.get("f66")),
            "大单净流入": f72,
            "换手率": f8,
            "量比": _to_float(s.get("f10")),
            "总市值": _to_float(s.get("f20")),
            "封板力度": seal,
            "所属板块": s.get("_sector_name", ""),
            "观察要点": note,
        })
    _write_csv(path, _LIMIT_UP_CSV_FIELDS, _LIMIT_UP_CSV_FIELDS, rows)


def _write_csv(path, fields, headers, rows):
    """与 base.save_data 完全一致的 CSV 写入方式"""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row.get(k, "") for k in fields])


def _gen_reasons_list(s):
    """根据评分生成理由（主力介入 + 趋势向上 + 刚启动）"""
    reasons = []
    capital = s.get("_score_capital", 0)
    trend = s.get("_score_trend", 0)
    start = s.get("_score_start", 0)
    sector = s.get("_score_sector", 0)
    f62 = _to_float(s.get("f62"))
    f184 = _to_float(s.get("f184"))
    f66 = _to_float(s.get("f66"))
    f204_calc = s.get("_f62_5d", 0)
    f3 = _to_float(s.get("f3"))
    f10 = _to_float(s.get("f10"))
    sect_name = s.get("_sector_name", "")

    # 主力介入
    if capital >= 0.75:
        super_yi = f66 / 1e8
        reasons.append(f"主力大幅介入(超大单{super_yi:.1f}亿)" if super_yi > 0.5
                       else f"主力资金强势({_fmt_yi(f62)})")
    elif capital >= 0.55:
        reasons.append(f"主力持续流入({_fmt_yi(f62)})")

    # 启动信号
    if start >= 0.70:
        # 判断是板块刚启动还是个股加速
        today_pct = f62 / (abs(f204_calc) + f62) if (abs(f204_calc) + f62) > 0 else 0
        if today_pct > 0.40:
            reasons.append(f"资金加速涌入(今日占5日{today_pct:.0%})")
        else:
            reasons.append(f"板块刚启动({sect_name})")
    elif start >= 0.50:
        reasons.append(f"资金温和放量({sect_name})")

    # 趋势向上
    if trend >= 0.75:
        reasons.append(f"放量趋势确立(量比{f10:.1f} 涨{f3:.1f}%)")
    elif trend >= 0.55:
        reasons.append(f"量价配合上行(涨{f3:.1f}% 占比{f184:.0f}%)")
    elif trend >= 0.40:
        reasons.append(f"温和启动(涨{f3:.1f}%)")

    if not reasons:
        reasons.append("综合评分入选")
    return reasons[:3]


# ============================================================
# 主流程
# ============================================================

# 同日多次选股的排名追踪（P10用）
_same_day_rankings = {}


def get_sector_picks(date_str=None, top_sectors=5, top_picks=10):
    """程序化接口：返回精选结果 dict（供 pipeline/web_api 调用）"""
    global _same_day_rankings
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    # ── P13: 下午自动收紧 ──
    hour = datetime.now().hour
    afternoon_mode = hour >= 13

    # ── P2: 市场环境门控 ──
    try:
        from market_diagnosis import get_diagnosis
        diag = get_diagnosis(date_str)
        if diag:
            risk = diag.get("risks", {}).get("level", "low")
            if risk in ("high", "critical"):
                return {"error": f"市场风险过高({risk})，暂停选股", "date": date_str}
    except Exception:
        pass  # diagnosis 不可用时跳过门控

    sector_codes = load_sector_top_codes(date_str, top_sectors)
    if not sector_codes:
        return {"error": "无板块数据", "date": date_str}

    stocks = load_sector_stocks(date_str, sector_codes)
    if not stocks:
        return {"error": "无成分股数据", "date": date_str}

    codes_set = {s.get("f12", "") for s in stocks}
    price_history = load_past_closes(date_str, codes_set)
    limit_up, candidates, excluded = split_stocks(stocks, price_history)

    sector_flows = {}
    for i, code in enumerate(sector_codes):
        sector_flows[code] = 0.5 + (top_sectors - i) * 0.1

    sector_freshness, _, sector_persistence = load_sector_multiday(date_str)
    stock_multiday = load_stock_multiday(date_str)
    scored = score_candidates(candidates, price_history, sector_flows,
                              sector_freshness, stock_multiday,
                              sector_persistence, afternoon=afternoon_mode)
    # P10: 记录本次排名用于后续衰减
    for i, s in enumerate(scored):
        s["_rank"] = i + 1
        code = s.get("f12", "")
        if code:
            _same_day_rankings[code] = i + 1
    _same_day_rankings["_best_rank"] = min(_same_day_rankings.values()) if _same_day_rankings else 99

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
            "score_start": s.get("_score_start", 0),
            "score_capital": s.get("_score_capital", 0),
            "score_trend": s.get("_score_trend", 0),
            "score_sector": s.get("_score_sector", 0),
            "sector_name": s.get("_sector_name", ""),
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
        "top_sectors": top_sectors,
        "candidates_count": len(scored),
        "limit_up_count": len(limit_up),
        "excluded_count": len(excluded),
        "picks": picks,
        "limit_up": limit_up_list,
    }


def run(date_str=None, top_sectors=5, top_picks=10):
    """执行板块成分股精选全流程（CLI 模式，含文件输出）"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    print(f"\n{'═' * 70}")
    print(f"  板块成分股精选 [{date_str}]")
    print(f"  策略: 主板 · 启动(35%) · 主力(22%) · 趋势(20%) · 板块(13%) · 位置(10%)")
    print(f"{'═' * 70}\n")

    # 1. 获取 Top N 行业板块代码
    print(f"[1/4] 获取 Top {top_sectors} 行业板块...")
    sector_codes = load_sector_top_codes(date_str, top_sectors)
    if not sector_codes:
        print("  ✗ 无板块数据, 请先运行 python fetch_data.py")
        return None

    # 2. 加载成分股明细
    print(f"\n[2/4] 加载成分股明细...")
    stocks = load_sector_stocks(date_str, sector_codes)
    if not stocks:
        print("  ✗ 无成分股数据, 请先运行 python fetchers/sector_flow.py")
        return None

    # 3. 加载历史价格
    print(f"\n[3/4] 加载历史价格数据...")
    codes_set = {s.get("f12", "") for s in stocks}
    price_history = load_past_closes(date_str, codes_set)

    # 4. 涨停分流 + 候选精选
    print(f"\n[4/4] 选股分流 (涨停→观察 / 候选→评分)...")
    limit_up, candidates, excluded = split_stocks(stocks, price_history)

    # 板块资金流强度（用于板块共振评分）
    sector_flows = {}
    for i, code in enumerate(sector_codes):
        sector_flows[code] = 0.5 + (top_sectors - i) * 0.1  # 排名越前板块分越高

    # 启动信号数据
    sector_freshness, _, sector_persistence = load_sector_multiday(date_str)
    stock_multiday = load_stock_multiday(date_str)

    scored = score_candidates(candidates, price_history, sector_flows,
                              sector_freshness, stock_multiday,
                              sector_persistence,
                              afternoon=datetime.now().hour >= 13)
    for i, s in enumerate(scored):
        s["_rank"] = i + 1

    # 输出
    print_results(limit_up, scored, excluded, top_picks)

    # 保存
    save_results(scored, limit_up, date_str, top_picks)

    return {"limit_up": limit_up, "scored": scored, "excluded": excluded}


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
