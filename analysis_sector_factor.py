#!/usr/bin/env python3
"""
Sector & Factor Analysis across 3 dates: 20260622, 20260623, 20260624.
Cross-references backtest win/loss for predictive power.

Uses ordinal ranking to select top-decile (handles low-cardinality factors).
"""
import pandas as pd
import numpy as np
from pathlib import Path
from collections import defaultdict
import json

DATES = ["20260622", "20260623", "20260624"]
SCORE_DIR = Path("/Users/taopeng/PycharmProjects/stock_fund_flow/research_data")
BT_DIR = SCORE_DIR / "backtest" / "daily"

FACTOR_COLS = [
    "资金得分", "趋势得分", "启动因子", "板块得分", "位置得分",
    "分析师得分", "多日得分", "技术面得分", "行业内得分", "融资得分",
    "加速度得分", "占比趋势得分", "日内稳定", "日内加速", "排名轨迹",
    "VWAP位置", "板块轨迹", "价格动量", "涨停邻近", "行业分散",
    "板块价格", "尾盘收益", "尾盘量能", "拥挤度",
    "综合得分", "启动得分", "短线得分", "中线得分",
]

# ── Load ────────────────────────────────────────────────────────────────
def load_scores(date_str):
    df = pd.read_csv(SCORE_DIR / date_str / "scores.csv", dtype={"代码": str})
    for c in FACTOR_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "涨跌幅" in df.columns:
        df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
    return df

def load_backtest(date_str):
    path = BT_DIR / f"backtest_{date_str}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, dtype={"代码": str})
    df["次日收益%"] = pd.to_numeric(df["次日收益%"], errors="coerce")
    return df

print("Loading data...")
scores_dfs = {d: load_scores(d) for d in DATES}
bt_dfs = {d: load_backtest(d) for d in DATES}

# ── Merge ────────────────────────────────────────────────────────────────
def merge_with_bt(date_str):
    sdf = scores_dfs[date_str].copy()
    bdf = bt_dfs[date_str]
    if bdf is None:
        return sdf
    return sdf.merge(bdf[["代码", "次日收益%", "胜负"]], on="代码", how="left")

merged_dfs = {d: merge_with_bt(d) for d in DATES}

# ── Cardinality check ───────────────────────────────────────────────────
print("\nFactor cardinality (unique values per date):")
for f in FACTOR_COLS:
    uniq = [merged_dfs[d][f].nunique() for d in DATES]
    print(f"  {f:<20s}: {uniq}")

# ── Top-decile win rate using ORDINAL ranking ────────────────────────────
def top_decile_ordinal(df, factor_col, decile_n=10):
    """Select top decile by ordinal rank (not percentile), break ties arbitrarily."""
    valid = df[df[factor_col].notna() & df["胜负"].notna()].copy()
    if len(valid) < 50:
        return None
    # Ordinal rank: 1 = highest value, ties broken by first occurrence
    valid["_rank"] = valid[factor_col].rank(ascending=False, method="first")
    n_top = max(1, int(len(valid) / decile_n))
    top = valid[valid["_rank"] <= n_top]
    if len(top) < 5:
        return None
    wins = (top["胜负"] == "胜").sum()
    wr = wins / len(top)
    avg_ret = top["次日收益%"].mean()
    # Also compute bottom decile for contrast
    bottom = valid[valid["_rank"] > len(valid) - n_top]
    bottom_wr = (bottom["胜负"] == "胜").sum() / len(bottom) if len(bottom) > 0 else None
    bottom_ret = bottom["次日收益%"].mean() if len(bottom) > 0 else None
    spread = wr - bottom_wr if bottom_wr is not None else None
    return {
        "win_rate": wr, "n": len(top), "avg_return": avg_ret,
        "bottom_wr": bottom_wr, "bottom_ret": bottom_ret, "spread": spread,
    }

# Calculate for all factors across all dates
factor_results = {}
for f in FACTOR_COLS:
    results = {}
    for d in DATES:
        mdf = merged_dfs[d]
        r = top_decile_ordinal(mdf, f)
        if r:
            results[d] = r
    if results:
        wrs = [r["win_rate"] for r in results.values()]
        rets = [r["avg_return"] for r in results.values()]
        spreads = [r["spread"] for r in results.values() if r["spread"] is not None]
        factor_results[f] = {
            "by_date": results,
            "avg_wr": np.mean(wrs),
            "std_wr": np.std(wrs),
            "avg_ret": np.mean(rets),
            "std_ret": np.std(rets),
            "avg_spread": np.mean(spreads) if spreads else 0,
            "n_dates": len(results),
            "unique_vals": {d: merged_dfs[d][f].nunique() for d in DATES},
        }

# ══════════════════════════════════════════════════════════════════════════
# 1. SECTOR ANALYSIS
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("1. SECTOR ANALYSIS: Win Rate by Industry (pooled across 3 dates)")
print("=" * 80)

sector_stats = defaultdict(lambda: {"wins": 0, "total": 0, "dates_present": set(), "returns": []})

for d in DATES:
    mdf = merged_dfs[d]
    valid = mdf[mdf["胜负"].notna() & mdf["行业"].notna()]
    for _, row in valid.iterrows():
        sector = row["行业"]
        sector_stats[sector]["dates_present"].add(d)
        sector_stats[sector]["total"] += 1
        sector_stats[sector]["returns"].append(row["次日收益%"])
        if row["胜负"] == "胜":
            sector_stats[sector]["wins"] += 1

sector_summary = []
for sec, stats in sector_stats.items():
    if stats["total"] >= 5:
        wr = stats["wins"] / stats["total"]
        avg_ret = np.mean(stats["returns"])
        sector_summary.append({
            "行业": sec,
            "样本数": stats["total"],
            "胜率": round(wr, 4),
            "平均收益%": round(avg_ret, 4),
            "出现日期": len(stats["dates_present"]),
        })

sector_df = pd.DataFrame(sector_summary).sort_values("胜率", ascending=False)
consistent = sector_df[(sector_df["胜率"] > 0.45) & (sector_df["出现日期"] == 3)]

print(f"\nConsistently strong sectors (>45% WR, all 3 dates): {len(consistent)}")
print(consistent.to_string(index=False))

# All sectors for reference
print(f"\nAll sectors ranked by win rate (top 30):")
print(sector_df.head(30).to_string(index=False))

# ══════════════════════════════════════════════════════════════════════════
# 2. FACTOR PREDICTIVE POWER
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("2. FACTOR PREDICTIVE POWER (Top-Decile Standalone Win Rate)")
print("=" * 80)

# Sort by avg win rate
sorted_factors = sorted(factor_results.items(), key=lambda x: x[1]["avg_wr"], reverse=True)

print(f"\n{'Factor':<18s} {'Avg WR':>7s} {'Std WR':>7s} {'Spread':>7s} {'Avg Ret%':>8s} {'#Dates':>7s} {'Cardinality':>20s}")
print("-" * 85)
for name, stats in sorted_factors:
    card = ", ".join(f"{d[-4:]}:{v}" for d, v in stats["unique_vals"].items())
    print(f"{name:<18s} {stats['avg_wr']:>6.3f} {stats['std_wr']:>6.3f} {stats['avg_spread']:>6.3f} {stats['avg_ret']:>7.3f}% {stats['n_dates']:>6d}  {card}")

# ══════════════════════════════════════════════════════════════════════════
# 3. MOST STABLE FACTORS
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("3. TOP 5 MOST STABLE FACTORS (high avg WR + low std + present all 3 dates)")
print("=" * 80)

# Stability = avg_wr - 2*std_wr, only for factors present in all 3 dates
stable_candidates = []
for name, stats in sorted_factors:
    if stats["n_dates"] >= 2:
        stats["stability"] = stats["avg_wr"] - 2 * stats["std_wr"]
        stable_candidates.append((name, stats))

stable_sort = sorted(stable_candidates, key=lambda x: x[1]["stability"], reverse=True)

print(f"\n{'Rank':<5s} {'Factor':<18s} {'Avg WR':>7s} {'Std WR':>7s} {'Stability':>10s} {'Spread':>7s} {'Avg Ret%':>8s}")
print("-" * 70)
for i, (name, stats) in enumerate(stable_sort[:15], 1):
    print(f"{i:<5d} {name:<18s} {stats['avg_wr']:>6.3f} {stats['std_wr']:>6.3f} {stats['stability']:>9.3f} {stats['avg_spread']:>6.3f} {stats['avg_ret']:>7.3f}%")

# ══════════════════════════════════════════════════════════════════════════
# 4. FACTOR CORRELATION (redundancy)
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("4. FACTOR CORRELATION & REDUNDANCY")
print("=" * 80)

# Pool all 3 dates
available = [f for f in FACTOR_COLS if all(f in scores_dfs[d].columns for d in DATES)]
all_scores = pd.concat([scores_dfs[d][available] for d in DATES], ignore_index=True)
corr_matrix = all_scores.corr()

high_corr_pairs = []
for i in range(len(available)):
    for j in range(i + 1, len(available)):
        r = corr_matrix.iloc[i, j]
        if abs(r) > 0.6:
            high_corr_pairs.append((available[i], available[j], round(r, 3)))

high_corr_pairs.sort(key=lambda x: abs(x[2]), reverse=True)

print(f"\nFactor pairs with |r| > 0.6:")
print(f"{'Factor 1':<18s} {'Factor 2':<18s} {'r':>8s}")
print("-" * 48)
for f1, f2, r in high_corr_pairs[:25]:
    print(f"{f1:<18s} {f2:<18s} {r:>8.3f}")

# Find redundant clusters via graph connectivity
from collections import defaultdict as dd
graph = dd(set)
for f1, f2, r in high_corr_pairs:
    if abs(r) > 0.7:
        graph[f1].add(f2)
        graph[f2].add(f1)

visited = set()
clusters = []
for node in graph:
    if node not in visited:
        stack = [node]
        cluster = set()
        while stack:
            n = stack.pop()
            if n not in visited:
                visited.add(n)
                cluster.add(n)
                stack.extend(graph[n] - visited)
        if len(cluster) > 1:
            clusters.append(cluster)

# Also identify singleton useless factors (cardinality = 1 for all dates)
useless = [f for f, stats in factor_results.items()
           if all(v <= 1 for v in stats["unique_vals"].values())]

print(f"\nRedundant factor clusters (|r| > 0.7, {len(clusters)} groups):")
for i, cl in enumerate(clusters):
    members = sorted(cl)
    # Best performer in cluster
    best = max(members, key=lambda m: factor_results.get(m, {}).get("avg_wr", 0))
    wrs = {m: f"{factor_results.get(m, {}).get('avg_wr', 0):.3f}" for m in members}
    print(f"  Cluster {i+1}: {', '.join(f'{m}(WR={wrs[m]})' for m in members)}")
    print(f"     -> Keep: {best}, drop the rest as redundant")

if useless:
    print(f"\nZero-discrimination factors (constant across all dates): {useless}")

# ══════════════════════════════════════════════════════════════════════════
# 5. SECTOR ROTATION
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("5. SECTOR ROTATION: Win Rate Change Between Dates")
print("=" * 80)

# Per-date sector win rates
date_sector_wr = {}
for d in DATES:
    mdf = merged_dfs[d]
    valid = mdf[mdf["胜负"].notna() & mdf["行业"].notna()]
    wr_map = {}
    for sec in valid["行业"].unique():
        sec_data = valid[valid["行业"] == sec]
        if len(sec_data) >= 5:
            wr_map[sec] = {
                "wr": (sec_data["胜负"] == "胜").sum() / len(sec_data),
                "n": len(sec_data),
                "avg_ret": sec_data["次日收益%"].mean(),
            }
    date_sector_wr[d] = wr_map

# Rotation between consecutive dates
for d1, d2 in [("20260622", "20260623"), ("20260623", "20260624")]:
    label = f"{d1[-4:]} -> {d2[-4:]}"
    print(f"\n--- {label} ---")
    common = set(date_sector_wr[d1].keys()) & set(date_sector_wr[d2].keys())
    rotation = []
    for sec in common:
        wr1 = date_sector_wr[d1][sec]["wr"]
        wr2 = date_sector_wr[d2][sec]["wr"]
        ret1 = date_sector_wr[d1][sec]["avg_ret"]
        ret2 = date_sector_wr[d2][sec]["avg_ret"]
        rotation.append({
            "sector": sec,
            "wr_before": wr1, "wr_after": wr2, "wr_change": wr2 - wr1,
            "ret_before": ret1, "ret_after": ret2,
        })
    rot_df = pd.DataFrame(rotation)

    # Top improvers
    improvers = rot_df.nlargest(10, "wr_change")
    print(f"\n  Top 10 Improving Sectors:")
    print(f"  {'Sector':<18s} {'WR Before':>9s} {'WR After':>9s} {'Change':>8s} {'AvgRet Before':>13s} {'AvgRet After':>13s}")
    print("  " + "-" * 75)
    for _, r in improvers.iterrows():
        print(f"  {r['sector']:<18s} {r['wr_before']:>8.3f} {r['wr_after']:>8.3f} {r['wr_change']:>+7.3f} {r['ret_before']:>12.3f}% {r['ret_after']:>12.3f}%")

    # Top decliners
    decliners = rot_df.nsmallest(10, "wr_change")
    print(f"\n  Top 10 Declining Sectors:")
    print(f"  {'Sector':<18s} {'WR Before':>9s} {'WR After':>9s} {'Change':>8s} {'AvgRet Before':>13s} {'AvgRet After':>13s}")
    print("  " + "-" * 75)
    for _, r in decliners.iterrows():
        print(f"  {r['sector']:<18s} {r['wr_before']:>8.3f} {r['wr_after']:>8.3f} {r['wr_change']:>+7.3f} {r['ret_before']:>12.3f}% {r['ret_after']:>12.3f}%")

# Net momentum: sum of changes across both transitions
all_rotations = []
for d1, d2 in [("20260622", "20260623"), ("20260623", "20260624")]:
    common = set(date_sector_wr[d1].keys()) & set(date_sector_wr[d2].keys())
    for sec in common:
        wr1 = date_sector_wr[d1][sec]["wr"]
        wr2 = date_sector_wr[d2][sec]["wr"]
        all_rotations.append({"sector": sec, "transition": f"{d1[-4:]}->{d2[-4:]}", "change": wr2 - wr1})

all_rot_df = pd.DataFrame(all_rotations)
net = all_rot_df.groupby("sector")["change"].agg(["sum", "mean", "count"]).reset_index()
net = net[net["count"] == 2].sort_values("sum", ascending=False)

print(f"\n\nNet Rotation Momentum (sum of WR changes across both transitions):")
print(f"  {'Sector':<18s} {'Net Change':>10s} {'Avg Change':>10s}")
print("  " + "-" * 42)
for _, r in net.head(15).iterrows():
    print(f"  {r['sector']:<18s} {r['sum']:>+9.3f} {r['mean']:>+9.3f}")
print("  ...")
for _, r in net.tail(10).iterrows():
    print(f"  {r['sector']:<18s} {r['sum']:>+9.3f} {r['mean']:>+9.3f}")

# ══════════════════════════════════════════════════════════════════════════
# BONUS: Per-date win rate table for top stable factors
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("BONUS: Per-Date Win Rate Detail for Top Stable Factors")
print("=" * 80)

print(f"\n{'Factor':<18s}", end="")
for d in DATES:
    print(f"  {d[-4:]:>12s}", end="")
print(f"  {'Avg':>8s}  {'Std':>8s}")
print("-" * 70)

for name, stats in stable_sort[:15]:
    print(f"{name:<18s}", end="")
    wrs = []
    for d in DATES:
        rd = stats["by_date"].get(d)
        if rd:
            print(f"  {rd['win_rate']:>8.3f}(s={rd['spread']:+.3f})", end="")
            wrs.append(rd["win_rate"])
        else:
            print(f"  {'N/A':>12s}", end="")
    if wrs:
        print(f"  {np.mean(wrs):>7.3f}  {np.std(wrs):>7.3f}")
    else:
        print()

print("\nDone.")
