"""限售解禁采集器"""
from data_collector.fetchers import lockup_expiry

NAME = "限售解禁"
DESCRIPTION = "未来90日限售解禁明细（解禁占比/解禁日期）"
REQUIRED = False

def collect(date_str):
    return lockup_expiry.fetch(date_str)
