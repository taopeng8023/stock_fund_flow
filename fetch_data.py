"""
全量数据采集 — 调度所有独立采集模块
用法:
  python fetch_data.py                采集当天全量数据
  python fetch_data.py --date=20260520  指定日期

数据模块:
  fetchers/fund_flow.py        个股资金流（全量）
  fetchers/sector_flow.py      板块资金流（行业+概念）
  fetchers/ratio_ranking.py    主力占比排名
  fetchers/analyst_forecast.py 分析师盈利预测+评级
  fetchers/dragon_tiger.py     龙虎榜上榜明细
  fetchers/north_flow.py       北向资金市场流向
"""
import sys
from datetime import datetime
from fetchers.base import today_str, BJS_TZ
from fetchers import fund_flow, sector_flow, ratio_ranking, analyst_forecast
from fetchers import dragon_tiger, north_flow


def main():
    date_str = today_str()
    for arg in sys.argv:
        if arg.startswith("--date="):
            date_str = arg.split("=")[1]

    print(f"═══ 采集 {date_str} 全量数据 ═══\n")

    # ── 1. 个股资金流 ──
    print("[1/6] 个股资金流（主力净流入额排名，全量）")
    stock_rows = fund_flow.fetch(date_str)
    if not stock_rows:
        print("无数据，API 不可用"); sys.exit(1)

    # ── 2. 板块资金流 ──
    print("\n[2/6] 板块资金流（行业+概念）")
    sector_flow.fetch(date_str)

    # ── 3. 主力占比排名 ──
    print("\n[3/6] 主力占比排名（f184 降序）")
    ratio_ranking.fetch(date_str=date_str)

    # ── 4. 分析师预测 ──
    print("\n[4/6] 分析师盈利预测 + 评级")
    analyst_forecast.fetch(date_str)

    # ── 5. 龙虎榜 ──
    print("\n[5/6] 龙虎榜上榜明细")
    dt_rows = dragon_tiger.fetch(date_str)

    # ── 6. 北向资金 ──
    print("\n[6/6] 北向资金市场流向")
    nf_rows = north_flow.fetch(date_str)

    # ── 汇总 ──
    from fetchers.base import DATA_ROOT
    import os
    date_dir = os.path.join(DATA_ROOT, date_str)
    files = os.listdir(date_dir) if os.path.exists(date_dir) else []

    print(f"\n{'═' * 60}")
    print(f"  采集完成 [{date_str}]")
    print(f"  数据目录: {date_dir}")
    print(f"  文件: {len(files)} 个")
    for f in sorted(files):
        size = os.path.getsize(os.path.join(date_dir, f))
        print(f"    {f} ({size:,} bytes)")
    print(f"{'═' * 60}")


if __name__ == "__main__":
    main()
