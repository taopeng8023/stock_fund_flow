"""
风险控制器 — 多层级门禁系统 + P&L 追踪 + 熔断机制

四层门禁:
  Gate 1: 体制门禁 (bear/bear_bias → 不交易)
  Gate 2: 日内中位数门禁 (14:01 快照 median < -1.0% → 不交易)
  Gate 3: 黑天鹅门禁 (BS L2+ → 不交易)
  Gate 4: 强熊熔断 (market_median < -1.5% → 不交易)

熔断机制:
  L1: 单日组合亏损 > -5% → 次日跳过
  L2: 单日组合亏损 > -8% → 此后2日跳过
  L3: 累计回撤 > -15% → 停止交易直到恢复到 -10%

用法:
  from portfolio.risk_controller import can_trade_today
  ok, reason, details = can_trade_today("20260625")
"""

import csv
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

PROJECT_ROOT = Path(__file__).parent.parent
RESEARCH_ROOT = PROJECT_ROOT / "research_data"
RISK_STATE_PATH = Path(__file__).parent / "risk_state.json"

# ── 门禁阈值 ──
INTRADAY_MEDIAN_THRESHOLD = -1.0    # 14:01 快照中位数 < -1.0% → 不交易
MELTDOWN_THRESHOLD = -1.5           # 全市场中位数 < -1.5% → 不交易
DAILY_LOSS_SKIP1 = -5.0             # 单日亏损 > 5% → 次日跳过
DAILY_LOSS_SKIP2 = -8.0             # 单日亏损 > 8% → 此后2日跳过
MAX_DRAWDOWN = -15.0                # 累计回撤 > 15% → 停止交易
DRAWDOWN_RECOVERY = -10.0           # 回撤恢复到 10% 以内 → 恢复交易

# ── 体制基础仓位（回测校准） ──
BASE_POSITION = {
    "bull": 0.80,
    "bull_bias": 0.50,
    "range": 0.35,
    "bear_bias": 0.0,   # 门禁阻断
    "bear": 0.0,         # 门禁阻断
}

RISK_DISCOUNT = {
    "low": 0.90,
    "medium": 0.75,
    "high": 0.50,
    "critical": 0.25,
}


# ═══════════════════════════════════════
# P&L 状态持久化
# ═══════════════════════════════════════

def _load_risk_state() -> dict:
    """加载风险状态文件。"""
    if RISK_STATE_PATH.exists():
        try:
            with open(RISK_STATE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "peak_equity": 1.0,
        "current_equity": 1.0,
        "daily_pnl_history": {},    # {date_str: pnl_pct}
        "circuit_breaker_until": None,  # 熔断截止日期
        "circuit_breaker_level": 0,
    }


def _save_risk_state(state: dict):
    """保存风险状态。"""
    RISK_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RISK_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def update_daily_pnl(date_str: str, pnl_pct: float):
    """更新每日 P&L 并检查熔断。pnl_pct 为百分比，如 -5.2 表示 -5.2%。"""
    state = _load_risk_state()
    state["daily_pnl_history"][date_str] = round(pnl_pct, 2)

    # 更新累计权益
    state["current_equity"] *= (1 + pnl_pct / 100)
    state["current_equity"] = round(state["current_equity"], 6)

    if state["current_equity"] > state["peak_equity"]:
        state["peak_equity"] = state["current_equity"]

    # 检查熔断
    d = datetime.strptime(date_str, "%Y%m%d")

    if pnl_pct <= DAILY_LOSS_SKIP2:
        # L2: 此后2日跳过
        skip_until = d + timedelta(days=3)
        state["circuit_breaker_until"] = skip_until.strftime("%Y%m%d")
        state["circuit_breaker_level"] = 2
    elif pnl_pct <= DAILY_LOSS_SKIP1:
        # L1: 次日跳过
        skip_until = d + timedelta(days=2)
        state["circuit_breaker_until"] = skip_until.strftime("%Y%m%d")
        state["circuit_breaker_level"] = 1
    else:
        # 检查累计回撤
        drawdown = (state["current_equity"] - state["peak_equity"]) / state["peak_equity"] * 100
        if drawdown <= MAX_DRAWDOWN:
            state["circuit_breaker_level"] = 3
            state["circuit_breaker_until"] = None  # 直到恢复到 -10%
        elif drawdown >= DRAWDOWN_RECOVERY and state.get("circuit_breaker_level") == 3:
            # 回撤恢复
            state["circuit_breaker_level"] = 0
            state["circuit_breaker_until"] = None

    _save_risk_state(state)
    return state


def get_drawdown() -> float:
    """返回当前累计回撤百分比。"""
    state = _load_risk_state()
    return round((state["current_equity"] - state["peak_equity"]) / state["peak_equity"] * 100, 2)


# ═══════════════════════════════════════
# 日内快照加载
# ═══════════════════════════════════════

def _find_intraday_snapshot(date_str: str, target_hour: int = 14) -> Optional[str]:
    """找到 date_str 日内最接近 target_hour 的快照文件路径。"""
    intraday_dir = RESEARCH_ROOT / date_str / "intraday"
    if not intraday_dir.is_dir():
        return None

    fund_flow_files = sorted([
        f for f in os.listdir(intraday_dir)
        if f.startswith("fund_flow_") and f.endswith(".csv")
    ])
    if not fund_flow_files:
        return None

    # 找 <= target_hour:00 的最新快照
    best = None
    for f in fund_flow_files:
        time_str = f.replace("fund_flow_", "").replace(".csv", "")
        try:
            h = int(time_str[:2])
        except ValueError:
            continue
        if h <= target_hour:
            best = f
    return str(intraday_dir / best) if best else str(intraday_dir / fund_flow_files[-1])


def _load_intraday_median(date_str: str) -> Optional[float]:
    """加载 date_str 日内快照的中位数涨跌幅。"""
    path = _find_intraday_snapshot(date_str)
    if not path or not os.path.exists(path):
        return None

    chgs = []
    try:
        with open(path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                try:
                    chg = float(r.get("涨跌幅", 0) or 0)
                except (ValueError, TypeError):
                    continue
                chgs.append(chg)
    except Exception:
        return None

    if not chgs:
        return None
    chgs.sort()
    return chgs[len(chgs) // 2]


# ═══════════════════════════════════════
# 体制检测
# ═══════════════════════════════════════

def _load_regime(date_str: str) -> str:
    """从 diagnosis JSON 加载市场体制。"""
    try:
        diag_dir = RESEARCH_ROOT / date_str / "diagnosis"
        if diag_dir.is_dir():
            files = sorted(os.listdir(diag_dir))
            if files:
                with open(diag_dir / files[-1]) as f:
                    d = json.load(f)
                return d.get("regime", {}).get("regime", "range")
    except Exception:
        pass

    # fallback: 从 scores.csv 推测
    scores_path = RESEARCH_ROOT / date_str / "scores.csv"
    if scores_path.exists():
        try:
            chgs = []
            with open(scores_path, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    try:
                        chgs.append(float(r.get("涨跌幅", 0) or 0))
                    except (ValueError, TypeError):
                        pass
            if chgs:
                chgs.sort()
                med = chgs[len(chgs) // 2]
                up_count = sum(1 for c in chgs if c > 0)
                up_ratio = up_count / len(chgs)
                if up_ratio > 0.55 and med > 0.3:
                    return "bull"
                elif up_ratio < 0.30 and med < -0.5:
                    return "bear"
        except Exception:
            pass
    return "range"


def _check_black_swan(date_str: str) -> Tuple[int, bool]:
    """检查黑天鹅等级。返回 (level, blocked)。"""
    try:
        from portfolio.black_swan import BlackSwanDetector
        bs = BlackSwanDetector(date_str).check()
        level = bs.get("level", 0)
        return level, level >= 2
    except Exception:
        return 0, False


# ═══════════════════════════════════════
# 主门禁函数
# ═══════════════════════════════════════

def can_trade_today(date_str: str) -> tuple:
    """
    判断今天是否可以交易。
    Returns:
        (can_trade: bool, reason: str, details: dict)
        details 包含: regime, bs_level, intraday_median, suggested_position
    """
    reasons = []
    details = {}

    # ── Gate 0: 熔断检查（前日亏损/累计回撤） ──
    state = _load_risk_state()
    cb_until = state.get("circuit_breaker_until")
    cb_level = state.get("circuit_breaker_level", 0)

    if cb_level == 3:
        drawdown = get_drawdown()
        if drawdown <= MAX_DRAWDOWN:
            return False, f"熔断L3: 累计回撤{drawdown:.1f}% > {MAX_DRAWDOWN}%, 停止交易", {
                "regime": "unknown", "bs_level": 0, "intraday_median": None,
                "drawdown": drawdown, "circuit_breaker": 3,
            }

    if cb_until and date_str < cb_until:
        return False, f"熔断L{cb_level}: 前日亏损触发, 暂停交易至 {cb_until}", {
            "regime": "unknown", "bs_level": 0, "intraday_median": None,
            "circuit_breaker": cb_level, "resume_date": cb_until,
        }

    # ── Gate 1: 体制门禁 ──
    regime = _load_regime(date_str)
    details["regime"] = regime

    if regime in ("bear", "bear_bias"):
        reasons.append(f"体制门禁: {regime}")

    # ── Gate 2: 日内中位数门禁 ──
    intraday_med = _load_intraday_median(date_str)
    details["intraday_median"] = round(intraday_med, 2) if intraday_med is not None else None

    if intraday_med is not None and intraday_med < INTRADAY_MEDIAN_THRESHOLD:
        reasons.append(f"日内中位数门禁: median={intraday_med:+.2f}% < {INTRADAY_MEDIAN_THRESHOLD}%")

    # ── Gate 3: 黑天鹅门禁 ──
    bs_level, bs_blocked = _check_black_swan(date_str)
    details["bs_level"] = bs_level

    if bs_blocked:
        reasons.append(f"黑天鹅L{bs_level}: 禁止买入")
    # BS L1: 仓位打7折
    gate_multiplier = 0.7 if bs_level == 1 else 1.0
    details["gate_multiplier"] = gate_multiplier

    # ── Gate 4: 强熊熔断（全体制） ──
    if intraday_med is not None and intraday_med < MELTDOWN_THRESHOLD:
        reasons.append(f"强熊熔断: median={intraday_med:+.2f}% < {MELTDOWN_THRESHOLD}%")

    # ── 汇总 ──
    if reasons:
        return False, " | ".join(reasons), details

    # ── 计算建议仓位 ──
    base_pct = BASE_POSITION.get(regime, 0.35)
    if bs_level == 1:
        base_pct *= 0.7
    details["suggested_position"] = round(base_pct * 100)

    return True, "PASS", details


def suggested_position_pct(date_str: str) -> int:
    """返回建议总仓位百分比。"""
    ok, _, details = can_trade_today(date_str)
    if not ok:
        return 0
    return details.get("suggested_position", 35)


# ═══════════════════════════════════════
# 便捷命令行
# ═══════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="风险控制器 — 门禁检查")
    parser.add_argument("--date", required=True, help="日期 YYYYMMDD")
    parser.add_argument("--update-pnl", type=float, help="更新当日 P&L (%)")
    args = parser.parse_args()

    if args.update_pnl is not None:
        state = update_daily_pnl(args.date, args.update_pnl)
        print(f"  已更新 {args.date} P&L: {args.update_pnl:+.2f}%")
        print(f"  当前权益: {state['current_equity']:.4f}")
        print(f"  峰值权益: {state['peak_equity']:.4f}")
        print(f"  累计回撤: {get_drawdown():.2f}%")
        if state.get("circuit_breaker_level", 0) > 0:
            print(f"  熔断等级: L{state['circuit_breaker_level']}")

    ok, reason, details = can_trade_today(args.date)
    status = "✅ PASS" if ok else "🚫 BLOCK"
    print(f"\n  {status} | {reason}")
    print(f"  体制: {details.get('regime', '?')}")
    print(f"  日内中位数: {details.get('intraday_median', 'N/A')}%")
    print(f"  黑天鹅: L{details.get('bs_level', 0)}")
    print(f"  建议仓位: {details.get('suggested_position', 0)}%")
    if "drawdown" in details:
        print(f"  累计回撤: {details['drawdown']:.1f}%")


if __name__ == "__main__":
    main()
