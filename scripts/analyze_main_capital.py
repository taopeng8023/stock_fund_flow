#!/usr/bin/env python3
"""
主力资金流向分析脚本 — 基于当日盘中快照分析主力资金流入板块 & 个股

分析维度:
  1. 概念板块主力净流入 TOP + 日内趋势 (东方财富板块API)
  2. 行业板块主力净流入 TOP + 日内趋势 (东方财富板块API)
  3. 个股主力净流入 TOP + 日内趋势
  4. 行业资金聚集排名 (个股自底向上汇总 — 最准确)
  5. 主力资金加速流入/流出 检测
  6. 多维度交叉验证

数据校验: 主力净流入 = 超大单净流入 + 大单净流入 (逐行验证)

用法:
  python scripts/analyze_main_capital.py                          # 最新日期
  python scripts/analyze_main_capital.py --date=20260702          # 指定日期
  python scripts/analyze_main_capital.py --date=20260702 --top=20 # 自定义TOP数量
  python scripts/analyze_main_capital.py --watch                  # 持续监控模式
"""

import csv
import os
import sys
import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

BJS_TZ = timezone(timedelta(hours=8))
PROJECT_ROOT = Path(__file__).parent.parent
RESEARCH_ROOT = PROJECT_ROOT / "research_data"


def find_latest_date() -> str | None:
    """找到 research_data 下最新的日期目录（排除 backtest）。"""
    dates = []
    for d in RESEARCH_ROOT.iterdir():
        if d.is_dir() and d.name.isdigit() and len(d.name) == 8:
            dates.append(d.name)
    return max(dates) if dates else None


def load_snapshot(date_str: str, snap_type: str, ts: str) -> list[dict]:
    """加载单个快照 CSV。snap_type: concept_flow | industry_flow | fund_flow"""
    path = RESEARCH_ROOT / date_str / "intraday" / f"{snap_type}_{ts}.csv"
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def list_snapshots(date_str: str, snap_type: str) -> list[str]:
    """列出某日期某类型的所有快照时间戳（排序）。"""
    intraday = RESEARCH_ROOT / date_str / "intraday"
    if not intraday.exists():
        return []
    prefix = f"{snap_type}_"
    tss = []
    for f in intraday.iterdir():
        if f.name.startswith(prefix) and f.name.endswith(".csv"):
            ts = f.name[len(prefix):-4]
            if ts.isdigit() and len(ts) == 6:
                tss.append(ts)
    return sorted(tss)


def parse_float(v: str) -> float:
    """安全解析浮点数。"""
    if v is None or v in ("", "-", "--", "—"):
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def validate_main_flow(name: str, main: float, super_f: float, large: float) -> bool:
    """校验 主力净流入 == 超大单净流入 + 大单净流入。允许 1% 误差。"""
    calc = super_f + large
    if abs(main) < 100:  # 极小值忽略
        return True
    diff_pct = abs(main - calc) / abs(main) * 100
    return diff_pct < 1.0


def analyze_sector_flow(date_str: str, snap_type: str, top_n: int = 15):
    """
    分析板块资金流（东方财富板块API聚合数据）。

    snap_type: "concept_flow" | "industry_flow"

    注意: 板块API数据与个股汇总数据来源不同，数值不可直接对比。
    概念板块之间大量重叠（一只股票属于多个概念），总和无意义。
    应以个股自底向上汇总（analyze_stock_flow 中的 sector_ranking）为行业资金流的主要依据。
    """
    tss = list_snapshots(date_str, snap_type)
    if len(tss) < 2:
        return None

    first_ts = tss[0]
    last_ts = tss[-1]

    first_rows = load_snapshot(date_str, snap_type, first_ts)
    last_rows = load_snapshot(date_str, snap_type, last_ts)

    if not first_rows or not last_rows:
        return None

    first_map = {r["代码"]: r for r in first_rows}

    results = []
    validation_errors = 0
    skipped_new = 0

    for last_r in last_rows:
        code = last_r["代码"]
        first_r = first_map.get(code)

        try:
            flow_now = parse_float(last_r.get("主力净流入", "0"))
            pct_now = parse_float(last_r.get("主力占比", "0"))
            chg_now = parse_float(last_r.get("涨跌幅", "0"))
            name = last_r.get("名称", "")
            super_flow = parse_float(last_r.get("超大单净流入", "0"))
            large_flow = parse_float(last_r.get("大单净流入", "0"))
            flow_5d = parse_float(last_r.get("5日主力净流入", "0"))
            flow_10d = parse_float(last_r.get("10日主力净流入", "0"))

            # 数据校验
            if not validate_main_flow(name, flow_now, super_flow, large_flow):
                validation_errors += 1

            # 日内增量: 首帧不存在则设为 0
            if first_r:
                flow_first = parse_float(first_r.get("主力净流入", "0"))
                intraday_delta = flow_now - flow_first
            else:
                intraday_delta = 0.0
                skipped_new += 1

            results.append({
                "代码": code,
                "名称": name,
                "涨跌幅": chg_now,
                "主力净流入": flow_now,
                "主力占比": pct_now,
                "超大单净流入": super_flow,
                "大单净流入": large_flow,
                "日内增量": intraday_delta,
                "5日主力净流入": flow_5d,
                "10日主力净流入": flow_10d,
            })
        except Exception:
            continue

    results.sort(key=lambda x: x["主力净流入"], reverse=True)

    top_inflow = results[:top_n]
    bottom_outflow = sorted(results, key=lambda x: x["主力净流入"])[:top_n]
    accelerating = sorted(results, key=lambda x: x["日内增量"], reverse=True)[:top_n]
    decelerating = sorted(results, key=lambda x: x["日内增量"])[:top_n]

    total_inflow = sum(r["主力净流入"] for r in results)
    total_super = sum(r["超大单净流入"] for r in results)
    total_large = sum(r["大单净流入"] for r in results)

    # 统计多日趋势
    pos_5d = sum(1 for r in results if r["5日主力净流入"] > 0)
    pos_today = sum(1 for r in results if r["主力净流入"] > 0)

    return {
        "type": snap_type,
        "snapshot_count": len(tss),
        "first_snapshot": first_ts,
        "last_snapshot": last_ts,
        "total_sectors": len(results),
        "total_main_inflow": total_inflow,
        "total_super_inflow": total_super,
        "total_large_inflow": total_large,
        "validation_errors": validation_errors,
        "new_sectors_not_in_first": skipped_new,
        "positive_sectors": pos_today,
        "positive_5d": pos_5d,
        "top_inflow": top_inflow,
        "bottom_outflow": bottom_outflow,
        "accelerating": accelerating,
        "decelerating": decelerating,
    }


def analyze_stock_flow(date_str: str, top_n: int = 20):
    """
    分析个股资金流 + 自底向上行业汇总。

    核心逻辑:
      - 读末帧快照（全量个股），首帧做对照计算日内增量
      - 末帧有但首帧无的个股: 保留，日内增量=0
      - 按个股"行业"字段自底向上汇总 → 行业资金排名（最准确）
    """
    tss = list_snapshots(date_str, "fund_flow")
    if len(tss) < 2:
        return None

    first_ts = tss[0]
    last_ts = tss[-1]

    first_rows = load_snapshot(date_str, "fund_flow", first_ts)
    last_rows = load_snapshot(date_str, "fund_flow", last_ts)

    if not first_rows or not last_rows:
        return None

    first_map = {r["代码"]: r for r in first_rows}

    results = []
    validation_errors = 0
    skipped_new = 0

    for r in last_rows:
        code = r["代码"]
        first_r = first_map.get(code)

        try:
            flow_now = parse_float(r.get("主力净流入", "0"))
            pct_now = parse_float(r.get("主力占比", "0"))
            chg_now = parse_float(r.get("涨跌幅", "0"))
            name = r.get("名称", "")
            industry = r.get("行业", "")
            mcap = parse_float(r.get("总市值", "0"))
            turnover = parse_float(r.get("换手率", "0"))
            volume = parse_float(r.get("成交额", "0"))
            super_flow = parse_float(r.get("超大单净流入", "0"))
            large_flow = parse_float(r.get("大单净流入", "0"))
            medium_flow = parse_float(r.get("中单净流入", "0"))
            small_flow = parse_float(r.get("小单净流入", "0"))
            flow_5d = parse_float(r.get("5日主力净流入", "0"))

            # 数据校验
            if not validate_main_flow(name, flow_now, super_flow, large_flow):
                validation_errors += 1

            # 日内增量: 首帧不存在则设为 0
            if first_r:
                flow_first = parse_float(first_r.get("主力净流入", "0"))
                intraday_delta = flow_now - flow_first
            else:
                intraday_delta = 0.0
                skipped_new += 1

            results.append({
                "代码": code,
                "名称": name,
                "行业": industry,
                "涨跌幅": chg_now,
                "总市值": mcap,
                "换手率": turnover,
                "成交额": volume,
                "主力净流入": flow_now,
                "主力占比": pct_now,
                "超大单净流入": super_flow,
                "大单净流入": large_flow,
                "中单净流入": medium_flow,
                "小单净流入": small_flow,
                "日内增量": intraday_delta,
                "5日主力净流入": flow_5d,
            })
        except Exception:
            continue

    # === 个股排名 ===
    results.sort(key=lambda x: x["主力净流入"], reverse=True)
    top_inflow = results[:top_n]
    bottom_outflow = sorted(results, key=lambda x: x["主力净流入"])[:top_n]
    accelerating = sorted(results, key=lambda x: x["日内增量"], reverse=True)[:top_n]

    # 主力占比排名（过滤微盘股: 成交额≥5000万）
    min_volume = 50_000_000
    meaningful = [r for r in results if r["成交额"] >= min_volume]
    top_by_pct = sorted(meaningful, key=lambda x: x["主力占比"], reverse=True)[:top_n]

    # === 行业自底向上汇总（最准确的行业资金流） ===
    sector_map: dict[str, dict] = {}
    for r in results:
        ind = r["行业"] or "未知"
        if ind not in sector_map:
            sector_map[ind] = {
                "行业": ind,
                "主力净流入": 0.0,
                "超大单净流入": 0.0,
                "大单净流入": 0.0,
                "中单净流入": 0.0,
                "小单净流入": 0.0,
                "总成交额": 0.0,
                "股票数": 0,
                "正流入股数": 0,
                "top_stocks": [],
            }
        s = sector_map[ind]
        s["主力净流入"] += r["主力净流入"]
        s["超大单净流入"] += r["超大单净流入"]
        s["大单净流入"] += r["大单净流入"]
        s["中单净流入"] += r["中单净流入"]
        s["小单净流入"] += r["小单净流入"]
        s["总成交额"] += r["成交额"]
        s["股票数"] += 1
        if r["主力净流入"] > 0:
            s["正流入股数"] += 1

    sector_ranking = sorted(sector_map.values(), key=lambda x: x["主力净流入"], reverse=True)

    # 每个行业 top 3 个股
    for s in sector_ranking:
        sector_stocks = [r for r in results if (r["行业"] or "未知") == s["行业"]]
        sector_stocks.sort(key=lambda x: x["主力净流入"], reverse=True)
        s["top_stocks"] = sector_stocks[:3]
        # 主力占比 = 主力净流入 / 总成交额
        s["主力占比"] = (s["主力净流入"] / s["总成交额"] * 100) if s["总成交额"] > 0 else 0.0

    # === 主力集中度 ===
    total_positive = sum(r["主力净流入"] for r in results if r["主力净流入"] > 0)
    top10_positive = sum(r["主力净流入"] for r in top_inflow if r["主力净流入"] > 0)
    concentration = top10_positive / total_positive if total_positive > 0 else 0

    # === 全市场统计 ===
    total_all = sum(r["主力净流入"] for r in results)
    pos_count = sum(1 for r in results if r["主力净流入"] > 0)
    neg_count = sum(1 for r in results if r["主力净流入"] < 0)
    # 价格涨跌统计（非主力流向）
    price_up = sum(1 for r in results if r["涨跌幅"] > 0)
    price_down = sum(1 for r in results if r["涨跌幅"] < 0)
    price_flat = sum(1 for r in results if r["涨跌幅"] == 0)

    return {
        "snapshot_count": len(tss),
        "first_snapshot": first_ts,
        "last_snapshot": last_ts,
        "total_stocks_in_last": len(last_rows),
        "total_stocks_analyzed": len(results),
        "new_stocks_not_in_first": skipped_new,
        "validation_errors": validation_errors,
        "positive_count": pos_count,
        "negative_count": neg_count,
        "price_up": price_up,
        "price_down": price_down,
        "price_flat": price_flat,
        "total_main_inflow_all_stocks": total_all,
        "all_stocks": results,  # 全量个股数据，供 summary 复用
        "top_inflow": top_inflow,
        "top_by_main_pct": top_by_pct,
        "accelerating": accelerating,
        "bottom_outflow": bottom_outflow,
        "sector_ranking": sector_ranking[:top_n],
        "all_sectors": sector_ranking,
        "concentration_top10": concentration,
    }


def format_amount(v: float) -> str:
    """格式化金额。"""
    if abs(v) >= 1e8:
        return f"{v/1e8:+.2f}亿"
    elif abs(v) >= 1e4:
        return f"{v/1e4:+.0f}万"
    else:
        return f"{v:+.0f}"


def format_pct(v: float) -> str:
    """格式化百分比。"""
    return f"{v:+.2f}%"


def print_separator(title: str = ""):
    """打印分隔线。"""
    width = 90
    if title:
        side = (width - len(title) - 2) // 2
        print(f"\n{'─' * side} {title} {'─' * side}")
    else:
        print(f"{'─' * width}")


def print_stock_table(rows: list[dict], title: str, show_industry: bool = True):
    """打印个股表格。"""
    print_separator(title)
    if show_industry:
        header = f"{'代码':<8} {'名称':<10} {'行业':<10} {'涨跌':>7} {'主力净流入':>12} {'主力占比':>7} {'超大单':>12} {'大单':>12} {'日内增量':>12}"
    else:
        header = f"{'代码':<8} {'名称':<10} {'涨跌':>7} {'主力净流入':>12} {'主力占比':>7} {'超大单':>12} {'大单':>12} {'日内增量':>12}"
    print(header)
    print("-" * len(header))
    for r in rows:
        if show_industry:
            print(
                f"{r['代码']:<8} {r['名称']:<10} {(r.get('行业', '')):<10} "
                f"{format_pct(r['涨跌幅']):>7} {format_amount(r['主力净流入']):>12} "
                f"{format_pct(r['主力占比']):>7} {format_amount(r['超大单净流入']):>12} "
                f"{format_amount(r['大单净流入']):>12} {format_amount(r['日内增量']):>12}"
            )
        else:
            chg = r.get("涨跌幅", 0)
            print(
                f"{r['代码']:<8} {r['名称']:<10} "
                f"{format_pct(chg):>7} {format_amount(r['主力净流入']):>12} "
                f"{format_pct(r['主力占比']):>7} {format_amount(r['超大单净流入']):>12} "
                f"{format_amount(r['大单净流入']):>12} {format_amount(r['日内增量']):>12}"
            )


def print_sector_table(rows: list[dict], title: str):
    """打印板块表格。"""
    print_separator(title)
    header = f"{'代码':<8} {'名称':<12} {'涨跌':>7} {'主力净流入':>12} {'主力占比':>7} {'超大单':>12} {'大单':>12} {'日内增量':>12}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['代码']:<8} {r['名称']:<12} "
            f"{format_pct(r['涨跌幅']):>7} {format_amount(r['主力净流入']):>12} "
            f"{format_pct(r['主力占比']):>7} {format_amount(r['超大单净流入']):>12} "
            f"{format_amount(r['大单净流入']):>12} {format_amount(r['日内增量']):>12}"
        )


def print_sector_stock_table(rows: list[dict], title: str):
    """打印行业汇总表格（从个股自底向上聚合）。"""
    print_separator(title)
    header = f"{'行业':<12} {'主力净流入':>12} {'主力占比':>8} {'超大单':>12} {'大单':>12} {'股票数':>6} {'正流比':>6}  TOP3个股"
    print(header)
    print("-" * len(header))
    for s in rows:
        pos_ratio = f"{s['正流入股数']}/{s['股票数']}"
        tops = ", ".join(f"{st['名称']}({format_amount(st['主力净流入'])})" for st in s["top_stocks"])
        print(
            f"{s['行业']:<12} {format_amount(s['主力净流入']):>12} "
            f"{s['主力占比']:>7.2f}% {format_amount(s['超大单净流入']):>12} "
            f"{format_amount(s['大单净流入']):>12} {s['股票数']:>6} {pos_ratio:>6}  {tops}"
        )


def print_summary_box(date_str: str, concept_result, industry_result, stock_result):
    """打印总览摘要框。"""
    print()
    print("╔" + "═" * 78 + "╗")
    print(f"║  主力资金流向分析报告 — {date_str}{'':>52}║")
    print(f"║  分析时间: {datetime.now(BJS_TZ).strftime('%Y-%m-%d %H:%M:%S')} BJT{'':>46}║")
    print("╠" + "═" * 78 + "╣")

    if stock_result:
        total = stock_result["total_main_inflow_all_stocks"]
        direction = "流入" if total > 0 else "流出"
        pos = stock_result["positive_count"]
        neg = stock_result["negative_count"]
        conc = stock_result.get("concentration_top10", 0) * 100
        val_err = stock_result.get("validation_errors", 0)
        new_stocks = stock_result.get("new_stocks_not_in_first", 0)
        print(f"║  全市场: {stock_result['total_stocks_analyzed']}只个股 | 主力净{direction}: {format_amount(total):>10}{'':>32}║")
        print(f"║  正流入: {pos}只 | 负流出: {neg}只 | 主力集中度(TOP10): {conc:.1f}%{'':>28}║")
        if val_err:
            print(f"║  ⚠ 数据校验异常: {val_err}行{'':>60}║")
        if new_stocks:
            print(f"║  首帧缺失个股: {new_stocks}只 (日内增量=0){'':>51}║")

        print(f"║  --- 个股TOP3 ---{'':>61}║")
        for i, s in enumerate(stock_result["top_inflow"][:3], 1):
            print(f"║    #{i} {s['名称']:<10} ({s.get('行业', ''):<8}) {format_amount(s['主力净流入']):>12} ({format_pct(s['涨跌幅'])}){'':>15}║")

        # 行业汇总 TOP3
        if stock_result.get("all_sectors"):
            print(f"║  --- 行业汇总TOP3 (个股聚合) ---{'':>47}║")
            for i, s in enumerate(stock_result["all_sectors"][:3], 1):
                print(f"║    #{i} {s['行业']:<14} {format_amount(s['主力净流入']):>12} ({s['正流入股数']}/{s['股票数']}只正流){'':>18}║")

    if concept_result:
        total = concept_result["total_main_inflow"]
        direction = "流入" if total > 0 else "流出"
        print(f"║  --- 概念板块参考 (API聚合) ---{'':>48}║")
        print(f"║  {concept_result['total_sectors']}个概念 | 主力净{direction}: {format_amount(total):>10} (含重叠){'':>29}║")
        for i, s in enumerate(concept_result["top_inflow"][:3], 1):
            print(f"║    #{i} {s['名称']:<14} {format_amount(s['主力净流入']):>12} ({format_pct(s['涨跌幅'])}){'':>20}║")

    if industry_result:
        print(f"║  --- 行业板块参考 (API聚合) ---{'':>48}║")
        total = industry_result["total_main_inflow"]
        direction = "流入" if total > 0 else "流出"
        print(f"║  {industry_result['total_sectors']}个行业 | 主力净{direction}: {format_amount(total):>10}{'':>33}║")
        for i, s in enumerate(industry_result["top_inflow"][:3], 1):
            print(f"║    #{i} {s['名称']:<14} {format_amount(s['主力净流入']):>12} ({format_pct(s['涨跌幅'])}){'':>20}║")

    print("╚" + "═" * 78 + "╝")


def red_text(text: str) -> str:
    """终端红色文字。"""
    return f"\033[91m{text}\033[0m"


def green_text(text: str) -> str:
    """终端绿色文字。"""
    return f"\033[92m{text}\033[0m"


def compute_cross_reference(concept_result, industry_result, stock_result):
    """
    交叉参考分析: 概念板块TOP + 行业板块TOP(API) + 行业汇总TOP(个股聚合) 三重验证。
    """
    if not all([concept_result, industry_result, stock_result]):
        return {}

    concept_top = {s["名称"] for s in concept_result["top_inflow"][:10]}
    industry_api_top = {s["名称"] for s in industry_result["top_inflow"][:10]}
    industry_agg_top = {s["行业"] for s in stock_result["all_sectors"][:10]}

    # 行业API TOP ∩ 个股聚合行业 TOP = 被两个独立数据源验证
    verified = industry_api_top & industry_agg_top

    # 概念板块中，哪些对应的行业也在流入
    concept_verified = []
    for s in concept_result["top_inflow"][:10]:
        # 概念板块名称可能和行业名不一致，直接用概念名
        concept_verified.append(s["名称"])

    return {
        "verified_industries": verified,
        "concept_top_names": concept_top,
    }


def run_analysis(date_str: str, top_n: int = 15):
    """执行完整分析并打印报告。"""
    has_data = (
        list_snapshots(date_str, "concept_flow")
        or list_snapshots(date_str, "industry_flow")
        or list_snapshots(date_str, "fund_flow")
    )
    if not has_data:
        print(f"❌ 日期 {date_str} 无盘中快照数据")
        return

    # 1. 个股分析（核心，最先做）
    print(f"  分析个股资金...", end=" ", flush=True)
    stock_result = analyze_stock_flow(date_str, top_n)
    if stock_result:
        val_info = f", 校验异常{stock_result.get('validation_errors', 0)}行" if stock_result.get("validation_errors") else ""
        print(f"{stock_result['snapshot_count']}个快照, {stock_result['total_stocks_analyzed']}只股票{val_info}")
    else:
        print("无数据")

    # 2. 概念板块
    print(f"  分析概念板块...", end=" ", flush=True)
    concept_result = analyze_sector_flow(date_str, "concept_flow", top_n)
    if concept_result:
        print(f"{concept_result['snapshot_count']}个快照, {concept_result['total_sectors']}个概念")
    else:
        print("无数据")

    # 3. 行业板块
    print(f"  分析行业板块...", end=" ", flush=True)
    industry_result = analyze_sector_flow(date_str, "industry_flow", top_n)
    if industry_result:
        print(f"{industry_result['snapshot_count']}个快照, {industry_result['total_sectors']}个行业")
    else:
        print("无数据")

    # 4. 交叉参考
    cross_ref = compute_cross_reference(concept_result, industry_result, stock_result)

    # ==== 打印报告 ====
    print_summary_box(date_str, concept_result, industry_result, stock_result)

    # ==== 核心：行业资金聚集排名（个股自底向上汇总） ====
    if stock_result and stock_result.get("all_sectors"):
        print_sector_stock_table(
            stock_result["all_sectors"][:top_n],
            "★ 行业资金排名（个股自底向上聚合 — 最准确）"
        )

    # ==== 个股 TOP ====
    if stock_result:
        print_stock_table(stock_result["top_inflow"], "个股 — 主力净流入 TOP")
        print_stock_table(stock_result["top_by_main_pct"], "个股 — 主力占比 TOP（成交额≥5000万）")
        print_stock_table(stock_result["accelerating"], "个股 — 日内加速流入 TOP")
        print_stock_table(stock_result["bottom_outflow"], "个股 — 主力净流出 TOP")

    # ==== 概念板块 ====
    if concept_result:
        print_sector_table(concept_result["top_inflow"], "概念板块 — 主力净流入 TOP (API聚合)")
        print_sector_table(concept_result["accelerating"], "概念板块 — 日内加速流入 TOP")

    # ==== 行业板块 ====
    if industry_result:
        print_sector_table(industry_result["top_inflow"], "行业板块 — 主力净流入 TOP (API聚合)")
        print_sector_table(industry_result["accelerating"], "行业板块 — 日内加速流入 TOP")

    # ==== 多维度验证 ====
    if cross_ref.get("verified_industries"):
        print_separator("多维度验证 — 行业API + 个股聚合 共振板块")
        for ind in sorted(cross_ref["verified_industries"]):
            ind_data = next((s for s in stock_result["all_sectors"] if s["行业"] == ind), None)
            if ind_data:
                print(f"  ✅ {ind}: 个股聚合{format_amount(ind_data['主力净流入'])}, "
                      f"{ind_data['正流入股数']}/{ind_data['股票数']}只正流, "
                      f"主力占比{ind_data['主力占比']:.1f}%")

    # ==== 快照概览 ====
    print_separator("数据快照概览")
    for stype in ["concept_flow", "industry_flow", "fund_flow"]:
        tss = list_snapshots(date_str, stype)
        if tss:
            print(f"  {stype}: {tss[0][:2]}:{tss[0][2:4]}:{tss[0][4:]} → "
                  f"{tss[-1][:2]}:{tss[-1][2:4]}:{tss[-1][4:]} ({len(tss)}个快照)")

    # ==== 数据质量报告 ====
    if stock_result:
        errors = []
        if stock_result.get("validation_errors"):
            errors.append(f"主力≠超大+大单: {stock_result['validation_errors']}行")
        if stock_result.get("new_stocks_not_in_first"):
            errors.append(f"首帧缺失: {stock_result['new_stocks_not_in_first']}只")
        if errors:
            print(f"\n  ⚠ 数据质量: {'; '.join(errors)}")

    print()


def watch_mode():
    """持续监控模式。"""
    import time
    import signal

    running = True

    def _handler(sig, frame):
        nonlocal running
        print("\n  收到停止信号...")
        running = False

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)

    print("╔" + "═" * 58 + "╗")
    print("║  主力资金流向监控模式{'':>42}║")
    print("║  盘中(9:30-15:00): 每5分钟分析{'':>35}║")
    print("║  盘后: 每30分钟分析{'':>42}║")
    print("╚" + "═" * 58 + "╝")

    while running:
        now = datetime.now(BJS_TZ)
        date_str = now.strftime("%Y%m%d")

        if now.weekday() >= 5:
            print(f"  {now.strftime('%H:%M:%S')} 非交易日，等待...")
            time.sleep(1800)
            continue

        t = (now.hour, now.minute)
        in_morning = (9, 30) <= t < (11, 35)
        in_afternoon = (13, 0) <= t < (15, 5)
        in_session = in_morning or in_afternoon

        if in_session:
            run_analysis(date_str)
            time.sleep(300)
        else:
            has_data = bool(list_snapshots(date_str, "fund_flow") or list_snapshots(date_str, "concept_flow"))
            if has_data:
                print(f"  {now.strftime('%H:%M:%S')} 盘后分析...")
                run_analysis(date_str)
                time.sleep(1800)
            else:
                print(f"  {now.strftime('%H:%M:%S')} 等待数据...")
                time.sleep(600)


def load_scores(date_str: str) -> dict:
    """加载 scores.csv, 返回 {code: {综合信号, 启动信号, 综合得分, ...}}"""
    path = RESEARCH_ROOT / date_str / "scores.csv"
    if not path.exists():
        return {}
    score_map = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = r.get("代码", "").strip()
            if not code:
                continue
            score_map[code] = {
                "综合信号": r.get("综合信号", ""),
                "启动信号": r.get("启动信号", ""),
                "综合得分": parse_float(r.get("综合得分", "0")),
                "启动得分": parse_float(r.get("启动得分", "0")),
                "短线得分": parse_float(r.get("短线得分", "0")),
                "中线得分": parse_float(r.get("中线得分", "0")),
            }
    return score_map


def rank_percentile(values: list[float], v: float) -> float:
    """计算 v 在 values 中的百分位排名 (0.0-1.0)。"""
    better = sum(1 for x in values if x < v)
    return better / len(values) if values else 0.5


def score_potential_stocks(stock_results: list[dict], sector_ranking: list[dict], top_sector_names: set[str]):
    """
    潜力股评分 = 主力净流入排名 + 日内增量排名 + 主力占比排名 + 行业共振 + 涨幅合理性

    Filters: 成交额 >= 5000万, 主力净流入 > 0, 非ST, 涨跌幅 < 9.5%
    """
    min_volume = 50_000_000
    candidates = [
        r for r in stock_results
        if r["成交额"] >= min_volume
        and r["主力净流入"] > 0
        and "ST" not in r["名称"]
        and r["涨跌幅"] < 9.5  # 排除已涨停，无买入机会
        and r["日内增量"] > -1e8  # 不在日内大幅流出
    ]

    if not candidates:
        return []

    # Rank percentile vectors
    inflows = [r["主力净流入"] for r in candidates]
    deltas = [r["日内增量"] for r in candidates]
    pcts = [r["主力占比"] for r in candidates]

    for r in candidates:
        inflow_score = rank_percentile(inflows, r["主力净流入"]) * 0.30
        delta_score = rank_percentile(deltas, r["日内增量"]) * 0.25
        pct_score = rank_percentile(pcts, r["主力占比"]) * 0.20

        # 行业共振: sector in top 5
        sector_bonus = 0.15 if r["行业"] in top_sector_names else 0.0

        # 涨幅合理性: 3%-8% sweet spot, <2% or >8% slightly penalized
        chg = r["涨跌幅"]
        if 3 <= chg <= 8:
            chg_score = 0.10
        elif 2 <= chg < 3 or 8 < chg <= 9.5:
            chg_score = 0.05
        else:
            chg_score = 0.0

        r["潜力评分"] = inflow_score + delta_score + pct_score + sector_bonus + chg_score

    candidates.sort(key=lambda x: x["潜力评分"], reverse=True)
    return candidates


def score_buy_picks(potential_stocks: list[dict], verified_sectors: set[str], scores_map: dict):
    """
    推荐买入 = 潜力股基础上叠加:
      - 主力占比 >= 3%
      - 日内增量 > 0 (资金正在加速流入)
      - 涨跌幅 2%~9% (有空间且非追涨停)
      - 优先选择行业共振板块内个股
      - 有 scores.csv 启动信号者加分

    Returns sorted by composite buy score.
    """
    picks = []
    for r in potential_stocks:
        score = r.get("潜力评分", 0)

        # 过滤条件
        if r["主力占比"] < 3.0:
            continue
        if r["日内增量"] <= 0:
            continue
        if r["涨跌幅"] < 2.0 or r["涨跌幅"] > 9.0:
            continue

        # 行业共振加分
        if r["行业"] in verified_sectors:
            score += 0.05

        # scores.csv 启动信号加分
        sig = scores_map.get(r["代码"], {})
        if sig.get("启动信号") == "P4_start":
            score += 0.10
        elif sig.get("综合信号") and "P" in str(sig.get("综合信号", "")):
            score += 0.05

        # 短线/中线得分
        if sig.get("短线得分", 0) > 0.7:
            score += 0.05
        if sig.get("中线得分", 0) > 0.7:
            score += 0.03

        r["推荐评分"] = score
        picks.append(r)

    picks.sort(key=lambda x: x["推荐评分"], reverse=True)
    return picks


def print_trading_summary(date_str: str, stock_result, concept_result, industry_result):
    """打印简洁交易摘要: 板块TOP3 + 主力TOP10 + 潜力TOP10 + 推荐买入TOP10。"""
    if not stock_result:
        print("❌ 无个股数据")
        return

    # Aggregate all results from stock analysis
    all_stocks = []
    for s in stock_result.get("all_sectors", []):
        for st in s.get("top_stocks", []):
            pass
    # Reconstruct full stock list from sectors
    # Actually, we need the raw stock results. Let's re-derive from the sector data.

    # Build full stock list from sector_ranking
    stock_list = []
    seen = set()
    for s in stock_result.get("all_sectors", []):
        for st in s.get("top_stocks", []):
            if st["代码"] not in seen:
                stock_list.append(st)
                seen.add(st["代码"])

    # Get top sector names
    all_sectors = stock_result.get("all_sectors", [])
    top3_sectors = all_sectors[:3]
    top5_names = {s["行业"] for s in all_sectors[:5]}

    # Cross-ref verified sectors
    verified = set()
    if industry_result and stock_result:
        ind_top = {s["名称"] for s in industry_result.get("top_inflow", [])[:10]}
        agg_top = {s["行业"] for s in all_sectors[:10]}
        verified = ind_top & agg_top

    # 复用 analyze_stock_flow 的全量个股数据（含日内增量）
    all_results = stock_result.get("all_stocks", [])

    # Scores integration
    scores_map = load_scores(date_str)

    # === 潜力股评分 ===
    potential = score_potential_stocks(all_results, all_sectors, top5_names)

    # === 推荐买入 ===
    buy_picks = score_buy_picks(potential, verified, scores_map)

    # === 主力流入 TOP10 ===
    all_sorted = sorted(all_results, key=lambda x: x["主力净流入"], reverse=True)
    # Filter: 成交额 >= 5000万
    main_top10 = [r for r in all_sorted if r["成交额"] >= 50_000_000][:10]

    # === PRINT ===
    now = datetime.now(BJS_TZ)
    print()
    print("╔" + "═" * 68 + "╗")
    print(f"║  📊 交易摘要 — {date_str}  {now.strftime('%H:%M:%S')} BJT{'':>30}║")
    print(f"║  快照: {stock_result['first_snapshot'][:2]}:{stock_result['first_snapshot'][2:4]} → "
          f"{stock_result['last_snapshot'][:2]}:{stock_result['last_snapshot'][2:4]} "
          f"({stock_result['snapshot_count']}帧){'':>20}║")
    total = stock_result["total_main_inflow_all_stocks"]
    direction = "流入" if total > 0 else "流出"
    print(f"║  全市场主力净{direction}: {format_amount(total)}{'':>40}║")
    print("╠" + "═" * 68 + "╣")

    # --- 板块TOP3 ---
    print("║  ▌ 主力流入 TOP3 板块 (个股聚合){'':>32}║")
    print("║  " + "─" * 64 + "║")
    for i, s in enumerate(top3_sectors, 1):
        pos_ratio = f"{s['正流入股数']}/{s['股票数']}"
        marker = " 🔥" if s["主力占比"] > 8 else ""
        print(f"║  #{i} {s['行业']:<14} {format_amount(s['主力净流入']):>12}  "
              f"占比{s['主力占比']:.1f}%  正流{pos_ratio}{marker}{'':>8}║")

    # --- 个股主力TOP10 ---
    print("╠" + "═" * 68 + "╣")
    print("║  ▌ 主力净流入 TOP10 个股{'':>40}║")
    print("║  " + "─" * 64 + "║")
    for i, r in enumerate(main_top10, 1):
        print(f"║  #{i:<2} {r['代码']:<8} {r['名称']:<10} {r['行业']:<10} "
              f"{format_amount(r['主力净流入']):>12}  {format_pct(r['涨跌幅']):>6}{'':>2}║")

    # --- 潜力股TOP10 ---
    print("╠" + "═" * 68 + "╣")
    print("║  ▌ 最优潜力 TOP10 (主力+加速+共振+涨幅){'':>24}║")
    print("║  " + "─" * 64 + "║")
    for i, r in enumerate(potential[:10], 1):
        sig_str = ""
        code_scores = scores_map.get(r["代码"], {})
        if code_scores.get("启动信号") == "P4_start":
            sig_str = " 🚀启动"
        elif code_scores.get("综合信号") and "P" in str(code_scores.get("综合信号", "")):
            sig_str = " 📶信号"
        print(f"║  #{i:<2} {r['代码']:<8} {r['名称']:<10} {r['行业']:<10} "
              f"{format_amount(r['主力净流入']):>10}  "
              f"评分{r['潜力评分']:.3f}{sig_str}{'':>4}║")

    # --- 推荐买入TOP10 ---
    print("╠" + "═" * 68 + "╣")
    print("║  ▌ 推荐买入 TOP10 (过滤+共振+信号验证){'':>24}║")
    print("║  " + "─" * 64 + "║")
    if buy_picks:
        for i, r in enumerate(buy_picks[:10], 1):
            reasons = []
            if r["行业"] in verified:
                reasons.append("共振")
            code_scores = scores_map.get(r["代码"], {})
            if code_scores.get("启动信号") == "P4_start":
                reasons.append("启动")
            if code_scores.get("短线得分", 0) > 0.7:
                reasons.append("短线强")
            reason_str = "+".join(reasons) if reasons else ""
            print(f"║  #{i:<2} {r['代码']:<8} {r['名称']:<10} {r['行业']:<10} "
                  f"{format_amount(r['主力净流入']):>10}  "
                  f"{format_pct(r['涨跌幅']):>6}  {reason_str}{'':>8}║")
    else:
        print("║  (暂无满足条件的推荐，等待更多快照数据){'':>22}║")

    print("╚" + "═" * 68 + "╝")
    print()

    # Quick concept highlight
    if concept_result:
        top_concepts = concept_result["top_inflow"][:5]
        parts = [f"{c['名称']} {format_amount(c['主力净流入'])}" for c in top_concepts]
        print(f"  概念热点: {' | '.join(parts)}")
    if verified:
        print(f"  共振板块: {' | '.join(sorted(verified))}")
    print()


def _load_prev_state() -> dict | None:
    """加载上次运行保存的状态，用于变化检测。"""
    state_file = PROJECT_ROOT / ".states" / "brief_last_state.json"
    if not state_file.exists():
        return None
    try:
        with open(state_file) as f:
            return json.load(f)
    except Exception:
        return None


def _save_state(state: dict):
    """保存当前状态供下次对比。"""
    state_dir = PROJECT_ROOT / ".states"
    state_dir.mkdir(exist_ok=True)
    state_file = state_dir / "brief_last_state.json"
    with open(state_file, "w") as f:
        json.dump(state, f, ensure_ascii=False, default=str)


def load_kline_indicators(codes: list[str]) -> dict:
    """
    为指定股票列表加载K线技术指标。
    Returns {code: {ma_position, momentum_5d, vol_ratio, ma_alignment, ...}}
    """
    kline_root = PROJECT_ROOT / "baostock_data" / "data" / "daily"
    if not kline_root.exists():
        return {}

    indicators = {}
    for code in codes:
        # Map code to baostock filename: 600547 -> sh.600547, 002475 -> sz.002475
        if code.startswith("6"):
            filename = f"sh.{code}.csv"
        elif code.startswith(("0", "3")):
            filename = f"sz.{code}.csv"
        elif code.startswith(("4", "8", "9")):
            filename = f"bj.{code}.csv"
        else:
            continue

        path = kline_root / filename
        if not path.exists():
            continue

        try:
            with open(path, encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            if len(rows) < 30:
                continue

            # 取最近30日
            recent = rows[-30:]
            closes = [parse_float(r["收盘"]) for r in recent]
            volumes = [parse_float(r["成交量"]) for r in recent]
            highs = [parse_float(r["最高"]) for r in recent]
            lows = [parse_float(r["最低"]) for r in recent]

            if not closes or closes[-1] <= 0:
                continue

            # MA计算
            def ma(data, n):
                if len(data) < n:
                    return None
                return sum(data[-n:]) / n

            ma5 = ma(closes, 5)
            ma10 = ma(closes, 10)
            ma20 = ma(closes, 20)
            ma60 = ma(closes, 30)  # 用30日近似60日

            cur_close = closes[-1]

            # 均线位置: 价格在MA20之上/之下
            above_ma20 = cur_close > ma20 if ma20 else None
            above_ma5 = cur_close > ma5 if ma5 else None

            # 均线排列: 多头(MA5>MA10>MA20) / 空头 / 交叉
            if ma5 and ma10 and ma20:
                if ma5 > ma10 > ma20:
                    alignment = "多头排列"
                elif ma5 < ma10 < ma20:
                    alignment = "空头排列"
                elif ma5 > ma10 and ma10 < ma20:
                    alignment = "金叉初期"
                else:
                    alignment = "震荡"
            else:
                alignment = "数据不足"

            # 5日动量
            if len(closes) >= 6:
                momentum_5d = (closes[-1] - closes[-6]) / closes[-6] * 100
            else:
                momentum_5d = 0.0

            # 量比: 近5日均量 vs 近20日均量
            vol_5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0
            vol_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 1
            vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1.0

            # 近5日最高价位置
            high_5 = max(highs[-5:]) if len(highs) >= 5 else cur_close
            low_5 = min(lows[-5:]) if len(lows) >= 5 else cur_close
            price_position = (cur_close - low_5) / (high_5 - low_5) if high_5 > low_5 else 0.5

            # 近20日最高/最低
            high_20 = max(highs[-20:]) if len(highs) >= 20 else cur_close
            low_20 = min(lows[-20:]) if len(lows) >= 20 else cur_close
            pos_20d = (cur_close - low_20) / (high_20 - low_20) if high_20 > low_20 else 0.5

            # MA20斜率（5日）
            ma20_5d_ago = ma(closes[:-5], 20) if len(closes) >= 25 else None
            ma20_slope = ((ma20 - ma20_5d_ago) / ma20_5d_ago * 100) if ma20 and ma20_5d_ago and ma20_5d_ago > 0 else 0.0

            indicators[code] = {
                "close": cur_close,
                "ma5": ma5,
                "ma10": ma10,
                "ma20": ma20,
                "above_ma20": above_ma20,
                "above_ma5": above_ma5,
                "alignment": alignment,
                "momentum_5d": momentum_5d,
                "vol_ratio": vol_ratio,
                "price_position": price_position,
                "pos_20d": pos_20d,
                "ma20_slope": ma20_slope,
                "has_kline": True,
            }
        except Exception:
            continue

    return indicators


def kline_enhance_score(flow_data: dict, kline: dict | None) -> dict:
    """
    综合主力资金 + K线技术面，给出增强评分。
    Returns enhanced score dict with涨幅概率 and 持续流入概率.
    """
    if not kline or not kline.get("has_kline"):
        return {"涨幅概率": None, "持续流入概率": None, "技术评级": "无K线数据"}

    score = 0.0
    reasons = []

    # 1. 均线位置 (25分)
    if kline["above_ma20"]:
        if kline["above_ma5"]:
            score += 25
            reasons.append("价上MA5/MA20")
        else:
            score += 15
            reasons.append("价上MA20")
    elif kline["above_ma5"]:
        score += 10
    else:
        score += 0

    # 2. 均线排列 (25分)
    align = kline["alignment"]
    if align == "多头排列":
        score += 25
        reasons.append("多头排列")
    elif align == "金叉初期":
        score += 18
        reasons.append("金叉初期")
    elif align == "震荡":
        score += 10
    else:
        score += 0

    # 3. 量比 (20分)
    vol_r = kline["vol_ratio"]
    if 1.1 <= vol_r <= 2.5:
        score += 20
        reasons.append("温和放量")
    elif vol_r > 2.5:
        score += 12
        reasons.append("巨量")
    elif 0.8 <= vol_r < 1.1:
        score += 10
    else:
        score += 5

    # 4. MA20斜率 (15分)
    slope = kline["ma20_slope"]
    if slope > 1:
        score += 15
        reasons.append("MA20↑陡")
    elif slope > 0:
        score += 10
        reasons.append("MA20↑")
    elif slope > -2:
        score += 5
    else:
        score += 0

    # 5. 短期动量 (15分)
    mom = kline["momentum_5d"]
    if 2 <= mom <= 8:
        score += 15
        reasons.append("5日温和涨")
    elif 0 < mom < 2:
        score += 10
    elif mom > 8:
        score += 5
        reasons.append("短线过热")
    else:
        score += 0

    # 综合评级
    if score >= 80:
        tech_rating = "★★★ 强势"
    elif score >= 60:
        tech_rating = "★★ 偏强"
    elif score >= 40:
        tech_rating = "★ 中性"
    else:
        tech_rating = "弱势"

    # 涨幅概率: 技术评分映射到概率
    rise_prob = min(0.85, max(0.10, score / 100))

    # 持续流入概率: 技术面好 + 主力占比高 → 持续概率高
    flow_pct = flow_data.get("主力占比", 0)
    flow_trend = flow_data.get("日内增量", 0)
    continue_prob = min(0.90, max(0.05,
        (score / 100) * 0.50  # 技术面 50%
        + (min(flow_pct, 20) / 20) * 0.30  # 主力占比 30%
        + (0.20 if flow_trend > 0 else 0.10)  # 趋势 20%
    ))

    return {
        "涨幅概率": rise_prob,
        "持续流入概率": continue_prob,
        "技术评级": tech_rating,
        "技术分": score,
        "信号": "+".join(reasons) if reasons else "—",
        "均线": f"MA5={kline['ma5']:.1f}" if kline.get("ma5") else "",
        "量比": f"{kline['vol_ratio']:.1f}x",
        "位置": f"{kline['pos_20d']*100:.0f}%",
    }


def detect_stealth_inflow(date_str: str, stock_result) -> list[dict]:
    """
    检测偷偷流入个股: 跨多帧持续正流入 + 低涨幅 + 高主力占比 + 换手适中。
    Returns ranked list of stealth stocks.
    """
    tss = list_snapshots(date_str, "fund_flow")
    if len(tss) < 3:
        return []

    # 取最后3帧
    last3_ts = tss[-3:]
    frames = []
    for ts in last3_ts:
        rows = load_snapshot(date_str, "fund_flow", ts)
        frame = {}
        for r in rows:
            frame[r["代码"]] = {
                "名称": r.get("名称", ""),
                "行业": r.get("行业", ""),
                "主力净流入": parse_float(r.get("主力净流入", "0")),
                "主力占比": parse_float(r.get("主力占比", "0")),
                "涨跌幅": parse_float(r.get("涨跌幅", "0")),
                "成交额": parse_float(r.get("成交额", "0")),
                "换手率": parse_float(r.get("换手率", "0")),
            }
        frames.append(frame)

    latest = frames[-1]
    results = []
    for code, cur in latest.items():
        # 过滤: 3帧全部正流入
        if any(code not in f or f[code]["主力净流入"] <= 0 for f in frames):
            continue
        # 涨幅 < 5%, > -2% (不要跌的)
        if cur["涨跌幅"] >= 5 or cur["涨跌幅"] <= -2:
            continue
        # 主力占比 >= 3%
        if cur["主力占比"] < 3:
            continue
        # 成交额 >= 5000万
        if cur["成交额"] < 5e7:
            continue
        # 换手率 0.5%-8%
        if cur["换手率"] < 0.5 or cur["换手率"] > 8:
            continue

        flows = [f[code]["主力净流入"] for f in frames]
        trend = flows[-1] - flows[0]  # 流速增量
        avg_flow = sum(flows) / len(flows)

        # 偷偷评分: 占比权重40 + 趋势权重35 + 稳定性25
        stability = 1.0 - (max(flows) - min(flows)) / max(abs(avg_flow), 1)
        stealth_score = (
            cur["主力占比"] * 0.40
            + (1.0 if trend > 0 else 0.5) * 0.35
            + max(0, stability) * 0.25
        )

        results.append({
            "代码": code,
            "名称": cur["名称"],
            "行业": cur["行业"],
            "主力净流入": cur["主力净流入"],
            "主力占比": cur["主力占比"],
            "涨跌幅": cur["涨跌幅"],
            "换手率": cur["换手率"],
            "流速增量": trend,
            "偷偷评分": stealth_score,
        })

    results.sort(key=lambda x: x["偷偷评分"], reverse=True)
    return results


def detect_stealth_sectors(stealth_stocks: list[dict]) -> list[dict]:
    """按行业汇总偷偷流入个股。"""
    sector_map: dict[str, dict] = {}
    for s in stealth_stocks:
        ind = s["行业"] or "未知"
        if ind not in sector_map:
            sector_map[ind] = {"行业": ind, "个股数": 0, "主力净流入": 0.0, "top3": []}
        sector_map[ind]["个股数"] += 1
        sector_map[ind]["主力净流入"] += s["主力净流入"]
        sector_map[ind]["top3"].append(s["名称"])
    for v in sector_map.values():
        v["top3"] = v["top3"][:3]
    return sorted(sector_map.values(), key=lambda x: x["主力净流入"], reverse=True)


def print_brief_insight(date_str: str, stock_result, concept_result, industry_result):
    """打印简洁洞察：对比上次运行，突出变化和趋势。"""
    if not stock_result:
        print("无数据")
        return

    tss = list_snapshots(date_str, "fund_flow")
    frame_count = len(tss)
    last_ts = stock_result.get("last_snapshot", "??????")
    ts_str = f"{last_ts[:2]}:{last_ts[2:4]}"

    total = stock_result["total_main_inflow_all_stocks"]
    direction = "流入" if total > 0 else "流出"
    price_up = stock_result.get("price_up", 0)
    price_down = stock_result.get("price_down", 0)

    # 板块排名
    sectors = stock_result.get("all_sectors", [])[:10]
    sector_map = {s["行业"]: s for s in sectors}
    top3 = sectors[:3]

    # 个股
    all_stocks = stock_result.get("all_stocks", [])
    top_stocks = sorted(all_stocks, key=lambda x: x["主力净流入"], reverse=True)[:5]

    # 概念
    concept_top = concept_result["top_inflow"][:5] if concept_result else []

    # ---- 加载上次状态，计算变化 ----
    prev = _load_prev_state()
    insights = []

    # 板块排名变化
    prev_sectors = {}
    if prev and prev.get("date") == date_str:
        prev_sectors = {s["行业"]: i for i, s in enumerate(prev.get("sectors", []))}
        prev_top3 = {s["行业"] for s in prev.get("sectors", [])[:3]}

        # 新进入TOP3的板块
        curr_top3 = {s["行业"] for s in top3}
        new_in_top3 = curr_top3 - prev_top3
        dropped_from_top3 = prev_top3 - curr_top3
        for name in new_in_top3:
            s = sector_map.get(name, {})
            insights.append(f"{name} +{format_amount(s.get('主力净流入', 0))}冲至TOP3")
        for name in dropped_from_top3:
            idx = next((i for i, s in enumerate(sectors) if s["行业"] == name), -1)
            insights.append(f"{name} 跌出TOP3（现第{idx+1}）" if idx >= 0 else f"{name} 跌出TOP3")

        # 排名跃升（上升>=3位）
        for i, s in enumerate(sectors):
            prev_rank = prev_sectors.get(s["行业"], 99)
            if prev_rank - i >= 3:
                insights.append(f"{s['行业']} 排名跃升 {prev_rank+1}→{i+1}")

        # 净流出变化
        prev_total = prev.get("total_main", 0)
        delta_total = total - prev_total
        if delta_total > 5e8:
            insights.append(f"主力回流 {format_amount(delta_total)}")
        elif delta_total < -5e8:
            insights.append(f"主力加速流出 {format_amount(abs(delta_total))}")

        # 新股进入TOP5
        prev_top_codes = {s["代码"] for s in prev.get("top_stocks", [])}
        for r in top_stocks[:3]:
            if r["代码"] not in prev_top_codes:
                tag = "涨停" if r["涨跌幅"] >= 9.9 else f"+{format_pct(r['涨跌幅'])}"
                insights.append(f"{r['名称']} 新进个股TOP3（{tag}）")

    # ---- 当前状态 ----
    print()
    print("─" * 70)
    print(f"{ts_str}，{frame_count}帧。", end=" ")
    print(f"主力净{direction} {format_amount(total)}，涨{price_up}家/跌{price_down}家。")

    # TOP3板块 + 亮点
    sector_line = []
    for s in top3:
        fire = "🔥" if s["主力占比"] > 8 else ""
        sector_line.append(f"{s['行业']} {format_amount(s['主力净流入'])}{fire}")
    print(f"板块TOP3: {' | '.join(sector_line)}")

    # 个股TOP5
    stock_line = []
    for r in top_stocks:
        chg = r["涨跌幅"]
        if chg >= 9.9:
            label = f"{r['名称']}涨停"
        elif chg <= -9.9:
            label = f"{r['名称']}跌停"
        else:
            label = f"{r['名称']}{format_pct(chg)}"
        stock_line.append(f"{label} {format_amount(r['主力净流入'])}")
    print(f"个股TOP5: {' | '.join(stock_line)}")

    # 概念TOP3
    if concept_top:
        parts = [f"{c['名称']} {format_amount(c['主力净流入'])}" for c in concept_top[:3]]
        print(f"概念TOP3: {' | '.join(parts)}")

    # 变化洞察
    if insights:
        print(f"变化: {'; '.join(insights)}。")

    # 推荐
    all_sectors = stock_result.get("all_sectors", [])
    top5_names = {s["行业"] for s in all_sectors[:5]}
    verified = set()
    if industry_result:
        ind_top = {s["名称"] for s in industry_result.get("top_inflow", [])[:10]}
        agg_top = {s["行业"] for s in all_sectors[:10]}
        verified = ind_top & agg_top
    scores_map = load_scores(date_str)
    potential = score_potential_stocks(all_stocks, all_sectors, top5_names)
    buy_picks = score_buy_picks(potential, verified, scores_map)[:5]
    if buy_picks:
        parts = []
        for r in buy_picks:
            sig = ""
            code_scores = scores_map.get(r["代码"], {})
            if code_scores.get("启动信号") == "P4_start":
                sig = "🚀"
            elif code_scores.get("短线得分", 0) > 0.7:
                sig = "📶"
            parts.append(
                f"{sig}{r['名称']}({r['行业']}) "
                f"{format_amount(r['主力净流入'])} {format_pct(r['涨跌幅'])}"
            )
        print(f"推荐买入: {' | '.join(parts)}")

    # 偷偷流入
    stealth_stocks = detect_stealth_inflow(date_str, stock_result)
    if stealth_stocks:
        stealth_sectors = detect_stealth_sectors(stealth_stocks)
        top_stealth_sectors = stealth_sectors[:3]
        top_stealth_stocks = stealth_stocks[:5]

        sector_str = ' | '.join(
            f"{s['行业']} {s['个股数']}只{format_amount(s['主力净流入'])}"
            for s in top_stealth_sectors
        )
        stock_str = ' | '.join(
            f"{s['名称']}({s['行业']}) {format_amount(s['主力净流入'])}"
            for s in top_stealth_stocks
        )
        print(f"偷偷流入: {sector_str}")
        print(f"  → {stock_str}")

    # K线技术面增强 — 推荐股 + 主力TOP股
    kline_codes = list(dict.fromkeys(
        [r["代码"] for r in buy_picks[:5]] + [r["代码"] for r in top_stocks[:5]]
    ))
    try:
        from kline_strategy_signals import score_kline_broad
        broad_scores = {}
        for code in kline_codes:
            s = score_kline_broad(code)
            if s:
                broad_scores[code] = s

        if broad_scores:
            # K线技术评分（降阈至60分以获更多参考）
            ranked = sorted(broad_scores.items(), key=lambda x: x[1]["broad_score"], reverse=True)
            parts = []
            for code, s in ranked[:5]:
                if s["broad_score"] < 40:
                    continue
                tags = []
                if s["golden_cross"]: tags.append("金叉")
                if s["near_ma20"]: tags.append("支撑")
                if s["alignment"] == "多头排列": tags.append("多头")
                tag_str = ("+" + "+".join(tags)) if tags else ""
                parts.append(
                    f"{code} K{s['broad_score']:.0f}分"
                    f"涨率{s['est_win_rate']*100:.0f}%"
                    f"{s['alignment'][:2]}{s['trend']}"
                    f"量{s['vol_ratio']:.1f}x{tag_str}"
                )
            if parts:
                print(f"K线技术: {' | '.join(parts)}")
                # Highlight >72%
                high = [(c, s) for c, s in ranked if s["est_win_rate"] >= 0.72]
                if high:
                    codes_high = [f"{c}({s['est_win_rate']*100:.0f}%)" for c, s in high[:3]]
                    print(f"  ⚡ 高胜率: {', '.join(codes_high)}")
            else:
                print(f"K线: 无≥40分个股 (扫描{len(broad_scores)}只)")
    except ImportError:
        pass  # kline_strategy_signals not available

    print("─" * 70)

    # ---- 保存当前状态 ----
    _save_state({
        "date": date_str,
        "ts": last_ts,
        "frame_count": frame_count,
        "total_main": total,
        "sectors": [{"行业": s["行业"], "主力净流入": s["主力净流入"]} for s in sectors],
        "top_stocks": [{"代码": r["代码"], "名称": r["名称"], "主力净流入": r["主力净流入"]} for r in top_stocks],
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="主力资金流向分析")
    parser.add_argument("--date", default=None, help="日期 YYYYMMDD (默认最新)")
    parser.add_argument("--top", type=int, default=15, help="TOP N 数量 (默认15)")
    parser.add_argument("--watch", action="store_true", help="持续监控模式")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--summary", action="store_true", help="交易摘要模式（板块TOP3+个股TOP10+潜力TOP10+推荐买入TOP10）")
    parser.add_argument("--brief", action="store_true", help="简洁洞察模式（一句话总结当前主力方向）")
    args = parser.parse_args()

    if args.watch:
        watch_mode()
        sys.exit(0)

    date_str = args.date or find_latest_date()
    if not date_str:
        print("❌ 未找到任何日期数据")
        sys.exit(1)

    if args.brief:
        stock_result = analyze_stock_flow(date_str, args.top)
        concept_result = analyze_sector_flow(date_str, "concept_flow", args.top)
        industry_result = analyze_sector_flow(date_str, "industry_flow", args.top)
        print_brief_insight(date_str, stock_result, concept_result, industry_result)
    elif args.summary:
        # 交易摘要模式
        stock_result = analyze_stock_flow(date_str, args.top)
        concept_result = analyze_sector_flow(date_str, "concept_flow", args.top)
        industry_result = analyze_sector_flow(date_str, "industry_flow", args.top)
        print_trading_summary(date_str, stock_result, concept_result, industry_result)
    elif args.json:
        concept_result = analyze_sector_flow(date_str, "concept_flow", args.top)
        industry_result = analyze_sector_flow(date_str, "industry_flow", args.top)
        stock_result = analyze_stock_flow(date_str, args.top)

        output = {
            "date": date_str,
            "analysis_time": datetime.now(BJS_TZ).isoformat(),
            "data_quality": {
                "stock_validation_errors": stock_result.get("validation_errors", 0) if stock_result else -1,
                "stock_new_not_in_first": stock_result.get("new_stocks_not_in_first", 0) if stock_result else -1,
            },
            "concept": concept_result,
            "industry": industry_result,
            "stock_summary": {
                k: v for k, v in stock_result.items()
                if k not in ("all_sectors", "sector_ranking")
            } if stock_result else None,
            "stock_sector_ranking": stock_result["all_sectors"] if stock_result else [],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    else:
        run_analysis(date_str, args.top)
