"""
全量数据采集 — 模块化入口
用法:
  python -m data_collector.main                    采集当天全量数据
  python -m data_collector.main --date=20260617    指定日期
  python -m data_collector.main --retry            失败自动重试
"""
import sys
from datetime import datetime
from data_collector.fetchers.base import today_str, BJS_TZ
from data_collector.pipeline import run_pipeline, run_with_retry
from data_collector.output import print_summary


def main():
    date_str = today_str()
    retry = False

    for arg in sys.argv:
        if arg.startswith("--date="):
            date_str = arg.split("=")[1]
        if arg == "--retry":
            retry = True

    print(f"═══ 采集 {date_str} 全量数据 ═══")

    if retry:
        results, aborted = run_with_retry(date_str, max_retries=2)
    else:
        results, aborted = run_pipeline(date_str)

    print_summary(results, date_str, aborted)

    if aborted:
        sys.exit(1)


if __name__ == "__main__":
    main()
