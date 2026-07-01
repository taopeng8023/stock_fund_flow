"""
BaoStock 数据模块 — 配置常量
"""
import os
from datetime import timezone, timedelta

# ============================================================
# 路径
# ============================================================
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
BAOSTOCK_DATA_ROOT = os.path.join(MODULE_DIR, "data")

# ============================================================
# 时区
# ============================================================
BJS_TZ = timezone(timedelta(hours=8))

# ============================================================
# 频率定义
# ============================================================
# 日线及以上
FREQUENCIES_DAILY_AND_ABOVE = ["d", "w", "m"]
# 分钟线
FREQUENCIES_MINUTE = ["5", "15", "30", "60"]

# 频率 → 文件名前缀 映射
FREQUENCY_MAP = {
    "d": "daily_kline",
    "w": "weekly_kline",
    "m": "monthly_kline",
    "5": "minute_kline_5",
    "15": "minute_kline_15",
    "30": "minute_kline_30",
    "60": "minute_kline_60",
}

# 频率 → 文件名前缀 反向映射
FREQUENCY_FILE_MAP = {v: k for k, v in FREQUENCY_MAP.items()}

# ============================================================
# K线数据字段
# ============================================================
# BaoStock API 请求字段
KLINE_FIELDS = [
    "date", "code", "open", "high", "low", "close", "preclose",
    "volume", "amount", "adjustflag", "turn", "tradestatus",
    "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM",
    "isST",
]

# CSV 输出表头
KLINE_HEADERS = [
    "日期", "代码", "名称", "开盘", "最高", "最低", "收盘", "前收盘",
    "成交量", "成交额", "复权类型", "换手率", "交易状态",
    "涨跌幅", "市盈率", "市净率", "市销率", "市现率",
    "是否ST",
]

# 分钟线字段（不含估值指标）
KLINE_FIELDS_MINUTE = [
    "date", "time", "code", "open", "high", "low", "close",
    "volume", "amount", "adjustflag",
]

KLINE_HEADERS_MINUTE = [
    "日期", "时间", "代码", "名称", "开盘", "最高", "最低", "收盘",
    "成交量", "成交额", "复权类型",
]

# ============================================================
# 日期范围
# ============================================================
# 分钟线最早可用日期
MINUTE_START_DATE = "2019-01-02"

# ============================================================
# 兼容模式（接入现有 daily_pipeline）
# ============================================================
# 输出到项目全局 data/ 目录时使用的文件名
DAILY_KLINES_FILENAME = "bao_daily_klines"
MINUTE_KLINES_FILENAME = "bao_minute_klines"
STOCK_LIST_FILENAME = "stock_list"

# ============================================================
# 指数代码
# ============================================================
INDEX_CODES = {
    "sh.000001": "上证指数",
    "sh.000016": "上证50",
    "sh.000300": "沪深300",
    "sh.000688": "科创50",
    "sh.000905": "中证500",
    "sh.000852": "中证1000",
    "sz.399001": "深证成指",
    "sz.399006": "创业板指",
    "sz.399005": "中小100",
}
