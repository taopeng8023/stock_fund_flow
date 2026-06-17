"""
个股资金流 — 全量采集（主力净流入额 f62 降序）
数据页面: https://data.eastmoney.com/zjlx/detail.html
"""
import time
from .base import push2_get, save_data

FS_ALL = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
STOCK_FIELDS = ("f12,f14,f2,f3,f5,f6,f8,f10,f15,f16,f17,f18,f20,f62,f184,"
                "f66,f69,f72,f75,f78,f81,f84,f87,f100,f124,"
                "f164,f165,f166,f167,f168,f169,f170,f171,f172,f173,f174,f175")

CSV_FIELDS = ["f12", "f14", "f2", "f3", "f62", "f184", "f66", "f69",
              "f10", "f8", "f20", "f17", "f18", "f100",
              "f164", "f165", "f168", "f169", "f170", "f174"]
CSV_HEADERS = ["代码", "名称", "最新价", "涨跌幅", "主力净流入", "主力占比",
               "超大单净流入", "超大单占比", "量比", "换手率", "总市值", "开盘价", "昨收", "行业",
               "融资买入额", "融资买入占比", "融资净买入", "融资净买入占比", "融券卖出额", "融券净卖出"]


def fetch(date_str=None):
    """获取全市场所有个股资金流数据"""
    all_rows = []
    total = None
    page = 1
    while True:
        params = {
            "pn": str(page), "pz": "100", "po": "1",
            "np": "1", "fltt": "2", "invt": "2",
            "fid": "f62", "fs": FS_ALL, "fields": STOCK_FIELDS,
        }
        data = push2_get(params)
        if data.get("rc") != 0 or not data.get("data", {}).get("diff"):
            break
        result = data["data"]
        if total is None:
            total = result.get("total", 0)
            print(f"  全市场 {total} 只，全量采集...")
        all_rows.extend(result["diff"])
        if page % 5 == 0:
            print(f"    第 {page} 页, {len(all_rows)} 只")
        if page * 100 >= total:
            break
        page += 1
        time.sleep(0.15)

    pos = sum(1 for r in all_rows if isinstance(r.get("f62"), (int, float)) and r["f62"] > 0)
    print(f"  全量: {len(all_rows)} 只 (其中正流入 {pos} 只, {page} 页)")

    ts = all_rows[0].get("f124") if all_rows else None
    if isinstance(ts, (int, float)):
        from datetime import datetime
        from .base import BJS_TZ
        print(f"  更新时间: {datetime.fromtimestamp(ts, tz=BJS_TZ).strftime('%Y-%m-%d %H:%M:%S')}")

    save_data(all_rows, "fund_flow", CSV_FIELDS, CSV_HEADERS, date_str)
    return all_rows
