"""
步骤3: 次日回测 — 对比前一日评分 vs 次日实际收益
保存 research_data/backtest/daily/backtest_<date>.csv
追加 research_data/backtest/summary.csv
"""
import csv
import os
import random
import statistics
import math
from datetime import datetime, timezone, timedelta

BJS_TZ = timezone(timedelta(hours=8))
RESEARCH_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data"
)


def _tof(val, default=0.0):
    if val is None or val == "" or val == "-": return default
    try: return float(val)
    except: return default


def _load_scores(date_str):
    """加载 scores.csv"""
    path = os.path.join(RESEARCH_ROOT, date_str, "scores.csv")
    if not os.path.exists(path): return None
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def _load_next_day_prices(date_str):
    """获取次日价格 — intraday CSV(含北交所) + fund_flow.json 互补。
    返回 {code: {open, close, chg}}
    """
    price_map = {}

    # 1. intraday CSV — 覆盖全交易所(含92xxxx北交所)
    intraday_dir = os.path.join(RESEARCH_ROOT, date_str, "intraday")
    if os.path.isdir(intraday_dir):
        files = sorted(
            [f for f in os.listdir(intraday_dir) if f.startswith("fund_flow_") and f.endswith(".csv")]
        )
        if files:
            path = os.path.join(intraday_dir, files[0])
            with open(path, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    code = r.get("代码", "")
                    open_p = _tof(r.get("开盘"))
                    close_p = _tof(r.get("最新价"))
                    chg = _tof(r.get("涨跌幅"))
                    if code and open_p > 0:
                        price_map[code] = {"open": open_p, "close": close_p, "chg": chg}

    # 2. fund_flow.json — 补充缺失(含更多主板股票)
    import json
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    ff_path = os.path.join(data_dir, date_str, "fund_flow.json")
    if os.path.exists(ff_path):
        with open(ff_path, encoding="utf-8") as f:
            for r in json.load(f):
                code = r.get("f12", "")
                if code in price_map:
                    continue  # 已有 intraday 数据，不覆盖
                open_p = _tof(r.get("f17"))
                close_p = _tof(r.get("f2"))
                chg = _tof(r.get("f3"))
                if code and open_p > 0:
                    price_map[code] = {"open": open_p, "close": close_p, "chg": chg}

    return price_map


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


def run_daily_backtest(pick_date, eval_date=None):
    """回测 pick_date 评分 vs eval_date 收益"""
    if eval_date is None:
        eval_date = _find_next_trading_day(pick_date)
    if not eval_date:
        print(f"  找不到 {pick_date} 之后的有效交易日")
        return None

    print(f"回测: {pick_date} → {eval_date}")

    scores = _load_scores(pick_date)
    if not scores:
        print(f"  ✗ {pick_date} 无评分数据")
        return None

    next_prices = _load_next_day_prices(eval_date)
    if not next_prices:
        print(f"  ✗ {eval_date} 无价格数据")
        return None

    # 匹配
    matched = 0
    rows = []
    rets = []
    for s in scores:
        code = s["代码"]
        if code not in next_prices: continue
        pick_price = _tof(s.get("最新价"))
        if pick_price <= 0: continue

        t = next_prices[code]
        open_p = t["open"]
        next_ret = round((open_p - pick_price) / pick_price * 100, 2)
        win = "胜" if next_ret > 0 else ("平" if next_ret == 0 else "负")

        rows.append({
            "代码": code, "名称": s["名称"], "综合得分": s["综合得分"],
            "选股日价格": pick_price, "次日开盘价": open_p,
            "次日收益%": next_ret, "胜负": win,
        })
        rets.append(next_ret)
        matched += 1

    print(f"  匹配: {matched}/{len(scores)} 只")

    # 排序 + 分析
    rows.sort(key=lambda x: -float(x["综合得分"]))
    total = len(rows)
    top50 = rows[:50]
    top50_ret = statistics.mean(r["次日收益%"] for r in top50)
    top50_wr = sum(1 for r in top50 if r["胜负"] == "胜") / len(top50) if top50 else 0

    random.seed(42)
    rand50 = random.sample(rows, min(50, total))
    rand50_ret = statistics.mean(r["次日收益%"] for r in rand50)
    rand50_wr = sum(1 for r in rand50 if r["胜负"] == "胜") / len(rand50) if rand50 else 0

    avg_ret = statistics.mean(rets) if rets else 0
    win_rate = sum(1 for r in rows if r["胜负"] == "胜") / total if total else 0

    # 十分位
    decile_size = total // 10
    deciles = []
    for i in range(10):
        g = rows[i * decile_size : (i + 1) * decile_size if i < 9 else total]
        d_ret = statistics.mean(r["次日收益%"] for r in g) if g else 0
        d_wr = sum(1 for r in g if r["胜负"] == "胜") / len(g) if g else 0
        deciles.append((d_ret, d_wr))

    factor_edge = top50_ret - (statistics.mean(r["次日收益%"] for r in rows[-50:]) if total >= 50 else avg_ret)

    print(f"  Top50: {top50_ret:+.2f}% WR={top50_wr:.1%}  |  随机50: {rand50_ret:+.2f}% WR={rand50_wr:.1%}")
    print(f"  全市场: {avg_ret:+.2f}% WR={win_rate:.1%}  |  区分度: {factor_edge:+.2f}%")

    # ── 保存 daily CSV ──
    daily_dir = os.path.join(RESEARCH_ROOT, "backtest", "daily")
    os.makedirs(daily_dir, exist_ok=True)
    daily_path = os.path.join(daily_dir, f"backtest_{pick_date}.csv")
    fields = ["代码", "名称", "综合得分", "选股日价格", "次日开盘价", "次日收益%", "胜负"]
    with open(daily_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  ✓ {daily_path}")

    # ── 追加 summary CSV ──
    summary_path = os.path.join(RESEARCH_ROOT, "backtest", "summary.csv")
    summary_exists = os.path.exists(summary_path)
    with open(summary_path, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if not summary_exists:
            w.writerow(["日期", "总股票", "全市场均收益", "全市场胜率",
                        "Top50均收益", "Top50胜率", "随机50均收益", "随机50胜率",
                        "因子区分度", "D1均收益", "D10均收益"])
        w.writerow([
            pick_date, total, round(avg_ret, 3), round(win_rate, 3),
            round(top50_ret, 3), round(top50_wr, 3),
            round(rand50_ret, 3), round(rand50_wr, 3),
            round(factor_edge, 3),
            round(deciles[0][0], 3) if deciles else 0,
            round(deciles[-1][0], 3) if deciles else 0,
        ])
    print(f"  ✓ summary 追加")

    return {
        "pick_date": pick_date, "eval_date": eval_date, "total": total,
        "avg_ret": avg_ret, "win_rate": win_rate,
        "top50_ret": top50_ret, "top50_wr": top50_wr,
        "rand50_ret": rand50_ret, "rand50_wr": rand50_wr,
        "factor_edge": factor_edge, "deciles": deciles,
    }


if __name__ == "__main__":
    import sys
    pick = sys.argv[1] if len(sys.argv) > 1 else None
    eval_d = sys.argv[2] if len(sys.argv) > 2 else None
    if pick:
        run_daily_backtest(pick, eval_d)
    else:
        print("用法: python backtest.py <pick_date> [eval_date]")
