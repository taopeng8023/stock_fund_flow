"""
Daily pipeline orchestrator wrapping existing modules, saving results to vnpy database
"""
import sys
import os
from datetime import datetime
from fetchers.base import BJS_TZ


def run(date_str=None, db_path=None):
    """执行完整每日管线: 诊断 + 选股 + 绩效（数据导入由调用方完成）"""
    from vnpy_bridge.database import init_db, save_picks, save_diagnosis

    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    init_db(db_path)
    results = {"date": date_str, "bar_count": 0, "picks": [], "diagnosis": None}

    # 1. 盘面诊断
    print(f"[pipeline] 盘面诊断...")
    from market_diagnosis import get_diagnosis
    diag = get_diagnosis(date_str)
    if diag:
        save_diagnosis(date_str, diag)
        results["diagnosis"] = {
            "regime": diag["regime"]["regime"],
            "confidence": diag["regime"]["confidence"],
            "risk_level": diag["risks"]["level"],
            "position_advice": diag["position"]["adjusted"],
        }
        print(f"  诊断完成: {diag['regime']['label']}, 风险 {diag['risks']['level']}")

    # 3. 选股分析
    print(f"[pipeline] 多因子选股...")
    from sector_enhanced_picks import get_picks
    pick_result = get_picks(date_str, top_n=5)
    if pick_result:
        save_picks(date_str, pick_result["picks"], pick_result["regime"])
        results["picks"] = pick_result["picks"]
        print(f"  选股完成: {len(pick_result['picks'])} 只")

    # 4. 绩效追踪（复用选股结果，不再重复调用 get_picks）
    print(f"[pipeline] 绩效追踪...")
    try:
        from performance import update, record_picks
        if pick_result:
            record_picks(pick_result["scored"][:5], date_str)
        update(date_str)
        from performance import get_summary
        results["performance"] = get_summary()
    except Exception as e:
        print(f"  绩效追踪: {e}")

    print(f"[pipeline] 完成")
    return results


def main():
    date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")
    db_path = None
    for arg in sys.argv:
        if arg.startswith("--date="):
            date_str = arg.split("=")[1]
        if arg.startswith("--db="):
            db_path = arg.split("=")[1]

    run(date_str, db_path)


if __name__ == "__main__":
    main()
