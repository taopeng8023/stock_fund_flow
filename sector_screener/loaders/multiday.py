"""多日历史数据加载 — 个股累计流入 + 板块多日排名"""
import json
import os
from datetime import datetime, timedelta
from collections import defaultdict
from data_collector.fetchers.base import DATA_ROOT, load_json
from sector_screener.config import to_float


def load_stock_multiday(date_str):
    """从历史 fund_flow.json 计算个股 5日/10日累计 + 正流入天数"""
    d = datetime.strptime(date_str, "%Y%m%d")
    result = {}

    cursor = d - timedelta(days=1)
    days_found = 0
    attempts = 0
    while days_found < 10 and attempts < 60:
        attempts += 1
        prev_str = cursor.strftime("%Y%m%d")
        path = os.path.join(DATA_ROOT, prev_str, "fund_flow.json")
        cursor -= timedelta(days=1)
        if not os.path.exists(path):
            continue
        days_found += 1
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)
        for r in rows:
            code = r.get("f12", "")
            f62 = to_float(r.get("f62"))
            if code not in result:
                result[code] = {"f62_5d": 0.0, "f62_10d": 0.0,
                                "pos_days_3d": 0, "pos_days_5d": 0,
                                "daily_f62": []}
            if days_found <= 5:
                result[code]["f62_5d"] += f62
                if days_found <= 3 and f62 > 0:
                    result[code]["pos_days_3d"] += 1
                if f62 > 0:
                    result[code]["pos_days_5d"] += 1
            result[code]["f62_10d"] += f62
            if days_found <= 10:
                result[code]["daily_f62"].append(f62)

    print(f"  个股多日累计: {len(result)} 只, 历史{days_found}天")
    return result


def load_sector_multiday(date_str):
    """板块多日历史 (re-export from sector loader)"""
    from sector_screener.loaders.sector import load_sector_multiday as _load
    return _load(date_str)
