#!/usr/bin/env python3
"""
全量多快照回测 — 收盘价入场 × 全量快照出场

对所有有 scores.csv 的日期运行 backtest_multi_snapshot.run_backtest()
用法: python scripts/backtest_all.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from daily_pipeline.backtest_multi_snapshot import run_backtest, _find_next_trading_day

RESEARCH_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data"
)


def main():
    # 找出所有有 scores.csv 的日期
    dates = sorted(
        d for d in os.listdir(RESEARCH_ROOT)
        if d.isdigit() and os.path.isfile(os.path.join(RESEARCH_ROOT, d, "scores.csv"))
    )

    print(f"=== 全量多快照回测 ===\n")
    print(f"  候选日期: {len(dates)} 天\n")

    total, passed, failed = 0, 0, 0
    results = []

    for pick in dates:
        eval_date = _find_next_trading_day(pick)
        if not eval_date:
            print(f"  {pick} → 无下一交易日，跳过")
            continue

        total += 1
        print(f"\n{'='*60}")
        print(f"  [{total}] {pick} → {eval_date}")
        print(f"{'='*60}")

        try:
            result = run_backtest(pick, eval_date)
            if result:
                passed += 1
                agg = result["aggregate"]
                snaps = len(result["snapshots"])
                results.append({
                    "pick": pick, "eval": eval_date,
                    "snapshots": snaps,
                    "top50_ret": agg["top50_ret_mean"],
                    "factor_edge": agg["factor_edge_mean"],
                })
                print(f"  ✓ {snaps}快照 Top50={agg['top50_ret_mean']:+.2f}% "
                      f"区分度={agg['factor_edge_mean']:+.2f}%")
            else:
                failed += 1
                print(f"  ✗ 回测返回空")
        except Exception as e:
            failed += 1
            print(f"  ✗ 异常: {e}")

    # ── 汇总 ──
    print(f"\n{'='*60}")
    print(f"  汇总: {total} 次回测, {passed} 成功, {failed} 失败")
    print(f"{'='*60}")

    if results:
        print(f"\n  {'日期':<12} {'快照':<6} {'Top50':<10} {'区分度':<10}")
        print(f"  {'-'*40}")
        for r in results:
            print(f"  {r['pick']:<12} {r['snapshots']:<6} "
                  f"{r['top50_ret']:+.2f}%     {r['factor_edge']:+.2f}%")

        # 均值
        avg_top50 = sum(r["top50_ret"] for r in results) / len(results)
        avg_edge = sum(r["factor_edge"] for r in results) / len(results)
        print(f"  {'─'*40}")
        print(f"  {'均值':<12} {'':<6} {avg_top50:+.2f}%     {avg_edge:+.2f}%")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
