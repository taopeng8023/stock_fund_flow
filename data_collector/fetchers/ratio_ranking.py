"""
主力占比排名 — f184 降序，前 300 只正流入股
数据页面: https://data.eastmoney.com/zjlx/list.html
"""
import time
from .base import push2_get, save_data

FS_ALL = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
STOCK_FIELDS = ("f12,f14,f2,f3,f5,f6,f8,f10,f15,f16,f17,f18,f20,f62,f184,"
                "f66,f69,f72,f75,f78,f81,f84,f87,f100,f124")

CSV_FIELDS = ["f12", "f14", "f2", "f3", "f184", "f62", "f66", "f10", "f8", "f20", "f100"]
CSV_HEADERS = ["代码", "名称", "最新价", "涨跌幅", "主力占比", "主力净流入", "超大单净流入",
               "量比", "换手率", "总市值", "行业"]


def fetch(limit=300, date_str=None):
    """获取主力占比排名（不限市场，正流入过滤）"""
    all_rows = []
    page = 1
    while len(all_rows) < limit:
        params = {
            "pn": str(page), "pz": "100", "po": "1",
            "np": "1", "fltt": "2", "invt": "2",
            "fid": "f184", "fs": FS_ALL, "fields": STOCK_FIELDS,
        }
        data = push2_get(params)
        if data.get("rc") != 0 or not data.get("data", {}).get("diff"):
            break
        for row in data["data"]["diff"]:
            f62 = row.get("f62")
            if isinstance(f62, (int, float)) and f62 > 0:
                all_rows.append(row)
            if len(all_rows) >= limit:
                break
        if page * 100 >= data["data"].get("total", 0):
            break
        page += 1
        time.sleep(0.2)

    print(f"  占比排名: {len(all_rows)} 只")
    save_data(all_rows, "rank_ratio", CSV_FIELDS, CSV_HEADERS, date_str)
    return all_rows
