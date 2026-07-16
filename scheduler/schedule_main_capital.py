#!/usr/bin/env python3
"""
主力资金流向定时分析调度器 v5 final — 5122只全量回测优化版
  交易日每6分钟 | 会话感知 | 数据新鲜度 | K线选股引擎集成

v5 final 回测验证 (buy_sell_backtest.py 5122只 1991-2026 7544笔):
  ┌──────────────────────────────────────────────────────────────┐
  │ 回测验证结论 (2026-07-07)                                      │
  │                                                              │
  │  5122只全量 | 7544笔 | WR 48.2% | avg +5.37% | 止盈率12.0%   │
  │  盈亏比 3.65 | 夏普 2.0 | 持仓 12.9d | 峰值 +16.15%         │
  │                                                              │
  │  买入策略 (3层过滤):                                           │
  │    ① 量比>1 (缩量WR仅35.7%) → ② 双信号互确认(3日内≥2)        │
  │    ③ 三重确认优先 (MA金叉+连涨+连阳 20%+命中32%)              │
  │                                                              │
  │  卖出策略 (K线信号优先):                                        │
  │    量价背离 +15.98% > 急涨低开 +14.60% > 射击之星 +12.48%     │
  │    > 看跌吞没 +11.99% > 跌破MA20 +7.73% > 移动止损 +3.05%    │
  │                                                              │
  │  最优参数 (体制自适应):                                         │
  │    🐂牛市: 止盈30% 止损5% 移损10% | 全信号 三重确认优先        │
  │    📊震荡: 止盈25% 止损6% 移损8%  | 双确认 strict entry       │
  │    🐻熊市: 止盈15% 止损6% 移损6%  | 仅反转型信号(深跌/启明星)  │
  │                                                              │
  │  核心信号权重 (7544笔验证):                                     │
  │    深跌35%涨停 0.81 | MA金叉+连涨+连阳 0.70 | MA金叉+启明星 0.62│
  │    深跌25%放量 0.59 | MA金叉MA20 0.56 | 三连阳 0.52            │
  └──────────────────────────────────────────────────────────────┘

会话阶段:
  早盘 9:30-11:30  每6分钟（对齐5分钟采集 + 60s缓冲）
  午休 11:30-13:00  暂停
  午盘 13:00-15:00  每6分钟
  收盘 15:00-15:30  最后一次分析 + 全量选股扫描后退出

选股引擎:
  3层过滤: 量比>1 → 双信号确认 → 体制适配
  卖出优先: K线出场信号 > 移动止损 > 硬止损
  盘中轻量 → 尾盘全量精选 → 企微推送高置信信号

主力占比信号 (v6 forward验证 — 20日对 106,947条):
  条件: 主力占比 Top 1% | 等权买入 | 持有1日
  验证: 夏普 2.79 | 日胜率 78.9% | 累积 +27.82% | 最大回撤 -2.94%
  增强: 跳过周五(避免跨周末缺口) 夏普→3.50 | 累积→+30.29%
  逻辑: 机构尾盘高度控盘 = 次日继续上涨概率 55% | 均超额 +151bp
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

# ── 回测验证的最优参数 (v5 final 5122只) ──
TARGET_DEFAULT = 30                     # 默认止盈目标 (%)
TARGET_BULL = 30                        # 牛市止盈目标 (%)
TARGET_RANGE = 25                       # 震荡止盈目标 (%)
TARGET_BEAR = 15                        # 熊市止盈目标 (%)
STOP_LOSS_DEFAULT = 0.06                # 默认止损
STOP_LOSS_BULL = 0.05                   # 牛市放宽止损
STOP_LOSS_BEAR = 0.06                   # 熊市止损
TRAILING_STOP_DEFAULT = 0.08            # 移动止损
TRAILING_STOP_BULL = 0.10               # 牛市放宽移动止损
TRAILING_STOP_BEAR = 0.06               # 熊市移动止损
MAX_HOLD_DAYS = 200                     # 信号驱动, 仅兜底
CONFIRM_STRICT = True                   # strict confirm 入场确认
MIN_HISTORY_DAYS = 200                  # 最少200交易日
MIN_VOLUME_RATIO = 1.0                  # 入场最小量比 (缩量<1胜率仅35.7%)
OPTIMAL_VOLUME_RATIO = 2.0              # 最优量比 (暴量2-5x胜率58.7%)

# ── v5 final 信号优先级权重 (5122只 7544笔回测) ──
SIGNAL_PRIORITY = {
    # 三重确认 (最高20%+命中率)
    "三重确认":   0.85,  # MA金叉+连涨+连阳 20%+命中32%

    # 高胜率核心信号
    "深跌35%":   0.81,  # N=40, WR 50.0%, 20%+命中 27.5%
    "回踩MA5":   0.75,  # N=24, WR 50.0%, 20%+命中 16.7%
    "三日连涨":   0.70,  # N=289, WR 51.9%, 20%+命中 20.4%
    "MA5金叉":   0.62,  # N=38, WR 57.9%, 20%+命中 31.6% (启明星组合)
    "启明星":     0.62,
    "二连阴缩":   0.60,  # N=88, WR 42.0%, 20%+命中 20.5%
    "深跌25%":   0.59,  # N=1625, WR 51.3%, 20%+命中 15.0%
    "MA20金叉":  0.56,  # N=231, WR 46.8%, 20%+命中 21.2%
    "三连阳":     0.52,  # N=985, WR 45.4%, 20%+命中 16.5%
    "突破MA20":  0.52,

    # 低样本高胜率
    "涨停":       0.81, "缩量": 0.75, "放量": 0.59, "逼60日高": 0.70,
}

# ── 反转型信号 (熊市可用) ──
BEAR_SAFE_SIGNALS = {"深跌35%", "深跌25%", "启明星", "双针探底", "反包", "涨停"}

# ── 出场信号优先级 (用于退出判断) ──
EXIT_SIGNAL_PRIORITY = {
    "量价背离_价新高量缩":            1.0,   # 🏆 最佳: +15.98% 均值
    "看跌吞没_高位":                 0.9,   # +11.99% 均值
    "MA5死叉MA10_跌破MA20_MA20走平": 0.9,   # 趋势全面破坏
    "MA5死叉MA10_跌破MA20":          0.85,
    "急涨后大幅低开_获利了结":        0.8,   # +14.60% 但样本少
    "跌破MA20_收阴":                 0.7,   # 最常用趋势信号
    "射击之星_高位_收阴":            0.6,   # +12.48% 但容易误判
    "连续缩量滞涨_动能衰竭":          0.5,
}


# ── 买入策略辅助函数 ──
def evaluate_entry_signal(patterns: list[str], volume_ratio: float,
                          regime: str = "range") -> dict:
    """评估入场信号质量, 返回评分和策略建议.

    Returns:
        {"score": int, "level": str, "action": str, "target": int,
         "stop_loss": float, "trailing": float, "expected": str}
    """
    score = 0
    signal_count = len(patterns)
    triple = signal_count >= 3

    # 1. 信号强度评分
    for p in patterns:
        for kw, weight in SIGNAL_PRIORITY.items():
            if kw in p:
                score += weight * 100
                break

    # 2. 三重确认加成
    if triple:
        score += 20  # 额外加分

    # 3. 量比评分
    if volume_ratio >= OPTIMAL_VOLUME_RATIO:
        score += 15   # 暴量最优
    elif volume_ratio >= MIN_VOLUME_RATIO:
        score += 5    # 正常放量
    else:
        score -= 20   # 缩量惩罚

    # 4. 体制适配
    if regime == "bear":
        # 熊市: 仅反转型信号
        has_reversal = any(
            any(kw in p for kw in BEAR_SAFE_SIGNALS) for p in patterns
        )
        if not has_reversal:
            return {"score": 0, "level": "skip", "action": "熊市非反转型信号,跳过",
                    "target": 0, "stop_loss": 0, "trailing": 0, "expected": "N/A"}
        target = TARGET_BEAR
        sl = STOP_LOSS_BEAR
        trail = TRAILING_STOP_BEAR
        expected = "10%+"
    elif regime == "bull":
        target = TARGET_BULL
        sl = STOP_LOSS_BULL
        trail = TRAILING_STOP_BULL
        expected = "30%+" if score >= 120 else ("20%+" if score >= 100 else "15%+")
    else:
        target = TARGET_RANGE
        sl = STOP_LOSS_DEFAULT
        trail = TRAILING_STOP_DEFAULT
        expected = "20%+" if score >= 120 else ("15%+" if score >= 100 else "10%+")

    # 5. 入场决策
    if score >= 130:
        level = "strong_buy"
        action = f"强烈买入 | 持有21-60日 | 等待K线出场信号"
    elif score >= 110:
        level = "buy"
        action = f"买入 | strict confirm | 持有至信号出场"
    elif score >= 90:
        level = "watch"
        action = f"观察 | 等待更多确认信号"
    else:
        level = "skip"
        action = f"信号不足或量比不够,跳过"

    return {
        "score": int(score), "level": level, "action": action,
        "target": target, "stop_loss": round(sl * 100, 1),
        "trailing": round(trail * 100, 1), "expected": expected,
    }


def get_exit_strategy(regime: str = "range") -> dict:
    """基于回测结果的最优出场策略."""
    return {
        "priority": [
            "量价背离 (价新高量缩) → 最优 +15.98%",
            "急涨后大幅低开 → +14.60%",
            "射击之星高位收阴 → +12.48%",
            "看跌吞没高位 → +11.99%",
            "MA5死叉+跌破MA20 → +8.01%",
            "跌破MA20收阴 → +7.73%",
        ],
        "mechanical": [
            f"移动止损 {'10%' if regime == 'bull' else '8%' if regime == 'range' else '6%'} (从峰值回撤)",
            f"硬止损 {'5%' if regime == 'bull' else '6%'} (截断亏损)",
        ],
        "note": "K线出场信号优先于机械止损, 持股21-60天最优, 不要过早止盈",
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
        return {"regime": "bull", "target": TARGET_BULL, "label": "牛市 🐂",
                "stop_loss": STOP_LOSS_BULL, "trailing": TRAILING_STOP_BULL,
                "scanner_interval": SCANNER_INTERVAL, "min_score": 100}
    elif regime == "bear":
        return {"regime": "bear", "target": TARGET_BEAR, "label": "熊市 🐻",
                "stop_loss": STOP_LOSS_BEAR, "trailing": TRAILING_STOP_BEAR,
                "scanner_interval": SCANNER_INTERVAL_BEAR, "min_score": 110}
    else:
        return {"regime": "range", "target": TARGET_RANGE, "label": "震荡 📊",
                "stop_loss": STOP_LOSS_DEFAULT, "trailing": TRAILING_STOP_DEFAULT,
                "scanner_interval": SCANNER_INTERVAL, "min_score": 100}


def _count_snapshots(date_str: str) -> int:
    """统计当前日期 fund_flow 快照数。"""
    intraday = PROJECT_ROOT / "research_data" / date_str / "intraday"
    if not intraday.exists():
        return 0
    return sum(1 for f in intraday.iterdir()
               if f.name.startswith("fund_flow_") and f.name.endswith(".csv"))


# ═══════════════════════════════════════════════════════════════════════════════
# v6 主力占比 Top 1% 信号 — forward 验证: 夏普 2.79 日胜率 78.9% 累积 +27.82%
# ═══════════════════════════════════════════════════════════════════════════════

MAIN_CAPITAL_TOP_PCT = 0.01          # Top 1%
MAIN_CAPITAL_MIN_PICKS = 5           # 最少选股数
MAIN_CAPITAL_SKIP_FRIDAY = True      # 跳过周五→周一跨周末缺口


def _compute_main_capital_signal(date_str: str) -> list[dict]:
    """读取最新 fund_flow 快照, 计算主力占比 Top 1% 信号.

    Returns:
        [{code, name, price, main_pct, change_pct, super_large, large,
          turnover, volume_ratio, market_cap}, ...]
        按 主力占比 降序排列.
    """
    import pandas as pd

    intraday_dir = PROJECT_ROOT / "research_data" / date_str / "intraday"
    if not intraday_dir.exists():
        return []

    files = sorted(intraday_dir.glob("fund_flow_*.csv"))
    if not files:
        return []

    try:
        df = pd.read_csv(files[-1])
    except Exception:
        return []

    if "主力占比" not in df.columns:
        return []

    # 数值化
    for col in ["主力占比", "最新价", "涨跌幅", "超大单净流入", "大单净流入",
                "换手率", "量比", "总市值", "成交额"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    valid = df["主力占比"].notna()
    if valid.sum() < MAIN_CAPITAL_MIN_PICKS:
        return []

    threshold = df.loc[valid, "主力占比"].quantile(1 - MAIN_CAPITAL_TOP_PCT)
    top = df[valid & (df["主力占比"] >= threshold)].copy()
    top = top.sort_values("主力占比", ascending=False)

    picks = []
    for _, row in top.iterrows():
        picks.append({
            "code": str(row.get("代码", "")),
            "name": str(row.get("名称", "")),
            "price": float(row.get("最新价", 0)) if pd.notna(row.get("最新价")) else 0.0,
            "main_pct": float(row["主力占比"]),
            "change_pct": float(row.get("涨跌幅", 0)) if pd.notna(row.get("涨跌幅")) else 0.0,
            "super_large": float(row.get("超大单净流入", 0)) if pd.notna(row.get("超大单净流入")) else 0.0,
            "large": float(row.get("大单净流入", 0)) if pd.notna(row.get("大单净流入")) else 0.0,
            "turnover": float(row.get("换手率", 0)) if pd.notna(row.get("换手率")) else 0.0,
            "volume_ratio": float(row.get("量比", 0)) if pd.notna(row.get("量比")) else 0.0,
            "market_cap": float(row.get("总市值", 0)) if pd.notna(row.get("总市值")) else 0.0,
        })
    return picks


def _should_skip_main_capital_signal(date_str: str) -> bool:
    """判断是否应跳过主力占比信号 (周五跨周末风险)."""
    if not MAIN_CAPITAL_SKIP_FRIDAY:
        return False
    dt = datetime.strptime(date_str, "%Y%m%d")
    return dt.weekday() == 4  # 周五


def _save_main_capital_picks(date_str: str, picks: list[dict], time_label: str = ""):
    """持久化主力占比信号选股结果."""
    import json
    out_dir = PROJECT_ROOT / "research_data" / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{time_label}" if time_label else ""
    out_path = out_dir / f"main_capital_picks{suffix}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "date": date_str,
            "time": time_label,
            "signal": "主力占比Top1%",
            "sharpe_20d": 2.79,
            "daily_wr": 0.789,
            "cum_return_20d": 0.2782,
            "total_picks": len(picks),
            "picks": picks,
        }, f, ensure_ascii=False, indent=2)
    return out_path


def _notify_main_capital_signal(date_str: str, picks: list[dict]):
    """企微推送主力占比 Top 1% 收盘信号."""
    if not picks:
        return

    # 周五跳过推送（信号也跳过交易）
    if _should_skip_main_capital_signal(date_str):
        print(f"  ⏭ 周五 skip 主力占比信号 (跨周末风险)")
        return

    try:
        from notify.wecom_sender import send_markdown
    except ImportError:
        return

    top_n = min(10, len(picks))
    now_str = datetime.now(BJS_TZ).strftime("%H:%M")
    lines = [
        f"## 📊 主力占比 Top 1% 信号 — {date_str} {now_str}",
        f"> v6 forward验证: 夏普 2.79 | 日胜率 78.9% | 累积 +27.82%",
        f"> 入选 {len(picks)} 只 | 等权 | T+1 出场",
        "",
    ]

    for i, p in enumerate(picks[:top_n]):
        change_str = f"{p['change_pct']:+.2f}%" if p['change_pct'] else ""
        lines.append(
            f"**{i+1}. {p['name']}** ({p['code']})  "
            f"主力占比: {p['main_pct']:.2f}%  {change_str}"
        )
        cap_str = f"{p['market_cap']/1e8:.0f}亿" if p['market_cap'] else ""
        lines.append(f"> 换手: {p['turnover']:.2f}% | 量比: {p['volume_ratio']:.1f} | 市值: {cap_str}")

    if len(picks) > top_n:
        lines.append(f"")
        lines.append(f"> ... 共 {len(picks)} 只 (完整列表见 data/{date_str}/main_capital_picks.json)")

    lines.append("")
    lines.append("---")
    lines.append("> ⚠ 等权买入 | T+1收盘卖出 | 跳过周五 | 回测最大回撤-2.94%")

    try:
        send_markdown("\n".join(lines))
        print(f"  📤 企微推送: 主力占比 Top1% {len(picks)} 只")
    except Exception as e:
        print(f"  ⚠ 推送失败: {e}")


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
    print(f"  📊 v5回测: 5122只 7544笔 | 止盈率12.0% | 均值 +5.37%")

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
    exit_strategy = get_exit_strategy(regime["regime"])

    # ── v5 入场评估: 信号强度 + 量比 + 体制适配 ──
    def _signal_eval(c):
        """根据回测验证的综合评估."""
        patterns = c.get("patterns", [])
        vol_ratio = c.get("volume_ratio", c.get("vol_ratio_vs5", 1.0))
        eval_result = evaluate_entry_signal(patterns, vol_ratio, regime["regime"])
        return eval_result

    # 按评分重排
    def _sort_key(c):
        ev = _signal_eval(c)
        return ev["score"]
    candidates.sort(key=_sort_key, reverse=True)

    # 筛选高置信信号
    high_confidence = [c for c in candidates
                      if _signal_eval(c)["level"] in ("strong_buy", "buy")][:5]
    if not high_confidence:
        high_confidence = candidates[:3]

    try:
        from notify.wecom_sender import send_markdown
        exit_lines = "\n".join(f"> {i+1}. {s}" for i, s in enumerate(exit_strategy["priority"][:3]))
        lines = [
            f"## 🔍 选股信号 — {date_str}",
            f"> v5 final 7544笔 | {regime['label']} | 止盈 +{regime['target']}%",
            f"> 量比>1 | 双信号确认 | K线出场优先",
            "",
        ]
        for i, c in enumerate(high_confidence):
            ev = _signal_eval(c)
            lines.append(
                f"**{i+1}. {c['name']}** ({c['code']})  "
                f"评分: {ev['score']} | {ev['level']} | 预期: {ev['expected']}"
            )
            lines.append(f"> 策略: {ev['action']}")
            lines.append(f"> 止盈{ev['target']}% | 止损{ev['stop_loss']}% | 移损{ev['trailing']}%")
            patterns = c.get('patterns', [])
            if patterns:
                pattern_info = []
                for p in patterns[:3]:
                    for kw, wr in SIGNAL_PRIORITY.items():
                        if kw in p:
                            pattern_info.append(f"{p[:25]}({wr*100:.0f}%)")
                            break
                    else:
                        pattern_info.append(p[:25])
                lines.append(f"> 信号: {', '.join(pattern_info)}")
            lines.append("")

        # 添加出场策略
        lines.append("---")
        lines.append("## 📤 出场策略")
        lines.append(exit_lines)
        lines.append(f"> 机械: 移动止损{regime['trailing']*100:.0f}% | 硬止损{regime['stop_loss']*100:.0f}%")
        lines.append(f"> 最优持仓: 21-60天 | 不要过早止盈")

        send_markdown("\n".join(lines))
        print(f"  📤 企微推送: {len(high_confidence)} 只高置信信号 (含出场策略)")
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
    print(f"║  主力资金流向定时分析 v6 — 主力占比Top1%信号 + K线选股{'':>10}║")
    print(f"║  资金流: 每{INTERVAL_SEC//60}分钟 | 选股: 每{regime['scanner_interval']//60}分钟 | {regime['label']}{'':>13}║")
    print(f"║  📊 v6验证: 主力占比Top1% 夏普2.79 日胜率78.9% 累积+27.82%{'':>1}║")
    print(f"║  🎯 买入: 主力Top1%等权 | K线: 量比>1 + 三重确认 | 卖出: K线>移损{'':>0}║")
    if dry_run:
        print(f"║  ⚠ 试运行模式{'':>48}║")
    print("╚" + "═" * 62 + "╝")
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
            # ── v6 主力占比 Top 1% 信号 ──
            main_picks = _compute_main_capital_signal(date_str)
            if main_picks:
                time_label = now.strftime("%H%M%S")
                _save_main_capital_picks(date_str, main_picks, time_label)
                print(f"  📊 主力占比 Top1% 信号 ({len(main_picks)} 只) | 阈值≥{main_picks[-1]['main_pct']:.1f}%")
                # 每次执行都输出完整列表
                for i, p in enumerate(main_picks):
                    chg = f"{p['change_pct']:+.2f}%" if p['change_pct'] else "—"
                    cap = f"{p['market_cap']/1e8:.0f}亿" if p['market_cap'] else "—"
                    print(f"  {i+1:3d}. {p['name']:8s} {p['code']:12s} "
                          f"主力{p['main_pct']:5.1f}%  {chg:>8s}  "
                          f"换手{p['turnover']:.1f}%  {cap}")
                if phase == "closing":
                    _notify_main_capital_signal(date_str, main_picks)
            print(f"{'─'*60}")
            print(f"  {'✅' if ok else '❌'} {ts_display}")

        time.sleep(5)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="主力资金流向定时分析调度器 v5 final — 7544笔全量回测版",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
回测验证 (buy_sell_backtest.py v5 final, 5122只全量 1991-2026):
  7544笔 | WR 48.2% | 均值 +5.37% | 峰值 +16.15% | 盈亏比 3.65

买入: 量比>1 + 双信号确认 + 三重确认优先 (MA金叉+连涨+连阳 20%+命中32%)
卖出: K线信号优先 (量价背离+15.98% > 射击之星+12.48% > 看跌吞没+11.99%)
体制: 🐂牛市30% | 📊震荡25% | 🐻熊市15% (仅反转型信号)

用法:
  python schedule_main_capital.py                # 默认模式
  python schedule_main_capital.py --target 30    # 自定义止盈
  python schedule_main_capital.py --daemon       # 后台守护进程
        """)
    parser.add_argument("--daemon", action="store_true", help="后台运行")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-scanner", action="store_true", help="禁用选股扫描")
    parser.add_argument("--weekly", action="store_true", help="周线模式")
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
