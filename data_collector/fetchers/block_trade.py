"""
大宗交易每日明细 — 机构溢价/折价成交
API: RPT_DATA_BLOCKTRADE
决策信号: 溢价率>0=机构看多(不惜加价), 折价率<-8%=恐慌离场
"""
import time
from .base import datacenter_get, save_data, today_str, BJS_TZ
from datetime import datetime

REPORT_NAME = "RPT_DATA_BLOCKTRADE"
CSV_FIELDS = ["TRADE_DATE", "SECURITY_CODE", "SECURITY_NAME_ABBR",
              "DEAL_PRICE", "CLOSE_PRICE", "PREMIUM_RATIO",
              "DEAL_VOLUME", "DEAL_AMT",
              "BUYER_NAME", "SELLER_NAME",
              "CHANGE_RATE_1DAYS", "CHANGE_RATE_5DAYS", "CHANGE_RATE_10DAYS"]
CSV_HEADERS = ["交易日期", "代码", "名称",
               "成交价", "收盘价", "溢价率%",
               "成交量(股)", "成交额(元)",
               "买方", "卖方",
               "1日后涨跌", "5日后涨跌", "10日后涨跌"]


def fetch(date_str=None):
    """获取指定日期大宗交易明细"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")
    query_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    all_rows = []
    page = 1
    total_pages = None
    filt = f'(SECURITY_TYPE_WEB="1")(TRADE_DATE=\'{query_date}\')'

    while True:
        try:
            data = datacenter_get(
                report_name=REPORT_NAME, columns="ALL",
                page=page, size=500,
                sort_cols="DEAL_AMT", sort_types="-1",
                filter_str=filt,
            )
        except Exception as e:
            print(f"  大宗交易第 {page} 页失败: {e}")
            break

        if not data.get("success"):
            print(f"  大宗交易API失败: {data.get('message', '')}")
            break

        result = data["result"]
        if total_pages is None:
            total_pages = result["pages"]
            print(f"  大宗交易: {result['count']} 条, {total_pages} 页")

        all_rows.extend(result["data"])

        if page >= total_pages:
            break
        page += 1
        time.sleep(0.1)

    # 统计溢价/折价分布
    premium = sum(1 for r in all_rows if (r.get("PREMIUM_RATIO") or 0) > 0)
    discount = sum(1 for r in all_rows if (r.get("PREMIUM_RATIO") or 0) < 0)
    print(f"  大宗交易: {len(all_rows)} 笔 (溢价{premium}笔, 折价{discount}笔)")

    save_data(all_rows, "block_trade", CSV_FIELDS, CSV_HEADERS, date_str)
    return all_rows
