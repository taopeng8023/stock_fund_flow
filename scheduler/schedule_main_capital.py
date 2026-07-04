#!/usr/bin/env python3
"""
主力资金流向定时分析调度器 v3 — 交易日每6分钟，会话感知 + 数据新鲜度检测
+ K线形态选股引擎集成

会话阶段:
  早盘 9:30-11:30  每6分钟（对齐5分钟采集 + 60s缓冲）
  午休 11:30-13:00  暂停，等待下午开盘
  午盘 13:00-15:00  每6分钟
  收盘 15:00-15:30  最后一次分析 + 全量选股扫描后退出

数据新鲜度: 快照数未变 → 跳过分析，显示"数据未更新"
选股引擎: 盘中轻量扫描 → 尾盘全量交叉验证 → 企微推送高置信信号
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
SCANNER_INTERVAL = 30 * 60  # 选股扫描间隔（盘中每30分钟）

_running = True
_last_run_ts: float = 0
_last_snapshot_count: int = -1
_last_scanner_ts: float = 0
_last_scanner_picks: list = []  # 上次扫描结果缓存
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
    """执行资金流分析，返回是否成功。"""
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


def _run_signal_scanner(date_str: str, phase: str) -> bool:
    """运行 K线形态选股扫描。

    Args:
        date_str: 扫描日期
        phase: morning/afternoon/closing — 盘中轻量，尾盘全量

    Returns:
        是否成功
    """
    global _last_scanner_ts, _last_scanner_picks

    scanner_path = PROJECT_ROOT / "baostock_data" / "analysis" / "signal_scanner.py"
    if not scanner_path.exists():
        print("  ⚠ 选股扫描器不存在，跳过")
        return False

    # 盘中：轻量扫描
    if phase in ("morning", "afternoon"):
        top_n, min_consensus, lookback = 10, 1, 3
        label = "盘中快速"
    elif phase == "closing":
        top_n, min_consensus, lookback = 30, 2, 5
        label = "尾盘全量"
    else:
        top_n, min_consensus, lookback = 50, 1, 5
        label = "收盘后"

    print(f"  🔍 选股扫描 ({label}): ≥{min_consensus}引擎, 回溯{lookback}天...")

    try:
        cp = subprocess.run(
            [sys.executable, str(scanner_path),
             f"--date={date_str}",
             f"--top={top_n}",
             f"--min-consensus={min_consensus}",
             f"--lookback={lookback}",
             "--save"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=300,
        )
        if cp.stdout:
            # 只打印关键行，避免刷屏
            for line in cp.stdout.split("\n"):
                if any(kw in line for kw in
                       ("🎯", "扫描日期", "扫描完成", "命中", "💾", "信号详情")):
                    print(f"  {line.strip()}")
                elif line.strip().startswith(("1.", "2.", "3.", "4.", "5.")):
                    parts = line.strip().split()
                    if len(parts) >= 4:
                        print(f"  {line.strip()[:80]}")
        if cp.stderr and "Traceback" in cp.stderr:
            print(cp.stderr[:500], file=sys.stderr)

        _last_scanner_ts = time.time()
        return cp.returncode == 0
    except subprocess.TimeoutExpired:
        print("  ⏱ 选股扫描超时")
        return False
    except Exception as e:
        print(f"  ❌ 扫描器错误: {e}")
        return False


def _notify_scanner_picks(date_str: str, phase: str):
    """企微推送高置信选股信号。仅在尾盘/收盘时推送。"""
    if phase not in ("closing", "post"):
        return

    # 读取最新扫描结果
    results_dir = PROJECT_ROOT / "baostock_data" / "analysis" / "results"
    if not results_dir.exists():
        return

    import json
    import glob as _glob
    files = sorted(
        _glob.glob(str(results_dir / "signal_scanner_*.json")), reverse=True
    )
    if not files:
        return

    try:
        with open(files[0], encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return

    candidates = data.get("candidates", [])
    if not candidates:
        return

    # 推送 Top 5 高置信 (score > 100)
    high_confidence = [c for c in candidates if c.get("score", 0) > 100][:5]
    if not high_confidence:
        high_confidence = candidates[:3]

    try:
        from notify.wecom_sender import send_markdown
        lines = [
            f"## 🔍 选股信号 — {date_str}",
            f"> 多引擎交叉验证 | 形态×涨跌量价",
            "",
        ]
        for i, c in enumerate(high_confidence):
            lines.append(
                f"**{i+1}. {c['name']}** ({c['code']})  "
                f"得分: {c['score']:.0f} | WR: {c['best_wr']:.0f}%"
            )
            lines.append(
                f"> 形态: {', '.join(c.get('patterns', []))}  "
                f"量价: {', '.join(c.get('pv_signals', []))}"
            )
            lines.append("")

        send_markdown("\n".join(lines))
        print(f"  📤 企微推送: {len(high_confidence)} 只高置信信号")
    except ImportError:
        pass
    except Exception as e:
        print(f"  ⚠ 推送失败: {e}")


def _next_collect_time(now: datetime) -> datetime:
    """计算下一个数据采集时间点（:00, :05, :10, ...）。"""
    mins = now.minute
    next_mins = ((mins // COLLECT_INTERVAL) + 1) * COLLECT_INTERVAL
    if next_mins >= 60:
        return now.replace(minute=0, second=30, microsecond=0) + timedelta(hours=1)
    return now.replace(minute=next_mins, second=30, microsecond=0)


def run(dry_run: bool = False):
    global _last_run_ts, _last_snapshot_count, _last_scanner_ts, _has_shown_lunch

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    print("╔" + "═" * 60 + "╗")
    print(f"║  主力资金流向定时分析 v3 — K线选股引擎集成{'':>19}║")
    print(f"║  资金流: 每{INTERVAL_SEC//60}分钟 | 选股: 每{SCANNER_INTERVAL//60}分钟 | 会话感知{'':>8}║")
    if dry_run:
        print(f"║  ⚠ 试运行模式{'':>46}║")
    print("╚" + "═" * 60 + "╝")
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
            _has_shown_lunch = False
            continue

        # 午休：等到 13:00
        if phase == "lunch":
            target = now.replace(hour=13, minute=0, second=0, microsecond=0)
            _sleep_until(target, "lunch")
            _last_snapshot_count = -1
            _last_scanner_ts = 0  # 下午重新触发扫描
            _has_shown_lunch = False
            continue

        # 收盘后：最后一次分析 + 选股扫描后退出
        if phase == "post":
            if _last_snapshot_count > 0:
                print(f"\n  🔚 收盘，最终分析...")
                _run_analysis(date_str)
            print(f"\n  🔍 收盘选股扫描...")
            _run_signal_scanner(date_str, "post")
            _notify_scanner_picks(date_str, "post")
            print(f"  今日结束，退出。")
            break

        # 盘中（morning / afternoon / closing）
        # ── 选股扫描（独立于资金流分析频率）──
        scanner_elapsed = now.timestamp() - _last_scanner_ts
        if scanner_elapsed >= SCANNER_INTERVAL or _last_scanner_ts == 0:
            if phase == "closing":
                # 尾盘：全量严格扫描
                _run_signal_scanner(date_str, "closing")
                _notify_scanner_picks(date_str, "closing")
            elif phase in ("morning", "afternoon"):
                # 盘中：轻量扫描
                _run_signal_scanner(date_str, phase)
            _last_scanner_ts = now.timestamp()

        # ── 资金流分析 ──
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
    parser = argparse.ArgumentParser(description="主力资金流向定时分析调度器 v3")
    parser.add_argument("--daemon", action="store_true", help="后台运行")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-scanner", action="store_true", help="禁用选股扫描")
    args = parser.parse_args()

    if args.no_scanner:
        # 全局禁用扫描器
        SCANNER_INTERVAL = 999999
        print("  ⚠ 选股扫描已禁用")

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
