"""历史收盘价加载 — 用于趋势检测 + 位置评分 + 技术面"""
from collections import defaultdict
from datetime import datetime, timedelta
from data_collector.fetchers.base import load_json
from sector_screener.config import to_float


def load_past_closes(date_str, codes, days=60):
    """→ {code: [close, ...]}  最近在前"""
    price_history = defaultdict(list)
    d = datetime.strptime(date_str, "%Y%m%d")
    cursor = d - timedelta(days=1)
    attempts = 0
    while attempts < 80:
        attempts += 1
        cursor_str = cursor.strftime("%Y%m%d")
        rows = load_json(cursor_str, "fund_flow")
        cursor -= timedelta(days=1)
        if rows is None:
            continue
        for r in rows:
            code = r.get("f12", "")
            if code in codes:
                close = r.get("f2")
                if isinstance(close, (int, float)) and close > 0:
                    price_history[code].append(close)
    return price_history
