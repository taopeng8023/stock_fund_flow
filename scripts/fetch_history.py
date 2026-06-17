"""
历史数据回填 — 补全 fund_flow.json 等核心数据的历史交易日记录
用法:
  python scripts/fetch_history.py --start=20260501 --end=20260617
  python scripts/fetch_history.py --days=60
  python scripts/fetch_history.py --dry-run
"""
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fetchers.base import DATA_ROOT, BJS_TZ

TRADING_CALENDAR_HOLIDAYS_2026 = {
    # 元旦
    "20260101", "20260102",
    # 春节 (2026-02-17 除夕)
    "20260216", "20260217", "20260218", "20260219", "20260220",
    # 清明节
    "20260405", "20260406",
    # 劳动节
    "20260501", "20260502", "20260503", "20260504", "20260505",
    # 端午节
    "20260619", "20260620", "20260621",
    # 中秋节
    "20260925",
    # 国庆节
    "20261001", "20261002", "20261005", "20261006", "20261007", "20261008",
}


def is_trading_day(date_str):
    """判断是否为 A 股交易日（周一到周五 + 排除节假日）"""
    d = datetime.strptime(date_str, "%Y%m%d")
    if d.weekday() >= 5:  # 周六日
        return False
    if date_str in TRADING_CALENDAR_HOLIDAYS_2026:
        return False
    return True


def get_trading_days(start_date, end_date):
    """获取日期范围内的所有交易日"""
    d = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")
    days = []
    while d <= end:
        ds = d.strftime("%Y%m%d")
        if is_trading_day(ds):
            days.append(ds)
        d += timedelta(days=1)
    return days


def needs_fetch(date_str):
    """检查指定日期是否需要回填（fund_flow.json 缺失或为空）"""
    path = os.path.join(DATA_ROOT, date_str, "fund_flow.json")
    if not os.path.exists(path):
        return True, "文件不存在"
    import json
    with open(path) as f:
        data = json.load(f)
    if not data or len(data) < 1000:
        return True, f"数据量不足({len(data) if data else 0}条)"
    return False, ""


def main():
    start_date = None
    end_date = None
    days_back = None
    dry_run = "--dry-run" in sys.argv

    for arg in sys.argv:
        if arg.startswith("--start="):
            start_date = arg.split("=")[1]
        if arg.startswith("--end="):
            end_date = arg.split("=")[1]
        if arg.startswith("--days="):
            days_back = int(arg.split("=")[1])

    # 确定日期范围
    if start_date is None and days_back:
        end_date = datetime.now(BJS_TZ).strftime("%Y%m%d")
        d = datetime.now(BJS_TZ) - timedelta(days=days_back * 2)
        start_date = d.strftime("%Y%m%d")
    elif start_date is None:
        end_date = datetime.now(BJS_TZ).strftime("%Y%m%d")
        start_date = end_date  # 默认只回填今天
    if end_date is None:
        end_date = start_date

    trading_days = get_trading_days(start_date, end_date)

    # 筛选需要回填的日期
    to_fetch = []
    for ds in trading_days:
        missing, reason = needs_fetch(ds)
        if missing:
            to_fetch.append((ds, reason))

    print(f"日期范围: {start_date} ~ {end_date}")
    print(f"交易日: {len(trading_days)} 天")
    print(f"需回填: {len(to_fetch)} 天")
    print()

    if dry_run:
        print("=== 需回填日期（--dry-run 模式，不执行）===")
        for ds, reason in to_fetch:
            print(f"  {ds}: {reason}")
        return

    if not to_fetch:
        print("无需回填，数据完整 ✓")
        return

    # 回填
    from fetchers import fund_flow
    success = 0
    failed = []

    for i, (ds, reason) in enumerate(to_fetch, 1):
        print(f"[{i}/{len(to_fetch)}] {ds} ({reason})...", end=" ", flush=True)
        try:
            rows = fund_flow.fetch(ds)
            if rows:
                success += 1
                print(f"✓ {len(rows)} 条")
            else:
                failed.append((ds, "API 无数据"))
                print("✗ 无数据")
        except Exception as e:
            failed.append((ds, str(e)[:80]))
            print(f"✗ {e}")

    # 汇总
    print(f"\n{'=' * 50}")
    print(f"回填完成: 成功 {success}/{len(to_fetch)}")
    if failed:
        print(f"失败 {len(failed)} 天:")
        for ds, err in failed:
            print(f"  {ds}: {err}")


if __name__ == "__main__":
    main()
