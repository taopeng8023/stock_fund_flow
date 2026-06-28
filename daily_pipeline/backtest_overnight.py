"""
隔夜回测脚本 — 严格遵循 FUNDAMENTAL_RULES.md 5 条基本事实
规则1: 涨停>=9.9%排除
规则4: 出场价使用 09:45-10:15 区间快照均价
规则5: 所有数据交叉验证

用法:
  python daily_pipeline/backtest_overnight.py 20260622 20260623
  python daily_pipeline/backtest_overnight.py --all  # 批量回测
"""

import csv
import json
import os
import sys
from datetime import datetime, timezone, timedelta

BJS_TZ = timezone(timedelta(hours=8))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH_ROOT = os.path.join(PROJECT_ROOT, "research_data")
OUTPUT_DIR = os.path.join(RESEARCH_ROOT, "backtest", "overnight_v2")

os.makedirs(OUTPUT_DIR, exist_ok=True)


def run_filter(pick_date, top_n=5):
    """运行 filter_overnight 获取选股列表"""
    import subprocess
    cmd = [
        sys.executable,
        os.path.join(PROJECT_ROOT, "daily_pipeline", "filter_overnight.py"),
        pick_date, str(top_n)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)

    picks = []
    picks_csv = os.path.join(RESEARCH_ROOT, pick_date, "overnight_picks.csv")
    if os.path.exists(picks_csv):
        with open(picks_csv, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                picks.append(r)
    return picks, result.stdout


def get_exit_prices(eval_date, codes, window_start="0945", window_end="1015"):
    """获取 eval_date 09:45-10:15 区间快照均价"""
    snap_dir = os.path.join(RESEARCH_ROOT, eval_date, "intraday")
    if not os.path.exists(snap_dir):
        return {}

    all_files = os.listdir(snap_dir)
    fund_snaps = sorted([f for f in all_files if f.startswith("fund_flow_")])

    # 筛选 0945-1015 区间
    exit_snaps = []
    for fn in fund_snaps:
        ts = fn.replace("fund_flow_", "").replace(".csv", "")
        if window_start <= ts[:4] <= window_end:
            exit_snaps.append(fn)

    # 0945-1015 无快照 → 不 fallback，标记数据不足
    # FUNDAMENTAL_RULES 第3/4条: 不可能09:30卖出，操作窗口为09:45-10:15

    exit_prices = {}
    for fn in exit_snaps:
        fpath = os.path.join(snap_dir, fn)
        with open(fpath, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                code = r["代码"]
                if code not in codes:
                    continue
                val = r.get("最新价", "")
                if val in ("-", "", None):
                    continue
                try:
                    price = float(val)
                except ValueError:
                    continue
                if code not in exit_prices:
                    exit_prices[code] = []
                exit_prices[code].append(price)

    exit_avg = {c: sum(v)/len(v) for c, v in exit_prices.items()}
    return exit_avg, exit_snaps


def backtest_single(pick_date, eval_date, top_n=5):
    """单日隔夜回测"""
    picks, stdout = run_filter(pick_date, top_n)

    if not picks:
        return {
            "pick_date": pick_date,
            "eval_date": eval_date,
            "picks": [],
            "trades": [],
            "valid_n": 0,
            "wr": None,
            "avg_ret": None,
            "total_ret": None,
            "note": "无选股",
            "data_quality_warning": None,
        }

    codes = {p["代码"] for p in picks}

    # --- 除权除息检测 ---
    # 原理: eval_date 的 昨收 = pick_date 的收盘价(官方)
    #       pick_date 14:31快照价(入场价) ≈ pick_date 收盘价
    #       若 eval_昨收 与 entry_price 偏差 >2%, 很可能发生除权
    ex_dividend_map = {}
    try:
        entry_prices = {p["代码"]: float(p["最新价"]) for p in picks}

        eval_snap_dir = os.path.join(RESEARCH_ROOT, eval_date, "intraday")
        if os.path.exists(eval_snap_dir) and entry_prices:
            eval_fund_files = sorted([f for f in os.listdir(eval_snap_dir) if f.startswith("fund_flow_")])
            if eval_fund_files:
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
                                    # 除权日昨收会被交易所下调, 与入场价产生显著偏差
                                    ex_dividend_map[c] = abs(entry - eval_zs) / entry > 0.02
                                except (ValueError, ZeroDivisionError):
                                    pass
    except Exception:
        pass

    exit_avg, exit_snaps = get_exit_prices(eval_date, codes)

    # 快照质量评级
    if len(exit_snaps) >= 3:
        snapshot_quality = "good"
    elif len(exit_snaps) >= 1:
        snapshot_quality = "thin"
    else:
        snapshot_quality = "insufficient"

    trades = []
    for p in picks:
        code = p["代码"]
        name = p["名称"]
        tier = p.get("级别", "?")

        entry = float(p["最新价"])
        exit_p = exit_avg.get(code)

        if exit_p is None:
            trades.append({
                "code": code, "name": name, "tier": tier,
                "entry": entry, "exit": None, "ret": None,
                "win": None, "note": "次日无数据",
                "snapshot_quality": snapshot_quality,
                "ex_dividend_possible": ex_dividend_map.get(code, None),
            })
            continue

        ret = round((exit_p - entry) / entry * 100, 2)
        win = ret > 0

        trades.append({
            "code": code, "name": name, "tier": tier,
            "entry": entry, "exit": round(exit_p, 2),
            "ret": ret, "win": win,
            "snapshot_quality": snapshot_quality,
            "ex_dividend_possible": ex_dividend_map.get(code, None),
        })

    valid = [t for t in trades if t["ret"] is not None]
    if valid:
        wr = round(sum(1 for t in valid if t["win"]) / len(valid) * 100, 1)
        avg_ret = round(sum(t["ret"] for t in valid) / len(valid), 2)
        total_ret = round(sum(t["ret"] for t in valid), 2)
    else:
        wr, avg_ret, total_ret = None, None, None

    result = {
        "pick_date": pick_date,
        "eval_date": eval_date,
        "exit_window": f"{exit_snaps[0] if exit_snaps else 'N/A'}~{exit_snaps[-1] if exit_snaps else 'N/A'}",
        "exit_snap_count": len(exit_snaps),
        "picks": picks,
        "trades": trades,
        "valid_n": len(valid),
        "wr": wr,
        "avg_ret": avg_ret,
        "total_ret": total_ret,
        "stdout": stdout,
        "data_quality_warning": "no_snapshot_in_window" if len(exit_snaps) == 0 else ("single_snapshot_day" if len(exit_snaps) == 1 else None),
    }
    return result


def backtest_all(dates, top_n=5):
    """批量回测"""
    results = []
    for pick_date, eval_date in dates:
        print(f"回测 {pick_date} -> {eval_date} ...")
        r = backtest_single(pick_date, eval_date, top_n)
        results.append(r)
        if r["valid_n"]:
            print(f"  {r['valid_n']}笔 WR={r['wr']}% avg={r['avg_ret']}%")
        else:
            print(f"  无有效交易")

    # 汇总
    all_trades = []
    for r in results:
        for t in r["trades"]:
            if t["ret"] is not None:
                all_trades.append(t)

    if all_trades:
        wins = sum(1 for t in all_trades if t["win"])
        n = len(all_trades)
        wr = round(wins / n * 100, 1)
        avg_ret = round(sum(t["ret"] for t in all_trades) / n, 2)
        total_ret = round(sum(t["ret"] for t in all_trades), 2)
        rets = [t["ret"] for t in all_trades]
        rets_sorted = sorted(rets)
        median = rets_sorted[n // 2] if n % 2 else (rets_sorted[n//2-1] + rets_sorted[n//2]) / 2

        # 按 tier 分组
        tier_stats = {}
        for t in all_trades:
            tk = t["tier"].split(":")[0] if ":" in t["tier"] else t["tier"]
            if tk not in tier_stats:
                tier_stats[tk] = {"n": 0, "wins": 0, "rets": []}
            tier_stats[tk]["n"] += 1
            if t["win"]:
                tier_stats[tk]["wins"] += 1
            tier_stats[tk]["rets"].append(t["ret"])

        for tk in tier_stats:
            ts = tier_stats[tk]
            ts["wr"] = round(ts["wins"] / ts["n"] * 100, 1)
            ts["avg_ret"] = round(sum(ts["rets"]) / ts["n"], 2)

        # 快照质量分布
        quality_good = sum(1 for t in all_trades if t.get("snapshot_quality") == "good")
        quality_thin = sum(1 for t in all_trades if t.get("snapshot_quality") == "thin")
        quality_insufficient = sum(1 for t in all_trades if t.get("snapshot_quality") == "insufficient")

        summary = {
            "total_n": n,
            "total_wins": wins,
            "total_losses": n - wins,
            "wr": wr,
            "avg_ret": avg_ret,
            "total_ret": total_ret,
            "median_ret": round(median, 2),
            "max_ret": round(max(rets), 2),
            "min_ret": round(min(rets), 2),
            "std_ret": round((sum((x - avg_ret)**2 for x in rets) / n) ** 0.5, 2),
            "tier_stats": tier_stats,
            "snapshot_quality_good": quality_good,
            "snapshot_quality_thin": quality_thin,
            "snapshot_quality_insufficient": quality_insufficient,
            "method": "filter_overnight top5 -> next_day 09:45-10:15 avg(exit) | FUNDAMENTAL_RULES v1",
        }
    else:
        summary = {"total_n": 0, "note": "无有效交易"}

    output = {
        "generated": datetime.now(BJS_TZ).strftime("%Y-%m-%d %H:%M"),
        "rules": [
            "1. 涨停>=9.9%排除买入",
            "2. 隔夜套利: T日尾盘买 T+1日早盘卖",
            "3. 不可能09:30卖出",
            "4. 出场价=09:45-10:15区间快照均价",
            "5. 所有数据交叉验证",
        ],
        "days": results,
        "summary": summary,
    }

    # 保存
    out_path = os.path.join(OUTPUT_DIR, "overnight_backtest_v2.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n已保存: {out_path}")
    return output


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        # 单日: python backtest_overnight.py 20260622 20260623
        pick_date = sys.argv[1]
        eval_date = sys.argv[2]
        result = backtest_single(pick_date, eval_date)
        print(json.dumps({k: v for k, v in result.items() if k not in ("picks", "stdout")},
                         ensure_ascii=False, indent=2, default=str))
    elif len(sys.argv) == 2 and sys.argv[1] == "--all":
        # 批量回测所有可用日期
        dates = [
            ("20260618", "20260622"),
            ("20260622", "20260623"),
            ("20260623", "20260624"),
            ("20260624", "20260625"),
            ("20260625", "20260626"),
        ]
        backtest_all(dates)
    else:
        print(__doc__)
        print("用法: python daily_pipeline/backtest_overnight.py <pick_date> <eval_date>")
        print("      python daily_pipeline/backtest_overnight.py --all")
