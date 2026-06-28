#!/usr/bin/env python3
"""
Agent B: 独立验证隔夜回测数据
从原始 CSV 独立计算所有指标，不依赖 Agent A 的结果。
"""

import csv
import os
import glob
import json
from collections import defaultdict
from pathlib import Path

BASE = Path("/Users/taopeng/PycharmProjects/stock_fund_flow/research_data")

# === Load overnight_picks for each pick_date ===
def load_picks(pick_date):
    """Load overnight_picks.csv, return dict: code -> {entry_price, entry_change%, level, ...}"""
    fp = BASE / pick_date / "overnight_picks.csv"
    if not fp.exists():
        return {}
    picks = {}
    with open(fp, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row['代码'].strip()
            picks[code] = {
                'entry_price': float(row['最新价']),
                'entry_change_pct': float(row['涨跌幅']),
                'name': row.get('名称', '').strip(),
                'level': row.get('级别', '').strip(),
            }
    return picks

# === Cross-validate entry prices with scores.csv ===
def load_scores_price(pick_date, codes_to_check):
    """Load scores.csv, return dict: code -> {latest_price, prev_close, change_pct}"""
    fp = BASE / pick_date / "scores.csv"
    if not fp.exists():
        return {}
    result = {}
    with open(fp, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row['代码'].strip()
            if code in codes_to_check:
                result[code] = {
                    'latest_price': float(row['最新价']),
                    'prev_close': float(row.get('昨收', 0)),
                    'change_pct': float(row.get('涨跌幅', 0)),
                }
    return result

# === Find intraday snapshots in 0945-1015 window ===
def find_intraday_window(eval_date):
    """Find fund_flow_*.csv files in eval_date/intraday/, filter to 0945-1015 range.
    Returns list of (timestamp_str, filepath).
    If no 0945-1015 snapshots, fallback to earliest >= 0930.
    """
    intraday_dir = BASE / eval_date / "intraday"
    if not intraday_dir.exists():
        return []

    all_files = sorted(glob.glob(str(intraday_dir / "fund_flow_*.csv")))
    window_snapshots = []
    fallback_snapshots = []

    for fp in all_files:
        fname = os.path.basename(fp)
        # Extract timestamp from fund_flow_HHMMSS.csv
        ts_str = fname.replace("fund_flow_", "").replace(".csv", "")
        if len(ts_str) == 6 and ts_str.isdigit():
            hhmm = ts_str[:4]
            # 0945-1015 window
            if "0945" <= hhmm <= "1015":
                window_snapshots.append((ts_str, fp))
            # Fallback: >= 0930
            if hhmm >= "0930":
                fallback_snapshots.append((ts_str, fp))

    if window_snapshots:
        return window_snapshots
    elif fallback_snapshots:
        return [fallback_snapshots[0]]  # earliest >= 0930
    else:
        return []

# === Load price for a stock from a snapshot CSV ===
def load_snapshot_prices(filepath, codes_to_find):
    """Load 最新价 for specified codes from a fund_flow snapshot CSV."""
    prices = {}
    with open(filepath, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row['代码'].strip()
            if code in codes_to_find:
                prices[code] = float(row['最新价'])
    return prices

# === Main verification ===
def main():
    pick_dates = ["20260622", "20260623", "20260624", "20260625", "20260626"]
    eval_dates = ["20260623", "20260624", "20260625", "20260626", "20260627"]

    # ====== STEP 1: Load all picks ======
    all_picks = {}
    for pd in pick_dates:
        all_picks[pd] = load_picks(pd)
        print(f"[PICK] {pd}: {len(all_picks[pd])} stocks")
        for code, info in all_picks[pd].items():
            print(f"  {code} {info['name']}: entry={info['entry_price']}, change={info['entry_change_pct']}%, level={info['level']}")

    # ====== STEP 2: Entry price cross-validation ======
    print("\n" + "="*80)
    print("=== 入场价验证 (scores.csv vs overnight_picks.csv) ===")
    print("="*80)
    entry_mismatches = []
    for pd in pick_dates:
        picks = all_picks[pd]
        if not picks:
            continue
        scores = load_scores_price(pd, set(picks.keys()))
        for code, pinfo in picks.items():
            sinfo = scores.get(code, {})
            s_price = sinfo.get('latest_price', 'MISSING')
            pick_price = pinfo['entry_price']
            match = abs(pick_price - s_price) < 0.001 if isinstance(s_price, float) else False
            status = "OK" if match else "MISMATCH"
            if not match:
                entry_mismatches.append((pd, code, s_price, pick_price))
            print(f"  {pd} {code}: scores={s_price} picks={pick_price} -> {status}")

    # ====== STEP 3: Calculate exit prices ======
    print("\n" + "="*80)
    print("=== 出场价计算 ===")
    print("="*80)

    all_results = []  # list of dicts for each trade

    for i, pd in enumerate(pick_dates):
        ed = eval_dates[i]
        picks = all_picks[pd]
        if not picks:
            continue

        snapshots = find_intraday_window(ed)
        if not snapshots:
            print(f"\n  {pd}->{ed}: NO intraday data available")
            continue

        ts_list = [s[0] for s in snapshots]
        print(f"\n  {pd}->{ed}: {len(snapshots)} snapshots in window: {ts_list}")

        # Collect prices across all snapshots
        all_snapshot_prices = defaultdict(list)
        for ts_str, fp in snapshots:
            prices = load_snapshot_prices(fp, set(picks.keys()))
            for code, price in prices.items():
                all_snapshot_prices[code].append((ts_str, price))

        # Calculate exit prices
        for code, pinfo in picks.items():
            prices_list = all_snapshot_prices.get(code, [])
            if prices_list:
                exit_avg = sum(p[1] for p in prices_list) / len(prices_list)
                ret_pct = (exit_avg - pinfo['entry_price']) / pinfo['entry_price'] * 100
                result = {
                    'pick_date': pd,
                    'eval_date': ed,
                    'code': code,
                    'name': pinfo['name'],
                    'level': pinfo['level'],
                    'entry_price': pinfo['entry_price'],
                    'entry_change_pct': pinfo['entry_change_pct'],
                    'exit_prices': prices_list,
                    'exit_avg': round(exit_avg, 3),
                    'snapshot_count': len(prices_list),
                    'ret_pct': round(ret_pct, 4),
                    'is_win': ret_pct > 0,
                }
                all_results.append(result)
                print(f"    {code} {pinfo['name']}: entry={pinfo['entry_price']}, exit_avg={exit_avg:.3f} ({len(prices_list)} snaps), ret={ret_pct:.4f}% {'WIN' if ret_pct > 0 else 'LOSS'}")
            else:
                print(f"    {code} {pinfo['name']}: NOT FOUND in intraday snapshots!")

    # ====== STEP 4: Calculate summary stats ======
    print("\n" + "="*80)
    print("=== 汇总统计 ===")
    print("="*80)

    total_trades = len(all_results)
    wins = sum(1 for r in all_results if r['is_win'])
    losses = total_trades - wins
    wr = wins / total_trades * 100 if total_trades > 0 else 0
    avg_ret = sum(r['ret_pct'] for r in all_results) / total_trades if total_trades > 0 else 0
    total_ret = sum(r['ret_pct'] for r in all_results)

    print(f"\n  Total trades: {total_trades}")
    print(f"  Wins: {wins}")
    print(f"  Losses: {losses}")
    print(f"  WR: {wr:.2f}%")
    print(f"  Avg ret: {avg_ret:.4f}%")
    print(f"  Total ret: {total_ret:.4f}%")

    # ====== STEP 5: Daily breakdown ======
    print("\n" + "="*80)
    print("=== 逐日统计 ===")
    print("="*80)

    from itertools import groupby
    daily = defaultdict(list)
    for r in all_results:
        daily[r['pick_date']].append(r)

    for pd in sorted(daily.keys()):
        trades = daily[pd]
        tw = sum(1 for t in trades if t['is_win'])
        tl = len(trades) - tw
        td_wr = tw / len(trades) * 100 if trades else 0
        td_avg = sum(t['ret_pct'] for t in trades) / len(trades) if trades else 0
        td_total = sum(t['ret_pct'] for t in trades)
        print(f"\n  {pd} (n={len(trades)}): WR={td_wr:.1f}%, avg_ret={td_avg:.4f}%, total={td_total:.4f}%")
        for t in trades:
            print(f"    {t['code']} {t['name']}: ret={t['ret_pct']:.4f}% ({t['snapshot_count']} snaps, exit_avg={t['exit_avg']})")

    # ====== STEP 6: Check for locked-up stocks ======
    print("\n" + "="*80)
    print("=== 涨停排除检查 ===")
    print("="*80)
    for r in all_results:
        if r['entry_change_pct'] >= 9.9:
            print(f"  WARNING: {r['pick_date']} {r['code']} {r['name']}: entry change={r['entry_change_pct']}% (>=9.9%, should be excluded per Rule #1)")

    # ====== STEP 7: Save results to JSON ======
    output = {
        'agent': 'Agent B - Independent Verification',
        'summary': {
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'wr_pct': round(wr, 2),
            'avg_ret_pct': round(avg_ret, 6),
            'total_ret_pct': round(total_ret, 6),
        },
        'daily': {},
        'trades': [],
        'entry_mismatches': entry_mismatches,
    }

    for pd in sorted(daily.keys()):
        trades = daily[pd]
        tw = sum(1 for t in trades if t['is_win'])
        td_wr = tw / len(trades) * 100 if trades else 0
        td_avg = sum(t['ret_pct'] for t in trades) / len(trades) if trades else 0
        output['daily'][pd] = {
            'n': len(trades),
            'wins': tw,
            'losses': len(trades) - tw,
            'wr_pct': round(td_wr, 1),
            'avg_ret_pct': round(td_avg, 6),
            'total_ret_pct': round(sum(t['ret_pct'] for t in trades), 6),
        }

    for r in all_results:
        output['trades'].append({
            'pick_date': r['pick_date'],
            'eval_date': r['eval_date'],
            'code': r['code'],
            'name': r['name'],
            'level': r['level'],
            'entry_price': r['entry_price'],
            'entry_change_pct': r['entry_change_pct'],
            'exit_avg': r['exit_avg'],
            'snapshot_count': r['snapshot_count'],
            'ret_pct': r['ret_pct'],
            'is_win': r['is_win'],
        })

    out_path = BASE / "backtest" / "overnight_v2"
    out_path.mkdir(parents=True, exist_ok=True)
    with open(out_path / "agent_b_verify.json", 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  Results saved to: {out_path / 'agent_b_verify.json'}")

    # ====== STEP 8: Cross-compare with Agent A if available ======
    agent_a_path = BASE / "backtest" / "overnight_v2" / "overnight_backtest_v2.json"
    print("\n" + "="*80)
    print("=== Agent A vs Agent B 对比 ===")
    print("="*80)
    if agent_a_path.exists():
        with open(agent_a_path, 'r') as f:
            agent_a = json.load(f)

        a_summary = agent_a.get('summary', {})
        b_summary = output['summary']

        comparisons = [
            ('total_trades', 'n', 'Total trades'),
            ('wins', 'wins', 'Wins'),
            ('losses', 'losses', 'Losses'),
            ('wr_pct', 'wr_pct', 'WR'),
            ('avg_ret_pct', 'avg_ret_pct', 'Avg ret'),
            ('total_ret_pct', 'total_ret_pct', 'Total ret'),
        ]

        for a_key, b_key, label in comparisons:
            a_val = a_summary.get(a_key, 'N/A')
            b_val = b_summary.get(b_key, 'N/A')
            match = abs(a_val - b_val) < 0.01 if isinstance(a_val, (int, float)) and isinstance(b_val, (int, float)) else (a_val == b_val)
            status = "OK" if match else "MISMATCH"
            print(f"  {label}: Agent A={a_val}, Agent B={b_val} -> {status}")

        # Daily comparison
        print("\n  -- Daily --")
        a_daily = agent_a.get('daily', {})
        b_daily = output['daily']
        for pd in sorted(set(list(a_daily.keys()) + list(b_daily.keys()))):
            ad = a_daily.get(pd, {})
            bd = b_daily.get(pd, {})
            print(f"  {pd}:")
            for key in ['n', 'wins', 'losses', 'wr_pct', 'avg_ret_pct', 'total_ret_pct']:
                av = ad.get(key, 'N/A')
                bv = bd.get(key, 'N/A')
                match = abs(av - bv) < 0.01 if isinstance(av, (int,float)) and isinstance(bv, (int,float)) else (av == bv)
                status = "OK" if match else "MISMATCH"
                print(f"    {key}: A={av} B={bv} -> {status}")

        # Trade-by-trade comparison
        print("\n  -- Trade-by-trade --")
        a_trades = {(t['pick_date'], t['code']): t for t in agent_a.get('trades', [])}
        b_trades = {(t['pick_date'], t['code']): t for t in output['trades']}

        for key in sorted(set(a_trades.keys()) | set(b_trades.keys())):
            at = a_trades.get(key, {})
            bt = b_trades.get(key, {})
            match_ret = abs(at.get('ret_pct', 999) - bt.get('ret_pct', 999)) < 0.01
            print(f"    {key[0]} {key[1]}: ret A={at.get('ret_pct','?')} B={bt.get('ret_pct','?')} -> {'OK' if match_ret else 'MISMATCH'}")
    else:
        print(f"  Agent A JSON not found at {agent_a_path}")
        print("  (This is expected if Agent A has not yet generated results)")

if __name__ == "__main__":
    main()
