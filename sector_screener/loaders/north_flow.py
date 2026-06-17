"""北向资金数据加载"""
from data_collector.fetchers.base import load_json


def load_north_flow_data(date_str):
    """→ {net_north, masked}"""
    rows = load_json(date_str, "north_flow")
    if rows is None:
        print(f"  北向资金: 无数据")
        return {"net_north": 0, "masked": True}
    if not rows:
        return {"net_north": 0, "masked": True}
    latest = rows[0]
    net_north = latest.get("net_north", 0)
    if abs(net_north) > 500 or (net_north == 0 and latest.get("net_south", 0) == 0):
        print(f"  北向资金: 盘中数据暂不可用")
        return {"net_north": 0, "masked": True}
    print(f"  北向资金: 北向净流入 {net_north:+.1f}亿")
    return {"net_north": net_north, "masked": False}
