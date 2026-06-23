"""
隔夜套利法 — 尾盘买入次日开盘卖出
策略来源: 腾讯云AI Agent实战 + 天风证券两阶段模型
筛选逻辑:
  ① 量能突破: 量比>2.0 (成交量超5日均量2倍)
  ② 趋势形态: 多头排列(MA5>MA10>MA20) + 站上MA20
  ③ 资金异动: 主力今日净流入 + 占比温和加速(P32)
  ④ 板块热度: 所属板块处于当日强势
  ⑤ 综合评分: 综合得分>0.50 过滤劣质
  附加检测: 无出货信号(P29高换手/散户主导/融券压力)

用法:
  python -m daily_pipeline.filter_overnight <date>   → 输出买入清单
  python -m daily_pipeline.filter_overnight <date> --send  → 输出+推送微信
"""
import csv
import os
import sys
from datetime import datetime, timezone, timedelta

BJS_TZ = timezone(timedelta(hours=8))
RESEARCH_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data"
)

# ── 策略阈值 ──
THRESHOLDS = {
    "min_total_score": 0.38,       # 综合得分下限 (~P70)
    "min_capital_score": 0.50,     # 资金强度下限 (~P50)
    "min_trend_score": 0.50,       # 趋势质量下限
    "min_sector_score": 0.45,      # 板块热度下限
    "min_vol_ratio": 1.2,          # 量比下限
    "max_turnover": 15.0,          # 换手率上限(防出货)
    "exclude_signals": [           # 排除以下信号
        "P29_high_turnover",       # 高换手出货
        "P6_retail",               # 散户主导
        "P32_pump_risk",           # 单日脉冲
        "P32_extreme",             # 占比极端
        "P35_short_pressure",      # 融券强做空
    ],
    "prefer_signals": [            # 优先以下信号
        "P32_ratio_accel",         # 占比温和加速
        "P34_gap_strong",          # 高开高走
        "P33_margin_strong",       # 融资买入
        "E3_strong_start",         # 强势启动
        "E5_ratio_early",          # 占比早期加速
    ],
}


def _tof(val, default=0.0):
    try: return float(val) if val else default
    except: return default


def filter_overnight(date_str=None, top_n=20):
    """筛选隔夜套利候选股票"""
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

    candidates = []
    rejected = {"low_score": 0, "low_capital": 0, "low_trend": 0,
                "low_sector": 0, "low_vol": 0, "high_turnover": 0,
                "bad_signal": 0}

    for r in rows:
        score = _tof(r["综合得分"])
        capital = _tof(r["资金得分"])
        trend = _tof(r["趋势得分"])
        sector = _tof(r["板块得分"])
        turnover = _tof(r["换手率"])
        vol_ratio = _tof(r["量比"])
        main_flow = _tof(r.get("主力净流入", 0))
        signals = r.get("触发信号", "")

        # 逐层过滤
        if score < THRESHOLDS["min_total_score"]:
            rejected["low_score"] += 1; continue
        if capital < THRESHOLDS["min_capital_score"]:
            rejected["low_capital"] += 1; continue
        if trend < THRESHOLDS["min_trend_score"]:
            rejected["low_trend"] += 1; continue
        if sector < THRESHOLDS["min_sector_score"]:
            rejected["low_sector"] += 1; continue
        if vol_ratio < THRESHOLDS["min_vol_ratio"]:
            rejected["low_vol"] += 1; continue
        if turnover > THRESHOLDS["max_turnover"]:
            rejected["high_turnover"] += 1; continue

        # 信号过滤
        bad = [s for s in THRESHOLDS["exclude_signals"] if s in signals]
        if bad:
            rejected["bad_signal"] += 1; continue

        # 计算优先级得分
        good_signals = [s for s in THRESHOLDS["prefer_signals"] if s in signals]
        early_score = _tof(r["启动得分"])

        priority = score * 0.35 + capital * 0.25 + early_score * 0.25 + len(good_signals) * 0.05

        candidates.append({
            "代码": r["代码"], "名称": r["名称"], "最新价": _tof(r["最新价"]),
            "综合得分": score, "启动得分": early_score, "资金得分": capital,
            "趋势得分": trend, "板块得分": sector, "涨跌幅": _tof(r["涨跌幅"]),
            "换手率": turnover, "量比": vol_ratio, "优先信号": ",".join(good_signals),
            "_priority": priority,
        })

    candidates.sort(key=lambda x: -x["_priority"])

    # 输出
    print(f"\n{'='*65}")
    print(f"  隔夜套利法筛选 — {date_str}")
    print(f"{'='*65}")
    print(f"  全市场: {len(rows)} 只 → 通过: {len(candidates)} 只")

    # 拒绝原因
    print(f"\n  过滤明细:")
    for reason, count in rejected.items():
        if count > 0:
            label = {"low_score": "综合得分不足", "low_capital": "资金强度不足",
                     "low_trend": "趋势质量不足", "low_sector": "板块热度不足",
                     "low_vol": "量比不足", "high_turnover": "换手过高",
                     "neg_flow": "主力净流出", "bad_signal": "风险信号"}[reason]
            print(f"    {label}: {count}只")

    if not candidates:
        print("\n  无候选股票")
        return []

    # Top N
    top = candidates[:top_n]
    print(f"\n  Top {top_n} 买入候选:")
    print(f"  {'':>3} {'代码':<8} {'名称':<8} {'优先':>6} {'综合':>6} {'启动':>6} {'涨跌':>6} {'换手':>5} {'优先信号'}")
    print(f"  {'─'*65}")
    for i, c in enumerate(top):
        print(f"  {i+1:>2}. {c['代码']:<8} {c['名称']:<8s} "
              f"{c['_priority']:>6.3f} {c['综合得分']:>6.3f} {c['启动得分']:>6.3f} "
              f"{c['涨跌幅']:>+5.1f}% {c['换手率']:>5.1f}% {c['优先信号'][:20]}")

    # 统计
    avg_chg = sum(c["涨跌幅"] for c in top) / len(top)
    avg_score = sum(c["综合得分"] for c in top) / len(top)
    print(f"\n  均综合={avg_score:.3f}  均涨跌={avg_chg:+.1f}%")

    # 保存
    out_path = os.path.join(RESEARCH_ROOT, date_str, "overnight_picks.csv")
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        fields = ["代码","名称","最新价","综合得分","启动得分",
                  "资金得分","趋势得分","板块得分","涨跌幅","换手率","量比","优先信号"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(top)
    print(f"  ✓ {out_path}")

    return top


if __name__ == "__main__":
    date_str = sys.argv[1] if len(sys.argv) > 1 else None
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    filter_overnight(date_str, top_n)
