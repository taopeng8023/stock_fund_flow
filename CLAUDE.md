# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A股量化选股系统 — A-share quantitative stock selection system that fetches market data from 东方财富 (East Money), runs multi-factor scoring, market diagnosis, and performance tracking. The system exposes a Reflex web dashboard with bash-scheduled daily automation.

**增强计划**: 详见 [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md) — 8 阶段增量构建买入推荐、卖出信号、黑天鹅监控、飞书通知、调度守护、Dashboard 增强。

## Common Commands

```bash
# Activate virtualenv
source .venv/bin/activate

# Full data fetch (10 modules) for today or a specific date
python -m data_collector.main
python -m data_collector.main --date=20260520

# Market diagnosis (regime detection, breadth, sentiment, risk, position advice)
python market_diagnosis.py
python market_diagnosis.py --date=20260520

# Multi-factor stock picking (top 10 by default, 22-dimension model)
python -m sector_screener.main --top=10
python -m sector_screener.main --date=20260520 --top=5
# or the standalone version
python sector_enhanced_picks.py --top=10

# Intraday pipeline (snapshots every 30 min + scoring at 14:31)
bash daily_pipeline/run.sh                           # today mode (waits for time slots)
bash daily_pipeline/run.sh 20260623                  # historical date

# Performance tracking — backtest historical picks against today's data
python performance.py --update           # evaluate pending picks
python performance.py --report           # cumulative stats view
python performance.py --summary          # JSON output

# Web dashboard
cd web_dashboard && reflex run           # http://localhost:8000
```

## Architecture

### Data Flow

```
东方财富 API (push2delay / datacenter-web)
        │
        ▼
  data_collector/fetchers/  (10 data modules)
  data_collector/collectors/ (thin wrappers, registered in config.py)
        │
        ▼
  data/YYYYMMDD/*.json + *.csv   ← raw data persisted here
        │
        ├──► market_diagnosis.py  → data/<date>/diagnosis/diagnosis_<ts>.json
        ├──► performance.py       → performance.json
        └──► sector_screener/     → data/<date>/sector_enhanced_picks.json
```

### Intraday Pipeline (独立于 data_collector)

```
daily_pipeline/run.sh
  │  9 time slots: 0931 1001 1031 1101 1131 1301 1331 1401 1431
  │
  ├──► collect.py  → research_data/<date>/intraday/fund_flow_<HHMMSS>.csv
  ├──► score.py    → research_data/<date>/scores.csv (全市场 16-factor 评分)
  ├──► analyze.py  → research_data/<date>/report_<date>.md
  ├──► backtest.py → 分组回测 + decile 单调性检验
  └──► filter_overnight.py → 尾盘隔夜候选筛选
```

### Layer Map

| Layer | Location | Role |
|-------|----------|------|
| Data fetching | `data_collector/fetchers/` | East Money API wrappers, save JSON+CSV to `data/<date>/` |
| Data orchestration | `data_collector/` | Pipeline engine, collector registry (10 collectors), retry logic |
| Intraday snapshots | `daily_pipeline/` | Intraday collect → score → analyze → backtest (独立 pipeline) |
| Stock screening | `sector_screener/` | Multi-factor scoring, sector-first filtering, 22 dimensions + P-factor adjustments |
| Analysis | `market_diagnosis.py`, `performance.py` | Read raw JSON, compute signals, produce structured results |
| Web UI | `web_dashboard/` | Reflex app (port 8000), 4 tabs: 盘面诊断/数据采集/选股结果/定时任务 |
| Entry points | `data_collector/main.py`, `sector_screener/main.py`, `daily_pipeline/main.py` | CLI for data fetch, stock picking, intraday scoring |

### Key Design Decisions

- **Data format**: Raw data stored as date-partitioned JSON+CSV under `data/YYYYMMDD/`. Analysis modules read from these files, not from the database. No ORM currently in use (Peewee planned in Phase 1 of enhancement plan).
- **22-factor model** (`sector_screener/scorers/`): Sector-first filtering + weighted scoring with regime-dependent weight adjustments (bull/bear/range). Three weight maps in `sector_screener/config.py`: WEIGHTS_BASE, WEIGHTS_BULL, WEIGHTS_BEAR. Post-scoring P-factor adjustment layer (P0-P33) in `scorers/p_factors.py` modifies total by up to ±0.15.
- **16-factor intraday model** (`daily_pipeline/score.py`): Full-market scoring with intraday trajectory factors (flow_stability, intraday_accel, rank_trajectory, vwap_position) not available in sector_screener.
- **Beijing time throughout**: `BJS_TZ = timezone(timedelta(hours=8))` used consistently. Dates formatted as `YYYYMMDD`.
- **market_sentiment.py**: Standalone module (not in collector registry). Computes 0-100 composite sentiment from 5 components: breadth, fund flow, volume, margin, index. Must be called separately; depends on existing fund_flow.json data.

### Collector Registry (`data_collector/config.py`)

Execution order, 10 collectors:

| # | Name | Required | Description |
|---|------|----------|-------------|
| 1 | fund_flow | **Yes** | 全市场个股资金流 (~5,533 stocks, paginated) |
| 2 | sector_flow | No | 行业+概念板块资金流 + Top8 成分股钻取 |
| 3 | ratio_ranking | No | 主力占比排名 (f184 降序, top 300 正流入) |
| 4 | analyst | No | 分析师盈利预测 + 评级 (2,761 stocks) |
| 5 | dragon_tiger | No | 龙虎榜上榜明细 + 机构席位识别 |
| 6 | north_flow | No | 北向资金市场流向 (沪深港通) |
| 7 | block_trade | No | 大宗交易明细 (溢价率, 买卖方) |
| 8 | org_research | No | 近30日机构调研明细 |
| 9 | earnings_forecast | No | 最新业绩预告 (增量采集+历史合并) |
| 10 | lockup_expiry | No | 限售解禁明细 (90日窗口) |

### Fetcher Modules (`data_collector/fetchers/`)

Each fetcher has a `fetch(date_str=None)` function. Two API patterns used:

**push2delay** (`push2delay.eastmoney.com/api/qt/clist/get`):
- `fund_flow.py` — 全市场个股资金流 (main/super/large/medium/small net flow, 37 raw fields → 20 CSV columns)
- `sector_flow.py` — 行业 + 概念板块资金流 + Top8 成分股钻取 (多日排名: 1d/5d/10d)
- `ratio_ranking.py` — 主力占比排名 (f184 desc, top 300)
- `north_flow.py` — 北向/南向资金 K 线数据
- `market_sentiment.py` — 7 大指数行情 + 情绪温度计合成 (standalone, not in pipeline)

**datacenter** (`datacenter-web.eastmoney.com/api/data/v1/get`):
- `analyst_forecast.py` — 分析师评级 + EPS 预测 (500/page)
- `dragon_tiger.py` — 龙虎榜上榜明细 (含 D1/D2/D5/D10 上榜后收益)
- `block_trade.py` — 大宗交易 (溢价率, 机构买入检测)
- `org_research.py` — 机构调研 (30日窗口, 500/page)
- `earnings_forecast.py` — 业绩预告 (增量模式, 按公告日期去重合并)
- `lockup_expiry.py` — 限售解禁 (90日前瞻窗口, >5%解禁比告警)

### Scoring Dimensions (`sector_screener/scorers/`)

22 dimensions, each returns 0.0-1.0:

| # | File | Dimension | Weight (base) |
|---|------|-----------|---------------|
| 1 | start_signal.py | 启动信号 (板块新鲜度+资金加速度) | 0.13 |
| 2 | capital.py | 资金强度 (f62/f66/f72/f184/f69 加权百分位) | 0.19 |
| 3 | trend.py | 趋势确认 (量比/换手/动量/均线斜率) | 0.10 |
| 4 | sector.py | 板块共振 (行业排名+轮动+概念叠加) | 0.05 |
| 5 | position.py | 位置健康 (60日高低位, 均值回归) | 0.07 |
| 6 | analyst.py | 分析师共识 (评级+EPS增长) | 0.05 |
| 7 | multiday.py | 多日累计 (3d/5d/10d 持续性) | 0.06 |
| 8 | technical.py | 技术形态 (MA排列+突破) | 0.05 |
| 9 | dragon_tiger.py | 龙虎榜 (上榜+机构+主买) | 0.03 |
| 10 | north_flow.py | 北向环境 (净流向+量级) | 0.02 |
| 11 | ratio_rank.py | 主力占比排名 | 0.02 |
| 12 | intra_sector.py | 行业内排名 | 0.04 |
| 13 | margin_net.py | 融资净买入 | 0.03 |
| 14 | flow_accel.py | 资金加速度 (3d/10d 比值) | 0.02 |
| 15 | block_trade.py | 大宗交易 (溢价+机构) | 0.02 |
| 16 | org_research.py | 机构调研 (次数) | 0.02 |
| 17 | earnings_forecast.py | 业绩预告 (类型评分) | 0.02 |
| 18 | lockup_expiry.py | 限售解禁 (惩罚项) | 0.01 |
| 19 | margin_short.py | 融券压力 (卖空检测) | 0.02 |
| 20 | margin_long.py | 融资买入力 (买入比+净买比) | 0.02 |
| 21 | volume_quality.py | 量价质量 (放量真伪) | 0.01 |
| 22 | intraday.py | 盘中轨迹动量 (快照间改善) | 0.02 |

### Market Diagnosis (`market_diagnosis.py`)

8 diagnostic modules producing a comprehensive daily health check:

| Module | Output |
|--------|--------|
| Breadth | 涨跌分布, 涨跌停数, P10/P25/median/P75/P90 |
| Fund Flow Panorama | 主力净流入总额, 正流比, 机构主导比, 量比, 换手 |
| Sector Rotation | Top5/Bottom5 行业+概念板块 |
| Northbound Flow | 北向净流向+量级 |
| Market Regime | 4维评分卡 → 5级体制 (bull/bull_bias/range/bear_bias/bear) |
| Risk Warning | 极端宽度/跌停潮/涨跌停狂热/量价背离/北向逃逸 → risk level (low/medium/high/critical) |
| Position Advice | 基础仓位 × 风险折扣 → 建议仓位 (0-100%) |
| Market Sentiment | 情绪温度计 0-100 (5成分合成) |

### Dependencies (installed in .venv/)

```
reflex==0.9.5.post2     # Web framework (NOT FastAPI — CLAUDE.md previously stated FastAPI in error)
redis==7.4.1             # Redis client (installed but not currently used in project)
requests==2.34.2
pydantic==2.13.4
httpx==0.28.1
python-socketio==5.16.3
granian==2.7.6           # ASGI server
rich==14.3.4
```

No `requirements.txt` present. Key absent packages (to be installed per enhancement plan): `peewee`, `apscheduler`, `numpy`.

### Things That Don't Exist Yet (but are referenced)

- `daily_run.sh` — referenced by web_dashboard Cron tab, DOES NOT EXIST. Will be created in Phase 7.
- Peewee ORM models — referenced in old CLAUDE.md, never implemented. Will be created in Phase 1.
- Any sell/exit strategy code — the system is purely buy-side. Will be created in Phase 4.
- Any notification code (飞书/email/etc.) — completely absent. Will be created in Phase 6.
- APScheduler daemon — all scheduling is bash-based. Will be created in Phase 7.
