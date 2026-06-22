"""
步骤2: 收盘全市场评分 — 对所有股票打分，保存 scores.csv
使用 fund_flow 字段 + 价格历史，权重对齐 sector_screener
"""
import csv
import json
import os
import statistics
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta

BJS_TZ = timezone(timedelta(hours=8))
RESEARCH_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data"
)

# ── 评分权重（对齐 sector_screener 优化后权重） ──
WEIGHTS = {
    "capital": 0.19, "start_signal": 0.13, "trend": 0.10,
    "position": 0.07, "multiday": 0.06, "sector": 0.05,
    "analyst": 0.05, "technical": 0.05, "intra_sector": 0.04,
    "margin_net": 0.03, "flow_accel": 0.02,
}

# ── scores.csv 输出列 ──
SCORE_HEADERS = [
    "代码", "名称", "最新价", "综合得分",
    "资金得分", "趋势得分", "启动得分", "板块得分", "位置得分",
    "分析师得分", "多日得分", "技术面得分", "行业内得分",
    "融资得分", "加速度得分", "占比趋势得分",
    "涨跌幅", "换手率", "量比", "总市值", "触发信号",
]

# 腾讯K线 API (获取价格历史)
KLINE_URL = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _tof(val, default=0.0):
    if val is None or val == "" or val == "-": return default
    try: return float(val)
    except: return default


def _range_score(value, ideal_min, ideal_max, floor, ceil):
    if ideal_min <= value <= ideal_max: return 1.0
    if value < ideal_min:
        return max(0.0, (value - floor) / (ideal_min - floor)) if ideal_min > floor else 0.0
    return max(0.0, (ceil - value) / (ceil - ideal_max)) if ceil > ideal_max else 0.0


def _pct_rank(values, target):
    if not values or max(values) == min(values): return 0.5
    return sum(1 for v in values if v <= target) / len(values)


def _load_stock_prices(codes, max_stocks=200):
    """从腾讯K线获取价格历史（近60日），限制请求数"""
    prices = {}
    count = 0
    for code in codes:
        if count >= max_stocks: break
        mkt = "sh" if code.startswith("6") else "sz"
        url = f"{KLINE_URL}?param={mkt}{code},day,,,65,qfq"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            key = f"{mkt}{code}"
            if data.get("data") and data["data"].get(key) and data["data"][key].get("qfqday"):
                closes = [float(k[2]) for k in data["data"][key]["qfqday"]]
                if len(closes) >= 20:
                    prices[code] = closes
                    count += 1
        except:
            pass
    return prices


def _load_latest_fund_flow(date_str):
    """加载最新的盘中快照 CSV"""
    intraday_dir = os.path.join(RESEARCH_ROOT, date_str, "intraday")
    if not os.path.isdir(intraday_dir): return None

    files = sorted(
        [f for f in os.listdir(intraday_dir) if f.startswith("fund_flow_") and f.endswith(".csv")],
        reverse=True,
    )
    if not files: return None

    path = os.path.join(intraday_dir, files[0])
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def _load_sector_flows(date_str):
    """加载最新行业板块流，按名称索引"""
    intraday_dir = os.path.join(RESEARCH_ROOT, date_str, "intraday")
    if not os.path.isdir(intraday_dir): return {}, {}

    # 行业流 — 用"名称"列做 key（stock的f100是行业名称，非代码）
    ind_files = sorted(
        [f for f in os.listdir(intraday_dir) if f.startswith("industry_flow_") and f.endswith(".csv")],
        reverse=True,
    )
    sector_map = {}
    if ind_files:
        with open(os.path.join(intraday_dir, ind_files[0]), encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                name = r.get("名称", "")
                flow = _tof(r.get("主力净流入"))
                if name:
                    sector_map[name] = flow

    # 概念流
    con_files = sorted(
        [f for f in os.listdir(intraday_dir) if f.startswith("concept_flow_") and f.endswith(".csv")],
        reverse=True,
    )
    concept_map = {}
    if con_files:
        with open(os.path.join(intraday_dir, con_files[0]), encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                name = r.get("名称", "")
                flow = _tof(r.get("主力净流入"))
                if name:
                    concept_map[name] = flow

    return sector_map, concept_map


def score_all_stocks(date_str=None):
    """全市场评分，返回 [{...}, ...] + 保存 scores.csv"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    print(f"全市场评分 [{date_str}]")

    # 加载数据
    stocks = _load_latest_fund_flow(date_str)
    if not stocks:
        print("  ✗ 无盘中数据")
        return []

    sector_flows, concept_flows = _load_sector_flows(date_str)
    print(f"  采集: {len(stocks)} 只, {len(sector_flows)} 个行业板块")

    # ── 风控过滤 ──
    stocks = [s for s in stocks if _tof(s.get("最新价")) >= 4.0]
    stocks = [s for s in stocks if 30 <= _tof(s.get("总市值")) / 1e8 <= 2000]
    print(f"  过滤后: {len(stocks)} 只 (价格≥4, 市值30-2000亿)")

    # 预计算百分位数组 (过滤后)
    f62_vals = [_tof(s.get("主力净流入")) for s in stocks]
    f184_vals = [_tof(s.get("主力占比")) for s in stocks]
    f168_vals = [_tof(s.get("融资净买入")) for s in stocks]

    # 行业内排名: 按行业分组
    sector_groups = defaultdict(list)
    for s in stocks:
        sector_groups[s.get("行业", "其他")].append(_tof(s.get("主力净流入")))

    # ── 加载价格历史（前200只市值最大的） ──
    mcap_stocks = sorted(stocks, key=lambda s: -_tof(s.get("总市值")))
    top_codes = [s["代码"] for s in mcap_stocks[:200]]
    price_hist = _load_stock_prices(top_codes, 200)
    print(f"  价格历史: {len(price_hist)} 只")
    # ── 逐只评分 ──
    results = []
    for s in stocks:
        code = s.get("代码", "")
        name = s.get("名称", "")
        f2 = _tof(s.get("最新价"))
        f3 = _tof(s.get("涨跌幅"))
        f8_val = _tof(s.get("换手率"))
        f10_val = _tof(s.get("量比"))
        f20 = _tof(s.get("总市值"))
        f62_val = _tof(s.get("主力净流入"))
        f184_val = _tof(s.get("主力占比"))
        f66_val = _tof(s.get("超大单净流入"))
        f69_val = _tof(s.get("超大单占比"))
        f87_val = _tof(s.get("小单占比"))
        f168_val = _tof(s.get("融资净买入"))
        f165_val = _tof(s.get("5日主力占比"))
        f175_val = _tof(s.get("10日主力占比"))
        f164_val = _tof(s.get("5日主力净流入"))
        f174_val = _tof(s.get("10日主力净流入"))
        industry = s.get("行业", "")

        sub = {}
        signals = []

        # ── capital (19%) ──
        cap = _pct_rank(f62_vals, f62_val) * 0.60
        cap += _pct_rank(f184_vals, f184_val) * 0.25
        cap += (_pct_rank([_tof(x.get("超大单占比")) for x in stocks], f69_val)) * 0.15
        sub["capital"] = round(cap, 3)

        # ── start_signal (13%) — 简化版: 5日vs10日加速 ──
        if f164_val > 0 and f174_val > 0: ratio = min(3.0, f164_val / max(f174_val, 1))
        else: ratio = 0.5
        start = ratio / 3.0 * 0.50 + _pct_rank(f62_vals, f62_val) * 0.50
        sub["start_signal"] = round(start, 3)

        # ── trend (10%) ──
        tr = _range_score(f10_val, 1.5, 4.0, 0.8, 8.0) * 0.15
        tr += _range_score(f8_val, 5.0, 18.0, 2.0, 25.0) * 0.25
        tr += _range_score(f3, 2.5, 7.0, -2.0, 9.5) * 0.25
        # 中期动量 from price history
        closes = price_hist.get(code, [])
        if closes and len(closes) >= 20:
            ret_10d = (closes[0] - closes[9]) / closes[9] * 100 if closes[9] > 0 else 0
            ret_20d = (closes[0] - closes[19]) / closes[19] * 100 if closes[19] > 0 else 0
            tr += (_range_score(ret_10d, 3, 15, -5, 25) * 0.55 + _range_score(ret_20d, 5, 25, -5, 35) * 0.45) * 0.20
        else:
            tr += 0.5 * 0.20
        tr += 0.5 * 0.15  # short_trend default
        sub["trend"] = round(tr, 3)

        # ── sector (5%) ──
        if sector_flows and industry:
            all_sf = list(sector_flows.values())
            industry_flow = sector_flows.get(industry, 0)
            sec = _pct_rank(all_sf, industry_flow) if all_sf else 0.5
        else:
            sec = 0.5
        # 概念叠加: 简化处理
        if concept_flows:
            all_cf = list(concept_flows.values())
            sec = sec * 0.70 + 0.5 * 0.30  # 概念部分默认中性
        sub["sector"] = round(sec, 3)

        # ── position (7%) ──
        if closes and len(closes) >= 20:
            high_60 = max(closes[:60]) if len(closes) >= 60 else max(closes)
            low_60 = min(closes[:60]) if len(closes) >= 60 else min(closes)
            if high_60 > low_60:
                pos = (f2 - low_60) / (high_60 - low_60)
                if 0.10 <= pos < 0.25: p_score = 0.80
                elif pos < 0.10: p_score = 0.25
                elif 0.25 <= pos < 0.40: p_score = 0.55
                elif 0.40 <= pos < 0.65: p_score = 0.45
                elif 0.65 <= pos < 0.85: p_score = 0.35
                else: p_score = 0.15
            else:
                p_score = 0.5
        else:
            p_score = 0.5
        sub["position"] = round(p_score, 3)

        # ── analyst (5%) — 简化: 无数据给中性 ──
        sub["analyst"] = 0.5

        # ── multiday (6%) — 3d/5d/10d累计 ──
        md = _pct_rank(f62_vals, f62_val) * 0.40  # 今日作为代理
        if f164_val > 0: md += _pct_rank(f62_vals, f164_val) * 0.35
        else: md += 0.5 * 0.35
        if f174_val > 0: md += _pct_rank(f62_vals, f174_val) * 0.25
        else: md += 0.5 * 0.25
        sub["multiday"] = round(md, 3)

        # ── technical (5%) ──
        if closes and len(closes) >= 20:
            ma5 = sum(closes[:5]) / 5; ma10 = sum(closes[:10]) / 10; ma20 = sum(closes[:20]) / 20
            align = sum([ma5 > ma10, ma10 > ma20, ma5 > ma20, f2 > ma5]) / 4
            breakout = 0.4
            if len(closes) >= 60:
                high_60d = max(closes[1:61])
                if f2 > high_60d * 0.98: breakout = 1.0
            elif f2 > max(closes[1:21]) * 0.98: breakout = 0.8
            tech = align * 0.60 + breakout * 0.40
        else:
            tech = 0.5
        sub["technical"] = round(tech, 3)

        # ── intra_sector (4%) ──
        if industry in sector_groups:
            sub["intra_sector"] = round(_pct_rank(sector_groups[industry], f62_val), 3)
        else:
            sub["intra_sector"] = 0.5

        # ── margin_net (3%) ──
        sub["margin_net"] = round(_pct_rank(f168_vals, f168_val), 3)

        # ── flow_accel (2%) ──
        if f164_val > 0 and f174_val > 0 and abs(f174_val) > 1:
            accel = min(2.0, max(0.3, f164_val / max(abs(f174_val), 1)))
        else:
            accel = 1.0
        sub["flow_accel"] = round(_range_score(accel, 1.3, 2.5, 0.3, 4.0), 3)

        # ── 综合得分 ──
        total = sum(sub.get(k, 0.5) * WEIGHTS.get(k, 0) for k in WEIGHTS)
        # 剩余权重(analyst等)补 0.5*weight
        for k in WEIGHTS:
            if k not in sub:
                total += 0.5 * WEIGHTS[k]

        # ── P32: 占比趋势 ──
        ratio_score = 0.0
        if f165_val > 0 and f184_val > f165_val * 1.5 and f165_val > f175_val and 5 <= f184_val <= 12:
            ratio_score = 0.05
            signals.append("P32_ratio_accel")
        elif f184_val > 10 and f165_val < f184_val * 0.3:
            ratio_score = -0.05
            signals.append("P32_pump_risk")
        elif f184_val > 12 and f165_val > 6:
            ratio_score = -0.04
            signals.append("P32_extreme")
        sub["ratio_trend"] = round(ratio_score, 3)
        total += ratio_score

        # ── 风控过滤 ──
        mcap_yi = f20 / 1e8
        if f8_val > 13 and cap < 0.75: total -= 0.06; signals.append("P29_high_turnover")
        if mcap_yi < 30: total -= 0.05
        if f87_val > 30 and f3 < 3: total -= 0.08; signals.append("P6_retail")

        total = max(0.0, min(1.0, total))

        results.append({
            "代码": code, "名称": name, "最新价": f2, "综合得分": round(total, 4),
            "资金得分": sub.get("capital", 0.5), "趋势得分": sub.get("trend", 0.5),
            "启动得分": sub.get("start_signal", 0.5), "板块得分": sub.get("sector", 0.5),
            "位置得分": sub.get("position", 0.5), "分析师得分": sub.get("analyst", 0.5),
            "多日得分": sub.get("multiday", 0.5), "技术面得分": sub.get("technical", 0.5),
            "行业内得分": sub.get("intra_sector", 0.5), "融资得分": sub.get("margin_net", 0.5),
            "加速度得分": sub.get("flow_accel", 0.5), "占比趋势得分": ratio_score,
            "涨跌幅": f3, "换手率": f8_val, "量比": f10_val, "总市值": mcap_yi,
            "触发信号": ",".join(signals),
        })

    # ── 排序 + 保存 CSV ──
    results.sort(key=lambda x: -x["综合得分"])
    csv_path = os.path.join(RESEARCH_ROOT, date_str, "scores.csv")
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SCORE_HEADERS, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)

    # 统计
    top50 = results[:50]
    avg_score = statistics.mean(r["综合得分"] for r in results)
    print(f"  ✓ scores.csv ({len(results)} 只, 均分{avg_score:.3f}, "
          f"Top50均分{statistics.mean(r['综合得分'] for r in top50):.3f})")
    return results


if __name__ == "__main__":
    score_all_stocks()
