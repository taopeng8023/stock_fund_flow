"""主力占比排名采集器 — f184 降序，前300正流入"""
from data_collector.fetchers import ratio_ranking

NAME = "主力占比排名"
DESCRIPTION = "f184 降序，前300只正流入股"
REQUIRED = False

def collect(date_str):
    """返回 rows 列表"""
    rows = ratio_ranking.fetch(date_str=date_str)
    return rows
