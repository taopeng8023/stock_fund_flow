"""
买入引擎 — 三级买入规则（16503笔回测驱动）

Tier 1 (🥇 57%胜率): score≥0.60 + capital≥0.80 + P34_gap − P36_overheat
Tier 2 (🥈 43%胜率): score≥0.60 + capital≥0.70 + P37_up − P36_overheat
Tier 3 (🥉 34%胜率): score≥0.55 + capital≥0.60 + P37_up − P36_overheat/P35_short

避雷: P36_overheat(胜率-8%), P35_short_pressure(Bot50专属)
门禁: 黑天鹅 Level ≥ 2 → 禁止一切买入
行业: 同行业 ≤ 2只

用法:
  python -m portfolio.buy_engine --date=20260624
  python -m portfolio.buy_engine --date=20260624 --top=5 --tier=1
  python -m portfolio.buy_engine --date=20260624 --no-notify
"""

import argparse
import csv
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
RESEARCH_ROOT = PROJECT_ROOT / "research_data"

# ── 三级买入规则 ──
TIERS = {
    1: {"score": 0.60, "capital": 0.80, "require": ["P34_gap_strong"],
        "block": ["P36_overheat"], "label": "🥇 高确定性"},
    2: {"score": 0.60, "capital": 0.70, "require": ["P37_momentum_up"],
        "block": ["P36_overheat"], "label": "🥈 中确定性"},
    3: {"score": 0.55, "capital": 0.60, "require": ["P37_momentum_up"],
        "block": ["P36_overheat", "P35_short_pressure"], "label": "🥉 基础池"},
}

MAX_PER_SECTOR = 2


def load_scores(date_str: str) -> list:
    path = RESEARCH_ROOT / date_str / "scores.csv"
    if not path.exists():
        print(f"❌ {date_str} 无评分数据，请先运行评分")
        sys.exit(1)
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def check_black_swan(date_str: str) -> tuple:
    """返回 (level, blocked)"""
    try:
        from portfolio.black_swan import BlackSwanDetector
        bs = BlackSwanDetector(date_str).check()
        level = bs["level"]
        blocked = level >= 2
        return level, blocked
    except Exception:
        return 0, False


def filter_candidates(scores: list, tier: int = 1) -> list:
    """三级筛选买入候选。

    Tier 1: score≥0.60 + capital≥0.80 + P34_gap − overheat → 57%胜率
    Tier 2: score≥0.60 + capital≥0.70 + P37_up − overheat → 43%胜率
    Tier 3: score≥0.55 + capital≥0.60 + P37_up − overheat/P35 → 34%胜率

    Returns list sorted by composite score desc.
    """
    rules = TIERS.get(tier, TIERS[1])
    candidates = []

    for r in scores:
        score = float(r.get("综合得分", 0) or 0)
        if score < rules["score"]:
            continue

        capital = float(r.get("资金得分", 0.5) or 0.5)
        if capital < rules["capital"]:
            continue

        signals = r.get("综合信号", "")
        # 必备信号
        if not all(s in signals for s in rules["require"]):
            continue
        # 避雷信号
        if any(s in signals for s in rules["block"]):
            continue

        candidates.append({
            "code": r["代码"],
            "name": r["名称"],
            "score": round(score, 4),
            "tier": tier,
            "tier_label": rules["label"],
            "chg_pct": round(float(r.get("涨跌幅", 0) or 0), 2),
            "price": round(float(r.get("最新价", 0) or 0), 2),
            "industry": r.get("行业", ""),
            "signals": signals,
            "capital": capital,
            "mcap_yi": round(float(r.get("总市值", 0) or 0), 1),
        })

    candidates.sort(key=lambda x: -x["score"])
    return candidates


def apply_sector_diversity(candidates: list, max_n: int) -> list:
    """行业分散硬约束：同行业最多 max_n 只。"""
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
                             tier: int = 0, no_notify: bool = False) -> dict:
    """生成买入推荐。tier=0 表示合并所有三级。"""
    scores = load_scores(date_str)

    # 黑天鹅门禁
    bs_level, blocked = check_black_swan(date_str)
    if blocked:
        print(f"\n{'='*60}")
        print(f"  🚨 黑天鹅 Level {bs_level} — 禁止买入")
        print(f"{'='*60}")
        return {"date": date_str, "blocked": True, "bs_level": bs_level,
                "buys": [], "reason": f"黑天鹅 Level {bs_level}"}

    # 筛选
    if tier > 0:
        candidates = filter_candidates(scores, tier)
    else:
        # 合并三级
        candidates = []
        seen = set()
        for t in [1, 2, 3]:
            for c in filter_candidates(scores, t):
                if c["code"] not in seen:
                    candidates.append(c)
                    seen.add(c["code"])
        candidates.sort(key=lambda x: -x["score"])

    print(f"  全量: {len(scores)}只 → 候选: {len(candidates)}只")

    # 行业分散
    buys = apply_sector_diversity(candidates, MAX_PER_SECTOR)[:top_n]
    sector_dist = Counter(b["industry"] for b in buys)

    print(f"  行业≤{MAX_PER_SECTOR}: {len(buys)}只 分布: {dict(sector_dist)}")

    # 输出
    _print_recommendations(date_str, bs_level, buys)

    if not no_notify and buys:
        _notify_buys(date_str, bs_level, buys)

    return {"date": date_str, "blocked": False, "bs_level": bs_level,
            "candidates": len(candidates), "buys": buys}


def _print_recommendations(date_str, bs_level, buys):
    print(f"\n{'='*70}")
    print(f"  🎯 买入推荐 [{date_str}]  BS Level {bs_level}")
    print(f"{'='*70}")

    if not buys:
        print("  (无符合条件的买入标的)")
        return

    print(f"\n  {'':<3s} {'代码':<8s} {'名称':<8s} {'得分':<8s} {'涨跌':<7s} "
          f"{'资金':<6s} {'行业':<10s} {'级别':<10s} {'信号'}")
    print(f"  {'─'*68}")
    for i, b in enumerate(buys):
        sig_short = []
        if "P34_gap_strong" in b["signals"]: sig_short.append("GAP↑")
        if "P37_momentum_up" in b["signals"]: sig_short.append("MOM↑")
        if "P32_ratio_accel" in b["signals"]: sig_short.append("ACC↑")
        sig_str = ",".join(sig_short)
        tier_label = b.get("tier_label", "")
        print(f"  {i+1:<2} {b['code']:<8s} {b['name']:<8s} {b['score']:.4f} "
              f"{b['chg_pct']:+.1f}%{'':>2s} {b['capital']:.2f}{'':>2s} "
              f"{b['industry']:<10s} {tier_label:<10s} {sig_str}")

    print(f"\n  规则: 🥇分≥0.6+资≥0.8+P34_gap  🥈分≥0.6+资≥0.7+P37_up  🥉分≥0.55+资≥0.6+P37_up")
    print(f"  避雷: P36_overheat P35_short_pressure | 同行业≤{MAX_PER_SECTOR}只")


def _notify_buys(date_str, bs_level, buys):
    try:
        from notify.wecom_sender import send_markdown
    except ImportError:
        return

    lines = [f"## 🎯 买入推荐 — {date_str}"]
    lines.append(f"> 黑天鹅 Level {bs_level} | 推荐 {len(buys)} 只")
    lines.append("")

    for i, b in enumerate(buys[:8]):
        lines.append(f"**{i+1}. {b['name']}**（{b['code']}）")
        lines.append(f"> 得分 {b['score']:.3f} | 涨跌 {b['chg_pct']:+.1f}% | "
                     f"资金 {b['capital']:.2f} | {b['industry']}")
        lines.append("")

    send_markdown("\n".join(lines))
    print("  📤 已推送企业微信")


def main():
    parser = argparse.ArgumentParser(description="买入推荐引擎")
    parser.add_argument("--date", required=True)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--tier", type=int, default=0, help="1=高确定性 2=中 3=基础 0=合并全部")
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args()

    generate_recommendations(args.date, top_n=args.top, tier=args.tier,
                             no_notify=args.no_notify)


if __name__ == "__main__":
    main()
