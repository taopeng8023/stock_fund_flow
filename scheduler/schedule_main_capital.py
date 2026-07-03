#!/usr/bin/env python3
"""
主力资金流向定时分析调度器 v2 — 交易日每6分钟，会话感知 + 数据新鲜度检测

会话阶段:
  早盘 9:30-11:30  每6分钟（对齐5分钟采集 + 60s缓冲）
  午休 11:30-13:00  暂停，等待下午开盘
  午盘 13:00-15:00  每6分钟
  收盘 15:00-15:30  最后一次分析后退出

数据新鲜度: 快照数未变 → 跳过分析，显示"数据未更新"
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
INTERVAL_SEC = 6 * 60
COLLECT_INTERVAL = 5 * 60  # 数据采集间隔

_running = True
_last_run_ts: float = 0
_last_snapshot_count: int = -1
_has_shown_lunch = False


def _signal_handler(sig, frame):
    global _running
    print(f"\n  ⏹ 停止信号，等待当前任务...")
    _running = False


def _is_trading_day(dt: datetime) -> bool:
    return dt.weekday() < 5


def _session_phase(dt: datetime) -> str:
    """返回当前会话阶段: morning | lunch | afternoon | closing | post"""
    t = (dt.hour, dt.minute)
    if t < (9, 30):
        return "pre"
    elif t < (11, 35):
        return "morning"
    elif t < (13, 0):
        return "lunch"
    elif t < (15, 1):
        return "afternoon"
    elif t < (15, 30):
        return "closing"
    else:
        return "post"


def _count_snapshots(date_str: str) -> int:
    """统计当前日期 fund_flow 快照数。"""
    intraday = PROJECT_ROOT / "research_data" / date_str / "intraday"
    if not intraday.exists():
        return 0
    return sum(1 for f in intraday.iterdir()
               if f.name.startswith("fund_flow_") and f.name.endswith(".csv"))


def _sleep_until(target: datetime, reason: str):
    """休眠到指定时间，每秒检查停止信号。"""
    global _has_shown_lunch
    if reason and reason != "lunch":
        print(f"  ⏳ {reason}，等待 {target.strftime('%H:%M')}...")
    elif reason == "lunch" and not _has_shown_lunch:
        print(f"\n  🔕 午休中 (11:30-13:00)，暂停分析")
        _has_shown_lunch = True

    while _running:
        now = datetime.now(BJS_TZ)
        if now >= target:
            return
        remaining = (target - now).total_seconds()
        sleep_sec = min(5, max(1, remaining))
        time.sleep(sleep_sec)


def _run_analysis(date_str: str) -> bool:
    """执行分析，返回是否成功。"""
    try:
        cp = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "analyze_main_capital.py"),
             f"--date={date_str}", "--brief"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=120,
        )
        if cp.stdout:
            print(cp.stdout)
        if cp.stderr:
            print(cp.stderr, file=sys.stderr)
        return cp.returncode == 0
    except subprocess.TimeoutExpired:
        print("  ⏱ 超时")
        return False
    except Exception as e:
        print(f"  ❌ {e}")
        return False


def _next_collect_time(now: datetime) -> datetime:
    """计算下一个数据采集时间点（:00, :05, :10, ...）。"""
    mins = now.minute
    next_mins = ((mins // COLLECT_INTERVAL) + 1) * COLLECT_INTERVAL
    if next_mins >= 60:
        return now.replace(minute=0, second=30, microsecond=0) + timedelta(hours=1)
    return now.replace(minute=next_mins, second=30, microsecond=0)


def run(dry_run: bool = False):
    global _last_run_ts, _last_snapshot_count, _has_shown_lunch

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    print("╔" + "═" * 55 + "╗")
    print(f"║  主力资金流向定时分析 v2{'':>32}║")
    print(f"║  间隔: 每{INTERVAL_SEC//60}分钟 | 会话感知 | 数据新鲜度检测{'':>16}║")
    if dry_run:
        print(f"║  ⚠ 试运行模式{'':>42}║")
    print("╚" + "═" * 55 + "╝")
    print()

    while _running:
        now = datetime.now(BJS_TZ)
        date_str = now.strftime("%Y%m%d")

        # 非交易日
        if not _is_trading_day(now):
            tomorrow = now.replace(hour=9, minute=30, second=0, microsecond=0) + timedelta(days=1)
            while not _is_trading_day(tomorrow):
                tomorrow += timedelta(days=1)
            wait = (tomorrow - now).total_seconds()
            if wait > 0:
                print(f"  {now.strftime('%m/%d %a')} 非交易日，休眠至 {tomorrow.strftime('%m/%d %H:%M')}")
                _sleep_until(tomorrow, "")
            continue

        phase = _session_phase(now)

        # 盘前：等到 9:30
        if phase == "pre":
            target = now.replace(hour=9, minute=30, second=0, microsecond=0)
            _sleep_until(target, "盘前")
            _has_shown_lunch = False  # 重置午休标志
            continue

        # 午休：等到 13:00
        if phase == "lunch":
            target = now.replace(hour=13, minute=0, second=0, microsecond=0)
            _sleep_until(target, "lunch")
            _last_snapshot_count = -1  # 重置，下午强制首次分析
            _has_shown_lunch = False
            continue

        # 收盘后：最后一次分析后退出
        if phase == "post":
            if _last_snapshot_count > 0:
                print(f"\n  🔚 收盘，最终分析...")
                _run_analysis(date_str)
            print(f"  今日结束，退出。")
            break

        # 盘中（morning / afternoon / closing）
        # 检查是否到执行时间
        elapsed = now.timestamp() - _last_run_ts
        if elapsed < INTERVAL_SEC:
            sleep_sec = INTERVAL_SEC - elapsed
            time.sleep(min(sleep_sec, 30))
            continue

        # 检查数据新鲜度
        current_count = _count_snapshots(date_str)
        data_fresh = current_count > _last_snapshot_count
        data_first = _last_snapshot_count < 0

        if not data_fresh and not data_first:
            # 数据未更新，但仍在交易时段 → 等待下次数据采集后再分析
            next_coll = _next_collect_time(now)
            wait_sec = min(120, (next_coll - now).total_seconds())
            if wait_sec > 10:
                print(f"  ⏳ {now.strftime('%H:%M:%S')} 数据未更新({current_count}帧)，等下次采集...")
                _sleep_until(next_coll, "")
                continue

        # 执行分析
        _last_run_ts = now.timestamp()
        _last_snapshot_count = current_count
        ts_display = now.strftime('%H:%M:%S')

        if dry_run:
            print(f"  [DRY] {ts_display} → 跳过 ({current_count}帧)")
        else:
            print(f"\n{'─'*60}")
            phase_label = {"morning": "早盘", "afternoon": "午盘", "closing": "尾盘"}.get(phase, "")
            print(f"  📊 {ts_display} {phase_label} ({current_count}帧)")
            print(f"{'─'*60}")
            ok = _run_analysis(date_str)
            print(f"{'─'*60}")
            print(f"  {'✅' if ok else '❌'} {ts_display}")

        time.sleep(5)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="主力资金流向定时分析调度器 v2")
    parser.add_argument("--daemon", action="store_true", help="后台运行")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.daemon:
        pid = os.fork()
        if pid > 0:
            print(f"  后台守护进程 PID: {pid}")
            sys.exit(0)
        log_dir = PROJECT_ROOT / "logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / "scheduler_main_capital.log"
        sys.stdout = open(log_file, "a")
        sys.stderr = sys.stdout

    run(dry_run=args.dry_run)
