"""
BaoStock 数据模块 — 配置常量

数据存储: baostock_data/data/<频率子目录>/
  日线按品种分类:
    daily/stocks/   ← 个股 (sh.6*/sz.非159非399)
    daily/etfs/     ← ETF  (sh.5*/sz.159*)
    daily/indices/  ← 指数 (sh.0*/sz.399*)
  其他频率 (w/m/minute_*) 扁平存储。

分析脚本统一通过本模块获取路径，不再各自硬编码。
"""
import os
import re
from datetime import timezone, timedelta

# ============================================================
# 路径
# ============================================================
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(MODULE_DIR)
BAOSTOCK_DATA_ROOT = os.path.join(MODULE_DIR, "data")
KLINE_DATA_DIR = os.path.join(PROJECT_ROOT, "kline_data")

# 频率子目录
DAILY_DIR = os.path.join(BAOSTOCK_DATA_ROOT, "daily")
WEEKLY_DIR = os.path.join(BAOSTOCK_DATA_ROOT, "weekly")
MONTHLY_DIR = os.path.join(BAOSTOCK_DATA_ROOT, "monthly")
MINUTE_5_DIR = os.path.join(BAOSTOCK_DATA_ROOT, "minute_5")
MINUTE_15_DIR = os.path.join(BAOSTOCK_DATA_ROOT, "minute_15")
MINUTE_30_DIR = os.path.join(BAOSTOCK_DATA_ROOT, "minute_30")
MINUTE_60_DIR = os.path.join(BAOSTOCK_DATA_ROOT, "minute_60")
INDEX_DIR = os.path.join(BAOSTOCK_DATA_ROOT, "index")

# 日线子目录（按品种分类）
DAILY_STOCKS_DIR = os.path.join(DAILY_DIR, "stocks")
DAILY_ETFS_DIR = os.path.join(DAILY_DIR, "etfs")
DAILY_INDICES_DIR = os.path.join(DAILY_DIR, "indices")

# 频率 → 目录 映射
FREQ_DIR_MAP = {
    "d": DAILY_DIR,
    "w": WEEKLY_DIR,
    "m": MONTHLY_DIR,
    "5": MINUTE_5_DIR,
    "15": MINUTE_15_DIR,
    "30": MINUTE_30_DIR,
    "60": MINUTE_60_DIR,
}

# 日线品种 → 子目录 映射
DAILY_TYPE_DIR_MAP = {
    "stock": DAILY_STOCKS_DIR,
    "etf": DAILY_ETFS_DIR,
    "index": DAILY_INDICES_DIR,
}

# 所有数据目录（用于初始化）
ALL_DATA_DIRS = [
    DAILY_DIR, DAILY_STOCKS_DIR, DAILY_ETFS_DIR, DAILY_INDICES_DIR,
    WEEKLY_DIR, MONTHLY_DIR,
    MINUTE_5_DIR, MINUTE_15_DIR, MINUTE_30_DIR, MINUTE_60_DIR,
    INDEX_DIR, KLINE_DATA_DIR,
]

# 股票列表固定路径
STOCK_LIST_PATH = os.path.join(BAOSTOCK_DATA_ROOT, "stock_list.csv")

# ============================================================
# 目录初始化
# ============================================================
def ensure_dirs() -> None:
    """创建所有数据目录（已有则跳过）。"""
    for d in ALL_DATA_DIRS:
        os.makedirs(d, exist_ok=True)


# ============================================================
# 代码分类
# ============================================================
def classify_code(code: str) -> str:
    """根据股票代码判断品种类型，返回 'stock' | 'etf' | 'index'。

    规则:
      sh.6xxxxx / sh.689xxx → stock (个股)
      sh.5xxxxx              → etf  (ETF)
      sh.0xxxxx              → index (上证指数族)
      sz.0xxxxx-3xxxxx       → stock (排除 159/399)
      sz.159xxx              → etf  (ETF)
      sz.399xxx              → index (深证指数族)
      bj.xxxxxx              → stock (北交所)
    """
    m = re.match(r'(sh|sz|bj)\.(\d+)', code)
    if not m:
        return "stock"
    market, num = m.group(1), m.group(2)
    if market == 'sh':
        if num.startswith('6') or num.startswith('689'):
            return "stock"
        if num.startswith('5'):
            return "etf"
        return "index"  # sh.0*
    elif market == 'sz':
        if num.startswith('159'):
            return "etf"
        if num.startswith('399'):
            return "index"
        return "stock"
    return "stock"  # bj


def get_daily_subdir(code: str) -> str:
    """根据代码返回日线 CSV 应存储的子目录路径。"""
    typ = classify_code(code)
    return DAILY_TYPE_DIR_MAP[typ]


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
