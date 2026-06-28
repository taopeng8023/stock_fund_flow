#!/usr/bin/env python3
"""富集回测快照 — 将 scores.csv 信号列联入 snapshot CSV，输出带信号的 enriched CSV。

用法: python scripts/enrich_snapshots.py
输出: research_data/backtest/1430_entry/<date>/enriched/snapshot_HHMMSS_enriched.csv
"""

import csv
import os
import sys

RESEARCH_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data"
)
BACKTEST_ROOT = os.path.join(RESEARCH_ROOT, "backtest", "1430_entry")


def load_score_map(date_str):
    """从 scores.csv 加载 {code: {综合信号, 启动信号, 综合得分, ...}}"""
    path = os.path.join(RESEARCH_ROOT, date_str, "scores.csv")
    if not os.path.exists(path):
        return {}
    score_map = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = r.get("代码", "")
            if not code:
                continue
            score_map[code] = {
                "综合信号": r.get("综合信号", ""),
                "启动信号": r.get("启动信号", ""),
                "综合得分": r.get("综合得分", ""),
                "资金得分": r.get("资金得分", ""),
                "板块得分": r.get("板块得分", ""),
                "行业": r.get("行业", ""),
                "涨跌幅": r.get("涨跌幅", ""),
                "换手率": r.get("换手率", ""),
                "总市值": r.get("总市值", ""),
                "市场体制": r.get("市场体制", ""),
                "中线得分": r.get("中线得分", ""),
                "启动得分": r.get("启动得分", ""),
            }
    return score_map


def enrich_snapshots(pick_date):
    """富集指定日期的所有快照 CSV"""
    snap_dir = os.path.join(BACKTEST_ROOT, pick_date, "snapshots")
    if not os.path.exists(snap_dir):
        print(f"  X 快照目录不存在: {snap_dir}")
        return

    score_map = load_score_map(pick_date)
    if not score_map:
        print(f"  X scores.csv 不存在: {pick_date}")
        return

    out_dir = os.path.join(BACKTEST_ROOT, pick_date, "enriched")
    os.makedirs(out_dir, exist_ok=True)

    snap_files = sorted(f for f in os.listdir(snap_dir) if f.endswith(".csv"))
    for sf in snap_files:
        snap_path = os.path.join(snap_dir, sf)
        out_path = os.path.join(out_dir, sf.replace(".csv", "_enriched.csv"))

        with open(snap_path, encoding="utf-8-sig") as fin:
            rows = list(csv.DictReader(fin))

        # 信号汇总
        signal_counts = {}

        with open(out_path, "w", encoding="utf-8-sig", newline="") as fout:
            fields = list(rows[0].keys()) + ["综合信号", "启动信号", "资金得分", "板块得分",
                                               "市场体制", "中线得分", "启动得分"]
            writer = csv.DictWriter(fout, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()

            for row in rows:
                code = row.get("代码", "")
                sm = score_map.get(code, {})
                row["综合信号"] = sm.get("综合信号", "")
                row["启动信号"] = sm.get("启动信号", "")
                row["资金得分"] = sm.get("资金得分", "")
                row["板块得分"] = sm.get("板块得分", "")
                row["市场体制"] = sm.get("市场体制", "")
                row["中线得分"] = sm.get("中线得分", "")
                row["启动得分"] = sm.get("启动得分", "")
                writer.writerow(row)

                # 统计信号出现次数
                for sig in sm.get("综合信号", "").split(","):
                    sig = sig.strip()
                    if sig:
                        signal_counts[sig] = signal_counts.get(sig, 0) + 1
                for sig in sm.get("启动信号", "").split(","):
                    sig = sig.strip()
                    if sig:
                        signal_counts[sig] = signal_counts.get(sig, 0) + 1

        print(f"  OK: {sf} → {len(rows)} stocks, {len(signal_counts)} unique signals")
    return True


def main():
    print("=== 快照信号富集 ===\n")
    dates = sorted(
        d for d in os.listdir(BACKTEST_ROOT)
        if d.isdigit() and os.path.isdir(os.path.join(BACKTEST_ROOT, d, "snapshots"))
    )
    print(f"  候选日期: {dates}\n")

    for d in dates:
        print(f"  [{d}]")
        enrich_snapshots(d)

    print("\n  富集完成")


if __name__ == "__main__":
    main()
