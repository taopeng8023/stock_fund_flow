#!/usr/bin/env python3
"""
主力资金流向定时分析调度器 v5 — 信号驱动退出优化版
  交易日每6分钟 | 会话感知 | 数据新鲜度 | K线选股引擎集成

v5 信号驱动优化 (基于 buy_sell_backtest.py 1704只主板 1991-2026 3199笔):
  ┌──────────────────────────────────────────────────────────────┐
  │ 回测验证结论 (2026-07-06)                                      │
  │                                                              │
  │  信号驱动退出 > 固定持仓天数:                                    │
  │    3199笔中仅1笔触发安全兜底(0.03%), 信号驱动完全可行            │
  │    日线 WR 55.4% | 20%+命中 9.0% | 30%+ 6.4% | avg +4.64%   │
  │    盈亏比 4.43 | 夏普 2.44 | 持仓 9.8d                        │
  │                                                              │
  │  Top信号 (日线 20%+命中率):                                     │
  │    深跌35%+涨停+巨量+突破MA20   81.2% WR | 25.0% 20%+          │
  │    MA20上升+回踩MA5不破低      75.0% WR | 25.0% 20%+          │
  │    MA5金叉+三日连涨_逼60日高   69.7% WR | 13.4% 20%+          │
  │    MA5金叉+启明星_双确认       61.9% WR | 19.0% 20%+          │
  │    深跌25%+放量破高+站上MA10   58.7% WR | N=736 (最大样本)     │
  │                                                              │
  │  K线出场信号覆盖:                                               │
  │    射击之星(7.2%) | 跌破MA20(3.8%) | 量价背离(2.3%)            │
  │    看跌吞没(1.5%) | MA死叉(0.8%) | 总覆盖16.3%                │
  │                                                              │
  │  最优参数:                                                     │
  │    止盈: 30% | 止损: -6% | 移动止损: -8% | 信号驱动(兜底200日) │
  │    牛市: 止盈30% 止损5% 移动止损10%                             │
  │    熊市: 止盈15% 止损6% 移动止损6% 仅反转型信号                  │
  └──────────────────────────────────────────────────────────────┘

会话阶段:
  早盘 9:30-11:30  每6分钟（对齐5分钟采集 + 60s缓冲）
  午休 11:30-13:00  暂停
  午盘 13:00-15:00  每6分钟
  收盘 15:00-15:30  最后一次分析 + 全量选股扫描后退出

选股引擎:
  --trend: 启用趋势跟踪模式 (回测验证 WR更高, 20%+命中更高)
  --target: 止盈目标 (默认30%, 牛市30%, 熊市15%)
  盘中轻量 → 尾盘全量精选 → 企微推送高置信信号
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
COLLECT_INTERVAL = 5 * 60          # 数据采集间隔
SCANNER_INTERVAL = 30 * 60         # 选股扫描间隔（盘中每30分钟）
SCANNER_INTERVAL_BEAR = 60 * 60    # 熊市选股扫描间隔（放宽至1小时）

# ── 回测验证的最优参数 (v5 信号驱动) ──
TARGET_DEFAULT = 30                     # 默认止盈目标 (%)
TARGET_BULL = 30                        # 牛市止盈目标 (%)
TARGET_BEAR = 15                        # 熊市止盈目标 (%)
STOP_LOSS_DEFAULT = 0.06                # 默认止损
STOP_LOSS_BULL = 0.05                   # 牛市放宽止损
TRAILING_STOP_DEFAULT = 0.08            # 移动止损
TRAILING_STOP_BULL = 0.10               # 牛市放宽移动止损
MAX_HOLD_DAYS = 200                     # 信号驱动, 仅兜底
CONFIRM_STRICT = True                   # strict confirm 入场确认
MIN_HISTORY_DAYS = 200                  # 最少200交易日

# ── v5 信号优先级权重 (日线 3199笔回测 20%+命中率) ──
SIGNAL_PRIORITY = {
    # 高胜率核心信号
    "深跌35%":   0.81,  # N=16, WR 81.2%, 20%+命中 25.0%
    "回踩MA5":   0.75,  # N=8,  WR 75.0%, 20%+命中 25.0%
    "三日连涨":   0.70,  # N=119, WR 69.7%, 20%+命中 13.4%
    "MA5金叉":   0.62,  # N=21, WR 61.9%, 20%+命中 19.0%
    "启明星":     0.62,  # 同MA5金叉+启明星组合
    "二连阴缩":   0.60,  # N=37, WR 59.5%, 20%+命中 10.8%

    # 大样本验证信号
    "深跌25%":   0.59,  # N=736, WR 58.7%, 20%+命中 9.4%
    "MA20金叉":  0.56,  # N=358, WR 55.6%, 20%+命中 11.2%
    "三连阳":     0.52,  # N=448, WR 52.2%, 20%+命中 8.3%
    "突破MA20":  0.52,  # 同三连阳组合

    # 低样本高胜率 (监控用)
    "涨停":       0.81,  # 深跌35%组合一部分
    "缩量":       0.75,  # 回踩MA5组合一部分
    "放量":       0.59,  # 多信号共用
    "逼60日高":   0.70,  # 三日连涨组合一部分
}

# ── 出场信号优先级 (用于退出判断) ──
EXIT_SIGNAL_PRIORITY = {
    "MA5死叉MA10_跌破MA20_MA20走平": 1.0,   # 最高优先级: 趋势全面破坏
    "MA5死叉MA10_跌破MA20":          0.9,   # 趋势破坏
    "跌破MA20_收阴":                 0.8,   # 趋势破坏确认
    "看跌吞没_高位":                 0.7,   # 顶部反转
    "量价背离_价新高量缩":            0.6,   # 动能衰竭
    "射击之星_高位_收阴":            0.5,   # 顶部反转 (常见但容易误判)
    "连续缩量滞涨_动能衰竭":          0.5,   # 动能衰竭
    "急涨后大幅低开_获利了结":        0.4,   # 恐慌抛售
}

_running = True
_last_run_ts: float = 0
_last_snapshot_count: int = -1
_last_scanner_ts: float = 0
_has_shown_lunch = False

# 全局配置
_weekly_mode = False
_target_pct = TARGET_DEFAULT


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


def _detect_regime() -> str:
    """
    简易市场体制检测 — 基于当前日期判断大周期.
    回测验证 (v5 3199笔):
      牛市2019-20 TP 30%+ | 牛市2024H2 TP 30%+
      熊市2018 仅反转型信号 | 熊市2022 TP降至15%
    实际部署可用 market_diagnosis.py 替代.
    """
    now = datetime.now(BJS_TZ)
    year_month = now.year + now.month / 12.0

    if year_month < 2019.0:
        return "bear"       # 2018 熊市
    elif year_month < 2021.0:
        return "bull"       # 2019-2020 牛市
    elif year_month < 2022.5:
        return "range"      # 2021 震荡
    elif year_month < 2022.9:
        return "bear"       # 2022 熊市
    elif year_month < 2024.5:
        return "range"      # 2023-2024H1 震荡
    elif year_month < 2025.4:
        return "bull"       # 2024H2-2025Q1 牛市(924行情)
    elif year_month < 2025.9:
        return "range"      # 2025Q2-Q3 震荡
    elif year_month < 2026.2:
        return "bull"       # 2025H2-2026Q1 年末行情
    else:
        return "range"      # 2026Q2+ 震荡


def _get_regime_params() -> dict:
    """根据当前体制返回最优参数."""
    regime = _detect_regime()
    if regime == "bull":
        return {"target": TARGET_BULL, "label": "牛市",
                "scanner_interval": SCANNER_INTERVAL,
                "min_score": 80}
    elif regime == "bear":
        return {"target": TARGET_BEAR, "label": "熊市",
                "scanner_interval": SCANNER_INTERVAL_BEAR,
                "min_score": 120}
    else:
        return {"target": _target_pct, "label": "震荡",
                "scanner_interval": SCANNER_INTERVAL,
                "min_score": 100}


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
    """运行 K线形态选股扫描 — 回测优化版.

    Args:
        date_str: 扫描日期
        phase: morning/afternoon/closing/post
              盘中轻量 → 尾盘全量精选

    Returns:
        是否成功
    """
    global _last_scanner_ts

    scanner_path = PROJECT_ROOT / "baostock_data" / "analysis" / "picks.py"
    if not scanner_path.exists():
        print("  ⚠ 选股扫描器不存在，跳过")
        return False

    regime = _get_regime_params()

    # ── 根据回测结果优化的扫描策略 ──
    if phase in ("morning", "afternoon"):
        # 盘中: 轻量扫描, 高门槛
        scan_mode = ["scan", f"--date={date_str}", "--top=10"]
        label = f"盘中轻量({regime['label']})"
    elif phase == "closing":
        # 尾盘: 全量严格扫描, 回测验证的20%止盈目标
        if _weekly_mode:
            # 周线模式: 单信号入场, 更高命中率
            scan_mode = ["scan", f"--date={date_str}", "--top=30", "--strict"]
            label = f"尾盘周线精选(目标{regime['target']}%)"
        else:
            scan_mode = ["scan", f"--date={date_str}", "--top=30", "--strict"]
            label = f"尾盘日线精选(目标{regime['target']}%)"
    else:
        # 收盘后: 大范围扫描, 为次日准备
        scan_mode = ["scan", f"--date={date_str}", "--top=50"]
        label = "收盘后全量"

    print(f"  🔍 选股扫描 ({label})...")
    print(f"  📊 v5回测: 3199笔 | WR 55.4% | 20%+ 9.0% | 盈亏比 4.43")

    try:
        cp = subprocess.run(
            [sys.executable, str(scanner_path)] + scan_mode,
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=300,
        )
        if cp.stdout:
            for line in cp.stdout.split("\n"):
                if any(kw in line for kw in
                       ("🎯", "扫描日期", "扫描完成", "命中", "💾", "信号详情",
                        "WR", "胜率", "20%")):
                    print(f"  {line.strip()}")
                elif line.strip().startswith(("1.", "2.", "3.", "4.", "5.")):
                    parts = line.strip().split()
                    if len(parts) >= 4:
                        print(f"  {line.strip()[:100]}")
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
    """企微推送高置信选股信号 — 回测优化版.

    仅在尾盘/收盘时推送.
    优化: 按回测验证的信号优先级排序, 标注预期收益率.
    """
    if phase not in ("closing", "post"):
        return

    results_dir = PROJECT_ROOT / "baostock_data" / "analysis" / "results"
    if not results_dir.exists():
        return

    import json
    import glob as _glob
    files = sorted(
        _glob.glob(str(results_dir / "picks_scan_*.json")), reverse=True
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

    regime = _get_regime_params()

    # ── 回测优化: 按信号优先级加权排序 ──
    def _signal_weight(c):
        """根据回测验证的信号类型计算权重."""
        patterns = c.get("patterns", [])
        if not patterns:
            return c.get("score", 50)
        weights = []
        for p in patterns:
            for keyword, wr in SIGNAL_PRIORITY.items():
                if keyword in p:
                    weights.append(wr)
                    break
        # 平均信号权重 × 基础分数
        avg_wr = sum(weights) / len(weights) if weights else 0.30
        return c.get("score", 50) * (1 + avg_wr)

    # 按加权分数重排
    candidates.sort(key=_signal_weight, reverse=True)

    # 体制自适应门槛
    high_confidence = [c for c in candidates
                      if _signal_weight(c) > regime["min_score"]][:5]
    if not high_confidence:
        high_confidence = candidates[:3]

    try:
        from notify.wecom_sender import send_markdown
        lines = [
            f"## 🔍 选股信号 — {date_str}",
            f"> v5回测 | {regime['label']} | 止盈 +{regime['target']}% | 信号驱动",
            f"> 3199笔 WR 55.4% | 盈亏比 4.43 | 来源: buy_sell_backtest v5",
            "",
        ]
        for i, c in enumerate(high_confidence):
            weight = _signal_weight(c)
            if weight > 130:
                expected_ret = "30%+"
            elif weight > 110:
                expected_ret = "20%+"
            elif weight > 90:
                expected_ret = "15%+"
            else:
                expected_ret = "10%+"
            lines.append(
                f"**{i+1}. {c['name']}** ({c['code']})  "
                f"得分: {weight:.0f} | 预期: {expected_ret}"
            )
            patterns = c.get('patterns', [])
            if patterns:
                # 标注回测验证的命中率
                pattern_info = []
                for p in patterns[:3]:
                    for kw, wr in SIGNAL_PRIORITY.items():
                        if kw in p:
                            pattern_info.append(f"{p[:20]}({wr*100:.0f}%)")
                            break
                    else:
                        pattern_info.append(p[:20])
                lines.append(f"> 形态: {', '.join(pattern_info)}")
            pv = c.get('pv_signals', [])
            if pv:
                lines.append(f"> 量价: {', '.join(pv[:3])}")
            lines.append("")

        send_markdown("\n".join(lines))
        print(f"  📤 企微推送: {len(high_confidence)} 只高置信信号 (门槛{regime['min_score']})")
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

    regime = _get_regime_params()

    print("╔" + "═" * 62 + "╗")
    print(f"║  主力资金流向定时分析 v5 — 信号驱动退出版{'':>18}║")
    print(f"║  资金流: 每{INTERVAL_SEC//60}分钟 | 选股: 每{regime['scanner_interval']//60}分钟 | {regime['label']}{'':>13}║")
    print(f"║  📊 v5回测: 3199笔 | WR 55.4% | 盈亏比 4.43 | 夏普 2.44{'':>4}║")
    print(f"║  🎯 出入场: 信号驱动 | 止盈{_target_pct}% | K线出场 | 兜底200日{'':>6}║")
    if dry_run:
        print(f"║  ⚠ 试运行模式{'':>48}║")
    print("╚" + "═" + 62 + "╝")
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

        # 盘前
        if phase == "pre":
            target = now.replace(hour=9, minute=30, second=0, microsecond=0)
            _sleep_until(target, "盘前")
            _has_shown_lunch = False
            # 开盘时重新检测体制
            regime = _get_regime_params()
            continue

        # 午休
        if phase == "lunch":
            target = now.replace(hour=13, minute=0, second=0, microsecond=0)
            _sleep_until(target, "lunch")
            _last_snapshot_count = -1
            _last_scanner_ts = 0
            _has_shown_lunch = False
            continue

        # 收盘后
        if phase == "post":
            if _last_snapshot_count > 0:
                print(f"\n  🔚 收盘，最终分析...")
                _run_analysis(date_str)
            print(f"\n  🔍 收盘选股扫描 ({regime['label']} 目标{regime['target']}%)...")
            _run_signal_scanner(date_str, "post")
            _notify_scanner_picks(date_str, "post")
            print(f"  今日结束，退出。")
            break

        # ── 盘中 ──
        # 选股扫描
        scanner_elapsed = now.timestamp() - _last_scanner_ts
        scanner_due = scanner_elapsed >= regime["scanner_interval"] or _last_scanner_ts == 0

        if scanner_due:
            if phase == "closing":
                _run_signal_scanner(date_str, "closing")
                _notify_scanner_picks(date_str, "closing")
            elif phase in ("morning", "afternoon"):
                _run_signal_scanner(date_str, phase)
            _last_scanner_ts = now.timestamp()

        # 资金流分析
        elapsed = now.timestamp() - _last_run_ts
        if elapsed < INTERVAL_SEC:
            sleep_sec = INTERVAL_SEC - elapsed
            time.sleep(min(sleep_sec, 30))
            continue

        # 数据新鲜度
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
            print(f"  📊 {ts_display} {phase_label} ({current_count}帧) [{regime['label']}]")
            print(f"{'─'*60}")
            ok = _run_analysis(date_str)
            print(f"{'─'*60}")
            print(f"  {'✅' if ok else '❌'} {ts_display}")

        time.sleep(5)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="主力资金流向定时分析调度器 v5 — 信号驱动退出版",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
回测验证 (buy_sell_backtest.py v5, 1704只主板 1991-2026):
  日线 WR 55.4% | 20%+命中 9.0% | 30%+ 6.4% | 盈亏比 4.43
  信号驱动退出仅0.03%安全兜底 | K线出场信号覆盖16.3%
  Top信号: 深跌35%涨停 81.2%WR | 回踩MA5 75.0%WR

用法:
  python schedule_main_capital.py                  # 信号驱动模式(默认)
  python schedule_main_capital.py --target 30      # 牛市: 止盈30%
  python schedule_main_capital.py --target 15      # 熊市: 止盈15%
  python schedule_main_capital.py --daemon         # 后台守护进程
        """)
    parser.add_argument("--daemon", action="store_true", help="后台运行")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-scanner", action="store_true", help="禁用选股扫描")
    parser.add_argument("--weekly", action="store_true",
                       help="周线重采样模式 (保留兼容)")
    parser.add_argument("--target", type=int, default=TARGET_DEFAULT,
                       help=f"止盈目标%% (默认{TARGET_DEFAULT}%%, 牛市30%%, 熊市15%%)")
    args = parser.parse_args()

    # 全局配置
    _weekly_mode = args.weekly
    _target_pct = args.target

    if args.no_scanner:
        SCANNER_INTERVAL = 999999
        SCANNER_INTERVAL_BEAR = 999999
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
