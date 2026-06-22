"""
步骤4: 累积回测分析 — 读取 summary.csv，输出因子优化建议
"""
import csv
import os
import statistics
import math

RESEARCH_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data"
)


def load_summary():
    """读取累积汇总 CSV"""
    path = os.path.join(RESEARCH_ROOT, "backtest", "summary.csv")
    if not os.path.exists(path): return []
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def analyze():
    """分析累积回测结果，输出优化建议"""
    rows = load_summary()
    if not rows:
        print("无累积回测数据")
        return

    print(f"\n{'='*60}")
    print(f"  累积回测分析 ({len(rows)} 个交易日)")
    print(f"{'='*60}")

    # ── 汇总指标 ──
    top50_rets = [float(r["Top50均收益"]) for r in rows]
    rand50_rets = [float(r["随机50均收益"]) for r in rows]
    win_rates = [float(r["Top50胜率"]) for r in rows]
    factor_edges = [float(r["因子区分度"]) for r in rows]
    d1_rets = [float(r["D1均收益"]) for r in rows]
    d10_rets = [float(r["D10均收益"]) for r in rows]

    avg_top50 = statistics.mean(top50_rets)
    avg_rand50 = statistics.mean(rand50_rets)
    avg_wr = statistics.mean(win_rates)
    avg_edge = statistics.mean(factor_edges)

    # 累计收益
    cum_top = 1.0; cum_rand = 1.0
    for r in rows:
        cum_top *= 1 + float(r["Top50均收益"]) / 100
        cum_rand *= 1 + float(r["随机50均收益"]) / 100

    print(f"\n  核心指标:")
    print(f"    Top50 日均收益: {avg_top50:+.3f}%")
    print(f"    随机50日均收益: {avg_rand50:+.3f}%")
    print(f"    Top50 超额:     {avg_top50 - avg_rand50:+.3f}%")
    print(f"    Top50 均胜率:   {avg_wr:.1%}")
    print(f"    均因子区分度:   {avg_edge:+.3f}%")
    print(f"    Top50 累计:     {cum_top-1:+.2%}")
    print(f"    随机50 累计:    {cum_rand-1:+.2%}")

    # 胜率稳定性
    wr_std = statistics.stdev(win_rates) if len(win_rates) > 1 else 0
    up_days = sum(1 for r in top50_rets if r > 0)
    print(f"\n  稳定性:")
    print(f"    Top50 胜率标准差: {wr_std:.3f}")
    print(f"    Top50 正收益天数: {up_days}/{len(rows)} ({up_days/len(rows):.0%})")

    # 十分位衰减
    if d1_rets and d10_rets:
        avg_d1 = statistics.mean(d1_rets)
        avg_d10 = statistics.mean(d10_rets)
        print(f"\n  十分位:")
        print(f"    D1 (最高分) 均收益: {avg_d1:+.3f}%")
        print(f"    D10 (最低分) 均收益: {avg_d10:+.3f}%")
        print(f"    D1-D10 差值: {avg_d1 - avg_d10:+.3f}%")

    # ── 权重优化建议 ──
    print(f"\n{'─'*60}")
    print(f"  优化建议")
    print(f"{'─'*60}")

    suggestions = []

    if len(rows) >= 5:
        # 1. 因子区分度趋势
        recent_edge = statistics.mean(factor_edges[-5:]) if len(factor_edges) >= 5 else avg_edge
        if recent_edge < avg_edge * 0.5:
            suggestions.append("⚠️ 近期因子区分度下降，考虑增加资金流因子权重")
        elif recent_edge > avg_edge * 1.5:
            suggestions.append("✅ 近期因子区分度上升，当前权重有效")

        # 2. Top50 vs 随机
        if avg_top50 > avg_rand50 + 0.1:
            suggestions.append("✅ 模型持续跑赢随机，策略有效")
        elif avg_top50 < avg_rand50:
            suggestions.append("❌ 模型跑输随机！需要重大调整")

        # 3. 胜率建议
        if avg_wr < 0.48:
            suggestions.append("⚠️ 胜率偏低(<48%)，考虑增加防守型因子(位置/分析师)")
        elif avg_wr > 0.55:
            suggestions.append("✅ 胜率良好(>55%)，可适当增加进攻型因子(资金/趋势)")

        # 4. D1 vs D10
        if avg_d1 < avg_d10:
            suggestions.append("❌ D10收益高于D1！评分逻辑可能倒置，检查position/trend因子")

        # 5. 权重微调建议
        suggestions.append(f"💡 当前累积 {len(rows)} 天，{'>' if len(rows) >= 20 else '还需'}{20-len(rows)} 天后可做因子IC分析")

    for s in suggestions:
        print(f"  {s}")

    return {
        "days": len(rows), "avg_top50": avg_top50, "avg_rand50": avg_rand50,
        "avg_wr": avg_wr, "avg_edge": avg_edge, "cum_top": cum_top - 1,
        "cum_rand": cum_rand - 1, "suggestions": suggestions,
    }


if __name__ == "__main__":
    analyze()
