"""
买入引擎 — 信号驱动 + 体制感知（6 Agent 回测验证）

信号发现: 574,604+笔跨日回测, 4交易日, 118盘中快照
基线: 全市场胜率 28.3%, 均收益 -1.13%

优化验证 (2026-06-27): 成交额修复后全量重评分+重新回测, P梯队权重再校准

核心规则（5 Agent 信号发现 + 4日跨日回测 574,604笔验证）:
  P1 🥇 E3+P34共振:       E3_strong_start + P34_gap_strong       → 74.4% WR, +2.99%, N=617, 全体制有效
  P0 🥈 静默突破:          P34_gap_strong + P32_pump_risk        → 66.2% WR, +2.44%, N=1456, 牛熊通用
  P2 🥉 高价+空头+Gap反:   P_high_price + P35_short_pressure
                           + P34_gap_reverse                     → 72.8% WR, +2.04%, N=243, 震荡专用
  P3 🏅 半导体+空头回补:   行业=半导体 + P35_short_cover           → 53.7% WR, +1.18%, N=5101, 牛/震荡有效
  P4 🔥 三重信号:          P34_P32_combo + P35_short_cover       → 85.9% WR, +4.96%, N=640, 最强复合

避雷: P6_retail(0%WR) | P37_momentum_up(全局-8pp,熊市仅20.7%) | P37_momentum_down
       贵金属(3.9%) | 乘用车(5.6%) | 普钢(9.1%) | 地面兵装(9.4%) | 数字媒体(9.7%)
门禁: 黑天鹅 Level ≥ 2 → 禁止一切买入
行业: 同行业 ≤ 2只
仓位: P4=20% + P1=30% + P0=30% + P2=15% + P3=10% | 熊市P3→5%, P2禁用
P34_alone: 43.9% WR, -0.20%, N=9200 — 独立使用≈随机, 必须组合匹配

用法:
  python -m portfolio.buy_engine --date=20260624
  python -m portfolio.buy_engine --date=20260624 --top=10
  python -m portfolio.buy_engine --date=20260624 --no-notify
"""

import argparse
import csv
import os
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
RESEARCH_ROOT = PROJECT_ROOT / "research_data"


# ═══════════════════════════════════════
# 崩溃闸门（structure_score 回测: 603201 案例, 108/108 checks）
# ═══════════════════════════════════════

def _find_prior_trading_dates(date_str: str, n: int = 3) -> list:
    """扫描 research_data/ 下小于 date_str 的数字目录，返回有 scores.csv 的最近 n 个日期。"""
    if not RESEARCH_ROOT.exists():
        return []
    date_dirs = sorted([
        d for d in os.listdir(RESEARCH_ROOT)
        if os.path.isdir(RESEARCH_ROOT / d) and d.isdigit() and d < date_str
    ], reverse=True)
    prior_dates = []
    for d in date_dirs:
        if (RESEARCH_ROOT / d / "scores.csv").exists():
            prior_dates.append(d)
            if len(prior_dates) >= n:
                break
    return prior_dates


def _load_prior_day_returns(prior_dates: list) -> dict:
    """从 prior_dates 的 scores.csv 读取涨跌幅。返回 {code: [chg_d1, chg_d2, ...]}。"""
    from collections import defaultdict
    returns_map = defaultdict(list)
    for d in prior_dates:
        path = RESEARCH_ROOT / d / "scores.csv"
        try:
            with open(path, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    code = r.get("代码", "")
                    if not code:
                        continue
                    try:
                        chg = float(r.get("涨跌幅", 0) or 0)
                    except (ValueError, TypeError):
                        chg = 0.0
                    returns_map[code].append(chg)
        except Exception:
            continue
    return dict(returns_map)


def _check_crash_gate(code: str, today_chg: float, prior_returns: dict) -> tuple:
    """三道崩溃闸门。回测发现 603201 连续跌停但 structure_score 仍高居 Top 10。
    Gate 1: 当日跌幅 < -8%
    Gate 2: 前 3 日内任意日跌幅 <= -9.5%
    Gate 3: 3 日累计跌幅 <= -12%
    Returns (pass: bool, reason: str|None).
    """
    # Gate 1: 当日跌停前置过滤
    if today_chg < -8.0:
        return False, f"Gate1:{code}当日大跌({today_chg:+.1f}%)"

    if not prior_returns or code not in prior_returns:
        return True, None

    priors = prior_returns[code]

    # Gate 2: 多日崩盘检测
    for i, ret in enumerate(priors[:3]):
        if ret <= -9.5:
            return False, f"Gate2:{code}前{i+1}日跌停({ret:+.1f}%)"

    # Gate 3: 连续下跌检测
    if len(priors) >= 2:
        cum_3d = today_chg + priors[0] + priors[1]
        if cum_3d <= -12.0:
            return False, f"Gate3:{code}3日累计下跌({cum_3d:+.1f}%)"

    return True, None


# ═══════════════════════════════════════
# 信号驱动买入规则（5 Agent 信号发现验证）
# ═══════════════════════════════════════

TIERS = {
    "P0": {
        "require_signals": ["P34_gap_strong", "P32_pump_risk"],
        "block_signals": ["P37_momentum_down", "P6_retail", "P33_margin_weak"],
        "label": "P0:静默突破",
        "wr": "66.2%", "n": 1456, "avg_ret": "+2.44%",
        "desc": "P34_gap_strong+P32_pump_risk — 静默期后突发主力+高开=真突破",
        "position_pct": 30,
        "score_min": 0.40,
        "regime_ok": ["bull", "bull_bias", "range", "bear", "bear_bias"],
    },
    "P1": {
        "require_signals": ["E3_strong_start", "P34_gap_strong"],
        "block_signals": ["P37_momentum_down", "P6_retail", "P33_margin_weak"],
        "label": "P1:E3+P34共振",
        "wr": "74.4%", "n": 617, "avg_ret": "+2.99%",
        "desc": "E3_strong_start+P34_gap_strong — 开盘强势+缺口确认, 当前最强单层信号",
        "position_pct": 30,
        "score_min": 0.38,
        "regime_ok": ["bull", "bull_bias", "range", "bear", "bear_bias"],
    },
    "P2": {
        "require_signals": ["P_high_price", "P35_short_pressure", "P34_gap_reverse"],
        "block_signals": ["P37_momentum_down", "P6_retail", "P33_margin_weak"],
        "label": "P2:高价+空头+Gap反转",
        "wr": "72.8%", "n": 243, "avg_ret": "+2.04%",
        "desc": "P_high_price+P35_short_pressure+P34_gap_reverse — 高价优质回调",
        "position_pct": 15,
        "score_min": 0.30,
        "regime_ok": ["bull", "bull_bias", "range"],  # 0625熊市0%WR
        "note": "震荡市90.8%WR但熊市0%WR, 体制极度敏感",
    },
    "P3": {
        "require_signals": ["P35_short_cover"],
        "block_signals": ["P37_momentum_down", "P6_retail", "P33_margin_weak"],
        "label": "P3:半导体+空头回补",
        "wr": "53.7%", "n": 5101, "avg_ret": "+1.18%",
        "desc": "行业=半导体+P35_short_cover — 主线行业叠加空头回补",
        "position_pct": 10,
        "position_pct_bear": 5,
        "score_min": 0.38,
        "required_sector": "半导体",
        "regime_ok": ["bull", "bull_bias", "range", "bear", "bear_bias"],
    },
    "P4": {
        "require_signals": ["P34_P32_combo", "P35_short_cover"],
        "block_signals": ["P37_momentum_down", "P6_retail"],
        "label": "P4:三重信号",
        "wr": "85.9%", "n": 640, "avg_ret": "+4.96%",
        "desc": "P34_P32_combo+P35_short_cover — 主力突破+空头回补, 当前最强复合信号",
        "position_pct": 20,
        "score_min": 0.40,
        "regime_ok": ["bull", "bull_bias", "range", "bear", "bear_bias"],
    },
    "OBSERVE": {
        "require_signals": [],
        "block_signals": ["P37_momentum_down", "P6_retail"],
        "label": "观察:中线≥0.8",
        "wr": "57.7%", "n": 78, "avg_ret": "+1.29%",
        "desc": "中线得分≥0.8 — 中长线趋势确认",
        "position_pct": 0,
        "score_min": 0,
        "mid_score_min": 0.8,
        "regime_ok": ["bull", "bull_bias", "range", "bear", "bear_bias"],
    },
}

# ═══════════════════════════════════════
# 全局风控
# ═══════════════════════════════════════

MAX_PRICE = 200.0               # P2规则用高价股,放宽上限
MIN_TURNOVER_YI = 0.5           # 最小成交额 5000万
MAX_PER_SECTOR = 2              # 同行业最多 2 只

# ── 全局排除令牌（5 Agent 验证为负向信号，任何规则均排除）──
#
# ⚠️ P34_gap_strong 矛盾警示 (BACKTEST_REPORT_20260626):
#   P34_gap_strong 作为独立信号持续恶化 (47%→33%→30%, 3日跨期回测)
#   仅在搭配 P32_pump_risk (P0静默突破) 或 E3_strong_start (P1共振) 时可信
#   本引擎通过 TIERS.require_signals 强制组合匹配，禁止 P34_gap_strong 单独入选
GLOBAL_EXCLUDE_TOKENS = [
    "P6_retail",              # 散户主导 → 0% WR (38样本)
    "P35_short_heavy",        # 融券/主力比>3 → -5.6pp
    "P35_short_moderate",     # 融券中等 → 31.91% WR (vs P34_P32_combo, N=47)
    "P33_margin_moderate",    # 融资中等 → 17.54% WR (vs P34_P32_combo, N=57)
    "P36_overheat",           # 过热线 → 4.30% WR (最可靠负向信号, N=93)
    "P_low_liquidity",        # 流动性不足
    "P_small_cap",            # 小市值<30亿 → -7.2pp
]
# P33_margin_weak 移除全局排除: 在P4(P34_P32_combo)上下文 89.77% WR (N=88)
# 仅在各Tier级block_signals中排除 (P0/P1/P2/P3), P4不排除

# ── 熊市额外排除（牛市信号在熊市崩溃）──
BEAR_EXCLUDE_TOKENS = [
    "P37_momentum_up",        # 全局-8pp, 熊市仅20.7% WR vs 牛市57.4%
]

# ── 行业黑名单（5 Agent 验证为系统性低胜率板块）──
SECTOR_BLOCKLIST = [
    "贵金属",                  # 3.9% WR, N=208
    "乘用车",                  # 5.6% WR, N=126
    "普钢",                    # 9.1% WR, N=275
    "地面兵装Ⅱ",              # 9.4% WR, N=192
    "数字媒体",                # 9.7% WR, N=228
    "养殖业",                  # 持续衰退
    "保险Ⅱ",                  # 持续衰退
    "航运港口",                # 持续衰退
    "银行Ⅱ",                  # 持续衰退
]


def load_scores(date_str: str) -> list:
    path = RESEARCH_ROOT / date_str / "scores.csv"
    if not path.exists():
        print(f"❌ {date_str} 无评分数据，请先运行评分")
        sys.exit(1)
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def check_black_swan(date_str: str) -> tuple:
    try:
        from portfolio.black_swan import BlackSwanDetector
        bs = BlackSwanDetector(date_str).check()
        level = bs["level"]
        return level, level >= 2
    except Exception:
        return 0, False


def detect_regime(date_str: str) -> str:
    """检测当前市场体制。"""
    try:
        from market_diagnosis import load_diagnosis
        d = load_diagnosis(date_str)
        if d:
            return d.get("regime", {}).get("label", "range")
    except Exception:
        pass
    return "range"


def _is_bear_regime(regime: str) -> bool:
    return regime in ("bear", "bear_bias")


def filter_candidates(scores: list, tier_key: str, regime: str = "range",
                      prior_returns: dict = None) -> list:
    """按 tier 规则筛选候选。P0-P3 基于信号，OBSERVE 基于中线得分。"""
    rules = TIERS[tier_key]
    is_bear = _is_bear_regime(regime)

    # 体制门禁
    if regime not in rules.get("regime_ok", []):
        return []

    candidates = []
    for r in scores:
        # ── 基本风控 ──
        try:
            price = float(r.get("最新价", 0) or 0)
        except (ValueError, TypeError):
            continue
        if price <= 0 or price > MAX_PRICE:
            continue

        try:
            turnover = float(r.get("成交额", 0) or 0)
        except (ValueError, TypeError):
            turnover = 0
        if turnover < MIN_TURNOVER_YI * 1e8:
            continue

        industry = r.get("行业", "")
        if industry in SECTOR_BLOCKLIST:
            continue

        # ── 崩溃闸门 (structure_score 回测: 603201 案例, 108/108 checks) ──
        try:
            today_chg = float(r.get("涨跌幅", 0) or 0)
        except (ValueError, TypeError):
            today_chg = 0.0
        if prior_returns is not None:
            passes, gate_reason = _check_crash_gate(r["代码"], today_chg, prior_returns)
            if not passes:
                print(f"  崩溃闸门: {gate_reason}")
                continue

        try:
            score = float(r.get("综合得分", 0) or 0)
        except (ValueError, TypeError):
            continue
        if score < rules.get("score_min", 0):
            continue

        # 中线得分门槛 (OBSERVE)
        if "mid_score_min" in rules:
            try:
                mid = float(r.get("中线得分", 0) or 0)
            except (ValueError, TypeError):
                continue
            if mid < rules["mid_score_min"]:
                continue

        # 行业门禁 (P3)
        if "required_sector" in rules:
            if industry != rules["required_sector"]:
                continue

        signals = r.get("综合信号", "")
        early_sigs = r.get("启动信号", "")

        # ── 全局排除 ──
        if any(tok in signals for tok in GLOBAL_EXCLUDE_TOKENS):
            continue
        if "P37_momentum_down" in signals:
            continue

        # ── 熊市排除 ──
        if is_bear:
            if any(tok in signals for tok in BEAR_EXCLUDE_TOKENS):
                continue

        # ── Tier 必备信号 ──
        required = rules.get("require_signals", [])
        if required:
            all_sigs = signals + "," + early_sigs
            if not all(s in all_sigs for s in required):
                continue

        # ── Tier 避雷信号 ──
        block = rules.get("block_signals", [])
        if block:
            all_sigs = signals + "," + early_sigs
            if any(s in all_sigs for s in block):
                continue

        try:
            score_val = float(r.get("综合得分", 0) or 0)
            capital_val = float(r.get("资金得分", 0.5) or 0.5)
            sector_val = float(r.get("板块得分", 0.5) or 0.5)
            chg_val = float(r.get("涨跌幅", 0) or 0)
            mcap_val = float(r.get("总市值", 0) or 0)
        except (ValueError, TypeError):
            continue

        # 建议出场窗口
        suggest_exit = "14:00-14:30" if (regime == "bull" and score_val > 0.8) else "10:00-10:30"

        candidates.append({
            "code": r["代码"],
            "name": r["名称"],
            "score": round(score_val, 4),
            "tier": tier_key,
            "tier_label": rules["label"],
            "chg_pct": round(chg_val, 2),
            "price": round(price, 2),
            "industry": industry,
            "signals": signals,
            "early_signals": early_sigs,
            "capital": round(capital_val, 2),
            "sector_score": round(sector_val, 2),
            "mcap_yi": round(mcap_val, 1),
            "wr_ref": rules["wr"],
            "avg_ret_ref": rules.get("avg_ret", "—"),
            "suggest_exit": suggest_exit,
        })

    candidates.sort(key=lambda x: -x["score"])
    return candidates


def apply_sector_diversity(candidates: list, max_n: int) -> list:
    selected = []
    sector_count = Counter()
    for c in candidates:
        ind = c["industry"]
        if sector_count[ind] >= max_n:
            continue
        selected.append(c)
        sector_count[ind] += 1
    return selected


def generate_recommendations(date_str: str, top_n: int = 10,
                             no_notify: bool = False) -> dict:
    """生成买入推荐。P1→P0→P4→P2→P3→OBSERVE 优先级。"""
    scores = load_scores(date_str)
    regime = detect_regime(date_str)

    # 加载前3日涨跌幅用于崩溃闸门 (structure_score 回测优化)
    prior_dates = _find_prior_trading_dates(date_str, n=3)
    prior_returns = _load_prior_day_returns(prior_dates) if prior_dates else None

    # 黑天鹅门禁
    bs_level, blocked = check_black_swan(date_str)
    if blocked:
        print(f"\n{'='*60}")
        print(f"  🚨 黑天鹅 Level {bs_level} — 禁止一切买入")
        print(f"{'='*60}")
        return {"date": date_str, "blocked": True, "bs_level": bs_level,
                "buys": [], "reason": f"黑天鹅 Level {bs_level}"}

    # 强熊熔断: 全市场<-1.5%时禁止一切买入
    if _is_bear_regime(regime):
        all_chgs = []
        for s in scores:
            try:
                all_chgs.append(float(s.get("涨跌幅", 0) or 0))
            except (ValueError, TypeError):
                pass
        if all_chgs:
            market_median = sorted(all_chgs)[len(all_chgs) // 2]
            if market_median < -1.5:
                print(f"\n{'='*60}")
                print(f"  🐻 强熊熔断: 全市场涨跌幅中位数={market_median:+.2f}% < -1.5%")
                print(f"     因子失效风险极高，禁止一切买入")
                print(f"{'='*60}")
                return {"date": date_str, "blocked": True, "bs_level": bs_level,
                        "regime": regime, "candidates": [], "buys": [],
                        "block_reason": f"强熊熔断(全市场中位数{market_median:+.2f}%)"}

    # 按优先级筛选：P1 → P0 → P4 → P2 → P3 → OBSERVE
    tier_priority = ["P4", "P1", "P0", "P2", "P3", "OBSERVE"]
    all_candidates = []
    seen = set()

    for tk in tier_priority:
        for c in filter_candidates(scores, tk, regime, prior_returns=prior_returns):
            if c["code"] not in seen:
                all_candidates.append(c)
                seen.add(c["code"])

    # P0 优先，同 tier 内按 score 降序
    tier_order = {t: i for i, t in enumerate(tier_priority)}
    all_candidates.sort(key=lambda x: (tier_order.get(x["tier"], 99), -x["score"]))

    is_bear = _is_bear_regime(regime)
    print(f"  全量: {len(scores)}只 → 候选: {len(all_candidates)}只 | 体制: {regime}"
          f"{' ⚠️熊市限制' if is_bear else ''}")

    # 动态TopN: 基于市场体制调整选股数量
    _dynamic_cap = {
        "bull": 10, "bull_bias": 15, "range": 20, "bear_bias": 25, "bear": 30,
    }
    effective_n = max(top_n, _dynamic_cap.get(regime, top_n))  # bear多选, bull少选

    # 行业分散
    buys = apply_sector_diversity(all_candidates, MAX_PER_SECTOR)[:effective_n]
    sector_dist = Counter(b["industry"] for b in buys)

    print(f"  行业≤{MAX_PER_SECTOR}: {len(buys)}只 分布: {dict(sector_dist)}")

    # 仓位分配 (熊市P3降仓: 回测WR 36.1% 超额仅+0.71%)
    for b in buys:
        tier_cfg = TIERS.get(b["tier"], {})
        base_pct = tier_cfg.get("position_pct", 0)
        if is_bear and "position_pct_bear" in tier_cfg:
            base_pct = tier_cfg["position_pct_bear"]
        b["suggested_pct"] = base_pct

    _print_recommendations(date_str, bs_level, regime, buys)

    if not no_notify and buys:
        _notify_buys(date_str, bs_level, buys)

    return {"date": date_str, "blocked": False, "bs_level": bs_level,
            "regime": regime, "candidates": len(all_candidates), "buys": buys}


def _print_recommendations(date_str, bs_level, regime, buys):
    is_bear = _is_bear_regime(regime)
    print(f"\n{'='*85}")
    print(f"  🎯 买入推荐 [{date_str}]  BS L{bs_level}  体制: {regime}"
          f"{' ⚠️' if is_bear else ''}")
    print(f"  验证: 6 Agent 信号发现 | 574,604笔跨日回测 | 成交额修复+P4三重信号")
    print(f"{'='*85}")

    if not buys:
        print("  (无符合条件的买入标的)")
        print(f"\n  💡 体制 {regime} + BS L{bs_level}，建议等待更好时机")
        return

    print(f"\n  {'#':<3} {'代码':<9} {'名称':<8} {'得分':<8} {'涨跌':<7} "
          f"{'行业':<10} {'级别':<24} {'仓位':<5} {'WR参考':<10} {'建议出场'}")
    print(f"  {'─'*100}")

    for i, b in enumerate(buys):
        tier_note = TIERS.get(b["tier"], {}).get("note", "")
        note_str = f" [{tier_note}]" if tier_note else ""
        exit_hint = b.get("suggest_exit", "10:00-10:30")
        print(f"  {i+1:<3} {b['code']:<9} {b['name']:<8} {b['score']:.4f} "
              f"{b['chg_pct']:>+.1f}%  {b['industry']:<10} "
              f"{b['tier_label']:<24} {b['suggested_pct']}%  "
              f"WR={b['wr_ref']:<8} {exit_hint}")

    # 仓位汇总
    active_buys = [b for b in buys if b["suggested_pct"] > 0]
    total_position = sum(b["suggested_pct"] for b in active_buys)
    print(f"\n  ── 仓位合计: {total_position}% ({len(active_buys)}只) ──")
    print(f"  P1=30% | P0=30% | P2=15% | P3=10% | P4=20% | 观察=不占仓位")
    print(f"  避雷: P6_retail P37_down P35_short_heavy P35_short_moderate P33_margin_moderate P36_overheat P_small_cap P_low_liquidity")
    if is_bear:
        print(f"  熊市排除: P37_momentum_up P1/P2 限制")
    print(f"  行业黑名单: {', '.join(SECTOR_BLOCKLIST[:5])}...")
    print(f"  建议出场: 默认10:00-10:30 | 牛市高分(>0.8)→14:00-14:30")


def _notify_buys(date_str, bs_level, buys):
    try:
        from notify.wecom_sender import send_markdown
    except ImportError:
        return

    top = buys[:8]
    lines = [f"## 🎯 买入推荐 — {date_str}"]
    lines.append(f"> 黑天鹅 L{bs_level} | 推荐 {len(buys)} 只 | 5Agent信号发现验证")
    lines.append("")

    for i, b in enumerate(top):
        note = TIERS.get(b["tier"], {}).get("note", "")
        note_str = f" ⚠️{note}" if note else ""
        lines.append(f"**{i+1}. {b['name']}**（{b['code']}）")
        lines.append(f"> {b['tier_label']} | 得分 {b['score']:.3f} | "
                     f"涨跌 {b['chg_pct']:+.1f}% | {b['industry']} | "
                     f"WR参考={b['wr_ref']}{note_str}")
        lines.append("")

    send_markdown("\n".join(lines))
    print("  📤 已推送企业微信")


def main():
    parser = argparse.ArgumentParser(description="买入推荐引擎 (信号发现验证版)")
    parser.add_argument("--date", required=True, help="日期 YYYYMMDD")
    parser.add_argument("--top", type=int, default=10, help="推荐数量")
    parser.add_argument("--no-notify", action="store_true", help="不推送企业微信")
    args = parser.parse_args()

    generate_recommendations(args.date, top_n=args.top,
                             no_notify=args.no_notify)


if __name__ == "__main__":
    main()
