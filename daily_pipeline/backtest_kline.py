"""
K线形态维度回测 — 验证 kline_pattern 因子对隔夜交易胜率的贡献
用法:
  python daily_pipeline/backtest_kline.py                    # 默认日期对
  python daily_pipeline/backtest_kline.py --top 10 --min-kp 0.7  # 自定义
"""
import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

BJS_TZ = timezone(timedelta(hours=8))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH_ROOT = os.path.join(PROJECT_ROOT, "research_data")

DATE_PAIRS = [
    ("20260622", "20260623"),
    ("20260623", "20260624"),
    ("20260624", "20260625"),
    ("20260625", "20260626"),
    ("20260626", "20260627"),
    ("20260627", "20260628"),
    ("20260628", "20260629"),
    ("20260629", "20260630"),
    ("20260630", "20260701"),
]

WINDOW_START = "0945"
WINDOW_END = "1015"
MAX_ENTRY_CHG = 9.9
MIN_TURNOVER_YI = 0.5 if False else 0  # 放宽流动性过滤


def get_exit_prices(eval_date: str, target_codes: set) -> dict:
    """获取次日开盘区间均价 {code: avg_price}"""
    snap_dir = os.path.join(RESEARCH_ROOT, eval_date, "intraday")
    if not os.path.exists(snap_dir):
        return {}

    files = sorted([f for f in os.listdir(snap_dir) if f.startswith("fund_flow_")])
    exit_snaps = [f for f in files if WINDOW_START <= f.replace("fund_flow_", "").replace(".csv", "")[:4] <= WINDOW_END]

    exit_prices = defaultdict(list)
    for fn in exit_snaps:
        fpath = os.path.join(snap_dir, fn)
        try:
            with open(fpath, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    code = row.get("代码", "")
                    if code not in target_codes:
                        continue
                    val = row.get("最新价", "")
                    if val in ("-", "", None):
                        continue
                    try:
                        exit_prices[code].append(float(val))
                    except ValueError:
                        continue
        except Exception:
            continue

    return {code: sum(prices) / len(prices) for code, prices in exit_prices.items() if prices}


def load_scores(pick_date: str) -> list[dict]:
    """Load scores.csv, return list of rows."""
    fp = os.path.join(RESEARCH_ROOT, pick_date, "scores.csv")
    if not os.path.exists(fp):
        return []
    rows = []
    with open(fp, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def backtest_pair(pick_date: str, eval_date: str, top_n: int,
                  min_kp: float, min_score: float) -> dict:
    """Backtest one date pair."""
    rows = load_scores(pick_date)
    if not rows:
        return {"date": pick_date, "trades": 0, "wins": 0, "avg_ret": 0, "error": "no scores"}

    # Filter
    candidates = []
    for r in rows:
        try:
            score = float(r.get("综合得分", 0))
            chg = float(r.get("涨跌幅", 0))
            turnover = float(r.get("换手率", 0))
            kp = float(r.get("K线形态得分", 0.5))
        except (ValueError, TypeError):
            continue

        if chg >= MAX_ENTRY_CHG:
            continue
        if MIN_TURNOVER_YI > 0 and float(r.get("成交额", 0)) < MIN_TURNOVER_YI * 1e8:
            continue
        if score < min_score:
            continue
        if kp < min_kp:
            continue

        candidates.append((r["代码"], r["名称"], float(r.get("最新价", 0)),
                           chg, score, kp, r.get("行业", "")))

    if not candidates:
        return {"date": pick_date, "trades": 0, "wins": 0, "avg_ret": 0, "error": "no candidates"}

    # Sort by score desc, take top N
    candidates.sort(key=lambda x: -x[4])
    selected = candidates[:top_n]

    # Get exit prices
    codes = {c[0] for c in selected}
    exit_prices = get_exit_prices(eval_date, codes)

    trades = []
    for code, name, entry_price, chg, score, kp, industry in selected:
        if code not in exit_prices:
            continue
        exit_price = exit_prices[code]
        if entry_price <= 0:
            continue
        ret = (exit_price - entry_price) / entry_price
        trades.append({
            "code": code, "name": name, "entry": entry_price,
            "exit": exit_price, "ret": ret, "win": ret > 0,
            "score": score, "kp": kp, "industry": industry,
            "chg": chg,
        })

    wins = sum(1 for t in trades if t["win"])
    avg_ret = sum(t["ret"] for t in trades) / len(trades) * 100 if trades else 0
    wr = wins / len(trades) * 100 if trades else 0

    return {
        "date": pick_date, "eval": eval_date,
        "trades": len(trades), "wins": wins,
        "win_rate": wr, "avg_ret": avg_ret,
        "trades_list": trades,
    }


def main():
    parser = argparse.ArgumentParser(description="K线形态维度回测")
    parser.add_argument("--top", type=int, default=20, help="买入TOP N")
    parser.add_argument("--min-kp", type=float, default=0.0,
                        help="最低K线形态得分 (0=不限制)")
    parser.add_argument("--min-score", type=float, default=0.0,
                        help="最低综合得分 (0=不限制)")
    parser.add_argument("--all", action="store_true", help="显示所有交易")
    args = parser.parse_args()

    print(f"K线形态维度回测")
    print(f"  TOP-N: {args.top}  |  最低K线分: {args.min_kp}  |  最低综合分: {args.min_score}")
    print()

    all_trades = []
    total_wins = 0
    total_trades = 0
    pair_results = []

    for pick_date, eval_date in DATE_PAIRS:
        result = backtest_pair(pick_date, eval_date, args.top,
                               args.min_kp, args.min_score)
        pair_results.append(result)
        if result.get("error"):
            print(f"  {pick_date} → {eval_date}: {result['error']}")
            continue

        trades = result["trades"]
        total_trades += trades
        total_wins += result["wins"]
        all_trades.extend(result.get("trades_list", []))

        wr = result["win_rate"]
        ar = result["avg_ret"]
        bar = "▓" * int(wr / 5) if wr > 0 else ""
        print(f"  {pick_date}→{eval_date}: WR={wr:5.1f}% ({result['wins']}/{trades}) "
              f"avgR={ar:+5.2f}% {bar}")

    print()
    overall_wr = total_wins / total_trades * 100 if total_trades else 0
    overall_ar = sum(t["ret"] for t in all_trades) / len(all_trades) * 100 if all_trades else 0
    print(f"  ── 总计 ──")
    print(f"  总交易: {total_trades}  总胜: {total_wins}  胜率: {overall_wr:.1f}%  均收: {overall_ar:+.2f}%")

    # K-line pattern breakdown
    if all_trades:
        with_kp = [t for t in all_trades if t.get("kp", 0.5) > 0.55]
        without_kp = [t for t in all_trades if t.get("kp", 0.5) <= 0.55]
        if with_kp:
            kp_wr = sum(1 for t in with_kp if t["win"]) / len(with_kp) * 100
            kp_ar = sum(t["ret"] for t in with_kp) / len(with_kp) * 100
            print(f"  有K线信号({len(with_kp)}笔): WR={kp_wr:.1f}% avgR={kp_ar:+.2f}%")
        if without_kp:
            nk_wr = sum(1 for t in without_kp if t["win"]) / len(without_kp) * 100
            nk_ar = sum(t["ret"] for t in without_kp) / len(without_kp) * 100
            print(f"  无K线信号({len(without_kp)}笔): WR={nk_wr:.1f}% avgR={nk_ar:+.2f}%")

    print()
    if args.all and all_trades:
        print("  交易明细:")
        for t in sorted(all_trades, key=lambda x: -x["ret"]):
            kp_flag = "★" if t.get("kp", 0.5) > 0.55 else " "
            print(f"  {kp_flag} {t['code']:10s} {t['name']:8s} "
                  f"入场={t['entry']:.2f} 出场={t['exit']:.2f} "
                  f"收益={t['ret']*100:+.2f}% 得分={t['score']:.3f} KP={t.get('kp', 0.5):.2f}")

    # Return exit code: 0 if overall WR >= 80%, 1 otherwise
    return 0 if overall_wr >= 80.0 else 1


if __name__ == "__main__":
    sys.exit(main())
