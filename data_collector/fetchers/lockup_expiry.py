"""
限售解禁 — 解禁压力预警
API: RPT_LIFT_STOCK
决策信号: 未来7日内有大额解禁=回避信号
"""
import time
from .base import datacenter_get, save_data, today_str, BJS_TZ
from datetime import datetime, timedelta

REPORT_NAME = "RPT_LIFT_STOCK"
CSV_FIELDS = ["SECURITY_CODE", "SECURITY_NAME_ABBR", "FREE_DATE",
              "FREE_SHARES", "ADD_LISTING_SHARES",
              "ADD_LISTSHARES_RATIO", "CLOSE_PRICE"]
CSV_HEADERS = ["代码", "名称", "解禁日期",
               "解禁股数", "新增上市股数",
               "解禁占比%", "最新收盘价"]


def fetch(date_str=None):
    """获取未来限售解禁数据"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    all_rows = []
    page = 1
    total_pages = None
    # 取未来90天解禁
    end_d = (datetime.strptime(date_str, "%Y%m%d") + timedelta(days=90)).strftime("%Y-%m-%d")

    filt = f'(FREE_DATE<=\'{end_d}\')'

    while True:
        try:
            data = datacenter_get(
                report_name=REPORT_NAME, columns="ALL",
                page=page, size=500,
                sort_cols="FREE_DATE", sort_types="1",
                filter_str=filt,
            )
        except Exception as e:
            print(f"  限售解禁第 {page} 页失败: {e}")
            break

        if not data.get("success"):
            break

        result = data["result"]
        if total_pages is None:
            total_pages = result["pages"]
            print(f"  限售解禁: 未来90日 {result['count']} 条, {total_pages} 页")

        all_rows.extend(result["data"])

        if page >= total_pages:
            break
        page += 1
        time.sleep(0.1)

    # 未来7日大额解禁预警
    today = datetime.strptime(date_str, "%Y%m%d")
    week_end = (today + timedelta(days=7)).strftime("%Y-%m-%d")
    week_now = today.strftime("%Y-%m-%d")
    big_unlock = sum(1 for r in all_rows
                     if r.get("FREE_DATE", "") <= week_end
                     and (r.get("ADD_LISTSHARES_RATIO") or 0) > 5)
    print(f"  限售解禁: {len(all_rows)} 条, 未来7日大额解禁(>5%){big_unlock}只")

    save_data(all_rows, "lockup_expiry", CSV_FIELDS, CSV_HEADERS, date_str)
    return all_rows


def _tof(val):
    try: return float(val) if val else 0.0
    except: return 0.0

def transform(date_str):
    """聚合为选股用格式: {code: {days, ratio, free_date}}"""
    from .base import load_json

    rows = load_json(date_str, "lockup_expiry")
    if not rows:
        return {}

    today = datetime.strptime(date_str, "%Y%m%d")
    result = {}

    for r in rows:
        code = r.get("SECURITY_CODE", "")
        if not code:
            continue
        free_date = r.get("FREE_DATE", "")
        if not free_date:
            continue
        try:
            fd = datetime.strptime(free_date[:10], "%Y-%m-%d")
        except ValueError:
            continue
        days = (fd - today).days
        ratio = _tof(r.get("ADD_LISTSHARES_RATIO"))

        if code not in result or days < result[code]["days"]:
            result[code] = {"days": days, "ratio": ratio, "free_date": free_date[:10]}

    print(f"  限售解禁(选股): {len(result)} 只")
    return result
