#!/usr/bin/env python
"""
板块资金流趋势分析 + 主力潜伏选股

从 data/<date>/ 目录读取多日板块资金流快照，分析：
  1. 最近 N 天哪些板块持续获得主力资金净流入
  2. 在主力流入板块中，识别主力开始"潜伏"的个股

筛选逻辑：
  - 板块：多日累计主力净流入 > 0，且排名靠前
  - 个股：主力净流入 > 0 但非极端热门（中低位积累）
  - 近期趋势加速：5日流向 > 10日流向
  - 大单资金正流入（机构行为特征）
  - 换手率适中（非已追涨阶段）
  - 量比 > 0.8（有交易活跃度但不过热）

用法:
    python sector_flow_analysis.py                     # 分析最近3天
    python sector_flow_analysis.py 5                   # 分析最近5天
    python sector_flow_analysis.py --date=20260702      # 指定日期
    python sector_flow_analysis.py --top=10             # 分析前10个板块
"""
import os
import sys
import csv
import json
import re
import glob
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional, List, Set, Dict, Tuple

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_ROOT = os.path.join(PROJECT_ROOT, "data")
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
    return f"{val / 1e4:+.2f}万"


def find_data_dates(data_root: str = DATA_ROOT) -> List[str]:
    """扫描 data/ 目录，找到所有含数据的日期"""
    if not os.path.isdir(data_root):
        return []
    dates = set()
    for entry in os.listdir(data_root):
        entry_path = os.path.join(data_root, entry)
        if os.path.isdir(entry_path) and re.match(r"^\d{8}$", entry):
            # 检查是否有板块数据
            has_data = (
                os.path.exists(os.path.join(entry_path, "industry_flow.csv")) or
                os.path.exists(os.path.join(entry_path, "industry_flow_5d.csv")) or
                bool(glob.glob(os.path.join(entry_path, "industry_flow_*.csv")))
            )
            if has_data:
                dates.add(entry)
    return sorted(dates, reverse=True)


def find_sector_csv(date_path: str) -> Optional[str]:
    """找到板块资金流 CSV（优先今日排行，其次最新快照）"""
    # 优先 industry_flow.csv（今日排行）
    paths = [
        os.path.join(date_path, "industry_flow.csv"),
        os.path.join(date_path, "industry_flow_5d.csv"),
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    # 回退到时间戳命名的快照文件
    pattern = os.path.join(date_path, "industry_flow_*.csv")
    files = sorted(glob.glob(pattern), reverse=True)
    return files[0] if files else None


def read_sector_flow(csv_path: str) -> List[dict]:
    """读取板块资金流 CSV"""
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def find_stock_csvs(date_path: str) -> List[str]:
    """找到板块成分股 CSV 文件"""
    sectors_dir = os.path.join(date_path, "sectors")
    if os.path.isdir(sectors_dir):
        return sorted(glob.glob(os.path.join(sectors_dir, "sector_stocks_*.csv")))
    return sorted(glob.glob(os.path.join(date_path, "sector_stocks_*.csv")))


def read_sector_stocks(csv_path: str) -> Tuple[str, str, List[dict]]:
    """
    读取板块成分股 CSV，返回 (板块代码, 板块名称, 股票列表)
    CSV 文件名格式: sector_stocks_{code}_{date}_{time}.csv
    """
    basename = os.path.basename(csv_path)
    # 尝试从文件名解析板块代码
    parts = basename.replace(".csv", "").split("_")
    # sector_stocks_BK0477_20260702_143001.csv → code = BK0477 after "stocks_"
    code = ""
    for i, p in enumerate(parts):
        if p == "stocks" and i + 1 < len(parts):
            code = parts[i + 1]
            break
    if not code and len(parts) >= 3:
        code = parts[2]  # 回退

    rows = []
    sector_name = ""
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
            if not sector_name and row.get("名称"):
                sector_name = row.get("名称", "")
    return code, sector_name, rows


# ============================================================
# 板块分析
# ============================================================
def analyze_sector_trend(dates: List[str], days: int) -> Dict[str, dict]:
    """
    多日板块资金流趋势分析
    返回: {sector_code: {name, flow_today, flow_5d, flow_10d, trend_score, ...}}
    """
    sector_data: Dict[str, dict] = {}  # code → {name, flows_by_date, ...}

    for date_str in dates[:days]:
        date_path = os.path.join(DATA_ROOT, date_str)
        csv_path = find_sector_csv(date_path)
        if not csv_path:
            continue

        sectors = read_sector_flow(csv_path)
        for s in sectors:
            code = s.get("代码", s.get("f12", ""))
            name = s.get("名称", s.get("f14", ""))
            flow_today = _to_float(s.get("主力净流入", s.get("f62", 0)))
            flow_5d = _to_float(s.get("5日主力净流入", s.get("f164", 0)))
            flow_10d = _to_float(s.get("10日主力净流入", s.get("f174", 0)))
            ratio = _to_float(s.get("主力占比", s.get("f184", 0)))

            if code not in sector_data:
                sector_data[code] = {
                    "name": name,
                    "flows": [],
                    "flow_5d": flow_5d,
                    "flow_10d": flow_10d,
                    "ratio": ratio,
                }
            sector_data[code]["flows"].append((date_str, flow_today))
            # 用最新数据更新 5d/10d
            if flow_5d != 0:
                sector_data[code]["flow_5d"] = flow_5d
            if flow_10d != 0:
                sector_data[code]["flow_10d"] = flow_10d
            if ratio != 0:
                sector_data[code]["ratio"] = ratio

    # 计算评分
    for code, data in sector_data.items():
        flows = [f for _, f in data["flows"]]
        cumulative = sum(flows)
        avg_daily = cumulative / len(flows) if flows else 0
        consecutive_pos = sum(1 for f in flows if f > 0)
        trend_accel = data["flow_5d"] - data["flow_10d"]  # 5日 > 10日 = 加速

        # 趋势评分 (0-100)
        score = 0.0
        # 累计流入 (40%)
        max_flow = max(abs(cumulative) for d in sector_data.values() if abs(cumulative) > 0)
        score += (cumulative / max_flow) * 40 if max_flow > 0 else 0
        # 连续流入天数 (30%)
        if len(flows) > 0:
            score += (consecutive_pos / len(flows)) * 30
        # 趋势加速 (30%)
        max_accel = max(abs(s["flow_5d"] - s["flow_10d"]) for s in sector_data.values())
        score += (trend_accel / max_accel) * 30 if max_accel > 0 else 0

        data["cumulative_flow"] = cumulative
        data["avg_daily_flow"] = avg_daily
        data["consecutive_pos_days"] = consecutive_pos
        data["trend_accel"] = trend_accel
        data["trend_score"] = round(score, 1)

    return sector_data


# ============================================================
# 潜伏股票筛选
# ============================================================
SNEAK_CRITERIA = {
    "main_flow_min": 100e4,       # 主力净流入 > 100万
    "main_flow_max_ratio": 20,     # 主力占比 < 20%（非追涨）
    "large_order_min": 50e4,       # 大单净流入 > 50万
    "turnover_max": 8.0,           # 换手率 < 8%（非过热）
    "turnover_min": 0.5,           # 换手率 > 0.5%（有活跃度）
    "volume_ratio_min": 0.8,       # 量比 > 0.8
    "change_max": 9.0,             # 涨跌幅 < 9%（非涨停追板）
    "change_min": -3.0,            # 涨跌幅 > -3%（排除大跌股）
}


def score_sneak_stock(stock: dict, sector_name: str) -> Dict:
    """
    评估单只股票的"潜伏"特征
    返回: {code, name, score, signals, reasons}
    """
    code = stock.get("代码", stock.get("f12", ""))
    name = stock.get("名称", stock.get("f14", ""))
    main_flow = _to_float(stock.get("主力净流入", stock.get("f62", 0)))
    main_ratio = _to_float(stock.get("主力占比%", stock.get("f184", 0)))
    super_large_flow = _to_float(stock.get("超大单净流入", stock.get("f66", 0)))
    large_flow = _to_float(stock.get("大单净流入", stock.get("f72", 0)))
    turnover = _to_float(stock.get("换手率%", stock.get("f8", 0)))
    volume_ratio = _to_float(stock.get("量比", stock.get("f10", 0)))
    change_pct = _to_float(stock.get("涨跌幅%", stock.get("f3", 0)))
    market_cap = _to_float(stock.get("总市值", stock.get("f20", 0)))
    flow_5d = _to_float(stock.get("5日主力净流入", stock.get("f164", 0)))
    flow_10d = _to_float(stock.get("10日主力净流入", stock.get("f174", 0)))
    rank_5d = stock.get("5日排名", stock.get("_rank_5d", ""))
    rank_10d = stock.get("10日排名", stock.get("_rank_10d", ""))

    signals = []
    score = 0.0

    # 1. 主力净流入适中 (0-20)
    c = SNEAK_CRITERIA
    if main_flow > c["main_flow_min"] and main_ratio < c["main_flow_max_ratio"]:
        signals.append(f"主力流入{_fmt_yi(main_flow)} 占比{main_ratio:.1f}% → 有资金但非热门")
        score += 20

    # 2. 大单 + 超大单正流入 (0-25)
    total_large = super_large_flow + large_flow
    if total_large > c["large_order_min"]:
        signals.append(f"大单+超大单净流入{_fmt_yi(total_large)} → 机构行为特征")
        score += 25

    # 3. 换手率适中 (0-20)
    if c["turnover_min"] < turnover < c["turnover_max"]:
        signals.append(f"换手率{turnover:.1f}% → 活跃但未过热")
        score += 20
    elif turnover > c["turnover_max"]:
        signals.append(f"换手率{turnover:.1f}%过高 → 可能已追涨")
        score -= 10

    # 4. 量比合理 (0-15)
    if volume_ratio > c["volume_ratio_min"]:
        signals.append(f"量比{volume_ratio:.1f} → 交投活跃")
        score += 15

    # 5. 涨跌幅合理 (0-10)
    if c["change_min"] < change_pct < c["change_max"]:
        signals.append(f"涨跌{change_pct:+.2f}% → 温和上涨")
        score += 10

    # 6. 趋势加速：5日 > 10日 (0-10)
    if flow_5d > flow_10d and flow_5d > 0:
        signals.append(f"5日流入{_fmt_yi(flow_5d)} > 10日{_fmt_yi(flow_10d)} → 近期加速")
        score += 10

    return {
        "code": code,
        "name": name,
        "sector": sector_name,
        "main_flow_yi": round(main_flow / 1e8, 2),
        "main_ratio": round(main_ratio, 2),
        "large_flow_yi": round(total_large / 1e8, 2),
        "turnover": round(turnover, 2),
        "volume_ratio": round(volume_ratio, 2),
        "change_pct": round(change_pct, 2),
        "market_cap_yi": round(market_cap / 1e8, 2),
        "flow_5d_yi": round(flow_5d / 1e8, 2),
        "flow_10d_yi": round(flow_10d / 1e8, 2),
        "rank_5d": rank_5d,
        "sneak_score": round(score, 1),
        "signals": signals,
    }


def screen_sneak_stocks(
    sector_data: Dict[str, dict],
    date_path: str,
    top_n_sectors: int = 5,
    top_n_stocks: int = 20,
) -> List[dict]:
    """
    在主力流入的板块中筛选潜伏股票
    """
    # 1. 找到Top流入板块
    ranked_sectors = sorted(
        sector_data.items(),
        key=lambda x: x[1].get("trend_score", 0),
        reverse=True,
    )[:top_n_sectors]

    all_candidates = []

    for code, sdata in ranked_sectors:
        sector_name = sdata["name"]
        cumulative = sdata.get("cumulative_flow", 0)
        if cumulative <= 0:
            continue

        print(f"\n  扫描板块: {sector_name}({code}) "
              f"累计流入{_fmt_yi(cumulative)} "
              f"评分{sdata.get('trend_score', 0):.0f}",
              flush=True)

        # 2. 读取该板块成分股
        stock_csvs = find_stock_csvs(date_path)
        stocks_found = []

        for stock_csv in stock_csvs:
            s_code, s_name, stocks = read_sector_stocks(stock_csv)
            if s_code == code or sector_name in s_name:
                stocks_found = stocks
                break

        if not stocks_found:
            print(f"    未找到成分股数据", flush=True)
            continue

        # 3. 逐只评分
        scored = []
        for stock in stocks_found:
            result = score_sneak_stock(stock, sector_name)
            if result["sneak_score"] > 0:
                scored.append(result)

        scored.sort(key=lambda x: x["sneak_score"], reverse=True)
        for s in scored[:5]:
            print(f"    {s['code']} {s['name']} "
                  f"评分{s['sneak_score']:.0f} "
                  f"主力流入{s['main_flow_yi']:.2f}亿 "
                  f"大单{s['large_flow_yi']:.2f}亿",
                  flush=True)

        all_candidates.extend(scored)

    # 综合排序
    all_candidates.sort(key=lambda x: x["sneak_score"], reverse=True)
    return all_candidates[:top_n_stocks]


# ============================================================
# 报告生成
# ============================================================
def generate_report(
    sector_data: Dict[str, dict],
    candidates: List[dict],
    dates: List[str],
    days: int,
) -> str:
    """生成 Markdown 分析报告"""
    today = datetime.now().strftime("%Y%m%d")
    lines = []

    lines.append(f"# 板块资金流 + 主力潜伏分析报告")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**分析范围**: 近 {len(dates[:days])} 个交易日 ({', '.join(dates[:days])})")
    lines.append("")

    # ── 一、板块资金流趋势 ──
    lines.append("## 一、板块资金流趋势 Top 10")
    lines.append("")
    lines.append("| 排名 | 板块 | 代码 | 累计流入 | 日均流入 | 5日流入 | 10日流入 | 趋势加速 | 连续流入 | 趋势评分 |")
    lines.append("|------|------|------|----------|----------|---------|----------|----------|----------|----------|")

    ranked = sorted(
        sector_data.items(),
        key=lambda x: x[1].get("trend_score", 0),
        reverse=True,
    )[:10]

    for i, (code, data) in enumerate(ranked, 1):
        accel = data.get("trend_accel", 0)
        accel_str = "🟢加速" if accel > 0 else ("🔴减速" if accel < 0 else "→")
        lines.append(
            f"| {i} | {data['name']} | {code} | "
            f"{_fmt_yi(data.get('cumulative_flow', 0))} | "
            f"{_fmt_yi(data.get('avg_daily_flow', 0))} | "
            f"{_fmt_yi(data.get('flow_5d', 0))} | "
            f"{_fmt_yi(data.get('flow_10d', 0))} | "
            f"{accel_str} | "
            f"{data.get('consecutive_pos_days', 0)}/{len(dates[:days])}天 | "
            f"{data.get('trend_score', 0):.0f} |"
        )

    lines.append("")

    # ── 二、主力潜伏个股 ──
    lines.append("## 二、主力潜伏候选股票")
    lines.append("")
    lines.append("**筛选逻辑**：板块主力持续流入 + 个股主力温和流入（非热门追涨）+ 大单资金正流入 + 换手率适中 + 量比合理")
    lines.append("")

    if candidates:
        lines.append("| # | 代码 | 名称 | 板块 | 主力流入(亿) | 占比% | 大单(亿) | 换手% | 量比 | 涨跌% | 市值(亿) | 5日(亿) | 10日(亿) | 潜伏评分 |")
        lines.append("|---|------|------|------|-------------|-------|---------|-------|------|-------|----------|---------|----------|----------|")

        for i, c in enumerate(candidates, 1):
            lines.append(
                f"| {i} | {c['code']} | {c['name']} | {c['sector']} | "
                f"{c['main_flow_yi']:.2f} | {c['main_ratio']:.1f} | {c['large_flow_yi']:.2f} | "
                f"{c['turnover']:.1f} | {c['volume_ratio']:.1f} | {c['change_pct']:+.1f} | "
                f"{c['market_cap_yi']:.1f} | {c['flow_5d_yi']:.2f} | {c['flow_10d_yi']:.2f} | "
                f"**{c['sneak_score']:.0f}** |"
            )
    else:
        lines.append("> ⚠ 未找到符合条件的潜伏股票。请确保已运行数据采集：")
        lines.append("> ```bash")
        lines.append("> python -m data_collector.main --date=20260702")
        lines.append("> ```")

    lines.append("")

    # ── 三、信号详情 ──
    lines.append("## 三、候选股票信号详情")
    lines.append("")

    for i, c in enumerate(candidates[:10], 1):
        lines.append(f"### {i}. {c['code']} {c['name']} ({c['sector']}) — 评分 {c['sneak_score']:.0f}")
        lines.append("")
        for sig in c["signals"]:
            lines.append(f"- ✅ {sig}")
        lines.append("")

    # ── 数据状态 ──
    lines.append("---")
    lines.append(f"*数据源: {DATA_ROOT}/*")
    lines.append(f"*分析日期: {len(dates)} 个交易日有数据*")
    lines.append("")

    return "\n".join(lines)


# ============================================================
# 主入口
# ============================================================
def main() -> None:
    days = 3
    top_n = 5
    date_str = None

    for arg in sys.argv[1:]:
        if arg.isdigit():
            days = int(arg)
        elif arg.startswith("--date="):
            date_str = arg.split("=")[1]
        elif arg.startswith("--top="):
            top_n = int(arg.split("=")[1])

    print("═" * 60, flush=True)
    print(f"  板块资金流 + 主力潜伏分析", flush=True)
    print("═" * 60, flush=True)

    # 1. 扫描数据日期
    dates = find_data_dates()
    if not dates:
        print("\n⚠ 未找到板块资金流数据！", flush=True)
        print(f"\n请先运行数据采集命令:", flush=True)
        print(f"  python -m data_collector.main --date={datetime.now().strftime('%Y%m%d')}", flush=True)
        print(f"\n数据将保存到: {DATA_ROOT}/<date>/industry_flow*.csv", flush=True)
        # 不退出，生成空报告模板
        sector_data = {}
        candidates = []
    else:
        print(f"\n[数据扫描] 找到 {len(dates)} 个有效日期: {', '.join(dates[:min(5, len(dates))])}",
              flush=True)

        # 2. 板块趋势分析
        print(f"\n[板块分析] 近 {min(days, len(dates))} 天资金流趋势 ...", flush=True)
        sector_data = analyze_sector_trend(dates, min(days, len(dates)))

        if sector_data:
            ranked = sorted(
                sector_data.items(),
                key=lambda x: x[1].get("trend_score", 0),
                reverse=True,
            )
            print(f"\n 🏆 Top 5 流入板块:")
            for i, (code, data) in enumerate(ranked[:5], 1):
                print(f"  {i}. {data['name']}({code}) "
                      f"累计{_fmt_yi(data.get('cumulative_flow', 0))} "
                      f"评分{data.get('trend_score', 0):.0f}",
                      flush=True)
        else:
            print("  ⚠ 未能解析板块数据", flush=True)

        # 3. 潜伏个股筛选
        print(f"\n[个股筛选] 在Top {top_n} 流入板块中筛选潜伏股票 ...", flush=True)
        candidates = []
        if date_str:
            target_path = os.path.join(DATA_ROOT, date_str)
        elif dates:
            target_path = os.path.join(DATA_ROOT, dates[0])
        else:
            target_path = ""

        if sector_data and target_path and os.path.isdir(target_path):
            candidates = screen_sneak_stocks(
                sector_data, target_path,
                top_n_sectors=top_n, top_n_stocks=20,
            )
            print(f"\n 共筛选出 {len(candidates)} 只潜伏候选", flush=True)
        else:
            print("  ⚠ 无可用数据，跳过个股筛选", flush=True)

    # 4. 生成报告
    print(f"\n[报告] 生成 Markdown 报告 ...", flush=True)
    report = generate_report(sector_data, candidates, dates, days)
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(
        REPORT_DIR,
        f"SECTOR_FLOW_REPORT_{datetime.now().strftime('%Y%m%d')}.md",
    )
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n✅ 报告已保存: {report_path}", flush=True)

    # 5. 输出摘要
    print(report.split("---")[0] if "---" in report else report[:2000])


if __name__ == "__main__":
    main()
