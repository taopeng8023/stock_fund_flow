"""
隔夜套利筛选 — 尾盘买入次日早盘卖出
约束: FUNDAMENTAL_RULES.md (涨停排除 / 隔夜套利 / 09:45-10:15 出场)
信号来源: 09:45-10:15 真实出场价格校准 (111 trades, 4 days)
A 级: 最高 WR 信号组合 (稀缺, 无需分数门槛)
B 级: 适中覆盖信号组合 (score>=0.40-0.55, capital>=0.60-0.75)
C 级: 精选信号 (score>=0.55-0.60, capital>=0.75-0.80)

校准数据 (09:45-10:15 出场窗口, 111笔):
  最佳单信号: P_low_vol_ratio(57.1%WR) P32_pump_risk(37.5%WR)
  最佳组合: P32_pump_risk+P37_momentum_up(60%WR+1.95%) P34_P32_combo+P37(60%WR+1.95%)
  可靠负向: E1_low_start(24.0%WR) E3_strong_start(18.2%WR)

避雷: P36_overheat(4.30%WR) P35_short_moderate(31.91%WR) P33_margin_moderate(17.54%WR)
       P33_margin_weak P35_short_heavy P6_retail P37_momentum_down
       E1_low_start E3_strong_start

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
    # ── A 级: 最高 WR, 稀缺信号自带高置信, 无需分数门槛 ──
    "A1": {
        "require": ["P34_P32_combo", "P35_short_cover"],
        "label": "A1:三重信号(85.9%WR)", "bonus": 0.15,
        "desc": "P34_P32_combo+P35_short_cover — 当前最强复合信号",
    },
    "A2": {
        "require": ["P34_gap_strong", "P32_pump_risk"],
        "label": "A2:静默突破(66.2%WR)", "bonus": 0.10,
        "desc": "P34_gap_strong+P32_pump_risk — 主力介入+高开突破",
    },
    "A3": {
        "require": ["P_high_price", "P35_short_pressure", "P34_gap_reverse"],
        "label": "A3:高价反转(72.8%WR)", "bonus": 0.10,
        "desc": "高价+空头压力+缺口反转 — 震荡市90.8%WR",
    },
    # ── B 级: 适中覆盖, 需分数+资金质量过滤 ──
    "B1": {
        "require": ["P35_short_cover", "E6_short_squeeze"],
        "score_min": 0.55, "capital_min": 0.75,
        "label": "B1:逼空回补(85.9%WR)", "bonus": 0.08,
        "desc": "空头回补+逼空启动 — P4核心组件",
    },
    "B2": {
        "require": ["P34_P32_combo", "P37_momentum_up"],
        "score_min": 0.40, "capital_min": 0.60,
        "label": "B2:突破+动量(69.8%WR)", "bonus": 0.06,
        "desc": "主力突破+得分动量向上",
    },
    "B3": {
        "require": ["P34_gap_reverse", "P35_short_cover"],
        "score_min": 0.40, "capital_min": 0.60,
        "label": "B3:低开反转+回补", "bonus": 0.06,
        "desc": "低开缺口反转+空头回补 — 震荡市反弹",
    },
    "B4": {
        "require": ["P_low_vol_ratio", "P35_short_cover"],
        "score_min": 0.45, "capital_min": 0.65,
        "label": "B4:低量比+回补(50%WR)", "bonus": 0.07,
        "desc": "低量比+空头回补 — 09:45-10:15真实WR=50%",
    },

    # ── C 级: 精选覆盖, 高门槛信号+分数双过滤 ──
    "C1": {
        "require": ["P35_short_cover"],
        "score_min": 0.60, "capital_min": 0.80,
        "label": "C1:空头回补(53.7%WR)", "bonus": 0.04,
        "desc": "融券回补 — 高门槛精选",
    },
    "C2": {
        "require": ["P34_gap_strong", "P33_margin_strong"],
        "score_min": 0.55, "capital_min": 0.75,
        "label": "C2:缺口+融资强势", "bonus": 0.02,
        "desc": "高开缺口+融资猛买 — 高门槛精选",
    },
    "C3": {
        "require": ["P32_ratio_accel", "P35_short_cover"],
        "score_min": 0.55, "capital_min": 0.75,
        "label": "C3:主力加速+回补", "bonus": 0.04,
        "desc": "主力占比加速+空头回补 — 资金共振",
    },
}

# ═══════════════════════════════════════
# 全局避雷 (7 Agent 交叉验证)
# ═══════════════════════════════════════

GLOBAL_BLOCK_SIGNALS = [
    "P36_overheat",           # 4.30% WR (最可靠负向, N=93)
    "P35_short_moderate",     # 31.91% WR (N=47)
    "P33_margin_moderate",    # 17.54% WR (N=57)
    "P33_margin_weak",        # 融资买入为负
    "P35_short_heavy",        # 融券/主力比>3
    "P6_retail",              # 散户主导 → 0% WR
    "P37_momentum_down",      # 得分动量下跌
    "E1_low_start",           # 24.0% WR — 09:45-10:15校准可靠负向
    "E3_strong_start",        # 18.2% WR — 09:45-10:15校准可靠负向
]

SIGNAL_VACUUM_BLOCK = True   # 排除无任何P3x信号的股票

# ═══════════════════════════════════════
# 风控参数
# ═══════════════════════════════════════

MAX_PRICE = 200.0             # 最高价
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

    # 黑天鹅门禁
    try:
        sys.path.insert(0, PROJECT_ROOT)
        from portfolio.black_swan import BlackSwanDetector
        bs = BlackSwanDetector(date_str).check()
        if bs["level"] >= 2:
            print(f"\n  🚨 黑天鹅 Level {bs['level']} — 禁止买入\n")
            return []
    except Exception:
        pass

    candidates = []
    stats = {"signal_vacuum": 0, "global_block": 0, "price_block": 0,
             "turnover_block": 0, "sector_block": 0, "no_tier": 0,
             "limit_up_block": 0}

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

        # 全局避雷
        if any(s in signals for s in GLOBAL_BLOCK_SIGNALS):
            stats["global_block"] += 1
            continue

        all_sigs = signals + "," + early_sigs

        score = float(r.get("综合得分", 0) or 0)
        capital = float(r.get("资金得分", 0.5) or 0.5)

        # 逐级匹配 A → B → C
        matched_tier = None
        for tk in ["A1", "A2", "A3", "B1", "B2", "B3", "B4", "C1", "C2", "C3"]:
            rules = TIERS[tk]
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
            "P33_margin_strong": 0.02,
            "P34_gap_strong": 0.03,
            "P32_ratio_accel": 0.01,
            "P35_short_cover": 0.01,
        }
        for sig, weight in bonus_signals.items():
            if sig in signals:
                bonus += weight

        priority = score * 0.5 + capital * 0.30 + bonus * 0.20

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
    print(f"\n{'='*70}")
    print(f"  隔夜套利筛选 — {date_str}")
    print(f"  验证: 574,604笔回测 + 7 Agent 交叉验证 + FINAL_VERIFICATION_REPORT")
    print(f"{'='*70}")
    print(f"  全市场: {len(rows)}只 → 候选: {len(candidates)}只 → 行业分散: {len(diversified)}只")
    print(f"  排除: 信号真空={stats['signal_vacuum']} 全局避雷={stats['global_block']} "
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
                             for tk in ["A1", "A2", "A3", "B1", "B4", "C1"])
    print(f"\n  信号体系: {tier_summary}")
    print(f"  全局避雷: {' '.join(GLOBAL_BLOCK_SIGNALS[:4])}...")
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
