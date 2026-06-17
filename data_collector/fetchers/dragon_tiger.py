"""
龙虎榜 — 当日上榜个股明细（机构/游资/散户买卖拆解）
数据页面: https://data.eastmoney.com/stock/tradedetail.html

字段说明:
  TRADE_DATE         交易日期
  SECURITY_CODE      股票代码
  SECURITY_NAME_ABBR 股票名称
  CLOSE_PRICE        收盘价
  CHANGE_RATE        涨跌幅(%)
  TURNOVERRATE       换手率(%)
  BILLBOARD_DEAL_AMT 龙虎榜成交额(万)
  DEAL_AMOUNT_RATIO  成交额占比(总成交)
  FREE_MARKET_CAP    流通市值
  EXPLAIN            解读（买卖席位分析）
  D1/D2/D5/D10_CLOSE_ADJCHRATE  上榜后1/2/5/10日涨跌幅
"""
from datetime import datetime, timezone, timedelta
from .base import datacenter_get, save_data, BJS_TZ

CSV_FIELDS = ["TRADE_DATE", "SECURITY_CODE", "SECURITY_NAME_ABBR",
              "CLOSE_PRICE", "CHANGE_RATE", "TURNOVERRATE",
              "BILLBOARD_DEAL_AMT", "DEAL_AMOUNT_RATIO",
              "FREE_MARKET_CAP", "EXPLAIN",
              "D1_CLOSE_ADJCHRATE", "D2_CLOSE_ADJCHRATE",
              "D5_CLOSE_ADJCHRATE", "D10_CLOSE_ADJCHRATE"]
CSV_HEADERS = ["交易日期", "代码", "名称",
               "收盘价", "涨跌幅", "换手率",
               "龙虎榜成交额(万)", "成交占比(总成交)",
               "流通市值", "解读",
               "上榜后1日涨跌", "上榜后2日涨跌",
               "上榜后5日涨跌", "上榜后10日涨跌"]


def fetch(date_str=None):
    """获取指定日期的龙虎榜数据"""
    if date_str is None:
        d = datetime.now(BJS_TZ) - timedelta(days=1)
        date_str = d.strftime("%Y%m%d")
        query_date = d.strftime("%Y-%m-%d")
    else:
        query_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    print(f"  龙虎榜日期: {query_date}")

    all_rows = []
    page = 1
    total_pages = None

    while True:
        filter_str = f"(TRADE_DATE='{query_date}')"
        try:
            data = datacenter_get(
                report_name="RPT_DAILYBILLBOARD_DETAILSNEW",
                columns="ALL",
                page=page,
                size=100,
                filter_str=filter_str,
            )
        except Exception as e:
            print(f"  龙虎榜第 {page} 页请求失败: {e}")
            break

        if not data.get("success"):
            print(f"  龙虎榜API失败: {data.get('message', '')}")
            break

        result = data["result"]
        if total_pages is None:
            total_pages = result.get("pages", 1)
            print(f"  共 {result.get('count', 0)} 条, {total_pages} 页")

        if result.get("data"):
            all_rows.extend(result["data"])
        else:
            break

        if page >= total_pages:
            break
        page += 1

    print(f"  龙虎榜: {len(all_rows)} 条")
    if all_rows:
        save_data(all_rows, "dragon_tiger", CSV_FIELDS, CSV_HEADERS, date_str)
    return all_rows


def transform(date_str):
    from .base import load_json
    rows = load_json(date_str, "dragon_tiger")
    if not rows: return {}
    from datetime import timedelta
    d = __import__('datetime').datetime.strptime(date_str, "%Y%m%d")
    if rows is None:
        prev = d - timedelta(days=1)
        for _ in range(3):
            prev_str = prev.strftime("%Y%m%d")
            rows = load_json(prev_str, "dragon_tiger")
            if rows is not None: break
            prev -= timedelta(days=1)
    if not rows: return {}
    return {r.get("SECURITY_CODE",""): {"on_board":True, "has_institution":"机构" in str(r.get("EXPLAIN","")), "is_main_buy":"主买" in str(r.get("EXPLAIN",""))} for r in rows}
