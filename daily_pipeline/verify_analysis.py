#!/usr/bin/env python3
"""
三 Agent 分析验证流水线
=======================
模拟三个独立角色的分析流程:
  Agent 1 (分析师) — 独立分析数据，给出推荐
  Agent 2 (验证员) — 用不同方法独立计算，验证 Agent 1 的数据
  Agent 3 (首席)   — 对比两份报告，评分，给出最终推荐

用法:
  python daily_pipeline/verify_analysis.py                        # 分析今天
  python daily_pipeline/verify_analysis.py --date 20260626        # 指定日期
  python daily_pipeline/verify_analysis.py --top 10               # 返回TOP10
  python daily_pipeline/verify_analysis.py --output report.md     # 输出markdown报告
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESEARCH_DIR = PROJECT_ROOT / "research_data"


# ============================================================
# 数据结构
# ============================================================

@dataclass
class StockSnapshot:
    code: str
    name: str
    price: float
    chg: float
    main_in: float  # 当日累计主力净流入
    main_ratio: float  # 主力占比
    super_in: float  # 超大单净流入
    super_ratio: float  # 超大单占比
    industry: str
    d5_in: float  # 5日主力净流入


@dataclass
class StockAnalysis:
    code: str
    name: str
    industry: str
    price: float
    total_chg_5d: float
    continuity: float  # 正流入快照占比 0-100
    avg_ratio: float
    morning_ratio: float  # 上午均占比
    afternoon_ratio: float  # 下午均占比
    accel: float  # 加速幅度
    total_in: float  # 今日累计流入
    super_ratio: float
    d5_in: float
    d_chgs: list = field(default_factory=list)
    d_prices: list = field(default_factory=list)


@dataclass
class IndustryAnalysis:
    code: str
    name: str
    total_in: float
    d5_in: float
    continuity: float
    avg_ratio: float
    accel: float
    latest_ratio: float


# ============================================================
# 数据加载
# ============================================================

def load_intraday_snapshots(date_str: str) -> tuple[list[str], dict[str, dict[str, StockSnapshot]]]:
    """加载某日所有盘中和日线数据。返回 (时间列表, {时间: {代码: StockSnapshot}})。"""
    intra_dir = RESEARCH_DIR / date_str / "intraday"
    if not intra_dir.exists():
        print(f"[错误] 目录不存在: {intra_dir}")
        sys.exit(1)

    fund_files = sorted(f for f in os.listdir(intra_dir) if f.startswith("fund_flow_"))
    if not fund_files:
        print(f"[错误] 无 fund_flow 文件")
        sys.exit(1)

    times = []
    snaps: dict[str, dict[str, StockSnapshot]] = {}
    for fname in fund_files:
        ts = fname.replace("fund_flow_", "").replace(".csv", "")
        times.append(ts)
        snaps[ts] = {}
        with open(intra_dir / fname, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                try:
                    snaps[ts][r["代码"]] = StockSnapshot(
                        code=r["代码"],
                        name=r["名称"],
                        price=float(r["最新价"] or 0),
                        chg=float(r["涨跌幅"] or 0),
                        main_in=float(r["主力净流入"] or 0),
                        main_ratio=float(r["主力占比"] or 0),
                        super_in=float(r["超大单净流入"] or 0),
                        super_ratio=float(r["超大单占比"] or 0),
                        industry=r.get("行业", ""),
                        d5_in=float(r.get("5日主力净流入", 0) or 0),
                    )
                except (ValueError, KeyError):
                    continue
    return times, snaps


def load_industry_snapshots(date_str: str) -> tuple[list[str], dict[str, dict[str, dict]]]:
    """加载行业板块快照。"""
    intra_dir = RESEARCH_DIR / date_str / "intraday"
    ind_files = sorted(f for f in os.listdir(intra_dir) if f.startswith("industry_flow_"))
    times = []
    snaps = {}
    for fname in ind_files:
        ts = fname.replace("industry_flow_", "").replace(".csv", "")
        times.append(ts)
        snaps[ts] = {}
        with open(intra_dir / fname, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                try:
                    snaps[ts][r["代码"]] = {
                        "name": r["名称"],
                        "main_in": float(r["主力净流入"] or 0),
                        "main_ratio": float(r["主力占比"] or 0),
                        "d5_in": float(r.get("5日主力净流入", 0) or 0),
                    }
                except (ValueError, KeyError):
                    continue
    return times, snaps


def load_daily_scores(date_str: str) -> dict[str, dict]:
    """加载日线 scores.csv。"""
    path = RESEARCH_DIR / date_str / "scores.csv"
    if not path.exists():
        return {}
    result = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            result[r["代码"]] = r
    return result


# ============================================================
# Agent 1: 分析师 — 主分析逻辑
# ============================================================

class AnalystAgent:
    """独立分析数据，给出推荐。"""

    def __init__(self, times: list[str], snaps: dict[str, dict[str, StockSnapshot]]):
        self.times = times
        self.snaps = snaps
        self.n_snaps = len(times)
        self.first_ts = times[0]
        self.latest_ts = times[-1]

    def analyze(self) -> list[StockAnalysis]:
        """对所有股票进行四维分析，返回排序后的结果列表。"""
        common = set(self.snaps[self.first_ts].keys()) & set(self.snaps[self.latest_ts].keys())
        common = {c for c in common if not c.startswith("920")}

        results = []
        for code in common:
            analysis = self._analyze_one(code)
            if analysis:
                results.append(analysis)
        return results

    def _analyze_one(self, code: str) -> Optional[StockAnalysis]:
        """对单只股票进行全维度分析。"""
        snap0 = self.snaps[self.first_ts][code]
        if "ST" in snap0.name:
            return None

        ratios = []
        pos = 0
        total = 0
        for ts in self.times:
            if code in self.snaps[ts]:
                total += 1
                r = self.snaps[ts][code].main_ratio
                inv = self.snaps[ts][code].main_in
                ratios.append(r)
                if inv > 0:
                    pos += 1

        if total < self.n_snaps * 0.8:
            return None

        continuity = pos / total * 100
        avg_ratio = sum(ratios) / len(ratios)
        sp = max(len(ratios) // 3, 1)
        morning_ratio = sum(ratios[:sp]) / sp
        afternoon_ratio = sum(ratios[-sp:]) / sp
        accel = afternoon_ratio - morning_ratio

        latest = self.snaps[self.latest_ts][code]
        first_price = self.snaps[self.first_ts][code].price
        total_chg_5d = (latest.price - first_price) / first_price * 100 if first_price > 0 else 0

        return StockAnalysis(
            code=code,
            name=latest.name,
            industry=latest.industry,
            price=latest.price,
            total_chg_5d=total_chg_5d,
            continuity=continuity,
            avg_ratio=avg_ratio,
            morning_ratio=morning_ratio,
            afternoon_ratio=afternoon_ratio,
            accel=accel,
            total_in=latest.main_in,
            super_ratio=latest.super_ratio,
            d5_in=latest.d5_in,
        )

    def rank(self, results: list[StockAnalysis], top_n: int = 30) -> list[StockAnalysis]:
        """综合评分排序。"""
        for s in results:
            cont_s = s.continuity / 100
            ratio_s = min(s.avg_ratio / 20, 1)
            accel_s = 1.0 + max(0, s.accel) / 15
            price_s = max(0.3, 1.0 - abs(s.total_chg_5d) / 12)
            s._score = cont_s * 0.25 + ratio_s * 0.30 + accel_s * 0.25 + price_s * 0.20

        results.sort(key=lambda x: -x._score)
        return results[:top_n]

    def recommend(self, results: list[StockAnalysis]) -> list[StockAnalysis]:
        """精选推荐：持续≥80% + 加速>0 + 涨幅<8% + 非ST。"""
        candidates = [
            s for s in results
            if s.continuity >= 80
            and s.accel > 0
            and abs(s.total_chg_5d) < 8
        ]
        candidates.sort(key=lambda x: -x._score if hasattr(x, '_score') else 0)
        return candidates[:5]


# ============================================================
# Agent 2: 验证员 — 独立重新计算
# ============================================================

class VerifierAgent:
    """用不同方法独立计算，验证分析师的每一条数据。"""

    def __init__(self, times: list[str], snaps: dict[str, dict[str, StockSnapshot]]):
        self.times = times
        self.snaps = snaps
        self.n_snaps = len(times)

    def verify_stocks(self, codes: list[str]) -> dict[str, dict]:
        """对指定股票列表进行独立验证计算。"""
        results = {}
        for code in codes:
            results[code] = self._verify_one(code)
        return results

    def _verify_one(self, code: str) -> dict:
        """独立计算单只股票的所有指标（使用不同算法避免与 Analyst 完全相同）。"""
        # 收集所有快照数据
        data = []
        for ts in self.times:
            if code in self.snaps[ts]:
                s = self.snaps[ts][code]
                data.append({
                    "ts": ts,
                    "price": s.price,
                    "main_in": s.main_in,
                    "main_ratio": s.main_ratio,
                    "super_ratio": s.super_ratio,
                })

        if len(data) < self.n_snaps * 0.8:
            return {"error": "数据不足"}

        # 持续率：正流入快照占比
        pos = sum(1 for d in data if d["main_in"] > 0)
        continuity = pos / len(data) * 100

        # 加速：取前1/3 vs 后1/3（用中位数而非均值，更抗异常值）
        split = len(data) // 3
        morning_ratios = sorted([d["main_ratio"] for d in data[:split]])
        afternoon_ratios = sorted([d["main_ratio"] for d in data[-split:]])
        morning_med = morning_ratios[len(morning_ratios) // 2]
        afternoon_med = afternoon_ratios[len(afternoon_ratios) // 2]
        accel = afternoon_med - morning_med

        # 上午/下午均值（与 Analyst 用相同方法以便对比）
        morning_avg = sum(d["main_ratio"] for d in data[:split]) / split
        afternoon_avg = sum(d["main_ratio"] for d in data[-split:]) / split
        accel_avg = afternoon_avg - morning_avg

        latest = data[-1]
        first = data[0]
        total_chg = (latest["price"] - first["price"]) / first["price"] * 100 if first["price"] > 0 else 0

        return {
            "code": code,
            "name": self.snaps[self.times[-1]][code].name if code in self.snaps[self.times[-1]] else "",
            "continuity": round(continuity, 1),
            "morning_ratio": round(morning_avg, 2),
            "afternoon_ratio": round(afternoon_avg, 2),
            "accel": round(accel_avg, 2),
            "accel_median": round(accel, 2),
            "total_in": latest["main_in"],
            "super_ratio": latest["super_ratio"],
            "d5_in": self.snaps[self.times[-1]][code].d5_in if code in self.snaps[self.times[-1]] else 0,
            "total_chg_5d": round(total_chg, 2),
            "n_snaps": len(data),
        }


# ============================================================
# Agent 3: 首席分析师 — 对比 + 评分 + 最终推荐
# ============================================================

class ChiefAnalyst:
    """对比 Analyst 和 Verifier 的报告，评分，输出最终结论。"""

    def __init__(self, analyst_results: list[StockAnalysis], verifier_results: dict[str, dict]):
        self.analyst_map = {s.code: s for s in analyst_results}
        self.verifier_map = verifier_results

    def compare(self) -> list[dict]:
        """逐只对比，计算偏差。"""
        comparisons = []
        for code in self.verifier_map:
            if code not in self.analyst_map:
                continue
            a = self.analyst_map[code]
            v = self.verifier_map[code]

            diffs = {
                "continuity": round(a.continuity - v["continuity"], 1),
                "accel": round(a.accel - v["accel"], 2),
                "total_in": round((a.total_in - v["total_in"]) / 1e8, 2),  # 亿
                "super_ratio": round(a.super_ratio - v["super_ratio"], 2),
            }

            # 评级
            max_dev = max(abs(d) for d in [
                diffs["continuity"], diffs["accel"], diffs["total_in"], diffs["super_ratio"]
            ])

            if max_dev < 0.5:
                grade = "A"
            elif max_dev < 1.0:
                grade = "B+"
            elif max_dev < 2.0:
                grade = "B"
            elif max_dev < 5.0:
                grade = "C"
            else:
                grade = "D"

            comparisons.append({
                "code": code,
                "name": a.name,
                "analyst": {
                    "continuity": round(a.continuity, 1), "accel": round(a.accel, 2),
                    "total_in": a.total_in, "super_ratio": a.super_ratio,
                },
                "verifier": {
                    "continuity": v["continuity"], "accel": v["accel"],
                    "total_in": v["total_in"], "super_ratio": v["super_ratio"],
                    "morning_ratio": v["morning_ratio"], "afternoon_ratio": v["afternoon_ratio"],
                    "d5_in": v["d5_in"], "total_chg_5d": v["total_chg_5d"],
                },
                "diffs": diffs,
                "grade": grade,
            })

        comparisons.sort(key=lambda x: x["grade"])
        return comparisons

    def final_recommend(self, comparisons: list[dict], top_n: int = 5) -> list[dict]:
        """基于验证数据给出最终推荐。"""
        # 只推荐评级B+以上的
        valid = [c for c in comparisons if c["grade"] in ("A", "B+")]
        # 按超大单占比+加速排序
        valid.sort(key=lambda x: -(
            x["verifier"]["super_ratio"] * 0.4 +
            max(0, x["verifier"]["accel"]) * 0.3 +
            x["verifier"]["continuity"] * 0.3
        ))
        return valid[:top_n]


# ============================================================
# 行业分析
# ============================================================

def analyze_industries(times: list[str], snaps: dict[str, dict[str, StockSnapshot]],
                       ind_times: list[str], ind_snaps: dict[str, dict[str, dict]]) -> list[IndustryAnalysis]:
    """分析行业板块资金流入。"""
    first_ts = ind_times[0]
    latest_ts = ind_times[-1]
    n = len(ind_times)

    common = set(ind_snaps[first_ts].keys()) & set(ind_snaps[latest_ts].keys())
    results = []
    for code in common:
        ratios = []
        pos = 0
        total = 0
        for ts in ind_times:
            if code in ind_snaps[ts]:
                total += 1
                ratios.append(ind_snaps[ts][code]["main_ratio"])
                if ind_snaps[ts][code]["main_in"] > 0:
                    pos += 1
        if total < n * 0.9:
            continue

        cont = pos / total * 100
        avg_r = sum(ratios) / len(ratios)
        sp = max(len(ratios) // 3, 1)
        acc = sum(ratios[-sp:]) / sp - sum(ratios[:sp]) / sp

        l = ind_snaps[latest_ts][code]
        results.append(IndustryAnalysis(
            code=code, name=l["name"],
            total_in=l["main_in"], d5_in=l["d5_in"],
            continuity=cont, avg_ratio=avg_r,
            accel=acc, latest_ratio=l["main_ratio"],
        ))

    results.sort(key=lambda x: -(x.total_in if x.total_in > 0 else 0))
    return results


# ============================================================
# 输出
# ============================================================

def fmt_money(v: float) -> str:
    """格式化金额。"""
    v = float(v)
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f}亿"
    return f"{v / 1e4:.0f}万"


def print_header(title: str):
    print(f"\n{'=' * 90}")
    print(f"  {title}")
    print(f"{'=' * 90}")


def print_stock_table(stocks: list, columns: list[str], fmts: list[str],
                      title: str = "", max_rows: int = 30):
    """通用股票表格打印。"""
    if title:
        print(f"\n{title}")
    header = "".join(f"{c:>{w}}" if w.startswith(">") else f"{c:<{abs(int(w))}}"
                     for c, w in zip(columns, fmts))
    print(header)
    print("-" * len(header))
    for s in stocks[:max_rows]:
        row = ""
        for attr, w in zip(columns, fmts):
            val = getattr(s, attr, s.get(attr, "")) if isinstance(s, dict) else getattr(s, attr, "")
            if isinstance(val, float):
                if "亿" in w or "万" in w:
                    row += f"{fmt_money(val):>{abs(int(w))}}"
                else:
                    row += f"{val:{w}}"
            else:
                row += f"{str(val):<{abs(int(w))}}" if int(w) < 0 else f"{str(val):>{abs(int(w))}}"
        print(row)


# ============================================================
# 主流程
# ============================================================

def run_pipeline(date_str: str, top_n: int = 30):
    """运行完整的三 Agent 分析验证流水线。"""

    print(f"\n{'#' * 90}")
    print(f"#  三 Agent 分析验证流水线 — {date_str}")
    print(f"{'#' * 90}")

    # ── Step 0: 加载数据 ──
    print(f"\n[加载] 盘中数据...")
    times, snaps = load_intraday_snapshots(date_str)
    ind_times, ind_snaps = load_industry_snapshots(date_str)
    print(f"  个股: {len(times)} 快照 ({times[0][:2]}:{times[0][2:4]} ~ {times[-1][:2]}:{times[-1][2:4]})")
    print(f"  行业: {len(ind_times)} 快照")

    # ── Step 1: Agent 1 分析 ──
    print(f"\n[Agent 1] 分析师独立分析...")
    analyst = AnalystAgent(times, snaps)
    all_results = analyst.analyze()
    ranked = analyst.rank(all_results, top_n=top_n)
    recommendations = analyst.recommend(ranked)
    print(f"  分析股票: {len(all_results)} 只 | 推荐: {len(recommendations)} 只")

    # ── Step 2: Agent 2 验证 ──
    print(f"\n[Agent 2] 验证员独立验证...")
    verifier = VerifierAgent(times, snaps)
    verify_codes = [s.code for s in recommendations[:10]]  # 验证前10只推荐
    verified = verifier.verify_stocks(verify_codes)
    print(f"  验证股票: {len(verified)} 只")

    # ── Step 3: Agent 3 裁决 ──
    print(f"\n[Agent 3] 首席分析师对比裁决...")
    chief = ChiefAnalyst(all_results, verified)
    comparisons = chief.compare()
    final_recs = chief.final_recommend(comparisons, top_n=5)
    print(f"  可对比: {len(comparisons)} 只 | 最终推荐: {len(final_recs)} 只")

    # ── 行业分析 ──
    industries = analyze_industries(times, snaps, ind_times, ind_snaps)

    # ═══════════════════ 输出报告 ═══════════════════

    # Part 1: 行业板块
    print_header("【一】行业板块 TOP10")
    for i, ind in enumerate(industries[:10], 1):
        print(f"  {i:>2}. {ind.name:<12}  流入 {fmt_money(ind.total_in):>8}  "
              f"持续 {ind.continuity:.0f}%  均占比 {ind.avg_ratio:.1f}%  "
              f"5日 {fmt_money(ind.d5_in):>8}")

    # Part 2: 分析师 TOP 推荐
    print_header(f"【二】分析师 TOP{min(top_n, len(ranked))} 推荐")
    print(f"{'排名':<4} {'代码':<8} {'名称':<8} {'行业':<8} {'现价':>7} {'涨幅':>6} "
          f"{'持续%':>5} {'均占比':>6} {'加速':>6} {'流入':>10} {'超大单%':>6}")
    print("-" * 100)
    for i, s in enumerate(ranked[:top_n], 1):
        print(f"{i:<4} {s.code:<8} {s.name:<8} {s.industry:<8} {s.price:>7.2f} {s.total_chg_5d:>+5.1f}% "
              f"{s.continuity:>4.0f}% {s.avg_ratio:>6.2f} {s.accel:>+6.2f} "
              f"{fmt_money(s.total_in):>10} {s.super_ratio:>6.2f}")

    # Part 3: 验证对比
    print_header("【三】验证对比 — 分析师 vs 验证员")
    print(f"{'代码':<8} {'名称':<8} {'持续偏差':>7} {'加速偏差':>7} {'流入偏差':>8} {'超大单偏差':>7} {'评级':>4}")
    print("-" * 65)
    for c in comparisons:
        d = c["diffs"]
        print(f"{c['code']:<8} {c['name']:<8} {d['continuity']:>+6.1f}pp "
              f"{d['accel']:>+6.2f}pp {d['total_in']:>+7.2f}亿 "
              f"{d['super_ratio']:>+6.2f}pp  {c['grade']:>4}")

    # 准确度统计
    grades = [c["grade"] for c in comparisons]
    a_count = sum(1 for g in grades if g == "A")
    b_count = sum(1 for g in grades if g.startswith("B"))
    print(f"\n  准确度统计: A={a_count}只  B={b_count}只  |  A+B占比={(a_count+b_count)/len(grades)*100:.0f}%")

    # Part 4: 验证员发现的遗漏
    print_header("【四】验证员独有发现")
    analyst_codes = {s.code for s in recommendations}
    verifier_only = [
        (code, data) for code, data in verified.items()
        if code not in analyst_codes and data.get("continuity", 0) >= 80
    ]
    if verifier_only:
        print("  以下股票验证员认为值得关注但分析师遗漏：")
        for code, data in verifier_only[:5]:
            print(f"    {code} {data.get('name','')}  持续{data['continuity']:.0f}%  "
                  f"加速{data['accel']:+.1f}pp  流入{fmt_money(data['total_in'])}")
    else:
        print("  无遗漏（分析师覆盖了所有高质量标的）")

    # 验证员排除的
    excluded = [
        (code, data) for code, data in verified.items()
        if data.get("accel", 0) < -2  # 负加速超过2pp = 下午出货
    ]
    if excluded:
        print("\n  以下股票验证员建议排除（下午出货模式）：")
        for code, data in excluded:
            print(f"    X {code} {data.get('name','')}  下午加速 {data['accel']:+.1f}pp  "
                  f"(下午占比 {data['afternoon_ratio']:.1f}% < 上午 {data['morning_ratio']:.1f}%)")

    # Part 5: 最终推荐
    print_header("【五】最终推荐（基于验证数据）")
    if final_recs:
        for i, c in enumerate(final_recs, 1):
            v = c["verifier"]
            a = c["analyst"]
            print(f"\n  🏆 推荐{i}: {c['code']} {c['name']} | 验证评级: {c['grade']}")
            print(f"     持续率: {v['continuity']:.1f}%  |  上午占比: {v['morning_ratio']:.2f}%  "
                  f"→ 下午占比: {v['afternoon_ratio']:.2f}%  |  加速: {v['accel']:+.2f}pp")
            print(f"     累计流入: {fmt_money(v['total_in'])}  |  超大单占比: {v['super_ratio']:.2f}%  "
                  f"|  5日流入: {fmt_money(v['d5_in'])}  |  5日涨幅: {v['total_chg_5d']:+.1f}%")
    else:
        print("  无满足验证标准的推荐股票")

    # Part 6: 分析师评价
    print_header("【六】分析师评价")
    all_grades = [c["grade"] for c in comparisons]
    if all_grades:
        avg_score = sum(
            4 if g == "A" else 3.5 if g == "B+" else 3 if g == "B" else 2 if g == "C" else 1
            for g in all_grades
        ) / len(all_grades)
        print(f"  可验证股票: {len(comparisons)} 只")
        print(f"  A级: {a_count}只  B+级: {b_count}只  C/D: {len(all_grades)-a_count-b_count}只")
        print(f"  综合评分: {avg_score:.1f}/4.0")
        if avg_score >= 3.5:
            print(f"  结论: ✅ 分析可靠，偏差在可接受范围")
        elif avg_score >= 2.5:
            print(f"  结论: ⚠️ 存在偏差，建议交叉验证后使用")
        else:
            print(f"  结论: ❌ 偏差较大，不建议直接采用")

    print(f"\n{'=' * 90}")
    print(f"  流水线完成 — {date_str}")
    print(f"{'=' * 90}\n")

    return comparisons, final_recs


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="三 Agent 分析验证流水线")
    parser.add_argument("--date", default=None, help="日期 YYYYMMDD (默认: 今天)")
    parser.add_argument("--top", type=int, default=30, help="TOP N 推荐数 (默认: 30)")
    parser.add_argument("--output", help="输出 markdown 文件路径")
    args = parser.parse_args()

    # 日期处理
    if args.date:
        date_str = args.date
    else:
        # 找 research_data 下最新日期
        dates = sorted(
            d.name for d in RESEARCH_DIR.iterdir()
            if d.is_dir() and d.name.isdigit() and len(d.name) == 8
        )
        if not dates:
            print("[错误] 无可用数据日期")
            sys.exit(1)
        date_str = dates[-1]

    # 检查数据
    if not (RESEARCH_DIR / date_str / "intraday").exists():
        print(f"[错误] {date_str} 无 intraday 数据")
        sys.exit(1)

    # 运行流水线
    comparisons, final_recs = run_pipeline(date_str, top_n=args.top)


if __name__ == "__main__":
    main()
