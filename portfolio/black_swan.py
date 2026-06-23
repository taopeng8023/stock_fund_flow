"""
黑天鹅监控 — 13 条规则检测极端市场风险，输出 Level 0-3 响应级别。

用法:
  python -m portfolio.black_swan                     # 诊断今天
  python -m portfolio.black_swan --date=20260623     # 指定日期
  python -m portfolio.black_swan --no-notify         # 静默模式，不发通知
  python -m portfolio.black_swan --json              # JSON 输出
  python -m portfolio.black_swan --disable BS-11,BS-13  # 禁用指定规则

环境变量:
  BS_DISABLE_RULES=BS-11,BS-13   禁用的规则列表
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 数据加载
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

# 规则注册表 — rule_id → (method_name, 默认启用)
_RULE_REGISTRY = [
    ("BS-1", "_check_bs1", True),
    ("BS-2", "_check_bs2", True),
    ("BS-3", "_check_bs3", True),
    ("BS-4", "_check_bs4", True),
    ("BS-5", "_check_bs5", True),
    ("BS-6", "_check_bs6", True),
    ("BS-7", "_check_bs7", True),
    ("BS-8", "_check_bs8", True),
    ("BS-9", "_check_bs9", True),
    ("BS-10", "_check_bs10", True),
    ("BS-11", "_check_bs11", True),
    ("BS-12", "_check_bs12", True),
    ("BS-13", "_check_bs13", True),
]


def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def _is_today(date_str: str) -> bool:
    return date_str == _today_str()


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _load_json(date_str: str, name: str) -> Optional[list]:
    path = DATA_ROOT / date_str / f"{name}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_diagnosis(date_str: str) -> Optional[dict]:
    path = DATA_ROOT / date_str / "diagnosis" / "latest.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_north_flow(date_str: str) -> Optional[list]:
    return _load_json(date_str, "north_flow")


def _load_fund_flow(date_str: str) -> Optional[list]:
    return _load_json(date_str, "fund_flow")


def _load_industry_flow(date_str: str) -> Optional[list]:
    data = _load_json(date_str, "industry_flow")
    if data is not None:
        return data
    csv_path = DATA_ROOT / date_str / "industry_flow.csv"
    if csv_path.exists():
        import csv
        with open(csv_path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    return None


def _find_prev_trading_day(date_str: str) -> Optional[str]:
    """找前一个交易日 — 跳过周末 + 以 fund_flow.json 存在为准（自动处理节假日）。

    最多回溯 30 天，覆盖春节/国庆等长假期。
    """
    dt = datetime.strptime(date_str, "%Y%m%d")
    for _ in range(30):
        dt -= timedelta(days=1)
        if dt.weekday() >= 5:  # 周六日
            continue
        prev = dt.strftime("%Y%m%d")
        if (DATA_ROOT / prev / "fund_flow.json").exists():
            return prev
    return None


# ---------------------------------------------------------------------------
# 诊断计算
# ---------------------------------------------------------------------------

def _compute_metrics(rows: list) -> tuple:
    """单次遍历计算 breadth + fund_flow_stats + abs_chgs。"""
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


def _compute_sentiment(rows: list, date_str: str) -> dict:
    """计算情绪温度计。

    策略：
      - 有存档诊断 → 直接用（保证历史回测一致）
      - 无存档但是今天 → 实时计算（正常模式）
      - 无存档且历史日期 → 返回空 indices（跳过 BS-3），只给 score
    """
    diag = _load_diagnosis(date_str)
    if diag and diag.get("sentiment"):
        return diag["sentiment"]

    if _is_today(date_str):
        try:
            from data_collector.fetchers.market_sentiment import compute_sentiment, fetch_indices
            result = compute_sentiment(rows, index_data=fetch_indices())
            _save_sentiment_cache(date_str, result)
            return result
        except Exception:
            return {"score": 50, "indices": {}}

    # 历史日期无存档 — 只返回 score，不给 indices（避免用实时指数污染回测）
    try:
        from data_collector.fetchers.market_sentiment import compute_sentiment
        result = compute_sentiment(rows, index_data={})  # 无指数数据
        return result  # score 可用，indices 为空 → BS-3 会跳过
    except Exception:
        return {"score": 50, "indices": {}}


def _save_sentiment_cache(date_str: str, sentiment: dict):
    """保存 sentiment 到 data/<date>/ 目录，下次不用重算。"""
    cache_dir = DATA_ROOT / date_str
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "sentiment_cache.json"
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(sentiment, f, ensure_ascii=False)
    except Exception:
        pass  # 缓存失败不影响主流程


def _compute_north_diag(north_rows: list) -> dict:
    if not north_rows:
        return {"available": False, "reason": "数据不可用"}
    latest = north_rows[0]
    net_north = latest.get("net_north", 0)
    if abs(net_north) > 500 or (net_north == 0 and latest.get("net_south", 0) == 0):
        return {"available": False, "reason": "盘中数据被屏蔽，盘后更新"}
    return {"available": True, "net_north": round(net_north, 1)}


# ---------------------------------------------------------------------------
# 并行数据采集
# ---------------------------------------------------------------------------

_FETCHERS = [
    ("个股资金流", "data_collector.fetchers.fund_flow"),
    ("行业资金流", "data_collector.fetchers.sector_flow"),
    ("北向资金",   "data_collector.fetchers.north_flow"),
]

def _fetch_one(name: str, module_path: str, date_str: str) -> tuple:
    """单个模块采集，返回 (name, ok, error)。"""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        mod.fetch(date_str)
        return (name, True, "")
    except Exception as e:
        return (name, False, str(e))


def _ensure_data(date_str: str) -> bool:
    """确保 fund_flow.json 存在。缺失时并行采集 3 个模块。"""
    if _load_fund_flow(date_str):
        return True

    print(f"⚡ {date_str} 无数据，并行采集 (3/10)...")
    ok_count = 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_fetch_one, name, mod, date_str): name
                   for name, mod in _FETCHERS}
        for f in as_completed(futures):
            name, ok, err = f.result()
            icon = "✅" if ok else f"❌ {err}"
            if ok:
                ok_count += 1
            print(f"   📡 {name}... {icon}")

    if ok_count == 0:
        print(f"❌ 全部采集失败，请手动运行: python -m data_collector.main --date={date_str}")
        return False
    if not _load_fund_flow(date_str):
        print(f"❌ fund_flow 采集失败，请检查网络")
        return False
    print(f"   ✅ 数据就绪（{ok_count}/3 模块，并行耗时 ~最长单个模块）")
    return True


# ---------------------------------------------------------------------------
# 规则检测器
# ---------------------------------------------------------------------------

class BlackSwanDetector:
    """黑天鹅检测器 — 完全独立，零外部依赖。"""

    def __init__(self, date_str: str, preloaded: dict = None, disabled_rules: set = None):
        self.date_str = date_str
        self.prev_date = _find_prev_trading_day(date_str)
        self._disabled = disabled_rules or set()

        # 加载原始数据
        self.fund_flow = _load_fund_flow(date_str) if not preloaded else preloaded.get("rows")
        self.today_industry = _load_industry_flow(date_str)
        self.prev_north = _load_north_flow(self.prev_date) if self.prev_date else None
        self.prev_industry = _load_industry_flow(self.prev_date) if self.prev_date else None

        # 诊断指标
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

        self.prev_diag = _load_diagnosis(self.prev_date) if self.prev_date else None
        self._rules = []

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _b(self, key, default=None):
        return self._breadth.get(key, default)

    def _f(self, key, default=None):
        return self._fund_flow_stats.get(key, default)

    def _enabled(self, rule_id: str) -> bool:
        return rule_id not in self._disabled

    # ------------------------------------------------------------------
    # 规则
    # ------------------------------------------------------------------

    def _check_bs1(self):
        t = THRESHOLDS["BS-1"]
        up_ratio, limit_down = self._b("up_ratio", 0), self._b("limit_down", 0)
        ok = up_ratio < t["up_ratio"] and limit_down > t["limit_down"]
        return self._rule("BS-1", "宽度熔断", "CRITICAL", ok,
                          f"上涨比 {up_ratio:.1%}（阈值 {t['up_ratio']:.0%}），"
                          f"跌停 {limit_down} 只（阈值 {t['limit_down']}）",
                          {"up_ratio": up_ratio, "limit_down": limit_down})

    def _check_bs2(self):
        t = THRESHOLDS["BS-2"]
        main_yi = self._f("total_main_flow", 0) / 1e8
        pos = self._f("pos_flow_ratio", 0)
        if main_yi < -t["extreme_outflow_yi"]:
            sev, ok, tag = "CRITICAL", True, f"极端出逃 >{t['extreme_outflow_yi']}亿"
        elif main_yi < -t["outflow_yi"] and pos < t["pos_ratio"]:
            sev, ok, tag = "SEVERE", True, f"阈值 -{t['outflow_yi']}亿"
        else:
            sev, ok, tag = "CRITICAL", False, f"阈值 -{t['outflow_yi']}亿"
        return self._rule("BS-2", "资金出逃", sev, ok,
                          f"主力净流入 {main_yi:.0f}亿（{tag}），"
                          f"正流比 {pos:.1%}（阈值 {t['pos_ratio']:.0%}）",
                          {"main_flow_yi": main_yi, "pos_ratio": pos})

    def _check_bs3(self):
        t = THRESHOLDS["BS-3"]
        indices = self._sentiment.get("indices", {})
        if not indices:
            return self._rule_skip("BS-3", "连续暴跌", "SEVERE", "缺少指数数据（历史日期无存档）")
        ok, parts = False, []
        for key, label in [("sh", "上证"), ("sz", "深证"), ("cy", "创业板")]:
            idx = indices.get(key)
            if not idx:
                continue
            today = idx.get("chg_pct", 0)
            if today <= t["flash_crash_pct"]:
                ok = True
                parts.append(f"{label} 单日闪崩 {today:+.1f}%（阈值 {t['flash_crash_pct']:+.1f}%）")
                continue
            if today > t["consec_pct"]:
                continue
            prev = None
            if self.prev_diag:
                pi = self.prev_diag.get("sentiment", {}).get("indices", {}).get(key)
                if pi:
                    prev = pi.get("chg_pct", 0)
            if prev is not None and prev <= t["consec_pct"]:
                ok = True
                parts.append(f"{label} 今{today:+.1f}% / 昨{prev:+.1f}%（阈值 {t['consec_pct']:+.1f}%）")
            elif prev is None:
                parts.append(f"{label} 今{today:+.1f}%（昨日缺数据）")
        return self._rule("BS-3", "连续暴跌", "SEVERE", ok,
                          "; ".join(parts) if parts else "未触发")

    def _check_bs4(self):
        t = THRESHOLDS["BS-4"]
        ld, av = self._b("limit_down", 0), self._f("avg_vol_ratio", 1.0)
        ok = ld > t["limit_down"] and av > t["avg_vol_ratio"]
        return self._rule("BS-4", "流动性冻结", "CRITICAL", ok,
                          f"跌停 {ld} 只（阈值 {t['limit_down']}），"
                          f"均量比 {av:.1f}x（阈值 {t['avg_vol_ratio']}x）",
                          {"limit_down": ld, "avg_vol_ratio": av})

    def _check_bs5(self):
        t = THRESHOLDS["BS-5"]
        if not self._north_diag.get("available", True):
            return self._rule_skip("BS-5", "北向恐慌", "SEVERE",
                                   f"盘中屏蔽: {self._north_diag.get('reason', '?')}")
        today = self._north_diag.get("net_north") if self._north_diag.get("available") else None
        if today is None:
            return self._rule_skip("BS-5", "北向恐慌", "SEVERE", "缺少今日北向数据")
        if today > -t["outflow_yi"]:
            return self._rule_ok("BS-5", "北向恐慌", "SEVERE",
                                 f"北向净流入 {today:.0f}亿（阈值 -{t['outflow_yi']}亿）")
        prev = self.prev_north[0].get("net_north", 0) if self.prev_north else None
        ok = prev is not None and prev < -t["outflow_yi"]
        return self._rule("BS-5", "北向恐慌", "SEVERE", ok,
                          f"北向 今{today:.0f}亿 / 昨{prev:.0f}亿（阈值 -{t['outflow_yi']}亿）" if prev
                          else f"北向今日{today:.0f}亿（昨日缺数据）",
                          {"today_net": today, "prev_net": prev})

    def _check_bs6(self):
        t = THRESHOLDS["BS-6"]
        if not self.today_industry:
            return self._rule_skip("BS-6", "板块雪崩", "HIGH", "缺少今日行业数据")

        def _flow(r):
            v = r.get("f62") or r.get("主力净流入") or 0
            return float(v) if v else 0

        def _name(r):
            return r.get("f14") or r.get("名称") or "?"

        top5 = sorted(self.today_industry, key=_flow, reverse=True)[:5]
        names = [_name(r) for r in top5]
        flows = [_flow(r) for r in top5]
        if not all(f < 0 for f in flows):
            return self._rule_ok("BS-6", "板块雪崩", "HIGH",
                                 f"Top5 行业未全部转负: {', '.join(names)}")
        flip = 0
        if self.prev_industry:
            pm = {_name(r): _flow(r) for r in self.prev_industry}
            flip = sum(1 for n, f in zip(names, flows) if pm.get(n, 0) > 0 and f < 0)
        ok = flip >= t["flip_count"] if self.prev_industry else True
        return self._rule("BS-6", "板块雪崩", "HIGH", ok,
                          f"Top5 行业: {', '.join(names)}，全部净流出，{flip} 个翻负",
                          {"top5": names, "flip_count": flip})

    def _check_bs7(self):
        if not self._abs_chgs:
            return self._rule_skip("BS-7", "波动率爆炸", "SEVERE", "无数据")
        s = sorted(self._abs_chgs)
        n = len(s)
        m = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
        t = THRESHOLDS["BS-7"]["median_abs_chg"]
        return self._rule("BS-7", "波动率爆炸", "SEVERE", m > t,
                          f"中位 abs(chg) {m:.2f}%（阈值 {t}%），共 {n} 样本",
                          {"median_abs_chg": m, "sample_count": n})

    def _check_bs8(self):
        t = THRESHOLDS["BS-8"]
        v = self._f("margin_pos_ratio", 0.5)
        return self._rule("BS-8", "融资恐慌", "HIGH", v < t["margin_pos_ratio"],
                          f"融资正流比 {v:.1%}（阈值 {t['margin_pos_ratio']:.0%}）",
                          {"margin_pos_ratio": v})

    def _check_bs9(self):
        t = THRESHOLDS["BS-9"]
        s = self._sentiment.get("score", 50)
        if s < t["frozen"]:
            sev, ok, tag = "CRITICAL", True, f"冰冻 <{t['frozen']}"
        elif s < t["pessimistic"]:
            sev, ok, tag = "SEVERE", True, f"悲观 <{t['pessimistic']}"
        else:
            sev, ok, tag = "CRITICAL", False, f"阈值 {t['pessimistic']}"
        return self._rule("BS-9", "情绪冰冻", sev, ok,
                          f"情绪温度计 {s:.0f}/100（{tag}）", {"sentiment_score": s})

    def _check_bs10(self):
        t = THRESHOLDS["BS-10"]
        v = self._b("up_ratio", 0)
        return self._rule("BS-10", "极端过热", "SEVERE" if v > t["extreme"] else "HIGH",
                          v > t["up_ratio"],
                          f"上涨比 {v:.1%}（阈值 {t['up_ratio']:.0%}），短期过热",
                          {"up_ratio": v})

    def _check_bs11(self):
        t = THRESHOLDS["BS-11"]
        v = self._b("limit_up", 0)
        return self._rule("BS-11", "涨停狂热", "HIGH", v > t["limit_up"],
                          f"涨停 {v} 只（阈值 {t['limit_up']}），情绪亢奋",
                          {"limit_up": v})

    def _check_bs12(self):
        t = THRESHOLDS["BS-12"]
        m, p = self._b("median", 0), self._f("pos_flow_ratio", 0.5)
        return self._rule("BS-12", "量价背离", "HIGH",
                          m > t["median_ret"] and p < t["pos_flow"],
                          f"中位涨跌 {m:+.1f}% 但主力正流比仅 {p:.1%}，资金出逃",
                          {"median_ret": m, "pos_flow_ratio": p})

    def _check_bs13(self):
        t = THRESHOLDS["BS-13"]
        hv, m = self._f("high_vol_ratio", 0), self._b("median", 0)
        return self._rule("BS-13", "放量暴跌", "HIGH",
                          hv > t["high_vol_ratio"] and m < t["median_ret"],
                          f"高量比(>3)占比 {hv:.1%}（阈值 {t['high_vol_ratio']:.0%}）且 "
                          f"中位跌幅 {m:+.1f}%（阈值 {t['median_ret']:+.1f}%）",
                          {"high_vol_ratio": hv, "median_ret": m})

    # ------------------------------------------------------------------
    # 构建器
    # ------------------------------------------------------------------

    @staticmethod
    def _rule(rid, name, severity, triggered, detail="", values=None, skipped=False):
        d = {"rule_id": rid, "name": name, "severity": severity,
             "triggered": triggered, "detail": detail, "values": values or {}}
        if skipped:
            d["skipped"] = True
        return d

    @classmethod
    def _rule_skip(cls, rid, name, severity, reason):
        return cls._rule(rid, name, severity, False, f"⏭️ {reason}", skipped=True)

    @classmethod
    def _rule_ok(cls, rid, name, severity, detail):
        return cls._rule(rid, name, severity, False, detail=detail)

    # ------------------------------------------------------------------
    # 主检测
    # ------------------------------------------------------------------

    def check(self) -> dict:
        """执行规则检测（跳过被禁用的规则）。"""
        self._rules = []
        for rid, method_name, _ in _RULE_REGISTRY:
            if not self._enabled(rid):
                self._rules.append(
                    self._rule(rid, "已禁用", "HIGH", False, "⏭️ 已禁用", skipped=True))
                continue
            self._rules.append(getattr(self, method_name)())

        triggered = [r for r in self._rules if r["triggered"]]
        c = sum(1 for r in triggered if r["severity"] == "CRITICAL")
        s = sum(1 for r in triggered if r["severity"] == "SEVERE")
        h = sum(1 for r in triggered if r["severity"] == "HIGH")

        if c >= 1 or s >= 3:
            level, name = 3, "紧急"
        elif s >= 1 or h >= 3:
            level, name = 2, "警告"
        elif h >= 1:
            level, name = 1, "关注"
        else:
            level, name = 0, "正常"

        return {
            "date": self.date_str,
            "prev_date": self.prev_date,
            "level": level, "level_name": name,
            "triggered_count": len(triggered),
            "summary": {"critical": c, "severe": s, "high": h},
            "rules": self._rules,
            "triggered_rules": triggered,
            "disabled_rules": sorted(self._disabled),
            "actions": _build_actions(level),
        }


def _build_actions(level):
    if level == 0:
        return ["正常运行，无需特殊操作"]
    if level == 1:
        return ["新仓仓位打 7 折"]
    if level == 2:
        return ["禁止新买入", "止损收紧至 -3%", "已有持仓逐只审查"]
    return ["🚨 禁止一切买入", "全部持仓标记审查",
            "紧急卖出 UR-2 级别信号", "建议立即减仓至 30% 以下"]


# ---------------------------------------------------------------------------
# 通知 & 输出
# ---------------------------------------------------------------------------

def _notify(result: dict):
    if result["level"] == 0:
        return
    try:
        from notify.message_builder import build_black_swan_alert, dedup_key
        from notify.wecom_sender import send_text, send_markdown
    except ImportError:
        print("  ⚠️ 通知模块不可用")
        return

    msg, at_all = build_black_swan_alert(
        date_str=result["date"], level=result["level"],
        level_name=result["level_name"],
        triggered_rules=result["triggered_rules"],
        suggested_actions=result["actions"],
    )
    if at_all:
        send_text(f"⚠️ 黑天鹅预警 — Level {result['level']} {result['level_name']}",
                  mentioned_list=["@all"],
                  dedup_key=dedup_key("black_swan_text", result["date"]))
    send_markdown(msg, dedup_key=dedup_key("black_swan_detail", result["date"]))
    print(f"  📤 企业微信已推送（Level {result['level']}, @all={at_all}）")


def _print_result(result: dict):
    level = result["level"]
    emoji = {0: "🟢", 1: "🟡", 2: "🟠", 3: "🔴"}.get(level, "⚪")

    print(f"\n{'='*60}")
    print(f"  {emoji} Level {level} — {result['level_name']}")
    print(f"  日期: {result['date']}  前交易日: {result['prev_date'] or 'N/A'}")
    print(f"  触发: CRITICAL={result['summary']['critical']} "
          f"SEVERE={result['summary']['severe']} HIGH={result['summary']['high']}")
    if result.get("disabled_rules"):
        print(f"  禁用: {', '.join(result['disabled_rules'])}")
    print(f"{'='*60}")

    print(f"\n  {'规则':<6s} {'严重度':<10s} {'触发':<6s} 详情")
    print(f"  {'-'*4} {'-'*8} {'-'*4} {'-'*40}")
    for r in result["rules"]:
        flag = "⚠️" if r["triggered"] else "　"
        skipped = r.get("skipped", False)
        status = "跳过" if skipped else ("触发" if r["triggered"] else "—")
        print(f"  {r['rule_id']:<6s} {r['severity']:<10s} {status:<6s} {flag} {r['detail'][:80]}")

    print(f"\n  建议动作:")
    for a in result["actions"]:
        print(f"    → {a}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_disabled(cli_arg: str) -> set:
    """从 CLI --disable 和环境变量 BS_DISABLE_RULES 解析禁用规则。"""
    disabled = set()
    for source in [os.environ.get("BS_DISABLE_RULES", ""), cli_arg]:
        for rid in source.replace(" ", "").split(","):
            rid = rid.strip()
            if rid and rid.startswith("BS-"):
                disabled.add(rid)
    return disabled


def main():
    parser = argparse.ArgumentParser(description="黑天鹅风险监控")
    parser.add_argument("--date", default=None, help="日期 YYYYMMDD")
    parser.add_argument("--no-notify", action="store_true", help="静默模式")
    parser.add_argument("--no-collect", action="store_true", help="禁止自动采集")
    parser.add_argument("--disable", default="", help="禁用规则，逗号分隔（如 BS-11,BS-13）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    date_str = args.date or _today_str()
    disabled = _parse_disabled(args.disable)  # 合并 CLI + 环境变量

    if not args.no_collect and not _ensure_data(date_str):
        sys.exit(1)
    if args.no_collect and not _load_fund_flow(date_str):
        print(f"❌ {date_str} 无数据: python -m data_collector.main --date={date_str}")
        sys.exit(1)

    result = BlackSwanDetector(date_str, disabled_rules=disabled).check()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        _print_result(result)

    if not args.no_notify and result["level"] >= 1:
        _notify(result)


if __name__ == "__main__":
    main()
