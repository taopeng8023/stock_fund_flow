"""机构调研采集器"""
from data_collector.fetchers import org_research

NAME = "机构调研"
DESCRIPTION = "近30日机构调研明细（调研次数/家数）"
REQUIRED = False

def collect(date_str):
    return org_research.fetch(date_str)
