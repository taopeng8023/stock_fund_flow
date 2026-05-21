"""
Market sentiment & index data fetcher
Fetches major index quotes + aggregates retail sentiment from fund_flow data
"""
import json
import os
from datetime import datetime
from .base import push2_get, get_date_dir, DATA_ROOT, BJS_TZ, HEADERS
import urllib.request
import urllib.parse


# Major A-share indices
INDICES = {
    "sh": "1.000001",   # 上证指数
    "sz": "0.399001",   # 深证成指
    "cy": "0.399006",   # 创业板指
    "kc50": "1.000688", # 科创50
    "hs300": "1.000300", # 沪深300
    "zz500": "1.000905", # 中证500
    "zz1000": "1.000852", # 中证1000
}

INDEX_NAMES = {
    "sh": "上证指数", "sz": "深证成指", "cy": "创业板指",
    "kc50": "科创50", "hs300": "沪深300", "zz500": "中证500", "zz1000": "中证1000",
}


def fetch_indices():
    """Fetch real-time index quotes from East Money (with retries + fallback)"""
    import time
    codes = ",".join(INDICES.values())

    # Try push2 API first, then push2delay fallback
    urls = [
        f"https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&fields=f2,f3,f4,f5,f6,f12,f14&secids={codes}",
        f"https://push2delay.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&fields=f2,f3,f4,f5,f6,f12,f14&secids={codes}",
    ]

    for url in urls:
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                if data and data.get("data", {}).get("diff"):
                    break
            except Exception as e:
                if attempt < 2:
                    time.sleep(1)
                    continue
                data = None
        if data and data.get("data", {}).get("diff"):
            break

    if not data or not data.get("data", {}).get("diff"):
        print(f"  [sentiment] 指数数据获取失败，使用默认值")
        return None

    result = {}
    rows = data.get("data", {}).get("diff", [])
    for r in rows:
        code = r.get("f12", "")
        for key, ec in INDICES.items():
            if ec.split(".")[-1] == code:
                result[key] = {
                    "name": r.get("f14", INDEX_NAMES.get(key, "")),
                    "price": r.get("f2", 0),
                    "chg_pct": r.get("f3", 0),
                    "chg_amt": r.get("f4", 0),
                    "volume": r.get("f5", 0),
                    "turnover": r.get("f6", 0),
                }
                break
    return result


def compute_sentiment(fund_flow_rows, north_data=None, index_data=None):
    """
    Compute composite market sentiment from fund_flow data.
    Returns a 0-100 sentiment score + component breakdowns.
    """
    if not fund_flow_rows:
        return None

    n = len(fund_flow_rows)

    # --- Component 1: Breadth sentiment (0-25) ---
    chgs = []
    for r in fund_flow_rows:
        f3 = r.get("f3")
        if isinstance(f3, (int, float)):
            chgs.append(f3)

    up_ratio = sum(1 for c in chgs if c > 0) / len(chgs) if chgs else 0
    limit_up = sum(1 for c in chgs if c >= 9.5)
    limit_down = sum(1 for c in chgs if c <= -9.5)
    median_ret = sorted(chgs)[len(chgs) // 2] if chgs else 0

    # Breadth score: up_ratio based, 0-25
    breadth_score = min(25, max(0, up_ratio * 25 + (median_ret / 2)))

    # --- Component 2: Fund flow sentiment (0-25) ---
    main_flows = []
    main_ratios = []
    for r in fund_flow_rows:
        f62 = r.get("f62")
        if isinstance(f62, (int, float)):
            main_flows.append(f62)
        f184 = r.get("f184")
        if isinstance(f184, (int, float)):
            main_ratios.append(f184)

    pos_flow_ratio = sum(1 for f in main_flows if f > 0) / len(main_flows) if main_flows else 0
    total_main = sum(main_flows) if main_flows else 0
    avg_main_ratio = sum(main_ratios) / len(main_ratios) if main_ratios else 0

    # Flow score: positive flow ratio + intensity, 0-25
    flow_score = min(25, max(0, pos_flow_ratio * 20 + (avg_main_ratio / 2)))

    # --- Component 3: Volume / Activity (0-20) ---
    vol_ratios = []
    turnovers = []
    for r in fund_flow_rows:
        f10 = r.get("f10")
        if isinstance(f10, (int, float)) and f10 > 0:
            vol_ratios.append(f10)
        f8 = r.get("f8")
        if isinstance(f8, (int, float)):
            turnovers.append(f8)

    avg_vol = sum(vol_ratios) / len(vol_ratios) if vol_ratios else 0
    avg_turnover = sum(turnovers) / len(turnovers) if turnovers else 0
    high_vol_pct = sum(1 for v in vol_ratios if v > 2) / len(vol_ratios) if vol_ratios else 0

    # Volume score: moderate activity is good, 0-20
    vol_score = min(20, max(0, avg_vol * 4 + high_vol_pct * 10))

    # --- Component 4: Margin sentiment (0-15) ---
    margin_nets = []
    for r in fund_flow_rows:
        f168 = r.get("f168")
        if isinstance(f168, (int, float)):
            margin_nets.append(f168)

    margin_pos_ratio = sum(1 for m in margin_nets if m > 0) / len(margin_nets) if margin_nets else 0
    total_margin = sum(margin_nets) if margin_nets else 0

    # Margin score: positive margin ratio, 0-15
    margin_score = min(15, max(0, margin_pos_ratio * 15))

    # --- Component 5: Index trend (0-15) ---
    index_score = 7.5  # neutral default
    if index_data:
        idx_chgs = []
        for key in ["sh", "sz", "cy"]:
            if key in index_data:
                idx_chgs.append(index_data[key].get("chg_pct", 0))
        if idx_chgs:
            avg_idx_chg = sum(idx_chgs) / len(idx_chgs)
            # Map index change to 0-15: -3% → 0, 0% → 7.5, +3% → 15
            index_score = min(15, max(0, (avg_idx_chg + 3) * 2.5))

    # --- Composite ---
    total_score = breadth_score + flow_score + vol_score + margin_score + index_score
    total_score = min(100, max(0, round(total_score, 1)))

    # Sentiment label
    if total_score >= 75:
        label = "极度亢奋"
        level = "greedy"
    elif total_score >= 60:
        label = "偏乐观"
        level = "optimistic"
    elif total_score >= 40:
        label = "中性"
        level = "neutral"
    elif total_score >= 25:
        label = "偏悲观"
        level = "pessimistic"
    else:
        label = "极度恐慌"
        level = "fearful"

    return {
        "score": total_score,
        "label": label,
        "level": level,
        "components": {
            "breadth": round(breadth_score, 1),
            "fund_flow": round(flow_score, 1),
            "volume": round(vol_score, 1),
            "margin": round(margin_score, 1),
            "index": round(index_score, 1),
        },
        "detail": {
            "up_ratio": round(up_ratio, 3),
            "median_ret": round(median_ret, 2),
            "limit_up": limit_up,
            "limit_down": limit_down,
            "pos_flow_ratio": round(pos_flow_ratio, 3),
            "total_main_flow_yi": round(total_main / 1e8, 2),
            "avg_main_ratio": round(avg_main_ratio, 2),
            "avg_vol_ratio": round(avg_vol, 2),
            "avg_turnover": round(avg_turnover, 2),
            "high_vol_pct": round(high_vol_pct, 3),
            "margin_pos_ratio": round(margin_pos_ratio, 3),
            "total_margin_yi": round(total_margin / 1e8, 2),
            "stock_count": n,
        },
        "indices": index_data,
    }


def fetch_and_compute(date_str=None):
    """Fetch index data + compute sentiment for a given date"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    # Load fund_flow data
    fund_path = os.path.join(DATA_ROOT, date_str, "fund_flow.json")
    if not os.path.exists(fund_path):
        print(f"  [sentiment] fund_flow.json 不存在于 {date_str}")
        return None

    with open(fund_path, "r", encoding="utf-8") as f:
        rows = json.load(f)

    # Fetch index data
    indices = fetch_indices()
    if indices:
        print(f"  [sentiment] 指数: 上证 {indices.get('sh',{}).get('price','?')} "
              f"({indices.get('sh',{}).get('chg_pct',0):+.2f}%) | "
              f"深证 {indices.get('sz',{}).get('chg_pct',0):+.2f}% | "
              f"创业板 {indices.get('cy',{}).get('chg_pct',0):+.2f}%")

    # Compute sentiment
    return compute_sentiment(rows, index_data=indices)


if __name__ == "__main__":
    result = fetch_and_compute()
    if result:
        print(f"\n情绪温度计: {result['score']}/100 [{result['label']}]")
        print(f"分项: {result['components']}")
        print(f"涨停: {result['detail']['limit_up']} | 跌停: {result['detail']['limit_down']}")
        print(f"主力流入>0占比: {result['detail']['pos_flow_ratio']:.1%}")
