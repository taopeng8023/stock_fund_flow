"""
买入引擎 — 基于评分结果 + 信号过滤器输出买入推荐

强制执行规则（16503笔回测验证）:
  必备信号: P37_momentum_up (Top50中80%携带, 胜率+11%)
  加分信号: P34_gap_strong (胜率+19%, 均收益+0.81%)
  避雷信号: P36_overheat (胜率-8%), P35_short_pressure (Bot50中38%携带)
  黑天鹅门禁: Level ≥ 2 → 禁止买入
  得分门槛: 综合得分 ≥ 0.65 (胜率37% vs 全量25%)
  行业分散: 同行业最多2只

用法:
  python -m portfolio.buy_engine --date=20260624
  python -m portfolio.buy_engine --date=20260624 --top=5
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

# ── 买入规则（回测驱动）──
REQUIRED_SIGNALS = ["P37_momentum_up"]      # 必须携带
BONUS_SIGNALS = ["P34_gap_strong"]           # 加分信号
BLOCK_SIGNALS = ["P36_overheat", "P35_short_pressure"]  # 一票否决
SCORE_CUTOFF = 0.65                           # 最低得分
MAX_PER_SECTOR = 2                            # 同行业上限


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


def filter_candidates(scores: list) -> list:
    """筛选买入候选。

    流程:
      1. 基础得分门槛 ≥ SCORE_CUTOFF
      2. 必须携带 P37_momentum_up
      3. 不能携带 P36_overheat / P35_short_pressure
      4. 加分信号 P34_gap_strong 提权
    """
    candidates = []
    for r in scores:
        score = float(r.get("综合得分", 0) or 0)
        if score < SCORE_CUTOFF:
            continue

        signals = r.get("综合信号", "")
        # 必备检查
        if not all(s in signals for s in REQUIRED_SIGNALS):
            continue
        # 避雷检查
        if any(s in signals for s in BLOCK_SIGNALS):
            continue

        # 加分信号提权
        bonus = 0
        for s in BONUS_SIGNALS:
            if s in signals:
                bonus += 0.02

        adjusted_score = score + bonus

        candidates.append({
            "code": r["代码"],
            "name": r["名称"],
            "score": round(adjusted_score, 4),
            "raw_score": score,
            "chg_pct": round(float(r.get("涨跌幅", 0) or 0), 2),
            "price": round(float(r.get("最新价", 0) or 0), 2),
            "turnover": round(float(r.get("换手率", 0) or 0), 1),
            "industry": r.get("行业", ""),
            "signals": signals,
            "capital": float(r.get("资金得分", 0.5)),
            "trend": float(r.get("趋势得分", 0.5)),
            "position": float(r.get("位置得分", 0.5)),
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
                             no_notify: bool = False) -> dict:
    """生成买入推荐。"""
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
    candidates = filter_candidates(scores)
    print(f"  全量评分: {len(scores)} 只 → 通过过滤: {len(candidates)} 只"
          f" (≥{SCORE_CUTOFF}+P37_up+P34加分−风险信号)")

    # 行业分散
    buys = apply_sector_diversity(candidates, MAX_PER_SECTOR)[:top_n]
    sector_dist = Counter(b["industry"] for b in buys)

    print(f"  行业分散后: {len(buys)} 只 (同行业≤{MAX_PER_SECTOR})")
    print(f"  行业分布: {dict(sector_dist)}")

    # 输出
    _print_recommendations(date_str, bs_level, buys)

    if not no_notify and buys:
        _notify_buys(date_str, bs_level, buys)

    return {"date": date_str, "blocked": False, "bs_level": bs_level,
            "candidates": len(candidates), "buys": buys}


def _print_recommendations(date_str, bs_level, buys):
    print(f"\n{'='*65}")
    print(f"  🎯 买入推荐 [{date_str}]  BS Level {bs_level}")
    print(f"{'='*65}")

    if not buys:
        print("  (无符合条件的买入标的)")
        return

    print(f"\n  {'':<3s} {'代码':<8s} {'名称':<8s} {'得分':<8s} {'涨跌':<7s} "
          f"{'资金':<6s} {'行业':<12s} {'信号'}")
    print(f"  {'─'*65}")
    for i, b in enumerate(buys):
        signals_short = []
        for s in ["P37_up", "P34_gap", "P32_accel"]:
            s_map = {"P37_up": "P37_momentum_up",
                     "P34_gap": "P34_gap_strong",
                     "P32_accel": "P32_ratio_accel"}
            if s_map.get(s, s) in b["signals"]:
                signals_short.append(s)
        sig_str = ",".join(signals_short)
        print(f"  {i+1:<2} {b['code']:<8s} {b['name']:<8s} {b['score']:.4f} "
              f"{b['chg_pct']:+.1f}%{'':>2s} {b['capital']:.2f}{'':>2s} "
              f"{b['industry']:<12s} {sig_str}")

    print(f"\n  规则: 得分≥{SCORE_CUTOFF} + P37_up必备 + P36/P35避开 + 同行业≤{MAX_PER_SECTOR}只")


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
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args()

    generate_recommendations(args.date, top_n=args.top,
                             no_notify=args.no_notify)


if __name__ == "__main__":
    main()
