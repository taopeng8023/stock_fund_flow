"""
黑天鹅监控 — 9 条规则检测极端市场风险，输出 Level 0-3 响应级别。

用法:
  python -m portfolio.black_swan                   # 诊断今天
  python -m portfolio.black_swan --date=20260623   # 指定日期
  python -m portfolio.black_swan --no-notify       # 静默模式，不发通知
  python -m portfolio.black_swan --json            # JSON 输出

检测规则:

  级别 CRITICAL（触发 Level 3）:
    BS-1 宽度熔断  — 上涨比 < 10% 且跌停 > 100
    BS-2 资金出逃  — 主力净流出 > 500亿 且正流比 < 15%
    BS-4 流动性冻结 — 跌停 > 300 且量比 > 2.0
    BS-9 情绪冰冻  — 情绪温度计 < 15

  级别 SEVERE（触发 Level 2+）:
    BS-3 连续暴跌  — 上证/深证/创业板连续2日跌幅 ≥ 2.5%
    BS-5 北向恐慌  — 北向连续2日净流出 > 100亿
    BS-7 波动率爆炸 — 个股中位涨跌幅绝对值 > 5%

  级别 HIGH（触发 Level 1+）:
    BS-6 板块雪崩  — Top5 行业全部从流入翻转为流出
    BS-8 融资恐慌  — 融资正流比 < 20%

响应级别:
  Level 0 正常   — 无规则触发
  Level 1 关注   — 1+ 条 HIGH，新仓打7折
  Level 2 警告   — 1 条 SEVERE 或 3+ HIGH，禁止新买入，止损收紧至-3%
  Level 3 紧急   — 1 条 CRITICAL 或 3+ SEVERE，全部审查，@all 推送
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 数据加载（尽量自包含，不依赖 market_diagnosis.py 的内部函数）
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
DATA_ROOT = PROJECT_ROOT / "data"


def _load_json(date_str: str, name: str) -> Optional[list]:
    """加载 data/<date>/<name>.json，不存在返回 None。"""
    path = DATA_ROOT / date_str / f"{name}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_diagnosis(date_str: str) -> Optional[dict]:
    """加载 data/<date>/diagnosis/latest.json，不存在返回 None。"""
    path = DATA_ROOT / date_str / "diagnosis" / "latest.json"
    if not path.exists():
        # 尝试其他诊断文件
        diag_dir = DATA_ROOT / date_str / "diagnosis"
        if diag_dir.exists():
            files = sorted(diag_dir.glob("diagnosis_*.json"))
            if files:
                path = files[-1]
        else:
            return None
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_north_flow(date_str: str) -> Optional[list]:
    """加载 data/<date>/north_flow.json。"""
    return _load_json(date_str, "north_flow")


def _load_fund_flow(date_str: str) -> Optional[list]:
    """加载 data/<date>/fund_flow.json。"""
    return _load_json(date_str, "fund_flow")


def _load_industry_flow(date_str: str) -> Optional[list]:
    """加载 data/<date>/industry_flow.json 或 CSV。"""
    data = _load_json(date_str, "industry_flow")
    if data is not None:
        return data
    # 尝试 CSV
    csv_path = DATA_ROOT / date_str / "industry_flow.csv"
    if csv_path.exists():
        import csv
        with open(csv_path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    return None


def _find_prev_trading_day(date_str: str) -> Optional[str]:
    """找前一个交易日（简单跳过周末）。"""
    from datetime import datetime, timedelta
    dt = datetime.strptime(date_str, "%Y%m%d")
    # 最多往回找 7 天
    for _ in range(7):
        dt = dt - timedelta(days=1)
        prev = dt.strftime("%Y%m%d")
        # 跳过周六(5)周日(6)
        if dt.weekday() >= 5:
            continue
        # 检查是否有 fund_flow 数据
        if (DATA_ROOT / prev / "fund_flow.json").exists():
            return prev
    return None


# ---------------------------------------------------------------------------
# 规则检测
# ---------------------------------------------------------------------------

class BlackSwanDetector:
    """黑天鹅检测器。

    用法:
        detector = BlackSwanDetector("20260623")
        result = detector.check()
        # result = {level: 0, level_name: "正常", rules: [...], actions: [...]}
    """

    def __init__(self, date_str: str):
        self.date_str = date_str
        self.prev_date = _find_prev_trading_day(date_str)

        # 加载数据
        self.diag = _load_diagnosis(date_str)
        self.fund_flow = _load_fund_flow(date_str)
        self.north = _load_north_flow(date_str)

        # 历史数据（可能为 None）
        self.prev_diag = _load_diagnosis(self.prev_date) if self.prev_date else None
        self.prev_north = _load_north_flow(self.prev_date) if self.prev_date else None
        self.prev_industry = _load_industry_flow(self.prev_date) if self.prev_date else None
        self.today_industry = _load_industry_flow(date_str)

        self._rules = []

    # ------------------------------------------------------------------
    # 数据提取辅助
    # ------------------------------------------------------------------

    def _b(self, key: str, default=None):
        """从 breadth 段取值。"""
        if self.diag and "breadth" in self.diag:
            return self.diag["breadth"].get(key, default)
        return default

    def _f(self, key: str, default=None):
        """从 fund_flow 段取值。"""
        if self.diag and "fund_flow" in self.diag:
            return self.diag["fund_flow"].get(key, default)
        return default

    # ------------------------------------------------------------------
    # BS-1 宽度熔断 — CRITICAL
    # ------------------------------------------------------------------

    def _check_bs1(self):
        up_ratio = self._b("up_ratio", 0)
        limit_down = self._b("limit_down", 0)
        triggered = up_ratio < 0.10 and limit_down > 100
        return {
            "rule_id": "BS-1",
            "name": "宽度熔断",
            "severity": "CRITICAL",
            "triggered": triggered,
            "detail": (
                f"上涨比 {up_ratio:.1%}（阈值 10%），"
                f"跌停 {limit_down} 只（阈值 100）"
            ),
            "values": {"up_ratio": up_ratio, "limit_down": limit_down},
        }

    # ------------------------------------------------------------------
    # BS-2 资金出逃 — 两档
    #   CRITICAL: 主力净流出 > 1000亿（极端出逃，无需其他条件）
    #   SEVERE:   主力净流出 > 500亿 且 正流比 < 15%
    # ------------------------------------------------------------------

    def _check_bs2(self):
        total_main = self._f("total_main_flow", 0)
        main_yi = total_main / 1e8  # yuan → 亿
        pos_ratio = self._f("pos_flow_ratio", 0)

        if main_yi < -1000:
            severity = "CRITICAL"
            triggered = True
        elif main_yi < -500 and pos_ratio < 0.15:
            severity = "SEVERE"
            triggered = True
        else:
            severity = "CRITICAL"  # 默认标 severity
            triggered = False

        return {
            "rule_id": "BS-2",
            "name": "资金出逃",
            "severity": severity,
            "triggered": triggered,
            "detail": (
                f"主力净流入 {main_yi:.0f}亿"
                f"{'（极端出逃 >1000亿）' if main_yi < -1000 else '（阈值 -500亿）'}，"
                f"正流比 {pos_ratio:.1%}（阈值 15%）"
            ),
            "values": {"main_flow_yi": main_yi, "pos_ratio": pos_ratio},
        }

    # ------------------------------------------------------------------
    # BS-3 连续暴跌 — SEVERE（单日 ≥ 3.0% 或 连续2日 ≥ 2.0%）
    # ------------------------------------------------------------------

    def _check_bs3(self):
        if not self.diag or self.diag.get("sentiment") is None:
            return self._rule_skip("BS-3", "连续暴跌", "SEVERE", "缺少今日诊断数据")

        indices = self.diag.get("sentiment", {}).get("indices", {})
        triggered = False
        detail_parts = []

        for key, label in [("sh", "上证"), ("sz", "深证"), ("cy", "创业板")]:
            idx = indices.get(key)
            if not idx:
                continue
            today_chg = idx.get("chg_pct", 0)

            # 单日闪崩 ≥ 3%
            if today_chg <= -3.0:
                triggered = True
                detail_parts.append(f"{label} 单日闪崩 {today_chg:+.1f}%（阈值 -3.0%）")
                continue

            # 连续 2 日 ≥ 2.0%
            if today_chg > -2.0:
                continue

            prev_chg = None
            if self.prev_diag:
                prev_idx = (
                    self.prev_diag.get("sentiment", {})
                    .get("indices", {})
                    .get(key)
                )
                if prev_idx:
                    prev_chg = prev_idx.get("chg_pct", 0)

            if prev_chg is not None and prev_chg <= -2.0:
                triggered = True
                detail_parts.append(
                    f"{label} 今{today_chg:+.1f}% / 昨{prev_chg:+.1f}%（阈值 -2.0%）"
                )
            elif prev_chg is None:
                detail_parts.append(
                    f"{label} 今{today_chg:+.1f}%（昨日数据缺失，仅观察）"
                )

        return {
            "rule_id": "BS-3",
            "name": "连续暴跌",
            "severity": "SEVERE",
            "triggered": triggered,
            "detail": "; ".join(detail_parts) if detail_parts else "未触发",
            "values": {},
        }

    # ------------------------------------------------------------------
    # BS-4 流动性冻结 — CRITICAL
    # ------------------------------------------------------------------

    def _check_bs4(self):
        limit_down = self._b("limit_down", 0)
        avg_vol = self._f("avg_vol_ratio", 1.0)
        triggered = limit_down > 300 and avg_vol > 2.0
        return {
            "rule_id": "BS-4",
            "name": "流动性冻结",
            "severity": "CRITICAL",
            "triggered": triggered,
            "detail": (
                f"跌停 {limit_down} 只（阈值 300），"
                f"均量比 {avg_vol:.1f}x（阈值 2.0x）"
            ),
            "values": {"limit_down": limit_down, "avg_vol_ratio": avg_vol},
        }

    # ------------------------------------------------------------------
    # BS-5 北向恐慌 — SEVERE
    # ------------------------------------------------------------------

    def _check_bs5(self):
        # 检查诊断中的北向数据是否可用（盘中可能被屏蔽）
        if self.diag:
            nf_diag = self.diag.get("north_flow", {})
            if not nf_diag.get("available", True):
                return self._rule_skip("BS-5", "北向恐慌", "SEVERE",
                                       f"盘中数据被屏蔽: {nf_diag.get('reason', '未知')}")

        if not self.north or len(self.north) == 0:
            return self._rule_skip("BS-5", "北向恐慌", "SEVERE", "缺少今日北向数据")

        today_net = self.north[0].get("net_north", 0)  # 单位：亿
        if today_net > -100:
            return self._rule_ok("BS-5", "北向恐慌", "SEVERE",
                                 f"北向净流入 {today_net:.0f}亿（阈值 -100亿）")

        # 检查昨天
        prev_net = None
        if self.prev_north and len(self.prev_north) > 0:
            prev_net = self.prev_north[0].get("net_north", 0)

        triggered = prev_net is not None and prev_net < -100
        detail = (
            f"北向 今{today_net:.0f}亿 / 昨{prev_net:.0f}亿（阈值 -100亿）"
            if prev_net is not None
            else f"北向今日{today_net:.0f}亿（昨日数据缺失，仅观察）"
        )
        return {
            "rule_id": "BS-5",
            "name": "北向恐慌",
            "severity": "SEVERE",
            "triggered": triggered,
            "detail": detail,
            "values": {"today_net": today_net, "prev_net": prev_net},
        }

    # ------------------------------------------------------------------
    # BS-6 板块雪崩 — HIGH
    # ------------------------------------------------------------------

    def _check_bs6(self):
        if not self.today_industry:
            return self._rule_skip("BS-6", "板块雪崩", "HIGH", "缺少今日行业数据")

        # 取今日 f62 最大的前 5 个行业
        def _flow(r):
            """提取主力净流入 — 兼容 JSON (f62) 和 CSV (主力净流入)。"""
            v = r.get("f62") or r.get("主力净流入") or 0
            return float(v) if v else 0

        def _name(r):
            """提取行业名称 — 兼容 JSON (f14) 和 CSV (名称)。"""
            return r.get("f14") or r.get("名称") or "?"

        today_sorted = sorted(self.today_industry, key=_flow, reverse=True)
        top5 = today_sorted[:5]

        # 检查是否全部为负
        top5_names = [_name(r) for r in top5]
        top5_flows = [_flow(r) for r in top5]
        all_negative = all(f < 0 for f in top5_flows)

        if not all_negative:
            return self._rule_ok("BS-6", "板块雪崩", "HIGH",
                                 f"Top5 行业未全部转负: {', '.join(top5_names)}")

        # 如果有昨日数据，检查是否从正翻负
        flip_count = 0
        if self.prev_industry:
            prev_map = {_name(r): _flow(r) for r in self.prev_industry}
            for name, flow in zip(top5_names, top5_flows):
                prev_f = prev_map.get(name, 0)
                if prev_f > 0 and flow < 0:
                    flip_count += 1

        triggered = flip_count >= 3 if self.prev_industry else all_negative
        detail = (
            f"Top5 行业: {', '.join(top5_names)}，"
            f"全部净流出，{flip_count} 个从流入翻转为流出"
        )
        return {
            "rule_id": "BS-6",
            "name": "板块雪崩",
            "severity": "HIGH",
            "triggered": triggered,
            "detail": detail,
            "values": {"top5": top5_names, "flip_count": flip_count},
        }

    # ------------------------------------------------------------------
    # BS-7 波动率爆炸 — SEVERE
    # ------------------------------------------------------------------

    def _check_bs7(self):
        if not self.fund_flow:
            return self._rule_skip("BS-7", "波动率爆炸", "SEVERE", "缺少资金流数据")

        # 计算 median(abs(f3))
        abs_chgs = []
        for r in self.fund_flow:
            f3 = r.get("f3")
            if f3 is None:
                continue
            try:
                abs_chgs.append(abs(float(f3)))
            except (ValueError, TypeError):
                continue

        if not abs_chgs:
            return self._rule_skip("BS-7", "波动率爆炸", "SEVERE", "无有效涨跌幅数据")

        abs_chgs.sort()
        n = len(abs_chgs)
        if n % 2 == 1:
            median_abs = abs_chgs[n // 2]
        else:
            median_abs = (abs_chgs[n // 2 - 1] + abs_chgs[n // 2]) / 2

        triggered = median_abs > 5.0
        return {
            "rule_id": "BS-7",
            "name": "波动率爆炸",
            "severity": "SEVERE",
            "triggered": triggered,
            "detail": (
                f"个股中位涨跌幅绝对值 {median_abs:.2f}%（阈值 5%），"
                f"共 {n} 只有效样本"
            ),
            "values": {"median_abs_chg": median_abs, "sample_count": n},
        }

    # ------------------------------------------------------------------
    # BS-8 融资恐慌 — HIGH
    # ------------------------------------------------------------------

    def _check_bs8(self):
        margin_pos = self._f("margin_pos_ratio", 0.5)
        triggered = margin_pos < 0.20
        return {
            "rule_id": "BS-8",
            "name": "融资恐慌",
            "severity": "HIGH",
            "triggered": triggered,
            "detail": f"融资正流比 {margin_pos:.1%}（阈值 20%）",
            "values": {"margin_pos_ratio": margin_pos},
        }

    # ------------------------------------------------------------------
    # BS-9 情绪冰冻 — 两档
    #   CRITICAL: 情绪 < 15（完全冰冻）
    #   SEVERE:   情绪 < 30（极度悲观）
    # ------------------------------------------------------------------

    def _check_bs9(self):
        if not self.diag:
            return self._rule_skip("BS-9", "情绪冰冻", "CRITICAL", "缺少诊断数据")

        score = self.diag.get("sentiment", {}).get("score", 50)
        if score < 15:
            severity, triggered = "CRITICAL", True
        elif score < 30:
            severity, triggered = "SEVERE", True
        else:
            severity, triggered = "CRITICAL", False

        return {
            "rule_id": "BS-9",
            "name": "情绪冰冻",
            "severity": severity,
            "triggered": triggered,
            "detail": (
                f"情绪温度计 {score:.0f}/100"
                f"{'（冰冻 <15）' if score < 15 else '（悲观 <30）' if score < 30 else '（阈值 30）'}"
            ),
            "values": {"sentiment_score": score},
        }

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _rule_skip(rule_id, name, severity, reason):
        return {
            "rule_id": rule_id, "name": name, "severity": severity,
            "triggered": False, "detail": f"⏭️ {reason}", "values": {},
            "skipped": True,
        }

    @staticmethod
    def _rule_ok(rule_id, name, severity, detail):
        return {
            "rule_id": rule_id, "name": name, "severity": severity,
            "triggered": False, "detail": detail, "values": {},
        }

    # ------------------------------------------------------------------
    # 主检测
    # ------------------------------------------------------------------

    def check(self) -> dict:
        """执行全部 9 条规则，返回检测结果。"""
        self._rules = [
            self._check_bs1(),
            self._check_bs2(),
            self._check_bs3(),
            self._check_bs4(),
            self._check_bs5(),
            self._check_bs6(),
            self._check_bs7(),
            self._check_bs8(),
            self._check_bs9(),
        ]

        triggered = [r for r in self._rules if r["triggered"]]
        critical_count = sum(1 for r in triggered if r["severity"] == "CRITICAL")
        severe_count = sum(1 for r in triggered if r["severity"] == "SEVERE")
        high_count = sum(1 for r in triggered if r["severity"] == "HIGH")

        # 响应级别判定
        if critical_count >= 1 or severe_count >= 3:
            level = 3
            level_name = "紧急"
        elif severe_count >= 1 or high_count >= 3:
            level = 2
            level_name = "警告"
        elif high_count >= 1:
            level = 1
            level_name = "关注"
        else:
            level = 0
            level_name = "正常"

        # 建议动作
        actions = self._build_actions(level, critical_count, severe_count)

        return {
            "date": self.date_str,
            "prev_date": self.prev_date,
            "level": level,
            "level_name": level_name,
            "triggered_count": len(triggered),
            "summary": {
                "critical": critical_count,
                "severe": severe_count,
                "high": high_count,
            },
            "rules": self._rules,
            "triggered_rules": triggered,
            "actions": actions,
        }

    def _build_actions(self, level, critical_count, severe_count):
        actions = []
        if level == 0:
            actions.append("正常运行，无需特殊操作")
        elif level == 1:
            actions.append("新仓仓位打 7 折")
        elif level == 2:
            actions.append("禁止新买入")
            actions.append("止损收紧至 -3%")
            actions.append("已有持仓逐只审查")
        elif level == 3:
            actions.append("🚨 禁止一切买入")
            actions.append("全部持仓标记审查")
            actions.append("紧急卖出 UR-2 级别信号")
            actions.append("建议立即减仓至 30% 以下")
        return actions


# ---------------------------------------------------------------------------
# 通知
# ---------------------------------------------------------------------------

def _notify(result: dict):
    """将检测结果推送到企业微信。"""
    try:
        from notify.message_builder import build_black_swan_alert, dedup_key
        from notify.wecom_sender import send_text, send_markdown
    except ImportError:
        print("  ⚠️ 通知模块不可用，跳过推送")
        return

    level = result["level"]
    if level == 0:
        return  # Level 0 不推送

    date_str = result["date"]
    triggered = result["triggered_rules"]

    # 构建消息
    msg, at_all = build_black_swan_alert(
        date_str=date_str,
        level=level,
        level_name=result["level_name"],
        triggered_rules=triggered,
        suggested_actions=result["actions"],
    )

    # Level 2+ 先发一条 @all 文本
    if at_all:
        send_text(
            f"⚠️ 量化系统黑天鹅预警 — Level {level} {result['level_name']}",
            mentioned_list=["@all"],
            dedup_key=dedup_key("black_swan_text", date_str),
        )

    # 发 Markdown 详情
    send_markdown(
        msg,
        dedup_key=dedup_key("black_swan_detail", date_str),
    )
    print(f"  📤 企业微信通知已发送（Level {level}, @all={at_all}）")


# ---------------------------------------------------------------------------
# 格式化输出
# ---------------------------------------------------------------------------

def _print_result(result: dict):
    """终端格式化输出。"""
    level = result["level"]
    color = {0: "🟢", 1: "🟡", 2: "🟠", 3: "🔴"}.get(level, "⚪")

    print(f"\n{'='*60}")
    print(f"  {color} Level {level} — {result['level_name']}")
    print(f"  日期: {result['date']}  前交易日: {result['prev_date'] or 'N/A'}")
    print(f"  触发: CRITICAL={result['summary']['critical']} "
          f"SEVERE={result['summary']['severe']} "
          f"HIGH={result['summary']['high']}")
    print(f"{'='*60}")

    print("\n  规则检测明细:")
    print(f"  {'规则':<6s} {'严重度':<10s} {'触发':<6s} 详情")
    print(f"  {'-'*4} {'-'*8} {'-'*4} {'-'*40}")
    for r in result["rules"]:
        flag = "⚠️" if r["triggered"] else "　"
        skipped = r.get("skipped", False)
        status = "跳过" if skipped else ("触发" if r["triggered"] else "—")
        detail = r["detail"][:80]
        print(f"  {r['rule_id']:<6s} {r['severity']:<10s} {status:<6s} {flag} {detail}")

    print(f"\n  建议动作:")
    for a in result["actions"]:
        print(f"    → {a}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="黑天鹅风险监控")
    parser.add_argument("--date", default=None, help="日期 YYYYMMDD（默认今天）")
    parser.add_argument("--no-notify", action="store_true", help="静默模式，不推送")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    date_str = args.date
    if not date_str:
        from datetime import datetime
        date_str = datetime.now().strftime("%Y%m%d")

    # 检查数据是否存在
    if not (_load_diagnosis(date_str) or _load_fund_flow(date_str)):
        print(f"❌ {date_str} 无诊断数据，请先运行: python market_diagnosis.py --date={date_str}")
        sys.exit(1)

    detector = BlackSwanDetector(date_str)
    result = detector.check()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        _print_result(result)

    # 推送通知
    if not args.no_notify and result["level"] >= 1:
        _notify(result)


if __name__ == "__main__":
    main()
