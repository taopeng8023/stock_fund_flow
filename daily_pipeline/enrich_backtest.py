"""
Enrich multi-snapshot backtest CSVs with signal columns from scores.csv.
Joins on (代码, pick_date), adds: 综合信号, 启动信号, 短线得分, 中线得分,
and all 23 factor sub-scores.

Output: research_data/backtest/multi_snapshot/<date>/snapshots/enriched_<time>.csv
Also writes: research_data/backtest/multi_snapshot/ALL_ENRICHED.csv (all dates merged)
"""
import csv
import os
import sys

RESEARCH_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data"
)
MULTI_ROOT = os.path.join(RESEARCH_ROOT, "backtest", "multi_snapshot")

SIGNAL_COLS = [
    "综合信号", "综合信号说明", "启动信号", "启动信号说明",
    "短线得分", "中线得分",
]
FACTOR_COLS = [
    "启动得分", "资金得分", "趋势得分", "启动因子", "板块得分",
    "位置得分", "分析师得分", "多日得分", "技术面得分", "行业内得分",
    "融资得分", "加速度得分", "占比趋势得分",
    "日内稳定", "日内加速", "排名轨迹", "VWAP位置",
    "板块轨迹", "价格动量", "涨停邻近", "行业分散", "板块价格",
    "尾盘收益", "尾盘量能", "拥挤度",
    "涨跌幅", "换手率", "量比", "总市值",
]
ALL_EXTRA_COLS = SIGNAL_COLS + FACTOR_COLS


def load_scores(date_str):
    path = os.path.join(RESEARCH_ROOT, date_str, "scores.csv")
    if not os.path.exists(path):
        return {}
    scores = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = r.get("代码", "").strip()
            if code:
                scores[code] = {c: r.get(c, "") for c in ALL_EXTRA_COLS}
    return scores


def enrich_date(pick_date):
    snap_dir = os.path.join(MULTI_ROOT, pick_date, "snapshots")
    if not os.path.isdir(snap_dir):
        print(f"  ✗ {pick_date}: no snapshots dir")
        return []

    scores = load_scores(pick_date)
    if not scores:
        print(f"  ✗ {pick_date}: no scores")
        return []

    files = sorted([f for f in os.listdir(snap_dir)
                    if f.startswith("snapshot_") and f.endswith(".csv")
                    and not f.startswith("enriched_")])

    all_rows = []
    for f in files:
        in_path = os.path.join(snap_dir, f)
        out_path = os.path.join(snap_dir, f"enriched_{f}")

        enriched = []
        with open(in_path, encoding="utf-8-sig") as inf:
            reader = csv.DictReader(inf)
            in_fields = reader.fieldnames
            out_fields = list(in_fields) + ALL_EXTRA_COLS
            for row in reader:
                code = row.get("代码", "").strip()
                s = scores.get(code, {})
                for c in ALL_EXTRA_COLS:
                    row[c] = s.get(c, "")
                enriched.append(row)

        with open(out_path, "w", encoding="utf-8-sig", newline="") as outf:
            w = csv.DictWriter(outf, fieldnames=out_fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(enriched)

        for r in enriched:
            r["_pick_date"] = pick_date
            r["_snapshot_time"] = f.replace("snapshot_", "").replace(".csv", "")
        all_rows.extend(enriched)
        print(f"  ✓ {pick_date}/{f} → {len(enriched)} rows enriched")

    return all_rows


if __name__ == "__main__":
    dates = [d for d in os.listdir(MULTI_ROOT)
             if os.path.isdir(os.path.join(MULTI_ROOT, d))
             and os.path.isdir(os.path.join(MULTI_ROOT, d, "snapshots"))]

    print(f"Enriching {len(dates)} dates...")
    all_data = []
    for d in sorted(dates):
        rows = enrich_date(d)
        if rows:
            all_data.extend(rows)

    # Write merged file
    if all_data:
        merged_path = os.path.join(MULTI_ROOT, "ALL_ENRICHED.csv")
        all_fields = ["_pick_date", "_snapshot_time"] + \
                     [f for f in all_data[0].keys() if not f.startswith("_")]
        with open(merged_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=all_fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(all_data)
        print(f"\n✓ Merged {len(all_data)} rows → {merged_path}")

        # Print summary stats
        dates_set = set(r["_pick_date"] for r in all_data)
        snapshots_set = set((r["_pick_date"], r["_snapshot_time"]) for r in all_data)
        print(f"  Dates: {len(dates_set)}, Snapshots: {len(snapshots_set)}, Rows: {len(all_data)}")
    else:
        print("\n✗ No data enriched")
        sys.exit(1)
