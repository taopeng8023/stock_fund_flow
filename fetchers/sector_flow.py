"""
板块资金流 — 行业板块 + 概念板块（主力净流入额 f62 降序）
数据页面: https://data.eastmoney.com/zjlx/list.html
"""
import time
from .base import push2_get, save_data

SECTOR_FIELDS = "f12,f14,f2,f3,f62,f184,f66,f69,f124"
CSV_FIELDS = ["f12", "f14", "f62", "f184", "f66", "f69"]
CSV_HEADERS = ["代码", "名称", "主力净流入", "主力占比", "超大单净流入", "超大单占比"]

SECTORS = [
    ("m:90+t:2", "industry_flow", "行业板块"),
    ("m:90+t:3", "concept_flow", "概念板块"),
]


def fetch_sector(fs, label, date_str):
    all_rows = []
    total = None
    page = 1
    while True:
        params = {
            "pn": str(page), "pz": "100", "po": "1",
            "np": "1", "fltt": "2", "invt": "2",
            "fid": "f62", "fs": fs, "fields": SECTOR_FIELDS,
        }
        data = push2_get(params)
        if data.get("rc") != 0 or not data.get("data", {}).get("diff"):
            break
        result = data["data"]
        if total is None:
            total = result.get("total", 0)
        for row in result["diff"]:
            all_rows.append(row)
        if page * 100 >= total:
            break
        page += 1
        time.sleep(0.15)
    print(f"  {label}: {len(all_rows)} 个")
    return all_rows


def fetch(date_str=None):
    """获取行业板块 + 概念板块资金流"""
    results = {}
    for fs, filename, label in SECTORS:
        rows = fetch_sector(fs, label, date_str)
        if rows:
            save_data(rows, filename, CSV_FIELDS, CSV_HEADERS, date_str)
        results[filename] = rows
    return results
