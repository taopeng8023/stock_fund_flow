"""
APScheduler-based scheduling engine for daily pipeline steps.
Persists jobs + execution history via SQLite job store.
"""
import os
import json
import time
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED

from fetchers.base import BJS_TZ

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_FILE = os.path.join(PROJECT_DIR, ".scheduler_history.json")
DB_URL = f"sqlite:///{os.path.join(PROJECT_DIR, '.vntrader', 'scheduler.db')}"

# Ensure history file exists
if not os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "w") as f:
        json.dump([], f)

_scheduler = None
_execution_log = []


def _load_log():
    global _execution_log
    try:
        with open(HISTORY_FILE, "r") as f:
            _execution_log = json.load(f)
    except Exception:
        _execution_log = []
    return _execution_log


def _save_log():
    with open(HISTORY_FILE, "w") as f:
        json.dump(_execution_log[-200:], f, ensure_ascii=False, indent=2)


def _log_execution(job_id, status, result=None, error=None):
    entry = {
        "job_id": job_id,
        "status": status,
        "time": datetime.now(BJS_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "result": result,
        "error": str(error)[:500] if error else None,
    }
    _execution_log.append(entry)
    _save_log()
    return entry


# ── Job Functions ──

def job_fetch_data():
    """采集东方财富数据"""
    import subprocess, sys
    _load_log()
    date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")
    try:
        cp = subprocess.run(
            [sys.executable, os.path.join(PROJECT_DIR, "fetch_data.py"), f"--date={date_str}"],
            capture_output=True, text=True, timeout=120, cwd=PROJECT_DIR
        )
        success = cp.returncode == 0
        result = {"date": date_str, "stdout": cp.stdout[-1000:], "stderr": cp.stderr[-300:]}
        _log_execution("fetch-data", "success" if success else "failed", result)
        print(f"[scheduler] fetch-data: {'OK' if success else 'FAILED'}")
    except Exception as e:
        _log_execution("fetch-data", "failed", error=e)
        print(f"[scheduler] fetch-data error: {e}")


def job_import_data():
    """导入 fund_flow → vnpy BarData"""
    _load_log()
    date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")
    try:
        from vnpy_bridge.data_adapter import import_date
        n = import_date(date_str)
        result = {"date": date_str, "bar_count": n}
        _log_execution("import-data", "success", result)
        print(f"[scheduler] import-data: {n} bars")
    except Exception as e:
        _log_execution("import-data", "failed", error=e)
        print(f"[scheduler] import-data error: {e}")


def job_diagnosis():
    """盘面诊断"""
    _load_log()
    date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")
    try:
        from market_diagnosis import get_diagnosis
        from vnpy_bridge.database import init_db, save_diagnosis
        init_db()
        diag = get_diagnosis(date_str)
        if diag is None:
            _log_execution("diagnosis", "failed", error=f"数据不可用: {date_str}")
            return
        save_diagnosis(date_str, diag)
        result = {
            "date": date_str, "stock_count": diag["stock_count"],
            "regime": diag["regime"]["label"],
            "risk_level": diag["risks"]["level"],
            "position_advice": diag["position"]["adjusted"],
            "sentiment": diag.get("sentiment", {}).get("score"),
            "up_ratio": f"{diag['breadth']['up_ratio']:.1%}",
            "limit_up": diag['breadth']['limit_up'],
            "limit_down": diag['breadth']['limit_down'],
        }
        _log_execution("diagnosis", "success", result)
        print(f"[scheduler] diagnosis: {diag['regime']['label']}, risk={diag['risks']['level']}")
    except Exception as e:
        _log_execution("diagnosis", "failed", error=e)
        print(f"[scheduler] diagnosis error: {e}")


def job_stock_picks():
    """多因子选股"""
    _load_log()
    date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")
    try:
        from stock_picker import get_picks
        from vnpy_bridge.database import init_db, save_picks
        init_db()
        result = get_picks(date_str, top_n=5)
        if result:
            save_picks(date_str, result["picks"], result["regime"])
            summary = [{"rank": p["rank"], "code": p["code"], "name": p["name"],
                        "score": round(p["score"], 4)} for p in result["picks"]]
            _log_execution("stock-picks", "success", {"date": date_str, "picks": summary})
            print(f"[scheduler] stock-picks: {len(result['picks'])} stocks")
        else:
            _log_execution("stock-picks", "failed", error="选股结果为空")
    except Exception as e:
        _log_execution("stock-picks", "failed", error=e)
        print(f"[scheduler] stock-picks error: {e}")


def job_performance():
    """绩效追踪"""
    _load_log()
    date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")
    try:
        from performance import update, record_picks, get_summary
        from stock_picker import get_picks as gp
        pr = gp(date_str, top_n=5)
        recorded = 0
        if pr:
            record_picks(pr["scored"][:5], date_str)
            recorded = len(pr["scored"][:5])
        update(date_str)
        summary = get_summary()
        result = {"date": date_str, "picks_recorded": recorded,
                  "total_completed": summary["total_picks"],
                  "win_rate": summary["win_rate"], "avg_return": summary["avg_return"]}
        _log_execution("performance", "success", result)
        print(f"[scheduler] performance: recorded={recorded}")
    except Exception as e:
        _log_execution("performance", "failed", error=e)
        print(f"[scheduler] performance error: {e}")


def job_full_pipeline():
    """一键执行全管线（按顺序）"""
    _load_log()
    date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")
    steps = [
        ("fetch-data", job_fetch_data),
        ("import-data", job_import_data),
        ("diagnosis", job_diagnosis),
        ("stock-picks", job_stock_picks),
        ("performance", job_performance),
    ]
    results = {}
    for job_id, fn in steps:
        try:
            fn()
            log_entry = next((e for e in reversed(_execution_log) if e["job_id"] == job_id), None)
            results[job_id] = log_entry["status"] if log_entry else "unknown"
        except Exception as e:
            results[job_id] = f"error: {e}"
    _log_execution("run-all", "success", {"date": date_str, "steps": results})
    print(f"[scheduler] full pipeline: {results}")


# ── Job definitions for the scheduler ──

JOB_DEFS = [
    {"id": "fetch-data",   "name": "① 数据采集",   "desc": "从东方财富抓取个股资金流/板块/北向/龙虎榜/分析师数据", "fn": job_fetch_data},
    {"id": "import-data",  "name": "② 数据导入",   "desc": "将 fund_flow.json 导入 vnpy SQLite BarData 表", "fn": job_import_data},
    {"id": "diagnosis",    "name": "③ 盘面诊断",   "desc": "市场宽度/资金流/板块/北向/情绪/风险/仓位", "fn": job_diagnosis},
    {"id": "stock-picks",  "name": "④ 多因子选股", "desc": "24因子模型全市场打分，输出TOP 5精选个股", "fn": job_stock_picks},
    {"id": "performance",  "name": "⑤ 绩效追踪",   "desc": "记录当日选股 + 用今日数据回测历史选股", "fn": job_performance},
    {"id": "run-all",      "name": "一键执行全管线", "desc": "按顺序执行上述5个步骤", "fn": job_full_pipeline},
]


def get_scheduler():
    """Get or create the global scheduler instance"""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    jobstores = {"default": SQLAlchemyJobStore(url=DB_URL)}
    executors = {"default": ThreadPoolExecutor(3)}
    job_defaults = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 300}

    _scheduler = BackgroundScheduler(
        jobstores=jobstores, executors=executors, job_defaults=job_defaults,
        timezone=BJS_TZ,
    )

    # Register event listeners
    def on_job_event(event):
        job_id = event.job_id
        if event.exception:
            _log_execution(job_id, "failed", error=str(event.exception))
        elif hasattr(event, 'retval') and event.retval is not None:
            pass  # already logged inside job function

    _scheduler.add_listener(on_job_event, EVENT_JOB_ERROR)

    return _scheduler


def start_scheduler():
    """Start the scheduler and register daily jobs"""
    sched = get_scheduler()
    if sched.running:
        return

    # Register daily schedule jobs (15:35 — after market close)
    for job_def in JOB_DEFS:
        job_id = job_def["id"]
        try:
            sched.add_job(
                job_def["fn"],
                trigger="cron",
                id=job_id,
                hour=15, minute=35,
                replace_existing=True,
            )
        except Exception as e:
            print(f"[scheduler] failed to add job {job_id}: {e}")

    sched.start()
    print("[scheduler] APScheduler started (daily at 15:35)")


def stop_scheduler():
    """Stop the scheduler"""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def get_jobs_status():
    """Get all job definitions with their current schedule status"""
    sched = get_scheduler()
    _load_log()

    jobs = []
    for jd in JOB_DEFS:
        job_id = jd["id"]
        aps_job = sched.get_job(job_id)
        # Find last execution
        last_runs = [e for e in _execution_log if e["job_id"] == job_id]
        last_run = last_runs[-1] if last_runs else None

        jobs.append({
            "id": job_id,
            "name": jd["name"],
            "desc": jd["desc"],
            "scheduled": aps_job is not None,
            "next_run": aps_job.next_run_time.strftime("%Y-%m-%d %H:%M") if aps_job and aps_job.next_run_time else None,
            "last_run": last_run,
        })
    return jobs


def get_execution_history(limit=30):
    """Get recent execution history"""
    _load_log()
    return list(reversed(_execution_log[-limit:]))


def run_job_now(job_id):
    """Manually trigger a job immediately"""
    for jd in JOB_DEFS:
        if jd["id"] == job_id:
            jd["fn"]()
            _load_log()
            last_runs = [e for e in _execution_log if e["job_id"] == job_id]
            return last_runs[-1] if last_runs else None
    return None


def toggle_daily_schedule(enabled):
    """Enable or disable all daily cron jobs"""
    sched = get_scheduler()
    if enabled:
        for jd in JOB_DEFS:
            if not sched.get_job(jd["id"]):
                sched.add_job(jd["fn"], trigger="cron", id=jd["id"],
                              hour=15, minute=35, replace_existing=True)
        if not sched.running:
            sched.start()
        return {"daily_enabled": True, "message": "每日 15:35 自动执行已开启"}
    else:
        for jd in JOB_DEFS:
            job = sched.get_job(jd["id"])
            if job:
                job.remove()
        return {"daily_enabled": False, "message": "每日自动执行已关闭"}
