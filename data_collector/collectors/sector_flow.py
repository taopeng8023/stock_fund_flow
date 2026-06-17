"""板块资金流采集器 — 行业+概念 + Top5成分股钻取"""
from data_collector.fetchers import sector_flow

NAME = "板块资金流"
DESCRIPTION = "行业板块+概念板块 + Top5成分股钻取"
REQUIRED = False

def collect(date_str):
    """返回 results dict"""
    results = sector_flow.fetch(date_str)
    return results
