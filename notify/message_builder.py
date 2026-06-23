"""
消息模板构建器 — 为各通知事件构建企业微信消息内容。

通知事件:
  每日买入推荐 → Markdown 消息
  卖出信号     → Markdown 消息（红色主题）
  黑天鹅预警   → 文本消息 + @all
  每日市场摘要 → Markdown 消息
  Pipeline 错误 → 文本消息
  周度表现总结 → Markdown 消息
"""

import hashlib
import json
from datetime import date
from typing import Optional


# ---------------------------------------------------------------------------
# 去重键生成
# ---------------------------------------------------------------------------

def dedup_key(event_type: str, date_str: str, stock_code: str = "") -> str:
    """生成去重键: SHA256(event_type + date + stock_code)[:16]。"""
    raw = f"{event_type}|{date_str}|{stock_code}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 1. 每日买入推荐
# ---------------------------------------------------------------------------

def build_daily_buy_recommendation(
    date_str: str,
    regime: str,
    risk_level: str,
    suggested_position: int,
    buys: list[dict],
) -> str:
    """构建每日买入推荐 Markdown 消息。

    Args:
        date_str: 日期 YYYYMMDD
        regime: 市场体制 (bull/bull_bias/range/bear_bias/bear)
        risk_level: 风险等级 (low/medium/high/critical)
        suggested_position: 建议仓位 0-100
        buys: 买入列表，每项 {rank, code, name, score, allocation_pct, reasons, ...}

    Returns:
        Markdown 格式的消息内容
    """
    regime_map = {
        "bull": "多头🐂", "bull_bias": "偏多📈",
        "range": "震荡↔️", "bear_bias": "偏空📉", "bear": "空头🐻",
    }
    risk_map = {
        "low": "低🟢", "medium": "中🟡", "high": "高🟠", "critical": "危急🔴",
    }

    lines = [
        f"## 🎯 每日买入推荐",
        f"**日期**: {date_str}",
        f"**市场体制**: {regime_map.get(regime, regime)}",
        f"**风险等级**: {risk_map.get(risk_level, risk_level)}",
        f"**建议仓位**: <font color=\"info\">{suggested_position}%</font>",
        "",
    ]

    if not buys:
        lines.append("> 今日无符合条件的买入标的")
        lines.append("")
        lines.append(f"<font color=\"comment\">风险门禁已触发，建议观望</font>")
        return "\n".join(lines)

    lines.append(f"### 📊 买入清单（共 {len(buys)} 只）")
    lines.append("")

    for b in buys:
        rank = b.get("rank", "?")
        code = b.get("code", "")
        name = b.get("name", "")
        score = b.get("score", 0)
        chg = b.get("chg_pct", 0)
        alloc = b.get("allocation_pct", 0)
        sl = b.get("stop_loss", 0)
        tp = b.get("take_profit", 0)
        industry = b.get("industry", "")
        reasons = b.get("reasons", [])

        chg_color = "info" if chg > 0 else "warning"
        reason_text = "、".join(reasons[:3]) if reasons else "—"

        lines.append(
            f"**{rank}. {name}**（{code}）"
        )
        lines.append(f"> 得分: <font color=\"info\">{score:.3f}</font>　"
                      f"涨跌: <font color=\"{chg_color}\">{chg:+.1f}%</font>　"
                      f"仓位: <font color=\"comment\">{alloc}%</font>")
        lines.append(f"> 止损: {sl:+.0f}%　止盈: {tp:+.0f}%　行业: {industry}")
        lines.append(f"> 理由: {reason_text}")
        lines.append("")

    lines.append("---")
    lines.append(f"<font color=\"comment\">⏰ 生成时间: {date_str} 14:50</font>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. 卖出信号
# ---------------------------------------------------------------------------

def build_sell_signal(
    date_str: str,
    positions: list[dict],
) -> str:
    """构建卖出信号 Markdown 消息（红色主题）。

    Args:
        date_str: 日期
        positions: 触发卖出的持仓列表
                   每项 {code, name, entry_date, hold_days, pnl_pct, peak_pnl,
                         signals: [{rule_id, rule_name, urgency, reason}]}
    """
    urgency_map = {
        "URGENT": "🔴🔴 紧急",
        "HIGH": "🔴 强烈",
        "MEDIUM": "🟡 建议",
        "LOW": "🟢 关注",
    }

    total = len(positions)
    urgent_count = sum(
        1 for p in positions
        for s in p.get("signals", [])
        if s.get("urgency") == "URGENT"
    )

    lines = [
        f"## 🚨 卖出信号",
        f"**日期**: {date_str}",
        f"**触发数量**: {total} 只持仓（其中紧急 {urgent_count} 只）",
        "",
    ]

    for p in positions:
        code = p.get("code", "")
        name = p.get("name", "")
        hold_days = p.get("hold_days", 0)
        pnl = p.get("pnl_pct", 0)
        signals = p.get("signals", [])

        pnl_color = "info" if pnl > 0 else "warning"
        lines.append(f"### {name}（{code}）")
        lines.append(f"> 持有: {hold_days} 天　盈亏: <font color=\"{pnl_color}\">{pnl:+.1f}%</font>")
        lines.append("")

        for s in signals:
            urgency = s.get("urgency", "?")
            rule_id = s.get("rule_id", "")
            rule_name = s.get("rule_name", "")
            reason = s.get("reason", "")
            lines.append(
                f"> {urgency_map.get(urgency, urgency)} "
                f"**{rule_id} {rule_name}**"
            )
            if reason:
                lines.append(f"> <font color=\"comment\">{reason}</font>")
        lines.append("")

    lines.append("---")
    lines.append(f"<font color=\"comment\">建议在下一交易日 9:31 前处理</font>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. 黑天鹅预警
# ---------------------------------------------------------------------------

def build_black_swan_alert(
    date_str: str,
    level: int,
    level_name: str,
    triggered_rules: list[dict],
    suggested_actions: list[str],
) -> tuple[str, bool]:
    """构建黑天鹅预警消息。

    Args:
        date_str: 日期
        level: 响应级别 0-3
        level_name: 级别名称
        triggered_rules: 触发的规则列表 [{rule_id, name, severity, detail}]
        suggested_actions: 建议动作列表

    Returns:
        (消息内容, 是否@all)
    """
    severity_map = {
        "CRITICAL": "🔴 🔴",
        "SEVERE": "🔴",
        "HIGH": "🟠",
    }

    lines = [
        f"## ⚠️ 黑天鹅预警 — Level {level} {level_name}",
        f"**日期**: {date_str}",
        f"**级别**: {level_name}",
        "",
        "### 触发规则",
    ]

    for r in triggered_rules:
        sev = severity_map.get(r.get("severity", ""), "🟡")
        lines.append(
            f"> {sev} **{r.get('rule_id', '')}** {r.get('name', '')}"
        )
        if r.get("detail"):
            lines.append(f"> <font color=\"comment\">{r['detail']}</font>")

    lines.append("")
    lines.append("### 建议动作")
    for action in suggested_actions:
        lines.append(f"> - {action}")

    lines.append("")
    lines.append("---")
    lines.append(f"<font color=\"warning\">请立即查看并确认下一步操作</font>")

    at_all = level >= 2  # Level 2+ 触发 @all
    return "\n".join(lines), at_all


# ---------------------------------------------------------------------------
# 4. 每日市场摘要
# ---------------------------------------------------------------------------

def build_daily_summary(
    date_str: str,
    diagnosis: dict,
) -> str:
    """构建每日市场摘要 Markdown 消息。

    Args:
        date_str: 日期
        diagnosis: 诊断数据 {regime, risk_level, up_ratio, sentiment,
                             top_industries, bottom_industries,
                             north_net_flow, main_net_flow, breadth}
    """
    regime_map = {
        "bull": "多头🐂", "bull_bias": "偏多📈",
        "range": "震荡↔️", "bear_bias": "偏空📉", "bear": "空头🐻",
    }
    risk_map = {
        "low": "低🟢", "medium": "中🟡", "high": "高🟠", "critical": "危急🔴",
    }

    regime = diagnosis.get("regime", "?")
    risk = diagnosis.get("risk_level", "?")
    up_ratio = diagnosis.get("up_ratio", 0)
    sentiment = diagnosis.get("sentiment", 0)
    main_net = diagnosis.get("main_net_flow", 0)
    north_net = diagnosis.get("north_net_flow", 0)
    top_inds = diagnosis.get("top_industries", [])
    bottom_inds = diagnosis.get("bottom_industries", [])

    main_str = f"{main_net/1e8:+.1f}亿" if main_net else "—"
    north_str = f"{north_net/1e8:+.1f}亿" if north_net else "—"

    lines = [
        f"## 📋 每日市场摘要",
        f"**日期**: {date_str}",
        "",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 市场体制 | {regime_map.get(regime, regime)} |",
        f"| 风险等级 | {risk_map.get(risk, risk)} |",
        f"| 上涨比例 | {up_ratio:.1%} |",
        f"| 情绪温度 | {sentiment:.0f}/100 |",
        f"| 主力净流入 | {main_str} |",
        f"| 北向净流入 | {north_str} |",
        "",
    ]

    if top_inds:
        inds_str = "、".join(top_inds[:5])
        lines.append(f"**🔥 强势行业**: {inds_str}")
    if bottom_inds:
        inds_str = "、".join(bottom_inds[:5])
        lines.append(f"**❄️ 弱势行业**: {inds_str}")

    lines.append("")
    lines.append("---")
    lines.append(f"<font color=\"comment\">自动生成于 {date_str}</font>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. Pipeline 错误
# ---------------------------------------------------------------------------

def build_pipeline_error(
    stage: str,
    error_msg: str,
    traceback: str = "",
) -> str:
    """构建 Pipeline 错误文本消息。

    Args:
        stage: 出错阶段 (collect/score/buy/sell/diagnosis)
        error_msg: 错误信息
        traceback: 堆栈（截断前 500 字符）
    """
    tb_short = traceback[:500] if traceback else ""
    lines = [
        f"❌ Pipeline 错误",
        f"阶段: {stage}",
        f"错误: {error_msg}",
    ]
    if tb_short:
        lines.append(f"堆栈: {tb_short}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 6. 周度表现总结
# ---------------------------------------------------------------------------

def build_weekly_summary(
    start_date: str,
    end_date: str,
    metrics: dict,
) -> str:
    """构建周度表现总结 Markdown 消息。

    Args:
        start_date: 周起始日期
        end_date: 周结束日期
        metrics: {win_rate, total_pnl, best_pick, worst_pick, max_drawdown, sharpe}
    """
    wr = metrics.get("win_rate", 0)
    pnl = metrics.get("total_pnl", 0)
    best = metrics.get("best_pick", {})
    worst = metrics.get("worst_pick", {})
    dd = metrics.get("max_drawdown", 0)
    sharpe = metrics.get("sharpe", 0)

    pnl_color = "info" if pnl > 0 else "warning"

    lines = [
        f"## 📈 周度表现总结",
        f"**区间**: {start_date} ~ {end_date}",
        "",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 周收益 | <font color=\"{pnl_color}\">{pnl:+.2f}%</font> |",
        f"| 胜率 | {wr:.1%} |",
        f"| 最大回撤 | {dd:.2f}% |",
        f"| 夏普比率 | {sharpe:.2f} |",
        "",
    ]

    if best:
        lines.append(f"**🏆 最佳**: {best.get('name','')}（{best.get('code','')}）+{best.get('pnl',0):.1f}%")
    if worst:
        lines.append(f"**💩 最差**: {worst.get('name','')}（{worst.get('code','')}）{worst.get('pnl',0):.1f}%")

    lines.append("")
    lines.append(f"<font color=\"comment\">自动生成于 {end_date}</font>")

    return "\n".join(lines)
