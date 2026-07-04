"""
股票筛选共享模块 — 从 stock_list.csv 读取个股列表，过滤指数/ETF。

用法:
    from stock_filter import load_stock_files, is_stock_code
    # 或
    from baostock_data.analysis.stock_filter import load_stock_files, is_stock_code

    stock_files = load_stock_files(data_dir)  # → [sh.600000.csv, ...]
    if is_stock_code("sh.600000.csv"): ...
"""
import csv
import os
from glob import glob
from typing import Optional, Set


def _get_project_root():
    """返回项目根目录。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _is_stock_by_regex(code: str) -> bool:
    """正则回退：通过代码前缀判断个股。
    sh: 6xxxxx/689xxx = 个股, 000xxx = 指数, 5xxxxx = ETF
    sz: 000xxx-302xxx = 个股, 399xxx = 指数, 159xxx = ETF
    """
    import re
    m = re.match(r'(sh|sz)\.(\d+)', code)
    if not m:
        return False
    market, num = m.group(1), m.group(2)
    if market == 'sh':
        return num[0] == '6' or num.startswith('689')
    elif market == 'sz':
        return not num.startswith('399') and not num.startswith('159')
    return False


def _load_stock_set() -> set:
    """从 stock_list.csv 读取所有个股代码。CSV 不可用时回退到正则分类。"""
    project_root = _get_project_root()
    csv_path = os.path.join(project_root, "baostock_data", "data", "stock_list.csv")

    stocks = set()
    if os.path.exists(csv_path):
        with open(csv_path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                typ = r.get("类型", "").strip()
                if typ == "个股":
                    stocks.add(r["代码"].strip())
        if stocks:
            return stocks

    # 回退：扫描 daily/stocks/ 子目录（新结构优先）
    data_root = os.path.join(project_root, "baostock_data", "data")
    search_dirs = [
        os.path.join(data_root, "daily", "stocks"),
        os.path.join(data_root, "daily"),
    ]
    for search_dir in search_dirs:
        if os.path.isdir(search_dir):
            for f in glob(os.path.join(search_dir, "sh.*.csv")):
                code = os.path.splitext(os.path.basename(f))[0]
                if _is_stock_by_regex(code):
                    stocks.add(code)
            for f in glob(os.path.join(search_dir, "sz.*.csv")):
                code = os.path.splitext(os.path.basename(f))[0]
                if _is_stock_by_regex(code):
                    stocks.add(code)

    if not stocks:
        raise FileNotFoundError(
            f"无法获取个股列表: stock_list.csv 不存在且 daily/stocks/ 目录为空"
        )
    return stocks


# 模块级缓存，避免重复读取
_STOCK_SET: Optional[Set] = None


def get_stock_set() -> set:
    """获取个股代码集合（缓存）。"""
    global _STOCK_SET
    if _STOCK_SET is None:
        _STOCK_SET = _load_stock_set()
    return _STOCK_SET


def is_stock_code(filename: str) -> bool:
    """判断文件名是否为个股。基于 stock_list.csv 中的类型标记。"""
    code = os.path.splitext(os.path.basename(filename))[0]
    return code in get_stock_set()


def load_stock_files(data_dir: str) -> list[str]:
    """从 data_dir/stocks/ 加载所有个股 K 线 CSV。

    Args:
        data_dir: daily/ 目录路径 (自动追加 /stocks)

    Returns:
        排序后的个股 CSV 文件路径列表
    """
    stocks_dir = os.path.join(data_dir, "stocks")
    if os.path.isdir(stocks_dir):
        return sorted(glob(os.path.join(stocks_dir, "*.csv")))
    # fallback: 旧结构
    stock_set = get_stock_set()
    all_files = sorted(
        glob(os.path.join(data_dir, "sh.*.csv"))
        + glob(os.path.join(data_dir, "sz.*.csv"))
    )
    return [f for f in all_files
            if os.path.splitext(os.path.basename(f))[0] in stock_set]


def is_main_board(code: str) -> bool:
    """判断是否为主板股票（排除科创板、创业板、北交所）。

    主板:
      sh: 600xxx, 601xxx, 603xxx, 605xxx
      sz: 000xxx, 001xxx, 002xxx, 003xxx
    排除:
      688xxx/689xxx (科创板), 300xxx/301xxx (创业板), 8xxxxx (北交所)
    """
    import re
    m = re.match(r'(sh|sz)\.(\d+)', code)
    if not m:
        return False
    market, num = m.group(1), m.group(2)
    if market == 'sh':
        return num[:3] in ('600', '601', '603', '605')
    elif market == 'sz':
        return num[:3] in ('000', '001', '002', '003')
    return False


def load_main_board_files(data_dir: str) -> list[str]:
    """加载所有主板个股 K 线文件（排除科创/创业/北交所）。

    优先从 data_dir/stocks/ 读取（分类后的目录结构）。
    """
    stock_files = load_stock_files(data_dir)
    return [f for f in stock_files
            if is_main_board(os.path.splitext(os.path.basename(f))[0])]


def load_etf_files(data_dir: str) -> list[str]:
    """加载所有 ETF K 线文件。"""
    etfs_dir = os.path.join(data_dir, "etfs")
    if os.path.isdir(etfs_dir):
        return sorted(glob(os.path.join(etfs_dir, "*.csv")))
    return []


def load_index_files(data_dir: str) -> list[str]:
    """加载所有指数 K 线文件。"""
    idx_dir = os.path.join(data_dir, "indices")
    if os.path.isdir(idx_dir):
        return sorted(glob(os.path.join(idx_dir, "*.csv")))
    return []


def print_filter_summary(data_dir: str, main_board_only: bool = False):
    """打印过滤摘要。"""
    # 扫描子目录（新结构）+ 扁平目录（兼容旧结构）
    search_dirs = [
        os.path.join(data_dir, "stocks"),
        os.path.join(data_dir, "etfs"),
        os.path.join(data_dir, "indices"),
        data_dir,
    ]
    all_files = []
    for sd in search_dirs:
        if os.path.isdir(sd):
            all_files.extend(glob(os.path.join(sd, "sh.*.csv")))
            all_files.extend(glob(os.path.join(sd, "sz.*.csv")))
    all_files = sorted(set(all_files))
    if main_board_only:
        stock_files = load_main_board_files(data_dir)
        label = "主板个股"
    else:
        stock_files = load_stock_files(data_dir)
        label = "个股"
    print(f"总文件: {len(all_files)} | "
          f"个股: {len(stock_files)} | "
          f"ETF: {len(load_etf_files(data_dir))} | "
          f"指数: {len(load_index_files(data_dir))}")
