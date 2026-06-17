"""
北向资金 — 沪深港通市场层面资金流向
数据页面: https://data.eastmoney.com/hsgt/index.html

API: push2delay kamt.kline (沪深港通资金流向 k 线)
字段: 日期, 北向净流入(亿), 南向净流入(亿), 北向累计(亿), 南向累计(亿)

注: 盘中数据会被 mask 为 0，盘后(15:00+）可获取真实值
"""
import json
import urllib.request
from datetime import datetime
from .base import HEADERS, save_data, BJS_TZ

# CSV 输出字段
CSV_FIELDS = ["date", "net_north", "net_south", "balance", "balance_hk"]
CSV_HEADERS = ["日期", "北向净流入(亿)", "南向净流入(亿)", "北向累计(亿)", "南向累计(亿)"]


def fetch(date_str=None):
    """获取最近 N 个交易日北向资金流向"""
    url = ("https://push2delay.eastmoney.com/api/qt/kamt.kline/get"
           "?fields1=f1,f2,f3,f4"
           "&fields2=f51,f52,f53,f54,f55,f56"
           "&klt=101&lmt=30")

    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())

    if data.get("rc") != 0 or not data.get("data"):
        print(f"  北向资金API失败: rc={data.get('rc')}")
        return []

    rows = []
    # 合并沪深: hk2sh + hk2sz = 北向, sh2hk + sz2hk = 南向
    raw = data["data"]
    for key in ["hk2sh", "hk2sz", "sh2hk", "sz2hk"]:
        if key not in raw or not raw[key]:
            continue

    # 取沪股通的 k 线（含北向南向），按天汇总
    klines = raw.get("hk2sh", []) or []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 4:
            continue
        # f51=日期, f52=?, f53=北向(万), f54=南向(万), f55=北向累计(万), f56=南向累计(万)
        date = parts[0]
        net_north_sh = _parse_float(parts[2])
        net_south_sh = _parse_float(parts[3])
        balance_sh = _parse_float(parts[4]) if len(parts) > 4 else 0

    # 沪深分别有数据，需要合并
    all_rows = []
    for direction in ["north", "south"]:
        pass  # 下面统一处理

    # 分别取沪深数据，按天合并
    hk2sh_lines = raw.get("hk2sh", []) or []
    hk2sz_lines = raw.get("hk2sz", []) or []
    sh2hk_lines = raw.get("sh2hk", []) or []
    sz2hk_lines = raw.get("sz2hk", []) or []

    # 取最大行数
    all_klines = []
    for i in range(max(len(hk2sh_lines), len(hk2sz_lines))):
        row = {"date": "", "net_north": 0.0, "net_south": 0.0,
               "balance": 0.0, "balance_hk": 0.0}

        if i < len(hk2sh_lines):
            p = hk2sh_lines[i].split(",")
            row["date"] = p[0]
            row["net_north"] += _parse_float(p[2]) / 1e4  # 万→亿
            row["net_south"] += _parse_float(p[3]) / 1e4
            if len(p) > 4:
                row["balance"] += _parse_float(p[4]) / 1e4

        if i < len(hk2sz_lines):
            p = hk2sz_lines[i].split(",")
            if not row["date"]:
                row["date"] = p[0]
            row["net_north"] += _parse_float(p[2]) / 1e4
            if len(p) > 4:
                row["balance"] += _parse_float(p[4]) / 1e4

        # 南向
        if i < len(sh2hk_lines):
            p = sh2hk_lines[i].split(",")
            row["net_south"] += _parse_float(p[3]) / 1e4

        if i < len(sz2hk_lines):
            p = sz2hk_lines[i].split(",")
            row["net_south"] += _parse_float(p[3]) / 1e4

        row["net_north"] = round(row["net_north"], 2)
        row["net_south"] = round(row["net_south"], 2)
        row["balance"] = round(row["balance"], 2)
        all_klines.append(row)

    print(f"  北向资金: {len(all_klines)} 个交易日")

    # 不按天存储，始终覆盖最新文件
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")
    save_data(all_klines, "north_flow", CSV_FIELDS, CSV_HEADERS, date_str)
    return all_klines


def transform(date_str):
    from .base import load_json
    rows = load_json(date_str, "north_flow")
    if not rows: return {"net_north": 0, "masked": True}
    latest = rows[0]
    net_north = latest.get("net_north", 0)
    masked = abs(net_north) > 500 or (net_north == 0 and latest.get("net_south", 0) == 0)
    return {"net_north": net_north, "masked": masked}


def _parse_float(s):
    try:
        return float(s) if s else 0.0
    except (ValueError, TypeError):
        return 0.0
