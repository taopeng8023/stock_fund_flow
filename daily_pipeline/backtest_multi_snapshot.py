"""
多快照回测 — 评分日收盘价入场 × 下一交易日全量快照出场

入场价: pick_date 前复权收盘价 (kline_data JSON, 降级 scores.csv 昨收)
出场价: eval_date 全部 intraday 快照 (不再随机采样)

保存: research_data/backtest/multi_snapshot/<pick_date>/
  - snapshots/<time>.csv    每个快照的完整回测结果
  - summary.csv             该日汇总
用法: python -m daily_pipeline.backtest_multi_snapshot <pick_date> [eval_date]
"""
import csv
import json
import os
import random
import statistics
from datetime import datetime, timedelta, timezone

BJS_TZ = timezone(timedelta(hours=8))
RESEARCH_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data"
)


def _tof(val, default=0.0):
    if val is None or val == "" or val == "-":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _load_scores(date_str):
    """加载 scores.csv"""
    path = os.path.join(RESEARCH_ROOT, date_str, "scores.csv")
    if not os.path.exists(path):
        return None
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def _find_next_trading_day(date_str):
    """查找下一个有 intraday 数据的交易日"""
    d = datetime.strptime(date_str, "%Y%m%d")
    for _ in range(7):
        d += timedelta(days=1)
        next_str = d.strftime("%Y%m%d")
        intraday_dir = os.path.join(RESEARCH_ROOT, next_str, "intraday")
        if os.path.isdir(intraday_dir) and os.listdir(intraday_dir):
            return next_str
    return None


def _get_snapshot_times(eval_date):
    """获取 eval_date 所有 intraday 快照时间列表（已排序）"""
    intraday_dir = os.path.join(RESEARCH_ROOT, eval_date, "intraday")
    if not os.path.isdir(intraday_dir):
        return []
    files = sorted([
        f for f in os.listdir(intraday_dir)
        if f.startswith("fund_flow_") and f.endswith(".csv")
    ])
    times = []
    for f in files:
        # fund_flow_HHMMSS.csv → HHMMSS
        t = f.replace("fund_flow_", "").replace(".csv", "")
        times.append(t)
    return times


def _load_snapshot_prices(eval_date, time_str):
    """加载某个快照时刻的价格数据。返回 {code: {close, open, chg}}"""
    intraday_dir = os.path.join(RESEARCH_ROOT, eval_date, "intraday")
    path = os.path.join(intraday_dir, f"fund_flow_{time_str}.csv")
    if not os.path.exists(path):
        return {}

    price_map = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = r.get("代码", "")
            close_p = _tof(r.get("最新价"))
            open_p = _tof(r.get("开盘"))
            chg_pct = _tof(r.get("涨跌幅"))
            if code and close_p > 0:
                price_map[code] = {"close": close_p, "open": open_p, "chg": chg_pct}
    return price_map


def _find_prev_close_date(date_str):
    """在 kline_data 中找到 date_str 当天或最近一个交易日的 bar date。
    如果当天无 bar（非交易日），向前找最近有 bar 的日期。"""
    # 抽样一只股票来找该日期的 bar
    import glob
    kline_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kline_data"
    )
    candidates = [
        f for f in os.listdir(kline_dir)
        if f.endswith(".json") and not f.startswith("_")
    ]
    if not candidates:
        # 降级：返回 YYYY-MM-DD 格式
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    sample_path = os.path.join(kline_dir, candidates[0])
    with open(sample_path) as fh:
        data = json.load(fh)
    bars = data.get("bars", [])
    # kline_data bar dates 是 "YYYY-MM-DD" 格式，需要归一化为 "YYYYMMDD" 再比较
    dates = sorted(set(b["date"] for b in bars if b.get("date")))
    dates_normalized = [d.replace("-", "") for d in dates]
    # 找 <= date_str 的最大日期
    for i in range(len(dates) - 1, -1, -1):
        if dates_normalized[i] <= date_str:
            return dates[i]  # 返回原始格式 "YYYY-MM-DD"
    # 降级：返回原日期 YYYY-MM-DD 格式
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"


def _load_entry_prices(codes, pick_date, scores_map=None):
    """从 kline_data 批量加载 pick_date 收盘价作为入场价。
    返回 {code: close_price}。未找到的 code 降级使用 scores_map 的昨收。
    """
    try:
        from sector_screener.structurer.price_loader import load_multi_bars_local
    except ImportError:
        load_multi_bars_local = None

    # 先确定实际可用的 bar date（可能 pick_date 是非交易日）
    effective_date = _find_prev_close_date(pick_date)

    entry_prices = {}
    missing_codes = []

    if load_multi_bars_local:
        bars_map = load_multi_bars_local(list(codes), days=250)
        for code in codes:
            bars = bars_map.get(code)
            if bars:
                # 查找 effective_date 对应的 bar
                for b in reversed(bars):
                    if b.date == effective_date:
                        entry_prices[code] = b.close
                        break
                else:
                    # 没找到该日期，尝试降级
                    missing_codes.append(code)
            else:
                missing_codes.append(code)
    else:
        missing_codes = list(codes)

    # 降级：用 scores_map 的昨收
    if missing_codes and scores_map:
        for code in missing_codes:
            prev_close = scores_map.get(code)
            if prev_close and prev_close > 0:
                entry_prices[code] = prev_close

    return entry_prices


def run_backtest(pick_date, eval_date=None):
    """
    多快照回测主函数 — 收盘价入场 × 全量快照出场。
    返回 dict: {pick_date, eval_date, snapshots: [{time, total, avg_ret, win_rate,
                top50_ret, top50_wr, factor_edge, deciles, ...}], aggregate: {...}}
    """
    if eval_date is None:
        eval_date = _find_next_trading_day(pick_date)
    if not eval_date:
        print(f"  ✗ {pick_date} 之后无有效交易日")
        return None

    print(f"\n{'='*60}")
    print(f"多快照回测 (收盘价入场 × 全量快照): {pick_date} → {eval_date}")
    print(f"{'='*60}")

    # 加载评分
    scores = _load_scores(pick_date)
    if not scores:
        print(f"  ✗ {pick_date} 无评分数据")
        return None

    # 获取所有快照时间
    all_times = _get_snapshot_times(eval_date)
    if not all_times:
        print(f"  ✗ {eval_date} 无 intraday 快照")
        return None

    print(f"  全量快照: {len(all_times)} 个")

    # ── 加载入场价: pick_date 收盘价 (kline_data 前复权) ──
    all_codes = [s["代码"] for s in scores]
    scores_prev_close = {}
    for s in scores:
        prev = _tof(s.get("昨收"))
        if prev > 0:
            scores_prev_close[s["代码"]] = prev
    entry_prices = _load_entry_prices(all_codes, pick_date, scores_map=scores_prev_close)
    print(f"  入场价加载: {len(entry_prices)}/{len(all_codes)} 只 (kline_data 收盘价)")

    # 对每个快照进行回测
    snapshot_results = []
    for t in all_times:
        prices = _load_snapshot_prices(eval_date, t)
        if not prices:
            print(f"  ⚠ 快照 {t} 无数据，跳过")
            continue

        # 匹配评分与快照价格
        matched = []
        rets = []
        for s in scores:
            code = s["代码"]
            if code not in prices:
                continue
            # 入场价: pick_date 收盘价 (前复权)
            pick_price = entry_prices.get(code)
            if pick_price is None or pick_price <= 0:
                continue

            p = prices[code]
            snap_close = p["close"]
            snap_open = p["open"]
            snap_chg = p["chg"]

            # 收益：评分日收盘 → 快照时刻价格
            ret_close = round((snap_close - pick_price) / pick_price * 100, 2)
            ret_open = round((snap_open - pick_price) / pick_price * 100, 2) if snap_open > 0 else None

            win = "胜" if ret_close > 0 else ("平" if ret_close == 0 else "负")

            matched.append({
                "代码": code,
                "名称": s["名称"],
                "综合得分": float(s.get("综合得分", 0)),
                "行业": s.get("行业", ""),
                "入场价(收盘)": pick_price,
                "快照价格": snap_close,
                "快照开盘": snap_open,
                "快照涨跌幅": snap_chg,
                "收益%": ret_close,
                "开盘收益%": ret_open,
                "胜负": win,
            })
            rets.append(ret_close)

        if not matched:
            print(f"  ⚠ 快照 {t} 无匹配股票，跳过")
            continue

        # 排序 + 统计
        matched.sort(key=lambda x: -x["综合得分"])
        total = len(matched)
        top50 = matched[:50]
        top50_ret = statistics.mean(r["收益%"] for r in top50) if top50 else 0
        top50_wr = sum(1 for r in top50 if r["胜负"] == "胜") / len(top50) if top50 else 0

        avg_ret = statistics.mean(rets) if rets else 0
        win_rate = sum(1 for r in matched if r["胜负"] == "胜") / total if total else 0

        # 随机50 对照组
        random.seed(42)
        rand50 = random.sample(matched, min(50, total))
        rand50_ret = statistics.mean(r["收益%"] for r in rand50) if rand50 else 0
        rand50_wr = sum(1 for r in rand50 if r["胜负"] == "胜") / len(rand50) if rand50 else 0

        # 十分位
        decile_size = total // 10
        deciles = []
        for i in range(10):
            start_idx = i * decile_size
            end_idx = (i + 1) * decile_size if i < 9 else total
            g = matched[start_idx:end_idx]
            d_ret = statistics.mean(r["收益%"] for r in g) if g else 0
            d_wr = sum(1 for r in g if r["胜负"] == "胜") / len(g) if g else 0
            deciles.append({"ret": round(d_ret, 4), "wr": round(d_wr, 4)})

        # 因子区分度: Top D1 vs Bottom D10
        d1_ret = statistics.mean(r["收益%"] for r in matched[:decile_size]) if decile_size > 0 else 0
        d10_ret = statistics.mean(r["收益%"] for r in matched[-decile_size:]) if decile_size > 0 else 0
        factor_edge = round(d1_ret - d10_ret, 4)

        # Top10 / Bottom10 更细粒度
        top10 = matched[:10]
        top10_ret = statistics.mean(r["收益%"] for r in top10) if top10 else 0
        top10_wr = sum(1 for r in top10 if r["胜负"] == "胜") / len(top10) if top10 else 0

        bottom10 = matched[-10:]
        bottom10_ret = statistics.mean(r["收益%"] for r in bottom10) if bottom10 else 0

        snap_result = {
            "time": t,
            "total": total,
            "avg_ret": round(avg_ret, 4),
            "win_rate": round(win_rate, 4),
            "top10_ret": round(top10_ret, 4),
            "top10_wr": round(top10_wr, 4),
            "top50_ret": round(top50_ret, 4),
            "top50_wr": round(top50_wr, 4),
            "rand50_ret": round(rand50_ret, 4),
            "rand50_wr": round(rand50_wr, 4),
            "bottom10_ret": round(bottom10_ret, 4),
            "factor_edge": factor_edge,
            "deciles": deciles,
            "rows": matched,
        }
        snapshot_results.append(snap_result)
        print(f"  [{t}] N={total} 均收益={avg_ret:+.2f}% WR={win_rate:.1%} "
              f"Top50={top50_ret:+.2f}% 区分度={factor_edge:+.2f}%")

    if not snapshot_results:
        print(f"  ✗ {pick_date} → {eval_date} 无有效快照结果")
        return None

    # ── 汇总统计 ──
    all_avg_rets = [s["avg_ret"] for s in snapshot_results]
    all_top50_rets = [s["top50_ret"] for s in snapshot_results]
    all_edges = [s["factor_edge"] for s in snapshot_results]
    all_wrs = [s["win_rate"] for s in snapshot_results]

    aggregate = {
        "num_snapshots": len(snapshot_results),
        "snapshot_times": [s["time"] for s in snapshot_results],
        "avg_ret_mean": round(statistics.mean(all_avg_rets), 4),
        "avg_ret_stdev": round(statistics.stdev(all_avg_rets), 4) if len(all_avg_rets) > 1 else 0,
        "top50_ret_mean": round(statistics.mean(all_top50_rets), 4),
        "top50_ret_stdev": round(statistics.stdev(all_top50_rets), 4) if len(all_top50_rets) > 1 else 0,
        "factor_edge_mean": round(statistics.mean(all_edges), 4),
        "factor_edge_stdev": round(statistics.stdev(all_edges), 4) if len(all_edges) > 1 else 0,
        "win_rate_mean": round(statistics.mean(all_wrs), 4),
        "avg_ret_range": [round(min(all_avg_rets), 4), round(max(all_avg_rets), 4)],
        "top50_ret_range": [round(min(all_top50_rets), 4), round(max(all_top50_rets), 4)],
    }

    # ── 保存结果 ──
    out_dir = os.path.join(RESEARCH_ROOT, "backtest", "multi_snapshot", pick_date)
    snap_dir = os.path.join(out_dir, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)

    # 每个快照保存完整 CSV
    for snap in snapshot_results:
        t = snap["time"]
        snap_path = os.path.join(snap_dir, f"snapshot_{t}.csv")
        fields = [
            "代码", "名称", "行业", "综合得分", "入场价(收盘)",
            "快照价格", "快照开盘", "快照涨跌幅",
            "收益%", "开盘收益%", "胜负",
        ]
        with open(snap_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(snap["rows"])
        print(f"  ✓ 快照结果: {snap_path}")

    # 汇总 CSV
    summary_path = os.path.join(out_dir, "summary.csv")
    with open(summary_path, "w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "快照时间", "总股票数", "全市场均收益", "全市场胜率",
            "Top10均收益", "Top10胜率", "Top50均收益", "Top50胜率",
            "随机50均收益", "随机50胜率", "Bottom10均收益",
            "因子区分度", "D1均收益", "D10均收益",
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for snap in snapshot_results:
            w.writerow({
                "快照时间": snap["time"],
                "总股票数": snap["total"],
                "全市场均收益": snap["avg_ret"],
                "全市场胜率": snap["win_rate"],
                "Top10均收益": snap["top10_ret"],
                "Top10胜率": snap["top10_wr"],
                "Top50均收益": snap["top50_ret"],
                "Top50胜率": snap["top50_wr"],
                "随机50均收益": snap["rand50_ret"],
                "随机50胜率": snap["rand50_wr"],
                "Bottom10均收益": snap["bottom10_ret"],
                "因子区分度": snap["factor_edge"],
                "D1均收益": snap["deciles"][0]["ret"] if snap["deciles"] else 0,
                "D10均收益": snap["deciles"][-1]["ret"] if snap["deciles"] else 0,
            })
    print(f"  ✓ 汇总: {summary_path}")

    # JSON 统计摘要
    stats_path = os.path.join(out_dir, "stats.json")
    stats_out = {
        "pick_date": pick_date,
        "eval_date": eval_date,
        "snapshots": [
            {k: v for k, v in s.items() if k != "rows"}
            for s in snapshot_results
        ],
        "aggregate": aggregate,
    }
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats_out, f, ensure_ascii=False, indent=2)
    print(f"  ✓ 统计: {stats_path}")

    print(f"\n  汇总: N={len(snapshot_results)} 快照 "
          f"均收益={aggregate['avg_ret_mean']:+.2f}%±{aggregate['avg_ret_stdev']:.2f} "
          f"Top50={aggregate['top50_ret_mean']:+.2f}%±{aggregate['top50_ret_stdev']:.2f} "
          f"区分度={aggregate['factor_edge_mean']:+.2f}%±{aggregate['factor_edge_stdev']:.2f}")

    return {"pick_date": pick_date, "eval_date": eval_date,
            "aggregate": aggregate, "snapshots": snapshot_results}


if __name__ == "__main__":
    import sys
    pick = sys.argv[1] if len(sys.argv) > 1 else None
    eval_d = sys.argv[2] if len(sys.argv) > 2 else None
    if pick:
        result = run_backtest(pick, eval_d)
        if result:
            agg = result["aggregate"]
            print(f"\n✓ {pick} → {result['eval_date']} 完成")
            print(f"  Top50均收益: {agg['top50_ret_mean']:+.2f}%")
            print(f"  因子区分度: {agg['factor_edge_mean']:+.2f}%")
        else:
            print(f"\n✗ {pick} 回测失败")
            sys.exit(1)
    else:
        print("用法: python -m daily_pipeline.backtest_multi_snapshot <pick_date> [eval_date]")
