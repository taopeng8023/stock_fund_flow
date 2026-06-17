"""采集管线引擎 — 按注册表顺序执行采集器，管理失败和重试"""
import importlib
import time
from data_collector.config import COLLECTOR_REGISTRY


class CollectResult:
    """单个采集器执行结果"""
    def __init__(self, name, description, success, rows_count=0, error=None, elapsed=0):
        self.name = name
        self.description = description
        self.success = success
        self.rows_count = rows_count
        self.error = error
        self.elapsed = elapsed


def run_pipeline(date_str, registry=None):
    """按注册表顺序执行所有采集器

    返回:
      results: list[CollectResult]
      aborted: bool — 是否因 required 采集器失败而中断
    """
    if registry is None:
        registry = COLLECTOR_REGISTRY

    total = len(registry)
    results = []

    for i, entry in enumerate(registry, 1):
        name = entry["name"]
        desc = entry["description"]
        required = entry["required"]

        print(f"\n[{i}/{total}] {desc}")

        t0 = time.time()
        try:
            module = importlib.import_module(entry["module"])
            data = module.collect(date_str)
            elapsed = time.time() - t0
            count = len(data) if isinstance(data, (list, dict)) else (1 if data else 0)
            results.append(CollectResult(name, desc, True, count, elapsed=elapsed))
            print(f"  ✓ 完成 ({count} 条, {elapsed:.1f}s)")

        except Exception as e:
            elapsed = time.time() - t0
            results.append(CollectResult(name, desc, False, error=str(e), elapsed=elapsed))
            print(f"  ✗ 失败: {e}")
            if required:
                print(f"\n  ⚠️ 必要采集器 [{name}] 失败，管线中断")
                return results, True

    return results, False


def run_with_retry(date_str, max_retries=2):
    """带重试的采集管线"""
    for attempt in range(max_retries + 1):
        if attempt > 0:
            print(f"\n{'═' * 50}")
            print(f"  重试第 {attempt} 次...")
            time.sleep(5)

        results, aborted = run_pipeline(date_str)

        if not aborted:
            failed = [r for r in results if not r.success]
            if not failed:
                return results, False
            print(f"\n  {len(failed)} 个采集器失败，准备重试...")
        else:
            return results, True

    return results, False
