"""
结果持久化 — 所有分析脚本共享的 JSON 保存模块。

用法:
    from result_store import save_results, load_results

    save_results("kline_discovery", {
        "date": "20260704",
        "stocks_used": 3500,
        "patterns": [{"name": "...", "wr": 100.0, "n": 9, ...}],
        "config": {"target_wr": 85.0, "seed": 42},
    })
"""
import json
import os
from datetime import datetime
from typing import Optional, Dict, List


def _get_results_dir():
    """返回结果存储目录。"""
    results_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "results"
    )
    os.makedirs(results_dir, exist_ok=True)
    return results_dir


def save_results(script_name: str, data: dict, timestamp: str = None):
    """保存分析结果到 JSON 文件。

    Args:
        script_name: 脚本标识 (kline_discovery / strategy_screener / ...)
        data: 结果字典
        timestamp: 时间戳 (默认当前时间)
    """
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = _get_results_dir()
    path = os.path.join(results_dir, f"{script_name}_{timestamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  💾 结果已保存: {path}")
    return path


def load_results(script_name: Optional[str] = None, limit: int = 10) -> List[Dict]:
    """加载历史结果。

    Args:
        script_name: 脚本标识，None = 全部
        limit: 返回最近 N 个结果
    """
    results_dir = _get_results_dir()
    if not os.path.isdir(results_dir):
        return []

    files = sorted(
        [f for f in os.listdir(results_dir) if f.endswith(".json")],
        reverse=True,
    )
    if script_name:
        files = [f for f in files if f.startswith(script_name)]

    results = []
    for f in files[:limit]:
        with open(os.path.join(results_dir, f), encoding="utf-8") as fh:
            results.append(json.load(fh))
    return results


def get_latest(script_name: str) -> Optional[Dict]:
    """获取最近一次结果。"""
    results = load_results(script_name, limit=1)
    return results[0] if results else None
