#!/usr/bin/env python3
"""Analyze ALL backtest CSV files — per-date + cross-date."""
import csv
import math
from pathlib import Path
from collections import defaultdict

BACKTEST_DIR = Path("/Users/taopeng/PycharmProjects/stock_fund_flow/research_data/backtest/daily")
FILES = sorted(BACKTEST_DIR.glob("backtest_*.csv"))

def load(filepath):
    rows = []
    with open(filepath, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            r["综合得分"] = float(r["综合得分"])
            r["选股日价格"] = float(r["选股日价格"])
            r["次日开盘价"] = float(r["次日开盘价"])
            r["次日收益%"] = float(r["次日收益%"])
            r["胜负"] = r["胜负"].strip()
            rows.append(r)
    return rows

def spearman_rank_ic(scores, returns):
    """Spearman rank correlation between score and next-day return."""
    n = len(scores)
    import json
    # rank
    def rankify(vals):
        sorted_vals = sorted((v, i) for i, v in enumerate(vals))
        ranks = [0] * n
        i = 0
        while i < n:
            j = i
            while j < n and sorted_vals[j][0] == sorted_vals[i][0]:
                j += 1
            avg_rank = (i + j - 1) / 2.0 + 1  # 1-based
            for k in range(i, j):
                ranks[sorted_vals[k][1]] = avg_rank
            i = j
        return ranks
    score_ranks = rankify(scores)
    ret_ranks = rankify(returns)
    mean_sr = sum(score_ranks) / n
    mean_rr = sum(ret_ranks) / n
    cov = sum((score_ranks[i] - mean_sr) * (ret_ranks[i] - mean_rr) for i in range(n))
    std_sr = math.sqrt(sum((s - mean_sr) ** 2 for s in score_ranks))
    std_rr = math.sqrt(sum((r - mean_rr) ** 2 for r in ret_ranks))
    if std_sr == 0 or std_rr == 0:
        return 0.0
    return cov / (std_sr * std_rr)

def analyze_date(filepath):
    rows = load(filepath)
    date = filepath.stem.replace("backtest_", "")
    n = len(rows)
    # Sort by score desc
    rows.sort(key=lambda r: r["综合得分"], reverse=True)
    wins = [r for r in rows if r["胜负"] == "胜"]
    wr_all = len(wins) / n * 100
    avg_all = sum(r["次日收益%"] for r in rows) / n

    # Top50
    top50 = rows[:50]
    wins_top50 = [r for r in top50 if r["胜负"] == "胜"]
    wr_top50 = len(wins_top50) / len(top50) * 100
    avg_top50 = sum(r["次日收益%"] for r in top50) / len(top50)

    # Bottom50
    bot50 = rows[-50:]
    wins_bot50 = [r for r in bot50 if r["胜负"] == "胜"]
    wr_bot50 = len(wins_bot50) / len(bot50) * 100
    avg_bot50 = sum(r["次日收益%"] for r in bot50) / len(bot50)

    # Decile analysis (D1 = top decile, D10 = bottom decile)
    decile_size = n // 10
    deciles = {}
    for d in range(10):
        start = d * decile_size
        end = start + decile_size if d < 9 else n
        chunk = rows[start:end]
        chunk_wins = [r for r in chunk if r["胜负"] == "胜"]
        wr = len(chunk_wins) / len(chunk) * 100
        avg_ret = sum(r["次日收益%"] for r in chunk) / len(chunk)
        deciles[f"D{d+1}"] = {"count": len(chunk), "win_rate": round(wr, 2), "avg_return": round(avg_ret, 4)}

    # IC
    scores = [r["综合得分"] for r in rows]
    returns = [r["次日收益%"] for r in rows]
    ic = spearman_rank_ic(scores, returns)

    # Find score threshold for >50% win rate
    # Cumulative from top
    wins_cum, total_cum = 0, 0
    threshold_50 = None
    for r in rows:
        total_cum += 1
        if r["胜负"] == "胜":
            wins_cum += 1
        wr = wins_cum / total_cum * 100
        if wr >= 50 and threshold_50 is None:
            threshold_50 = r["综合得分"]
            break

    return {
        "date": date,
        "n": n,
        "wr_all": round(wr_all, 2),
        "avg_all": round(avg_all, 4),
        "wr_top50": round(wr_top50, 2),
        "avg_top50": round(avg_top50, 4),
        "wr_bot50": round(wr_bot50, 2),
        "avg_bot50": round(avg_bot50, 4),
        "deciles": deciles,
        "ic": round(ic, 4),
        "threshold_50pct": threshold_50,
        "top50_codes": set(r["代码"] for r in rows[:50]),
        "bot50_codes": set(r["代码"] for r in rows[-50:]),
        "all_data": rows,
    }

print("=" * 90)
print("PER-DATE ANALYSIS")
print("=" * 90)

all_results = []
for f in FILES:
    r = analyze_date(f)
    all_results.append(r)

# --- Per-date summary table ---
for r in all_results:
    print(f"\n{'─' * 80}")
    print(f"Date: {r['date']}  |  N={r['n']}  |  IC(Spearman)={r['ic']}")
    print(f"  Overall:        WR={r['wr_all']:.1f}%  AvgRet={r['avg_all']:.2f}%")
    print(f"  Top50:          WR={r['wr_top50']:.1f}%  AvgRet={r['avg_top50']:.2f}%")
    print(f"  Bottom50:       WR={r['wr_bot50']:.1f}%  AvgRet={r['avg_bot50']:.2f}%")
    print(f"  Top50 - Bot50 spread:  {r['avg_top50'] - r['avg_bot50']:.2f}%")
    print(f"  Score threshold for >50% cumulative WR:  {r['threshold_50pct']:.4f}" if r['threshold_50pct'] else "  Score threshold: N/A (no region with >50% WR)")
    print(f"\n  Decile Win Rates:")
    header = "  " + " | ".join(f"D{i+1:>2}" for i in range(10))
    print(header)
    vals = "  " + " | ".join(f"{r['deciles'][f'D{i+1}']['win_rate']:>5.1f}" for i in range(10))
    print(vals)
    print(f"\n  Monotonicity check (D1 > D10): {'PASS' if r['deciles']['D1']['win_rate'] > r['deciles']['D10']['win_rate'] else 'FAIL'}  "
          f"(D1={r['deciles']['D1']['win_rate']:.1f}% vs D10={r['deciles']['D10']['win_rate']:.1f}%)")

# --- Cross-date: consistent winners (stocks in Top50 of multiple dates) ---
print("\n" + "=" * 90)
print("CROSS-DATE ANALYSIS")
print("=" * 90)

# Consistent winners
winner_counter = defaultdict(list)  # code -> [(date, score, return), ...]
for r in all_results:
    for code in r["top50_codes"]:
        for row in r["all_data"]:
            if row["代码"] == code:
                winner_counter[code].append((r["date"], row["综合得分"], row["次日收益%"]))
                break

consistent_winners = []
for code, appearances in winner_counter.items():
    if len(appearances) >= 2:
        name = None
        for r in all_results:
            for row in r["all_data"]:
                if row["代码"] == code:
                    name = row["名称"]
                    break
            if name:
                break
        avg_ret = sum(a[2] for a in appearances) / len(appearances)
        consistent_winners.append((code, name, len(appearances), avg_ret, appearances))

consistent_winners.sort(key=lambda x: -x[2])

print("\n--- Consistent Winners (Top50 in >=2 dates) ---")
if consistent_winners:
    print(f"{'Code':<10} {'Name':<12} {'Apps':>4} {'AvgRet%':>8}  Dates")
    print("-" * 70)
    for code, name, cnt, avg_ret, apps in consistent_winners:
        date_str = ", ".join(a[0] for a in apps)
        print(f"{code:<10} {name:<12} {cnt:>4} {avg_ret:>+7.2f}%  {date_str}")
    print(f"\nTotal consistent winners: {len(consistent_winners)}")
else:
    print("None found across multiple dates.")

# Consistent losers
loser_counter = defaultdict(list)
for r in all_results:
    for code in r["bot50_codes"]:
        for row in r["all_data"]:
            if row["代码"] == code:
                loser_counter[code].append((r["date"], row["综合得分"], row["次日收益%"]))
                break

consistent_losers = []
for code, appearances in loser_counter.items():
    if len(appearances) >= 2:
        name = None
        for r in all_results:
            for row in r["all_data"]:
                if row["代码"] == code:
                    name = row["名称"]
                    break
            if name:
                break
        avg_ret = sum(a[2] for a in appearances) / len(appearances)
        consistent_losers.append((code, name, len(appearances), avg_ret, appearances))

consistent_losers.sort(key=lambda x: -x[2])

print("\n--- Consistent Losers (Bottom50 in >=2 dates) ---")
if consistent_losers:
    print(f"{'Code':<10} {'Name':<12} {'Apps':>4} {'AvgRet%':>8}  Dates")
    print("-" * 70)
    for code, name, cnt, avg_ret, apps in consistent_losers:
        date_str = ", ".join(a[0] for a in apps)
        print(f"{code:<10} {name:<12} {cnt:>4} {avg_ret:>+7.2f}%  {date_str}")
    print(f"\nTotal consistent losers: {len(consistent_losers)}")
else:
    print("None found across multiple dates.")

# --- Cross-date: score monotonicity stability ---
print("\n--- Score Monotonicity Stability ---")
print(f"{'Date':<12} {'IC':>8} {'D1_WR':>7} {'D10_WR':>7} {'D1-D10':>8} {'Mono?':>6}")
print("-" * 55)
mono_pass = 0
for r in all_results:
    d1 = r['deciles']['D1']['win_rate']
    d10 = r['deciles']['D10']['win_rate']
    diff = d1 - d10
    mono = "PASS" if diff > 0 else "FAIL"
    if diff > 0:
        mono_pass += 1
    print(f"{r['date']:<12} {r['ic']:>8.4f} {d1:>6.1f}% {d10:>6.1f}% {diff:>+7.1f}% {mono:>6}")

print(f"\nMonotonicity stable: {mono_pass}/{len(all_results)} dates ({mono_pass/len(all_results)*100:.0f}%)")

# --- Cross-date: score threshold for >50% WR ---
print("\n--- Score Threshold Consistency ---")
thresholds = [(r["date"], r["threshold_50pct"]) for r in all_results if r["threshold_50pct"] is not None]
if thresholds:
    for date, th in thresholds:
        print(f"  {date}: score >= {th:.4f} yields >50% cumulative win rate")
    avg_th = sum(t[1] for t in thresholds) / len(thresholds)
    min_th = min(t[1] for t in thresholds)
    max_th = max(t[1] for t in thresholds)
    print(f"\n  Threshold range: {min_th:.4f} - {max_th:.4f}, mean={avg_th:.4f}")
    print(f"  Conservative threshold for >50% WR across all dates: score >= {max_th:.4f}")
else:
    print("  No date achieved >50% cumulative win rate at any threshold.")

# --- Key findings ---
print("\n" + "=" * 90)
print("KEY FINDINGS")
print("=" * 90)

ics = [r["ic"] for r in all_results]
avg_ic = sum(ics) / len(ics)
pos_ic = sum(1 for ic in ics if ic > 0)
top50_wrs = [r["wr_top50"] for r in all_results]
avg_top50_wr = sum(top50_wrs) / len(top50_wrs)

print(f"""
1. IC Summary:
   - Individual ICs: {', '.join(f'{ic:.4f}' for ic in ics)}
   - Average IC: {avg_ic:.4f}
   - Positive IC: {pos_ic}/{len(ics)} dates
   - Interpretation: {'Score has PREDICTIVE power (consistently positive IC)' if avg_ic > 0.01 else 'Score has WEAK predictive power' if avg_ic > 0 else 'Score has NEGATIVE predictive power — model may be broken'}

2. Top50 Win Rate:
   - Individual: {', '.join(f'{w:.1f}%' for w in top50_wrs)}
   - Average Top50 WR: {avg_top50_wr:.1f}%
   - The Top50 selection {'OUTPERFORMS' if avg_top50_wr > 50 else 'UNDERPERFORMS'} random (50% baseline)

3. Model Stability:
   - Decile monotonicity passed: {mono_pass}/{len(all_results)} dates
   - Model is {'STABLE — D1 consistently outperforms D10' if mono_pass >= 3 else 'UNSTABLE — decile ordering not reliable'}
   - Number of consistent winners (Top50 in >=2 dates): {len(consistent_winners)}
   - Number of consistent losers (Bottom50 in >=2 dates): {len(consistent_losers)}

4. Score Decay Signal:
   - IC trend: {ics}
   - {'No clear decay — IC is stable or improving' if len(ics) >= 2 and ics[-1] >= ics[0] else 'Possible decay — IC is declining across dates'}
""")

# Print all raw data for JSON export
print("\n--- RAW DATA (for structured output) ---")
print("Deciles per date:")
for r in all_results:
    print(f"  {r['date']}: {r['deciles']}")
