"""业绩预告采集器"""
from data_collector.fetchers import earnings_forecast

NAME = "业绩预告"
DESCRIPTION = "最新业绩预告（预增/预减/扭亏/首亏）"
REQUIRED = False

def collect(date_str):
    return earnings_forecast.fetch(date_str)
