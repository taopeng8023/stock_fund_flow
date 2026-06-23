"""
每日选股×板块 联合分析报告
用法: python -m daily_pipeline.analyze <date>
      python -m daily_pipeline.analyze 20260623
"""
import csv
import os
import sys
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

BJS_TZ = timezone(timedelta(hours=8))
RESEARCH_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data"
)


def run(date_str=None):
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    # 加载数据
    scores_path = os.path.join(RESEARCH_ROOT, date_str, "scores.csv")
    sectors_path = os.path.join(RESEARCH_ROOT, date_str, "sector_scores.csv")

    if not os.path.exists(scores_path):
        print(f"✗ 无评分数据: {date_str}")
        return
    if not os.path.exists(sectors_path):
        print(f"✗ 无板块数据: {date_str}")
        return

    scores = []
    with open(scores_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            scores.append(r)
    sectors = []
    with open(sectors_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            sectors.append(r)

    scores.sort(key=lambda x: -float(x["综合得分"]))
    sec_map = {s["名称"]: s for s in sectors}
    n = len(scores)

    # ═══════════════════════════════════════
    print(f"\n{'='*65}")
    print(f"  {date_str} 个股×板块 联合分析报告")
    print(f"{'='*65}")
    print(f"  个股: {n}只  板块: {len(sectors)}个  均综合分: "
          f"{statistics.mean(float(r['综合得分']) for r in scores):.3f}")

    # ── 1. 得分分布 ──
    print(f"\n── 1. 综合得分分布 ──")
    bins = [(0, 0.25), (0.25, 0.30), (0.30, 0.35), (0.35, 0.40),
            (0.40, 0.45), (0.45, 0.50), (0.50, 0.55), (0.55, 1.0)]
    for lo, hi in bins:
        g = [r for r in scores if lo <= float(r["综合得分"]) < hi]
        if g:
            avg = statistics.mean(float(r["涨跌幅"]) for r in g)
            bar = "█" * max(1, int(avg * 15)) if avg > 0 else "▁" * max(1, int(abs(avg) * 15))
            print(f"  [{lo:.2f}-{hi:.2f}): {len(g):>5}只  均涨跌={avg:+.2f}%  {bar}")

    # ── 2. Top10 个股 ──
    print(f"\n── 2. Top10 个股 ──")
    for i, r in enumerate(scores[:10]):
        ind = r["行业"]
        sec = sec_map.get(ind, {})
        print(f"  {i+1:>2}. {r['代码']} {r['名称']:<6s} 综合={r['综合得分']} "
              f"启动={r['启动得分']} 行业={ind:<8s} "
              f"板块#{sec.get('最新排名','?')} 涨={float(r['涨跌幅']):+.1f}%")

    # ── 3. Top50 行业分布 ──
    print(f"\n── 3. Top50 行业分布 ──")
    ind50 = Counter(r["行业"] for r in scores[:50])
    for ind, c in ind50.most_common(8):
        sec = sec_map.get(ind, {})
        stocks_in = [r for r in scores[:50] if r["行业"] == ind]
        avg_s = statistics.mean(float(r["综合得分"]) for r in stocks_in)
        print(f"  {ind:<10s}: {c}只  均综合={avg_s:.3f}  "
              f"板块#{sec.get('最新排名','?')} {sec.get('得分','?')}")

    # ── 4. 板块排名 → 个股质量 ──
    print(f"\n── 4. 板块排名区间 → 个股表现 ──")
    for lo, hi, label in [(1, 5, "Top5"), (6, 15, "6-15"),
                           (16, 30, "16-30"), (31, 60, "31-60"), (61, 200, "61+")]:
        secs_in = [s for s in sectors if s["类型"] == "行业"
                   and lo <= int(s["最新排名"]) <= hi]
        all_s = []
        for s in secs_in:
            all_s.extend([r for r in scores if r["行业"] == s["名称"]])
        if all_s:
            avg_sc = statistics.mean(float(r["综合得分"]) for r in all_s)
            avg_chg = statistics.mean(float(r["涨跌幅"]) for r in all_s)
            print(f"  {label}: {len(secs_in)}板块 {len(all_s)}股  "
                  f"均综合={avg_sc:.3f}  均涨={avg_chg:+.1f}%")

    # ── 5. 板块×个股双高共振 ──
    print(f"\n── 5. 双高共振 (板块Top10 ∩ 个股Top5%) ──")
    top_pct = max(1, int(n * 0.05))
    top_stocks = set(r["代码"] for r in scores[:top_pct])
    for s in sorted(sectors, key=lambda x: -float(x["得分"]))[:10]:
        if s["类型"] != "行业":
            continue
        name = s["名称"]
        in_top = [r for r in scores if r["行业"] == name and r["代码"] in top_stocks]
        if in_top:
            print(f"  {name}(板块#{s['最新排名']} {s['得分']}): "
                  f"{len(in_top)}只进Top{top_pct}({len(in_top)/top_pct*100:.0f}%)")

    # ── 6. 板块资金跨日异动 ──
    print(f"\n── 6. 板块资金跨日异动 ──")
    rev = []
    for s in sectors:
        try:
            chg = float(s.get("流入跨日变化(亿)", 0))
            if abs(chg) > 10:
                rev.append((s["名称"], chg, s.get("得分", 0)))
        except:
            pass
    rev.sort(key=lambda x: -abs(x[1]))
    for name, chg, sc in rev[:6]:
        stocks_in = [r for r in scores if r["行业"] == name]
        avg_chg = statistics.mean(float(r["涨跌幅"]) for r in stocks_in) if stocks_in else 0
        arrow = "🔥涌入" if chg > 0 else "⚠️撤退"
        print(f"  {name}: {chg:+.1f}亿 {arrow}  板块{float(sc):.3f}  "
              f"个股{len(stocks_in)}只 均涨{avg_chg:+.1f}%")

    # ── 7. 概念板块热度 ──
    print(f"\n── 7. 高分概念板块 ──")
    concepts = sorted([s for s in sectors if s["类型"] == "概念"],
                      key=lambda x: -float(x["得分"]))
    for s in concepts[:6]:
        print(f"  {s['名称']}: {s['得分']} #{s['最新排名']}  "
              f"流入{s['最新流入(亿)']}亿 正流{float(s.get('正流占比',0)):.0%}")

    # ── 8. 信号分布 ──
    print(f"\n── 8. 信号分布 ──")
    sig_count = Counter()
    for r in scores:
        for s in (r["综合信号"].split(",") if r["综合信号"] else []):
            if s:
                sig_count[s] += 1
    for sig, c in sig_count.most_common(8):
        affected = [r for r in scores if sig in r.get("综合信号", "")]
        avg_ret = statistics.mean(float(r["涨跌幅"]) for r in affected) if affected else 0
        print(f"  {sig}: {c}只  均涨={avg_ret:+.1f}%")

    # ── 9. 综合 vs 板块 相关性 ──
    v1 = [float(r["综合得分"]) for r in scores]
    v2 = [float(r["板块得分"]) for r in scores]
    n2 = len(v1)
    s1 = sum(v1); s2 = sum(v2)
    s11 = sum(x * x for x in v1); s22 = sum(y * y for y in v2)
    s12 = sum(x * y for x, y in zip(v1, v2))
    corr = (n2 * s12 - s1 * s2) / ((n2 * s11 - s1 * s1) * (n2 * s22 - s2 * s2)) ** 0.5

    print(f"\n── 9. 综合得分 vs 板块得分 相关系数 ──")
    print(f"  corr = {corr:.3f}  ", end="")
    if corr > 0.3:
        print("(板块热度与个股质量正相关)")
    elif corr > 0.1:
        print("(弱正相关, 个股受板块影响有限)")
    else:
        print("(几乎独立, 个股选股不看板块)")

    # ── 10. 总结 ──
    print(f"\n{'='*65}")
    print(f"  总结")
    print(f"{'='*65}")

    # 最热板块
    top_sec = sorted(sectors, key=lambda x: -float(x["得分"]))[0]
    print(f"  最热板块: {top_sec['名称']}({top_sec['得分']}) #{top_sec['最新排名']}")

    # 双高共振最佳
    best_resonance = []
    for s in sorted(sectors, key=lambda x: -float(x["得分"]))[:10]:
        if s["类型"] != "行业": continue
        cnt = len([r for r in scores if r["行业"] == s["名称"] and r["代码"] in top_stocks])
        if cnt > 0:
            best_resonance.append((s["名称"], cnt, float(s["得分"])))
    if best_resonance:
        best = max(best_resonance, key=lambda x: x[1])
        print(f"  最佳共振: {best[0]}(板块{best[2]:.3f}, {best[1]}只进Top5%)")

    # 资金异动最显著
    if rev:
        biggest = rev[0]
        print(f"  最大异动: {biggest[0]} {biggest[1]:+.1f}亿")

    # 概念主线
    if concepts:
        top_concepts = ", ".join(f"{c['名称']}({c['得分']})" for c in concepts[:3])
        print(f"  概念主线: {top_concepts}")

    # 得分≥0.5
    good_n = sum(1 for r in scores if float(r["综合得分"]) >= 0.5)
    print(f"  买入候选: {good_n}只(得分≥0.5, {good_n/n*100:.0f}%)")

    # 个股→板块匹配率
    matched = sum(1 for r in scores if r["行业"] in sec_map)
    print(f"  行业匹配: {matched}/{n} ({matched/n*100:.0f}%)")

    print()


if __name__ == "__main__":
    date_str = sys.argv[1] if len(sys.argv) > 1 else None
    run(date_str)
