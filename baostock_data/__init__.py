"""
BaoStock 数据模块 — A股历史行情数据获取与存储

覆盖:
  - 日/周/月 K线 (1990至今)
  - 5/15/30/60 分钟K线 (2019至今)
  - 全市场股票列表
  - 季频财务数据
  - 指数日线数据

数据存储: baostock_data/data/<频率子目录>/  (增量追加模式)

注意：BaoStockFetcher 采用懒加载，只在直接访问时才 import baostock。
      仅需 config 常量的模块不会触发 baostock 依赖。
"""
from .config import (
    BAOSTOCK_DATA_ROOT,
    KLINE_DATA_DIR,
    BJS_TZ,
    KLINE_FIELDS,
    KLINE_HEADERS,
    FREQUENCY_MAP,
    FREQ_DIR_MAP,
    FREQUENCIES_MINUTE,
    FREQUENCIES_DAILY_AND_ABOVE,
    MINUTE_START_DATE,
    DAILY_DIR,
    WEEKLY_DIR,
    MONTHLY_DIR,
    INDEX_DIR,
    STOCK_LIST_PATH,
)


def __getattr__(name: str):
    """懒加载 BaoStockFetcher，避免 import 配置常量时触发 baostock 依赖"""
    if name == "BaoStockFetcher":
        from .fetcher import BaoStockFetcher as _fetcher
        return _fetcher
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BaoStockFetcher",
    "BAOSTOCK_DATA_ROOT",
    "KLINE_DATA_DIR",
    "BJS_TZ",
    "KLINE_FIELDS",
    "KLINE_HEADERS",
    "FREQUENCY_MAP",
    "FREQ_DIR_MAP",
    "FREQUENCIES_MINUTE",
    "FREQUENCIES_DAILY_AND_ABOVE",
    "MINUTE_START_DATE",
    "DAILY_DIR",
    "WEEKLY_DIR",
    "MONTHLY_DIR",
    "INDEX_DIR",
    "STOCK_LIST_PATH",
]
