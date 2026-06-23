"""
每日研究 Pipeline — CLI 入口
用法:
  python -m daily_pipeline.main --mode=intraday              盘中采集
  python -m daily_pipeline.main --mode=score --date=20260622  收盘评分
  python -m daily_pipeline.main --mode=backtest --date=20260622  次日回测
  python -m daily_pipeline.main --mode=optimize               累积分析
  python -m daily_pipeline.main --mode=full --date=20260622   一键: 评分+回测
"""
import sys
import os
from datetime import datetime, timezone, timedelta

BJS_TZ = timezone(timedelta(hours=8))


def main():
    mode = "full"
    date_str = None
    eval_date = None
    snapshot = None

    for arg in sys.argv:
        if arg.startswith("--mode="):
            mode = arg.split("=")[1]
        if arg.startswith("--date="):
            date_str = arg.split("=")[1]
        if arg.startswith("--eval="):
            eval_date = arg.split("=")[1]
        if arg.startswith("--snapshot="):
            snapshot = arg.split("=")[1]

    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    if mode == "intraday":
        from daily_pipeline.collect import collect_intraday_snapshot
        collect_intraday_snapshot(date_str)

    elif mode == "score":
        from daily_pipeline.score import score_all_stocks
        score_all_stocks(date_str, snapshot_cutoff=snapshot)

    elif mode == "sectors":
        from daily_pipeline.score import score_sectors
        score_sectors(date_str, snapshot_cutoff=snapshot)

    elif mode == "backtest":
        from daily_pipeline.backtest import run_daily_backtest
        run_daily_backtest(date_str, eval_date)

    elif mode == "optimize":
        from daily_pipeline.optimize import analyze
        analyze()

    elif mode == "full":
        # 评分 + 回测（需要前一天有评分数据）
        print(f"\n{'='*60}")
        print(f"  Daily Pipeline — {date_str}")
        print(f"{'='*60}")

        # 1. 评分
        from daily_pipeline.score import score_all_stocks
        scores = score_all_stocks(date_str)
        if not scores:
            print("评分失败，终止")
            return

        # 2. 回测（需要后一天数据，这里尝试回测前一天）
        # 找最近的前一个有评分的日期
        research_root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data"
        )
        date_dirs = sorted([
            d for d in os.listdir(research_root)
            if os.path.isdir(os.path.join(research_root, d)) and d.isdigit() and d < date_str
        ], reverse=True)
        if date_dirs:
            prev_date = date_dirs[0]
            scores_path = os.path.join(research_root, prev_date, "scores.csv")
            if os.path.exists(scores_path):
                print(f"\n  检测到前次评分 {prev_date}，运行回测...")
                from daily_pipeline.backtest import run_daily_backtest
                run_daily_backtest(prev_date, date_str)

    else:
        print(f"未知 mode: {mode}")
        print("可用: intraday | score | backtest | optimize | full")


if __name__ == "__main__":
    main()
