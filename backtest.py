"""
选股回溯 — 用次日数据验证选股表现
用法:
  python backtest.py --pick=20260616                         自动取今日验证
  python backtest.py --pick=20260616 --eval=20260617         指定验证日
  python backtest.py --pick=20260616 --file=picks_190308.csv 指定选股文件
"""
import csv
import json
import os
import sys
import glob
from datetime import datetime
from fetchers.base import DATA_ROOT, BJS_TZ, load_json


def find_picks_file(date_str):
    """在 picks 子目录下找最新的选股文件"""
    picks_dir = os.path.join(DATA_ROOT, date_str, "picks")
    if os.path.isdir(picks_dir):
        files = sorted(glob.glob(os.path.join(picks_dir, "picks_*.csv")), reverse=True)
        if files:
            return files[0]
    return None


def run(pick_date, eval_date=None, picks_file=None):
    if eval_date is None:
        eval_date = datetime.now(BJS_TZ).strftime("%Y%m%d")

    if picks_file is None:
        picks_file = find_picks_file(pick_date)
    if picks_file is None or not os.path.exists(picks_file):
        print(f"选股文件不存在: {picks_file}")
        return None

    # 读取选股
    picks = []
    with open(picks_file, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            picks.append(row)
    print(f"选股文件: {picks_file}")
    print(f"选股日期: {pick_date} → 验证日期: {eval_date}")
    print(f"选股数量: {len(picks)} 只\n")

    # 加载验证日数据
    eval_rows = load_json(eval_date, "fund_flow")
    if not eval_rows:
        print(f"验证日数据不可用: {eval_date}")
        return None
    eval_map = {r.get("f12", ""): r for r in eval_rows}

    # 逐只回溯
    rows = []
    for p in picks:
        code = p["代码"]
        t = eval_map.get(code)
        if not t:
            continue

        pick_price = float(p.get("最新价", 0))
        today_close = float(t.get("f2", 0) or 0)
        today_chg = float(t.get("f3", 0) or 0)

        if pick_price > 0:
            next_ret = round((today_close - pick_price) / pick_price * 100, 2)
        else:
            next_ret = round(today_chg, 2)

        win = "胜" if next_ret > 0 else ("平" if next_ret == 0 else "负")

        rows.append({
            "排名": p.get("排名", ""),
            "代码": code,
            "名称": p.get("名称", ""),
            "综合得分": float(p.get("综合得分", 0)),
            "选股日涨跌幅": float(p.get("涨跌幅", 0)),
            "次日涨跌幅": next_ret,
            "胜负": win,
            "选股日主力净流入": float(p.get("主力净流入", 0)),
            "选股日主力占比": float(p.get("主力占比", 0)),
            "资金得分": float(p.get("资金得分", 0)),
            "趋势得分": float(p.get("趋势得分", 0)),
            "启动得分": float(p.get("启动得分", 0)),
            "板块得分": float(p.get("板块得分", 0)),
            "所属板块": p.get("所属板块", ""),
            "选中理由1": p.get("选中理由1", ""),
            "选中理由2": p.get("选中理由2", ""),
            "选中理由3": p.get("选中理由3", ""),
        })

    # 输出表格
    print(f"{'排名':<4} {'代码':<8} {'名称':<8} {'综合得分':<6} {'选股日涨跌':<8} {'次日涨跌':<8} {'胜负'}")
    print("-" * 60)
    for r in rows:
        print(f"{r['排名']:<4} {r['代码']:<8} {r['名称']:<8s} {r['综合得分']:.4f} "
              f"{r['选股日涨跌幅']:>+7.2f}% {r['次日涨跌幅']:>+7.2f}% {r['胜负']}")

    # 汇总
    total = len(rows)
    wins_count = sum(1 for r in rows if r["胜负"] == "胜")
    rets = [r["次日涨跌幅"] for r in rows]
    avg_ret = sum(rets) / total if total else 0
    mid = total // 2
    hi_avg = sum(rets[:mid]) / mid if mid else 0
    lo_avg = sum(rets[mid:]) / (total - mid) if total > mid else 0

    print(f"\n{'=' * 60}")
    print(f"回溯汇总  [{pick_date} → {eval_date}]")
    print(f"{'=' * 60}")
    print(f"  总选股: {total} 只")
    print(f"  胜率: {wins_count}/{total} ({wins_count/total*100:.1f}%)" if total else "")
    print(f"  均收益: {avg_ret:+.2f}%")
    print(f"  最大涨幅: {max(rets):+.2f}%")
    print(f"  最大跌幅: {min(rets):+.2f}%")
    print(f"  因子区分度: {hi_avg - lo_avg:+.2f}% (前{mid}名 {hi_avg:+.2f}% / 后{total-mid}名 {lo_avg:+.2f}%)")

    # 保存
    backtest_dir = os.path.join(DATA_ROOT, "backtest")
    os.makedirs(backtest_dir, exist_ok=True)

    # CSV
    csv_path = os.path.join(backtest_dir, f"backtest_{pick_date}_{eval_date}.csv")
    fields = list(rows[0].keys())
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for r in rows:
            w.writerow([r[k] for k in fields])

    # JSON 汇总
    summary = {
        "pick_date": pick_date, "eval_date": eval_date,
        "total": total, "wins": wins_count, "losses": total - wins_count,
        "win_rate": round(wins_count / total * 100, 1) if total else 0,
        "avg_return": round(avg_ret, 2),
        "max_gain": round(max(rets), 2), "max_loss": round(min(rets), 2),
        "hi_avg_return": round(hi_avg, 2), "lo_avg_return": round(lo_avg, 2),
        "factor_edge": round(hi_avg - lo_avg, 2),
    }
    summary_path = os.path.join(backtest_dir, f"summary_{pick_date}_{eval_date}.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n  ✓ CSV:  {csv_path}")
    print(f"  ✓ JSON: {summary_path}")

    return rows


def main():
    pick_date = None
    eval_date = None
    picks_file = None

    for arg in sys.argv:
        if arg.startswith("--pick="):
            pick_date = arg.split("=")[1]
        if arg.startswith("--eval="):
            eval_date = arg.split("=")[1]
        if arg.startswith("--file="):
            picks_file = arg.split("=")[1]

    if pick_date is None:
        # 默认取最新数据
        dates = sorted([d for d in os.listdir(DATA_ROOT)
                       if os.path.isdir(os.path.join(DATA_ROOT, d)) and d.isdigit()],
                       reverse=True)
        if len(dates) >= 2:
            pick_date = dates[1]  # 倒数第二新的是上次选股日
            eval_date = dates[0]  # 最新的是验证日
        else:
            print("需要至少两个日期的数据才能回溯")
            sys.exit(1)

    run(pick_date, eval_date, picks_file)


if __name__ == "__main__":
    main()
