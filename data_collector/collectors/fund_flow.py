"""个股资金流采集器 — 全量（f62 降序）"""
from data_collector.fetchers import fund_flow

NAME = "个股资金流"
DESCRIPTION = "主力净流入额排名，全量"
REQUIRED = True

def collect(date_str):
    """返回 rows 列表"""
    rows = fund_flow.fetch(date_str)
    return rows
