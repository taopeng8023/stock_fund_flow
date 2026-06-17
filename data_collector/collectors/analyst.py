"""分析师预测采集器 — 全市场盈利预测 + 评级"""
from data_collector.fetchers import analyst_forecast

NAME = "分析师预测"
DESCRIPTION = "全市场盈利预测 + 评级"
REQUIRED = False

def collect(date_str):
    """返回 rows 列表"""
    rows = analyst_forecast.fetch(date_str)
    return rows
