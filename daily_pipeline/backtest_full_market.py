"""
全市场隔夜回测 — 所有 scores.csv 股票, 非仅 filter 选股
入场: pick_date scores.csv 最新价
出场: eval_date 09:45-10:15 窗口快照均价
规则: FUNDAMENTAL_RULES.md 5条
输出: 每笔交易含信号标签, 供信号组合发现

用法:
  python daily_pipeline/backtest_full_market.py
  python daily_pipeline/backtest_full_market.py --output research_data/backtest/overnight_v2/full_market_trades.json
"""

import csv
import json
import os
import sys
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta

BJS_TZ = timezone(timedelta(hours=8))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH_ROOT = os.path.join(PROJECT_ROOT, "research_data")
OUTPUT_DIR = os.path.join(RESEARCH_ROOT, "backtest", "overnight_v2")

DATE_PAIRS = [
    ("20260622", "20260623"),
    ("20260623", "20260624"),
    ("20260624", "20260625"),
    ("20260625", "20260626"),
]

MAX_ENTRY_CHG = 9.9
WINDOW_START = "0945"
WINDOW_END = "1015"
MIN_TURNOVER_YI = 0.5

# 需追踪的信号 token (P32-P37 + E1-E6 + P_low_vol + P_high_price)
SIGNAL_TOKENS = [
    "P32_pump_risk", "P32_extreme", "P32_ratio_accel", "P32_ratio_strong",
    "P33_margin_strong", "P33_margin_moderate", "P33_margin_weak",
    "P34_gap_strong", "P34_gap_reverse", "P34_gap_trap", "P34_P32_combo", "P34_gap_standalone",
    "P35_short_cover", "P35_short_pressure", "P35_short_moderate", "P35_short_heavy",
    "P36_overheat", "P36_cool",
    "P37_momentum_up", "P37_momentum_down",
    "E1_low_start", "E2_volume_early", "E3_strong_start",
    "E4_gap_start", "E5_ratio_early", "E6_short_squeeze",
    "P_low_vol_ratio", "P_low_vol", "P_high_price", "P6_retail",
]


def get_exit_prices(eval_date, target_codes):
    """获取 eval_date 09:45-10:15 区间快照均价, 返回 {code: avg_price}"""
    snap_dir = os.path.join(RESEARCH_ROOT, eval_date, "intraday")
    if not os.path.exists(snap_dir):
        return {}, 0

    all_files = os.listdir(snap_dir)
    fund_snaps = sorted([f for f in all_files if f.startswith("fund_flow_")])

    exit_snaps = []
    for fn in fund_snaps:
        ts = fn.replace("fund_flow_", "").replace(".csv", "")
        if WINDOW_START <= ts[:4] <= WINDOW_END:
            exit_snaps.append(fn)

    exit_prices = defaultdict(list)
    for fn in exit_snaps:
        fpath = os.path.join(snap_dir, fn)
        with open(fpath, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                code = r.get("代码", "")
                if code not in target_codes:
                    continue
                val = r.get("最新价", "")
                if val in ("-", "", None):
                    continue
                try:
                    exit_prices[code].append(float(val))
                except ValueError:
                    continue

    snap_count = len(exit_snaps)
    exit_avg = {c: sum(v) / len(v) for c, v in exit_prices.items()}
    return exit_avg, snap_count


def detect_ex_dividend(pick_date, eval_date, entry_prices):
    """检测除权除息: 对比 entry_price vs eval_date 昨收"""
    ex_map = {}
    try:
        eval_snap_dir = os.path.join(RESEARCH_ROOT, eval_date, "intraday")
        if not os.path.exists(eval_snap_dir) or not entry_prices:
            return ex_map

        eval_fund_files = sorted([f for f in os.listdir(eval_snap_dir)
                                  if f.startswith("fund_flow_")])
        if not eval_fund_files:
            return ex_map

        first_snap = os.path.join(eval_snap_dir, eval_fund_files[0])
        with open(first_snap, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                c = r.get("代码", "")
                if c in entry_prices:
                    zs = r.get("昨收", "")
                    if zs and zs not in ("-", "", None):
                        try:
                            eval_zs = float(zs)
                            entry = entry_prices[c]
                            ex_map[c] = abs(entry - eval_zs) / entry > 0.02
                        except (ValueError, ZeroDivisionError):
                            pass
    except Exception:
        pass
    return ex_map


def process_date_pair(pick_date, eval_date):
    """处理单个日期对, 返回全量交易列表"""
    scores_path = os.path.join(RESEARCH_ROOT, pick_date, "scores.csv")
    if not os.path.exists(scores_path):
        print(f"  ✗ {pick_date}: no scores.csv")
        return []

    # 读取所有股票
    rows = []
    with open(scores_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)

    print(f"  {pick_date}: {len(rows)} 只股票")

    # 收集 entry 价格 & 信号
    entry_prices = {}
    stock_data = {}
    for r in rows:
        try:
            price = float(r.get("最新价", 0) or 0)
        except (ValueError, TypeError):
            continue
        if price <= 0:
            continue

        entry_chg = float(r.get("涨跌幅", 0) or 0)
        if entry_chg >= MAX_ENTRY_CHG:
            continue

        turnover = float(r.get("成交额", 0) or 0)
        if turnover < MIN_TURNOVER_YI * 1e8:
            continue

        code = r.get("代码", "")
        signals = r.get("综合信号", "")
        early_sigs = r.get("启动信号", "")
        all_sigs = f"{signals},{early_sigs}"

        # 提取存在的信号 token
        present_signals = [s for s in SIGNAL_TOKENS if s in all_sigs]

        entry_prices[code] = price
        stock_data[code] = {
            "code": code,
            "name": r.get("名称", ""),
            "entry": price,
            "entry_chg": entry_chg,
            "score": float(r.get("综合得分", 0) or 0),
            "capital": float(r.get("资金得分", 0.5) or 0.5),
            "industry": r.get("行业", ""),
            "signals": present_signals,
            "all_sigs": all_sigs,
        }

    codes = set(entry_prices.keys())
    print(f"  {pick_date}: {len(codes)} 只合格 (排除涨停/低价/低成交额)")

    # 获取出场价
    exit_avg, snap_count = get_exit_prices(eval_date, codes)
    print(f"  {eval_date}: {snap_count} 窗口快照, {len(exit_avg)} 只有出场价")

    # 除权检测
    ex_map = detect_ex_dividend(pick_date, eval_date, entry_prices)

    # 快照质量
    if snap_count >= 3:
        snap_quality = "good"
    elif snap_count >= 1:
        snap_quality = "thin"
    else:
        snap_quality = "insufficient"

    # 生成交易记录
    trades = []
    for code, data in stock_data.items():
        exit_p = exit_avg.get(code)
        if exit_p is None:
            continue

        entry = data["entry"]
        ret = round((exit_p - entry) / entry * 100, 2)
        win = ret > 0

        trades.append({
            "pick_date": pick_date,
            "eval_date": eval_date,
            "code": code,
            "name": data["name"],
            "entry": round(entry, 2),
            "exit": round(exit_p, 2),
            "ret": ret,
            "win": win,
            "entry_chg": data["entry_chg"],
            "score": data["score"],
            "capital": data["capital"],
            "industry": data["industry"],
            "signals": data["signals"],
            "ex_dividend_possible": ex_map.get(code, False),
            "snap_quality": snap_quality,
            "exit_snap_count": snap_count,
        })

    print(f"  → {len(trades)} 笔有效交易")
    return trades


def compute_summary(trades):
    """计算汇总统计"""
    if not trades:
        return {"total_n": 0, "note": "无交易"}

    n = len(trades)
    wins = sum(1 for t in trades if t["win"])
    wr = round(wins / n * 100, 1)
    avg_ret = round(sum(t["ret"] for t in trades) / n, 2)
    total_ret = round(sum(t["ret"] for t in trades), 2)
    rets = sorted([t["ret"] for t in trades])
    median = rets[n // 2] if n % 2 else (rets[n // 2 - 1] + rets[n // 2]) / 2

    # 按日统计
    by_date = defaultdict(lambda: {"n": 0, "wins": 0, "rets": []})
    for t in trades:
        k = t["pick_date"]
        by_date[k]["n"] += 1
        if t["win"]:
            by_date[k]["wins"] += 1
        by_date[k]["rets"].append(t["ret"])

    date_stats = {}
    for k, v in by_date.items():
        date_stats[k] = {
            "n": v["n"],
            "wr": round(v["wins"] / v["n"] * 100, 1),
            "avg_ret": round(sum(v["rets"]) / v["n"], 2),
            "total_ret": round(sum(v["rets"]), 2),
        }

    # 得分分层
    score_bins = [(0, 0.3), (0.3, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0)]
    score_stats = {}
    for lo, hi in score_bins:
        bin_trades = [t for t in trades if lo <= t["score"] < hi]
        if bin_trades:
            s_wins = sum(1 for t in bin_trades if t["win"])
            score_stats[f"{lo}-{hi}"] = {
                "n": len(bin_trades),
                "wr": round(s_wins / len(bin_trades) * 100, 1),
                "avg_ret": round(sum(t["ret"] for t in bin_trades) / len(bin_trades), 2),
            }

    # 快照质量
    sq_good = sum(1 for t in trades if t["snap_quality"] == "good")
    sq_thin = sum(1 for t in trades if t["snap_quality"] == "thin")

    return {
        "total_n": n,
        "total_wins": wins,
        "total_losses": n - wins,
        "wr": wr,
        "avg_ret": avg_ret,
        "total_ret": total_ret,
        "median_ret": round(median, 2),
        "max_ret": round(max(rets), 2),
        "min_ret": round(min(rets), 2),
        "std_ret": round((sum((x - avg_ret) ** 2 for x in rets) / n) ** 0.5, 2),
        "by_date": date_stats,
        "by_score_range": score_stats,
        "snap_quality_good": sq_good,
        "snap_quality_thin": sq_thin,
        "method": "全市场 backtest_overnight | 0945-1015 avg exit | FUNDAMENTAL_RULES v1",
    }


def analyze_signal_combos(trades):
    """分析信号组合胜率 — 单个/双/三信号 + GLOBAL_BLOCK 验证"""
    # 单个信号
    single = defaultdict(lambda: {"n": 0, "wins": 0, "rets": []})
    # 双信号组合
    dual = defaultdict(lambda: {"n": 0, "wins": 0, "rets": []})
    # 三信号组合
    triple = defaultdict(lambda: {"n": 0, "wins": 0, "rets": []})

    # 当前 tier 匹配的信号组合 (与 filter_overnight.py TIERS 同步)
    tier_signals = {
        "A1": ["P37_momentum_up", "E6_short_squeeze", "P_high_price"],
        "A2": ["P35_short_cover", "P37_momentum_up", "P_high_price"],
        "A3": ["P_high_price", "P35_short_pressure", "P34_gap_reverse"],
        "B1": ["P34_gap_reverse", "P35_short_cover"],
        "B2": ["P_low_vol_ratio", "P_high_price"],
        "B3": ["P37_momentum_up", "P_high_price"],
        "B4": ["E6_short_squeeze", "P_high_price"],
        "B5": ["P35_short_cover", "E6_short_squeeze"],
        "B6": ["P_low_vol_ratio", "P35_short_cover"],
        "C1": ["P35_short_cover"],
    }

    for t in trades:
        sigs = t["signals"]

        # 单个信号统计
        for s in sigs:
            single[s]["n"] += 1
            if t["win"]:
                single[s]["wins"] += 1
            single[s]["rets"].append(t["ret"])

        # 双信号组合
        for i in range(len(sigs)):
            for j in range(i + 1, len(sigs)):
                key = f"{sigs[i]}+{sigs[j]}"
                dual[key]["n"] += 1
                if t["win"]:
                    dual[key]["wins"] += 1
                dual[key]["rets"].append(t["ret"])

        # 三信号组合
        for i in range(len(sigs)):
            for j in range(i + 1, len(sigs)):
                for k in range(j + 1, len(sigs)):
                    key = f"{sigs[i]}+{sigs[j]}+{sigs[k]}"
                    triple[key]["n"] += 1
                    if t["win"]:
                        triple[key]["wins"] += 1
                    triple[key]["rets"].append(t["ret"])

    # 计算 WR
    def calc_wr(stats, min_n=5):
        result = {}
        for k, v in stats.items():
            if v["n"] >= min_n:
                result[k] = {
                    "n": v["n"],
                    "wr": round(v["wins"] / v["n"] * 100, 1),
                    "avg_ret": round(sum(v["rets"]) / v["n"], 2),
                }
        return result

    # 计算当前 tier 在全市场中的表现
    tier_perf = {}
    for tk, required_sigs in tier_signals.items():
        tier_trades = [t for t in trades if all(s in t["signals"] for s in required_sigs)]
        if tier_trades and len(tier_trades) >= 3:
            w = sum(1 for t in tier_trades if t["win"])
            tier_perf[tk] = {
                "n": len(tier_trades),
                "wr": round(w / len(tier_trades) * 100, 1),
                "avg_ret": round(sum(t["ret"] for t in tier_trades) / len(tier_trades), 2),
                "signals": required_sigs,
            }

    # 验证当前避雷 + 已移除的信号
    block_signals = [
        "P36_overheat", "P37_momentum_down", "P33_margin_weak",
        "P35_short_heavy", "P6_retail",
        # 已从硬屏蔽移除，验证全市场表现
        "P35_short_moderate", "P33_margin_moderate",
        "E1_low_start", "E3_strong_start",
    ]
    block_perf = {}
    for bs_sig in block_signals:
        bt = [t for t in trades if bs_sig in t["signals"]]
        if bt:
            bw = sum(1 for t in bt if t["win"])
            block_perf[bs_sig] = {
                "n": len(bt),
                "wr": round(bw / len(bt) * 100, 1),
                "avg_ret": round(sum(t["ret"] for t in bt) / len(bt), 2),
            }

    return {
        "single": calc_wr(single, min_n=30),
        "dual": calc_wr(dual, min_n=30),
        "triple": calc_wr(triple, min_n=30),
        "tier_performance": tier_perf,
        "block_signal_performance": block_perf,
    }


def main():
    # --pair 模式: 单日期对 (供多 Agent 并行)
    if "--pair" in sys.argv:
        idx = sys.argv.index("--pair")
        pick_date = sys.argv[idx + 1]
        eval_date = sys.argv[idx + 2]
        output_path = sys.argv[idx + 3] if len(sys.argv) > idx + 3 else os.path.join(
            OUTPUT_DIR, f"trades_{pick_date}_{eval_date}.json")

        print("=" * 70)
        print(f"  单对回测: {pick_date} → {eval_date}")
        print("  入场: scores.csv 最新价 | 出场: 09:45-10:15 均价")
        print("=" * 70)

        all_trades = process_date_pair(pick_date, eval_date)
    else:
        output_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
            OUTPUT_DIR, "full_market_trades.json")

        print("=" * 70)
        print("  全市场隔夜回测")
        print("  入场: scores.csv 最新价 | 出场: 09:45-10:15 均价")
        print("  规则: FUNDAMENTAL_RULES.md (涨停排除/隔夜套利)")
        print("=" * 70)

        all_trades = []
        for pick_date, eval_date in DATE_PAIRS:
            print(f"\n{pick_date} → {eval_date}")
            trades = process_date_pair(pick_date, eval_date)
            all_trades.extend(trades)

    print(f"\n{'=' * 70}")
    print(f"  总计: {len(all_trades)} 笔交易")

    if not all_trades:
        print("  无有效交易!")
        return

    # 汇总
    summary = compute_summary(all_trades)
    print(f"  胜率: {summary['wr']}%  |  均收益: {summary['avg_ret']}%  |  中位数: {summary['median_ret']}%")
    print(f"  快照质量: good={summary['snap_quality_good']} thin={summary['snap_quality_thin']}")

    print(f"\n逐日:")
    for d, s in summary["by_date"].items():
        print(f"  {d}: {s['n']}笔 WR={s['wr']}% avg={s['avg_ret']}%")

    print(f"\n得分分层:")
    for rng, s in summary["by_score_range"].items():
        print(f"  score {rng}: {s['n']}笔 WR={s['wr']}% avg={s['avg_ret']}%")

    # 信号分析
    print(f"\n信号组合分析...")
    signal_analysis = analyze_signal_combos(all_trades)

    # 最佳信号排行
    sig_ranked = sorted(signal_analysis["single"].items(),
                        key=lambda x: (-x[1]["wr"], -x[1]["n"]))
    print(f"\n最佳单信号 (n>=30):")
    for i, (sig, perf) in enumerate(sig_ranked[:10]):
        print(f"  {i+1}. {sig}: WR={perf['wr']}% avg={perf['avg_ret']}% n={perf['n']}")

    dual_ranked = sorted(signal_analysis["dual"].items(),
                         key=lambda x: (-x[1]["wr"], -x[1]["n"]))
    print(f"\n最佳双信号 (n>=30):")
    for i, (sig, perf) in enumerate(dual_ranked[:10]):
        print(f"  {i+1}. {sig}: WR={perf['wr']}% avg={perf['avg_ret']}% n={perf['n']}")

    triple_ranked = sorted(signal_analysis["triple"].items(),
                           key=lambda x: (-x[1]["wr"], -x[1]["n"]))
    print(f"\n最佳三信号 (n>=30):")
    for i, (sig, perf) in enumerate(triple_ranked[:10]):
        print(f"  {i+1}. {sig}: WR={perf['wr']}% avg={perf['avg_ret']}% n={perf['n']}")

    # 当前 tier 表现
    print(f"\n当前 Tier 在全市场中的表现:")
    for tk in ["A1", "A2", "A3", "B1", "B2", "B3", "B4", "B5", "B6", "C1"]:
        if tk in signal_analysis["tier_performance"]:
            p = signal_analysis["tier_performance"][tk]
            print(f"  {tk}: WR={p['wr']}% avg={p['avg_ret']}% n={p['n']} signals={p['signals']}")
        else:
            print(f"  {tk}: (无匹配或 n<3)")

    # 避雷信号验证
    print(f"\n避雷信号表现验证:")
    for sig in ["P36_overheat", "P35_short_moderate", "P33_margin_moderate",
                "P37_momentum_down", "E1_low_start", "E3_strong_start"]:
        if sig in signal_analysis["block_signal_performance"]:
            p = signal_analysis["block_signal_performance"][sig]
            print(f"  {sig}: WR={p['wr']}% avg={p['avg_ret']}% n={p['n']}")

    # 保存
    output = {
        "generated": datetime.now(BJS_TZ).strftime("%Y-%m-%d %H:%M"),
        "summary": summary,
        "signal_analysis": {
            "top_single": [{"signal": k, **v} for k, v in sig_ranked[:20]],
            "top_dual": [{"combo": k, **v} for k, v in dual_ranked[:20]],
            "top_triple": [{"combo": k, **v} for k, v in triple_ranked[:20]],
        },
        "tier_performance": signal_analysis["tier_performance"],
        "block_signal_performance": signal_analysis["block_signal_performance"],
        "trades": all_trades,
        "trade_count": len(all_trades),
    }

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n✓ 已保存: {output_path}")

    # 信号分析精简版 (供 agent 分析)
    if "--pair" in sys.argv:
        analysis_path = os.path.join(OUTPUT_DIR, f"signal_analysis_{pick_date}_{eval_date}.json")
    else:
        analysis_path = os.path.join(OUTPUT_DIR, "full_market_signal_analysis.json")
    analysis_output = {
        "generated": datetime.now(BJS_TZ).strftime("%Y-%m-%d %H:%M"),
        "summary": summary,
        "top_single": [{"signal": k, **v} for k, v in sig_ranked[:30]],
        "top_dual": [{"combo": k, **v} for k, v in dual_ranked[:30]],
        "top_triple": [{"combo": k, **v} for k, v in triple_ranked[:30]],
        "tier_performance": signal_analysis["tier_performance"],
        "block_signal_performance": signal_analysis["block_signal_performance"],
    }
    with open(analysis_path, "w", encoding="utf-8") as f:
        json.dump(analysis_output, f, ensure_ascii=False, indent=2)
    print(f"✓ 已保存: {analysis_path}")

    return output


if __name__ == "__main__":
    main()
