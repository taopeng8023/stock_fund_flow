"""
风险控制器 — 选股严格度递进 + P&L 追踪 + 熔断机制

核心理念: 用选股严格度 + 仓位递进替代二值门禁。
回测验证: S1信号在极端熊市日(中位数-2.87%)盘中均价仍+5.55%, 隔夜WR=77.8%。
系统性下跌中信号筛选仍能产生正超额, 不应二值阻断。

日内中位数分层 (取消硬阻断):
  Level 0: median > -1.0%         → 正常交易, 全 tier
  Level 1: -1.5% < median <= -1.0% → 降仓, S1-S3+BEAR
  Level 2: -2.0% < median <= -1.5% → 精选, S1/S2/BEAR
  Level 3: -3.0% < median <= -2.0% → 极致精选, S1/BEAR
  Level 4: median <= -3.0%        → 仅S1, 最低仓位

唯一硬阻断: 黑天鹅 L2+ / 熔断L3(累计回撤>15%)

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

# ── 熔断阈值 ──
DAILY_LOSS_SKIP1 = -5.0            # 单日亏损 > 5% → 次日跳过
DAILY_LOSS_SKIP2 = -8.0            # 单日亏损 > 8% → 此后2日跳过
MAX_DRAWDOWN = -15.0               # 累计回撤 > 15% → 停止交易
DRAWDOWN_RECOVERY = -10.0          # 回撤恢复到 10% 以内 → 恢复交易

# ── 日内中位数分层阈值（从最负到最正, 依次匹配） ──
INTRADAY_LEVELS = [
    (float("-inf"), 4),  # < -3.0%: 仅S1
    (-3.0,  3),          # -3.0% ~ -2.0%: 极致精选
    (-2.0,  2),          # -2.0% ~ -1.5%: 精选
    (-1.5,  1),          # -1.5% ~ -1.0%: 降仓
    (-1.0,  0),          # > -1.0%: 正常
]

# 各层级的仓位乘数 + tier 限制
LEVEL_CONFIG = {
    0: {"position_mult": 1.0,  "tier_strictness": 0, "label": "正常"},
    1: {"position_mult": 0.50, "tier_strictness": 1, "label": "降仓"},
    2: {"position_mult": 0.25, "tier_strictness": 2, "label": "精选"},
    3: {"position_mult": 0.15, "tier_strictness": 3, "label": "极致精选"},
    4: {"position_mult": 0.10, "tier_strictness": 4, "label": "仅S1"},
}

# tier_strictness → 可用 tier 列表 (filter_overnight / buy_engine 使用)
# 0=全tier, 1=S1-S3+BEAR, 2=S1/S2/BEAR, 3=S1/BEAR, 4=S1 only
STRICTNESS_TIERS = {
    0: ["S1", "S2", "S3", "S4", "S5", "S6", "BEAR", "BULL-1", "BULL-2", "BULL-3"],
    1: ["S1", "S2", "S3", "BEAR"],
    2: ["S1", "S2", "BEAR"],
    3: ["S1", "BEAR"],
    4: ["S1"],
}

# ── 体制基础仓位（回测校准） ──
BASE_POSITION = {
    "bull": 0.80,
    "bull_bias": 0.50,
    "range": 0.35,
    "bear_bias": 0.25,   # 降仓但不阻断 — 精选 S-tier 仍可盈利
    "bear": 0.15,         # 降仓但不阻断 — BEAR tier 81.8%WR
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
    取消日内中位数硬阻断, 改用选股严格度 + 仓位递进。

    Returns:
        (can_trade: bool, reason: str, details: dict)
        details 包含: regime, bs_level, intraday_median, tier_strictness,
                      suggested_position, strictness_tiers, warning
    """
    reasons = []
    details = {}

    # ── Hard Gate 0: 熔断检查（前日亏损/累计回撤） ──
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

    # ── Hard Gate 1: 黑天鹅 L2+ ──
    bs_level, bs_blocked = _check_black_swan(date_str)
    details["bs_level"] = bs_level

    if bs_blocked:
        reasons.append(f"黑天鹅L{bs_level}: 禁止买入")

    if reasons:
        return False, " | ".join(reasons), details

    # ── 体制检测 ──
    regime = _load_regime(date_str)
    details["regime"] = regime

    # ── 日内中位数检测 ──
    intraday_med = _load_intraday_median(date_str)
    details["intraday_median"] = round(intraday_med, 2) if intraday_med is not None else None

    # ── 日内中位数分层（取消硬阻断） ──
    med_level = 0
    if intraday_med is not None:
        for threshold, level in INTRADAY_LEVELS:
            if intraday_med <= threshold:
                med_level = level
                break

    level_cfg = LEVEL_CONFIG[med_level]
    tier_strictness = level_cfg["tier_strictness"]
    position_multiplier = level_cfg["position_mult"]

    # ── 仓位乘数计算 ──
    # BS L1: 仓位打7折
    if bs_level == 1:
        position_multiplier *= 0.7

    # 熊市/偏熊: 仓位再打折 + 选股严格度至少 Level 2
    if regime in ("bear", "bear_bias"):
        position_multiplier *= 0.7
        tier_strictness = max(tier_strictness, 2)  # bear日至少精选模式
        details["regime_restricted"] = True
        if med_level <= 1:
            details["warning"] = f"{regime}体制, 精选模式(S1/S2/BEAR)"

    details["tier_strictness"] = tier_strictness
    details["strictness_tiers"] = STRICTNESS_TIERS[tier_strictness]
    details["position_multiplier"] = round(position_multiplier, 4)

    # ── 生成 warning ──
    if med_level >= 1 and "warning" not in details:
        details["warning"] = f"日内{level_cfg['label']}模式(median={intraday_med:+.2f}%), 仅{'/'.join(STRICTNESS_TIERS[tier_strictness])}"

    # ── 计算建议仓位 ──
    base_pct = BASE_POSITION.get(regime, 0.35)
    base_pct *= position_multiplier
    details["suggested_position"] = round(base_pct * 100)

    effective_level = tier_strictness
    effective_label = LEVEL_CONFIG[effective_level]["label"]
    return True, f"PASS (L{effective_level} {effective_label})", details


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
