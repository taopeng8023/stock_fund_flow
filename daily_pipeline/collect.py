"""
步骤1: 盘中采集 — 每30分钟采集全市场个股资金流 + 行业板块资金流
保存到 research_data/<date>/intraday/ 目录，全部 CSV 格式
"""
import os
import csv
import urllib.request
import urllib.parse
import json
import time
from datetime import datetime, timezone, timedelta

BJS_TZ = timezone(timedelta(hours=8))
RESEARCH_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data"
)

PUSH2_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/zjlx/list.html",
}

# ── 个股资金流字段 ──
STOCK_FIELDS = (
    "f12,f14,f2,f3,f5,f6,f8,f10,f15,f16,f17,f18,f20,f62,f184,"
    "f66,f69,f72,f75,f78,f81,f84,f87,"
    "f164,f165,f166,f167,f168,f169,f170,f172,f174,f175,f100"
)
STOCK_HEADERS = [
    "代码", "名称", "最新价", "涨跌幅", "成交量", "成交额", "换手率", "量比",
    "最高", "最低", "开盘", "昨收", "总市值", "主力净流入", "主力占比",
    "超大单净流入", "超大单占比", "大单净流入", "大单占比",
    "中单净流入", "中单占比", "小单净流入", "小单占比",
    "5日主力净流入", "5日主力占比", "5日超大买入", "5日超大卖出",
    "融资净买入", "融资净买入占比", "融券净卖出", "融券卖出量",
    "10日主力净流入", "10日主力占比", "行业",
]
STOCK_FIELD_KEYS = STOCK_FIELDS.split(",")

# ── 行业板块字段 ──
SECTOR_FIELDS = "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f164,f174"
SECTOR_HEADERS = [
    "代码", "名称", "最新价", "涨跌幅", "主力净流入", "主力占比",
    "超大单净流入", "超大单占比", "大单净流入", "大单占比",
    "中单净流入", "中单占比", "小单净流入", "小单占比",
    "5日主力净流入", "10日主力净流入",
]
SECTOR_FIELD_KEYS = SECTOR_FIELDS.split(",")


def _tof(val):
    try: return float(val) if val else 0.0
    except: return 0.0


def _intraday_dir(date_str):
    d = os.path.join(RESEARCH_ROOT, date_str, "intraday")
    os.makedirs(d, exist_ok=True)
    return d


def fetch_stock_fund_flow():
    """全市场个股资金流（分页拉取，约5000+只）"""
    all_rows = []
    page = 1
    total = 0

    while True:
        params = {
            "pn": str(page), "pz": "100", "po": "1", "np": "1",
            "fltt": "2", "invt": "2",
            "fid": "f62", "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": STOCK_FIELDS,
        }
        url = PUSH2_URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            print(f"  个股第{page}页失败: {e}")
            break

        if not data.get("data") or not data["data"].get("diff"):
            break

        result = data["data"]
        if total == 0:
            total = result.get("total", 0)
            print(f"  全市场个股: {total} 只, {(total+99)//100} 页")

        all_rows.extend(result["diff"])

        if page % 10 == 0:
            print(f"    第 {page} 页, {len(all_rows)} 只")

        if page * 100 >= total:
            break
        page += 1
        time.sleep(0.1)

    print(f"  总计: {len(all_rows)} 只")
    return all_rows


def fetch_sector_flow(fs="m:90+s:4"):
    """行业/概念板块资金流"""
    params = {
        "pn": "1", "pz": "200", "po": "1", "np": "1",
        "fltt": "2", "invt": "2",
        "fid": "f62", "fs": fs,
        "fields": SECTOR_FIELDS,
    }
    url = PUSH2_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        if data.get("data") and data["data"].get("diff"):
            return data["data"]["diff"]
    except Exception as e:
        print(f"  板块流失败: {e}")
    return []


def collect_intraday_snapshot(date_str=None):
    """采集当前时刻快照，保存 CSV"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")
    ts = datetime.now(BJS_TZ).strftime("%H%M%S")
    out_dir = _intraday_dir(date_str)

    print(f"[{ts}] 采集盘中快照...")

    # 1. 个股资金流
    stocks = fetch_stock_fund_flow()
    if stocks:
        csv_path = os.path.join(out_dir, f"fund_flow_{ts}.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(STOCK_HEADERS)
            for row in stocks:
                w.writerow([row.get(k, "") for k in STOCK_FIELD_KEYS])
        print(f"  ✓ fund_flow_{ts}.csv ({len(stocks)} 只)")

    # 2. 行业板块流
    industry = fetch_sector_flow("m:90+s:4")
    if industry:
        csv_path = os.path.join(out_dir, f"industry_flow_{ts}.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(SECTOR_HEADERS)
            for row in industry:
                w.writerow([row.get(k, "") for k in SECTOR_FIELD_KEYS])
        print(f"  ✓ industry_flow_{ts}.csv ({len(industry)} 个行业)")

    # 3. 概念板块流
    concept = fetch_sector_flow("m:90+t:3")
    if concept:
        csv_path = os.path.join(out_dir, f"concept_flow_{ts}.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(SECTOR_HEADERS)
            for row in concept:
                w.writerow([row.get(k, "") for k in SECTOR_FIELD_KEYS])
        print(f"  ✓ concept_flow_{ts}.csv ({len(concept)} 个概念)")

    return {"stocks": len(stocks), "industry": len(industry), "concept": len(concept)}


if __name__ == "__main__":
    collect_intraday_snapshot()
