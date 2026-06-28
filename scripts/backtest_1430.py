#!/usr/bin/env python3
"""
14:30 入场回测 — pick_date 14:30 后快照入场 × eval_date 全量快照出场

入场价: pick_date 自身 intraday 目录下第一个 >= 143000 的快照"最新价"
出场价: eval_date 全部 intraday 快照
用法: python scripts/backtest_1430.py
"""
import csv
import json
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from daily_pipeline.backtest_multi_snapshot import (
    _find_next_trading_day,
    _get_snapshot_times,
    _load_snapshot_prices,
    _load_scores,
    _tof,
)

RESEARCH_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data"
)

# ---- 入场价 ------------------------------------------------------

def _find_first_1430_snapshot(pick_date):
    """返回 pick_date 自身第一个在 14:30-15:30 之间的快照时间，无则返回 None"""
    all_times = _get_snapshot_times(pick_date)
    for t in all_times:
        if "143000" <= t <= "153000":
            return t
    return None


def _load_entry_prices_from_1430(pick_date, snapshot_time):
    """从 pick_date 的指定快照读取最新价作为入场价，返回 {code: price}"""
    path = os.path.join(RESEARCH_ROOT, pick_date, "intraday", f"fund_flow_{snapshot_time}.csv")
    if not os.path.exists(path):
        return {}
    prices = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = r.get("代码", "")
            close_p = _tof(r.get("最新价"))
            if code and close_p > 0:
                prices[code] = close_p
    return prices


# ---- 匹配 & 统计 --------------------------------------------------

def _match_scores_to_snapshot(scores, entry_prices, prices):
    """将评分股票匹配到快照价格，返回按综合得分降序排列的 rows 列表"""
    rows = []
    for s in scores:
        code = s["代码"]
        if code not in prices or code not in entry_prices:
            continue
        entry_p = entry_prices[code]
        if entry_p <= 0:
            continue
        snap_close = prices[code]["close"]
        ret = round((snap_close - entry_p) / entry_p * 100, 2)
        win = "胜" if ret > 0 else ("平" if ret == 0 else "负")
        rows.append({
            "代码": code, "名称": s["名称"],
            "综合得分": float(s.get("综合得分", 0)), "行业": s.get("行业", ""),
            "入场价": entry_p, "快照价": snap_close, "收益%": ret, "胜负": win,
        })
    rows.sort(key=lambda x: -x["综合得分"])
    return rows


def _compute_snapshot_stats(matched):
    """对已匹配结果计算全部统计指标"""
    total = len(matched)
    if total == 0:
        return None

    rets = [r["收益%"] for r in matched]
    wins = sum(1 for r in matched if r["胜负"] == "胜")

    def _ret(g):
        return statistics.mean(r["收益%"] for r in g) if g else 0.0

    def _wr(g):
        return sum(1 for r in g if r["胜负"] == "胜") / len(g) if g else 0.0

    top10, top50, bottom10 = matched[:10], matched[:50], matched[-10:]
    random.seed(42)
    rand50 = random.sample(matched, min(50, total))

    decile_size = total // 10
    deciles = []
    for i in range(10):
        start = i * decile_size
        end = (i + 1) * decile_size if i < 9 else total
        g = matched[start:end]
        deciles.append({"ret": round(_ret(g), 4), "wr": round(_wr(g), 4)})

    factor_edge = round(deciles[0]["ret"] - deciles[-1]["ret"], 4)
    return {
        "total": total,
        "avg_ret": round(statistics.mean(rets), 4),
        "win_rate": round(wins / total, 4),
        "top10_ret": round(_ret(top10), 4), "top10_wr": round(_wr(top10), 4),
        "top50_ret": round(_ret(top50), 4), "top50_wr": round(_wr(top50), 4),
        "rand50_ret": round(_ret(rand50), 4), "rand50_wr": round(_wr(rand50), 4),
        "bottom10_ret": round(_ret(bottom10), 4),
        "factor_edge": factor_edge,
        "deciles": deciles,
    }


def _aggregate_snapshots(snapshot_results):
    """跨快照汇总统计"""
    avg_rets = [s["avg_ret"] for s in snapshot_results]
    top50_rets = [s["top50_ret"] for s in snapshot_results]
    edges = [s["factor_edge"] for s in snapshot_results]
    wrs = [s["win_rate"] for s in snapshot_results]

    def _mstd(vals):
        m = statistics.mean(vals)
        s = statistics.stdev(vals) if len(vals) > 1 else 0.0
        return round(m, 4), round(s, 4)

    avg_ret_mean, avg_ret_stdev = _mstd(avg_rets)
    top50_mean, top50_stdev = _mstd(top50_rets)
    edge_mean, edge_stdev = _mstd(edges)

    return {
        "num_snapshots": len(snapshot_results),
        "snapshot_times": [s["time"] for s in snapshot_results],
        "avg_ret_mean": avg_ret_mean, "avg_ret_stdev": avg_ret_stdev,
        "top50_ret_mean": top50_mean, "top50_ret_stdev": top50_stdev,
        "factor_edge_mean": edge_mean, "factor_edge_stdev": edge_stdev,
        "win_rate_mean": round(statistics.mean(wrs), 4),
        "avg_ret_range": [round(min(avg_rets), 4), round(max(avg_rets), 4)],
        "top50_ret_range": [round(min(top50_rets), 4), round(max(top50_rets), 4)],
    }


# ---- 持久化 ------------------------------------------------------

def _save_results(out_dir, pick_date, eval_date, entry_snap, snapshot_results, aggregate):
    """保存快照 CSV / 汇总 CSV / 统计 JSON"""
    snap_dir = os.path.join(out_dir, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    row_fields = ["代码", "名称", "行业", "综合得分", "入场价", "快照价", "收益%", "胜负"]
    for snap in snapshot_results:
        t = snap["time"]
        snap_path = os.path.join(snap_dir, f"snapshot_{t}.csv")
        with open(snap_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=row_fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(snap["rows"])
        print(f"  OK 快照: {snap_path}")

    sum_fields = [
        "快照时间", "总股票数", "全市场均收益", "全市场胜率",
        "Top10均收益", "Top10胜率", "Top50均收益", "Top50胜率",
        "随机50均收益", "随机50胜率", "Bottom10均收益",
        "因子区分度", "D1均收益", "D10均收益",
    ]
    summary_path = os.path.join(out_dir, "summary.csv")
    with open(summary_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sum_fields, extrasaction="ignore")
        w.writeheader()
        for snap in snapshot_results:
            dec = snap["deciles"]
            w.writerow({
                "快照时间": snap["time"], "总股票数": snap["total"],
                "全市场均收益": snap["avg_ret"], "全市场胜率": snap["win_rate"],
                "Top10均收益": snap["top10_ret"], "Top10胜率": snap["top10_wr"],
                "Top50均收益": snap["top50_ret"], "Top50胜率": snap["top50_wr"],
                "随机50均收益": snap["rand50_ret"], "随机50胜率": snap["rand50_wr"],
                "Bottom10均收益": snap["bottom10_ret"],
                "因子区分度": snap["factor_edge"],
                "D1均收益": dec[0]["ret"] if dec else 0,
                "D10均收益": dec[-1]["ret"] if dec else 0,
            })
    print(f"  OK 汇总: {summary_path}")

    stats_path = os.path.join(out_dir, "stats.json")
    stats_out = {
        "pick_date": pick_date, "eval_date": eval_date,
        "entry_snapshot": entry_snap,
        "snapshots": [{k: v for k, v in s.items() if k != "rows"} for s in snapshot_results],
        "aggregate": aggregate,
    }
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats_out, f, ensure_ascii=False, indent=2)
    print(f"  OK 统计: {stats_path}")


# ---- 主回测 ------------------------------------------------------

def _run_snapshot_loop(scores, entry_prices, all_times, eval_date):
    """逐快照回测，返回 [(time, stats, rows), ...]"""
    results = []
    for t in all_times:
        prices = _load_snapshot_prices(eval_date, t)
        if not prices:
            continue
        matched = _match_scores_to_snapshot(scores, entry_prices, prices)
        if not matched:
            continue
        stats = _compute_snapshot_stats(matched)
        if not stats:
            continue
        stats["time"] = t
        stats["rows"] = matched
        results.append(stats)
        print(f"  [{t}] N={stats['total']} 均收益={stats['avg_ret']:+.2f}% "
              f"WR={stats['win_rate']:.1%} Top50={stats['top50_ret']:+.2f}% "
              f"区分度={stats['factor_edge']:+.2f}%")
    return results


def run_backtest_1430(pick_date, eval_date=None):
    """pick_date 14:30 后快照入场 → eval_date 全量快照出场。返回 dict 或 None"""
    if eval_date is None:
        eval_date = _find_next_trading_day(pick_date)
    if not eval_date:
        print(f"  X {pick_date} 之后无有效交易日")
        return None

    print(f"\n{'='*60}")
    print(f"14:30入场回测: {pick_date} -> {eval_date}")
    print(f"{'='*60}")

    scores = _load_scores(pick_date)
    if not scores:
        print(f"  X {pick_date} 无评分数据")
        return None

    entry_snap = _find_first_1430_snapshot(pick_date)
    entry_prices = _load_entry_prices_from_1430(pick_date, entry_snap) if entry_snap else None
    if not entry_prices:
        print(f"  X {pick_date} 无 >= 14:30 入场价数据")
        return None
    print(f"  入场快照: {entry_snap} (入场价 {len(entry_prices)} 只)")

    all_times = _get_snapshot_times(eval_date)
    if not all_times:
        print(f"  X {eval_date} 无 intraday 快照")
        return None
    print(f"  出场快照: {len(all_times)} 个")

    snapshot_results = _run_snapshot_loop(scores, entry_prices, all_times, eval_date)
    if not snapshot_results:
        print(f"  X {pick_date} -> {eval_date} 无有效快照结果")
        return None

    aggregate = _aggregate_snapshots(snapshot_results)
    out_dir = os.path.join(RESEARCH_ROOT, "backtest", "1430_entry", pick_date)
    _save_results(out_dir, pick_date, eval_date, entry_snap, snapshot_results, aggregate)

    print(f"\n  汇总: N={aggregate['num_snapshots']} 快照 "
          f"均收益={aggregate['avg_ret_mean']:+.2f}%+/-{aggregate['avg_ret_stdev']:.2f} "
          f"Top50={aggregate['top50_ret_mean']:+.2f}%+/-{aggregate['top50_ret_stdev']:.2f} "
          f"区分度={aggregate['factor_edge_mean']:+.2f}%+/-{aggregate['factor_edge_stdev']:.2f}")

    return {
        "pick_date": pick_date, "eval_date": eval_date,
        "entry_snapshot": entry_snap, "aggregate": aggregate,
        "snapshots": snapshot_results,
    }


# ---- 入口 --------------------------------------------------------

def _print_cross_day_summary(all_summaries, skipped):
    """打印跨日汇总表格并保存 summary_all.csv"""
    print(f"\n{'='*60}\n  跨日汇总 (14:30 入场回测)\n{'='*60}")
    if not all_summaries:
        print(f"\n  无有效回测结果, {skipped} 跳过/失败")
        return

    keys = ["avg_ret_mean", "top50_ret_mean", "factor_edge_mean", "win_rate_mean"]
    header = f"  {'日期':<10} {'出场':<10} {'快照':<6} {'均收益':<12} {'Top50':<12} {'区分度':<12} {'胜率':<8}"
    print(f"\n{header}\n  {'-'*70}")
    for s in all_summaries:
        print(f"  {s['pick_date']:<10} {s['eval_date']:<10} {s['num_snapshots']:<6} "
              f"{s['avg_ret_mean']:+.2f}%       "
              f"{s['top50_ret_mean']:+.2f}%       "
              f"{s['factor_edge_mean']:+.2f}%       "
              f"{s['win_rate_mean']:.1%}")

    cross_means = {k: round(statistics.mean([s[k] for s in all_summaries]), 4) for k in keys}
    print(f"  {'-'*70}")
    print(f"  {'跨日均值':<10} {'':<10} {'':<6} "
          f"{cross_means['avg_ret_mean']:+.2f}%       "
          f"{cross_means['top50_ret_mean']:+.2f}%       "
          f"{cross_means['factor_edge_mean']:+.2f}%       "
          f"{cross_means['win_rate_mean']:.1%}")

    pos_top50 = sum(1 for s in all_summaries if s["top50_ret_mean"] > 0)
    pos_edge = sum(1 for s in all_summaries if s["factor_edge_mean"] > 0)
    print(f"\n  Top50 正收益: {pos_top50}/{len(all_summaries)} ({pos_top50/len(all_summaries):.0%})")
    print(f"  区分度 > 0: {pos_edge}/{len(all_summaries)} ({pos_edge/len(all_summaries):.0%})")

    out_path = os.path.join(RESEARCH_ROOT, "backtest", "1430_entry", "summary_all.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    all_fields = [
        "日期", "出场日", "入场快照", "快照数",
        "均收益均值", "均收益标准差", "Top50均值", "Top50标准差",
        "区分度均值", "区分度标准差", "胜率均值",
    ]
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_fields)
        w.writeheader()
        for s in all_summaries:
            w.writerow({
                "日期": s["pick_date"], "出场日": s["eval_date"],
                "入场快照": s["entry_snap"], "快照数": s["num_snapshots"],
                "均收益均值": s["avg_ret_mean"], "均收益标准差": s["avg_ret_stdev"],
                "Top50均值": s["top50_ret_mean"], "Top50标准差": s["top50_ret_stdev"],
                "区分度均值": s["factor_edge_mean"], "区分度标准差": s["factor_edge_stdev"],
                "胜率均值": s["win_rate_mean"],
            })
    print(f"\n  跨日汇总已保存: {out_path}")


def main():
    """遍历所有有 scores.csv 的日期进行 14:30 入场回测"""
    dates = sorted(
        d for d in os.listdir(RESEARCH_ROOT)
        if d.isdigit() and os.path.isfile(os.path.join(RESEARCH_ROOT, d, "scores.csv"))
    )

    print("=== 14:30 入场多快照回测 ===\n")
    print(f"  候选日期: {len(dates)} 天")
    print("  入场: pick_date 自身 >= 14:30 快照中的最新价")
    print("  出场: 下一交易日全量 intraday 快照\n")

    all_summaries = []
    skipped = 0

    for pick in dates:
        eval_date = _find_next_trading_day(pick)
        if not eval_date:
            print(f"  {pick} -> 无下一交易日，跳过")
            skipped += 1
            continue

        try:
            result = run_backtest_1430(pick, eval_date)
            if result:
                agg = result["aggregate"]
                all_summaries.append({
                    "pick_date": pick, "eval_date": eval_date,
                    "entry_snap": result["entry_snapshot"],
                    "num_snapshots": agg["num_snapshots"],
                    "avg_ret_mean": agg["avg_ret_mean"], "avg_ret_stdev": agg["avg_ret_stdev"],
                    "top50_ret_mean": agg["top50_ret_mean"], "top50_ret_stdev": agg["top50_ret_stdev"],
                    "factor_edge_mean": agg["factor_edge_mean"], "factor_edge_stdev": agg["factor_edge_stdev"],
                    "win_rate_mean": agg["win_rate_mean"],
                })
            else:
                skipped += 1
        except Exception as e:
            print(f"  X {pick} 异常: {e}")
            skipped += 1

    _print_cross_day_summary(all_summaries, skipped)
    print(f"\n  总计: {len(all_summaries)} 成功, {skipped} 跳过/失败")
    return 0


if __name__ == "__main__":
    sys.exit(main())
