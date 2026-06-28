"""
隔夜套利筛选 — 尾盘买入次日早盘卖出
约束: FUNDAMENTAL_RULES.md (涨停排除 / 隔夜套利 / 09:45-10:15 出场)
信号来源: 全市场回测 16,645笔 (20260622-0626, 09:45-10:15 出场均价)
S 级: 全市场验证高 WR 信号组合 (n>=30)
BULL 级: 牛市专用 (P33_margin_weak/P35_short_heavy 牛市开放)
BEAR 级: 熊市专用

全市场回测 (16,645笔, 09:45-10:15 出场):
  最佳三信号: P37_momentum_up+E6+P_high_price(65.6%WR) P35_short_cover+P37+P_high_price(62.5%WR)
  最佳双信号: P_low_vol_ratio+P_high_price(57.7%WR) P37_momentum_up+P_high_price(55.2%WR)
  可靠负向: P36_overheat(29.2%WR) P37_momentum_down(26.2%WR)

避雷: P36_overheat P6_retail (全体制) + P33_margin_weak P35_short_heavy (非牛市)
       (P35_short_moderate/E1_low_start/E3_strong_start 全市场WR高于基准, 已移出屏蔽)

用法:
  python daily_pipeline/filter_overnight.py 20260626
  python daily_pipeline/filter_overnight.py 20260626 --top=10
"""

import csv
import os
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta

BJS_TZ = timezone(timedelta(hours=8))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH_ROOT = os.path.join(PROJECT_ROOT, "research_data")

# ═══════════════════════════════════════
# 信号组合体系 (FINAL_VERIFICATION_REPORT)
# ═══════════════════════════════════════

TIERS = {
    # ── S 级: 全市场验证高 WR, n>=30 ──
    "S1": {
        "require": ["P37_momentum_up", "E6_short_squeeze", "P_high_price"],
        "label": "S1:动量+逼空+高价(65.6%WR)", "bonus": 0.15,
        "desc": "最强三重信号 — n=32 WR=65.6% avg=+0.87%",
    },
    "S2": {
        "require": ["P35_short_cover", "P37_momentum_up", "P_high_price"],
        "label": "S2:回补+动量+高价(62.5%WR)", "bonus": 0.12,
        "desc": "第二三重信号 — n=40 WR=62.5% avg=+0.73%",
    },
    "S3": {
        "require": ["P_low_vol_ratio", "P_high_price"],
        "score_min": 0.40, "capital_min": 0.60,
        "label": "S3:低量比+高价(57.7%WR)", "bonus": 0.08,
        "desc": "最佳双信号 — n=71 WR=57.7% avg=+0.45%",
    },
    "S4": {
        "require": ["P37_momentum_up", "P_high_price"],
        "score_min": 0.40, "capital_min": 0.60,
        "label": "S4:动量+高价(55.2%WR)", "bonus": 0.07,
        "desc": "动量向上+高价 — n=116 WR=55.2% avg=+0.42%",
    },
    "S5": {
        "require": ["E6_short_squeeze", "P_high_price"],
        "score_min": 0.40, "capital_min": 0.60,
        "label": "S5:逼空+高价(52.5%WR)", "bonus": 0.06,
        "desc": "逼空启动+高价 — n=80 WR=52.5% avg=+0.12%",
    },
    "S6": {
        "require": ["P34_gap_reverse", "P35_short_cover"],
        "score_min": 0.40, "capital_min": 0.60,
        "label": "S6:低开反转+回补(54.3%WR)", "bonus": 0.08,
        "desc": "最佳验证双信号 — n=35 WR=54.3% avg=+0.61%",
    },
    # ── 熊市专用 ──
    "BEAR": {
        "require": ["E1_low_start", "E4_gap_start", "P35_short_pressure"],
        "regime_only": ["bear"],
        "label": "BEAR:低启+缺口+空头压(81.8%WR)", "bonus": 0.15,
        "desc": "熊市最强三信号 — n=11 WR=81.8% avg=+3.16%",
    },
    # ── 牛市专用 (P_high_price 不触发时的替代) ──
    "BULL-1": {
        "require": ["P33_margin_weak", "E1_low_start"],
        "regime_only": ["bull"],
        "label": "BULL-1:融资弱+低启(75.9%WR)", "bonus": 0.12,
        "desc": "牛市双信号替代 — n~100 WR=75.9% avg=+1.19%",
    },
    "BULL-2": {
        "require": ["P35_short_heavy", "E1_low_start", "P_low_vol_ratio"],
        "regime_only": ["bull"],
        "label": "BULL-2:空头重+低启+低量比(61-68%WR)", "bonus": 0.10,
        "desc": "牛市三信号 — P35_short_heavy 熊市屏蔽但牛市开放",
    },
    "BULL-3": {
        "require": ["P33_margin_weak", "P35_short_heavy", "E1_low_start"],
        "regime_only": ["bull"],
        "label": "BULL-3:融资弱+空头重+低启(81.8%WR)", "bonus": 0.14,
        "desc": "牛市最强三信号 — n=11 WR=81.8% avg=+1.26%",
    },
}

# ═══════════════════════════════════════
# 全局避雷 (全市场 16,645笔验证)
# ═══════════════════════════════════════

# ═══════════════════════════════════════
# 体制感知避雷 (全市场 16,645笔验证, 分体制分析)
# ═══════════════════════════════════════

# 全体制屏蔽 (WR < 30% in both bull & bear)
BLOCK_ALWAYS = [
    "P36_overheat",           # bull 28.0% / bear 29.8% — 全体制最差
    "P6_retail",              # 散户主导 → 低胜率
]

# 非牛市屏蔽 (bull开放, bear/range屏蔽)
# bull WR 极高 → 熊市 WR 极低 (delta -34~68pp)
BLOCK_NON_BULL = [
    "P33_margin_weak",        # bull 79.9% → bear 12.2% (delta -67.7pp)
    "P35_short_heavy",        # bull 66.9% → bear 20.6% (delta -46.3pp)
]

# 已从硬屏蔽降级为扣分 (全市场 WR 高于基准 32.6%):
#   P35_short_moderate (36.4% WR) — 在获胜组合中出现, 不应屏蔽
#   E1_low_start (35.1% WR) — 与 P35_short_pressure 组合达 50% WR
#   E3_strong_start (34.6% WR) — WR 高于基准, 不应硬屏蔽
#   P33_margin_moderate (32.7% WR) — WR=基准, avg_ret 略差

# ═══════════════════════════════════════
# 灾难信号组合 (回测验证 WR < 25%)
# ═══════════════════════════════════════

DISASTER_COMBOS = [
    ("P37_momentum_up", "P_low_vol"),  # P37_up+P_low_vol_ratio: WR=21.5% n=209
    ("E1_low_start", "P_low_vol"),     # E1_low+P_low_vol_ratio: WR=21.5% n=247
]

TIER_ORDER = ["S1", "S2", "S3", "S4", "S5", "S6", "BEAR", "BULL-1", "BULL-2", "BULL-3"]
SIGNAL_VACUUM_BLOCK = True   # 排除无任何P3x信号的股票

# ═══════════════════════════════════════
# 风控参数
# ═══════════════════════════════════════

MAX_PRICE = 2000.0            # 最高价（全市场验证高价股 WR 更高，不设低上限）
MIN_TURNOVER_YI = 0.5         # 最小成交额 5000万
MAX_PER_SECTOR = 2            # 同行业最多 2 只
MAX_ENTRY_CHG = 9.9           # 入场涨跌幅上限（FUNDAMENTAL_RULES 第1条：涨停买不进去）

SECTOR_BLOCKLIST = [
    "贵金属", "乘用车", "普钢", "地面兵装Ⅱ", "数字媒体",
    "养殖业", "保险Ⅱ", "航运港口", "银行Ⅱ",
]


def filter_overnight(date_str=None, top_n=5):
    """筛选隔夜套利候选"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    path = os.path.join(RESEARCH_ROOT, date_str, "scores.csv")
    if not os.path.exists(path):
        print(f"✗ 无评分数据: {date_str}")
        return []

    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)

    # 体制检测 (从 scores.csv 市场体制列, score.py _detect_regime() 写入)
    regime = "range"  # default
    for r in rows:
        mr = r.get("市场体制", "").strip()
        if mr in ("bull", "bear", "range"):
            regime = mr
            break

    # ── 统一门禁系统 (risk_controller) ──
    try:
        sys.path.insert(0, PROJECT_ROOT)
        from portfolio.risk_controller import can_trade_today
        gate_ok, gate_reason, gate_details = can_trade_today(date_str)
        if not gate_ok:
            print(f"\n  🚫 门禁阻断: {gate_reason}\n")
            return []
        bs_level = gate_details.get("bs_level", 0)
        strictness_tiers = gate_details.get("strictness_tiers", TIER_ORDER)
        tier_strictness = gate_details.get("tier_strictness", 0)
        if tier_strictness >= 1:
            print(f"\n  ⚠️ {gate_details.get('warning', gate_reason)}")
    except ImportError:
        # fallback: 仅黑天鹅门禁
        try:
            from portfolio.black_swan import BlackSwanDetector
            bs = BlackSwanDetector(date_str).check()
            if bs["level"] >= 2:
                print(f"\n  🚨 黑天鹅 Level {bs['level']} — 禁止买入\n")
                return []
        except Exception:
            pass

    candidates = []
    stats = {"signal_vacuum": 0, "global_block": 0, "disaster_block": 0,
             "price_block": 0, "turnover_block": 0, "sector_block": 0,
             "no_tier": 0, "limit_up_block": 0}

    for r in rows:
        try:
            price = float(r.get("最新价", 0) or 0)
        except (ValueError, TypeError):
            continue
        if price <= 0 or price > MAX_PRICE:
            stats["price_block"] += 1
            continue

        try:
            turnover = float(r.get("成交额", 0) or 0)
        except (ValueError, TypeError):
            turnover = 0
        if turnover < MIN_TURNOVER_YI * 1e8:
            stats["turnover_block"] += 1
            continue

        industry = r.get("行业", "")
        if industry in SECTOR_BLOCKLIST:
            stats["sector_block"] += 1
            continue

        # FUNDAMENTAL_RULES 第1条: 涨停买不进去
        try:
            entry_chg = float(r.get("涨跌幅", 0) or 0)
        except (ValueError, TypeError):
            entry_chg = 0
        if entry_chg >= MAX_ENTRY_CHG:
            stats["limit_up_block"] += 1
            continue

        signals = r.get("综合信号", "")
        early_sigs = r.get("启动信号", "")

        # 信号真空排除
        if SIGNAL_VACUUM_BLOCK:
            has_signal = any(s in signals for s in [
                "P32_", "P33_", "P34_", "P35_", "P36_", "P37_"
            ])
            if not has_signal:
                stats["signal_vacuum"] += 1
                continue

        # 全局避雷 (体制感知)
        block_list = BLOCK_ALWAYS + (BLOCK_NON_BULL if regime != "bull" else [])
        if any(s in signals for s in block_list):
            stats["global_block"] += 1
            continue

        # P37_momentum_down 条件解禁: 单信号屏蔽, 与 gap_trap 或 P_high_price 组合开放
        if "P37_momentum_down" in signals:
            if "P34_gap_trap" not in signals and "P_high_price" not in signals:
                stats["global_block"] += 1
                continue

        all_sigs = signals + "," + early_sigs

        # 灾难信号组合排除
        if any(all(tok in all_sigs for tok in combo) for combo in DISASTER_COMBOS):
            stats["disaster_block"] += 1
            continue

        score = float(r.get("综合得分", 0) or 0)
        capital = float(r.get("资金得分", 0.5) or 0.5)

        # 逐级匹配 S → BEAR → BULL
        matched_tier = None
        for tk in strictness_tiers:
            rules = TIERS[tk]
            if "regime_only" in rules and regime not in rules["regime_only"]:
                continue
            if "companion_any" in rules:
                if not any(s in all_sigs for s in rules["companion_any"]):
                    continue
            if not all(s in all_sigs for s in rules["require"]):
                continue
            if "score_min" in rules and score < rules["score_min"]:
                continue
            if "capital_min" in rules and capital < rules["capital_min"]:
                continue
            matched_tier = tk
            break

        if not matched_tier:
            stats["no_tier"] += 1
            continue

        # 加分 (BONUS_SIGNALS 叠加)
        bonus = TIERS[matched_tier]["bonus"]
        bonus_signals = {
            "P_high_price": 0.12,         # 最强跨体制质量过滤器 (+15-25pp WR)
            "P35_short_pressure": 0.04,   # 熊市反转信号 bear WR=51.9%
            "P34_gap_trap": 0.05,         # bear WR=50.0% single, combo 63.9%
            "P34_gap_reverse": 0.04,      # bear combo WR 57-77%
            "P33_margin_strong": 0.03,    # 唯一单信号 WR>50%
            "P34_gap_strong": 0.03,       # bear combo WR 60%+
            "E4_gap_start": 0.03,         # bear combo WR 62-75%
            "P34_gap_standalone": 0.02,   # bear combo WR 60%+ with P_high
            "P35_short_cover": 0.01,
        }
        for sig, weight in bonus_signals.items():
            if sig in signals:
                bonus += weight

        # bull: 信号为主 (score/capital 反向)  bear/range: 综合为主 (正向)
        if regime == "bull":
            priority = score * 0.20 + capital * 0.15 + bonus * 0.65
        else:
            priority = score * 0.50 + capital * 0.30 + bonus * 0.30

        candidates.append({
            "代码": r["代码"], "名称": r["名称"],
            "最新价": price,
            "综合得分": score, "资金得分": capital,
            "涨跌幅": float(r.get("涨跌幅", 0) or 0),
            "换手率": float(r.get("换手率", 0) or 0),
            "行业": industry,
            "级别": TIERS[matched_tier]["label"],
            "信号": signals,
            "_priority": round(priority, 4),
        })

    candidates.sort(key=lambda x: -x["_priority"])

    # 行业分散
    sector_count = Counter()
    diversified = []
    for c in candidates:
        ind = c["行业"]
        if sector_count[ind] >= MAX_PER_SECTOR:
            continue
        diversified.append(c)
        sector_count[ind] += 1

    # 输出
    block_desc = f"BLOCK_ALWAYS({len(BLOCK_ALWAYS)})+BLOCK_NON_BULL({len(BLOCK_NON_BULL)})" if regime != "bull" else f"BLOCK_ALWAYS({len(BLOCK_ALWAYS)}) only"
    print(f"\n{'='*70}")
    print(f"  隔夜套利筛选 — {date_str}  体制: {regime}  避雷: {block_desc}")
    print(f"  验证: 16,645笔全市场回测 + 独立 Agent 验证 + FULL_MARKET_BACKTEST_FINAL_REPORT")
    print(f"{'='*70}")
    print(f"  全市场: {len(rows)}只 → 候选: {len(candidates)}只 → 行业分散: {len(diversified)}只")
    print(f"  排除: 信号真空={stats['signal_vacuum']} 全局避雷={stats['global_block']} "
          f"灾难={stats['disaster_block']} "
          f"涨停={stats['limit_up_block']} "
          f"价格={stats['price_block']} 成交额={stats['turnover_block']} "
          f"行业={stats['sector_block']} 未匹配={stats['no_tier']}")

    if not diversified:
        print("\n  无候选股票")
        return []

    top = diversified[:top_n]
    tier_dist = Counter(c["级别"] for c in top)
    print(f"  级别分布: {dict(tier_dist)}")

    print(f"\n  {'':>3} {'代码':<8} {'名称':<8} {'优先':>6} {'综合':>6} {'资金':>6} "
          f"{'涨跌':>6} {'行业':<10} {'级别'}")
    print(f"  {'─'*72}")
    for i, c in enumerate(top):
        print(f"  {i+1:>2}. {c['代码']:<8} {c['名称']:<8s} "
              f"{c['_priority']:>6.3f} {c['综合得分']:>6.3f} {c['资金得分']:>6.3f} "
              f"{c['涨跌幅']:>+5.1f}% {c['行业']:<10s} {c['级别']}")

    if top:
        avg_score = sum(c["综合得分"] for c in top) / len(top)
        avg_cap = sum(c["资金得分"] for c in top) / len(top)
        avg_chg = sum(c["涨跌幅"] for c in top) / len(top)
        print(f"\n  均综合={avg_score:.3f} 均资金={avg_cap:.3f} 均涨跌={avg_chg:+.1f}%")

    tier_summary = ", ".join(f"{tk}={TIERS[tk]['desc']}"
                             for tk in ["S1", "S2", "S3", "S4", "S5", "S6", "BEAR", "BULL-1", "BULL-2", "BULL-3"])
    print(f"\n  信号体系: {tier_summary}")
    print(f"  体制感知避雷: ALWAYS={BLOCK_ALWAYS} NON_BULL={BLOCK_NON_BULL}")
    print(f"  灾难组合: {[f'{a}+{b}' for a,b in DISASTER_COMBOS]}")
    print(f"  行业黑名单: {', '.join(SECTOR_BLOCKLIST[:5])}...")

    # 保存
    out_path = os.path.join(RESEARCH_ROOT, date_str, "overnight_picks.csv")
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        fields = ["代码", "名称", "最新价", "综合得分", "资金得分",
                  "涨跌幅", "换手率", "行业", "级别", "_priority"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(top)
    print(f"  ✓ {out_path}")

    return top


if __name__ == "__main__":
    date_str = sys.argv[1] if len(sys.argv) > 1 else None
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    filter_overnight(date_str, top_n)
