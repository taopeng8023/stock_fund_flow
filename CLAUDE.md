# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A股量化选股系统 — A-share quantitative stock selection system that fetches market data from 东方财富 (East Money), runs multi-factor scoring, market diagnosis, and performance tracking. The system exposes a FastAPI web dashboard with APScheduler-driven daily automation.

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

# Multi-factor stock picking (top 5 by default, 24-factor model)
python stock_picker.py --top=5
python stock_picker.py --date=20260520 --top=3

# Performance tracking — backtest historical picks against today's data
python performance.py --update           # evaluate pending picks
python performance.py --report           # cumulative stats view
python performance.py --summary          # JSON output

# Quick top-100 main force capital flow (standalone)
python main.py
python main.py --csv

# Web dashboard + APScheduler (port 8000)
python run_vnpy_web.py                   # import data + start scheduler + web
python run_vnpy_web.py --no-scheduler    # web only, no cron jobs
python run_vnpy_web.py --port=8080

# Legacy scheduler (standalone, no web)
python scheduler.py                      # foreground, check every 30s
python scheduler.py --daemon             # background daemon
python scheduler.py --run                # execute once immediately

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
  fetchers/  (6 data modules)
        │
        ▼
  data/YYYYMMDD/*.json + *.csv   ← raw data persisted here
        │
        ├──► market_diagnosis.py  → MarketDiagnosis (Peewee/SQLite)
        ├──► stock_picker.py      → StockPick (Peewee/SQLite)
        ├──► performance.py       → performance.json
        └──► vnpy_bridge/data_adapter.py → vnpy BarData (SQLite)
                   │
                   ▼
         vnpy_bridge/pipeline.py  (orchestrates diagnosis + picks + performance)
                   │
                   ▼
         vnpy_bridge/web_api.py   → FastAPI REST → web_ui/index.html
```

### Layer Map

| Layer | Location | Role |
|-------|----------|------|
| Data fetching | `fetchers/` | East Money API wrappers, save JSON+CSV to `data/<date>/` |
| Shared base | `fetchers/base.py` | API helpers (`push2_get`, `datacenter_get`), `save_data`, timezone config (`BJS_TZ`) |
| Analysis | `market_diagnosis.py`, `stock_picker.py`, `performance.py` | Read raw JSON, compute signals, produce structured results |
| vnpy bridge | `vnpy_bridge/` | Convert to vnpy BarData, Peewee ORM persistence, pipeline orchestration, web API |
| Web UI | `vnpy_bridge/web_ui/index.html` | Single-page dashboard with Chart.js, tabbed panels |
| Entry points | `run_vnpy_web.py` (web+scheduler), `data_collector/` (data only), `scheduler.py` (legacy) | |

### Key Design Decisions

- **Two SQLite databases**: vnpy's own `.vntrader/database.db` (BarData via vnpy_sqlite) and custom Peewee tables in the same file (StockPick, MarketDiagnosis). The scheduler also has `.vntrader/scheduler.db` for APScheduler job state.
- **Data format**: Raw data stored as date-partitioned JSON+CSV under `data/YYYYMMDD/`. Analysis modules read from these files, not from the database.
- **Dual scheduler**: `scheduler.py` is the legacy polling scheduler (subprocess-based). `vnpy_bridge/scheduler_engine.py` is the newer APScheduler version with persistent job store and web API control. `run_vnpy_web.py` uses the APScheduler version.
- **24-factor model** (`stock_picker.py`): Weighted scoring with regime-dependent weight adjustments (bull vs bear market). Weights cover capital flow, volume/price, momentum, sector resonance, analyst consensus, multi-day flow, margin, dragon-tiger board, northbound flow, and technical patterns.
- **Beijing time throughout**: `BJS_TZ = timezone(timedelta(hours=8))` used consistently. Dates formatted as `YYYYMMDD`.

### Fetcher Modules

Each fetcher has a `fetch(date_str=None)` function:
- `fund_flow.py` — paginated full-market stock capital flow (main/super/large/medium/small net flow)
- `sector_flow.py` — industry + concept sector capital flow
- `ratio_ranking.py` — stocks ranked by main-force capital ratio (f184 desc)
- `analyst_forecast.py` — analyst ratings + EPS forecast growth from datacenter API
- `dragon_tiger.py` — 龙虎榜 daily listings with institution seat details
- `north_flow.py` — 北向资金 market-level direction + top stocks
- `market_sentiment.py` — 7 major index quotes + retail sentiment aggregation

### API Endpoints (port 8000)

- `GET /api/ping` — health check
- `GET /api/stock_picks?date=&top=` — latest or date-specific picks
- `GET /api/stock_picks/history?days=7` — multi-day history
- `GET /api/market_diagnosis?date=` — diagnosis data
- `GET /api/performance/summary` — cumulative performance stats
- `GET /api/scheduler/status` — data directory status
- `GET /api/scheduler/jobs` — APScheduler job list with next run times
- `POST /api/scheduler/jobs/{job_id}/run` — manually trigger a job
- `GET /api/scheduler/history` — execution history
- `POST /api/pipeline/run-all` — full 5-step pipeline via scheduler
- Individual step endpoints: `/api/pipeline/fetch-data`, `/api/pipeline/import-data`, `/api/pipeline/diagnosis`, `/api/pipeline/stock-picks`, `/api/pipeline/performance`

### Dependencies

- **vnpy 4.4.0** — quantitative trading framework (BarData, SQLite database, exchanges)
- **FastAPI + uvicorn** — web server
- **Peewee** — custom ORM tables alongside vnpy's DB
- **APScheduler** — cron-based job scheduling with SQLAlchemy job store
- **Chart.js** — frontend charts (loaded via CDN)
- No `requirements.txt` present; dependencies installed in `.venv/`
