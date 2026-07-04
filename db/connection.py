"""
DuckDB 连接管理 — 单例模式，全项目共享一个连接。
CSV 数据通过 read_csv_auto() 直接查询，不导入数据库。
"""
import os
import threading

import duckdb

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.path.join(PROJECT_ROOT, "data")
RESEARCH_ROOT = os.path.join(PROJECT_ROOT, "research_data")
BAOSTOCK_ROOT = os.path.join(PROJECT_ROOT, "baostock_data", "data")
KLINE_DATA_DIR = os.path.join(PROJECT_ROOT, "kline_data")

_lock = threading.Lock()
_conn: duckdb.DuckDBPyConnection | None = None


def get_db(db_path: str | None = None) -> duckdb.DuckDBPyConnection:
    """返回 DuckDB 连接单例。db_path 仅首次调用生效。"""
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:
            return _conn
        if db_path is None:
            db_path = os.path.join(PROJECT_ROOT, "stock.duckdb")
        _conn = duckdb.connect(db_path)
        # 允许跨目录读取 CSV
        _conn.execute("SET enable_http_metadata_cache=true")
        _conn.execute("SET threads=4")
        _conn.execute("SET memory_limit='2GB'")
        return _conn


def close_db():
    """关闭连接（通常不需要，进程退出自动关闭）。"""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def query_df(sql: str) -> "pd.DataFrame":
    """执行 SQL 并返回 pandas DataFrame。"""
    import pandas as pd
    return get_db().sql(sql).df()


def query_dicts(sql: str) -> list[dict]:
    """执行 SQL 并返回 list[dict]，兼容现有 csv.DictReader 返回值。"""
    df = query_df(sql)
    return df.to_dict(orient="records")


def read_csv_glob(
    pattern: str,
    columns: list[str] | None = None,
    where: str | None = None,
    order_by: str | None = None,
    filename_as: str | None = None,
) -> str:
    """构建 read_csv_auto glob 查询的 SQL 片段。

    返回完整 SELECT 语句，调用方可以用 get_db().sql() 执行。
    """
    parts = [f"FROM read_csv_auto('{pattern}', filename=true)"]
    if columns:
        parts.insert(0, f"SELECT {', '.join(columns)}")
    else:
        parts.insert(0, "SELECT *")
    if filename_as:
        # DuckDB read_csv_auto 中 filename 列需要特殊处理
        parts[0] = parts[0].replace("SELECT ", f"SELECT filename AS {filename_as}, ")
    if where:
        parts.append(f"WHERE {where}")
    if order_by:
        parts.append(f"ORDER BY {order_by}")
    return "\n".join(parts)
