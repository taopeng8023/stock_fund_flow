"""
A股量化系统 Web Dashboard — Reflex 前端
Tabs: 盘面诊断 | 数据采集 | 选股结果 | 定时任务
"""
import os
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path

import reflex as rx

# Ensure project root on sys.path
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from data_collector.fetchers.base import DATA_ROOT, BJS_TZ, load_json, today_str

# ============================================================
# State — 扁平属性，避免 Reflex Var 嵌套调用
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

    def set_date(self, date_str: str):
        self.selected_date = date_str
        self.diag_loaded = False
        self.picks_loaded = False
        self.collector_files = []

    def load_diagnosis(self):
        try:
            # 优先读持久化结果（秒开），否则实时计算（~10s）
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
                self.position_raw = f"{p.get('raw', 0):.0%}"
                self.position_adj = f"{p.get('adjusted', 0):.0%}"
                self.position_advice = p.get("advice", "")
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


# ============================================================
# Helpers
# ============================================================

def _fmt_yi(v):
    if v is None: return "0"
    v = float(v)
    return f"{v/1e8:+.2f}亿" if abs(v) >= 1e8 else f"{v/1e4:+.0f}万"

def _fmt_yi_s(v):
    if v is None: return "0"
    v = float(v)
    return f"{v/1e8:+.1f}亿" if abs(v) >= 1e8 else f"{v/1e4:+.0f}万"


# ============================================================
# UI Components
# ============================================================

def stat_card(title: str, value: str, color: str = "blue"):
    return rx.card(
        rx.vstack(
            rx.text(title, size="1", color="gray"),
            rx.text(value, size="5", weight="bold", color=color),
            align="center",
            spacing="0",
        ),
        padding="0.8em",
        min_width="120px",
    )

def navbar():
    return rx.hstack(
        rx.heading("📊 A股量化系统", size="7", weight="bold"),
        rx.spacer(),
        rx.hstack(
            rx.text("日期:"),
            rx.input(value=DashboardState.selected_date,
                     on_change=DashboardState.set_date, width="120px"),
            rx.button("今天", on_click=DashboardState.set_date(today_str()), size="2"),
            rx.button("刷新全部", on_click=[
                DashboardState.load_diagnosis,
                DashboardState.load_collector_files,
                DashboardState.load_picks,
            ], size="2"),
        ),
        width="100%", padding="1em",
        border_bottom="1px solid #e0e0e0",
    )


# ═══════════════════ Tab 1: 盘面诊断 ═══════════════════

def diagnosis_tab():
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
                spacing="3", width="100%",
            ),
        ),
        width="100%", padding="1em",
    )


# ═══════════════════ Tab 2: 数据采集 ═══════════════════

def collector_tab():
    return rx.vstack(
        rx.hstack(
            rx.button("🔄 刷新", on_click=DashboardState.load_collector_files, size="2"),
            rx.button("🚀 执行采集", on_click=DashboardState.run_collector, size="2", color_scheme="green"),
        ),
        rx.cond(
            DashboardState.collector_files.length() > 0,
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell("文件名"),
                        rx.table.column_header_cell("类型"),
                        rx.table.column_header_cell("大小"),
                        rx.table.column_header_cell("时间"),
                    ),
                ),
                rx.table.body(
                    rx.foreach(DashboardState.collector_files, lambda f: rx.table.row(
                        rx.table.cell(f["name"]),
                        rx.table.cell(rx.badge(f["type"])),
                        rx.table.cell(f["size"]),
                        rx.table.cell(f["mtime"]),
                    )),
                ),
                width="100%",
            ),
            rx.text("点击刷新加载文件列表", color="gray"),
        ),
        width="100%", padding="1em",
    )


# ═══════════════════ Tab 3: 选股结果 ═══════════════════

def picks_tab():
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
        rx.table.cell(p["rank"]),
        rx.table.cell(p["code"]),
        rx.table.cell(p["name"]),
        rx.table.cell(p["score"]),
        rx.table.cell(p["chg_pct"]),
        rx.table.cell(p["main_flow"]),
        rx.table.cell(p["sector_name"]),
    )

def _limit_row(p: dict):
    return rx.table.row(
        rx.table.cell(p["code"]),
        rx.table.cell(p["name"]),
        rx.table.cell(p["chg_pct"]),
        rx.table.cell(p["main_flow"]),
        rx.table.cell(p["sector_name"]),
    )


# ═══════════════════ Tab 4: 定时任务 ═══════════════════

def cron_tab():
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


# ═══════════════════ Main App ═══════════════════

def index():
    return rx.container(
        navbar(),
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger("📈 盘面诊断", value="diag"),
                rx.tabs.trigger("📁 数据采集", value="collector"),
                rx.tabs.trigger("🔥 选股结果", value="picks"),
                rx.tabs.trigger("⏰ 定时任务", value="cron"),
            ),
            rx.tabs.content(diagnosis_tab(), value="diag"),
            rx.tabs.content(collector_tab(), value="collector"),
            rx.tabs.content(picks_tab(), value="picks"),
            rx.tabs.content(cron_tab(), value="cron"),
            default_value="diag", width="100%",
        ),
        size="4",
    )

app = rx.App()
app.add_page(index, route="/", title="A股量化系统 Dashboard")
