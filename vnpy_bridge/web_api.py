"""
FastAPI router providing REST endpoints for stock picks, market diagnosis, performance
"""
import json
import os
from datetime import datetime
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

api_router = APIRouter(prefix="/api")


def _row_to_dict(row):
    """peewee model → dict"""
    data = {}
    for field_name in row._meta.fields:
        val = getattr(row, field_name)
        if isinstance(val, datetime):
            val = val.isoformat()
        data[field_name] = val
    # Parse JSON strings
    for json_field in ["sub_scores", "signals", "risks", "risk_alerts", "top_sectors",
                        "bottom_sectors", "scores_detail", "sentiment_components",
                        "sentiment_detail", "indices_data"]:
        if json_field in data and isinstance(data[json_field], str):
            try:
                data[json_field] = json.loads(data[json_field])
            except (json.JSONDecodeError, TypeError):
                pass
    return data


@api_router.get("/ping")
def ping():
    return {"status": "ok", "time": datetime.now().isoformat()}


@api_router.get("/stock_picks")
def stock_picks(date: str = Query(None), top: int = Query(5)):
    """获取选股结果：指定日期或最新"""
    from vnpy_bridge.database import get_picks_by_date, get_latest_picks
    if date:
        rows = get_picks_by_date(date)
    else:
        rows = get_latest_picks(top)

    if not rows:
        return {"date": date, "picks": [], "total": 0}

    picks = [_row_to_dict(r) for r in rows]
    return {"date": date or picks[0]["pick_date"], "picks": picks, "total": len(picks)}


@api_router.get("/stock_picks/history")
def stock_picks_history(days: int = Query(7)):
    """获取历史多日选股记录"""
    from peewee import fn
    from vnpy_bridge.database import StockPick
    dates = (
        StockPick.select(StockPick.pick_date)
        .distinct()
        .order_by(StockPick.pick_date.desc())
        .limit(days)
    )
    result = {}
    for d in dates:
        picks = list(StockPick.select().where(StockPick.pick_date == d.pick_date).order_by(StockPick.rank.asc()))
        result[d.pick_date] = [_row_to_dict(p) for p in picks]
    return {"history": result}


@api_router.get("/market_diagnosis")
def market_diagnosis(date: str = Query(None)):
    """获取盘面诊断：指定日期或最新"""
    from vnpy_bridge.database import get_latest_diagnosis, MarketDiagnosis
    if date:
        row = MarketDiagnosis.select().where(MarketDiagnosis.diag_date == date).first()
    else:
        row = get_latest_diagnosis()

    if row is None:
        return {"available": False}

    return {"available": True, "diagnosis": _row_to_dict(row)}


@api_router.get("/performance/summary")
def performance_summary():
    """获取绩效摘要"""
    from performance import get_summary
    return get_summary()


@api_router.get("/scheduler/status")
def scheduler_status():
    """获取调度器状态"""
    import os
    from fetchers.base import DATA_ROOT
    # 检查数据目录中的最新日期
    dates = sorted([d for d in os.listdir(DATA_ROOT)
                    if os.path.isdir(os.path.join(DATA_ROOT, d)) and d.isdigit()],
                   reverse=True)
    return {
        "latest_data_date": dates[0] if dates else None,
        "data_dates_count": len(dates),
        "data_root": DATA_ROOT,
    }


@api_router.post("/pipeline/fetch-data")
def step_fetch_data(date: str = Query(None)):
    """步骤1: 从东方财富采集数据"""
    import subprocess, sys, os
    if date:
        date_str = date
    else:
        from datetime import datetime as dt
        from fetchers.base import BJS_TZ
        date_str = dt.now(BJS_TZ).strftime("%Y%m%d")
    try:
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cp = subprocess.run(
            [sys.executable, os.path.join(project_dir, "fetch_data.py"), f"--date={date_str}"],
            capture_output=True, text=True, timeout=120, cwd=project_dir
        )
        return {"success": cp.returncode == 0, "date": date_str,
                "stdout": cp.stdout[-2000:], "stderr": cp.stderr[-500:]}
    except Exception as e:
        return {"success": False, "error": str(e)}


@api_router.post("/pipeline/import-data")
def step_import_data(date: str = Query(None)):
    """步骤2: 将 fund_flow.json 导入 vnpy 数据库"""
    if date is None:
        from datetime import datetime as dt
        from fetchers.base import BJS_TZ
        date = dt.now(BJS_TZ).strftime("%Y%m%d")
    try:
        from vnpy_bridge.data_adapter import import_date
        n = import_date(date)
        return {"success": True, "date": date, "bar_count": n,
                "message": f"导入 {n} 条 BarData"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@api_router.post("/pipeline/diagnosis")
def step_diagnosis(date: str = Query(None)):
    """步骤3: 盘面诊断（市场宽度/资金流/板块/情绪/风险/仓位）"""
    if date is None:
        from datetime import datetime as dt
        from fetchers.base import BJS_TZ
        date = dt.now(BJS_TZ).strftime("%Y%m%d")
    try:
        from market_diagnosis import get_diagnosis
        from vnpy_bridge.database import init_db, save_diagnosis
        init_db()
        diag = get_diagnosis(date)
        if diag is None:
            return {"success": False, "error": f"数据不可用: {date}，请先执行数据采集"}
        save_diagnosis(date, diag)
        return {
            "success": True, "date": date,
            "stock_count": diag["stock_count"],
            "regime": diag["regime"]["label"],
            "confidence": f"{diag['regime']['confidence']:.0%}",
            "risk_level": diag["risks"]["level"],
            "position_advice": diag["position"]["adjusted"],
            "sentiment": diag.get("sentiment", {}).get("score"),
            "sentiment_label": diag.get("sentiment", {}).get("label"),
            "up_ratio": f"{diag['breadth']['up_ratio']:.1%}",
            "limit_up": diag['breadth']['limit_up'],
            "limit_down": diag['breadth']['limit_down'],
            "risk_alerts": diag['risks']['alerts'][:3],
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@api_router.post("/pipeline/stock-picks")
def step_stock_picks(date: str = Query(None), top_n: int = Query(5)):
    """步骤4: 多因子选股"""
    if date is None:
        from datetime import datetime as dt
        from fetchers.base import BJS_TZ
        date = dt.now(BJS_TZ).strftime("%Y%m%d")
    try:
        from stock_picker import get_picks
        from vnpy_bridge.database import init_db, save_picks
        init_db()
        result = get_picks(date, top_n=top_n)
        if result is None:
            return {"success": False, "error": f"选股数据不可用: {date}"}
        save_picks(date, result["picks"], result["regime"])
        picks_summary = []
        for p in result["picks"]:
            picks_summary.append({
                "rank": p["rank"], "code": p["code"], "name": p["name"],
                "score": p["score"], "main_flow_yi": round(p.get("main_flow", 0) / 1e8, 2),
                "industry": p.get("industry", ""), "chg": p.get("chg", 0),
            })
        return {"success": True, "date": date, "picks_count": len(picks_summary),
                "regime": result["regime"], "picks": picks_summary}
    except Exception as e:
        return {"success": False, "error": str(e)}


@api_router.post("/pipeline/performance")
def step_performance(date: str = Query(None)):
    """步骤5: 绩效追踪（记录选股 + 回测历史）"""
    if date is None:
        from datetime import datetime as dt
        from fetchers.base import BJS_TZ
        date = dt.now(BJS_TZ).strftime("%Y%m%d")
    try:
        from performance import update, record_picks, get_summary
        from stock_picker import get_picks as gp
        pr = gp(date, top_n=5)
        picks_recorded = 0
        if pr:
            record_picks(pr["scored"][:5], date)
            picks_recorded = len(pr["scored"][:5])
        updated = update(date)
        summary = get_summary()
        return {
            "success": True, "date": date,
            "picks_recorded": picks_recorded,
            "total_picks": summary["total_picks"],
            "completed": summary["total_picks"],
            "win_rate": summary["win_rate"],
            "avg_return": summary["avg_return"],
            "factor_edge": summary.get("factor_analysis", {}).get("factor_edge"),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@api_router.post("/pipeline/run-all")
def trigger_pipeline(date: str = Query(None)):
    """全量执行: 导入 + 诊断 + 选股 + 绩效（通过调度引擎）"""
    from vnpy_bridge.scheduler_engine import run_job_now
    result = run_job_now("run-all")
    if result:
        return {"success": result.get("status") == "success", "result": result.get("result", result)}
    return {"success": False, "error": "执行失败"}


# ── Scheduler Management (APScheduler) ──

@api_router.get("/scheduler/jobs")
def scheduler_jobs():
    """获取所有调度任务状态"""
    from vnpy_bridge.scheduler_engine import get_jobs_status
    return {"jobs": get_jobs_status()}


@api_router.post("/scheduler/jobs/{job_id}/run")
def scheduler_run_job(job_id: str):
    """手动触发单个任务"""
    from vnpy_bridge.scheduler_engine import run_job_now
    result = run_job_now(job_id)
    if result:
        return {"success": result.get("status") == "success", "result": result}
    return {"success": False, "error": f"未知任务: {job_id}"}


@api_router.get("/scheduler/history")
def scheduler_execution_history(limit: int = Query(30)):
    """获取调度执行历史"""
    from vnpy_bridge.scheduler_engine import get_execution_history
    return {"history": get_execution_history(limit)}


@api_router.post("/scheduler/daily")
def scheduler_toggle_daily(enabled: bool = Query(True)):
    """开启/关闭每日自动调度"""
    from vnpy_bridge.scheduler_engine import toggle_daily_schedule
    return toggle_daily_schedule(enabled)
