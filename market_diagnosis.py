"""
盘面诊断系统 — 每日对全市场进行多维度体检
用法:
  python market_diagnosis.py              诊断今天
  python market_diagnosis.py --date=20260521  指定日期
"""
import sys
import os
import json
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from data_collector.fetchers.base import DATA_ROOT, BJS_TZ, load_json, format_amount, today_str

BJS = BJS_TZ


def load_market_data(date_str):
    """加载当日全量个股数据，缺失时自动采集"""
    rows = load_json(date_str, "fund_flow")
    if rows is None:
        print(f"数据文件不存在, 自动采集 {date_str}...")
        import subprocess, sys
        project_dir = os.path.dirname(os.path.abspath(__file__))
        cp = subprocess.run(
            [sys.executable, "-m", "data_collector.main",
             f"--date={date_str}"],
            cwd=project_dir,
        )
        if cp.returncode != 0:
            print(f"自动采集失败，请手动运行: python -m data_collector.main --date={date_str}")
            return None
        rows = load_json(date_str, "fund_flow")
    return rows


def load_history_medians(date_str, n_days=5):
    """加载历史 N 日的中位数涨跌幅"""
    from datetime import datetime as dt
    d = dt.strptime(date_str, "%Y%m%d")
    medians = []
    cursor = d
    attempts = 0
    while len(medians) < n_days and attempts < 15:
        cursor -= timedelta(days=1)
        attempts += 1
        prev_str = cursor.strftime("%Y%m%d")
        rows = load_json(prev_str, "fund_flow")
        if rows is None:
            continue
        chgs = [r.get("f3") for r in rows if isinstance(r.get("f3"), (int, float))]
        if chgs:
            medians.append(sorted(chgs)[len(chgs) // 2])
    return medians


# ============================================================
# 1. 市场宽度（Breadth）
# ============================================================
def diagnose_breadth(rows):
    """涨跌分布分析"""
    chgs = []
    for r in rows:
        f3 = r.get("f3")
        if isinstance(f3, (int, float)):
            chgs.append(f3)

    if not chgs:
        return {}

    n = len(chgs)
    up = sum(1 for c in chgs if c > 0)
    down = sum(1 for c in chgs if c < 0)
    flat = n - up - down
    up_ratio = up / n
    down_ratio = down / n

    # 分段统计
    limit_up = sum(1 for c in chgs if c >= 9.5)
    limit_down = sum(1 for c in chgs if c <= -9.5)
    big_up = sum(1 for c in chgs if 5 <= c < 9.5)
    big_down = sum(1 for c in chgs if -9.5 < c <= -5)
    moderate_up = sum(1 for c in chgs if 2 <= c < 5)
    moderate_down = sum(1 for c in chgs if -5 < c <= -2)
    mild_up = sum(1 for c in chgs if 0 < c < 2)
    mild_down = sum(1 for c in chgs if -2 < c < 0)

    chgs_sorted = sorted(chgs)
    median_ret = chgs_sorted[n // 2]
    p25 = chgs_sorted[n // 4]
    p75 = chgs_sorted[3 * n // 4]
    p10 = chgs_sorted[n // 10]
    p90 = chgs_sorted[9 * n // 10]

    return {
        "total": n,
        "up": up, "down": down, "flat": flat,
        "up_ratio": round(up_ratio, 3),
        "down_ratio": round(down_ratio, 3),
        "limit_up": limit_up, "limit_down": limit_down,
        "big_up": big_up, "big_down": big_down,
        "moderate_up": moderate_up, "moderate_down": moderate_down,
        "mild_up": mild_up, "mild_down": mild_down,
        "median": round(median_ret, 2),
        "p25": round(p25, 2), "p75": round(p75, 2),
        "p10": round(p10, 2), "p90": round(p90, 2),
    }


# ============================================================
# 2. 资金流全景
# ============================================================
def diagnose_fund_flow(rows):
    """全市场资金流分析"""
    f62_vals = []
    f66_vals = []
    f184_vals = []
    f10_vals = []
    f8_vals = []
    f168_vals = []

    for r in rows:
        for key, lst in [("f62", f62_vals), ("f66", f66_vals), ("f184", f184_vals),
                         ("f10", f10_vals), ("f8", f8_vals), ("f168", f168_vals)]:
            v = r.get(key)
            if isinstance(v, (int, float)):
                lst.append(v)

    if not f62_vals:
        return {}

    total_f62 = sum(f62_vals)
    total_f66 = sum(f66_vals) if f66_vals else 0
    pos_flow_ratio = sum(1 for f in f62_vals if f > 0) / len(f62_vals) if f62_vals else 0
    neg_flow_ratio = sum(1 for f in f62_vals if f < 0) / len(f62_vals) if f62_vals else 0

    f62_sorted = sorted(f62_vals)
    median_f62 = f62_sorted[len(f62_sorted) // 2]

    # 超大单占比: f66/f62 > 0.5 视为机构主导
    super_dominant = 0
    for r in rows:
        f62 = r.get("f62", 0) or 0
        f66 = r.get("f66", 0) or 0
        try:
            f62 = float(f62) if f62 else 0
            f66 = float(f66) if f66 else 0
        except (ValueError, TypeError):
            continue
        if f62 > 0 and f66 / f62 > 0.5:
            super_dominant += 1
    super_dominant_ratio = super_dominant / len(f62_vals) if f62_vals else 0

    # 融资数据汇总
    total_margin_net = sum(f168_vals) if f168_vals else 0
    margin_pos_ratio = sum(1 for f in f168_vals if f > 0) / len(f168_vals) if f168_vals else 0

    # 量比分布
    avg_vol_ratio = sum(f10_vals) / len(f10_vals) if f10_vals else 0
    high_vol = sum(1 for f in f10_vals if f > 3) / len(f10_vals) if f10_vals else 0

    # 换手率分布
    avg_turnover = sum(f8_vals) / len(f8_vals) if f8_vals else 0

    return {
        "total_main_flow": total_f62,
        "total_super_flow": total_f66,
        "pos_flow_ratio": round(pos_flow_ratio, 3),
        "neg_flow_ratio": round(neg_flow_ratio, 3),
        "median_main_flow": median_f62,
        "super_dominant_ratio": round(super_dominant_ratio, 3),
        "total_margin_net": total_margin_net,
        "margin_pos_ratio": round(margin_pos_ratio, 3) if f168_vals else 0,
        "avg_vol_ratio": round(avg_vol_ratio, 2),
        "high_vol_ratio": round(high_vol, 3),
        "avg_turnover": round(avg_turnover, 2),
    }


# ============================================================
# 3. 板块轮动（行业 + 概念）
# ============================================================
def diagnose_sectors(date_str):
    """板块资金流分析"""
    result = {"top_industries": [], "bottom_industries": [],
              "top_concepts": [], "bottom_concepts": []}

    for fname, label in [("industry_flow", "行业"), ("concept_flow", "概念")]:
        rows = load_json(date_str, fname)
        if rows is None:
            continue

        valid = [r for r in rows if isinstance(r.get("f62"), (int, float))]
        if not valid:
            continue

        valid.sort(key=lambda r: r.get("f62", 0), reverse=True)

        key = "top_industries" if label == "行业" else "top_concepts"
        result[key] = [{"name": r.get("f14", ""),
                        "flow": r["f62"],
                        "chg": r.get("f3", 0) or 0}
                       for r in valid[:5]]

        key = "bottom_industries" if label == "行业" else "bottom_concepts"
        result[key] = [{"name": r.get("f14", ""),
                        "flow": r["f62"],
                        "chg": r.get("f3", 0) or 0}
                       for r in valid[-5:]]

    return result


# ============================================================
# 4. 北向资金
# ============================================================
def diagnose_north_flow(date_str):
    """北向资金分析"""
    rows = load_json(date_str, "north_flow")
    if rows is None or not rows:
        return {"available": False, "reason": "数据不可用"}

    latest = rows[0]
    net_north = latest.get("net_north", 0)

    if abs(net_north) > 500 or (net_north == 0 and latest.get("net_south", 0) == 0):
        return {"available": False, "reason": "盘中数据被屏蔽，盘后更新"}

    return {
        "available": True,
        "date": latest.get("date", "?"),
        "net_north": round(net_north, 1),
        "net_south": round(latest.get("net_south", 0), 1),
        "direction": "流入" if net_north > 20 else ("流出" if net_north < -20 else "平衡"),
    }


# ============================================================
# 5. 市场环境综合判定
# ============================================================
def diagnose_regime(breadth, flow, history_medians):
    """综合判定市场牛熊，输出置信度"""
    if not breadth or not flow:
        return {"regime": "unknown", "confidence": 0, "label": "数据不足"}

    up_ratio = breadth["up_ratio"]
    median_ret = breadth["median"]
    pos_flow = flow["pos_flow_ratio"]
    m5 = sum(history_medians[:5]) / min(5, len(history_medians)) if history_medians else 0

    # 评分卡
    scores = {}

    # 宽度维度
    if up_ratio > 0.60:
        scores["width"] = 2
    elif up_ratio > 0.50:
        scores["width"] = 1
    elif up_ratio > 0.35:
        scores["width"] = 0
    elif up_ratio > 0.20:
        scores["width"] = -1
    else:
        scores["width"] = -2

    # 中位数维度
    if median_ret > 1.0:
        scores["median"] = 2
    elif median_ret > 0.3:
        scores["median"] = 1
    elif median_ret > -0.3:
        scores["median"] = 0
    elif median_ret > -1.0:
        scores["median"] = -1
    else:
        scores["median"] = -2

    # 资金维度
    if pos_flow > 0.55:
        scores["flow"] = 2
    elif pos_flow > 0.45:
        scores["flow"] = 1
    elif pos_flow > 0.35:
        scores["flow"] = 0
    elif pos_flow > 0.25:
        scores["flow"] = -1
    else:
        scores["flow"] = -2

    # 趋势维度
    if m5 > 1.0:
        scores["trend"] = 2
    elif m5 > 0.3:
        scores["trend"] = 1
    elif m5 > -0.3:
        scores["trend"] = 0
    elif m5 > -1.0:
        scores["trend"] = -1
    else:
        scores["trend"] = -2

    total_score = sum(scores.values())

    if total_score >= 5:
        regime = "bull"
        confidence = min(0.95, 0.5 + total_score * 0.06)
    elif total_score >= 2:
        regime = "bull_bias"
        confidence = 0.55 + (total_score - 2) * 0.08
    elif total_score >= -1:
        regime = "range"
        confidence = 0.50
    elif total_score >= -4:
        regime = "bear_bias"
        confidence = 0.55 + (abs(total_score) - 2) * 0.08
    else:
        regime = "bear"
        confidence = min(0.95, 0.5 + abs(total_score) * 0.06)

    labels = {
        "bull": "牛市 — 积极做多", "bull_bias": "偏多震荡 — 可适度加仓",
        "range": "震荡市 — 精选个股", "bear_bias": "偏空震荡 — 控制仓位",
        "bear": "熊市 — 防御为主",
    }

    return {
        "regime": regime,
        "confidence": round(confidence, 2),
        "label": labels.get(regime, "未知"),
        "total_score": total_score,
        "scores": scores,
        "trend_5d": round(m5, 2),
    }


# ============================================================
# 6. 风险预警 — 已委托给 portfolio.black_swan.BlackSwanDetector
# ============================================================
def diagnose_risks(breadth, flow, north, regime_result):
    """@deprecated — 实际逻辑已迁移到 BlackSwanDetector，
    保留签名仅供旧调用方兼容。新代码请用 portfolio.black_swan。
    """
    return {"alerts": [], "level": "low"}


# ============================================================
# 7. 仓位建议
# ============================================================
def position_advice(regime_result, risk_result):
    """根据市场环境和风险等级，输出仓位建议"""
    regime = regime_result.get("regime", "range")
    risk_level = risk_result.get("level", "low")

    base_position = {
        "bull": 0.80, "bull_bias": 0.65, "range": 0.50,
        "bear_bias": 0.35, "bear": 0.20,
    }.get(regime, 0.50)

    risk_discount = {"low": 0.90, "medium": 0.75, "high": 0.50, "critical": 0.25}
    adj_position = base_position * risk_discount.get(risk_level, 1.0)

    return {
        "base": round(base_position * 100),
        "adjusted": round(adj_position * 100),
        "advice": (
            f"基准仓位 {base_position*100:.0f}% x 风险折扣 {risk_discount.get(risk_level, 1.0):.0%} "
            f"= 建议仓位 {adj_position*100:.0f}%"
        ),
    }


# ============================================================
# 展示
# ============================================================
def print_header(title, width=72):
    print(f"\n{'═' * width}")
    print(f"  {title}")
    print(f"{'═' * width}")


def print_sub_header(title):
    print(f"\n  ┌─ {title} ─{'─' * 56}")


def bar_chart(value, max_width=40, label=""):
    """简单的ASCII条形图"""
    bar_len = int(abs(value) * max_width)
    if value >= 0:
        bar = "█" * min(bar_len, max_width)
    else:
        bar = "░" * min(bar_len, max_width)
    return bar


def print_diagnosis(date_str):
    """主诊断入口"""
    rows = load_market_data(date_str)
    if rows is None:
        return

    print_header(f"A股盘面诊断报告 [{date_str}]")
    print(f"  数据覆盖: {len(rows)} 只个股")
    print(f"  生成时间: {datetime.now(BJS).strftime('%Y-%m-%d %H:%M:%S')}")

    # ── 1. 市场宽度 ──
    breadth = diagnose_breadth(rows)
    if breadth:
        print_header("一、市场宽度（涨跌分布）")
        print(f"""
    全市场 {breadth['total']} 只个股

    📈 上涨 {breadth['up']} 只 ({breadth['up_ratio']:.1%})    涨停 {breadth['limit_up']} 只
    📉 下跌 {breadth['down']} 只 ({breadth['down_ratio']:.1%})    跌停 {breadth['limit_down']} 只
    ➖ 平盘 {breadth['flat']} 只

    涨跌分布:
      涨停(≥9.5%):  {breadth['limit_up']:>5} 只  {bar_chart(breadth['limit_up']/breadth['total'], 30, '')}
      大涨(5~9.5%): {breadth['big_up']:>5} 只  {bar_chart(breadth['big_up']/breadth['total'], 30, '')}
      中涨(2~5%):   {breadth['moderate_up']:>5} 只  {bar_chart(breadth['moderate_up']/breadth['total'], 30, '')}
      微涨(0~2%):   {breadth['mild_up']:>5} 只  {bar_chart(breadth['mild_up']/breadth['total'], 30, '')}
      微跌(-2~0%):  {breadth['mild_down']:>5} 只  {bar_chart(breadth['mild_down']/breadth['total'], 30, '')}
      中跌(-5~-2%): {breadth['moderate_down']:>5} 只  {bar_chart(breadth['moderate_down']/breadth['total'], 30, '')}
      大跌(-9.5~-5%):{breadth['big_down']:>5} 只  {bar_chart(breadth['big_down']/breadth['total'], 30, '')}
      跌停(≤-9.5%): {breadth['limit_down']:>5} 只  {bar_chart(breadth['limit_down']/breadth['total'], 30, '')}

    分位数: P10={breadth['p10']:+.1f}%  P25={breadth['p25']:+.1f}%  中位={breadth['median']:+.1f}%  P75={breadth['p75']:+.1f}%  P90={breadth['p90']:+.1f}%
""")

    # ── 2. 资金流全景 ──
    flow = diagnose_fund_flow(rows)
    if flow:
        print_header("二、资金流全景")
        margin_str = ""
        if flow.get("total_margin_net"):
            margin_str = (f"  融资净买入合计: {flow['total_margin_net']/1e8:+.1f}亿  "
                         f"融资净买入占比: {flow['margin_pos_ratio']:.1%}")
        print(f"""
  主力净流入合计:   {flow['total_main_flow']/1e8:+.1f}亿
  超大单净流入合计:  {flow['total_super_flow']/1e8:+.1f}亿
  中位数主力净流入:  {format_amount(flow['median_main_flow'])}
  主力净流入>0占比:  {flow['pos_flow_ratio']:.1%}
  主力净流入<0占比:  {flow['neg_flow_ratio']:.1%}
  机构主导(超大单>50%): {flow['super_dominant_ratio']:.1%}
{margin_str}
  均量比:            {flow['avg_vol_ratio']:.2f}
  高量比(>3)占比:    {flow['high_vol_ratio']:.1%}
  均换手率:          {flow['avg_turnover']:.2f}%
""")

    # ── 3. 板块轮动 ──
    sectors = diagnose_sectors(date_str)
    if sectors["top_industries"]:
        print_header("三、板块轮动")
        print(f"\n  🔥 行业流入 TOP5:")
        for s in sectors["top_industries"]:
            print(f"      {s['name']:<8}  {format_amount(s['flow']):>10}  涨跌 {s['chg']:+.2f}%")
        print(f"\n  ❄️  行业流出 BOTTOM5:")
        for s in sectors["bottom_industries"]:
            print(f"      {s['name']:<8}  {format_amount(s['flow']):>10}  涨跌 {s['chg']:+.2f}%")

    if sectors["top_concepts"]:
        print(f"\n  🔥 概念流入 TOP5:")
        for s in sectors["top_concepts"]:
            print(f"      {s['name']:<10}  {format_amount(s['flow']):>10}  涨跌 {s['chg']:+.2f}%")
        print(f"\n  ❄️  概念流出 BOTTOM5:")
        for s in sectors["bottom_concepts"]:
            print(f"      {s['name']:<10}  {format_amount(s['flow']):>10}  涨跌 {s['chg']:+.2f}%")

    # ── 4. 北向资金 ──
    north = diagnose_north_flow(date_str)
    print_header("四、北向资金")
    if north["available"]:
        print(f"""
  日期:         {north['date']}
  北向净{ north['direction']}: {north['net_north']:+.1f}亿
  南向净流入:   {north['net_south']:+.1f}亿
""")
    else:
        print(f"\n  ⚠️  {north['reason']}")

    # ── 5. 市场环境 ──
    history_medians = load_history_medians(date_str, 5)
    regime_result = diagnose_regime(breadth, flow, history_medians)
    print_header("五、市场环境判定")
    emoji = {"bull": "🐂", "bull_bias": "📈", "range": "📊", "bear_bias": "📉", "bear": "🐻"}
    regime_emoji = emoji.get(regime_result["regime"], "❓")
    print(f"""
  判定结果: {regime_emoji} {regime_result['label']}
  综合评分: {regime_result['total_score']:+.0f} (范围 -8 ~ +8)
  置信度:   {regime_result['confidence']:.0%}
  5日趋势:  {regime_result['trend_5d']:+.1f}% (5日滚动中位数均值)

  分项评分:
    宽度(涨跌比):   {regime_result['scores'].get('width', 0):+2.0f}
    中位(涨跌幅):   {regime_result['scores'].get('median', 0):+2.0f}
    资金(流入比):   {regime_result['scores'].get('flow', 0):+2.0f}
    趋势(5日均):   {regime_result['scores'].get('trend', 0):+2.0f}
""")

    # ── 6. 风险预警 ──
    risk_result = diagnose_risks(breadth, flow, north, regime_result)
    print_header("六、风险预警")
    level_labels = {"low": "🟢 低", "medium": "🟡 中", "high": "🟠 高", "critical": "🔴 极高"}
    print(f"\n  风险等级: {level_labels.get(risk_result['level'], '?')}")
    if risk_result["alerts"]:
        for alert in risk_result["alerts"]:
            print(f"    ⚠️  {alert}")
    else:
        print(f"    ✅ 无重大风险预警")

    # ── 7. 仓位建议 ──
    pos = position_advice(regime_result, risk_result)
    print_header("七、仓位建议")
    print(f"""
  {pos['advice']}

  操作指引:
    牛市 + 低风险 → 仓位 80%+ 积极追击，可适度追涨
    偏多震荡       → 仓位 60-70% 逢低加仓，关注突破
    震荡市         → 仓位 40-60% 精选个股，高抛低吸
    偏空震荡       → 仓位 30-40% 控制仓位，防御为主
    熊市 + 高风险  → 仓位 ≤20% 现金为王，等待企稳信号
""")

    # ── 汇总 ──
    print_header("诊断汇总")
    print(f"""
  {regime_emoji} 市场状态:   {regime_result['label']} (置信度 {regime_result['confidence']:.0%})
  🎯 建议仓位:   {pos['adjusted']}%
  ⚡ 风险等级:   {level_labels.get(risk_result['level'], '?')}

  核心关注:
    1. 涨跌比 {breadth['up_ratio']:.1%} | 中位数 {breadth['median']:+.1f}%
    2. 主力流入占比 {flow['pos_flow_ratio']:.1%} | 融资净买入 {'正' if flow.get('total_margin_net', 0) > 0 else '负'}偏
    3. 行业: {' / '.join(s['name'] for s in sectors.get('top_industries', [])[:3]) or '数据不足'} 领涨
""")

    if risk_result["alerts"]:
        print(f"  ⚠️  风险提示: {'; '.join(risk_result['alerts'][:3])}")
    print(f"\n  ⚠️  以上为量化模型输出，不构成投资建议。股市有风险，投资需谨慎。\n")


def main():
    date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")
    for arg in sys.argv:
        if arg.startswith("--date="):
            date_str = arg.split("=")[1]

    result = get_diagnosis(date_str)
    if result is None:
        print(f"数据不可用: {date_str}")
        sys.exit(1)
    print_diagnosis(date_str)


def diagnose_sentiment(rows, date_str=None):
    """市场情绪分析：计算情绪温度计 0-100"""
    from data_collector.fetchers.market_sentiment import compute_sentiment, fetch_indices
    index_data = fetch_indices()
    return compute_sentiment(rows, index_data=index_data)


# ============================================================
# 程序化接口
# ============================================================
def get_diagnosis(date_str=None):
    """程序化接口：返回结构化诊断结果 dict
    返回:
      None 如果数据不可用
      dict: 8 大诊断模块 + 原始数据
    """
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    rows = load_market_data(date_str)
    if rows is None:
        return None

    breadth = diagnose_breadth(rows)
    flow = diagnose_fund_flow(rows)
    sectors = diagnose_sectors(date_str)
    north = diagnose_north_flow(date_str)
    history_medians = load_history_medians(date_str, 5)
    regime_result = diagnose_regime(breadth, flow, history_medians)
    sentiment = diagnose_sentiment(rows, date_str)

    # 黑天鹅检测 — 替代原 diagnose_risks，传入预计算数据避免重复 I/O
    from portfolio.black_swan import BlackSwanDetector
    bs_result = BlackSwanDetector(
        date_str,
        preloaded={"breadth": breadth, "fund_flow": flow,
                   "north_flow": north, "sentiment": sentiment,
                   "rows": rows},
    ).check()

    # 映射 BlackSwan level → 原 risk level
    bs_level_map = {0: "low", 1: "medium", 2: "high", 3: "critical"}
    risk_result = {
        "alerts": [f"{r['rule_id']} {r['name']}: {r['detail']}" for r in bs_result["triggered_rules"]],
        "level": bs_level_map.get(bs_result["level"], "low"),
    }

    position = position_advice(regime_result, risk_result)

    result = {
        "date": date_str,
        "stock_count": len(rows),
        "breadth": breadth,
        "fund_flow": flow,
        "sectors": sectors,
        "north_flow": north,
        "regime": regime_result,
        "risks": risk_result,
        "black_swan": {  # 新增：完整黑天鹅检测结果
            "level": bs_result["level"],
            "level_name": bs_result["level_name"],
            "summary": bs_result["summary"],
            "triggered_rules": bs_result["triggered_rules"],
            "actions": bs_result["actions"],
        },
        "position": position,
        "sentiment": sentiment,
    }
    save_diagnosis(result, date_str)
    return result


def save_diagnosis(diag, date_str=None):
    """持久化诊断结果到 data/<date>/diagnosis/"""
    if diag is None:
        return
    date_str = date_str or diag.get("date", today_str())
    diag_dir = os.path.join(DATA_ROOT, date_str, "diagnosis")
    os.makedirs(diag_dir, exist_ok=True)

    ts = datetime.now(BJS_TZ).strftime("%H%M%S")
    path = os.path.join(diag_dir, f"diagnosis_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(diag, f, ensure_ascii=False, indent=2)

    # 同时保存一份 latest 副本便于快速读取
    latest_path = os.path.join(diag_dir, "latest.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(diag, f, ensure_ascii=False, indent=2)

    print(f"  诊断结果已保存: {os.path.basename(path)} ({os.path.getsize(path):,} bytes)")


def load_diagnosis(date_str):
    """加载最新诊断结果"""
    path = os.path.join(DATA_ROOT, date_str, "diagnosis", "latest.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


if __name__ == "__main__":
    main()
