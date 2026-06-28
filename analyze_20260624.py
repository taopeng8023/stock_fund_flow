"""
20260624 回测富集数据分析 — 最优信号组合搜寻
独立单日分析任务
"""
import csv
import os
import re
from collections import defaultdict
from pathlib import Path

ENRICHED_DIR = Path("/Users/taopeng/PycharmProjects/stock_fund_flow/research_data/backtest/1430_entry/20260624/enriched")

# ── 加载所有 49 个 CSV ──────────────────────────────────────────
rows = []
for fpath in sorted(ENRICHED_DIR.glob("snapshot_*_enriched.csv")):
    m = re.match(r"snapshot_(\d{6})_enriched\.csv", fpath.name)
    if not m:
        continue
    hhmmss = m.group(1)
    hour = int(hhmmss[:2])
    minute = int(hhmmss[2:4])
    period = "morning" if (hour < 10 or (hour == 10 and minute <= 30)) else "afternoon"

    with open(fpath, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row.get("代码", "").strip()
            name = row.get("名称", "").strip()
            industry = row.get("行业", "").strip()
            score = float(row.get("综合得分", "0") or "0")
            entry_price = float(row.get("入场价", "0") or "0")
            snapshot_price = float(row.get("快照价", "0") or "0")
            pnl_str = row.get("收益%", "0").strip()
            pnl = float(pnl_str) if pnl_str else 0.0
            result = row.get("胜负", "").strip()
            signal_raw = row.get("综合信号", "").strip()
            start_raw = row.get("启动信号", "").strip()

            signals = [s.strip() for s in signal_raw.split(",") if s.strip()]
            start_signals = [s.strip() for s in start_raw.split(",") if s.strip()]

            rows.append({
                "code": code, "name": name, "industry": industry,
                "score": score, "pnl": pnl, "result": result,
                "signals": signals, "start_signals": start_signals,
                "period": period, "hhmmss": hhmmss,
                "signal_set": set(signals), "start_set": set(start_signals),
            })

print(f"Loaded {len(rows)} rows from 49 snapshots")
total = len(rows)
wins = sum(1 for r in rows if r["result"] == "胜")
losses = sum(1 for r in rows if r["result"] == "负")
draws = sum(1 for r in rows if r["result"] == "平")
avg_pnl = sum(r["pnl"] for r in rows) / total
market_winrate = wins / total * 100
print(f"Total={total}  Win={wins}  Loss={losses}  Draw={draws}  Market Avg PnL={avg_pnl:.2f}%  Market WinRate={market_winrate:.2f}%")

# ── 1. 单信号表现 ──────────────────────────────────────────────
print("\n" + "=" * 80)
print("### 1. 单信号排名（N>=50）")
print("=" * 80)

signal_stats = defaultdict(lambda: {"n": 0, "pnl_sum": 0.0, "wins": 0})
for r in rows:
    for sig in r["signals"]:
        st = signal_stats[sig]
        st["n"] += 1
        st["pnl_sum"] += r["pnl"]
        if r["result"] == "胜":
            st["wins"] += 1

single_ranked = []
for sig, st in signal_stats.items():
    if st["n"] >= 50:
        single_ranked.append((sig, st["n"], st["pnl_sum"] / st["n"], st["wins"] / st["n"] * 100))

single_ranked.sort(key=lambda x: -x[3])  # by winrate desc

print(f"| 信号 | N | 均收益% | 胜率 |")
print(f"|------|---|--------|------|")
for sig, n, avg, wr in single_ranked:
    print(f"| {sig} | {n} | {avg:+.2f} | {wr:.1f} |")

# ── 2. P0-P3 组合验证 ──────────────────────────────────────────
print("\n" + "=" * 80)
print("### 2. P0-P3 组合验证")
print("=" * 80)

def has_signals(row, *needles):
    return all(n in row["signal_set"] for n in needles)

def has_start(row, *needles):
    return all(n in row["start_set"] for n in needles)

combo_stats = {}
# P0 静默突破: P34_gap_strong AND P32_pump_risk
mask_p0 = [r for r in rows if has_signals(r, "P34_gap_strong", "P32_pump_risk")]
# P1 E3+P34: E3_strong_start(in 启动信号) AND P34_gap_strong(in 综合信号)
mask_p1 = [r for r in rows if has_start(r, "E3_strong_start") and has_signals(r, "P34_gap_strong")]
# P2 高价+空头+Gap反: P_high_price AND P35_short_pressure AND P34_gap_reverse
mask_p2 = [r for r in rows if has_signals(r, "P_high_price", "P35_short_pressure", "P34_gap_reverse")]
# P3 半导体+空头回补: 行业=半导体 AND 综合信号含 P35_short_cover
mask_p3 = [r for r in rows if r["industry"] == "半导体" and has_signals(r, "P35_short_cover")]

combos = [
    ("P0 静默突破", mask_p0),
    ("P1 E3+P34", mask_p1),
    ("P2 高价空头Gap反", mask_p2),
    ("P3 半导体空头回补", mask_p3),
]

print(f"| 组合 | N | 均收益% | 胜率 |")
print(f"|------|---|--------|------|")
for label, subset in combos:
    n = len(subset)
    if n == 0:
        print(f"| {label} | 0 | - | - |")
        continue
    avg = sum(r["pnl"] for r in subset) / n
    wr = sum(1 for r in subset if r["result"] == "胜") / n * 100
    print(f"| {label} | {n} | {avg:+.2f} | {wr:.1f} |")
    combo_stats[label] = {"n": n, "avg": avg, "wr": wr, "rows": subset}

# ── 3. Top 信号组合 (N>=30) ─────────────────────────────────────
print("\n" + "=" * 80)
print("### 3. Top 10 信号组合（N>=30）")
print("=" * 80)

combo_counter = defaultdict(lambda: {"n": 0, "pnl_sum": 0.0, "wins": 0})
for r in rows:
    sigs = tuple(sorted(r["signals"]))
    if len(sigs) == 0:
        continue
    c = combo_counter[sigs]
    c["n"] += 1
    c["pnl_sum"] += r["pnl"]
    if r["result"] == "胜":
        c["wins"] += 1

combo_ranked = []
for sigs, c in combo_counter.items():
    if c["n"] >= 30:
        combo_ranked.append((",".join(sigs), c["n"], c["pnl_sum"] / c["n"], c["wins"] / c["n"] * 100))

combo_ranked.sort(key=lambda x: -x[3])
print(f"| 组合 | N | 均收益% | 胜率 |")
print(f"|------|---|--------|------|")
for i, (combo_str, n, avg, wr) in enumerate(combo_ranked[:10]):
    print(f"| {combo_str} | {n} | {avg:+.2f} | {wr:.1f} |")

# ── 4. 反向信号 ────────────────────────────────────────────────
print("\n" + "=" * 80)
print("### 4. 避雷信号")
print("=" * 80)

threshold = market_winrate
reverse_signals = []
for sig, st in signal_stats.items():
    if st["n"] >= 50:
        wr = st["wins"] / st["n"] * 100
        if wr < threshold:
            reverse_signals.append((sig, st["n"], st["pnl_sum"] / st["n"], wr))

reverse_signals.sort(key=lambda x: x[3])  # worst first
print(f"| 信号 | N | 均收益% | 胜率 |")
print(f"|------|---|--------|------|")
for sig, n, avg, wr in reverse_signals:
    print(f"| {sig} | {n} | {avg:+.2f} | {wr:.1f} |")

# ── 5. 快照时间衰减分析 ────────────────────────────────────────
print("\n" + "=" * 80)
print("### 5. 时段衰减")
print("=" * 80)

morning_total = sum(1 for r in rows if r["period"] == "morning")
afternoon_total = sum(1 for r in rows if r["period"] == "afternoon")
morning_wr = sum(1 for r in rows if r["period"] == "morning" and r["result"] == "胜") / morning_total * 100
afternoon_wr = sum(1 for r in rows if r["period"] == "afternoon" and r["result"] == "胜") / afternoon_total * 100
print(f"\n全市场: 早盘胜率={morning_wr:.1f}% (N={morning_total}) | 午盘胜率={afternoon_wr:.1f}% (N={afternoon_total}) | 衰减={morning_wr - afternoon_wr:+.1f}%")

print(f"\n| 组合 | 早盘胜率 | 午盘胜率 | 衰减 |")
print(f"|------|---------|---------|------|")

def period_winrate(subset, period):
    sub = [r for r in subset if r["period"] == period]
    if len(sub) == 0:
        return 0.0
    return sum(1 for r in sub if r["result"] == "胜") / len(sub) * 100

for label, subset in combos:
    if len(subset) == 0:
        continue
    m_wr = period_winrate(subset, "morning")
    a_wr = period_winrate(subset, "afternoon")
    decay = a_wr - m_wr
    print(f"| {label} | {m_wr:.1f} | {a_wr:.1f} | {decay:+.1f} |")

# ── 6. 关键发现 ────────────────────────────────────────────────
print("\n" + "=" * 80)
print("### 6. 关键发现")
print("=" * 80)

findings = []

# Best single signal
if single_ranked:
    best = single_ranked[0]
    findings.append(f"最强单信号: {best[0]} (N={best[1]}, 胜率={best[3]:.1f}%, 均收益={best[2]:+.2f}%)")

# Best combo
if combo_ranked:
    best_c = combo_ranked[0]
    findings.append(f"最强信号组合: {best_c[0]} (N={best_c[1]}, 胜率={best_c[3]:.1f}%, 均收益={best_c[2]:+.2f}%)")

# P0-P3 findings
for label, subset in combos:
    n = len(subset)
    if n == 0:
        findings.append(f"{label}: 无样本")
        continue
    avg = sum(r["pnl"] for r in subset) / n
    wr = sum(1 for r in subset if r["result"] == "胜") / n * 100
    vs_market = wr - market_winrate
    findings.append(f"{label}: N={n} 胜率={wr:.1f}%(vs市场{vs_market:+.1f}%) 均收益={avg:+.2f}%")

# Time decay
findings.append(f"时段衰减: 全市场 早盘{morning_wr:.1f}% -> 午盘{afternoon_wr:.1f}% ({morning_wr - afternoon_wr:+.1f}%)")

# Most dangerous signals
if reverse_signals:
    worst = reverse_signals[0]
    findings.append(f"最须规避信号: {worst[0]} 胜率仅{worst[3]:.1f}% (低于市场{market_winrate:.1f}%)")

# P34 standalone analysis
p34_stats = {}
for sig in ["P34_gap_strong", "P34_gap_reverse", "P34_gap_standalone"]:
    if sig in signal_stats:
        st = signal_stats[sig]
        wr = st["wins"] / st["n"] * 100
        p34_stats[sig] = wr

for sig, wr in sorted(p34_stats.items(), key=lambda x: -x[1]):
    findings.append(f"P34子类型: {sig} 胜率={wr:.1f}% (N={signal_stats[sig]['n']})")

# P35 analysis
for sig in ["P35_short_cover", "P35_short_pressure"]:
    if sig in signal_stats:
        st = signal_stats[sig]
        wr = st["wins"] / st["n"] * 100
        findings.append(f"P35子类型: {sig} 胜率={wr:.1f}% (N={signal_stats[sig]['n']})")

for i, f in enumerate(findings, 1):
    print(f"{i}. {f}")

print("\nDone.")
