"""
数据采集共享模块 — API 请求 + 数据存储 + 通用工具
"""
import urllib.request
import urllib.parse
import json
import csv
import os
import time
from datetime import datetime, timezone, timedelta

# ============================================================
# 配置
# ============================================================
PUSH2_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/report/stock.jshtml",
}

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_ROOT = os.path.join(PROJECT_DIR, "data")
BJS_TZ = timezone(timedelta(hours=8))


def get_date_dir(date_str=None):
    """获取日期对应的数据子目录，自动创建"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")
    d = os.path.join(DATA_ROOT, date_str)
    os.makedirs(d, exist_ok=True)
    return d


def today_str():
    return datetime.now(BJS_TZ).strftime("%Y%m%d")


# ============================================================
# API 请求
# ============================================================
def push2_get(params, retries=3):
    """push2delay API 通用请求"""
    url = PUSH2_URL + "?" + urllib.parse.urlencode(params)
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            last_err = e
            if i < retries - 1:
                time.sleep(2)
    raise last_err


def datacenter_get(report_name, columns="ALL", page=1, size=100,
                   sort_cols=None, sort_types=None, filter_str=None):
    """datacenter API 通用请求"""
    params = {
        "reportName": report_name,
        "columns": columns,
        "pageNumber": str(page),
        "pageSize": str(size),
        "source": "WEB",
        "client": "WEB",
    }
    if sort_cols:
        params["sortColumns"] = sort_cols
    if sort_types:
        params["sortTypes"] = sort_types
    if filter_str:
        params["filter"] = filter_str
    url = DATACENTER_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


# ============================================================
# 数据存储
# ============================================================
def save_data(rows, filename_base, csv_fields, csv_headers, date_str=None):
    """保存 JSON + CSV 双格式到日期目录"""
    date_dir = get_date_dir(date_str)

    json_path = os.path.join(date_dir, f"{filename_base}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    csv_path = os.path.join(date_dir, f"{filename_base}.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(csv_headers)
        for row in rows:
            writer.writerow([row.get(k, "") for k in csv_fields])

    print(f"  {filename_base}.json / .csv ({len(rows)} 条)")


def load_json(date_str, filename_base):
    """从日期目录加载 JSON 数据"""
    path = os.path.join(DATA_ROOT, date_str, f"{filename_base}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def format_amount(value):
    if value is None or value == "-":
        return "-"
    v = float(value)
    if abs(v) >= 1e8:
        return f"{v / 1e8:+.2f}亿"
    elif abs(v) >= 1e4:
        return f"{v / 1e4:+.2f}万"
    else:
        return f"{v:+.2f}"
