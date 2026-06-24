"""
隔夜套利筛选 — 尾盘买入次日开盘卖出（回测驱动三级规则）

16503笔回测验证的最优信号组合:
  🥇 57%胜率: 分≥0.60 + 资≥0.80 + P34_gap − P36_overheat
  🥈 43%胜率: 分≥0.60 + 资≥0.70 + P37_up − P36_overheat
  🥉 34%胜率: 分≥0.55 + 资≥0.60 + P37_up − P36_overheat/P35_short

王者信号: P34_gap_strong(胜率+19%), P37_momentum_up(胜率+11%)
避雷信号: P36_overheat(胜率-8%), P35_short_pressure(Bot50专属)
资金得分是唯一有效的二级过滤器(趋势/位置/板块无单调性)

用法:
  python daily_pipeline/filter_overnight.py 20260624
  python daily_pipeline/filter_overnight.py 20260624 --top=10
"""

import csv
import os
import sys
from datetime import datetime, timezone, timedelta

BJS_TZ = timezone(timedelta(hours=8))
RESEARCH_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data"
)

# ── 三级买入规则（全量回测+多时点验证）──
TIERS = {
    1: {"score": 0.60, "capital": 0.80, "require": ["P34_gap_strong"],
        "block": ["P36_overheat"], "label": "🥇王者(57-60%)", "bonus": 0.10,
        "desc": "P34_gap封王,全天正收益,14:00后+2.03%"},
    2: {"score": 0.60, "capital": 0.70, "require": ["P34_gap_strong"],
        "block": ["P36_overheat"], "label": "🥈高胜率(55-60%)", "bonus": 0.07,
        "desc": "资金门槛放宽,胜率55%+"},
    3: {"score": 0.60, "capital": 0.70, "require": ["P37_momentum_up"],
        "block": ["P36_overheat", "P35_short_pressure"], "label": "🥉备选(43%)", "bonus": 0.00,
        "desc": "P37_up兜底,反转日可能失效"},
}

# 加分信号（附加提权，不强制）
BONUS_SIGNALS = {
    "P33_margin_strong": 0.02,   # 融资猛买(胜率+21%)
    "P34_gap_strong":    0.03,   # 高开高走(王者)
    "P32_ratio_accel":   0.01,   # 占比加速
    "P35_short_cover":   0.01,   # 空头回补
}


def filter_overnight(date_str=None, top_n=20):
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
        import sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from portfolio.black_swan import BlackSwanDetector
        bs = BlackSwanDetector(date_str).check()
        if bs["level"] >= 2:
            print(f"\n  🚨 黑天鹅 Level {bs['level']} — 禁止买入\n")
            return []
    except Exception:
        pass

    candidates = []
    rejected = {"tier": 0}

    for r in rows:
        score = float(r.get("综合得分", 0) or 0)
        capital = float(r.get("资金得分", 0.5) or 0.5)
        signals = r.get("综合信号", "")

        # 逐级匹配
        matched_tier = 0
        for t in [1, 2, 3]:
            rules = TIERS[t]
            if (score >= rules["score"] and capital >= rules["capital"]
                    and all(s in signals for s in rules["require"])
                    and not any(s in signals for s in rules["block"])):
                matched_tier = t
                break

        if not matched_tier:
            rejected["tier"] += 1
            continue

        # 加分
        bonus = TIERS[matched_tier]["bonus"]
        for sig, weight in BONUS_SIGNALS.items():
            if sig in signals:
                bonus += weight

        priority = score * 0.6 + capital * 0.30 + bonus * 0.10

        candidates.append({
            "代码": r["代码"], "名称": r["名称"],
            "最新价": float(r.get("最新价", 0) or 0),
            "综合得分": score, "资金得分": capital,
            "涨跌幅": float(r.get("涨跌幅", 0) or 0),
            "换手率": float(r.get("换手率", 0) or 0),
            "行业": r.get("行业", ""),
            "级别": TIERS[matched_tier]["label"],
            "信号": signals,
            "_priority": round(priority, 4),
        })

    candidates.sort(key=lambda x: -x["_priority"])

    # 行业分散（同行业最多2只）
    from collections import Counter
    sector_count = Counter()
    diversified = []
    for c in candidates:
        ind = c["行业"]
        if sector_count[ind] >= 2:
            continue
        diversified.append(c)
        sector_count[ind] += 1

    # 输出
    print(f"\n{'='*65}")
    print(f"  隔夜套利筛选 — {date_str}")
    print(f"{'='*65}")
    print(f"  全市场: {len(rows)}只 → 三级通过: {len(candidates)}只 → 行业分散: {len(diversified)}只")
    print(f"  拒绝: {rejected['tier']}只 (未匹配任一级别)")

    if not diversified:
        print("\n  无候选股票")
        return []

    top = diversified[:top_n]
    tier_dist = Counter(c["级别"] for c in top)
    print(f"  级别分布: {dict(tier_dist)}")

    print(f"\n  {'':>3} {'代码':<8} {'名称':<8} {'优先':>6} {'综合':>6} {'资金':>6} "
          f"{'涨跌':>6} {'行业':<10} {'级别'}")
    print(f"  {'─'*68}")
    for i, c in enumerate(top):
        print(f"  {i+1:>2}. {c['代码']:<8} {c['名称']:<8s} "
              f"{c['_priority']:>6.3f} {c['综合得分']:>6.3f} {c['资金得分']:>6.3f} "
              f"{c['涨跌幅']:>+5.1f}% {c['行业']:<10s} {c['级别']}")

    if top:
        avg_score = sum(c["综合得分"] for c in top) / len(top)
        avg_cap = sum(c["资金得分"] for c in top) / len(top)
        avg_chg = sum(c["涨跌幅"] for c in top) / len(top)
        print(f"\n  均综合={avg_score:.3f} 均资金={avg_cap:.3f} 均涨跌={avg_chg:+.1f}%")
        print(f"\n  规则: 🥇分≥0.6+资≥0.8+P34_gap(57%,全天正收益)")
        print(f"        🥈分≥0.6+资≥0.7+P34_gap(55%)  🥉分≥0.6+资≥0.7+P37_up(兜底)")
        print(f"  避雷: P36_overheat P35_short_pressure | 同行业≤2只")

    # 保存
    out_path = os.path.join(RESEARCH_ROOT, date_str, "overnight_picks.csv")
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        fields = ["代码","名称","最新价","综合得分","资金得分",
                  "涨跌幅","换手率","行业","级别","_priority"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(top)
    print(f"  ✓ {out_path}")

    return top


if __name__ == "__main__":
    date_str = sys.argv[1] if len(sys.argv) > 1 else None
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    filter_overnight(date_str, top_n)
