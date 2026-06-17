"""大宗交易采集器"""
from data_collector.fetchers import block_trade

NAME = "大宗交易"
DESCRIPTION = "机构大宗交易明细（溢价率/成交额/买卖方）"
REQUIRED = False

def collect(date_str):
    return block_trade.fetch(date_str)
