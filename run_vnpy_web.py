"""
vnpy Web Dashboard 启动入口 + APScheduler 调度引擎
用法:
  python run_vnpy_web.py               默认: 导入数据 + 启动调度器 + web server
  python run_vnpy_web.py --no-scheduler  不启动自动调度
  python run_vnpy_web.py --port=8080     自定义端口
"""
import sys
import os
import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import uvicorn

from vnpy_bridge.database import init_db
from vnpy_bridge.web_api import api_router
from vnpy_bridge.scheduler_engine import start_scheduler, stop_scheduler

from fetchers.base import DATA_ROOT, BJS_TZ

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(PROJECT_DIR, ".vntrader", "database.db")


def create_app():
    app = FastAPI(title="A股量化选股系统", version="2.0")
    app.include_router(api_router)

    ui_dir = os.path.join(PROJECT_DIR, "vnpy_bridge", "web_ui")

    @app.get("/")
    def index():
        html_path = os.path.join(ui_dir, "index.html")
        if os.path.exists(html_path):
            return HTMLResponse(open(html_path).read())
        return HTMLResponse("<h1>Dashboard not found</h1>")

    if os.path.isdir(ui_dir):
        app.mount("/static", StaticFiles(directory=os.path.join(ui_dir, "static")), name="static")

    @app.on_event("shutdown")
    def on_shutdown():
        stop_scheduler()

    return app


def import_and_run(date_str=None):
    """初始化数据库 + 导入数据 + 运行管线"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    print(f"初始化数据库: {DB_PATH}")
    init_db(DB_PATH)

    has_data = os.path.exists(os.path.join(DATA_ROOT, date_str, "fund_flow.json"))

    if has_data:
        print(f"导入 {date_str} 数据到 vnpy 数据库...")
        from vnpy_bridge.data_adapter import import_date
        n = import_date(date_str, DB_PATH)
        print(f"  已导入 {n} 条 BarData")

        print(f"运行每日管线...")
        from vnpy_bridge.pipeline import run
        run(date_str, DB_PATH)
    else:
        print(f"  数据不存在: {date_str}，请先运行 python fetch_data.py --date={date_str}")


def main():
    date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")
    port = 8000
    enable_scheduler = "--no-scheduler" not in sys.argv

    for arg in sys.argv:
        if arg.startswith("--date="):
            date_str = arg.split("=")[1]
        if arg.startswith("--port="):
            port = int(arg.split("=")[1])

    import_and_run(date_str)

    if enable_scheduler:
        os.makedirs(os.path.join(PROJECT_DIR, ".vntrader"), exist_ok=True)
        start_scheduler()
    else:
        print("[scheduler] 自动调度已禁用 (--no-scheduler)")

    app = create_app()
    print(f"\n  A股量化选股系统 Web Dashboard")
    print(f"  地址: http://localhost:{port}")
    print(f"  自动调度: {'已开启 (每日15:35)' if enable_scheduler else '已关闭'}")
    print(f"  按 Ctrl+C 停止\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
