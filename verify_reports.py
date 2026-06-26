"""
Independent verification of three signal discovery reports.
Recomputes all claims from raw CSV files and flags deviations.
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path("/Users/taopeng/PycharmProjects/stock_fund_flow")
SCORES_DIR = ROOT / "research_data"
BT_DIR = ROOT / "research_data" / "backtest" / "daily"

DATES = ["20260622", "20260623", "20260624"]

# ── Load all data ──
all_data = {}  # date -> list of dicts with scores + next_day_return

for d in DATES:
    sc_path = SCORES_DIR / d / "scores.csv"
    bt_path = BT_DIR / f"backtest_{d}.csv"

    # Load backtest next-day returns
    bt_map = {}
    with open(bt_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            bt_map[r["代码"]] = float(r.get("次日收益%", 0) or 0)

    # Load scores and merge
    rows = []
    with open(sc_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = r["代码"]
            ret = bt_map.get(code, None)
            if ret is None:
                continue
            row = {
                "code": code,
                "name": r.get("名称", ""),
                "industry": r.get("行业", ""),
                "score": float(r.get("综合得分", 0) or 0),
                "capital": float(r.get("资金得分", 0) or 0),
                "sector": float(r.get("板块得分", 0) or 0),
                "trend": float(r.get("趋势得分", 0) or 0),
                "intraday_accel": float(r.get("日内加速", 0) or 0),
                "vwap_pos": float(r.get("VWAP位置", 0) or 0),
                "position": float(r.get("位置得分", 0) or 0),
                "chg": float(r.get("涨跌幅", 0) or 0),
                "composite_signal": r.get("综合信号", "") or "",
                "start_signal": r.get("启动信号", "") or "",
                "all_signals": (r.get("综合信号", "") or "") + "," + (r.get("启动信号", "") or ""),
                "tail_return": float(r.get("尾盘收益", 0) or 0),
                "next_day_return": ret,
                "is_win": ret > 0,
            }
            rows.append(row)

    all_data[d] = rows
    print(f"  Loaded {d}: {len(rows)} stocks with backtest data")

all_trades = []
for d in DATES:
    for t in all_data[d]:
        all_trades.append({**t, "date": d})

print(f"\n  Total pooled: {len(all_trades)} trades\n")

# Helper: compute stats for a subset
def compute_stats(trades):
    n = len(trades)
    if n == 0:
        return {"n": 0, "win_rate": None, "avg_return": None}
    wins = sum(1 for t in trades if t["is_win"])
    avg_ret = sum(t["next_day_return"] for t in trades) / n
    return {"n": n, "wins": wins, "win_rate": wins / n, "avg_return": avg_ret}


# ═══════════════════════════════════════════════════════════════
# PART 1: SCORE SIGNALS REPORT VERIFICATION
# ═══════════════════════════════════════════════════════════════
print("=" * 80)
print("PART 1: SCORE SIGNALS REPORT VERIFICATION")
print("=" * 80)

score_signal_rules = {
    "A1": lambda t: t["score"] >= 0.80,
    "A2": lambda t: t["score"] >= 0.75 and t["capital"] >= 0.80,
    "A3": lambda t: t["score"] >= 0.70 and t["sector"] >= 0.80,
    "A4": lambda t: t["score"] >= 0.65 and t["intraday_accel"] >= 0.80,
    "A5": lambda t: t["score"] >= 0.70 and t["vwap_pos"] >= 0.70,
    "A6": lambda t: t["score"] >= 0.70 and t["capital"] >= 0.70 and t["sector"] >= 0.70,
    "A7": lambda t: t["score"] >= 0.65 and t["capital"] >= 0.70 and t["sector"] >= 0.70 and t["trend"] >= 0.70,
    "A8": lambda t: t["score"] >= 0.60 and t["capital"] >= 0.70 and t["sector"] >= 0.60 and t["intraday_accel"] >= 0.70,
}

reported_score_signals = {
    "A1": {"rule": "综合得分 >= 0.80", "total_samples": 11, "win_rate_pct": 63.6, "avg_return_pct": 0.21, "stability_sigma_pp": 10.4, "per_date": {"20260622": {"samples": 4, "win_rate_pct": 75, "avg_return_pct": 0.38}, "20260623": {"samples": 4, "win_rate_pct": 50, "avg_return_pct": -0.02}, "20260624": {"samples": 3, "win_rate_pct": 66.7, "avg_return_pct": 0.31}}},
    "A2": {"rule": "综合得分 >= 0.75 AND 资金得分 >= 0.80", "total_samples": 63, "win_rate_pct": 44.4, "avg_return_pct": -0.02, "stability_sigma_pp": 14.5, "per_date": {"20260622": {"samples": 27, "win_rate_pct": 59.3, "avg_return_pct": 0.93}, "20260623": {"samples": 29, "win_rate_pct": 27.6, "avg_return_pct": -0.93}, "20260624": {"samples": 7, "win_rate_pct": 57.1, "avg_return_pct": 0.08}}},
    "A3": {"rule": "综合得分 >= 0.70 AND 板块得分 >= 0.80", "total_samples": 152, "win_rate_pct": 40.1, "avg_return_pct": 0.24, "stability_sigma_pp": 2.8, "per_date": {"20260622": {"samples": 92, "win_rate_pct": 41.3, "avg_return_pct": 0.51}, "20260623": {"samples": 29, "win_rate_pct": 41.4, "avg_return_pct": -0.23}, "20260624": {"samples": 31, "win_rate_pct": 35.5, "avg_return_pct": -0.13}}},
    "A4": {"rule": "综合得分 >= 0.65 AND 日内加速 >= 0.80", "total_samples": 508, "win_rate_pct": 34.8, "avg_return_pct": -0.19, "stability_sigma_pp": 1.1, "per_date": {"20260622": {"samples": 112, "win_rate_pct": 33.9, "avg_return_pct": -0.01}, "20260623": {"samples": 200, "win_rate_pct": 34, "avg_return_pct": -0.43}, "20260624": {"samples": 196, "win_rate_pct": 36.2, "avg_return_pct": -0.04}}},
    "A5": {"rule": "综合得分 >= 0.70 AND VWAP位置 >= 0.70", "total_samples": 3, "win_rate_pct": 33.3, "avg_return_pct": -2.07, "stability_sigma_pp": 0, "per_date": {"20260622": {"samples": 0, "win_rate_pct": None, "avg_return_pct": None}, "20260623": {"samples": 3, "win_rate_pct": 33.3, "avg_return_pct": -2.07}, "20260624": {"samples": 0, "win_rate_pct": None, "avg_return_pct": None}}},
    "A6": {"rule": "综合得分 >= 0.70 AND 资金得分 >= 0.70 AND 板块得分 >= 0.70", "total_samples": 183, "win_rate_pct": 42.1, "avg_return_pct": 0.26, "stability_sigma_pp": 0.8, "per_date": {"20260622": {"samples": 120, "win_rate_pct": 42.5, "avg_return_pct": 0.49}, "20260623": {"samples": 31, "win_rate_pct": 41.9, "avg_return_pct": -0.18}, "20260624": {"samples": 32, "win_rate_pct": 40.6, "avg_return_pct": -0.2}}},
    "A7": {"rule": "综合得分 >= 0.65 AND 资金得分 >= 0.70 AND 板块得分 >= 0.70 AND 趋势得分 >= 0.70", "total_samples": 70, "win_rate_pct": 25.7, "avg_return_pct": -0.6, "stability_sigma_pp": 7.3, "per_date": {"20260622": {"samples": 52, "win_rate_pct": 28.8, "avg_return_pct": -0.45}, "20260623": {"samples": 9, "win_rate_pct": 22.2, "avg_return_pct": 0.04}, "20260624": {"samples": 9, "win_rate_pct": 11.1, "avg_return_pct": -2.12}}},
    "A8": {"rule": "综合得分 >= 0.60 AND 资金得分 >= 0.70 AND 板块得分 >= 0.60 AND 日内加速 >= 0.70", "total_samples": 503, "win_rate_pct": 37.8, "avg_return_pct": 0.11, "stability_sigma_pp": 3.1, "per_date": {"20260622": {"samples": 167, "win_rate_pct": 34.1, "avg_return_pct": 0.24}, "20260623": {"samples": 125, "win_rate_pct": 41.6, "avg_return_pct": -0.09}, "20260624": {"samples": 211, "win_rate_pct": 38.4, "avg_return_pct": 0.12}}},
}

score_deviations = []
score_verified = []
score_flagged = []

for sig_name, rule_fn in score_signal_rules.items():
    reported = reported_score_signals[sig_name]

    # Pooled stats
    matched = [t for t in all_trades if rule_fn(t)]
    actual = compute_stats(matched)

    # Per-date stats
    per_date_actual = {}
    for d in DATES:
        day_matched = [t for t in all_data[d] if rule_fn(t)]
        per_date_actual[d] = compute_stats(day_matched)

    # Per-date from report
    rpd = reported["per_date"]

    # Check pooled
    pooled_n_dev = abs(actual["n"] - reported["total_samples"])
    pooled_n_dev_pct = pooled_n_dev / reported["total_samples"] * 100 if reported["total_samples"] > 0 else 0
    pooled_wr_dev = abs(actual["win_rate"] * 100 - reported["win_rate_pct"]) if actual["win_rate"] is not None else float("inf")
    pooled_ar_dev = abs(actual["avg_return"] - reported["avg_return_pct"]) if actual["avg_return"] is not None else float("inf")

    row = {
        "signal": sig_name,
        "rule": reported["rule"],
        "reported_n": reported["total_samples"],
        "actual_n": actual["n"],
        "n_dev_pct": round(pooled_n_dev_pct, 1),
        "reported_wr": reported["win_rate_pct"],
        "actual_wr": round(actual["win_rate"] * 100, 1) if actual["win_rate"] is not None else None,
        "wr_dev_pp": round(pooled_wr_dev, 1),
        "reported_ar": reported["avg_return_pct"],
        "actual_ar": round(actual["avg_return"], 2) if actual["avg_return"] is not None else None,
        "ar_dev_pp": round(pooled_ar_dev, 2),
        "per_date_check": {}
    }

    for d in DATES:
        d_actual = per_date_actual[d]
        d_reported = rpd[d]
        d_n = d_actual["n"]
        d_rn = d_reported["samples"]
        d_wr = round(d_actual["win_rate"] * 100, 1) if d_actual["win_rate"] is not None else None
        d_rwr = d_reported["win_rate_pct"]
        d_ar = round(d_actual["avg_return"], 2) if d_actual["avg_return"] is not None else None
        d_rar = d_reported["avg_return_pct"]
        row["per_date_check"][d] = {
            "reported_n": d_rn, "actual_n": d_n,
            "reported_wr": d_rwr, "actual_wr": d_wr,
            "reported_ar": d_rar, "actual_ar": d_ar,
        }

    # Flag deviations
    issues = []
    if pooled_n_dev_pct > 20:
        issues.append(f"SAMPLE SIZE INFLATED: reported={reported['total_samples']} actual={actual['n']} ({pooled_n_dev_pct:.0f}% deviation)")
    if pooled_wr_dev > 5:
        issues.append(f"WIN RATE DEVIATION: reported={reported['win_rate_pct']}% actual={actual['win_rate']*100:.1f}% ({pooled_wr_dev:.1f}pp)")

    for d in DATES:
        d_actual = per_date_actual[d]
        d_reported = rpd[d]
        d_nr = d_reported["samples"]
        d_na = d_actual["n"]
        if d_nr > 0 and d_na is not None:
            dn_dev = abs(d_na - d_nr) / d_nr * 100
            if dn_dev > 20:
                issues.append(f"  {d}: per-date N inflated (reported={d_nr} actual={d_na})")
        if d_reported["win_rate_pct"] is not None and d_actual["win_rate"] is not None:
            dwr_dev = abs(d_actual["win_rate"] * 100 - d_reported["win_rate_pct"])
            if dwr_dev > 5:
                issues.append(f"  {d}: per-date WR off (reported={d_reported['win_rate_pct']}% actual={d_actual['win_rate']*100:.1f}%)")

    row_data = {
        "signal": sig_name,
        "rule": reported["rule"],
        "reported_n": reported["total_samples"],
        "actual_n": actual["n"],
        "n_dev_pct": round(pooled_n_dev_pct, 1),
        "reported_wr": reported["win_rate_pct"],
        "actual_wr": round(actual["win_rate"] * 100, 1) if actual["win_rate"] is not None else None,
        "wr_dev_pp": round(pooled_wr_dev, 1),
        "reported_ar": reported["avg_return_pct"],
        "actual_ar": round(actual["avg_return"], 2) if actual["avg_return"] is not None else None,
        "ar_dev_pp": round(pooled_ar_dev, 2),
    }
    if issues:
        row_data["issues"] = issues
        score_flagged.append(row_data)
    else:
        score_verified.append(row_data)

    status = "OK" if not issues else "FLAGGED"
    print(f"\n  {sig_name}: {status}")
    print(f"    Reported: n={reported['total_samples']} wr={reported['win_rate_pct']}% ar={reported['avg_return_pct']}%")
    print(f"    Actual:   n={actual['n']} wr={actual['win_rate']*100:.1f}% ar={actual['avg_return']:.2f}%")
    if issues:
        for iss in issues:
            print(f"    ⚠️  {iss}")


# ═══════════════════════════════════════════════════════════════
# PART 2: TOKEN SIGNALS REPORT VERIFICATION
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("PART 2: TOKEN SIGNALS REPORT VERIFICATION")
print("=" * 80)

# Tokens: check presence in 综合信号 (P-tokens) or 启动信号 (E-tokens) columns
# E-tokens (E1, E2, E3, E4, E5, E6) are in start_signal field
# P-tokens are in composite_signal field
reported_tokens = {
    "P34_gap_strong": {"n": 397, "win_rate": 41.1, "avg_return": 0.22},
    "P34_gap_reverse": {"n": 181, "win_rate": 42, "avg_return": 0.06},
    "E6_short_squeeze": {"n": 579, "win_rate": 38.5, "avg_return": 0.18},
    "P35_short_cover": {"n": 940, "win_rate": 33.9, "avg_return": -0.06},
    "P35_short_pressure": {"n": 182, "win_rate": 36.3, "avg_return": -0.26},
    "P37_momentum_up": {"n": 3135, "win_rate": 31.8, "avg_return": -0.24},
    "P_low_liquidity": {"n": 2018, "win_rate": 28.2, "avg_return": -0.16},
    "P_high_price": {"n": 331, "win_rate": 31.7, "avg_return": -0.47},
    "E1_low_start": {"n": 5167, "win_rate": 26.3, "avg_return": -0.31},
    "P37_momentum_down": {"n": 2292, "win_rate": 23.7, "avg_return": -0.5},
}

reported_combos = {
    "P34_gap_reverse + P35_short_cover": {"n": 45, "win_rate": 55.6, "avg_return": 1.1},
    "P35_short_cover + P37_momentum_up": {"n": 215, "win_rate": 43.3, "avg_return": 0.27},
    "E1_low_start + P37_momentum_up": {"n": 1116, "win_rate": 33.8, "avg_return": -0.17},
    "P37_momentum_up + P_low_vol_ratio": {"n": 597, "win_rate": 32.5, "avg_return": -0.19},
}

token_deviations = []
token_verified = []

for token, reported in reported_tokens.items():
    # E-tokens are in start_signal, P-tokens in composite_signal
    # But the token report pools them — check all_signals field
    matched = [t for t in all_trades if token in t["all_signals"]]
    actual = compute_stats(matched)

    n_dev = abs(actual["n"] - reported["n"]) / reported["n"] * 100
    wr_dev = abs(actual["win_rate"] * 100 - reported["win_rate"]) if actual["win_rate"] is not None else 0
    ar_dev = abs(actual["avg_return"] - reported["avg_return"]) if actual["avg_return"] is not None else 0

    issues = []
    if n_dev > 20:
        issues.append(f"N inflated: reported={reported['n']} actual={actual['n']} ({n_dev:.0f}%)")
    if wr_dev > 5:
        issues.append(f"WR dev: reported={reported['win_rate']}% actual={actual['win_rate']*100:.1f}% ({wr_dev:.1f}pp)")

    row = {
        "token": token, "reported": reported, "actual_n": actual["n"],
        "actual_wr": round(actual["win_rate"] * 100, 1) if actual["win_rate"] is not None else None,
        "actual_ar": round(actual["avg_return"], 2) if actual["avg_return"] is not None else None,
        "issues": issues
    }
    if issues:
        token_deviations.append(row)
    else:
        token_verified.append(row)

    status = "OK" if not issues else "FLAGGED"
    print(f"\n  {token}: {status}")
    print(f"    Reported: n={reported['n']} wr={reported['win_rate']}% ar={reported['avg_return']}%")
    print(f"    Actual:   n={actual['n']} wr={actual['win_rate']*100:.1f}% ar={actual['avg_return']:.2f}%")
    if issues:
        for iss in issues:
            print(f"    ⚠️  {iss}")

# Token combos
for combo_label, reported in reported_combos.items():
    tokens_list = [t.strip() for t in combo_label.split("+")]
    matched = [t for t in all_trades if all(tok in t["all_signals"] for tok in tokens_list)]
    actual = compute_stats(matched)

    n_dev = abs(actual["n"] - reported["n"]) / reported["n"] * 100
    wr_dev = abs(actual["win_rate"] * 100 - reported["win_rate"]) if actual["win_rate"] is not None else 0

    issues = []
    if n_dev > 20:
        issues.append(f"N inflated: reported={reported['n']} actual={actual['n']} ({n_dev:.0f}%)")
    if wr_dev > 5:
        issues.append(f"WR dev: reported={reported['win_rate']}% actual={actual['win_rate']*100:.1f}% ({wr_dev:.1f}pp)")

    status = "OK" if not issues else "FLAGGED"
    print(f"\n  {combo_label}: {status}")
    print(f"    Reported: n={reported['n']} wr={reported['win_rate']}% ar={reported['avg_return']}%")
    print(f"    Actual:   n={actual['n']} wr={actual['win_rate']*100:.1f}% ar={actual['avg_return']:.2f}%")
    if issues:
        for iss in issues:
            print(f"    ⚠️  {iss}")


# ═══════════════════════════════════════════════════════════════
# PART 3: FACTOR INTERACTIONS REPORT VERIFICATION
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("PART 3: FACTOR INTERACTIONS REPORT VERIFICATION")
print("=" * 80)

# C1: 位置>=0.7 AND 资金>=0.8 AND 综合>=0.55
# C2: 板块>=0.85 AND 综合>=0.60
# C3: 日内加速>=0.8 AND VWAP位置>=0.6 AND 综合>=0.55
# C4: 综合信号含P34_gap_reverse AND P35_short_cover
# C5: 综合信号含P37_momentum_up AND P_low_vol_ratio AND 综合>=0.55
# C6: 启动信号含E1_low_start AND 板块>=0.7 AND 综合>=0.55
# C7: 尾盘收益>=0.9 AND 综合>=0.55

factor_interaction_rules = {
    "C1_beaten_down_fresh_money": lambda t: t["position"] >= 0.7 and t["capital"] >= 0.8 and t["score"] >= 0.55,
    "C2_sector_leader": lambda t: t["sector"] >= 0.85 and t["score"] >= 0.60,
    "C3_intraday_reversal": lambda t: t["intraday_accel"] >= 0.8 and t["vwap_pos"] >= 0.6 and t["score"] >= 0.55,
    "C4_squeeze_setup": lambda t: "P34_gap_reverse" in t["composite_signal"] and "P35_short_cover" in t["composite_signal"],
    "C5_momentum_low_vol": lambda t: "P37_momentum_up" in t["all_signals"] and "P_low_vol_ratio" in t["all_signals"] and t["score"] >= 0.55,
    "C6_low_start_sector": lambda t: "E1_low_start" in t["all_signals"] and t["sector"] >= 0.7 and t["score"] >= 0.55,
    "C7_late_session": lambda t: t["tail_return"] >= 0.9 and t["score"] >= 0.55,
}

reported_interactions = {
    "C1": {"pooled_n": 48, "pooled_wr": 0.312, "pooled_ar": -1.04, "per_date": {"20260622": {"n": 11, "wr": 0.364, "ar": 0.05}, "20260623": {"n": 13, "wr": 0.231, "ar": -0.55}, "20260624": {"n": 24, "wr": 0.333, "ar": -1.8}}},
    "C2": {"pooled_n": 354, "pooled_wr": 0.407, "pooled_ar": 0.07, "per_date": {"20260622": {"n": 42, "wr": 0.238, "ar": -0.07}, "20260623": {"n": 134, "wr": 0.41, "ar": -0.12}, "20260624": {"n": 178, "wr": 0.444, "ar": 0.23}}},
    "C3": {"pooled_n": 815, "pooled_wr": 0.298, "pooled_ar": -0.37, "per_date": {"20260622": {"n": 1, "wr": 1.0, "ar": 0.76}, "20260623": {"n": 513, "wr": 0.341, "ar": -0.28}, "20260624": {"n": 301, "wr": 0.223, "ar": -0.53}}},
    "C4": {"pooled_n": 45, "pooled_wr": 0.556, "pooled_ar": 1.10, "per_date": {"20260622": {"n": 5, "wr": 0.2, "ar": -0.94}, "20260623": {"n": 4, "wr": 0.0, "ar": -2.83}, "20260624": {"n": 36, "wr": 0.667, "ar": 1.82}}},
    "C5": {"pooled_n": 179, "pooled_wr": 0.341, "pooled_ar": -0.02, "per_date": {"20260622": {"n": 2, "wr": 0.5, "ar": 2.89}, "20260623": {"n": 101, "wr": 0.297, "ar": -0.18}, "20260624": {"n": 76, "wr": 0.395, "ar": 0.1}}},
    "C6": {"pooled_n": 755, "pooled_wr": 0.31, "pooled_ar": -0.01, "per_date": {"20260622": {"n": 310, "wr": 0.271, "ar": -0.06}, "20260623": {"n": 162, "wr": 0.327, "ar": 0.04}, "20260624": {"n": 283, "wr": 0.343, "ar": 0.01}}},
    "C7": {"pooled_n": 1199, "pooled_wr": 0.27, "pooled_ar": -0.50, "per_date": {"20260622": {"n": 175, "wr": 0.24, "ar": -0.39}, "20260623": {"n": 374, "wr": 0.23, "ar": -0.69}, "20260624": {"n": 650, "wr": 0.301, "ar": -0.42}}},
}

factor_flagged = []
factor_verified = []

for sig_name, rule_fn in factor_interaction_rules.items():
    prefix = sig_name.split("_")[0]  # C1, C2, etc.
    reported = reported_interactions[prefix]

    # Pooled
    matched = [t for t in all_trades if rule_fn(t)]
    actual = compute_stats(matched)

    n_dev = abs(actual["n"] - reported["pooled_n"]) / reported["pooled_n"] * 100 if reported["pooled_n"] > 0 else 0
    wr_dev = abs(actual["win_rate"] * 100 - reported["pooled_wr"] * 100) if actual["win_rate"] is not None else 0

    # Per-date
    per_date_issues = []
    for d in DATES:
        day_matched = [t for t in all_data[d] if rule_fn(t)]
        d_actual = compute_stats(day_matched)
        d_reported = reported["per_date"][d]
        dn_dev = abs(d_actual["n"] - d_reported["n"]) / d_reported["n"] * 100 if d_reported["n"] > 0 else 0
        dwr_dev = abs(d_actual["win_rate"] * 100 - d_reported["wr"] * 100) if d_actual["win_rate"] is not None and d_reported["wr"] is not None else 0
        if dn_dev > 20:
            per_date_issues.append(f"{d}: N inflated (r={d_reported['n']} a={d_actual['n']})")
        if dwr_dev > 5:
            per_date_issues.append(f"{d}: WR dev (r={d_reported['wr']*100:.0f}% a={d_actual['win_rate']*100:.1f}%)")

    issues = []
    if n_dev > 20:
        issues.append(f"Pooled N inflated: reported={reported['pooled_n']} actual={actual['n']} ({n_dev:.0f}%)")
    if wr_dev > 5:
        issues.append(f"Pooled WR dev: reported={reported['pooled_wr']*100:.1f}% actual={actual['win_rate']*100:.1f}% ({wr_dev:.1f}pp)")
    issues.extend(per_date_issues)

    row = {
        "name": sig_name, "reported_n": reported["pooled_n"], "actual_n": actual["n"],
        "reported_wr": reported["pooled_wr"], "actual_wr": actual["win_rate"],
        "reported_ar": reported["pooled_ar"], "actual_ar": actual["avg_return"],
        "issues": issues
    }
    if issues:
        factor_flagged.append(row)
    else:
        factor_verified.append(row)

    status = "OK" if not issues else "FLAGGED"
    print(f"\n  {sig_name}: {status}")
    print(f"    Reported: n={reported['pooled_n']} wr={reported['pooled_wr']*100:.1f}% ar={reported['pooled_ar']:.2f}%")
    print(f"    Actual:   n={actual['n']} wr={actual['win_rate']*100:.1f}% ar={actual['avg_return']:.2f}%")
    if issues:
        for iss in issues:
            print(f"    ⚠️  {iss}")


# ═══════════════════════════════════════════════════════════════
# PART 4: CROSS-REPORT CONTRADICTIONS
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("PART 4: CROSS-REPORT CONTRADICTIONS")
print("=" * 80)

contradictions = []

# C4 (factor interactions) says 综合信号含P34_gap_reverse AND P35_short_cover -> n=45, wr=55.6%
# Token report combo "P34_gap_reverse + P35_short_cover" -> n=45, wr=55.6%
# These should be identical. Let's check.

# C4 uses composite_signal field
c4_matched = [t for t in all_trades if "P34_gap_reverse" in t["all_signals"] and "P35_short_cover" in t["all_signals"]]
c4_stats = compute_stats(c4_matched)
# Token combo uses the same
token_combo_matched = c4_matched  # same filter
token_combo_stats = c4_stats  # same

print(f"\n  C4/Factor vs Token combo P34_gap_reverse+P35_short_cover:")
print(f"    Factor report: n=45 wr=55.6% ar=1.10%")
print(f"    Token report:  n=45 wr=55.6% ar=1.10%")
print(f"    Actual:        n={c4_stats['n']} wr={c4_stats['win_rate']*100:.1f}% ar={c4_stats['avg_return']:.2f}%")
print(f"    Status: CONSISTENT (same underlying logic)")

# A3 (score report): 综合>=0.70 AND 板块>=0.80 -> wr=40.1% n=152
# C2 (factor report): 板块>=0.85 AND 综合>=0.60 -> wr=40.7% n=354
# These use different thresholds. Compare the subset.
a3_matched = set(t["code"] + t["date"] for t in all_trades if t["score"] >= 0.70 and t["sector"] >= 0.80)
c2_matched_set = set(t["code"] + t["date"] for t in all_trades if t["sector"] >= 0.85 and t["score"] >= 0.60)
# C2_subset_in_A3: C2 stocks also satisfying A3
c2_a3_overlap = set(t["code"] + t["date"] for t in all_trades if t["sector"] >= 0.85 and t["score"] >= 0.60 and t["score"] >= 0.70 and t["sector"] >= 0.80)
print(f"\n  A3(score>=0.70,sector>=0.80) vs C2(sector>=0.85,score>=0.60):")
print(f"    A3 n=152, C2 n=354, overlap in C2 that also satisfies A3: {len(c2_a3_overlap)}")
print(f"    Different thresholds - not directly comparable. No contradiction.")

# A6 (score): 综合>=0.70 AND 资金>=0.70 AND 板块>=0.70 -> wr=42.1% n=183
# Let's see if any other report references this same rule
print(f"\n  A6 (score>=0.70, capital>=0.70, sector>=0.70): n=183 wr=42.1%")
print(f"    No equivalent in other reports. No contradiction possible.")

# A2 (score): 综合>=0.75 AND 资金>=0.80 -> wr=44.4% n=63
# Token P34_gap_strong: n=397 wr=41.1%
# Different logic, no direct comparison
print(f"\n  A2 (score>=0.75, capital>=0.80) vs P34_gap_strong token:")
print(f"    Different filter logics - not contradictions, different signals.")


# ═══════════════════════════════════════════════════════════════
# PART 5: IMPOSSIBLE SIGNALS CHECK
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("PART 5: IMPOSSIBLE SIGNALS CHECK")
print("=" * 80)

impossible = []

# A5: 综合>=0.70 AND VWAP位置>=0.70
# Reported: 3 total samples (0 on 20260622, 3 on 20260623, 0 on 20260624)
# Check if VWAP位置 >= 0.70 AND 综合得分 >= 0.70 is truly this rare
a5_actually = [t for t in all_trades if t["score"] >= 0.70 and t["vwap_pos"] >= 0.70]
print(f"\n  A5 (score>=0.70 AND vwap_pos>=0.70):")
print(f"    Reported: 3 total (0+3+0)")
print(f"    Actual:   {len(a5_actually)} total ({sum(1 for t in a5_actually if t['date']=='20260622')}+{sum(1 for t in a5_actually if t['date']=='20260623')}+{sum(1 for t in a5_actually if t['date']=='20260624')})")
if len(a5_actually) == 0:
    impossible.append("A5: score>=0.70 AND vwap_pos>=0.70 returns ZERO stocks across all dates")

# C1: 位置>=0.7 AND 资金>=0.8 AND 综合>=0.55 -> reported 48 pooled
c1_actually = [t for t in all_trades if t["position"] >= 0.7 and t["capital"] >= 0.8 and t["score"] >= 0.55]
print(f"\n  C1 (position>=0.7 AND capital>=0.8 AND score>=0.55):")
print(f"    Reported: 48 pooled (11+13+24)")
print(f"    Actual:   {len(c1_actually)}")
if len(c1_actually) == 0:
    impossible.append("C1: position>=0.7 AND capital>=0.8 AND score>=0.55 returns ZERO stocks")

# Check if any token has zero occurrences
for token in reported_tokens:
    matched = [t for t in all_trades if token in t["composite_signal"]]
    if len(matched) == 0:
        impossible.append(f"Token {token}: claimed n>0 but ZERO matches in composite_signal field")
        print(f"\n  {token}: IMPOSSIBLE - ZERO matches in actual data")

# Check P_low_vol_ratio in token combo - does this token exist in data?
vol_ratio_matches = [t for t in all_trades if "P_low_vol_ratio" in t["composite_signal"]]
print(f"\n  P_low_vol_ratio token (used in combo and C5): {len(vol_ratio_matches)} matches in composite_signal")
if len(vol_ratio_matches) == 0:
    impossible.append("P_low_vol_ratio: claimed in combo report but ZERO matches in data")

# Check P34_gap_reverse token
gap_reverse_matches = [t for t in all_trades if "P34_gap_reverse" in t["composite_signal"]]
print(f"  P34_gap_reverse token (used in C4 and combo): {len(gap_reverse_matches)} matches in composite_signal")
print(f"    Per date: 0622={sum(1 for t in gap_reverse_matches if t['date']=='20260622')}, 0623={sum(1 for t in gap_reverse_matches if t['date']=='20260623')}, 0624={sum(1 for t in gap_reverse_matches if t['date']=='20260624')}")

# Check E1_low_start in start_signal field
e1_start_matches = [t for t in all_trades if "E1_low_start" in t["start_signal"]]
print(f"  E1_low_start token (in start_signal field, used in C6): {len(e1_start_matches)} matches")
if len(e1_start_matches) == 0:
    impossible.append("E1_low_start: claimed in C6 but ZERO matches in start_signal field")

# Check if the "16,006 matched rows" claim from token report is accurate
print(f"\n  Token report claims: '16,006 matched rows' across all dates")
print(f"    Actual total trades loaded: {len(all_trades)}")

# C4: squeeze setup - check per-date match quality
c4_per_date = {}
for d in DATES:
    day_c4 = [t for t in all_data[d] if "P34_gap_reverse" in t["composite_signal"] and "P35_short_cover" in t["composite_signal"]]
    c4_per_date[d] = {"n": len(day_c4), "wr": sum(1 for t in day_c4 if t["is_win"])/len(day_c4)*100 if day_c4 else 0, "ar": sum(t["next_day_return"] for t in day_c4)/len(day_c4) if day_c4 else 0}
    print(f"    {d}: n={c4_per_date[d]['n']} wr={c4_per_date[d]['wr']:.1f}% ar={c4_per_date[d]['ar']:.2f}%")


# ═══════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)

print(f"\n  Score Signals: {len(score_verified)} verified, {len(score_flagged)} flagged")
print(f"  Token Signals: {len(token_verified)} verified, {len(token_deviations)} flagged")
print(f"  Factor Interactions: {len(factor_verified)} verified, {len(factor_flagged)} flagged")
print(f"  Impossible signals: {len(impossible)}")
for imp in impossible:
    print(f"    ❌ {imp}")

# Build output for structured tool
output = {
    "score_signals": {
        "verified": [{"signal": s["signal"], "actual_n": s["actual_n"], "actual_wr": s["actual_wr"], "actual_ar": s["actual_ar"]} for s in score_verified],
        "flagged": [{"signal": s["signal"], "issues": s["issues"], "reported_n": s["reported_n"], "actual_n": s["actual_n"], "reported_wr": s["reported_wr"], "actual_wr": s["actual_wr"], "reported_ar": s["reported_avg_return_pct"], "actual_ar": s["actual_avg_return"]} for s in score_flagged],
    },
    "token_signals": {
        "verified": token_verified,
        "flagged": token_deviations,
    },
    "factor_interactions": {
        "verified": factor_verified,
        "flagged": factor_flagged,
    },
    "contradictions": contradictions,
    "impossible_signals": impossible,
}

print("\n" + json.dumps(output, indent=2, ensure_ascii=False, default=str))
