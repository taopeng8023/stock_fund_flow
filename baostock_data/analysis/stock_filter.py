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


def _get_project_root():
    """返回项目根目录。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_stock_set() -> set:
    """从 stock_list.csv 读取所有个股代码，返回 {sh.600000, sz.000001, ...}。"""
    project_root = _get_project_root()
    csv_path = os.path.join(project_root, "baostock_data", "data", "stock_list.csv")
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"stock_list.csv 不存在: {csv_path}")

    stocks = set()
    with open(csv_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("类型") == "个股":
                stocks.add(r["代码"])
    return stocks


# 模块级缓存，避免重复读取
_STOCK_SET: set | None = None


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
    """从 data_dir 加载所有 K 线 CSV，仅返回个股文件。

    Args:
        data_dir: daily/ 目录路径

    Returns:
        排序后的个股 CSV 文件路径列表
    """
    all_files = sorted(
        glob(os.path.join(data_dir, "sh.*.csv"))
        + glob(os.path.join(data_dir, "sz.*.csv"))
    )
    stock_set = get_stock_set()
    stock_files = [
        f for f in all_files
        if os.path.splitext(os.path.basename(f))[0] in stock_set
    ]
    return stock_files


def print_filter_summary(data_dir: str):
    """打印过滤摘要。"""
    all_files = sorted(
        glob(os.path.join(data_dir, "sh.*.csv"))
        + glob(os.path.join(data_dir, "sz.*.csv"))
    )
    stock_files = load_stock_files(data_dir)
    skipped = len(all_files) - len(stock_files)
    print(f"总文件: {len(all_files)} (跳过 {skipped} 指数/ETF), 个股: {len(stock_files)} 只")
