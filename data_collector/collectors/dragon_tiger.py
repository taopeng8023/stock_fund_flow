"""龙虎榜采集器 — 当日上榜个股明细（机构/游资/散户买卖拆解）"""
from fetchers import dragon_tiger

NAME = "龙虎榜"
DESCRIPTION = "上榜个股明细（机构/游资/散户买卖拆解）"
REQUIRED = False

def collect(date_str):
    """返回 rows 列表"""
    rows = dragon_tiger.fetch(date_str)
    return rows
