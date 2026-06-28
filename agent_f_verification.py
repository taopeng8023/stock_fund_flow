#!/usr/bin/env python
"""
Agent F: 独立验证 Agent C 的稳定性分析结论
==========================================
从 enriched CSV 原始数据独立重新计算所有指标，与 Agent C 对比。
"""

import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path("/Users/taopeng/PycharmProjects/stock_fund_flow/research_data/backtest/1430_entry")
DATES = ["20260622", "20260623", "20260624", "20260625"]
OUTPUT_PATH = BASE_DIR / "agent_f_verification.json"

# ── 信号组定义（与 Agent C 完全一致） ──
def has_signal(combined_signals_str, target):
    if not combined_signals_str:
        return False
    signals = set(s.strip() for s in combined_signals_str.split(",") if s.strip())
    return target in signals

def has_start_signal(start_signals_str, target):
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

# ── 时段分类 ──
def classify_period(timestr):
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


def read_all_snapshots():
    """读入所有 enriched CSV，返回 data[date][timestr] = list of row dicts"""
    data = defaultdict(lambda: defaultdict(list))
    for date_str in DATES:
        enriched_dir = BASE_DIR / date_str / "enriched"
        if not enriched_dir.exists():
            print(f"  [WARN] 缺少 enriched 目录: {enriched_dir}")
            continue
        for csv_file in sorted(enriched_dir.glob("snapshot_*_enriched.csv")):
            fname = csv_file.stem
            parts = fname.split("_")
            timestr = parts[1] if len(parts) >= 2 else "unknown"

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


def compute_stats(rows):
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
    vals = [v for v in values if v is not None]
    return statistics.mean(vals) if vals else None


def safe_cv(values):
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    mean_abs = abs(statistics.mean(vals))
    if mean_abs < 1e-10:
        return None
    return round(statistics.stdev(vals) / mean_abs * 100, 2)


def main():
    print("=" * 70)
    print("Agent F: 独立验证 Agent C 稳定性分析")
    print("=" * 70)

    # ── 读取数据 ──
    print("\n>>> 读取 enriched 快照数据...")
    all_data = read_all_snapshots()
    for d in DATES:
        n_snaps = len(all_data.get(d, {}))
        total_rows = sum(len(rows) for rows in all_data.get(d, {}).values())
        print(f"  {d}: {n_snaps} 快照, {total_rows} 行")

    # ── 按日期+信号组收集 ──
    daily_signal_rows = defaultdict(lambda: defaultdict(list))
    all_rows_by_date = defaultdict(list)

    for date_str in DATES:
        for timestr, rows in all_data.get(date_str, {}).items():
            all_rows_by_date[date_str].extend(rows)
            for row in rows:
                for sg_name, filter_fn in SIGNAL_GROUPS.items():
                    if filter_fn(row):
                        daily_signal_rows[sg_name][date_str].append(row)

    # ── 全市场每日统计 ──
    market_daily_stats = {}
    for date_str in DATES:
        valid = [r for r in all_rows_by_date[date_str] if r.get("收益%_float") is not None]
        st = compute_stats(valid)
        market_daily_stats[date_str] = st

    # ── 每个信号组每日统计 ──
    signal_daily_stats = defaultdict(dict)
    for sg_name in SIGNAL_GROUPS:
        for date_str in DATES:
            rows = daily_signal_rows[sg_name].get(date_str, [])
            signal_daily_stats[sg_name][date_str] = compute_stats(rows)

    # ── 按信号组+日内时段统计 ──
    periods = ["早盘(0930-1030)", "午盘(1030-1130)", "午后(1300-1430)", "尾盘(1430-1500)"]
    intradays = {}

    for sg_name in SIGNAL_GROUPS:
        intraday = {}
        for period in periods:
            period_rows = []
            for date_str in DATES:
                for timestr, rows in all_data.get(date_str, {}).items():
                    if classify_period(timestr) == period:
                        for row in rows:
                            if SIGNAL_GROUPS[sg_name](row):
                                period_rows.append(row)
            intraday[period] = compute_stats(period_rows)
        intradays[sg_name] = intraday

    # ── Agent C 的宣称值 ──
    agent_c_claims = {
        "P0": {"wr_std_pp": 10.40, "total_N": 1456, "avg_WR": 66.53, "max_single_pct": 55.1},
        "P1": {"wr_std_pp": 14.60, "total_N": 617, "avg_WR": 80.84, "max_single_pct": 61.6},
        "P2": {"wr_std_pp": 64.18, "total_N": 243, "avg_WR": 45.38, "max_single_pct": 80.2},
        "P3": {"wr_std_pp": 24.61, "total_N": 5101, "avg_WR": 60.98, "max_single_pct": 49.6},
        "P4": {"wr_std_pp": 19.56, "total_N": 640, "avg_WR": 78.80, "max_single_pct": 59.4},
        "P34_alone": {"wr_std_pp": 14.44, "total_N": 9200, "avg_WR": 36.73, "max_single_pct": 63.2},
    }

    # ── 逐项验证 ──
    print("\n" + "=" * 70)
    print("逐项验证")
    print("=" * 70)

    verification_results = {}

    for sg_name in SIGNAL_GROUPS:
        print(f"\n--- {sg_name} ---")
        agent_c = agent_c_claims[sg_name]

        # 每日胜率列表
        daily_wr_list = []
        daily_n_list = []
        daily_ret_list = []
        daily_wr_dict = {}
        daily_n_dict = {}
        daily_ret_dict = {}

        for date_str in DATES:
            st = signal_daily_stats[sg_name][date_str]
            daily_n_dict[date_str] = st["N"]
            if st["WR"] is not None:
                daily_wr_dict[date_str] = st["WR"]
                daily_wr_list.append(st["WR"])
                daily_n_list.append(st["N"])
                daily_ret_list.append(st["avg_ret"])
            daily_ret_dict[date_str] = st["avg_ret"]

        # a. WR 标准差
        if len(daily_wr_list) >= 2:
            wr_std = round(statistics.stdev(daily_wr_list), 2)
        else:
            wr_std = 0.0 if len(daily_wr_list) <= 1 else None

        # b. CV
        cv_n = safe_cv(daily_n_list)
        cv_wr = safe_cv(daily_wr_list)
        cv_ret = safe_cv(daily_ret_list)

        # c. 日均胜率 / 总样本 / 样本集中度
        avg_wr = safe_mean(daily_wr_list)
        total_n = sum(daily_n_list)
        max_single_pct = round(max(daily_n_list) / total_n * 100, 1) if total_n > 0 else 100

        # 日内衰减
        wr_seq = [intradays[sg_name][p]["WR"] for p in periods if intradays[sg_name][p]["WR"] is not None]
        ret_seq = [intradays[sg_name][p]["avg_ret"] for p in periods if intradays[sg_name][p]["avg_ret"] is not None]

        half = len(wr_seq) // 2
        first_half_wr = safe_mean(wr_seq[:half]) if len(wr_seq) >= 2 else None
        second_half_wr = safe_mean(wr_seq[half:]) if len(wr_seq) >= 2 else None
        wr_delta = None
        if first_half_wr is not None and second_half_wr is not None:
            wr_delta = round(second_half_wr - first_half_wr, 2)

        # d. 超额收益
        avg_excess = None
        daily_excess = {}
        for date_str in DATES:
            mr = market_daily_stats[date_str]["avg_ret"]
            sr = signal_daily_stats[sg_name][date_str]["avg_ret"]
            if mr is not None and sr is not None:
                exc = round(sr - mr, 4)
                daily_excess[date_str] = exc
        if daily_excess:
            avg_excess = round(statistics.mean(list(daily_excess.values())), 4)

        # 对比
        claim_wr_std = agent_c["wr_std_pp"]
        claim_total_n = agent_c["total_N"]
        claim_max_pct = agent_c["max_single_pct"]
        claim_avg_wr = agent_c["avg_WR"]

        wr_std_match = abs(wr_std - claim_wr_std) < 0.02
        total_n_match = total_n == claim_total_n
        max_pct_match = abs(max_single_pct - claim_max_pct) < 0.05
        avg_wr_match = abs((avg_wr or 0) - claim_avg_wr) < 0.02

        matches = {
            "wr_std_pp": {"agent_c": claim_wr_std, "agent_f": wr_std, "match": wr_std_match, "diff": round(wr_std - claim_wr_std, 4)},
            "total_N": {"agent_c": claim_total_n, "agent_f": total_n, "match": total_n_match, "diff": total_n - claim_total_n},
            "max_single_day_N_pct": {"agent_c": claim_max_pct, "agent_f": max_single_pct, "match": max_pct_match, "diff": round(max_single_pct - claim_max_pct, 2)},
            "avg_WR_pct": {"agent_c": claim_avg_wr, "agent_f": round(avg_wr, 2) if avg_wr else None, "match": avg_wr_match, "diff": round((avg_wr or 0) - claim_avg_wr, 4)},
        }

        print(f"  WR_std:  Agent C={claim_wr_std}pp  vs  Agent F={wr_std}pp  -> {'MATCH' if wr_std_match else 'MISMATCH'}")
        print(f"  total_N: Agent C={claim_total_n}   vs  Agent F={total_n}   -> {'MATCH' if total_n_match else 'MISMATCH'}")
        print(f"  max_pct: Agent C={claim_max_pct}%  vs  Agent F={max_single_pct}%  -> {'MATCH' if max_pct_match else 'MISMATCH'}")
        print(f"  avg_WR:  Agent C={claim_avg_wr}%   vs  Agent F={round(avg_wr, 2) if avg_wr else 'N/A'}%   -> {'MATCH' if avg_wr_match else 'MISMATCH'}")
        print(f"  日内 WR 序列: {wr_seq}")
        print(f"  日内 前半vs后半 WR 差值: {wr_delta}pp")
        print(f"  日均超额收益: {avg_excess}")

        all_matches = all(m["match"] for m in matches.values())
        print(f"  >>> 全部一致: {all_matches}")

        verification_results[sg_name] = {
            "cross_day": {
                "daily_N": daily_n_dict,
                "daily_WR_pct": daily_wr_dict,
                "daily_avg_ret_pct": daily_ret_dict,
                "total_N": total_n,
                "avg_WR_pct": round(avg_wr, 2) if avg_wr else None,
                "wr_std_pp": wr_std,
                "cv_N_pct": cv_n,
                "cv_WR_pct": cv_wr,
                "cv_avg_ret_pct": cv_ret,
                "max_single_day_N_pct": max_single_pct,
                "avg_excess_ret_pct": avg_excess,
            },
            "intraday": {
                p: intradays[sg_name][p] for p in periods
            },
            "intraday_wr_sequence": wr_seq,
            "intraday_ret_sequence": [round(r, 4) if r else None for r in ret_seq],
            "first_vs_second_half_wr_delta_pp": wr_delta,
            "matches": matches,
        }

    # ── 全市场跨日 WR ──
    print("\n--- 全市场 跨日 WR ---")
    market_wr = {}
    for date_str in DATES:
        st = market_daily_stats[date_str]
        wr = st["WR"]
        market_wr[date_str] = wr
        print(f"  {date_str}: WR={wr}%, avg_ret={st['avg_ret']:.4f}%, N={st['N']}")

    market_wr_list = [v for v in market_wr.values() if v is not None]
    market_wr_std = round(statistics.stdev(market_wr_list), 2) if len(market_wr_list) >= 2 else 0
    market_avg_wr = round(safe_mean(market_wr_list), 2) if market_wr_list else None
    print(f"  全市场 avg_WR={market_avg_wr}%, WR_std={market_wr_std}pp")

    # ── 核心主张验证 ──
    print("\n" + "=" * 70)
    print("核心主张验证")
    print("=" * 70)

    claims_checks = {}

    # 主张1: 所有信号组 WR 波动都 > 10pp
    all_gt_10 = all(
        verification_results[sg]["cross_day"]["wr_std_pp"] is not None
        and verification_results[sg]["cross_day"]["wr_std_pp"] > 10
        for sg in SIGNAL_GROUPS
    )
    individual_gt_10 = {}
    for sg in SIGNAL_GROUPS:
        val = verification_results[sg]["cross_day"]["wr_std_pp"]
        individual_gt_10[sg] = f"{val}pp > 10pp: {'YES' if (val is not None and val > 10) else 'NO'}"

    claims_checks["claim_all_wr_std_gt_10pp"] = all_gt_10
    claims_checks["claim_all_wr_std_detail"] = individual_gt_10

    # 主张2: P0 的 WR std = 10.40pp
    p0_match = verification_results["P0"]["matches"]["wr_std_pp"]["match"]
    claims_checks["claim_p0_wr_std_10_40"] = p0_match

    # 主张3: P2 80%+ 样本集中单日
    p2_max_pct = verification_results["P2"]["cross_day"]["max_single_day_N_pct"]
    p2_concentrated = p2_max_pct > 80
    claims_checks["claim_p2_80pct_single_day"] = p2_concentrated
    claims_checks["p2_max_single_day_pct"] = p2_max_pct

    # 主张4: P0 日内衰减
    p0_wr_seq = verification_results["P0"]["intraday_wr_sequence"]
    p0_first = safe_mean(p0_wr_seq[:2]) if len(p0_wr_seq) >= 2 else None
    p0_second = safe_mean(p0_wr_seq[2:]) if len(p0_wr_seq) >= 2 else None
    p0_decay = (p0_second is not None and p0_first is not None and p0_second < p0_first - 3)
    claims_checks["claim_p0_intraday_decay"] = p0_decay
    claims_checks["p0_first_half_wr"] = round(p0_first, 2) if p0_first else None
    claims_checks["p0_second_half_wr"] = round(p0_second, 2) if p0_second else None

    # 主张5: P4 日内衰减
    p4_wr_seq = verification_results["P4"]["intraday_wr_sequence"]
    p4_first = safe_mean(p4_wr_seq[:2]) if len(p4_wr_seq) >= 2 else None
    p4_second = safe_mean(p4_wr_seq[2:]) if len(p4_wr_seq) >= 2 else None
    p4_decay = (p4_second is not None and p4_first is not None and p4_second < p4_first - 3)
    claims_checks["claim_p4_intraday_decay"] = p4_decay
    claims_checks["p4_first_half_wr"] = round(p4_first, 2) if p4_first else None
    claims_checks["p4_second_half_wr"] = round(p4_second, 2) if p4_second else None

    for claim_name, result in claims_checks.items():
        if not claim_name.startswith("p"):
            print(f"  {claim_name}: {result}")

    print(f"\n  claim_all_wr_std_gt_10pp : {all_gt_10}")
    for sg, detail in individual_gt_10.items():
        print(f"    {sg}: {detail}")

    print(f"\n  claim_p0_wr_std_10_40 : {p0_match} (Agent C=10.40, Agent F={verification_results['P0']['cross_day']['wr_std_pp']})")
    print(f"\n  claim_p2_80pct_single_day : {p2_concentrated} (max={p2_max_pct}%)")
    print(f"\n  claim_p0_intraday_decay : {p0_decay} (前={p0_first}, 后={p0_second})")
    print(f"\n  claim_p4_intraday_decay : {p4_decay} (前={p4_first}, 后={p4_second})")

    # ── 输出 ──
    output = {
        "meta": {
            "agent": "Agent F — 独立验证 Agent C 稳定性分析",
            "verified_claims": list(claims_checks.keys()),
            "dates_analyzed": DATES,
            "signal_groups": list(SIGNAL_GROUPS.keys()),
        },
        "verification_results": verification_results,
        "market_cross_day": {
            "daily_WR_pct": market_wr,
            "avg_WR_pct": market_avg_wr,
            "wr_std_pp": market_wr_std,
        },
        "claims_verification": claims_checks,
        "overall_assessment": {
            "all_matches": all(
                all(m["match"] for m in verification_results[sg]["matches"].values())
                for sg in SIGNAL_GROUPS
            ),
            "all_claims_verified": all(
                claims_checks.get(k, True)
                for k in [
                    "claim_all_wr_std_gt_10pp",
                    "claim_p0_wr_std_10_40",
                    "claim_p2_80pct_single_day",
                    "claim_p0_intraday_decay",
                    "claim_p4_intraday_decay",
                ]
            ),
        },
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8-sig") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n\n>>> 结果已保存: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
