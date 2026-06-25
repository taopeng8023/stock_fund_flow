"""
数据加载模块 — 从 CSV/JSON 文件读取回测、评分、绩效数据
所有函数在文件缺失时返回空列表，由 charts.py 处理占位图
"""
import csv
import json
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_ROOT = PROJECT_DIR / "data"
RESEARCH_ROOT = PROJECT_DIR / "research_data"
BACKTEST_DIR = RESEARCH_ROOT / "backtest"


def _read_csv(path, encoding="utf-8-sig"):
    """通用 CSV 读取，返回 list[dict]，不存在返回 []"""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding=encoding) as f:
        reader = csv.DictReader(f)
        return list(reader)


def load_backtest_summary():
    """加载回测汇总数据 research_data/backtest/summary.csv
    字段: 日期, 总股票, 全市场均收益, Top50均收益, Top50胜率,
           随机50均收益, 随机50胜率, 因子区分度, D1均收益, D10均收益
    """
    return _read_csv(BACKTEST_DIR / "summary.csv")


def load_backtest_daily(date_str):
    """加载单日回测明细 research_data/backtest/daily/backtest_<date>.csv
    字段: 代码, 名称, 综合得分, 选股日价格, 次日开盘价, 次日收益%, 胜负
    """
    path = BACKTEST_DIR / "daily" / f"backtest_{date_str}.csv"
    rows = _read_csv(path)
    # 按综合得分降序排列
    rows.sort(key=lambda r: float(r.get("综合得分", 0)), reverse=True)
    return rows


def load_backtest_dates():
    """列出所有有回测明细的日期"""
    dates = []
    daily_dir = BACKTEST_DIR / "daily"
    if daily_dir.exists():
        for f in sorted(daily_dir.iterdir()):
            if f.name.startswith("backtest_") and f.suffix == ".csv":
                dates.append(f.stem.replace("backtest_", ""))
    return dates


def load_scores(date_str):
    """加载当日评分数据 research_data/<date>/scores.csv
    36列：代码, 名称, 最新价, 行业, 综合得分, 启动得分, 资金得分...
    """
    path = RESEARCH_ROOT / date_str / "scores.csv"
    return _read_csv(path)


def load_performance():
    """加载绩效追踪 performance.json"""
    path = PROJECT_DIR / "performance.json"
    if not path.exists():
        return []
    with open(path, "r") as f:
        data = json.load(f)
    return data.get("records", [])


def load_performance_summary():
    """加载绩效摘要"""
    path = PROJECT_DIR / "performance.json"
    if not path.exists():
        return {}
    with open(path, "r") as f:
        data = json.load(f)
    return data.get("summary", {})


def load_fund_flow(date_str):
    """加载资金流数据 data/<date>/fund_flow.csv"""
    path = DATA_ROOT / date_str / "fund_flow.csv"
    return _read_csv(path)
