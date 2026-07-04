"""
DuckDB 集成模块 — 零迁移 CSV 查询层。

用法:
    from db import get_db, query_df, query_dicts

    # 直接查 CSV 文件
    db = get_db()
    df = db.sql("SELECT * FROM read_csv_auto('.../scores.csv')").df()

    # 跨日 glob 查询
    rows = query_dicts("SELECT 代码 FROM read_csv_auto('.../*.csv')")
"""

from db.connection import (
    get_db,
    close_db,
    query_df,
    query_dicts,
    read_csv_glob,
    PROJECT_ROOT,
    DATA_ROOT,
    RESEARCH_ROOT,
    BAOSTOCK_ROOT,
    KLINE_DATA_DIR,
)

__all__ = [
    "get_db",
    "close_db",
    "query_df",
    "query_dicts",
    "read_csv_glob",
    "PROJECT_ROOT",
    "DATA_ROOT",
    "RESEARCH_ROOT",
    "BAOSTOCK_ROOT",
    "KLINE_DATA_DIR",
]
