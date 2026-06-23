# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A股量化选股系统 — A-share quantitative stock selection system that fetches market data from 东方财富 (East Money), runs multi-factor scoring, market diagnosis, and performance tracking. The system exposes a Reflex web dashboard with APScheduler-driven daily automation.

**增强计划**: 详见 [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md) — 8 阶段增量构建买入推荐、卖出信号、黑天鹅监控、飞书通知、调度守护、Dashboard 增强。

## Common Commands

```bash
# Activate virtualenv
source .venv/bin/activate

# Full data fetch (6 modules) for today or a specific date
python -m data_collector.main
python -m data_collector.main --date=20260520

# Market diagnosis (regime detection, breadth, sentiment, risk, position advice)
python market_diagnosis.py
python market_diagnosis.py --date=20260520

# Multi-factor stock picking (top 10 by default, 14-dimension model)
python -m sector_screener.main --top=10
python -m sector_screener.main --date=20260520 --top=5
# or the standalone version
python sector_enhanced_picks.py --top=10

# Performance tracking — backtest historical picks against today's data
python performance.py --update           # evaluate pending picks
python performance.py --report           # cumulative stats view
python performance.py --summary          # JSON output

# Bash wrapper for cron
./daily_run.sh                           # run full pipeline now
./daily_run.sh --schedule               # start polling daemon
```

## Architecture

### Data Flow

```
东方财富 API (push2delay / datacenter-web)
        │
        ▼
  data_collector/fetchers/  (6 data modules)
        │
        ▼
  data/YYYYMMDD/*.json + *.csv   ← raw data persisted here
        │
        ├──► market_diagnosis.py  → MarketDiagnosis
        ├──► performance.py       → performance.json
        └──► sector_screener/     → 14-dimension stock picking + JSON/CSV output
```

### Layer Map

| Layer | Location | Role |
|-------|----------|------|
| Data fetching | `data_collector/fetchers/` | East Money API wrappers, save JSON+CSV to `data/<date>/` |
| Data orchestration | `data_collector/` | Pipeline engine, collector registry, retry logic |
| Stock screening | `sector_screener/` | Multi-factor scoring, sector-first filtering, 14 dimensions |
| Analysis | `market_diagnosis.py`, `performance.py` | Read raw JSON, compute signals, produce structured results |
| Entry points | `data_collector/main.py`, `sector_screener/main.py` | CLI for data fetch and stock picking |

### Key Design Decisions

- **Data format**: Raw data stored as date-partitioned JSON+CSV under `data/YYYYMMDD/`. Analysis modules read from these files, not from the database.
- **14-factor model** (`sector_screener/scorers/`): Sector-first filtering + weighted scoring with regime-dependent weight adjustments (bull vs bear market). Dimensions cover start signal, capital intensity, trend, sector resonance, position health, analyst consensus, multi-day flow, technical, dragon-tiger board, northbound flow, ratio ranking, intra-sector ranking, margin net, flow acceleration.
- **Beijing time throughout**: `BJS_TZ = timezone(timedelta(hours=8))` used consistently. Dates formatted as `YYYYMMDD`.

### Fetcher Modules (`data_collector/fetchers/`)

Each fetcher has a `fetch(date_str=None)` function:
- `fund_flow.py` — paginated full-market stock capital flow (main/super/large/medium/small net flow)
- `sector_flow.py` — industry + concept sector capital flow + constituent stock drill-down
- `ratio_ranking.py` — stocks ranked by main-force capital ratio (f184 desc)
- `analyst_forecast.py` — analyst ratings + EPS forecast growth from datacenter API
- `dragon_tiger.py` — 龙虎榜 daily listings with institution seat details
- `north_flow.py` — 北向资金 market-level direction + top stocks
- `market_sentiment.py` — 7 major index quotes + retail sentiment aggregation

### Dependencies

- **FastAPI + uvicorn** — web server (if web UI is re-enabled)
- **Peewee** — ORM for StockPick / MarketDiagnosis persistence
- **Chart.js** — frontend charts (loaded via CDN)
- No `requirements.txt` present; dependencies installed in `.venv/`
