"""
个股资金流净流入正向排名 Top 100
数据来源: 东方财富 https://data.eastmoney.com/zjlx/detail.html

用法:
  python main.py          默认展示 Top 100（主力净流入降序）
  python main.py --csv    同时导出 CSV
"""
import urllib.request
import urllib.parse
import json
import sys
import time
from datetime import datetime, timezone, timedelta

FIELDS = ("f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,"
          "f64,f65,f70,f71,f76,f77,f82,f83,f124")
FIELD_NAMES = {
    "f12": "代码", "f14": "名称", "f2": "最新价", "f3": "涨跌幅(%)",
    "f62": "主力净流入", "f184": "主力净流入占比(%)",
    "f66": "超大单净流入", "f69": "超大单净流入占比(%)",
    "f72": "大单净流入", "f75": "大单净流入占比(%)",
    "f78": "中单净流入", "f81": "中单净流入占比(%)",
    "f84": "小单净流入", "f87": "小单净流入占比(%)",
}

BASE_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/zjlx/detail.html",
}
FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"


def api_get(params, retries=3):
    """发送 API 请求"""
    url = BASE_URL + "?" + urllib.parse.urlencode(params)
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


def fetch_top100():
    """获取个股资金流正向排名 Top 100（f62 降序）"""
    params = {
        "pn": "1", "pz": "100", "po": "1",
        "np": "1", "fltt": "2", "invt": "2",
        "fid": "f62",
        "fs": FS,
        "fields": FIELDS,
    }
    data = api_get(params)
    if data.get("rc") != 0 or not data.get("data", {}).get("diff"):
        return None, None

    return data["data"]["diff"], data["data"].get("total", 0)


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


def print_table(rows):
    headers = ["排名", "代码", "名称", "最新价", "涨跌幅(%)", "主力净流入", "主力占比(%)"]
    widths = [5, 8, 10, 8, 10, 14, 10]
    line = "".join(h.ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("-" * sum(widths))
    for i, row in enumerate(rows):
        code = row.get("f12", "-")
        name = row.get("f14", "-")
        price = row.get("f2", "-")
        pct = row.get("f3")
        chg = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "-"
        flow = format_amount(row.get("f62"))
        r184 = row.get("f184")
        ratio = f"{r184:.2f}%" if isinstance(r184, (int, float)) else "-"
        cols = [str(i + 1), code, name, str(price), chg, flow, ratio]
        print("".join(c.ljust(w) for c, w in zip(cols, widths)))


def get_update_time(rows):
    ts = rows[0].get("f124") if rows else None
    if isinstance(ts, (int, float)):
        tz = timezone(timedelta(hours=8))
        return datetime.fromtimestamp(ts, tz=tz).strftime("%Y-%m-%d %H:%M:%S")
    return "-"


def save_csv(rows, filename="fund_flow_top100.csv"):
    import csv
    keys = [k for k in FIELD_NAMES if k != "f87"]
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([FIELD_NAMES[k] for k in keys])
        for row in rows:
            writer.writerow([row.get(k, "-") for k in keys])
    print(f"\n已保存至 {filename}")


def main():
    print("正在获取个股资金流正向排名 Top 100 (主力净流入降序)...\n")
    rows, total = fetch_top100()

    if not rows:
        print("无数据返回，API 可能暂时不可用")
        sys.exit(1)

    update_time = get_update_time(rows)
    print(f"数据更新时间: {update_time}")
    print(f"全市场 {total} 只个股\n")
    print_table(rows)

    if "--csv" in sys.argv:
        save_csv(rows)


if __name__ == "__main__":
    main()
