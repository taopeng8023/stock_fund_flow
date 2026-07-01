"""
BaoStock 数据模块 — A股历史行情数据获取与存储

覆盖:
  - 日/周/月 K线 (1990至今)
  - 5/15/30/60 分钟K线 (2019至今)
  - 全市场股票列表
  - 季频财务数据
  - 指数日线数据

数据存储: baostock_data/data/YYYYMMDD/
"""
from .fetcher import BaoStockFetcher
from .config import (
    BAOSTOCK_DATA_ROOT,
    BJS_TZ,
    KLINE_FIELDS,
    KLINE_HEADERS,
    FREQUENCY_MAP,
    FREQUENCIES_MINUTE,
    FREQUENCIES_DAILY_AND_ABOVE,
    MINUTE_START_DATE,
)

__all__ = [
    "BaoStockFetcher",
    "BAOSTOCK_DATA_ROOT",
    "BJS_TZ",
    "KLINE_FIELDS",
    "KLINE_HEADERS",
    "FREQUENCY_MAP",
    "FREQUENCIES_MINUTE",
    "FREQUENCIES_DAILY_AND_ABOVE",
    "MINUTE_START_DATE",
]
