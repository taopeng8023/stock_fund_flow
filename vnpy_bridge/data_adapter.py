"""
East Money fund_flow → vnpy BarData converter + SQLite importer
"""
import os
import json
from datetime import datetime

from vnpy.trader.object import BarData
from vnpy.trader.constant import Exchange, Interval
from vnpy_sqlite.sqlite_database import SqliteDatabase

from fetchers.base import DATA_ROOT, BJS_TZ


def code_to_vt(code):
    """A股代码 → vnpy symbol.exchange 格式"""
    if not isinstance(code, str) or len(code) < 3:
        return None, None
    if code.startswith("6"):
        return code, Exchange.SSE
    return code, Exchange.SZSE


def fund_flow_row_to_bar(row, date_str):
    """单条 fund_flow 记录 → vnpy BarData"""
    code = row.get("f12", "")
    symbol, exchange = code_to_vt(code)
    if symbol is None:
        return None

    f2 = row.get("f2", 0) or 0
    f17 = row.get("f17", 0) or 0

    try:
        close = float(f2)
        open_p = float(f17)
    except (ValueError, TypeError):
        return None

    if close <= 0 and open_p <= 0:
        return None

    high = max(close, open_p)
    low = min(close, open_p)

    try:
        dt = datetime.strptime(date_str, "%Y%m%d")
    except ValueError:
        return None

    bar = BarData(
        symbol=code,
        exchange=exchange,
        datetime=dt,
        interval=Interval.DAILY,
        volume=0,
        turnover=float(row.get("f62", 0) or 0),
        open_price=open_p,
        high_price=high,
        low_price=low,
        close_price=close,
        open_interest=0,
        gateway_name="EASTMONEY",
    )
    return bar


def import_date(date_str, db_path=None):
    """将指定日期的 fund_flow.json 导入 vnpy SQLite 数据库
    返回导入的 BarData 数量
    """
    fund_path = os.path.join(DATA_ROOT, date_str, "fund_flow.json")
    if not os.path.exists(fund_path):
        print(f"  数据文件不存在: {fund_path}")
        return 0

    with open(fund_path, "r", encoding="utf-8") as f:
        rows = json.load(f)

    bars = []
    for row in rows:
        bar = fund_flow_row_to_bar(row, date_str)
        if bar is not None:
            bars.append(bar)

    if not bars:
        return 0

    db = SqliteDatabase()
    db.save_bar_data(bars)
    db_path_str = db_path or db.db.database
    print(f"  导入 {len(bars)} 条 BarData → {db_path_str}")
    return len(bars)


def import_date_range(start_date, end_date, db_path=None):
    """批量导入历史数据"""
    from datetime import timedelta
    d = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")
    total = 0
    while d <= end:
        ds = d.strftime("%Y%m%d")
        n = import_date(ds, db_path)
        total += n
        d += timedelta(days=1)
    return total
