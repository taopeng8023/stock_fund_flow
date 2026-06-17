"""
机构调研 — 机构密集调研预示后续建仓
API: RPT_ORG_SURVEYNEW
决策信号: 近30日调研次数/家数越多=机构关注度越高
"""
import time
from .base import datacenter_get, save_data, today_str, BJS_TZ
from datetime import datetime, timedelta

REPORT_NAME = "RPT_ORG_SURVEYNEW"
CSV_FIELDS = ["SECURITY_CODE", "SECURITY_NAME_ABBR", "NOTICE_DATE",
              "RECEIVE_START_DATE", "RECEIVE_PLACE", "RECEIVE_WAY_EXPLAIN",
              "RECEPTIONIST", "SUM"]
CSV_HEADERS = ["代码", "名称", "公告日期",
               "调研日期", "调研地点", "调研方式",
               "接待人员", "调研次数"]


def fetch(date_str=None):
    """获取近30日机构调研数据"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    # 取近30天
    d = datetime.strptime(date_str, "%Y%m%d")
    start = (d - timedelta(days=30)).strftime("%Y-%m-%d")

    all_rows = []
    page = 1
    total_pages = None
    filt = f'(NUMBERNEW="1")(IS_SOURCE="1")(RECEIVE_START_DATE>\'{start}\')'

    while True:
        try:
            data = datacenter_get(
                report_name=REPORT_NAME, columns="ALL",
                page=page, size=500,
                sort_cols="RECEIVE_START_DATE", sort_types="-1",
                filter_str=filt,
            )
        except Exception as e:
            print(f"  机构调研第 {page} 页失败: {e}")
            break

        if not data.get("success"):
            break

        result = data["result"]
        if total_pages is None:
            total_pages = result["pages"]
            print(f"  机构调研: 近30日 {result['count']} 条, {total_pages} 页")

        all_rows.extend(result["data"])

        if page >= total_pages:
            break
        page += 1
        time.sleep(0.1)

    # 按股票汇总调研次数
    from collections import Counter
    stock_counts = Counter(r.get("SECURITY_CODE") for r in all_rows)
    hot = sum(1 for c in stock_counts.values() if c >= 3)
    print(f"  机构调研: {len(all_rows)} 条, 覆盖{len(stock_counts)}只, 热门(>=3次){hot}只")

    save_data(all_rows, "org_research", CSV_FIELDS, CSV_HEADERS, date_str)
    return all_rows


def transform(date_str):
    """聚合为选股用格式: {code: research_count}"""
    from collections import Counter
    from .base import load_json

    rows = load_json(date_str, "org_research")
    if not rows:
        return {}
    counts = Counter(r.get("SECURITY_CODE") for r in rows if r.get("SECURITY_CODE"))
    print(f"  机构调研(选股): {len(counts)} 只")
    return dict(counts)
