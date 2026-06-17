"""北向资金采集器 — 沪深港通市场层面资金流向"""
from data_collector.fetchers import north_flow

NAME = "北向资金"
DESCRIPTION = "沪深港通市场层面资金流向"
REQUIRED = False

def collect(date_str):
    """返回 rows 列表"""
    rows = north_flow.fetch(date_str)
    return rows
