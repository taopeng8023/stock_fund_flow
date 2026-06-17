"""板块数据加载 — Top N 行业板块 + 成分股"""
import csv
import glob
import os
from data_collector.fetchers.base import DATA_ROOT, load_json
from sector_screener.config import to_float


def load_sector_top_codes(date_str, top_n=5):
    """从 industry_flow_*.csv 获取主力净流入 Top N 板块代码"""
    patterns = [
        os.path.join(DATA_ROOT, date_str, "industry_flow_*.csv"),
        os.path.join(DATA_ROOT, date_str, "industry_flow.csv"),
    ]
    all_matches = []
    for pat in patterns:
        all_matches.extend(glob.glob(pat))
    csv_path = sorted(all_matches, key=os.path.getmtime, reverse=True)[0] if all_matches else ""
    codes = []
    if csv_path and os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        sorted_rows = sorted(rows, key=lambda r: to_float(r.get("主力净流入")), reverse=True)
        for r in sorted_rows[:top_n]:
            code = r.get("代码", "")
            codes.append(code)
            flow_yi = to_float(r.get("主力净流入")) / 1e8
            print(f"  Top板块: {code} {r.get('名称','')} 主力{flow_yi:+.2f}亿")
    else:
        print(f"  ✗ 未找到行业板块CSV: data/{date_str}/industry_flow_*.csv")
    return codes


def _load_sector_names(date_str):
    """BK代码→板块名称 映射"""
    patterns = [
        os.path.join(DATA_ROOT, date_str, "industry_flow_*.csv"),
        os.path.join(DATA_ROOT, date_str, "industry_flow.csv"),
    ]
    all_matches = []
    for pat in patterns:
        all_matches.extend(glob.glob(pat))
    csv_path = sorted(all_matches, key=os.path.getmtime, reverse=True)[0] if all_matches else ""
    if csv_path and os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            return {r["代码"]: r["名称"] for r in csv.DictReader(f)}
    return {}


def load_sector_stocks(sector_codes, date_str=None):
    """实时从东方财富 API 拉取板块成分股 + 5日/10日排名"""
    from data_collector.fetchers.sector_flow import fetch_sector_stocks
    all_stocks = []
    seen = set()
    sector_names = _load_sector_names(date_str) if date_str else {}

    for code in sector_codes:
        print(f"  实时拉取 {code} 成分股...", end=" ", flush=True)
        stocks = fetch_sector_stocks(code, "", None)
        if not stocks:
            print("无数据")
            continue
        for s in stocks:
            stock_code = s.get("f12", "")
            if stock_code and stock_code not in seen:
                seen.add(stock_code)
                s["_sector_code"] = code
                s["_sector_name"] = sector_names.get(code, code)
                all_stocks.append(s)
        print(f"{len(stocks)} 只")
    print(f"  去重后共计 {len(all_stocks)} 只个股（{len(sector_codes)} 个板块）")
    return all_stocks


def load_sector_multiday(date_str):
    """行业板块今日/5日/10日排名 → 板块新鲜度 + 持续性"""
    patterns = [
        os.path.join(DATA_ROOT, date_str, "industry_flow_*.csv"),
        os.path.join(DATA_ROOT, date_str, "industry_flow.csv"),
    ]
    all_matches = []
    for pat in patterns:
        all_matches.extend(glob.glob(pat))
    csv_path = sorted(all_matches, key=os.path.getmtime, reverse=True)[0] if all_matches else ""
    if not csv_path:
        return {}, {}, {}
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {}, {}, {}

    by_f62 = sorted(rows, key=lambda r: to_float(r.get("主力净流入")), reverse=True)
    by_5d = sorted(rows, key=lambda r: to_float(r.get("5日主力净流入")), reverse=True)
    by_10d = sorted(rows, key=lambda r: to_float(r.get("10日主力净流入")), reverse=True)

    rank_today = {r.get("代码"): i + 1 for i, r in enumerate(by_f62)}
    rank_5d = {r.get("代码"): i + 1 for i, r in enumerate(by_5d)}
    rank_10d = {r.get("代码"): i + 1 for i, r in enumerate(by_10d)}
    total = len(rows)

    sector_freshness = {}
    sector_persistence = {}
    for r in rows:
        code = r.get("代码", "")
        r_today = rank_today.get(code, total)
        r_5d = rank_5d.get(code, total)
        r_10d = rank_10d.get(code, total)
        jump_5d = (r_5d - r_today) / total
        jump_10d = (r_10d - r_today) / total
        freshness = max(0.0, min(1.0, (jump_5d * 0.6 + jump_10d * 0.4 + 0.3)))
        sector_freshness[code] = round(freshness, 3)
        persistence = 1.0 if r_5d <= total * 0.3 else (0.7 if r_5d <= total * 0.5 else 0.4)
        sector_persistence[code] = persistence

    return sector_freshness, rank_today, sector_persistence
