"""
每日选股×板块 联合分析报告 → Markdown文件
用法: python -m daily_pipeline.analyze <date>             → 单日报告
      python -m daily_pipeline.analyze <date1> <date2>    → 双日对比报告
输出: research_data/<date>/report_<date>.md
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


def _bar(val, width=15):
    if val > 0: return "█" * max(1, int(val * width))
    return "▁" * max(1, int(abs(val) * width))


def run(date_str=None):
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    scores_path = os.path.join(RESEARCH_ROOT, date_str, "scores.csv")
    sectors_path = os.path.join(RESEARCH_ROOT, date_str, "sector_scores.csv")
    if not os.path.exists(scores_path):
        print(f"✗ 无评分数据: {date_str}"); return
    if not os.path.exists(sectors_path):
        print(f"✗ 无板块数据: {date_str}"); return

    scores = [r for r in csv.DictReader(open(scores_path, encoding="utf-8-sig"))]
    sectors = [r for r in csv.DictReader(open(sectors_path, encoding="utf-8-sig"))]
    scores.sort(key=lambda x: -float(x["综合得分"]))
    sec_map = {s["名称"]: s for s in sectors}
    n = len(scores)

    # 预计算
    all_total = [float(r["综合得分"]) for r in scores]
    avg_total = statistics.mean(all_total)
    good_n = sum(1 for r in scores if float(r["综合得分"]) >= 0.5)
    matched = sum(1 for r in scores if r["行业"] in sec_map)
    top_pct_n = max(1, int(n * 0.05))
    top_stocks_set = set(r["代码"] for r in scores[:top_pct_n])

    # 信号统计
    sig_count = Counter()
    for r in scores:
        for s in (r["综合信号"].split(",") if r["综合信号"] else []):
            if s: sig_count[s] += 1

    # ── 构建 Markdown ──
    lines = []
    w = lines.append

    w(f"# {date_str} 每日分析报告")
    w("")
    w(f"**{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}** | "
      f"个股 {n} 只 | 板块 {len(sectors)} 个 | "
      f"均综合分 {avg_total:.3f} | 买入候选 {good_n} 只({good_n/n*100:.0f}%)")
    w("")

    # ── 1. 市场概况 ──
    w("## 一、市场概况")
    w("")
    w("### 综合得分分布")
    w("")
    w("| 得分区间 | 数量 | 均涨跌 | 分布 |")
    w("|---------|------|--------|------|")
    bins = [(0,0.25),(0.25,0.30),(0.30,0.35),(0.35,0.40),(0.40,0.45),(0.45,0.50),(0.50,0.55),(0.55,1.0)]
    for lo, hi in bins:
        g = [r for r in scores if lo <= float(r["综合得分"]) < hi]
        if g:
            avg = statistics.mean(float(r["涨跌幅"]) for r in g)
            w(f"| {lo:.2f}-{hi:.2f} | {len(g)} | {avg:+.2f}% | {_bar(avg, 20)} |")

    w("")
    w("### 关键信号")
    w("")
    w("| 信号 | 数量 | 均涨跌 | 含义 |")
    w("|------|------|--------|------|")
    sig_names = {
        "P32_ratio_accel": "占比加速", "P32_pump_risk": "脉冲风险", "P32_extreme": "占比极端",
        "P37_momentum_up": "得分改善", "P37_momentum_down": "得分恶化",
        "P36_overheat": "全维过热", "P34_gap_strong": "高开高走",
        "P29_high_turnover": "高换手出货", "P_low_liquidity": "低流动性",
    }
    for sig, c in sig_count.most_common(6):
        affected = [r for r in scores if sig in r.get("综合信号", "")]
        avg = statistics.mean(float(r["涨跌幅"]) for r in affected) if affected else 0
        name = sig_names.get(sig, sig)
        w(f"| {name} | {c} | {avg:+.1f}% | {sig} |")

    # ── 2. 个股精选 ──
    w("")
    w("## 二、个股精选 Top20")
    w("")
    w("| # | 代码 | 名称 | 综合 | 启动 | 行业 | 板块# | 涨跌 | 换手 | 信号 |")
    w("|---|------|------|------|------|------|-------|------|------|------|")
    for i, r in enumerate(scores[:20]):
        sec = sec_map.get(r["行业"], {})
        sigs = r["综合信号"][:30] if r["综合信号"] else "-"
        w(f"| {i+1} | {r['代码']} | {r['名称']} | {r['综合得分']} | {r['启动得分']} | "
          f"{r['行业']} | #{sec.get('最新排名','?')} | {float(r['涨跌幅']):+.1f}% | "
          f"{float(r['换手率']):.1f}% | {sigs} |")

    # 启动得分 Top10
    w("")
    w("### 启动得分 Top10（刚启动检测）")
    w("")
    w("| # | 代码 | 名称 | 启动 | 综合 | 行业 | 涨跌 | 启动信号 |")
    w("|---|------|------|------|------|------|------|------|")
    by_early = sorted(scores, key=lambda x: -float(x["启动得分"]))
    for i, r in enumerate(by_early[:10]):
        early_sigs = r["启动信号"][:30] if r["启动信号"] else "-"
        w(f"| {i+1} | {r['代码']} | {r['名称']} | {r['启动得分']} | {r['综合得分']} | "
          f"{r['行业']} | {float(r['涨跌幅']):+.1f}% | {early_sigs} |")

    # ── 3. 行业板块 ──
    w("")
    w("## 三、行业板块")
    w("")
    w("### 板块评分 Top15")
    w("")
    ind_sectors = [s for s in sectors if s["类型"] == "行业"]
    w("| # | 板块 | 得分 | 排名 | 流入(亿) | 排名变化 | 正流比 | 跨日变化(亿) |")
    w("|---|------|------|------|----------|---------|--------|-------------|")
    for i, s in enumerate(sorted(ind_sectors, key=lambda x: -float(x["得分"]))[:15]):
        trend = "↑" if int(s.get("排名变化", 0)) > 0 else ("↓" if int(s.get("排名变化", 0)) < 0 else "→")
        cross = s.get("流入跨日变化(亿)", 0) or 0
        cross_str = f"{float(cross):+.1f}" if cross else "-"
        w(f"| {i+1} | {s['名称']} | {s['得分']} | #{s['最新排名']}{trend} | "
          f"{s['最新流入(亿)']} | {s.get('排名变化','-')} | {float(s.get('正流占比',0)):.0%} | {cross_str} |")

    # 板块排名→个股表现
    w("")
    w("### 板块排名 → 个股表现")
    w("")
    w("| 板块区间 | 板块数 | 个股数 | 均综合分 | 均涨跌 |")
    w("|---------|--------|--------|---------|--------|")
    for lo, hi, label in [(1,5,"Top5"),(6,15,"6-15"),(16,30,"16-30"),(31,60,"31-60"),(61,200,"61+")]:
        secs_in = [s for s in ind_sectors if lo <= int(s["最新排名"]) <= hi]
        all_s = []
        for s in secs_in:
            all_s.extend([r for r in scores if r["行业"] == s["名称"]])
        if all_s:
            w(f"| {label} | {len(secs_in)} | {len(all_s)} | "
              f"{statistics.mean(float(r['综合得分']) for r in all_s):.3f} | "
              f"{statistics.mean(float(r['涨跌幅']) for r in all_s):+.1f}% |")

    # 资金跨日异动
    w("")
    w("### 资金跨日异动")
    w("")
    rev = []
    for s in sectors:
        try:
            chg = float(s.get("流入跨日变化(亿)", 0))
            if abs(chg) > 10: rev.append((s["名称"], chg, float(s["得分"]), s.get("最新排名", 0)))
        except: pass
    rev.sort(key=lambda x: -abs(x[1]))
    w("| 板块 | 变化(亿) | 方向 | 板块得分 | 排名 |")
    w("|------|---------|------|---------|------|")
    for name, chg, sc, rank in rev[:8]:
        arrow = "🔥涌入" if chg > 0 else "⚠️撤退"
        w(f"| {name} | {chg:+.1f} | {arrow} | {sc:.3f} | #{rank} |")

    # ── 4. 概念板块 ──
    w("")
    w("## 四、概念板块 Top10")
    w("")
    concepts = sorted([s for s in sectors if s["类型"] == "概念"], key=lambda x: -float(x["得分"]))
    w("| # | 概念 | 得分 | 排名 | 流入(亿) | 正流比 |")
    w("|---|------|------|------|----------|--------|")
    for i, s in enumerate(concepts[:10]):
        w(f"| {i+1} | {s['名称']} | {s['得分']} | #{s['最新排名']} | "
          f"{s['最新流入(亿)']} | {float(s.get('正流占比',0)):.0%} |")

    # ── 5. 共振精选 ──
    w("")
    w("## 五、双高共振（板块热 + 个股强）")
    w("")
    w("> 板块排名Top15 ∩ 个股进Top5% → 最优标的")
    w("")
    w("| 板块 | 板块得分 | 板块排名 | 进Top5%个股数 | 代表个股 |")
    w("|------|---------|---------|------------|--------|")
    for s in sorted(ind_sectors, key=lambda x: -float(x["得分"]))[:15]:
        name = s["名称"]
        in_top = [r for r in scores if r["行业"] == name and r["代码"] in top_stocks_set]
        if in_top:
            reps = ", ".join(f"{r['代码']}({r['综合得分']})" for r in in_top[:3])
            w(f"| {name} | {s['得分']} | #{s['最新排名']} | {len(in_top)} | {reps} |")

    # ── 6. 总结 ──
    w("")
    w("## 六、总结")
    w("")
    top_sec = sorted(ind_sectors, key=lambda x: -float(x["得分"]))[0]
    top_con = ", ".join(f"{c['名称']}({c['得分']})" for c in concepts[:3])

    # 板块→个股相关系数
    v1 = [float(r["综合得分"]) for r in scores]
    v2 = [float(r["板块得分"]) for r in scores]
    n2 = len(v1); s1 = sum(v1); s2 = sum(v2)
    s11 = sum(x*x for x in v1); s22 = sum(y*y for y in v2); s12 = sum(x*y for x,y in zip(v1,v2))
    corr = (n2*s12-s1*s2)/((n2*s11-s1*s1)*(n2*s22-s2*s2))**0.5

    w(f"- **最热板块**: {top_sec['名称']}({top_sec['得分']}) #{top_sec['最新排名']}")
    w(f"- **概念主线**: {top_con}")
    w(f"- **买入候选**: {good_n}只 (得分≥0.5, {good_n/n*100:.0f}%)")
    w(f"- **行业匹配率**: {matched}/{n} ({matched/n*100:.0f}%)")
    w(f"- **板块→个股相关**: {corr:.3f} ({'强' if abs(corr)>0.3 else '弱'}相关)")
    w(f"- **得分单调性**: {'✅完美' if all_total[0] > avg_total else '正常'}")
    w(f"- **P37覆盖**: {sig_count.get('P37_momentum_up',0)+sig_count.get('P37_momentum_down',0)}只")

    # 保存
    report_path = os.path.join(RESEARCH_ROOT, date_str, f"report_{date_str}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"✓ {report_path}")
    print("\n".join(lines))


def compare(date1, date2):
    """双日对比报告"""
    def _load(date_str):
        sp = os.path.join(RESEARCH_ROOT, date_str, "scores.csv")
        cp = os.path.join(RESEARCH_ROOT, date_str, "sector_scores.csv")
        if not os.path.exists(sp) or not os.path.exists(cp): return None, None
        sc = [r for r in csv.DictReader(open(sp, encoding="utf-8-sig"))]
        se = [r for r in csv.DictReader(open(cp, encoding="utf-8-sig"))]
        return sc, se

    s1, c1 = _load(date1)
    s2, c2 = _load(date2)
    if not s1 or not s2:
        print("✗ 数据不全"); return

    s1.sort(key=lambda x: -float(x["综合得分"]))
    s2.sort(key=lambda x: -float(x["综合得分"]))
    sec_map1 = {s["名称"]: s for s in c1}
    sec_map2 = {s["名称"]: s for s in c2}
    sec1_score = {s["名称"]: float(s["得分"]) for s in c1}
    sec2_score = {s["名称"]: float(s["得分"]) for s in c2}

    lines = []
    w = lines.append
    w(f"# {date1} vs {date2} 对比分析报告")
    w("")
    w(f"**{date1}**: {len(s1)}只 {len(c1)}板块 | **{date2}**: {len(s2)}只 {len(c2)}板块")
    w("")

    # 1. 市场对比
    w("## 一、市场对比")
    w("")
    w("| 指标 | {date1} | {date2} | 变化 |")
    w("|------|--------|--------|------|")
    avg1 = statistics.mean(float(r["综合得分"]) for r in s1)
    avg2 = statistics.mean(float(r["综合得分"]) for r in s2)
    chg1 = statistics.mean(float(r["涨跌幅"]) for r in s1)
    chg2 = statistics.mean(float(r["涨跌幅"]) for r in s2)
    good1 = sum(1 for r in s1 if float(r["综合得分"]) >= 0.5)
    good2 = sum(1 for r in s2 if float(r["综合得分"]) >= 0.5)
    w(f"| 均综合分 | {avg1:.3f} | {avg2:.3f} | {avg2-avg1:+.3f} |")
    w(f"| 均涨跌 | {chg1:+.1f}% | {chg2:+.1f}% | {chg2-chg1:+.1f}% |")
    w(f"| 买入候选(≥0.5) | {good1}({good1/len(s1)*100:.0f}%) | {good2}({good2/len(s2)*100:.0f}%) | {good2-good1:+d} |")

    # 2. 板块对比
    w("")
    w("## 二、板块得分对比")
    w("")
    w("| 板块 | {date1}得分 | {date2}得分 | 变化 | 趋势 |")
    w("|------|-----------|-----------|------|------|")
    all_secs = set(sec1_score.keys()) | set(sec2_score.keys())
    changes = []
    for name in all_secs:
        sc1 = sec1_score.get(name, 0.5)
        sc2 = sec2_score.get(name, 0.5)
        changes.append((name, sc1, sc2, sc2 - sc1))
    changes.sort(key=lambda x: -abs(x[3]))
    for name, sc1, sc2, diff in changes[:15]:
        arrow = "🔥↑" if diff > 0.05 else ("⚠↓" if diff < -0.05 else "→")
        w(f"| {name} | {sc1:.3f} | {sc2:.3f} | {diff:+.3f} | {arrow} |")

    # 3. 行业资金跨日对比
    w("")
    w("## 三、行业资金跨日对比")
    w("")
    cross_flow = []
    for s in c2:
        name = s["名称"]
        flow2 = float(s.get("最新流入(亿)", 0) or 0)
        # 找昨日同板块
        prev = next((x for x in c1 if x["名称"] == name), None)
        flow1 = float(prev.get("最新流入(亿)", 0) or 0) if prev else 0
        if abs(flow2 - flow1) > 5:
            cross_flow.append((name, flow1, flow2, flow2 - flow1))
    cross_flow.sort(key=lambda x: -abs(x[3]))
    w("| 板块 | {date1}流入 | {date2}流入 | 变化 | 方向 |")
    w("|------|----------|----------|------|------|")
    for name, f1, f2, diff in cross_flow[:12]:
        arrow = "🔥涌入" if diff > 0 else "⚠️撤退"
        w(f"| {name} | {f1:+.1f}亿 | {f2:+.1f}亿 | {diff:+.1f}亿 | {arrow} |")

    # 4. 概念对比
    w("")
    w("## 四、概念主线切换")
    w("")
    con1 = sorted([s for s in c1 if s["类型"]=="概念"], key=lambda x: -float(x["得分"]))
    con2 = sorted([s for s in c2 if s["类型"]=="概念"], key=lambda x: -float(x["得分"]))
    w(f"**{date1}**: " + " → ".join(f"{c['名称']}({c['得分']})" for c in con1[:5]))
    w("")
    w(f"**{date2}**: " + " → ".join(f"{c['名称']}({c['得分']})" for c in con2[:5]))

    # 5. 持续高分个股
    w("")
    w("## 五、两日持续高分个股")
    w("")
    top200_1 = set(r["代码"] for r in s1[:200])
    top200_2 = set(r["代码"] for r in s2[:200])
    persistent = top200_1 & top200_2
    w(f"两日均在Top200: **{len(persistent)}只**")
    w("")
    w("| 代码 | 名称 | {date1}得分 | {date2}得分 | 变化 |")
    w("|------|------|-----------|-----------|------|")
    score_map1 = {r["代码"]: float(r["综合得分"]) for r in s1}
    score_map2 = {r["代码"]: float(r["综合得分"]) for r in s2}
    ps = [(c, score_map1[c], score_map2[c], score_map2[c]-score_map1[c]) for c in persistent]
    ps.sort(key=lambda x: -x[3])
    for code, sc1, sc2, diff in ps[:15]:
        name = next((r["名称"] for r in s2 if r["代码"]==code), code)
        arrow = "↑" if diff > 0 else "↓"
        w(f"| {code} | {name} | {sc1:.3f} | {sc2:.3f} | {diff:+.3f} {arrow} |")

    # 6. 总结
    w("")
    w("## 六、总结")
    w("")
    new_hot = [name for name,_,_,diff in changes[:10] if diff > 0.05]
    fading = [name for name,_,_,diff in changes[:10] if diff < -0.05]
    w(f"- **板块升温**: {', '.join(new_hot[:5]) if new_hot else '无'}")
    w(f"- **板块降温**: {', '.join(fading[:5]) if fading else '无'}")
    w(f"- **持续高分**: {len(persistent)}只连续两日Top200")
    w(f"- **概念切换**: {con2[0]['名称'] if con2 else '?'}替代{con1[0]['名称'] if con1 else '?'}成为新主线")

    report_path = os.path.join(RESEARCH_ROOT, date2, f"report_compare_{date1}_{date2}.md")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"✓ {report_path}")
    print("\n".join(lines))


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        compare(sys.argv[1], sys.argv[2])
    else:
        date_str = sys.argv[1] if len(sys.argv) > 1 else None
        run(date_str)
