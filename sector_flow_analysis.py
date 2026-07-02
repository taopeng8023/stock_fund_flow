#!/usr/bin/env python
"""
板块资金流趋势分析 + 主力潜伏/启动选股

从 research_data/<date>/intraday/ 读取盘中快照数据，分析：
  1. 最近 N 天哪些板块持续获得主力资金净流入
  2. 在主力流入板块中，识别个股机会：

两种模式：
  --mode=sneak    潜伏模式（默认）：主力温和流入 + 大单支撑 + 换手适中 + 趋势加速
  --mode=breakout 启动模式：主力加速流入 + 放量突破 + 价格动量 + 大单主导 + 板块共振

数据源:
  - research_data/<date>/intraday/industry_flow_*.csv  → 行业板块资金流快照
  - research_data/<date>/intraday/fund_flow_*.csv      → 全市场个股资金流（含"行业"字段）

用法:
    python sector_flow_analysis.py                        # 潜伏模式，近3天
    python sector_flow_analysis.py 5                      # 近5天
    python sector_flow_analysis.py --mode=breakout        # 启动模式
    python sector_flow_analysis.py 5 --mode=breakout      # 启动模式，近5天
    python sector_flow_analysis.py --top=10               # 前10个板块
"""
import os
import sys
import csv
import re
import glob
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional, List, Dict, Tuple

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
RESEARCH_ROOT = os.path.join(PROJECT_ROOT, "research_data")
REPORT_DIR = os.path.join(PROJECT_ROOT, "baostock_data", "analysis")


# ============================================================
# 工具
# ============================================================
def _to_float(val) -> float:
    if val is None or val == "-" or val == "":
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _fmt_yi(val: float) -> str:
    if abs(val) >= 1e8:
        return f"{val / 1e8:+.2f}亿"
    if abs(val) >= 1e4:
        return f"{val / 1e4:+.0f}万"
    return f"{val:+.0f}"


def _fmt_yi_short(val: float) -> str:
    if abs(val) >= 1e8:
        return f"{val / 1e8:+.2f}亿"
    return f"{val / 1e4:+.0f}万"


def find_data_dates() -> List[str]:
    """扫描 research_data/ 找到含 intraday 数据的日期"""
    if not os.path.isdir(RESEARCH_ROOT):
        return []
    dates = []
    for entry in os.listdir(RESEARCH_ROOT):
        entry_path = os.path.join(RESEARCH_ROOT, entry)
        if os.path.isdir(entry_path) and re.match(r"^\d{8}$", entry):
            intraday = os.path.join(entry_path, "intraday")
            if os.path.isdir(intraday):
                dates.append(entry)
    return sorted(dates, reverse=True)


def find_latest_snapshot(date_str: str, prefix: str) -> Optional[str]:
    """找到指定日期的最新盘中快照 CSV"""
    intraday = os.path.join(RESEARCH_ROOT, date_str, "intraday")
    if not os.path.isdir(intraday):
        return None
    pattern = os.path.join(intraday, f"{prefix}_*.csv")
    # 排除带 (1) 后缀的重复文件
    files = [f for f in glob.glob(pattern) if "(1)" not in f]
    # 按时间戳排序，取最晚的
    files.sort(reverse=True)
    return files[0] if files else None


def read_csv(path: str) -> List[dict]:
    """读取 CSV，返回 dict 列表"""
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


# ============================================================
# 板块趋势分析
# ============================================================
def analyze_sector_trend(dates: List[str], days: int) -> Dict[str, dict]:
    """
    多日板块资金流趋势分析
    每个日期取当日最后一笔快照（收盘价附近）
    返回: {sector_code: {name, daily_flows, flow_5d, flow_10d, trend_score, ...}}
    """
    sector_data: Dict[str, dict] = {}

    for date_str in dates[:days]:
        csv_path = find_latest_snapshot(date_str, "industry_flow")
        if not csv_path:
            continue

        sectors = read_csv(csv_path)
        for s in sectors:
            code = s.get("代码", "").strip()
            name = s.get("名称", "").strip()
            if not code:
                continue

            flow = _to_float(s.get("主力净流入", 0))
            ratio = _to_float(s.get("主力占比", 0))
            flow_5d = _to_float(s.get("5日主力净流入", 0))
            flow_10d = _to_float(s.get("10日主力净流入", 0))
            super_large = _to_float(s.get("超大单净流入", 0))
            large = _to_float(s.get("大单净流入", 0))

            if code not in sector_data:
                sector_data[code] = {
                    "name": name,
                    "daily": [],
                    "flow_5d": 0.0,
                    "flow_10d": 0.0,
                    "ratio": 0.0,
                    "super_large": 0.0,
                    "large": 0.0,
                }
            sector_data[code]["daily"].append((date_str, flow))
            if abs(flow_5d) > abs(sector_data[code]["flow_5d"]):
                sector_data[code]["flow_5d"] = flow_5d
            if abs(flow_10d) > abs(sector_data[code]["flow_10d"]):
                sector_data[code]["flow_10d"] = flow_10d
            if abs(ratio) > abs(sector_data[code]["ratio"]):
                sector_data[code]["ratio"] = ratio
                sector_data[code]["super_large"] = super_large
                sector_data[code]["large"] = large

    # 计算趋势评分
    all_flows = [sum(f for _, f in d["daily"]) for d in sector_data.values()]
    max_abs_flow = max(abs(v) for v in all_flows) if all_flows else 1.0
    all_accels = [d["flow_5d"] - d["flow_10d"] for d in sector_data.values()]
    max_abs_accel = max(abs(v) for v in all_accels) if all_accels else 1.0

    for code, data in sector_data.items():
        flows = [f for _, f in data["daily"]]
        cumulative = sum(flows)
        n_days = len(flows)
        consecutive_pos = sum(1 for f in flows if f > 0)
        trend_accel = data["flow_5d"] - data["flow_10d"]

        # 评分 (0-100): 累计40% + 连续性30% + 加速30%
        score = 0.0
        if max_abs_flow > 0:
            score += (cumulative / max_abs_flow) * 40
        if n_days > 0:
            score += (consecutive_pos / n_days) * 30
        if max_abs_accel > 0:
            score += (trend_accel / max_abs_accel) * 30

        data["cumulative"] = cumulative
        data["avg_daily"] = cumulative / n_days if n_days else 0
        data["consecutive"] = consecutive_pos
        data["accel"] = trend_accel
        data["score"] = round(score, 1)
        data["n_days"] = n_days

    return sector_data


# ============================================================
# 潜伏股票筛选
# ============================================================
def _score_stock(stock: dict, sector_name: str) -> dict:
    """单只股票的潜伏评分 (0-100)"""
    code = stock.get("代码", "").strip()
    name = stock.get("名称", "").strip()
    main_flow = _to_float(stock.get("主力净流入", 0))
    main_ratio = _to_float(stock.get("主力占比", 0))
    super_large = _to_float(stock.get("超大单净流入", 0))
    large_flow = _to_float(stock.get("大单净流入", 0))
    mid_flow = _to_float(stock.get("中单净流入", 0))
    small_flow = _to_float(stock.get("小单净流入", 0))
    turnover = _to_float(stock.get("换手率", 0))
    volume_ratio = _to_float(stock.get("量比", 0))
    change_pct = _to_float(stock.get("涨跌幅", 0))
    market_cap = _to_float(stock.get("总市值", 0))
    flow_5d = _to_float(stock.get("5日主力净流入", 0))
    flow_10d = _to_float(stock.get("10日主力净流入", 0))
    margin_net = _to_float(stock.get("融资净买入", 0))
    total_large = super_large + large_flow

    signals = []
    score = 0.0

    # 1. 主力净流入适中 (20分) — 有资金但非极端
    if main_flow > 100e4 and main_ratio > 0:
        signals.append(f"主力净流入{_fmt_yi(main_flow)} 占比{main_ratio:.1f}%")
        score += 12
        # 占比适中加分（非涨停热门）
        if main_ratio < 20:
            score += 8
            signals[-1] += " → 温和流入"
        else:
            signals[-1] += " → 注意：占比偏高"

    # 2. 大单+超大单正流入 (25分) — 机构行为
    if total_large > 50e4:
        signals.append(f"大单+超大单{_fmt_yi(total_large)} → 机构特征")
        score += 15
        if super_large > 0 and large_flow > 0:
            score += 10
            signals[-1] += "（双单支撑）"
    elif total_large > 0:
        score += 5

    # 3. 换手率适中 (20分)
    if 0.5 < turnover < 8:
        signals.append(f"换手率{turnover:.1f}% → 活跃适中")
        score += 12
        if 1 < turnover < 5:
            score += 8
    elif turnover >= 8:
        signals.append(f"换手率{turnover:.1f}%过高 → 注意风险")
        score -= 5
    else:
        signals.append(f"换手率{turnover:.2f}%过低 → 缺乏活跃度")

    # 4. 量比合理 (15分)
    if volume_ratio > 0.8:
        signals.append(f"量比{volume_ratio:.1f}")
        score += 10
        if 1.0 < volume_ratio < 3.0:
            score += 5
            signals[-1] += " → 温和放量"

    # 5. 涨跌幅温和 (10分)
    if -3 < change_pct < 9:
        signals.append(f"涨跌{change_pct:+.1f}%")
        score += 5
        if 0 < change_pct < 5:
            score += 5
            signals[-1] += " → 温和上涨"

    # 6. 趋势加速 5d > 10d (10分)
    if flow_5d > flow_10d and flow_5d > 0:
        signals.append(f"5日{_fmt_yi(flow_5d)} > 10日{_fmt_yi(flow_10d)} → 趋势加速")
        score += 6
        if flow_5d > flow_10d * 1.5:
            score += 4
            signals[-1] += "（显著）"

    # 7. 融资净买入 > 0 — 杠杆资金也看好
    if margin_net > 0:
        score += 3
        # 不添加信号，静默加分

    # 8. 中单流出 + 主力流入 = 散户在卖、主力在接
    if main_flow > 0 and mid_flow < 0:
        score += 5
        signals.append(f"中单流出示弱{_fmt_yi(mid_flow)} → 散户卖出，主力承接")

    return {
        "code": code,
        "name": name,
        "sector": sector_name,
        "main_flow_yi": round(main_flow / 1e8, 2),
        "main_ratio": round(main_ratio, 1),
        "large_flow_yi": round(total_large / 1e8, 2),
        "turnover": round(turnover, 1),
        "volume_ratio": round(volume_ratio, 1),
        "change_pct": round(change_pct, 1),
        "market_cap_yi": round(market_cap / 1e8, 0),
        "flow_5d_yi": round(flow_5d / 1e8, 2),
        "flow_10d_yi": round(flow_10d / 1e8, 2),
        "margin_yi": round(margin_net / 1e8, 2),
        "sneak_score": round(min(score, 100), 1),
        "signals": signals,
    }


def _score_breakout_stock(stock: dict, sector_name: str) -> dict:
    """启动模式评分 (0-100)：主力加速流入 + 放量突破 + 价格动量 + 大单主导 + 板块共振"""
    code = stock.get("代码", "").strip()
    name = stock.get("名称", "").strip()
    main_flow = _to_float(stock.get("主力净流入", 0))
    main_ratio = _to_float(stock.get("主力占比", 0))
    super_large = _to_float(stock.get("超大单净流入", 0))
    large_flow = _to_float(stock.get("大单净流入", 0))
    mid_flow = _to_float(stock.get("中单净流入", 0))
    small_flow = _to_float(stock.get("小单净流入", 0))
    turnover = _to_float(stock.get("换手率", 0))
    volume_ratio = _to_float(stock.get("量比", 0))
    change_pct = _to_float(stock.get("涨跌幅", 0))
    market_cap = _to_float(stock.get("总市值", 0))
    flow_5d = _to_float(stock.get("5日主力净流入", 0))
    flow_10d = _to_float(stock.get("10日主力净流入", 0))
    margin_net = _to_float(stock.get("融资净买入", 0))
    total_large = super_large + large_flow
    total_retail = mid_flow + small_flow

    signals = []
    score = 0.0

    # 1. 主力加速流入 (30pts) — 今日主力显著流入 + 趋势加速
    if main_flow > 100e4:
        score += 12
        signals.append(f"主力净流入{_fmt_yi(main_flow)}")
        if main_ratio > 3:
            score += 8
            signals[-1] += f" 占比{main_ratio:.1f}%"
        if flow_5d > flow_10d and flow_5d > 0:
            score += 10
            signals.append(f"趋势加速: 5日{_fmt_yi(flow_5d)} > 10日{_fmt_yi(flow_10d)}")
    elif main_flow > 0:
        score += 5

    # 2. 放量突破 (25pts) — 量比放大 + 换手活跃
    if volume_ratio > 1.5:
        score += 15
        signals.append(f"放量: 量比{volume_ratio:.1f}")
        if turnover > 3:
            score += 10
            signals[-1] += f" 换手{turnover:.1f}% → 量价配合"
    elif volume_ratio > 1.0:
        score += 8
        signals.append(f"温和放量: 量比{volume_ratio:.1f}")

    # 3. 价格动量 (20pts) — 涨幅 + 量价方向一致
    if 1 < change_pct < 9:
        score += 12
        signals.append(f"涨跌{change_pct:+.1f}% → 多头启动")
        if change_pct > 2:
            score += 8
    elif 0 < change_pct <= 1:
        score += 6
        signals.append(f"涨跌{change_pct:+.1f}% → 温和蓄力")
    elif change_pct < -3:
        score -= 5
        signals.append(f"回调{change_pct:+.1f}% → 注意")

    # 4. 大单主导 (15pts) — 主力买入 vs 散户卖出
    if total_large > 100e4 and abs(total_large) > abs(total_retail):
        score += 10
        signals.append(f"大单主导: {_fmt_yi(total_large)} > 散户{_fmt_yi(total_retail)}")
        if super_large > large_flow:
            score += 5
            signals[-1] += " → 超大单占优"

    # 5. 板块共振 (10pts) — 个股在流入板块中
    score += 10  # 已经在Top板块中，直接给分
    if change_pct > 0 and main_flow > 0 and turnover > 1:
        signals.append(f"板块+资金+量价共振")

    return {
        "code": code,
        "name": name,
        "sector": sector_name,
        "main_flow_yi": round(main_flow / 1e8, 2),
        "main_ratio": round(main_ratio, 1),
        "large_flow_yi": round(total_large / 1e8, 2),
        "turnover": round(turnover, 1),
        "volume_ratio": round(volume_ratio, 1),
        "change_pct": round(change_pct, 1),
        "market_cap_yi": round(market_cap / 1e8, 0),
        "flow_5d_yi": round(flow_5d / 1e8, 2),
        "flow_10d_yi": round(flow_10d / 1e8, 2),
        "margin_yi": round(margin_net / 1e8, 2),
        "sneak_score": round(min(score, 100), 1),
        "signals": signals,
    }


# ============================================================
# 选股入口（统一）
# ============================================================
def screen_stocks(
    sector_data: Dict[str, dict],
    dates: List[str],
    days: int,
    top_n_sectors: int = 5,
    mode: str = "sneak",
) -> List[dict]:
    """
    在主力流入板块中筛选股票
    mode: "sneak" = 潜伏, "breakout" = 启动
    """
    if not dates:
        return []
    latest_date = dates[0]
    fund_csv = find_latest_snapshot(latest_date, "fund_flow")
    if not fund_csv:
        print(f"  ⚠ 未找到 fund_flow 快照 ({latest_date})", flush=True)
        return []

    all_stocks = read_csv(fund_csv)
    print(f"  读取 {len(all_stocks)} 只个股 ({os.path.basename(fund_csv)})", flush=True)

    # Top N 流入板块
    ranked_sectors = sorted(
        sector_data.items(),
        key=lambda x: x[1].get("score", 0),
        reverse=True,
    )
    top_sectors = [
        (code, data) for code, data in ranked_sectors
        if data.get("cumulative", 0) > 0 and data.get("n_days", 0) > 0
    ][:top_n_sectors]

    mode_label = "启动上涨" if mode == "breakout" else "潜伏"
    print(f"\n  Top {len(top_sectors)} 流入板块 ({mode_label}模式):", flush=True)
    for code, data in top_sectors:
        print(f"    {data['name']}({code}) 累计{_fmt_yi(data['cumulative'])} "
              f"评分{data['score']:.0f}", flush=True)

    # 按行业名称索引
    sector_stocks: Dict[str, List[dict]] = defaultdict(list)
    for s in all_stocks:
        industry = s.get("行业", "").strip()
        if industry:
            sector_stocks[industry].append(s)

    # 选股函数
    scorer = _score_breakout_stock if mode == "breakout" else _score_stock

    all_candidates = []
    for code, sdata in top_sectors:
        sector_name = sdata["name"]
        stocks = sector_stocks.get(sector_name, [])
        if not stocks:
            for ind_name, ind_stocks in sector_stocks.items():
                if sector_name in ind_name or ind_name in sector_name:
                    stocks = ind_stocks
                    break
        if not stocks:
            print(f"    {sector_name}: 无匹配个股", flush=True)
            continue

        print(f"\n  [{sector_name}] {len(stocks)} 只成分股 ...", flush=True)

        scored = []
        for stock in stocks:
            result = scorer(stock, sector_name)
            if result["sneak_score"] > 0:
                scored.append(result)

        scored.sort(key=lambda x: x["sneak_score"], reverse=True)
        for s in scored[:8]:
            print(f"    {s['code']} {s['name']:<8s} "
                  f"评分{s['sneak_score']:.0f} "
                  f"主力{s['main_flow_yi']:.2f}亿 "
                  f"换手{s['turnover']:.1f}% "
                  f"涨跌{s['change_pct']:+.1f}%",
                  flush=True)
        all_candidates.extend(scored)

    all_candidates.sort(key=lambda x: x["sneak_score"], reverse=True)
    return all_candidates


# ============================================================
# 报告生成
# ============================================================
def generate_report(
    sector_data: Dict[str, dict],
    candidates: List[dict],
    dates: List[str],
    days: int,
    mode_label: str = "主力潜伏",
) -> str:
    """生成 Markdown 分析报告"""
    lines = []
    analyzed_dates = dates[:days]

    lines.append(f"# 板块资金流 + {mode_label}分析报告")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**分析范围**: {len(analyzed_dates)} 个交易日")
    lines.append(f"**日期**: {', '.join(analyzed_dates)}")
    lines.append("")

    # ── 一、板块资金流趋势 ──
    lines.append("## 一、板块资金流趋势 Top 15")
    lines.append("")
    lines.append("| # | 板块 | 代码 | 累计流入 | 日均流入 | 5日流入 | 10日流入 | 趋势 | 连续流入 | 评分 |")
    lines.append("|---|------|------|----------|----------|---------|----------|------|----------|------|")

    ranked = sorted(
        sector_data.items(),
        key=lambda x: x[1].get("score", 0),
        reverse=True,
    )[:15]

    for i, (code, data) in enumerate(ranked, 1):
        accel = data.get("accel", 0)
        if accel > 1e8:
            accel_str = "🟢加速"
        elif accel < -1e8:
            accel_str = "🔴减速"
        else:
            accel_str = "→ 持平"
        lines.append(
            f"| {i} | {data['name']} | {code} | "
            f"{_fmt_yi_short(data.get('cumulative', 0))} | "
            f"{_fmt_yi_short(data.get('avg_daily', 0))} | "
            f"{_fmt_yi_short(data.get('flow_5d', 0))} | "
            f"{_fmt_yi_short(data.get('flow_10d', 0))} | "
            f"{accel_str} | "
            f"{data.get('consecutive', 0)}/{data.get('n_days', 0)}天 | "
            f"**{data.get('score', 0):.0f}** |"
        )
    lines.append("")

    # ── 二、主力潜伏候选 ──
    lines.append("## 二、主力潜伏候选股票")
    lines.append("")
    lines.append("> **筛选逻辑**：板块主力持续流入 + 个股主力温和流入 + 大单支撑 + 换手适中 + 量比合理 + 趋势加速")
    lines.append("")

    if candidates:
        top = candidates[:30]
        lines.append("| # | 代码 | 名称 | 板块 | 主力(亿) | 占比% | 大单(亿) | 换手% | 量比 | 涨跌% | 5日(亿) | 10日(亿) | 融资(亿) | 评分 |")
        lines.append("|---|------|------|------|----------|-------|----------|-------|------|-------|---------|----------|----------|------|")

        for i, c in enumerate(top, 1):
            lines.append(
                f"| {i} | {c['code']} | {c['name']} | {c['sector']} | "
                f"{c['main_flow_yi']:.2f} | {c['main_ratio']:.1f} | {c['large_flow_yi']:.2f} | "
                f"{c['turnover']:.1f} | {c['volume_ratio']:.1f} | {c['change_pct']:+.1f} | "
                f"{c['flow_5d_yi']:.2f} | {c['flow_10d_yi']:.2f} | {c['margin_yi']:.2f} | "
                f"**{c['sneak_score']:.0f}** |"
            )
    else:
        lines.append("> ⚠ 未找到符合条件的潜伏股票")

    lines.append("")

    # ── 三、Top 10 信号详情 ──
    if candidates:
        lines.append("## 三、Top 10 股票信号详情")
        lines.append("")
        for i, c in enumerate(candidates[:10], 1):
            lines.append(f"### {i}. {c['code']} {c['name']} — {c['sector']} (评分 {c['sneak_score']:.0f})")
            lines.append(f"主力流入 {c['main_flow_yi']:.2f}亿 | 占比 {c['main_ratio']:.1f}% | "
                         f"大单 {c['large_flow_yi']:.2f}亿 | 换手 {c['turnover']:.1f}% | "
                         f"涨跌 {c['change_pct']:+.1f}%")
            lines.append("")
            for sig in c["signals"]:
                lines.append(f"- ✅ {sig}")
            lines.append("")

    lines.append("---")
    lines.append(f"*数据源: {RESEARCH_ROOT}/*/intraday/*")
    lines.append(f"*分析日期数: {len(dates)} 个交易日可用*")
    lines.append("")

    return "\n".join(lines)


# ============================================================
# 主入口
# ============================================================
def main() -> None:
    days = 3
    top_n = 5
    mode = "sneak"  # sneak | breakout

    global RESEARCH_ROOT
    for arg in sys.argv[1:]:
        if arg.isdigit():
            days = int(arg)
        elif arg.startswith("--top="):
            top_n = int(arg.split("=")[1])
        elif arg.startswith("--mode="):
            mode = arg.split("=", 1)[1]
        elif arg.startswith("--data-root="):
            RESEARCH_ROOT = arg.split("=", 1)[1]
        elif arg.startswith("--data_root="):
            RESEARCH_ROOT = arg.split("=", 1)[1]

    mode_label = "启动上涨" if mode == "breakout" else "主力潜伏"
    report_prefix = "BREAKOUT" if mode == "breakout" else "SECTOR_FLOW"

    print("═" * 60, flush=True)
    print(f"  板块资金流 + {mode_label}分析", flush=True)
    print(f"  数据源: {RESEARCH_ROOT}", flush=True)
    print("═" * 60, flush=True)

    # 1. 扫描数据
    dates = find_data_dates()
    if not dates:
        print("\n⚠ 未找到 research_data/<date>/intraday/ 数据！")
        print("请先运行盘中数据采集。")
        return

    print(f"\n[数据] {len(dates)} 个交易日: {', '.join(dates[:min(6, len(dates))])}", flush=True)
    analyze_days = min(days, len(dates))

    # 2. 板块趋势
    print(f"\n[板块] 分析近 {analyze_days} 天趋势 ...", flush=True)
    sector_data = analyze_sector_trend(dates, analyze_days)

    if sector_data:
        ranked = sorted(sector_data.items(), key=lambda x: x[1].get("score", 0), reverse=True)
        print(f"\n  🏆 Top 流入板块:", flush=True)
        for i, (code, data) in enumerate(ranked[:5], 1):
            print(f"  {i}. {data['name']}({code}) "
                  f"累计{_fmt_yi(data['cumulative'])} "
                  f"评分{data['score']:.0f}",
                  flush=True)

    # 3. 选股
    print(f"\n[选股] 在Top {top_n}板块中筛选{mode_label} ...", flush=True)
    candidates = screen_stocks(
        sector_data, dates, analyze_days,
        top_n_sectors=top_n, mode=mode,
    )
    print(f"\n  共 {len(candidates)} 只候选", flush=True)

    # 4. 报告
    print(f"\n[报告] 生成中 ...", flush=True)
    report = generate_report(sector_data, candidates, dates, analyze_days, mode_label)
    os.makedirs(REPORT_DIR, exist_ok=True)

    # 清理旧同模式报告，只保留最新
    old_reports = sorted(glob.glob(os.path.join(REPORT_DIR, f"{report_prefix}_REPORT_*.md")))
    for old in old_reports[:-1]:
        try:
            os.remove(old)
        except Exception:
            pass

    report_path = os.path.join(REPORT_DIR, f"{report_prefix}_REPORT_{datetime.now().strftime('%Y%m%d')}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"  ✅ {report_path}", flush=True)

    # 摘要输出
    print(f"\n{'═' * 60}")
    print(report[:3000])
    print(f"{'═' * 60}")


if __name__ == "__main__":
    main()
