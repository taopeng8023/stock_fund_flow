"""龙虎榜数据加载"""
from datetime import datetime, timedelta
from fetchers.base import load_json


def load_dragon_tiger_data(date_str):
    """→ {code: {on_board, has_institution, is_main_buy}}"""
    rows = load_json(date_str, "dragon_tiger")
    if rows is None:
        d = datetime.strptime(date_str, "%Y%m%d")
        prev = d - timedelta(days=1)
        for _ in range(3):
            prev_str = prev.strftime("%Y%m%d")
            rows = load_json(prev_str, "dragon_tiger")
            if rows is not None:
                print(f"  龙虎榜: 使用 {prev_str} 数据 (当日暂无)")
                break
            prev -= timedelta(days=1)
    if rows is None:
        print(f"  龙虎榜: 无可用数据")
        return {}

    dt_data = {}
    for r in rows:
        code = r.get("SECURITY_CODE", "")
        explain = r.get("EXPLAIN", "") or ""
        has_inst = "机构" in str(explain)
        is_buy = "主买" in str(explain)
        dt_data[code] = {
            "on_board": True,
            "has_institution": has_inst,
            "is_main_buy": is_buy,
        }
    print(f"  龙虎榜: {len(dt_data)} 只上榜")
    return dt_data
