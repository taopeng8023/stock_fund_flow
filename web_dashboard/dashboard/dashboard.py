"""
A股量化系统 Web Dashboard — Reflex 前端
侧边栏导航: 概览 | 交易 | 分析 | 系统
"""
import os
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path

from typing import Any

import reflex as rx
from reflex.vars import Var
import plotly.io as pio

# Ensure project root on sys.path
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from data_collector.fetchers.base import DATA_ROOT, today_str
from .data_loaders import (
    load_backtest_summary, load_backtest_daily, load_scores, load_performance_summary
)
from . import charts


from reflex.components.plotly.plotly import Plotly as _Plotly


class PlotlyFromDict(_Plotly):
    """rx.plotly wrapper that accepts pre-serialized dict data."""

    data: Var[dict]


def _fig_to_dict(fig):
    """Convert Plotly Figure to JSON-serializable dict for Reflex state."""
    return json.loads(pio.to_json(fig))


# ============================================================
# State
# ============================================================

class DashboardState(rx.State):
    selected_date: str = today_str()

    # ── Diagnosis ──
    diag_loaded: bool = False
    regime: str = ""
    regime_conf: str = ""
    stock_count: int = 0
    up_ratio: str = ""
    down_ratio: str = ""
    limit_up_count: int = 0
    limit_down_count: int = 0
    median_chg: str = ""
    total_main_flow: str = ""
    pos_flow_ratio: str = ""
    super_dom_ratio: str = ""
    margin_pos_ratio: str = ""
    risk_level: str = ""
    risk_alerts: list = []
    position_raw: str = ""
    position_adj: str = ""
    position_advice: str = ""

    # ── Collector ──
    collector_files: list[dict] = []
    collector_loading: bool = False

    # ── Picks ──
    picks_loaded: bool = False
    picks: list[dict] = []
    limit_up: list[dict] = []

    # ── Cron ──
    cron_content: str = ""
    cron_output: str = ""
    cron_running: bool = False

    # ── Portfolio ──
    positions: list[dict] = []
    positions_loaded: bool = False
    total_pnl: str = ""

    # ── Risk ──
    bs_level: int = -1
    bs_level_name: str = ""
    bs_triggered: list[dict] = []
    bs_actions: list[str] = []
    bs_loaded: bool = False
    bs_critical: int = 0
    bs_severe: int = 0
    bs_high: int = 0
    # Chart figures (pre-computed dicts)
    breadth_donut_fig: dict = {}
    risk_gauge_fig: dict = {}

    # ── Trades ──
    trades: list[dict] = []
    trades_loaded: bool = False

    # ── Backtest ──
    bt_loaded: bool = False
    bt_summary_rows: list[dict] = []
    bt_selected_daily_date: str = ""
    bt_daily_rows: list[dict] = []
    bt_equity_fig: dict = {}
    bt_daily_bars_fig: dict = {}
    bt_factor_edge_fig: dict = {}
    bt_decile_fig: dict = {}
    bt_score_scatter_fig: dict = {}

    # ── Factor ──
    factor_loaded: bool = False
    factor_scores_rows: list[dict] = []
    factor_contrib_fig: dict = {}
    factor_hist_fig: dict = {}
    factor_box_fig: dict = {}

    # ── Performance ──
    perf_summary: dict = {}

    # ── Navigation ──
    selected_page: str = "diagnosis"
    sidebar_open: bool = True

    def set_page(self, page: str):
        """Switch the active page. Reset load flags so user can re-load."""
        self.selected_page = page
        if page == "diagnosis":
            self.diag_loaded = False
        elif page == "risk":
            self.bs_loaded = False
        elif page == "portfolio":
            self.positions_loaded = False
        elif page == "trades":
            self.trades_loaded = False
        elif page == "picks":
            self.picks_loaded = False
        elif page == "backtest":
            self.bt_loaded = False
        elif page == "factor":
            self.factor_loaded = False
        elif page == "collector":
            self.collector_files = []
        # cron page has manual load button

    def toggle_sidebar(self):
        self.sidebar_open = not self.sidebar_open

    def set_date(self, date_str: str):
        self.selected_date = date_str
        self.diag_loaded = False
        self.picks_loaded = False
        self.collector_files = []

    def load_diagnosis(self):
        try:
            from market_diagnosis import load_diagnosis as load_diag, get_diagnosis
            d = load_diag(self.selected_date)
            if d is None:
                d = get_diagnosis(self.selected_date)
            if d:
                self.regime = d.get("regime", {}).get("label", "-")
                self.regime_conf = f"{d.get('regime', {}).get('confidence', 0):.0%}"
                self.stock_count = d.get("stock_count", 0)
                b = d.get("breadth", {})
                self.up_ratio = f"{b.get('up_ratio', 0):.1%}"
                self.down_ratio = f"{b.get('down_ratio', 0):.1%}"
                self.limit_up_count = b.get("limit_up", 0)
                self.limit_down_count = b.get("limit_down", 0)
                self.median_chg = f"{b.get('median', 0):+.1f}%"
                f2 = d.get("fund_flow", {})
                self.total_main_flow = _fmt_yi(f2.get("total_main_flow", 0))
                self.pos_flow_ratio = f"{f2.get('pos_flow_ratio', 0):.1%}"
                self.super_dom_ratio = f"{f2.get('super_dominant_ratio', 0):.1%}"
                self.margin_pos_ratio = f"{f2.get('margin_pos_ratio', 0):.1%}"
                r = d.get("risks", {})
                self.risk_level = r.get("level", "-")
                self.risk_alerts = r.get("alerts", [])[:5]
                p = d.get("position", {})
                self.position_raw = f"{p.get('base', 0)}%"
                self.position_adj = f"{p.get('adjusted', 0)}%"
                self.position_advice = p.get("advice", "")
                # Build breadth donut chart
                self.breadth_donut_fig = _fig_to_dict(
                    charts.build_breadth_donut(self.up_ratio, self.down_ratio)
                )
                self.diag_loaded = True
        except Exception as e:
            self.regime = f"错误: {e}"
            self.diag_loaded = True

    def load_collector_files(self):
        self.collector_loading = True
        date_dir = os.path.join(DATA_ROOT, self.selected_date)
        files = []
        if os.path.exists(date_dir):
            for f in sorted(os.listdir(date_dir)):
                path = os.path.join(date_dir, f)
                size = os.path.getsize(path)
                mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%H:%M:%S")
                files.append(dict(name=f, size=f"{size:,}", mtime=mtime,
                                  type=f.split(".")[-1].upper()))
        self.collector_files = files
        self.collector_loading = False

    def load_picks(self):
        try:
            path = os.path.join(DATA_ROOT, self.selected_date, "sector_enhanced_picks.json")
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                self.picks = data.get("candidates", [])
                self.limit_up = data.get("limit_up_observe", [])
            else:
                self.picks = []
                self.limit_up = []
        except Exception:
            self.picks = []
            self.limit_up = []
        self.picks_loaded = True

    def load_cron_script(self):
        path = os.path.join(PROJECT_DIR, "daily_run.sh")
        if os.path.exists(path):
            with open(path) as f:
                self.cron_content = f.read()
        else:
            self.cron_content = "# daily_run.sh 不存在"

    async def run_cron(self):
        self.cron_running = True
        self.cron_output = ""
        yield
        path = os.path.join(PROJECT_DIR, "daily_run.sh")
        if not os.path.exists(path):
            self.cron_output = "daily_run.sh 不存在"
            self.cron_running = False
            return
        try:
            cp = subprocess.run(["bash", path], capture_output=True, text=True, timeout=300,
                                cwd=str(PROJECT_DIR))
            out = cp.stdout[-5000:] or "(无输出)"
            if cp.stderr:
                out += f"\n\nSTDERR:\n{cp.stderr[-2000:]}"
            if cp.returncode != 0:
                out += f"\n\n退出码: {cp.returncode}"
            self.cron_output = out
        except subprocess.TimeoutExpired:
            self.cron_output = "执行超时 (>300s)"
        except Exception as e:
            self.cron_output = f"执行失败: {e}"
        self.cron_running = False

    async def run_collector(self):
        self.cron_output = "正在采集数据..."
        self.cron_running = True
        yield
        try:
            cp = subprocess.run(
                [sys.executable, "-m", "data_collector.main", f"--date={self.selected_date}"],
                capture_output=True, text=True, timeout=120, cwd=str(PROJECT_DIR))
            self.cron_output = cp.stdout[-5000:] or "(无输出)"
            if cp.stderr:
                self.cron_output += f"\n\nSTDERR:\n{cp.stderr[-1000:]}"
        except subprocess.TimeoutExpired:
            self.cron_output = "采集超时 (>120s)"
        except Exception as e:
            self.cron_output = f"采集失败: {e}"
        self.cron_running = False
        self.load_collector_files()

    async def run_screener(self):
        self.cron_output = "正在选股..."
        self.cron_running = True
        yield
        try:
            cp = subprocess.run(
                [sys.executable, "-m", "sector_screener.main",
                 f"--date={self.selected_date}", "--top=10"],
                capture_output=True, text=True, timeout=120, cwd=str(PROJECT_DIR))
            self.cron_output = cp.stdout[-5000:] or "(无输出)"
        except subprocess.TimeoutExpired:
            self.cron_output = "选股超时 (>120s)"
        except Exception as e:
            self.cron_output = f"选股失败: {e}"
        self.cron_running = False
        self.load_picks()

    def load_portfolio(self):
        try:
            from portfolio.manager import _load, _fetch_realtime_prices
            data = _load()
            active = [p for p in data.get("positions", []) if p.get("status") == "active"]
            codes = [p["code"] for p in active]
            prices = _fetch_realtime_prices(codes)
            positions = []
            for p in active:
                entry = float(p.get("entry_price", 1))
                cp = prices.get(p["code"]) or p.get("current_price") or entry
                pnl = round((cp - entry) / entry * 100, 2) if cp else 0
                positions.append({
                    "code": p["code"],
                    "name": p.get("name", ""),
                    "entry_price": f"{entry:.2f}",
                    "current_price": f"{cp:.2f}" if cp else "—",
                    "pnl_pct": f"{pnl:+.1f}%",
                    "pnl_val": pnl,
                    "hold_days": _hold_days(p.get("entry_date", "")),
                    "sl_tp": f"{p.get('stop_loss_pct', -5):+.0f}%/{p.get('take_profit_pct', 15):+.0f}%",
                })
            self.positions = positions
            total = sum(p["pnl_val"] for p in positions)
            self.total_pnl = f"{total:+.1f}%"
            self.positions_loaded = True
        except Exception as e:
            self.positions = []
            self.total_pnl = f"加载失败: {e}"
            self.positions_loaded = True

    def load_trades(self):
        try:
            from portfolio.manager import _load
            data = _load()
            trades = []
            for t in data.get("trades", [])[-50:]:
                pnl = t.get("pnl_pct")
                trades.append({
                    "date": t.get("date", ""),
                    "code": t.get("code", ""),
                    "name": t.get("name", ""),
                    "action": t.get("action", ""),
                    "price": f"{t.get('price', 0):.2f}",
                    "pnl_pct": f"{pnl:+.1f}%" if pnl is not None else "—",
                    "reason": (t.get("reason", "") or "")[:30],
                })
            self.trades = trades
            self.trades_loaded = True
        except Exception:
            self.trades = []
            self.trades_loaded = True

    def load_risk(self):
        try:
            from portfolio.black_swan import BlackSwanDetector
            bs = BlackSwanDetector(self.selected_date).check()
            self.bs_level = bs["level"]
            self.bs_level_name = bs["level_name"]
            self.bs_triggered = bs.get("triggered_rules", [])
            summary = bs.get("summary", {})
            self.bs_critical = summary.get("critical", 0)
            self.bs_severe = summary.get("severe", 0)
            self.bs_high = summary.get("high", 0)
            self.bs_actions = bs.get("actions", [])
            # Build risk gauge chart
            self.risk_gauge_fig = _fig_to_dict(
                charts.build_risk_gauge(self.bs_level, summary)
            )
            self.bs_loaded = True
        except Exception as e:
            self.bs_level_name = f"错误: {e}"
            self.bs_loaded = True

    # ── Backtest ──
    def load_backtest(self):
        rows = load_backtest_summary()
        self.bt_summary_rows = rows
        if rows:
            self.bt_selected_daily_date = rows[-1].get("日期", "")
            # Pre-compute all backtest chart figures
            self.bt_equity_fig = _fig_to_dict(charts.build_equity_curve(rows))
            self.bt_daily_bars_fig = _fig_to_dict(charts.build_daily_return_bars(rows))
            self.bt_factor_edge_fig = _fig_to_dict(charts.build_factor_edge(rows))
            self.bt_decile_fig = _fig_to_dict(charts.build_decile_chart(rows))
        self.bt_loaded = True

    def load_backtest_daily(self):
        if self.bt_selected_daily_date:
            daily = load_backtest_daily(self.bt_selected_daily_date)
            self.bt_daily_rows = daily
            self.bt_score_scatter_fig = _fig_to_dict(charts.build_score_scatter(daily))

    def load_factor_analysis(self):
        rows = load_scores(self.selected_date)
        self.factor_scores_rows = rows
        if rows:
            self.factor_contrib_fig = _fig_to_dict(charts.build_factor_contribution(rows))
            self.factor_hist_fig = _fig_to_dict(charts.build_score_histogram(rows))
            self.factor_box_fig = _fig_to_dict(charts.build_industry_box(rows))
        self.factor_loaded = True

    def load_performance_summary(self):
        self.perf_summary = load_performance_summary()


# ============================================================
# Helpers
# ============================================================

def _hold_days(entry_date: str) -> str:
    try:
        days = (datetime.now() - datetime.strptime(entry_date, "%Y%m%d")).days
        return f"{days}天"
    except Exception:
        return "—"

def _fmt_yi(v):
    if v is None: return "0"
    v = float(v)
    return f"{v/1e8:+.2f}亿" if abs(v) >= 1e8 else f"{v/1e4:+.0f}万"


# ============================================================
# UI Components
# ============================================================

def stat_card(title: str, value: str, color: str = "blue"):
    return rx.card(
        rx.vstack(
            rx.text(title, size="1",
                    color=rx.color_mode_cond(rx.color("gray", 10), rx.color("gray", 11))),
            rx.text(value, size="5", weight="bold", color=color),
            align="center", spacing="0",
        ),
        padding="0.8em", min_width="120px",
    )


# ============================================================
# Layout Components
# ============================================================

def _nav_item(page_key: str, label: str, icon_tag: str) -> rx.Component:
    """Sidebar navigation item with active-state highlighting."""
    return rx.cond(
        DashboardState.selected_page == page_key,
        rx.button(
            rx.hstack(
                rx.icon(tag=icon_tag, size=16),
                rx.text(label, size="2", weight="bold"),
                spacing="2", align="center",
            ),
            variant="solid", color_scheme="blue",
            on_click=DashboardState.set_page(page_key),
            width="100%", justify="start",
            border_radius="8px", cursor="pointer",
        ),
        rx.button(
            rx.hstack(
                rx.icon(tag=icon_tag, size=16),
                rx.text(label, size="2"),
                spacing="2", align="center",
            ),
            variant="ghost", color_scheme="gray",
            on_click=DashboardState.set_page(page_key),
            width="100%", justify="start",
            border_radius="8px", cursor="pointer",
        ),
    )


def _section_label(title: str):
    return rx.text(
        title, size="1", weight="bold",
        color=rx.color_mode_cond(rx.color("gray", 10), rx.color("gray", 11)),
        letter_spacing="0.05em",
        padding_x="0.5em", padding_y="0.75em 0.5em 0.25em",
    )


def _header():
    return rx.hstack(
        rx.button(
            rx.icon(tag="menu", size=18),
            variant="ghost", color_scheme="gray",
            on_click=DashboardState.toggle_sidebar,
        ),
        rx.heading("📊 A股量化系统", size="5", weight="bold"),
        rx.spacer(),
        rx.text("日期:", size="2"),
        rx.input(value=DashboardState.selected_date,
                 on_change=DashboardState.set_date, width="120px", size="2"),
        rx.button("今天", on_click=DashboardState.set_date(today_str()), size="2"),
        rx.color_mode.button(),
        rx.button("刷新全部", on_click=[
            DashboardState.load_diagnosis,
            DashboardState.load_collector_files,
            DashboardState.load_picks,
        ], size="2"),
        width="100%", height="56px", padding="0 1.5em", align="center",
        border_bottom=rx.color_mode_cond("1px solid #e5e7eb", "1px solid #2d2d3f"),
    )


def _sidebar():
    return rx.cond(
        DashboardState.sidebar_open,
        rx.vstack(
            _section_label("概览"),
            _nav_item("diagnosis", "盘面诊断", "activity"),
            _nav_item("risk", "风险监控", "shield-alert"),
            rx.separator(size="4"),
            _section_label("交易"),
            _nav_item("portfolio", "持仓管理", "briefcase"),
            _nav_item("trades", "交易历史", "arrow-left-right"),
            _nav_item("picks", "选股结果", "flame"),
            rx.separator(size="4"),
            _section_label("分析"),
            _nav_item("backtest", "回测分析", "trending-up"),
            _nav_item("factor", "因子分析", "microscope"),
            rx.separator(size="4"),
            _section_label("系统"),
            _nav_item("collector", "数据采集", "folder-open"),
            _nav_item("cron", "定时任务", "clock"),
            rx.spacer(),
            rx.separator(size="4"),
            rx.text("v0.5.10", size="1",
                    color=rx.color_mode_cond(rx.color("gray", 9), rx.color("gray", 11))),
            width="260px",
            height="100%",
            padding="1em",
            border_right=rx.color_mode_cond("1px solid #e5e7eb", "1px solid #2d2d3f"),
            spacing="1",
            overflow_y="auto",
        ),
        rx.fragment(),
    )


def _main_content():
    return rx.scroll_area(
        rx.match(
            DashboardState.selected_page,
            ("diagnosis",  diagnosis_page()),
            ("risk",       risk_page()),
            ("portfolio",  portfolio_page()),
            ("trades",     trades_page()),
            ("picks",      picks_page()),
            ("backtest",   backtest_page()),
            ("factor",     factor_page()),
            ("collector",  collector_page()),
            ("cron",       cron_page()),
            diagnosis_page(),
        ),
        width="100%", height="calc(100vh - 56px)",
        scrollbars="vertical",
    )


def _layout():
    return rx.fragment(
        _header(),
        rx.flex(
            _sidebar(),
            _main_content(),
            height="calc(100vh - 56px)",
            overflow="hidden",
        ),
    )


# ═══════════════════ Page 1: 盘面诊断 ═══════════════════

def diagnosis_page():
    return rx.vstack(
        rx.button("🔄 加载盘面诊断", on_click=DashboardState.load_diagnosis, size="2"),
        rx.cond(
            ~DashboardState.diag_loaded,
            rx.text("点击按钮加载", color="gray"),
            rx.vstack(
                rx.heading(f"📈 {DashboardState.regime}  (置信度 {DashboardState.regime_conf})", size="5"),
                rx.hstack(
                    stat_card("全市场股票", DashboardState.stock_count.to_string(), "purple"),
                    stat_card("风险等级", DashboardState.risk_level.upper(), "orange"),
                    stat_card("建议仓位", DashboardState.position_adj, "blue"),
                    stat_card("仓位建议", DashboardState.position_advice, "gray"),
                    wrap="wrap",
                ),
                rx.heading("市场宽度", size="4"),
                rx.hstack(
                    stat_card("上涨占比", DashboardState.up_ratio, "green"),
                    stat_card("下跌占比", DashboardState.down_ratio, "red"),
                    stat_card("涨停", DashboardState.limit_up_count.to_string(), "red"),
                    stat_card("跌停", DashboardState.limit_down_count.to_string(), "green"),
                    stat_card("中位涨跌", DashboardState.median_chg, "blue"),
                    wrap="wrap",
                ),
                rx.heading("资金全景", size="4"),
                rx.hstack(
                    stat_card("主力净流入", DashboardState.total_main_flow, "green"),
                    stat_card("主力正流占比", DashboardState.pos_flow_ratio, "blue"),
                    stat_card("超大单主导", DashboardState.super_dom_ratio, "purple"),
                    stat_card("融资正流占比", DashboardState.margin_pos_ratio, "orange"),
                    wrap="wrap",
                ),
                rx.cond(
                    DashboardState.risk_alerts.length() > 0,
                    rx.vstack(
                        rx.heading("⚠️ 风险提示", size="4", color="red"),
                        rx.foreach(DashboardState.risk_alerts,
                                   lambda a: rx.text(f"• {a}", color="red", size="2")),
                    ),
                ),
                # 涨跌分布环形图
                rx.card(
                    PlotlyFromDict(data=DashboardState.breadth_donut_fig, height="350px"),
                    width="100%",
                ),
                spacing="3", width="100%",
            ),
        ),
        width="100%", padding="1em",
    )


# ═══════════════════ Page 2: 数据采集 ═══════════════════

def collector_page():
    return rx.vstack(
        rx.hstack(
            rx.button("🔄 刷新", on_click=DashboardState.load_collector_files, size="2"),
            rx.button("🚀 执行采集", on_click=DashboardState.run_collector, size="2", color_scheme="green"),
        ),
        rx.cond(
            DashboardState.collector_files.length() > 0,
            rx.table.root(
                rx.table.header(rx.table.row(
                    rx.table.column_header_cell("文件名"),
                    rx.table.column_header_cell("类型"),
                    rx.table.column_header_cell("大小"),
                    rx.table.column_header_cell("时间"),
                )),
                rx.table.body(rx.foreach(DashboardState.collector_files, lambda f: rx.table.row(
                    rx.table.cell(f["name"]),
                    rx.table.cell(rx.badge(f["type"])),
                    rx.table.cell(f["size"]),
                    rx.table.cell(f["mtime"]),
                ))),
                width="100%",
            ),
            rx.text("点击刷新加载文件列表", color="gray"),
        ),
        width="100%", padding="1em",
    )


# ═══════════════════ Page 3: 选股结果 ═══════════════════

def picks_page():
    return rx.vstack(
        rx.hstack(
            rx.button("🔄 加载", on_click=DashboardState.load_picks, size="2"),
            rx.button("🚀 执行选股", on_click=DashboardState.run_screener, size="2", color_scheme="green"),
        ),
        rx.cond(
            ~DashboardState.picks_loaded,
            rx.text("点击按钮加载选股结果", color="gray"),
            rx.cond(
                DashboardState.picks.length() > 0,
                rx.vstack(
                    rx.heading(f"🔥 精选候选 ({DashboardState.picks.length()} 只)", size="5"),
                    rx.table.root(
                        rx.table.header(rx.table.row(
                            rx.table.column_header_cell("排名"), rx.table.column_header_cell("代码"),
                            rx.table.column_header_cell("名称"), rx.table.column_header_cell("得分"),
                            rx.table.column_header_cell("涨跌%"), rx.table.column_header_cell("主力流入"),
                            rx.table.column_header_cell("板块"),
                        )),
                        rx.table.body(rx.foreach(DashboardState.picks, _pick_row)),
                        width="100%",
                    ),
                    rx.cond(
                        DashboardState.limit_up.length() > 0,
                        rx.vstack(
                            rx.heading(f"👀 涨停观察池 ({DashboardState.limit_up.length()} 只)", size="5", color="red"),
                            rx.table.root(
                                rx.table.header(rx.table.row(
                                    rx.table.column_header_cell("代码"), rx.table.column_header_cell("名称"),
                                    rx.table.column_header_cell("涨跌%"), rx.table.column_header_cell("主力流入"),
                                    rx.table.column_header_cell("板块"),
                                )),
                                rx.table.body(rx.foreach(DashboardState.limit_up, _limit_row)),
                                width="100%",
                            ),
                        ),
                    ),
                    spacing="3", width="100%",
                ),
                rx.text("无选股数据", color="gray"),
            ),
        ),
        width="100%", padding="1em",
    )

def _pick_row(p: dict):
    return rx.table.row(
        rx.table.cell(p["rank"]), rx.table.cell(p["code"]),
        rx.table.cell(p["name"]), rx.table.cell(p["score"]),
        rx.table.cell(p["chg_pct"]), rx.table.cell(p["main_flow"]),
        rx.table.cell(p["sector_name"]),
    )

def _limit_row(p: dict):
    return rx.table.row(
        rx.table.cell(p["code"]), rx.table.cell(p["name"]),
        rx.table.cell(p["chg_pct"]), rx.table.cell(p["main_flow"]),
        rx.table.cell(p["sector_name"]),
    )


# ═══════════════════ Page 4: 定时任务 ═══════════════════

def cron_page():
    return rx.vstack(
        rx.heading("📋 daily_run.sh", size="5"),
        rx.button("📂 加载脚本", on_click=DashboardState.load_cron_script, size="2"),
        rx.cond(
            DashboardState.cron_content != "",
            rx.code_block(DashboardState.cron_content, language="bash",
                          width="100%", border_radius="8px"),
        ),
        rx.button("▶ 执行 daily_run.sh", on_click=DashboardState.run_cron,
                  size="3", color_scheme="red", loading=DashboardState.cron_running),
        rx.cond(
            DashboardState.cron_output != "",
            rx.vstack(
                rx.heading("执行输出", size="5"),
                rx.box(
                    rx.text(DashboardState.cron_output, white_space="pre-wrap", size="1"),
                    padding="1em", background="#1e1e1e", color="#d4d4d4",
                    border_radius="8px", width="100%", max_height="400px",
                    overflow="auto",
                ),
                width="100%",
            ),
        ),
        spacing="4", width="100%", padding="1em",
    )


# ═══════════════════ Page 5: 持仓管理 ═══════════════════

def portfolio_page():
    return rx.vstack(
        rx.hstack(
            rx.button("🔄 刷新持仓", on_click=DashboardState.load_portfolio, size="2"),
            rx.button("🚀 检查卖出信号", on_click=DashboardState.load_risk, size="2", color_scheme="orange"),
        ),
        rx.cond(
            ~DashboardState.positions_loaded,
            rx.text("点击刷新加载持仓", color="gray"),
            rx.cond(
                DashboardState.positions.length() > 0,
                rx.vstack(
                    rx.hstack(
                        rx.heading(f"💼 当前持仓 ({DashboardState.positions.length()} 只)", size="5"),
                        rx.spacer(),
                        rx.text(DashboardState.total_pnl, size="4", weight="bold"),
                    ),
                    rx.table.root(
                        rx.table.header(rx.table.row(
                            rx.table.column_header_cell("代码"), rx.table.column_header_cell("名称"),
                            rx.table.column_header_cell("入场价"), rx.table.column_header_cell("现价"),
                            rx.table.column_header_cell("盈亏%"), rx.table.column_header_cell("持有天"),
                            rx.table.column_header_cell("止损/止盈"),
                        )),
                        rx.table.body(rx.foreach(DashboardState.positions, _pos_row)),
                        width="100%",
                    ),
                    spacing="3", width="100%",
                ),
                rx.text("📭 暂无持仓，使用 portfolio.manager 添加", color="gray"),
            ),
        ),
        width="100%", padding="1em",
    )

def _pos_row(p: dict):
    return rx.table.row(
        rx.table.cell(p["code"]), rx.table.cell(p["name"]),
        rx.table.cell(p["entry_price"]), rx.table.cell(p["current_price"]),
        rx.table.cell(p["pnl_pct"]), rx.table.cell(p["hold_days"]),
        rx.table.cell(p["sl_tp"]),
    )


# ═══════════════════ Page 6: 风险监控 ═══════════════════

def risk_page():
    return rx.vstack(
        rx.button("🔄 加载风险数据", on_click=DashboardState.load_risk, size="2"),
        rx.cond(
            ~DashboardState.bs_loaded,
            rx.text("点击加载黑天鹅风险数据", color="gray"),
            rx.vstack(
                rx.hstack(
                    rx.heading(f"Level {DashboardState.bs_level} — {DashboardState.bs_level_name}",
                               size="6"),
                    rx.spacer(),
                    rx.card(
                        rx.vstack(
                            rx.text("风险统计", size="3", weight="bold"),
                            rx.text(f"CRITICAL: {DashboardState.bs_critical}", color="red"),
                            rx.text(f"SEVERE: {DashboardState.bs_severe}", color="orange"),
                            rx.text(f"HIGH: {DashboardState.bs_high}", color="yellow"),
                            spacing="1",
                        ),
                        padding="1em",
                    ),
                ),
                # 风险等级仪表盘
                rx.card(
                    PlotlyFromDict(data=DashboardState.risk_gauge_fig, height="350px"),
                    width="100%",
                ),
                rx.cond(
                    DashboardState.bs_triggered.length() > 0,
                    rx.vstack(
                        rx.heading("⚠️ 触发规则", size="4", color="red"),
                        rx.foreach(DashboardState.bs_triggered, _trigger_row),
                        width="100%",
                    ),
                    rx.text("✅ 无规则触发，市场正常", color="green", size="3"),
                ),
                rx.cond(
                    DashboardState.bs_actions.length() > 0,
                    rx.vstack(
                        rx.heading("📋 建议动作", size="4"),
                        rx.foreach(DashboardState.bs_actions,
                                   lambda a: rx.text(f"→ {a}", size="2")),
                        width="100%",
                    ),
                ),
                spacing="3", width="100%",
            ),
        ),
        width="100%", padding="1em",
    )

def _trigger_row(r: dict):
    return rx.card(
        rx.hstack(
            rx.badge(r["rule_id"], color_scheme="red"),
            rx.text(r["name"], weight="bold"),
            rx.text(r["detail"], size="2", color="gray"),
        ),
        padding="0.5em", width="100%",
    )


# ═══════════════════ Page 7: 交易历史 ═══════════════════

def trades_page():
    return rx.vstack(
        rx.button("🔄 刷新", on_click=DashboardState.load_trades, size="2"),
        rx.cond(
            ~DashboardState.trades_loaded,
            rx.text("点击刷新加载交易记录", color="gray"),
            rx.cond(
                DashboardState.trades.length() > 0,
                rx.table.root(
                    rx.table.header(rx.table.row(
                        rx.table.column_header_cell("日期"), rx.table.column_header_cell("代码"),
                        rx.table.column_header_cell("名称"), rx.table.column_header_cell("操作"),
                        rx.table.column_header_cell("价格"), rx.table.column_header_cell("盈亏%"),
                        rx.table.column_header_cell("原因"),
                    )),
                    rx.table.body(rx.foreach(DashboardState.trades, _trade_row)),
                    width="100%",
                ),
                rx.text("暂无交易记录", color="gray"),
            ),
        ),
        width="100%", padding="1em",
    )

def _trade_row(t: dict):
    return rx.table.row(
        rx.table.cell(t["date"]), rx.table.cell(t["code"]),
        rx.table.cell(t["name"]), rx.table.cell(t["action"]),
        rx.table.cell(t["price"]), rx.table.cell(t["pnl_pct"]),
        rx.table.cell(t["reason"]),
    )


# ═══════════════════ Page 8: 回测分析 ═══════════════════

def backtest_page():
    return rx.vstack(
        rx.hstack(
            rx.button("🔄 加载回测数据", on_click=DashboardState.load_backtest, size="2"),
            rx.button("📊 加载明细", on_click=DashboardState.load_backtest_daily, size="2",
                      color_scheme="green"),
        ),
        rx.cond(
            ~DashboardState.bt_loaded,
            rx.text("点击「加载回测数据」查看回测分析图表", color="gray"),
            rx.cond(
                DashboardState.bt_summary_rows.length() > 0,
                rx.vstack(
                    # Row 1: 累计收益曲线 + 每日收益分布
                    rx.hstack(
                        rx.card(
                            PlotlyFromDict(data=DashboardState.bt_equity_fig,
                                      height="400px", width="100%"),
                            width="50%",
                        ),
                        rx.card(
                            PlotlyFromDict(data=DashboardState.bt_daily_bars_fig,
                                      height="400px", width="100%"),
                            width="50%",
                        ),
                        width="100%", spacing="3",
                    ),
                    # Row 2: 因子区分度 + 分位数单调性
                    rx.hstack(
                        rx.card(
                            PlotlyFromDict(data=DashboardState.bt_factor_edge_fig,
                                      height="400px", width="100%"),
                            width="50%",
                        ),
                        rx.card(
                            PlotlyFromDict(data=DashboardState.bt_decile_fig,
                                      height="400px", width="100%"),
                            width="50%",
                        ),
                        width="100%", spacing="3",
                    ),
                    # Row 3: 得分 vs 收益散点
                    rx.card(
                        rx.vstack(
                            rx.text(f"选股日期: {DashboardState.bt_selected_daily_date}",
                                    size="2", color="gray"),
                            PlotlyFromDict(data=DashboardState.bt_score_scatter_fig,
                                      height="400px", width="100%"),
                            width="100%",
                        ),
                        width="100%",
                    ),
                    spacing="3", width="100%",
                ),
                rx.text("暂无回测数据，请先运行 python -m daily_pipeline.main --mode=backtest",
                        color="gray"),
            ),
        ),
        width="100%", padding="1em",
    )


# ═══════════════════ Page 9: 因子分析 ═══════════════════

def factor_page():
    return rx.vstack(
        rx.hstack(
            rx.button("🔄 加载因子数据", on_click=DashboardState.load_factor_analysis, size="2"),
            rx.text(f"日期: {DashboardState.selected_date}", size="2", color="gray"),
        ),
        rx.cond(
            ~DashboardState.factor_loaded,
            rx.text("点击「加载因子数据」查看因子分析图表", color="gray"),
            rx.cond(
                DashboardState.factor_scores_rows.length() > 0,
                rx.vstack(
                    rx.card(
                        PlotlyFromDict(data=DashboardState.factor_contrib_fig,
                                  height="500px", width="100%"),
                        width="100%",
                    ),
                    rx.hstack(
                        rx.card(
                            PlotlyFromDict(data=DashboardState.factor_hist_fig,
                                      height="400px", width="100%"),
                            width="50%",
                        ),
                        rx.card(
                            PlotlyFromDict(data=DashboardState.factor_box_fig,
                                      height="500px", width="100%"),
                            width="50%",
                        ),
                        width="100%", spacing="3",
                    ),
                    spacing="3", width="100%",
                ),
                rx.text("暂无当日评分数据，请先运行 python -m daily_pipeline.score 生成评分",
                        color="gray"),
            ),
        ),
        width="100%", padding="1em",
    )


# ═══════════════════ Main App ═══════════════════

def index():
    return _layout()

app = rx.App()
app.add_page(index, route="/", title="A股量化系统 Dashboard")
