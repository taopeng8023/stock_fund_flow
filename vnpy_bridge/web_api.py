"""
FastAPI router providing REST endpoints for stock picks, market diagnosis, performance
"""
import json
from datetime import datetime
from fastapi import APIRouter, Query
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


@api_router.post("/pipeline/run")
def trigger_pipeline(date: str = Query(None)):
    """手动触发管线执行"""
    from vnpy_bridge.pipeline import run
    try:
        result = run(date_str=date)
        return {"success": True, "result": {
            "bar_count": result["bar_count"],
            "picks_count": len(result.get("picks", [])),
            "diagnosis": result.get("diagnosis"),
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}
