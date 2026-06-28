#!/usr/bin/env python
"""
Agent C: 信号组合跨日稳定性分析
=================================
分析 P0~P4 + P34_alone 六个信号组的跨日稳定性和日内衰减，
输出可靠性等级判定。
"""

import csv
import json
import os
import math
import statistics
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path("/Users/taopeng/PycharmProjects/stock_fund_flow/research_data/backtest/1430_entry")
DATES = ["20260622", "20260623", "20260624", "20260625"]
OUTPUT_PATH = BASE_DIR / "agent_c_stability_analysis.json"

# ── 信号组定义 ──────────────────────────────────────────────
# 每个信号组: (name, filter_fn)
# filter_fn 接受 row dict, 返回 True/False

def has_signal(combined_signals_str, target) -> bool:
    """检查综合信号列是否包含某个信号标签"""
    if not combined_signals_str:
        return False
    signals = set(s.strip() for s in combined_signals_str.split(",") if s.strip())
    return target in signals

def has_start_signal(start_signals_str, target) -> bool:
    """检查启动信号列是否包含某个信号标签"""
    if not start_signals_str:
        return False
    signals = set(s.strip() for s in start_signals_str.split(",") if s.strip())
    return target in signals

SIGNAL_GROUPS = {
    "P0": lambda r: (
        has_signal(r.get("综合信号", ""), "P34_gap_strong")
        and has_signal(r.get("综合信号", ""), "P32_pump_risk")
    ),
    "P1": lambda r: (
        has_start_signal(r.get("启动信号", ""), "E3_strong_start")
        and has_signal(r.get("综合信号", ""), "P34_gap_strong")
    ),
    "P2": lambda r: (
        has_signal(r.get("综合信号", ""), "P_high_price")
        and has_signal(r.get("综合信号", ""), "P35_short_pressure")
        and has_signal(r.get("综合信号", ""), "P34_gap_reverse")
    ),
    "P3": lambda r: (
        r.get("行业", "") == "半导体"
        and has_signal(r.get("综合信号", ""), "P35_short_cover")
    ),
    "P4": lambda r: (
        has_signal(r.get("综合信号", ""), "P34_P32_combo")
        and has_signal(r.get("综合信号", ""), "P35_short_cover")
    ),
    "P34_alone": lambda r: has_signal(r.get("综合信号", ""), "P34_gap_standalone"),
}

# ── 时段分类 ────────────────────────────────────────────────
def classify_period(timestr: str) -> str:
    """根据 HHMMSS 时间字符串分类"""
    if not timestr or len(timestr) < 4:
        return "unknown"
    hhmm = int(timestr[:4])
    if 930 <= hhmm <= 1030:
        return "早盘(0930-1030)"
    elif 1030 < hhmm <= 1130:
        return "午盘(1030-1130)"
    elif 1300 <= hhmm <= 1430:
        return "午后(1300-1430)"
    elif 1430 < hhmm <= 1500:
        return "尾盘(1430-1500)"
    else:
        return "unknown"


def read_all_snapshots() -> dict:
    """
    返回:
        data[date_str][snapshot_time_str] = list of row dicts
    """
    data = defaultdict(lambda: defaultdict(list))
    for date_str in DATES:
        enriched_dir = BASE_DIR / date_str / "enriched"
        if not enriched_dir.exists():
            print(f"  [WARN] 缺少 enriched 目录: {enriched_dir}")
            continue
        for csv_file in sorted(enriched_dir.glob("snapshot_*_enriched.csv")):
            # 从文件名提取时间，如 snapshot_093143_enriched.csv -> 093143
            fname = csv_file.stem  # snapshot_093143_enriched
            parts = fname.split("_")
            if len(parts) >= 2:
                timestr = parts[1]
            else:
                timestr = "unknown"

            with open(csv_file, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ret_str = row.get("收益%", "").strip()
                    try:
                        ret_val = float(ret_str)
                    except (ValueError, TypeError):
                        ret_val = None
                    row["收益%_float"] = ret_val
                    data[date_str][timestr].append(row)
    return data


def compute_stats(rows: list) -> dict:
    """计算 N, WR, avg_ret, median_ret, ret_std"""
    valid = [r for r in rows if r.get("收益%_float") is not None]
    n = len(valid)
    if n == 0:
        return {"N": 0, "WR": None, "avg_ret": None, "median_ret": None, "ret_std": None}

    wins = sum(1 for r in valid if r.get("胜负", "") == "胜")
    wr = wins / n * 100
    returns = [r["收益%_float"] for r in valid]
    avg_ret = statistics.mean(returns)
    median_ret = statistics.median(returns)
    ret_std = statistics.stdev(returns) if n >= 2 else 0.0

    return {
        "N": n,
        "WR": round(wr, 2),
        "avg_ret": round(avg_ret, 4),
        "median_ret": round(median_ret, 4),
        "ret_std": round(ret_std, 4),
    }


def safe_mean(values):
    """忽略 None 的均值"""
    vals = [v for v in values if v is not None]
    return statistics.mean(vals) if vals else None


def safe_cv(values):
    """变异系数: std / |mean|, 百分比"""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    mean_abs = abs(statistics.mean(vals))
    if mean_abs < 1e-10:
        return None
    return round(statistics.stdev(vals) / mean_abs * 100, 2)


def main():
    print("=" * 60)
    print("Agent C: 信号组合跨日稳定性分析")
    print("=" * 60)

    # ── 1. 读取所有数据 ──
    print("\n[1/4] 读取快照数据...")
    all_data = read_all_snapshots()
    for d in DATES:
        n_snapshots = len(all_data.get(d, {}))
        total_rows = sum(len(rows) for rows in all_data.get(d, {}).values())
        print(f"  {d}: {n_snapshots} 快照, {total_rows} 行")

    # ── 2. 每日统计 ──
    print("\n[2/4] 计算信号组每日统计...")
    # 结构: daily_stats[signal_group][date] = stats_dict
    # 同时收集全市场每日收益
    daily_market_avg_ret = {}
    daily_total_rows = {}

    daily_stats = defaultdict(lambda: defaultdict(lambda: {"rows": []}))

    for date_str in DATES:
        all_rows_for_date = []
        for timestr, rows in all_data.get(date_str, {}).items():
            all_rows_for_date.extend(rows)
            for row in rows:
                daily_total_rows[date_str] = daily_total_rows.get(date_str, 0) + 1
                for sg_name, filter_fn in SIGNAL_GROUPS.items():
                    if filter_fn(row):
                        daily_stats[sg_name][date_str]["rows"].append(row)

        # 全市场均收益
        valid = [r for r in all_rows_for_date if r.get("收益%_float") is not None]
        if valid:
            daily_market_avg_ret[date_str] = statistics.mean(
                [r["收益%_float"] for r in valid]
            )
        else:
            daily_market_avg_ret[date_str] = None

    # 计算每日 stats
    daily_stats_computed = defaultdict(dict)
    for sg_name in SIGNAL_GROUPS:
        for date_str in DATES:
            rows = daily_stats[sg_name].get(date_str, {}).get("rows", [])
            daily_stats_computed[sg_name][date_str] = compute_stats(rows)

    # ── 3. 跨日分析 + 日内稳定性 ──
    print("\n[3/4] 跨日 + 日内稳定性分析...")

    results = {}

    for sg_name in SIGNAL_GROUPS:
        print(f"\n  --- {sg_name} ---")

        # ---- 跨日指标 ----
        daily_n = []
        daily_wr = []
        daily_avgret = []
        coverage_dates = []

        for date_str in DATES:
            st = daily_stats_computed[sg_name][date_str]
            if st["N"] > 0:
                coverage_dates.append(date_str)
                daily_n.append(st["N"])
                daily_wr.append(st["WR"])
                daily_avgret.append(st["avg_ret"])

        n_days = len(coverage_dates)

        if n_days == 0:
            results[sg_name] = {
                "cross_day": {
                    "cv_N": None,
                    "cv_WR": None,
                    "cv_avg_ret": None,
                    "coverage_days": 0,
                    "total_days": 4,
                    "daily_detail": {},
                },
                "reliability": "排除 (无有效数据)",
                "intraday": {},
            }
            continue

        cv_n = safe_cv(daily_n)
        cv_wr = safe_cv(daily_wr)
        cv_avgret = safe_cv(daily_avgret)

        avg_n = safe_mean(daily_n)
        avg_wr = safe_mean(daily_wr)
        avg_avgret = safe_mean(daily_avgret)

        # 日均超额
        avg_excess = None
        daily_excess_list = []
        for date_str in coverage_dates:
            mr = daily_market_avg_ret.get(date_str)
            sr = daily_stats_computed[sg_name][date_str]["avg_ret"]
            if mr is not None and sr is not None:
                excess = sr - mr
                daily_excess_list.append(excess)
        if daily_excess_list:
            avg_excess = round(statistics.mean(daily_excess_list), 4)

        # WR 波动 (绝对百分点)
        wr_std_pp = round(statistics.stdev(daily_wr), 2) if len(daily_wr) >= 2 else 0.0

        # 样本集中度
        max_single_day_pct = max(daily_n) / sum(daily_n) * 100 if sum(daily_n) > 0 else 100

        cross_day = {
            "daily_N": {d: daily_stats_computed[sg_name][d]["N"] for d in DATES},
            "daily_WR_pct": {d: daily_stats_computed[sg_name][d]["WR"] for d in DATES},
            "daily_avg_ret_pct": {d: daily_stats_computed[sg_name][d]["avg_ret"] for d in DATES},
            "daily_median_ret_pct": {d: daily_stats_computed[sg_name][d]["median_ret"] for d in DATES},
            "daily_ret_std": {d: daily_stats_computed[sg_name][d]["ret_std"] for d in DATES},
            "avg_N": round(avg_n, 1) if avg_n else None,
            "avg_WR_pct": round(avg_wr, 2) if avg_wr else None,
            "avg_avg_ret_pct": round(avg_avgret, 4) if avg_avgret else None,
            "cv_N_pct": cv_n,
            "cv_WR_pct": cv_wr,
            "cv_avg_ret_pct": cv_avgret,
            "wr_std_pp": wr_std_pp,
            "coverage_days": n_days,
            "total_days": 4,
            "coverage_ratio": round(n_days / 4, 2),
            "market_daily_avg_ret": {d: round(r, 4) if r else None for d, r in daily_market_avg_ret.items()},
            "avg_excess_ret_pct": avg_excess,
            "daily_excess_ret_pct": {d: round(e, 4) for d, e in zip(coverage_dates, daily_excess_list)},
            "max_single_day_N_pct": round(max_single_day_pct, 1),
            "total_N": sum(daily_n),
        }

        print(f"    跨日: 覆盖 {n_days}/4 天, 总样本 {sum(daily_n)}")
        print(f"      avg_WR={avg_wr:.1f}%, wr_std={wr_std_pp:.2f}pp, cv_WR={cv_wr}%")
        print(f"      avg_ret={avg_avgret:.4f}%, avg_excess={avg_excess}")
        print(f"      max_single_day_pct={max_single_day_pct:.1f}%")

        # ---- 日内稳定性分析 ----
        intraday = {}
        periods = ["早盘(0930-1030)", "午盘(1030-1130)", "午后(1300-1430)", "尾盘(1430-1500)"]

        for period in periods:
            period_rows = []
            for date_str in DATES:
                for timestr, rows in all_data.get(date_str, {}).items():
                    if classify_period(timestr) == period:
                        for row in rows:
                            if SIGNAL_GROUPS[sg_name](row):
                                period_rows.append(row)

            st = compute_stats(period_rows)
            intraday[period] = st

        # 日内衰减检测
        wr_sequence = [intraday[p]["WR"] for p in periods if intraday[p]["WR"] is not None]
        ret_sequence = [intraday[p]["avg_ret"] for p in periods if intraday[p]["avg_ret"] is not None]

        decay_warning = False
        decay_detail = ""
        if len(wr_sequence) >= 2:
            # 尾部时段 vs 头部时段
            first_half = wr_sequence[: len(wr_sequence) // 2]
            second_half = wr_sequence[len(wr_sequence) // 2 :]
            if safe_mean(first_half) and safe_mean(second_half):
                if safe_mean(second_half) < safe_mean(first_half) - 3:
                    decay_warning = True
                    decay_detail = f"WR 后半段 ({safe_mean(second_half):.1f}%) < 前半段 ({safe_mean(first_half):.1f}%) ，差值>{3}pp"
        if len(ret_sequence) >= 2:
            first_half_r = ret_sequence[: len(ret_sequence) // 2]
            second_half_r = ret_sequence[len(ret_sequence) // 2 :]
            if safe_mean(first_half_r) and safe_mean(second_half_r):
                if safe_mean(first_half_r) is not None and safe_mean(second_half_r) is not None:
                    if safe_mean(second_half_r) < safe_mean(first_half_r):
                        if decay_detail:
                            decay_detail += "; "
                        decay_detail += f"ret 后半段 ({safe_mean(second_half_r):.4f}%) < 前半段 ({safe_mean(first_half_r):.4f}%)"
                        decay_warning = True

        intraday_summary = {
            "periods": intraday,
            "wr_sequence": wr_sequence,
            "ret_sequence": ret_sequence,
            "decay_warning": decay_warning,
            "decay_detail": decay_detail or "无明显衰减",
        }

        print(f"    日内 WR: {wr_sequence}")
        print(f"    日内 ret: {[round(x, 4) if x else None for x in ret_sequence]}")
        print(f"    衰减?: {decay_warning} — {decay_detail}")

        # ---- 可靠性等级判定 ----
        reliability = "排除"
        reliability_reasons = []

        if avg_wr is None or avg_n is None:
            reliability = "排除 (无数据)"
        elif max_single_day_pct > 80:
            reliability = "排除"
            reliability_reasons.append(f"80%样本集中单日 ({max_single_day_pct:.1f}%)")
        elif sum(daily_n) < 30:
            reliability = "排除"
            reliability_reasons.append(f"N<30 (total={sum(daily_n)})")
        elif wr_std_pp > 10:
            reliability = "排除"
            reliability_reasons.append(f"波动>10pp (wr_std={wr_std_pp:.2f})")
        elif avg_wr > 55 and sum(daily_n) > 100 and wr_std_pp < 2 and avg_avgret is not None and avg_avgret > 0:
            reliability = "S级"
            reliability_reasons.append("WR>55% + N>100 + 跨日波动<2pp + 均收益为正")
        elif avg_wr > 45 and sum(daily_n) > 100 and wr_std_pp < 5:
            reliability = "A级"
            reliability_reasons.append("WR>45% + N>100 + 波动<5pp")
        elif avg_wr > 40 and sum(daily_n) > 50 and wr_std_pp < 10:
            reliability = "B级"
            reliability_reasons.append("WR>40% + N>50 + 波动<10pp")
        else:
            # 列出不满足的条件
            if avg_wr <= 55:
                reliability_reasons.append(f"WR不满足S级(需>55%, 实际{avg_wr:.1f}%)")
            if sum(daily_n) <= 100:
                reliability_reasons.append(f"N不满足S/A级(需>100, 实际{sum(daily_n)})")
            if wr_std_pp >= 2:
                reliability_reasons.append(f"波动不满足S级(需<2pp, 实际{wr_std_pp:.2f}pp)")
            if avg_avgret is not None and avg_avgret <= 0:
                reliability_reasons.append(f"均收益不满足S级(需>0, 实际{avg_avgret:.4f}%)")
            if avg_wr <= 45 and avg_wr > 40 and sum(daily_n) >= 50 and wr_std_pp < 10:
                reliability = "B级"
                reliability_reasons.append("WR>40% + N>50 + 波动<10pp")
            elif avg_wr <= 45 and avg_wr > 40 and sum(daily_n) >= 50:
                reliability = "B级"
                reliability_reasons.append("WR>40% + N>50 (波动偏大)")
            else:
                reliability = "排除 (不满足任何等级)"
                reliability_reasons.append("各项指标均不满足A/B级门槛")

        print(f"    可靠性: {reliability}")
        print(f"    原因: {'; '.join(reliability_reasons)}")

        results[sg_name] = {
            "cross_day": cross_day,
            "intraday": intraday_summary,
            "reliability": {
                "grade": reliability,
                "reasons": reliability_reasons,
                "checks": {
                    "WR_gt_55": avg_wr is not None and avg_wr > 55,
                    "N_gt_100": sum(daily_n) > 100,
                    "wr_std_lt_2pp": wr_std_pp < 2,
                    "avg_ret_positive": avg_avgret is not None and avg_avgret > 0,
                    "WR_gt_45": avg_wr is not None and avg_wr > 45,
                    "wr_std_lt_5pp": wr_std_pp < 5,
                    "WR_gt_40": avg_wr is not None and avg_wr > 40,
                    "N_gt_50": sum(daily_n) > 50,
                    "wr_std_lt_10pp": wr_std_pp < 10,
                    "single_day_lt_80pct": max_single_day_pct < 80,
                    "N_gt_30": sum(daily_n) >= 30,
                },
            },
        }

    # ── 4. 汇总输出 ──
    print("\n[4/4] 保存结果...")

    # 汇总表
    summary_table = []
    for sg_name in SIGNAL_GROUPS:
        r = results[sg_name]
        cd = r["cross_day"]
        rb = r["reliability"]
        summary_table.append({
            "signal_group": sg_name,
            "reliability": rb["grade"],
            "total_N": cd["total_N"],
            "coverage_days": f"{cd['coverage_days']}/4",
            "avg_WR_pct": cd["avg_WR_pct"],
            "avg_avg_ret_pct": cd["avg_avg_ret_pct"],
            "avg_excess_ret_pct": cd["avg_excess_ret_pct"],
            "wr_std_pp": cd["wr_std_pp"],
            "cv_N_pct": cd["cv_N_pct"],
            "cv_WR_pct": cd["cv_WR_pct"],
            "cv_avg_ret_pct": cd["cv_avg_ret_pct"],
            "max_single_day_N_pct": cd["max_single_day_N_pct"],
            "decay_warning": r["intraday"]["decay_warning"],
            "decay_detail": r["intraday"]["decay_detail"],
        })

    output = {
        "meta": {
            "agent": "Agent C — 信号组合跨日稳定性分析",
            "dates_analyzed": DATES,
            "total_dates": len(DATES),
            "signal_groups": list(SIGNAL_GROUPS.keys()),
        },
        "summary_table": summary_table,
        "signal_groups": results,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8-sig") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  结果已保存: {OUTPUT_PATH}")

    # ── 打印最终摘要 ──
    print("\n" + "=" * 60)
    print("最终摘要")
    print("=" * 60)
    print(f"{'信号组':<12} {'等级':<12} {'总N':<8} {'覆盖':<8} {'avg_WR':<10} {'avg_ret':<10} {'超额':<10} {'WR波动':<8} {'衰减'}")
    print("-" * 85)
    for row in summary_table:
        wr_str = f"{row['avg_WR_pct']:.1f}%" if row['avg_WR_pct'] else "N/A"
        ret_str = f"{row['avg_avg_ret_pct']:.4f}%" if row['avg_avg_ret_pct'] else "N/A"
        ex_str = f"{row['avg_excess_ret_pct']:.4f}%" if row['avg_excess_ret_pct'] else "N/A"
        wr_std = f"{row['wr_std_pp']:.2f}pp" if row['wr_std_pp'] is not None else "N/A"
        decay = "YES" if row['decay_warning'] else "no"
        print(f"{row['signal_group']:<12} {row['reliability']:<12} {row['total_N']:<8} {row['coverage_days']:<8} {wr_str:<10} {ret_str:<10} {ex_str:<10} {wr_std:<8} {decay}")

    print("\n  可靠性等级说明:")
    print("    S级: WR>55% + N>100 + 跨日波动<2pp + 均收益为正")
    print("    A级: WR>45% + N>100 + 波动<5pp")
    print("    B级: WR>40% + N>50 + 波动<10pp")
    print("    排除: 波动>10pp / N<30 / 80%样本集中单日")


if __name__ == "__main__":
    main()
