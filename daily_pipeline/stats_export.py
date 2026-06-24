"""
回测统计导出 — 按日期生成全量信号/得分组合统计数据

输出:
  backtest_stats/<date>/signal_combos.csv       — 信号组合排名
  backtest_stats/<date>/score_signal_combos.csv — 得分×信号组合排名
  backtest_stats/<date>/daily_summary.json      — 当日汇总
  backtest_stats/latest/full_summary.json       — 全局汇总
"""

import csv, json, os, statistics
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone, timedelta

BJS_TZ = timezone(timedelta(hours=8))
PROJECT_ROOT = Path(__file__).parent.parent
RESEARCH_DIR = PROJECT_ROOT / "research_data"
BT_DIR = RESEARCH_DIR / "backtest" / "daily"
STATS_DIR = PROJECT_ROOT / "backtest_stats"


def _tof(v, d=0.0):
    if v is None or v == "" or v == "-": return d
    try: return float(v)
    except: return d


def export_stats(date_str: str):
    """导出指定日期的回测统计数据。"""
    bt_path = BT_DIR / f"backtest_{date_str}.csv"
    sc_path = RESEARCH_DIR / date_str / "scores.csv"
    if not bt_path.exists():
        print(f"  {date_str}: 无回测数据, 跳过")
        return

    date_dir = STATS_DIR / date_str
    date_dir.mkdir(parents=True, exist_ok=True)

    # 加载
    with open(bt_path, encoding="utf-8-sig") as f:
        bt = {r["代码"]: float(r.get("次日收益%",0)or 0) for r in csv.DictReader(f)}
    with open(sc_path, encoding="utf-8-sig") as f:
        scores = list(csv.DictReader(f))

    trades = []
    for r in scores:
        code = r["代码"]
        if code not in bt: continue
        trades.append({
            "ret": bt[code], "code": code,
            "score": float(r.get("综合得分",0)or 0),
            "capital": float(r.get("资金得分",0.5)or 0.5),
            "mcap": float(r.get("总市值",0)or 0),
            "chg": float(r.get("涨跌幅",0)or 0),
            "signals": r.get("综合信号",""),
        })

    N = len(trades)
    base_wr = sum(1 for t in trades if t["ret"]>0)/N if N else 0
    base_avg = sum(t["ret"] for t in trades)/N if N else 0

    # ── 1. 信号组合 ──
    SIGS = ["P34_gap_strong","P37_momentum_up","P32_ratio_accel",
            "P33_margin_strong","P35_short_cover",
            "P36_overheat","P35_short_pressure","P37_momentum_down"]

    signal_rows = []
    for s in SIGS:
        filtered = [t for t in trades if s in t["signals"]]
        if len(filtered) < 5: continue
        wr = sum(1 for t in filtered if t["ret"]>0)/len(filtered)
        avg = sum(t["ret"] for t in filtered)/len(filtered)
        signal_rows.append({"signal": f"+{s}", "count": len(filtered),
                            "win_rate": round(wr,4), "avg_return": round(avg,4)})

    # 双信号组合
    pos_sigs = ["P34_gap_strong","P37_momentum_up","P32_ratio_accel",
                "P33_margin_strong","P35_short_cover"]
    for i in range(len(pos_sigs)):
        for j in range(i+1, len(pos_sigs)):
            s1, s2 = pos_sigs[i], pos_sigs[j]
            filtered = [t for t in trades if s1 in t["signals"] and s2 in t["signals"]]
            if len(filtered) < 5: continue
            wr = sum(1 for t in filtered if t["ret"]>0)/len(filtered)
            avg = sum(t["ret"] for t in filtered)/len(filtered)
            signal_rows.append({"signal": f"+{s1}+{s2}", "count": len(filtered),
                                "win_rate": round(wr,4), "avg_return": round(avg,4)})

    # 正向+避雷
    neg_sigs = ["P36_overheat","P35_short_pressure"]
    for ps in pos_sigs:
        for ns in neg_sigs:
            filtered = [t for t in trades if ps in t["signals"] and ns not in t["signals"]]
            if len(filtered) < 5: continue
            wr = sum(1 for t in filtered if t["ret"]>0)/len(filtered)
            avg = sum(t["ret"] for t in filtered)/len(filtered)
            signal_rows.append({"signal": f"+{ps} -{ns}", "count": len(filtered),
                                "win_rate": round(wr,4), "avg_return": round(avg,4)})

    signal_rows.sort(key=lambda x: -x["win_rate"])

    with open(date_dir / "signal_combos.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["signal","count","win_rate","avg_return"])
        w.writeheader(); w.writerows(signal_rows)

    # ── 2. 得分×信号组合 ──
    score_rows = []
    for lo, hi, slabel in [(0.70,1.0,"分≥0.70"),(0.60,0.70,"分0.60-0.70"),(0.55,0.60,"分0.55-0.60")]:
        for cap_lo, cap_hi, clabel in [(0.80,1.0,"资≥0.80"),(0.70,0.80,"资0.70-0.80")]:
            for sig_label, sig_fn in [
                ("P34_gap", lambda t: "P34_gap_strong" in t["signals"]),
                ("P34_gap+P35_cover", lambda t: "P34_gap_strong" in t["signals"] and "P35_short_cover" in t["signals"]),
                ("P34_gap-P36", lambda t: "P34_gap_strong" in t["signals"] and "P36_overheat" not in t["signals"]),
                ("P37_up", lambda t: "P37_momentum_up" in t["signals"]),
                ("大盘>500亿", lambda t: t["mcap"] >= 500),
            ]:
                filtered = [t for t in trades
                            if lo <= t["score"] < hi and cap_lo <= t["capital"] < cap_hi
                            and sig_fn(t)]
                if len(filtered) < 5: continue
                wr = sum(1 for t in filtered if t["ret"]>0)/len(filtered)
                avg = sum(t["ret"] for t in filtered)/len(filtered)
                score_rows.append({
                    "combo": f"{slabel} {clabel} {sig_label}",
                    "count": len(filtered), "win_rate": round(wr,4),
                    "avg_return": round(avg,4),
                })

    score_rows.sort(key=lambda x: -x["win_rate"])

    with open(date_dir / "score_signal_combos.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["combo","count","win_rate","avg_return"])
        w.writeheader(); w.writerows(score_rows)

    # ── 3. 当日汇总 ──
    # Top50 统计
    scored_all = [(t["score"], t["ret"]) for t in trades]
    scored_all.sort(key=lambda x: -x[0])
    top50 = scored_all[:50]
    t50_wr = sum(1 for _, r in top50 if r > 0) / 50
    t50_avg = sum(r for _, r in top50) / 50

    summary = {
        "date": date_str,
        "total_stocks": N,
        "baseline_win_rate": round(base_wr, 4),
        "baseline_avg_return": round(base_avg, 4),
        "top50_win_rate": round(t50_wr, 4),
        "top50_avg_return": round(t50_avg, 4),
        "top50_excess": round(t50_avg - base_avg, 4),
        "top_signal_combos": [
            {"signal": r["signal"], "win_rate": r["win_rate"], "count": r["count"]}
            for r in signal_rows[:5]
        ],
        "top_score_combos": [
            {"combo": r["combo"], "win_rate": r["win_rate"], "count": r["count"]}
            for r in score_rows[:5]
        ],
        "weights": {"capital":0.19,"sector":0.10,"position":0.08,"intra_sector":0.06,
                    "multiday":0.06,"start_signal":0.03,"trend":0.01},
        "exported_at": datetime.now(BJS_TZ).isoformat(),
    }

    with open(date_dir / "daily_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"  {date_str}: {N}只 Top50胜率{t50_wr:.0%} 均{t50_avg:+.2f}% → {date_dir}")


def export_latest_summary():
    """导出全局汇总。"""
    latest_dir = STATS_DIR / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)

    # 汇总所有日期
    all_summaries = []
    for d in ["20260617","20260618","20260622","20260623"]:
        path = STATS_DIR / d / "daily_summary.json"
        if path.exists():
            with open(path) as f:
                all_summaries.append(json.load(f))

    if not all_summaries:
        return

    total_stocks = sum(s["total_stocks"] for s in all_summaries)
    avg_t50_wr = statistics.mean([s["top50_win_rate"] for s in all_summaries])
    avg_t50_ret = statistics.mean([s["top50_avg_return"] for s in all_summaries])
    avg_excess = statistics.mean([s["top50_excess"] for s in all_summaries])

    # 全局最优: 合并所有日期的信号组合(按胜率排序, 取样本≥20的)
    global_combos = defaultdict(lambda: {"count":0, "wins":0, "returns":[]})
    for d in ["20260617","20260618","20260622","20260623"]:
        path = STATS_DIR / d / "score_signal_combos.csv"
        if not path.exists(): continue
        with open(path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                key = r["combo"]
                global_combos[key]["count"] += int(r["count"])
                global_combos[key]["wins"] += int(float(r["win_rate"]) * int(r["count"]))
                global_combos[key]["returns"].append(float(r["avg_return"]))

    top_global = []
    for combo, data in global_combos.items():
        if data["count"] < 20: continue
        wr = data["wins"] / data["count"]
        avg_ret = statistics.mean(data["returns"])
        top_global.append({"combo": combo, "count": data["count"],
                           "win_rate": round(wr,4), "avg_return": round(avg_ret,4)})
    top_global.sort(key=lambda x: -x["win_rate"])

    full_summary = {
        "version": "v6 (sector 10% trend 1%)",
        "dates": [s["date"] for s in all_summaries],
        "total_stocks": total_stocks,
        "avg_top50_win_rate": round(avg_t50_wr, 4),
        "avg_top50_return": round(avg_t50_ret, 4),
        "avg_top50_excess": round(avg_excess, 4),
        "top_global_combos": top_global[:10],
        "weights": all_summaries[0]["weights"] if all_summaries else {},
        "exported_at": datetime.now(BJS_TZ).isoformat(),
    }

    with open(latest_dir / "full_summary.json", "w", encoding="utf-8") as f:
        json.dump(full_summary, f, ensure_ascii=False, indent=2)

    print(f"\n  全局汇总: {total_stocks}只 Top50胜率{avg_t50_wr:.0%} 超额{avg_excess:+.2f}% → {latest_dir}")


if __name__ == "__main__":
    print("导出回测统计数据...\n")
    for d in ["20260617", "20260618", "20260622", "20260623"]:
        export_stats(d)
    export_latest_summary()
    print("\n完成")
