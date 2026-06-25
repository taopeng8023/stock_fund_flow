"""
盘中快照采集调度器 — 交易日 9:30-11:30 / 13:00-15:00 每5分钟采集一次

用法:
  python -m scheduler.collect_scheduler                     # 今天（前台运行）
  python -m scheduler.collect_scheduler --date=20260625     # 指定日期
  python -m scheduler.collect_scheduler --dry-run           # 试运行（只打印不采集）

调度规则:
  - 仅交易日执行（跳过周六日）
  - 上午 9:30-11:30，每5分钟（9:30, 9:35, ..., 11:30）
  - 下午 13:00-15:00，每5分钟（13:00, 13:05, ..., 15:00）
  - 开盘 9:30 和收盘 15:00 必采
  - 同分钟不重复采集
"""
import os
import sys
import subprocess
import time
import signal
from datetime import datetime, timezone, timedelta
from pathlib import Path

BJS_TZ = timezone(timedelta(hours=8))
PROJECT_ROOT = Path(__file__).parent.parent

# 上午时段
MORNING_START = (9, 30)
MORNING_END = (11, 35)   # 含 11:30
# 下午时段
AFTERNOON_START = (13, 0)
AFTERNOON_END = (15, 5)  # 含 15:00
# 采集间隔（分钟）
INTERVAL_MINUTES = 5

# 状态
_running = True
_collected = set()  # 已采集的时间点 "HHMM"


def _signal_handler(sig, frame):
    global _running
    print(f"\n  收到停止信号，等待当前采集完成...")
    _running = False


def _is_trading_day(dt: datetime) -> bool:
    """周六日跳过。"""
    return dt.weekday() < 5


def _is_in_session(dt: datetime) -> bool:
    """是否在交易时段内。"""
    t = (dt.hour, dt.minute)
    return (MORNING_START <= t < MORNING_END) or (AFTERNOON_START <= t < AFTERNOON_END)


def _is_collect_time(dt: datetime) -> bool:
    """是否应该采集：分钟数是5的倍数且在交易时段。"""
    if not _is_in_session(dt):
        return False
    return dt.minute % INTERVAL_MINUTES == 0


def _should_collect(dt: datetime) -> bool:
    """是否需要采集：时间点匹配 + 未采集过。"""
    key = dt.strftime("%H%M")
    if key in _collected:
        return False
    if not _is_collect_time(dt):
        return False
    return True


def _run_collect(date_str: str, ts: str) -> bool:
    """执行采集。"""
    try:
        cp = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "daily_pipeline" / "collect.py"),
             date_str],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=120,
        )
        ok = cp.returncode == 0
        if ok:
            # 统计采集结果
            lines = cp.stdout.strip().split("\n")
            summary = lines[-1] if lines else ""
            return True, summary[:80]
        else:
            err = cp.stderr[-100:] if cp.stderr else "未知错误"
            return False, err
    except subprocess.TimeoutExpired:
        return False, "采集超时"
    except Exception as e:
        return False, str(e)


def run(date_str: str = None, dry_run: bool = False):
    """主调度循环。"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    dt = datetime.strptime(date_str, "%Y%m%d")
    if not _is_trading_day(dt):
        print(f"  {date_str} 非交易日({dt.strftime('%A')}), 跳过")
        return

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    print(f"╔{'═'*58}╗")
    print(f"║  盘中快照采集调度器{'':>42s}║")
    print(f"║  日期: {date_str}  {dt.strftime('%A')}{'':>39s}║")
    print(f"║  时段: 9:30-11:30 / 13:00-15:00{'':>30s}║")
    print(f"║  频率: 每 {INTERVAL_MINUTES} 分钟{'':>46s}{'':>1s}║")
    if dry_run:
        print(f"║  ⚠ 试运行模式 (不实际采集){'':>38s}║")
    print(f"╚{'═'*58}╝")

    total_collected = 0
    total_failed = 0

    while _running:
        now = datetime.now(BJS_TZ)

        # 检查日期是否变化
        if now.strftime("%Y%m%d") != date_str:
            print(f"\n  日期已变为 {now.strftime('%Y%m%d')}，退出")
            break

        # 检查是否已过收盘时间
        if now.hour >= 15 and now.minute > 5:
            print(f"\n  已过收盘时间 15:00，今日采集结束")
            break

        # 等待进入交易时段
        if not _is_in_session(now):
            # 计算到下一个交易时段的等待时间
            if now.hour < 9 or (now.hour == 9 and now.minute < 30):
                wait_sec = ((9 - now.hour) * 3600 + (30 - now.minute) * 60 - now.second)
            elif now.hour < 13:
                wait_sec = ((13 - now.hour) * 3600 - now.minute * 60 - now.second)
            else:
                wait_sec = 30
            wait_sec = max(5, min(wait_sec, 3600))
            next_ts = (now + timedelta(seconds=wait_sec)).strftime("%H:%M:%S")
            if not dry_run:
                print(f"  ⏳ 等待开盘... 下次检查 {next_ts}")
            time.sleep(wait_sec)
            continue

        # 检查是否需要采集
        if _should_collect(now):
            ts = now.strftime("%H%M")
            ts_display = now.strftime("%H:%M:%S")
            _collected.add(ts)

            if dry_run:
                print(f"  [DRY] {ts_display} → 跳过(试运行)")
            else:
                print(f"  📡 {ts_display} 采集...", end=" ", flush=True)
                ok, msg = _run_collect(date_str, ts)
                if ok:
                    total_collected += 1
                    print(f"✅ {msg}" if msg else "✅")
                else:
                    total_failed += 1
                    print(f"❌ {msg}")

        # 等待到下一个检查点
        time.sleep(5)

    print(f"\n  今日完成: 成功 {total_collected} 次, 失败 {total_failed} 次")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="盘中快照采集调度器")
    parser.add_argument("--date", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(args.date, dry_run=args.dry_run)
