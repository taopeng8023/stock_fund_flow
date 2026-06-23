"""
黑天鹅监控 — 13 条规则检测极端市场风险，输出 Level 0-3 响应级别。

用法:
  python -m portfolio.black_swan                   # 诊断今天
  python -m portfolio.black_swan --date=20260623   # 指定日期
  python -m portfolio.black_swan --no-notify       # 静默模式，不发通知
  python -m portfolio.black_swan --json            # JSON 输出
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 数据加载（尽量自包含，不依赖 market_diagnosis.py 的内部函数）
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
DATA_ROOT = PROJECT_ROOT / "data"

# ---------------------------------------------------------------------------
# 集中阈值 — 所有规则阈值在这里修改
# ---------------------------------------------------------------------------
THRESHOLDS = {
    "BS-1": {"up_ratio": 0.10, "limit_down": 100},
    "BS-2": {"extreme_outflow_yi": 1000, "outflow_yi": 500, "pos_ratio": 0.15},
    "BS-3": {"flash_crash_pct": -3.0, "consec_pct": -2.0},
    "BS-4": {"limit_down": 300, "avg_vol_ratio": 2.0},
    "BS-5": {"outflow_yi": 100},
    "BS-6": {"flip_count": 3},
    "BS-7": {"median_abs_chg": 5.0},
    "BS-8": {"margin_pos_ratio": 0.20},
    "BS-9": {"frozen": 15, "pessimistic": 30},
    "BS-10": {"up_ratio": 0.85, "extreme": 0.90},
    "BS-11": {"limit_up": 200},
    "BS-12": {"median_ret": 0.5, "pos_flow": 0.35},
    "BS-13": {"high_vol_ratio": 0.4, "median_ret": -1.0},
}


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
# 自包含诊断计算（不依赖 market_diagnosis.py）
# ---------------------------------------------------------------------------

def _compute_metrics(rows: list) -> tuple:
    """单次遍历计算 breadth + fund_flow_stats + abs_chgs（供 BS-7 用）。

    原来 _compute_breadth / _compute_fund_flow_stats / BS-7 各扫一遍 rows，
    现在合并为一次遍历: 3×O(N) → O(N)。
    """
    chgs, f62_vals, f10_vals, f168_vals = [], [], [], []
    abs_chgs = []

    for r in rows:
        f3 = r.get("f3")
        if isinstance(f3, (int, float)):
            chgs.append(f3)
            abs_chgs.append(abs(f3))

        for key, lst in [("f62", f62_vals), ("f10", f10_vals), ("f168", f168_vals)]:
            v = r.get(key)
            if isinstance(v, (int, float)):
                lst.append(v)

    # --- breadth ---
    breadth = {}
    if chgs:
        n = len(chgs)
        up = sum(1 for c in chgs if c > 0)
        chgs_sorted = sorted(chgs)
        m = n // 2
        breadth = {
            "up_ratio": round(up / n, 3),
            "limit_up": sum(1 for c in chgs if c >= 9.5),
            "limit_down": sum(1 for c in chgs if c <= -9.5),
            "median": round(chgs_sorted[m], 2),
        }

    # --- fund_flow_stats ---
    flow = {}
    if f62_vals:
        nf = len(f62_vals)
        flow = {
            "total_main_flow": sum(f62_vals),
            "pos_flow_ratio": round(sum(1 for f in f62_vals if f > 0) / nf, 3),
            "margin_pos_ratio": round(sum(1 for f in f168_vals if f > 0) / len(f168_vals), 3) if f168_vals else 0,
            "avg_vol_ratio": round(sum(f10_vals) / len(f10_vals), 2) if f10_vals else 0,
            "high_vol_ratio": round(sum(1 for f in f10_vals if f > 3) / len(f10_vals), 3) if f10_vals else 0,
        }

    return breadth, flow, abs_chgs


def _compute_sentiment(rows: list, date_str: str = None) -> dict:
    """计算情绪温度计。

    优先从存档诊断读取（保证历史回测一致），当日无存档时才实时计算。
    """
    # 优先用存档诊断中的 sentiment（保证跨日一致）
    diag = _load_diagnosis(date_str) if date_str else None
    if diag and diag.get("sentiment"):
        return diag["sentiment"]

    # 实时计算
    try:
        from data_collector.fetchers.market_sentiment import compute_sentiment, fetch_indices
        index_data = fetch_indices()
        return compute_sentiment(rows, index_data=index_data)
    except Exception:
        return {"score": 50, "indices": {}}


def _compute_north_diag(north_rows: list) -> dict:
    """诊断北向数据是否可用。"""
    if not north_rows:
        return {"available": False, "reason": "数据不可用"}
    latest = north_rows[0]
    net_north = latest.get("net_north", 0)
    if abs(net_north) > 500 or (net_north == 0 and latest.get("net_south", 0) == 0):
        return {"available": False, "reason": "盘中数据被屏蔽，盘后更新"}
    return {
        "available": True,
        "net_north": round(net_north, 1),
    }


# ---------------------------------------------------------------------------
# 规则检测
# ---------------------------------------------------------------------------

class BlackSwanDetector:
    """黑天鹅检测器 — 完全独立，不依赖 market_diagnosis.py。

    所有指标从 raw fund_flow.json / north_flow.json 直接计算。

    用法:
        detector = BlackSwanDetector("20260623")
        result = detector.check()
        # result = {level: 0, level_name: "正常", rules: [...], actions: [...]}
    """

    def __init__(self, date_str: str, preloaded: dict = None):
        """
        Args:
            date_str: 日期 YYYYMMDD
            preloaded: 可选，market_diagnosis 可传入预计算数据避免重复 I/O
                       {breadth, fund_flow, north_flow, sentiment, rows}
        """
        self.date_str = date_str
        self.prev_date = _find_prev_trading_day(date_str)

        # 加载原始数据
        self.fund_flow = _load_fund_flow(date_str) if not preloaded else preloaded.get("rows")
        self.today_industry = _load_industry_flow(date_str)
        self.prev_north = _load_north_flow(self.prev_date) if self.prev_date else None
        self.prev_industry = _load_industry_flow(self.prev_date) if self.prev_date else None

        # 诊断指标 — 优先用预计算值，否则自算
        if preloaded:
            self._breadth = preloaded.get("breadth", {})
            self._fund_flow_stats = preloaded.get("fund_flow", {})
            self._sentiment = preloaded.get("sentiment", {})
            self._north_diag = preloaded.get("north_flow", {})
            self._abs_chgs = preloaded.get("abs_chgs", [])
        else:
            self._breadth, self._fund_flow_stats, self._abs_chgs = (
                _compute_metrics(self.fund_flow) if self.fund_flow else ({}, {}, [])
            )
            self._sentiment = _compute_sentiment(self.fund_flow, date_str) if self.fund_flow else {}
            north_rows = _load_north_flow(date_str)
            self._north_diag = _compute_north_diag(north_rows) if north_rows else {}

        # 历史诊断（仅 BS-3 需要昨日指数，从 diagnosis JSON 加载）
        self.prev_diag = _load_diagnosis(self.prev_date) if self.prev_date else None

        self._rules = []

    # ------------------------------------------------------------------
    # 数据提取辅助
    # ------------------------------------------------------------------

    def _b(self, key: str, default=None):
        """从 breadth 取值。"""
        return self._breadth.get(key, default)

    def _f(self, key: str, default=None):
        """从 fund_flow_stats 取值。"""
        return self._fund_flow_stats.get(key, default)

    # ------------------------------------------------------------------
    # BS-1 宽度熔断 — CRITICAL
    # ------------------------------------------------------------------

    def _check_bs1(self):
        t = THRESHOLDS["BS-1"]
        up_ratio = self._b("up_ratio", 0)
        limit_down = self._b("limit_down", 0)
        triggered = up_ratio < t["up_ratio"] and limit_down > t["limit_down"]
        return self._rule("BS-1", "宽度熔断", "CRITICAL", triggered,
                          detail=f"上涨比 {up_ratio:.1%}（阈值 {t['up_ratio']:.0%}），"
                                 f"跌停 {limit_down} 只（阈值 {t['limit_down']}）",
                          values={"up_ratio": up_ratio, "limit_down": limit_down})

    # ------------------------------------------------------------------
    # BS-2 资金出逃 — 两档
    #   CRITICAL: 主力净流出 > 1000亿（极端出逃，无需其他条件）
    #   SEVERE:   主力净流出 > 500亿 且 正流比 < 15%
    # ------------------------------------------------------------------

    def _check_bs2(self):
        t = THRESHOLDS["BS-2"]
        main_yi = self._f("total_main_flow", 0) / 1e8
        pos_ratio = self._f("pos_flow_ratio", 0)

        if main_yi < -t["extreme_outflow_yi"]:
            severity, triggered = "CRITICAL", True
            tag = f"极端出逃 >{t['extreme_outflow_yi']}亿"
        elif main_yi < -t["outflow_yi"] and pos_ratio < t["pos_ratio"]:
            severity, triggered = "SEVERE", True
            tag = f"阈值 -{t['outflow_yi']}亿"
        else:
            severity, triggered, tag = "CRITICAL", False, f"阈值 -{t['outflow_yi']}亿"

        return self._rule("BS-2", "资金出逃", severity, triggered,
                          detail=f"主力净流入 {main_yi:.0f}亿（{tag}），"
                                 f"正流比 {pos_ratio:.1%}（阈值 {t['pos_ratio']:.0%}）",
                          values={"main_flow_yi": main_yi, "pos_ratio": pos_ratio})

    # ------------------------------------------------------------------
    # BS-3 连续暴跌 — SEVERE（单日 ≥ 3.0% 或 连续2日 ≥ 2.0%）
    # ------------------------------------------------------------------

    def _check_bs3(self):
        t = THRESHOLDS["BS-3"]
        indices = self._sentiment.get("indices", {})
        if not indices:
            return self._rule_skip("BS-3", "连续暴跌", "SEVERE", "缺少指数数据")

        triggered, parts = False, []
        for key, label in [("sh", "上证"), ("sz", "深证"), ("cy", "创业板")]:
            idx = indices.get(key)
            if not idx:
                continue
            today_chg = idx.get("chg_pct", 0)

            if today_chg <= t["flash_crash_pct"]:
                triggered = True
                parts.append(f"{label} 单日闪崩 {today_chg:+.1f}%（阈值 {t['flash_crash_pct']:+.1f}%）")
                continue
            if today_chg > t["consec_pct"]:
                continue

            prev_chg = None
            if self.prev_diag:
                prev_idx = self.prev_diag.get("sentiment", {}).get("indices", {}).get(key)
                if prev_idx:
                    prev_chg = prev_idx.get("chg_pct", 0)

            if prev_chg is not None and prev_chg <= t["consec_pct"]:
                triggered = True
                parts.append(f"{label} 今{today_chg:+.1f}% / 昨{prev_chg:+.1f}%（阈值 {t['consec_pct']:+.1f}%）")
            elif prev_chg is None:
                parts.append(f"{label} 今{today_chg:+.1f}%（昨日数据缺失）")

        return self._rule("BS-3", "连续暴跌", "SEVERE", triggered,
                          detail="; ".join(parts) if parts else "未触发")

    # ------------------------------------------------------------------
    # BS-4 流动性冻结 — CRITICAL
    # ------------------------------------------------------------------

    def _check_bs4(self):
        t = THRESHOLDS["BS-4"]
        limit_down = self._b("limit_down", 0)
        avg_vol = self._f("avg_vol_ratio", 1.0)
        triggered = limit_down > t["limit_down"] and avg_vol > t["avg_vol_ratio"]
        return self._rule("BS-4", "流动性冻结", "CRITICAL", triggered,
                          detail=f"跌停 {limit_down} 只（阈值 {t['limit_down']}），"
                                 f"均量比 {avg_vol:.1f}x（阈值 {t['avg_vol_ratio']}x）",
                          values={"limit_down": limit_down, "avg_vol_ratio": avg_vol})

    # ------------------------------------------------------------------
    # BS-5 北向恐慌 — SEVERE
    # ------------------------------------------------------------------

    def _check_bs5(self):
        t = THRESHOLDS["BS-5"]
        if not self._north_diag.get("available", True):
            return self._rule_skip("BS-5", "北向恐慌", "SEVERE",
                                   f"盘中数据被屏蔽: {self._north_diag.get('reason', '未知')}")

        today_net = self._north_diag.get("net_north") if self._north_diag.get("available") else None
        if today_net is None:
            return self._rule_skip("BS-5", "北向恐慌", "SEVERE", "缺少今日北向数据")
        if today_net > -t["outflow_yi"]:
            return self._rule_ok("BS-5", "北向恐慌", "SEVERE",
                                 f"北向净流入 {today_net:.0f}亿（阈值 -{t['outflow_yi']}亿）")

        prev_net = self.prev_north[0].get("net_north", 0) if self.prev_north else None
        triggered = prev_net is not None and prev_net < -t["outflow_yi"]
        detail = (f"北向 今{today_net:.0f}亿 / 昨{prev_net:.0f}亿（阈值 -{t['outflow_yi']}亿）"
                  if prev_net is not None
                  else f"北向今日{today_net:.0f}亿（昨日数据缺失）")
        return self._rule("BS-5", "北向恐慌", "SEVERE", triggered, detail=detail,
                          values={"today_net": today_net, "prev_net": prev_net})

    # ------------------------------------------------------------------
    # BS-6 板块雪崩 — HIGH
    # ------------------------------------------------------------------

    def _check_bs6(self):
        t = THRESHOLDS["BS-6"]
        if not self.today_industry:
            return self._rule_skip("BS-6", "板块雪崩", "HIGH", "缺少今日行业数据")

        def _flow(r):
            v = r.get("f62") or r.get("主力净流入") or 0
            return float(v) if v else 0

        def _name(r):
            return r.get("f14") or r.get("名称") or "?"

        today_sorted = sorted(self.today_industry, key=_flow, reverse=True)
        top5 = today_sorted[:5]
        top5_names = [_name(r) for r in top5]
        top5_flows = [_flow(r) for r in top5]

        if not all(f < 0 for f in top5_flows):
            return self._rule_ok("BS-6", "板块雪崩", "HIGH",
                                 f"Top5 行业未全部转负: {', '.join(top5_names)}")

        flip_count = 0
        if self.prev_industry:
            prev_map = {_name(r): _flow(r) for r in self.prev_industry}
            flip_count = sum(1 for n, f in zip(top5_names, top5_flows)
                             if prev_map.get(n, 0) > 0 and f < 0)

        triggered = flip_count >= t["flip_count"] if self.prev_industry else True
        return self._rule("BS-6", "板块雪崩", "HIGH", triggered,
                          detail=f"Top5 行业: {', '.join(top5_names)}，"
                                 f"全部净流出，{flip_count} 个从流入翻转为流出",
                          values={"top5": top5_names, "flip_count": flip_count})

    # ------------------------------------------------------------------
    # BS-7 波动率爆炸 — SEVERE
    # ------------------------------------------------------------------

    def _check_bs7(self):
        if not self._abs_chgs:
            return self._rule_skip("BS-7", "波动率爆炸", "SEVERE", "无有效涨跌幅数据")

        abs_chgs = sorted(self._abs_chgs)
        n = len(abs_chgs)
        median_abs = abs_chgs[n // 2] if n % 2 == 1 else (abs_chgs[n // 2 - 1] + abs_chgs[n // 2]) / 2

        t = THRESHOLDS["BS-7"]["median_abs_chg"]
        triggered = median_abs > t
        return self._rule("BS-7", "波动率爆炸", "SEVERE", triggered,
                          detail=f"个股中位涨跌幅绝对值 {median_abs:.2f}%（阈值 {t}%），共 {n} 样本",
                          values={"median_abs_chg": median_abs, "sample_count": n})

    def _check_bs8(self):
        t = THRESHOLDS["BS-8"]
        margin_pos = self._f("margin_pos_ratio", 0.5)
        triggered = margin_pos < t["margin_pos_ratio"]
        return self._rule("BS-8", "融资恐慌", "HIGH", triggered,
                          detail=f"融资正流比 {margin_pos:.1%}（阈值 {t['margin_pos_ratio']:.0%}）",
                          values={"margin_pos_ratio": margin_pos})

    def _check_bs9(self):
        t = THRESHOLDS["BS-9"]
        score = self._sentiment.get("score", 50)
        if score < t["frozen"]:
            severity, triggered, tag = "CRITICAL", True, f"冰冻 <{t['frozen']}"
        elif score < t["pessimistic"]:
            severity, triggered, tag = "SEVERE", True, f"悲观 <{t['pessimistic']}"
        else:
            severity, triggered, tag = "CRITICAL", False, f"阈值 {t['pessimistic']}"
        return self._rule("BS-9", "情绪冰冻", severity, triggered,
                          detail=f"情绪温度计 {score:.0f}/100（{tag}）",
                          values={"sentiment_score": score})

    def _check_bs10(self):
        t = THRESHOLDS["BS-10"]
        up_ratio = self._b("up_ratio", 0)
        triggered = up_ratio > t["up_ratio"]
        severity = "SEVERE" if up_ratio > t["extreme"] else "HIGH"
        return self._rule("BS-10", "极端过热", severity, triggered,
                          detail=f"上涨比 {up_ratio:.1%}（阈值 {t['up_ratio']:.0%}），短期过热风险",
                          values={"up_ratio": up_ratio})

    def _check_bs11(self):
        t = THRESHOLDS["BS-11"]
        limit_up = self._b("limit_up", 0)
        triggered = limit_up > t["limit_up"]
        return self._rule("BS-11", "涨停狂热", "HIGH", triggered,
                          detail=f"涨停 {limit_up} 只（阈值 {t['limit_up']}），情绪极度亢奋",
                          values={"limit_up": limit_up})

    def _check_bs12(self):
        t = THRESHOLDS["BS-12"]
        median_ret = self._b("median", 0)
        pos_flow = self._f("pos_flow_ratio", 0.5)
        triggered = median_ret > t["median_ret"] and pos_flow < t["pos_flow"]
        return self._rule("BS-12", "量价背离", "HIGH", triggered,
                          detail=f"中位涨跌 {median_ret:+.1f}% 但主力正流比仅 {pos_flow:.1%}，资金出逃",
                          values={"median_ret": median_ret, "pos_flow_ratio": pos_flow})

    def _check_bs13(self):
        t = THRESHOLDS["BS-13"]
        high_vol = self._f("high_vol_ratio", 0)
        median_ret = self._b("median", 0)
        triggered = high_vol > t["high_vol_ratio"] and median_ret < t["median_ret"]
        return self._rule("BS-13", "放量暴跌", "HIGH", triggered,
                          detail=f"高量比(>3)占比 {high_vol:.1%}（阈值 {t['high_vol_ratio']:.0%}）且 "
                                 f"中位跌幅 {median_ret:+.1f}%（阈值 {t['median_ret']:+.1f}%）",
                          values={"high_vol_ratio": high_vol, "median_ret": median_ret})

    # ------------------------------------------------------------------
    # 规则结果构建器
    # ------------------------------------------------------------------

    @staticmethod
    def _rule(rule_id: str, name: str, severity: str, triggered: bool,
              detail: str = "", values: dict = None, skipped: bool = False):
        """统一构建规则检测结果 dict。"""
        return {
            "rule_id": rule_id, "name": name,
            "severity": severity, "triggered": triggered,
            "detail": detail, "values": values or {},
            **({"skipped": True} if skipped else {}),
        }

    @classmethod
    def _rule_skip(cls, rule_id, name, severity, reason):
        return cls._rule(rule_id, name, severity, False,
                         detail=f"⏭️ {reason}", skipped=True)

    @classmethod
    def _rule_ok(cls, rule_id, name, severity, detail):
        return cls._rule(rule_id, name, severity, False, detail=detail)

    # ------------------------------------------------------------------
    # 主检测
    # ------------------------------------------------------------------

    def check(self) -> dict:
        """执行全部 13 条规则，返回检测结果。"""
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
            self._check_bs10(),
            self._check_bs11(),
            self._check_bs12(),
            self._check_bs13(),
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

def _ensure_data(date_str: str) -> bool:
    """确保 fund_flow.json 存在。缺失时只采集黑天鹅必需的 3/10 个模块。

    黑天鹅 13 条规则需要的数据源:
      fund_flow.json   (个股资金流)      → 所有 BS 规则的基础数据
      north_flow.json  (北向资金)        → BS-5
      industry_flow.*  (行业资金流)      → BS-6

    Returns:
        True if data is ready, False if unrecoverable.
    """
    if _load_fund_flow(date_str):
        return True

    print(f"⚡ {date_str} 无数据，采集必需模块 (3/10)...")
    try:
        from data_collector.fetchers.fund_flow import fetch as fetch_fund_flow
        from data_collector.fetchers.sector_flow import fetch as fetch_sector_flow
        from data_collector.fetchers.north_flow import fetch as fetch_north_flow

        for name, fetcher in [("个股资金流", fetch_fund_flow),
                               ("行业资金流", fetch_sector_flow),
                               ("北向资金", fetch_north_flow)]:
            print(f"   📡 {name}...", end=" ", flush=True)
            try:
                fetcher(date_str)
                print("✅")
            except Exception as e:
                print(f"❌ {e}")
                return False
    except ImportError as e:
        print(f"❌ 无法导入采集模块: {e}")
        print(f"   请手动运行: python -m data_collector.main --date={date_str}")
        return False

    if not _load_fund_flow(date_str):
        print(f"❌ 采集完成但数据文件未生成，请检查网络")
        return False
    print(f"   ✅ 数据就绪（3/10 模块，耗时远小于全量采集）")
    return True


def main():
    parser = argparse.ArgumentParser(description="黑天鹅风险监控")
    parser.add_argument("--date", default=None, help="日期 YYYYMMDD（默认今天）")
    parser.add_argument("--no-notify", action="store_true", help="静默模式，不推送")
    parser.add_argument("--no-collect", action="store_true", help="禁止自动采集，数据缺失直接报错")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    date_str = args.date
    if not date_str:
        from datetime import datetime
        date_str = datetime.now().strftime("%Y%m%d")

    # 确保数据就绪
    if args.no_collect:
        if not _load_fund_flow(date_str):
            print(f"❌ {date_str} 无 fund_flow.json，请先运行: python -m data_collector.main --date={date_str}")
            sys.exit(1)
    else:
        if not _ensure_data(date_str):
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
