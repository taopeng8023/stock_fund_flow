"""采集完成汇总报告"""
import os
from datetime import datetime
from data_collector.fetchers.base import DATA_ROOT, BJS_TZ


def print_summary(results, date_str, aborted=False):
    """打印采集汇总报告"""
    date_dir = os.path.join(DATA_ROOT, date_str)
    files = os.listdir(date_dir) if os.path.exists(date_dir) else []

    print(f"\n{'═' * 60}")
    if aborted:
        print(f"  ⚠️  采集中断 [{date_str}]")
    else:
        print(f"  采集完成 [{date_str}]")
    print(f"  数据目录: {date_dir}")
    print(f"  生成文件: {len(files)} 个")
    print(f"{'═' * 60}")

    # 采集器明细
    print(f"\n  📊 采集器明细:")
    for r in results:
        status = "✅" if r.success else "❌"
        rows_info = f"{r.rows_count:,} 条" if r.rows_count else "-"
        error_info = f" — {r.error}" if r.error else ""
        print(f"    {status} {r.name:<14} {rows_info:<12} {r.elapsed:.1f}s{error_info}")

    # 文件清单
    print(f"\n  📁 文件清单 ({date_str}/):")
    for f in sorted(files):
        size = os.path.getsize(os.path.join(date_dir, f))
        print(f"    {f:<45} {size:>10,} bytes")

    now = datetime.now(BJS_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n  🕐 采集时间: {now}")
    print(f"  {'═' * 60}")
