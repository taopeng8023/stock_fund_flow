"""
图表模块 — 纯 Plotly go.Figure 构建函数
每个函数接收数据、返回 go.Figure，空数据返回带 "暂无数据" 注解的空图
"""
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ============================================================
# 通用工具
# ============================================================

def _empty_figure(message="暂无数据"):
    """空图占位"""
    fig = go.Figure()
    fig.add_annotation(
        x=0.5, y=0.5, text=message, showarrow=False,
        font=dict(size=16, color="#999"), xref="paper", yref="paper"
    )
    fig.update_layout(
        xaxis=dict(showgrid=False, zeroline=False, visible=False),
        yaxis=dict(showgrid=False, zeroline=False, visible=False),
        margin=dict(l=20, r=20, t=30, b=20),
        height=300,
    )
    return fig


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ============================================================
# Tab A: 回测分析
# ============================================================

def build_equity_curve(summary_rows):
    """A1. 累计收益曲线 — Top50 vs 随机50"""
    if not summary_rows:
        return _empty_figure("暂无回测数据，请先运行回测")

    dates = [r.get("日期", "") for r in summary_rows]
    top50_rets = [_safe_float(r.get("Top50均收益", 0)) for r in summary_rows]
    rand50_rets = [_safe_float(r.get("随机50均收益", 0)) for r in summary_rows]

    cum_top = 100.0
    cum_rand = 100.0
    cum_top_vals = []
    cum_rand_vals = []

    for tr, rr in zip(top50_rets, rand50_rets):
        cum_top *= (1 + tr / 100)
        cum_rand *= (1 + rr / 100)
        cum_top_vals.append(round(cum_top, 2))
        cum_rand_vals.append(round(cum_rand, 2))

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=cum_top_vals, mode="lines+markers",
        name="Top50 累计", line=dict(color="#22c55e", width=2),
        marker=dict(size=4),
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=cum_rand_vals, mode="lines+markers",
        name="随机50 累计", line=dict(color="#94a3b8", width=2, dash="dash"),
        marker=dict(size=4),
    ))
    fig.add_hline(y=100, line_dash="dot", line_color="#64748b", opacity=0.5)
    fig.update_layout(
        title="累计收益曲线（基准=100）",
        xaxis_title="日期", yaxis_title="累计净值",
        hovermode="x unified",
        margin=dict(l=40, r=20, t=50, b=40),
        height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def build_daily_return_bars(summary_rows):
    """A2. 每日 Top50 收益分布柱状图"""
    if not summary_rows:
        return _empty_figure("暂无回测数据")

    dates = [r.get("日期", "") for r in summary_rows]
    top50_rets = [_safe_float(r.get("Top50均收益", 0)) for r in summary_rows]
    colors = ["#22c55e" if v >= 0 else "#ef4444" for v in top50_rets]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=dates, y=top50_rets,
        marker_color=colors,
        name="Top50 日均收益",
        text=[f"{v:+.2f}%" for v in top50_rets],
        textposition="outside",
        textfont=dict(size=10),
    ))
    fig.add_hline(y=0, line_dash="solid", line_color="#64748b", opacity=0.5)
    fig.update_layout(
        title="每日 Top50 收益分布",
        xaxis_title="日期", yaxis_title="收益 (%)",
        margin=dict(l=40, r=20, t=50, b=40),
        height=400,
        showlegend=False,
    )
    return fig


def build_factor_edge(summary_rows):
    """A3. 因子区分度时间序列"""
    if not summary_rows:
        return _empty_figure("暂无回测数据")

    dates = [r.get("日期", "") for r in summary_rows]
    edge = [_safe_float(r.get("因子区分度", 0)) for r in summary_rows]
    d1 = [_safe_float(r.get("D1均收益", 0)) for r in summary_rows]
    d10 = [_safe_float(r.get("D10均收益", 0)) for r in summary_rows]

    fig = make_subplots(specs=[[{"secondary_y": False}]])
    fig.add_trace(go.Scatter(
        x=dates, y=edge, mode="lines+markers",
        name="因子区分度 (D1-D10)", line=dict(color="#8b5cf6", width=2),
        fill="tozeroy", fillcolor="rgba(139,92,246,0.1)",
        marker=dict(size=4),
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=d1, mode="lines+markers",
        name="D1 (最高分)", line=dict(color="#22c55e", width=1.5, dash="dot"),
        marker=dict(size=3),
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=d10, mode="lines+markers",
        name="D10 (最低分)", line=dict(color="#ef4444", width=1.5, dash="dot"),
        marker=dict(size=3),
    ))
    fig.add_hline(y=0, line_dash="solid", line_color="#64748b", opacity=0.5)
    fig.update_layout(
        title="因子区分度 — D1 vs D10 收益差",
        xaxis_title="日期", yaxis_title="收益 (%)",
        hovermode="x unified",
        margin=dict(l=40, r=20, t=50, b=40),
        height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def build_decile_chart(summary_rows):
    """A4. 分位数单调性检验"""
    if not summary_rows:
        return _empty_figure("暂无回测数据")

    # 取最近 5 天
    rows = summary_rows[-5:]
    dates = [r.get("日期", "") for r in rows]
    d1_vals = [_safe_float(r.get("D1均收益", 0)) for r in rows]
    d10_vals = [_safe_float(r.get("D10均收益", 0)) for r in rows]

    fig = go.Figure()
    x = list(range(len(dates)))
    width = 0.35
    fig.add_trace(go.Bar(
        x=[i - width/2 for i in x], y=d1_vals, width=width,
        name="D1 (最高分)", marker_color="#8b5cf6",
        text=[f"{v:+.2f}%" for v in d1_vals], textposition="outside",
        textfont=dict(size=10),
    ))
    fig.add_trace(go.Bar(
        x=[i + width/2 for i in x], y=d10_vals, width=width,
        name="D10 (最低分)", marker_color="#94a3b8",
        text=[f"{v:+.2f}%" for v in d10_vals], textposition="outside",
        textfont=dict(size=10),
    ))
    fig.update_layout(
        title="分位数单调性 — D1 vs D10（最近 5 天）",
        xaxis=dict(tickmode="array", tickvals=x, ticktext=dates),
        yaxis_title="收益 (%)",
        barmode="group",
        margin=dict(l=40, r=20, t=50, b=40),
        height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.add_hline(y=0, line_dash="solid", line_color="#64748b", opacity=0.5)
    return fig


def build_score_scatter(daily_rows):
    """A5. 综合得分 vs 次日收益 散点图"""
    if not daily_rows:
        return _empty_figure("暂无当日回测明细，请选择日期加载")

    scores = [_safe_float(r.get("综合得分", 0)) for r in daily_rows]
    returns = [_safe_float(r.get("次日收益%", 0)) for r in daily_rows]
    results = [r.get("胜负", "") for r in daily_rows]

    wins_x = [s for s, r in zip(scores, returns) if r > 0]
    wins_y = [r for r in returns if r > 0]
    losses_x = [s for s, r in zip(scores, returns) if r <= 0]
    losses_y = [r for r in returns if r <= 0]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=wins_x, y=wins_y, mode="markers",
        name="胜", marker=dict(color="#22c55e", size=5, opacity=0.5),
    ))
    fig.add_trace(go.Scatter(
        x=losses_x, y=losses_y, mode="markers",
        name="负/平", marker=dict(color="#ef4444", size=5, opacity=0.5),
    ))
    fig.add_hline(y=0, line_dash="solid", line_color="#64748b", opacity=0.5)
    # 简单趋势线
    if len(scores) > 1:
        import numpy as np
        try:
            z = np.polyfit(scores, returns, 1)
            p = np.poly1d(z)
            x_line = [min(scores), max(scores)]
            y_line = [p(x_line[0]), p(x_line[1])]
            fig.add_trace(go.Scatter(
                x=x_line, y=y_line, mode="lines",
                name="趋势线", line=dict(color="#3b82f6", width=2, dash="dash"),
            ))
        except Exception:
            pass

    fig.update_layout(
        title="综合得分 vs 次日收益",
        xaxis_title="综合得分", yaxis_title="次日收益 (%)",
        margin=dict(l=40, r=20, t=50, b=40),
        height=400,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


# ============================================================
# Tab B: 因子分析
# ============================================================

def build_factor_contribution(scores_rows):
    """B1. 因子加权贡献排名"""
    if not scores_rows:
        return _empty_figure("暂无评分数据，请先运行评分程序")

    # 因子列名映射（中文 → 权重，基于 daily_pipeline/score.py WEIGHTS_BASE）
    factor_weights = {
        "资金得分": 0.26, "板块得分": 0.14, "行业内得分": 0.08,
        "技术面得分": 0.08, "位置得分": 0.08, "趋势得分": 0.03,
        "多日得分": 0.04, "分析师得分": 0.04, "融资得分": 0.01,
        "加速度得分": 0.01, "占比趋势得分": 0.02, "日内稳定": 0.03,
        "日内加速": 0.03, "排名轨迹": 0.02, "VWAP位置": 0.07,
        "板块轨迹": 0.02, "价格动量": 0.03, "涨停邻近": 0.01,
        "行业分散": 0.01, "板块价格": 0.05, "尾盘收益": 0.03,
        "尾盘量能": 0.02, "拥挤度": 0.02, "启动得分": 0.03,
    }

    n = len(scores_rows)
    if n == 0:
        return _empty_figure("暂无评分数据")

    contributions = []
    for name, weight in factor_weights.items():
        if name in scores_rows[0]:
            mean_score = sum(_safe_float(r.get(name, 0)) for r in scores_rows) / n
            contributions.append({
                "name": name, "weight": weight,
                "mean_score": mean_score,
                "contribution": weight * mean_score,
            })

    contributions.sort(key=lambda x: x["contribution"], reverse=True)
    top15 = contributions[:15]
    top15.reverse()  # 水平柱状图从下到上

    names = [c["name"] for c in top15]
    values = [c["contribution"] for c in top15]
    mean_scores = [c["mean_score"] for c in top15]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=names, x=values, orientation="h",
        marker=dict(
            color=values, colorscale="Blues",
            showscale=False,
            line=dict(width=0),
        ),
        text=[f"{v:.3f} (均值{m:.2f})" for v, m in zip(values, mean_scores)],
        textposition="outside", textfont=dict(size=11),
    ))
    fig.update_layout(
        title="因子加权贡献度排名（Top 15）",
        xaxis_title="加权贡献 = 权重 × 均值得分",
        margin=dict(l=80, r=60, t=50, b=30),
        height=500,
    )
    return fig


def build_score_histogram(scores_rows):
    """B2. 全市场综合得分分布"""
    if not scores_rows:
        return _empty_figure("暂无评分数据")

    scores = [_safe_float(r.get("综合得分", 0)) for r in scores_rows]
    mean_score = sum(scores) / len(scores) if scores else 0
    scores_sorted = sorted(scores)
    median_score = scores_sorted[len(scores_sorted) // 2] if scores_sorted else 0

    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=scores, nbinsx=50, marker_color="#3b82f6", opacity=0.7,
        name="股票数量",
    ))
    fig.add_vline(x=mean_score, line_dash="dash", line_color="#ef4444",
                  annotation_text=f"均值 {mean_score:.3f}",
                  annotation_position="top right")
    fig.add_vline(x=median_score, line_dash="dot", line_color="#f59e0b",
                  annotation_text=f"中位 {median_score:.3f}",
                  annotation_position="top left")
    fig.update_layout(
        title="全市场综合得分分布",
        xaxis_title="综合得分", yaxis_title="股票数量",
        margin=dict(l=40, r=20, t=50, b=40),
        height=400,
        showlegend=False,
        bargap=0.05,
    )
    return fig


def build_industry_box(scores_rows):
    """B3. 各行业评分分布（Top 10 行业）"""
    if not scores_rows:
        return _empty_figure("暂无评分数据")

    # 按行业分组，取股票数量最多的 10 个行业
    industry_scores = {}
    for r in scores_rows:
        ind = r.get("行业", "未知")
        sc = _safe_float(r.get("综合得分", 0))
        if ind not in industry_scores:
            industry_scores[ind] = []
        industry_scores[ind].append(sc)

    # 排序取 Top 10
    top_industries = sorted(industry_scores.items(), key=lambda x: len(x[1]), reverse=True)[:10]

    fig = go.Figure()
    for ind, vals in top_industries:
        fig.add_trace(go.Box(
            y=vals, name=f"{ind} ({len(vals)})",
            boxpoints="outliers", marker=dict(size=2, opacity=0.5),
            line=dict(width=1),
        ))

    fig.update_layout(
        title="各行业综合得分分布（Top 10 行业，按股票数）",
        yaxis_title="综合得分",
        margin=dict(l=40, r=20, t=50, b=80),
        height=500,
        showlegend=False,
        xaxis=dict(tickfont=dict(size=10)),
    )
    return fig


# ============================================================
# Tab 增强: 盘面诊断
# ============================================================

def build_breadth_donut(up_ratio, down_ratio):
    """D1. 涨跌分布环形图"""
    try:
        up = float(str(up_ratio).replace("%", ""))
        down = float(str(down_ratio).replace("%", ""))
    except (TypeError, ValueError):
        return _empty_figure("暂无涨跌数据")

    flat = max(0, 100 - up * 100 - down * 100) if up <= 1 else max(0, 100 - up - down)

    up_pct = up * 100 if up <= 1 else up
    down_pct = down * 100 if down <= 1 else down

    fig = go.Figure()
    fig.add_trace(go.Pie(
        values=[up_pct, down_pct, flat],
        labels=["上涨", "下跌", "平盘"],
        hole=0.65,
        marker=dict(colors=["#ef4444", "#22c55e", "#94a3b8"]),
        textinfo="label+percent",
        textfont=dict(size=12),
        sort=False,
    ))
    fig.add_annotation(
        x=0.5, y=0.5, text=f"{up_pct:.1f}%",
        font=dict(size=24, color="#ef4444"), showarrow=False,
    )
    fig.update_layout(
        title="涨跌分布",
        margin=dict(l=20, r=20, t=40, b=20),
        height=350,
        showlegend=False,
    )
    return fig


# ============================================================
# Tab 增强: 风险监控
# ============================================================

def build_risk_gauge(bs_level, bs_summary):
    """R1. 黑天鹅风险等级仪表盘"""
    level_colors = {
        0: ("green", "安全"),
        1: ("#eab308", "关注"),
        2: ("#f97316", "警惕"),
        3: ("#ef4444", "危险"),
    }
    color, label = level_colors.get(bs_level, ("gray", "未知"))
    count_critical = bs_summary.get("critical", 0) if bs_summary else 0
    count_severe = bs_summary.get("severe", 0) if bs_summary else 0
    count_high = bs_summary.get("high", 0) if bs_summary else 0
    total = count_critical + count_severe + count_high

    fig = go.Figure()
    fig.add_trace(go.Indicator(
        mode="gauge+number+delta",
        value=bs_level if bs_level >= 0 else 0,
        title={"text": f"风险等级 — {label}", "font": {"size": 16}},
        number={"font": {"size": 40, "color": color}},
        delta={"reference": 1, "increasing": {"color": "#ef4444"}},
        gauge={
            "axis": {"range": [0, 3], "tickwidth": 1},
            "bar": {"color": color},
            "steps": [
                {"range": [0, 1], "color": "rgba(34,197,94,0.3)"},
                {"range": [1, 2], "color": "rgba(234,179,8,0.3)"},
                {"range": [2, 3], "color": "rgba(239,68,68,0.3)"},
            ],
            "threshold": {
                "line": {"color": "#ef4444", "width": 3},
                "thickness": 0.8, "value": 2.5,
            },
        },
    ))
    if total > 0:
        fig.add_annotation(
            x=0.5, y=-0.1, xref="paper", yref="paper",
            text=f"触发规则: CRITICAL {count_critical} | SEVERE {count_severe} | HIGH {count_high}",
            font=dict(size=12, color="#64748b"), showarrow=False,
        )
    fig.update_layout(
        margin=dict(l=20, r=20, t=60, b=60),
        height=350,
    )
    return fig
