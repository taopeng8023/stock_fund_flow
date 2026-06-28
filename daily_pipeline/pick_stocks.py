#!/usr/bin/env python3
"""
选股脚本 — 6万账户、5只持仓、仅主板
用法:
  python daily_pipeline/pick_stocks.py              # 当天
  python daily_pipeline/pick_stocks.py 20260626     # 指定日期
  python daily_pipeline/pick_stocks.py --dry-run    # 只看不保存
"""

import csv
import os
import sys
import subprocess
from datetime import datetime, timezone, timedelta

BJS_TZ = timezone(timedelta(hours=8))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH_ROOT = os.path.join(PROJECT_ROOT, "research_data")

# 配置
ACCOUNT = 60000
POSITIONS = 5
TOP_N = 10

# 主板前缀
MAIN_BOARD = ("600", "601", "603", "605", "000", "001", "002", "003")


def find_nearest_trading_day():
    """找到最近的交易日（周一至周五，排除今天如果是周末）"""
    today = datetime.now(BJS_TZ).date()
    # 周末回退到周五
    while today.weekday() >= 5:  # 5=Sat, 6=Sun
        today -= timedelta(days=1)
    return today.strftime("%Y%m%d")


def run(date_str=None):
    if date_str is None:
        date_str = find_nearest_trading_day()

    print("=" * 70)
    print("  A股隔夜选股 — %s" % date_str)
    print("  账户: %s元 | 持仓: %s只 | 仅主板" % ("{:,}".format(ACCOUNT), POSITIONS))
    print("=" * 70)
    print()

    # 检查数据
    scores_path = os.path.join(RESEARCH_ROOT, date_str, "scores.csv")
    if not os.path.exists(scores_path):
        print("[ERROR] %s 无评分数据，请先运行 score.py" % date_str)
        return None

    # 运行 filter_overnight
    cmd = [
        sys.executable,
        os.path.join(PROJECT_ROOT, "daily_pipeline", "filter_overnight.py"),
        date_str,
        "--account=%s" % ACCOUNT,
        "--positions=%s" % POSITIONS,
        "--main-board",
        "--top=%s" % TOP_N,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    print(result.stdout)

    if result.returncode != 0:
        print("[ERROR] 脚本执行失败")
        if result.stderr:
            print(result.stderr)
        return None

    # 读取选股结果
    picks_path = os.path.join(RESEARCH_ROOT, date_str, "overnight_picks.csv")
    if not os.path.exists(picks_path):
        print("[WARN] 未生成选股文件，可能当天无符合条件的标的")
        return None

    picks = []
    with open(picks_path, encoding="utf-8-sig") as f:
        picks = list(csv.DictReader(f))

    if not picks:
        print("[INFO] 无符合条件的标的")
        return None

    # 汇总展示
    print()
    print("=" * 70)
    print("  最终推荐 (%s只)" % len(picks))
    print("=" * 70)

    total_cost = 0
    for i, p in enumerate(picks):
        code = p["代码"]
        name = p["名称"]
        price = float(p["最新价"])
        tier = p.get("级别", "?").split(":")[0]
        chg = float(p.get("涨跌幅", 0))
        industry = p.get("行业", "?")
        score = float(p.get("综合得分", 0))
        cap_score = float(p.get("资金得分", 0))

        cost_1lot = price * 100
        max_lots = int(ACCOUNT * 0.8 / POSITIONS / cost_1lot)
        buy_lots = min(max_lots, int(ACCOUNT * 0.15 / cost_1lot))  # 单只不超15%仓位
        if buy_lots < 1:
            buy_lots = 1
        buy_cost = buy_lots * cost_1lot
        total_cost += buy_cost

        # 板块
        if code.startswith("60"):
            board = "沪主板"
        elif code.startswith("00"):
            board = "深主板"
        else:
            board = "?"

        print("  %2d. %s %-8s %s | ¥%7.2f | %+5.1f%% | %s | %s" % (
            i + 1, code, name, board, price, chg, industry, tier))
        print("      综合=%.3f 资金=%.3f | 买%dx100=%s元" % (
            score, cap_score, buy_lots, "{:,}".format(buy_cost)))

    print()
    print("  [总计] 约 %s 元 (账户 %s 元, 占比 %.0f%%)" % (
        "{:,}".format(total_cost),
        "{:,}".format(ACCOUNT),
        total_cost / ACCOUNT * 100,
    ))
    print("  [卖出] 次日 09:45-10:15 市价全部卖出")
    print()

    return picks


if __name__ == "__main__":
    date_arg = None
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            date_arg = a

    run(date_arg)
