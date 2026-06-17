"""
业绩预告 — 最新业绩变动
API: RPT_PUBLIC_OP_NEWPREDICT
决策信号: 预增/扭亏=利好, 预减/首亏=利空
"""
import time
from .base import datacenter_get, save_data, today_str, BJS_TZ
from datetime import datetime

REPORT_NAME = "RPT_PUBLIC_OP_NEWPREDICT"
CSV_FIELDS = ["SECURITY_CODE", "SECURITY_NAME_ABBR", "NOTICE_DATE",
              "PREDICT_FINANCE", "PREDICT_AMT_LOWER", "PREDICT_AMT_UPPER",
              "ADD_AMP_LOWER", "ADD_AMP_UPPER",
              "PREDICT_TYPE", "PREDICT_CONTENT"]
CSV_HEADERS = ["代码", "名称", "公告日期",
               "预测指标", "预测净利润下限", "预测净利润上限",
               "增长下限%", "增长上限%",
               "预告类型", "预告摘要"]


# 预告类型映射
TYPE_SCORE = {
    "预增": 1.0, "扭亏": 0.9, "略增": 0.7, "续盈": 0.5,
    "不确定": 0.5, "略减": 0.3, "预减": 0.1, "首亏": 0.0, "续亏": 0.0,
}


def fetch(date_str=None):
    """获取最新业绩预告"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    all_rows = []
    page = 1
    total_pages = None

    while True:
        try:
            data = datacenter_get(
                report_name=REPORT_NAME, columns="ALL",
                page=page, size=500,
                sort_cols="NOTICE_DATE", sort_types="-1",
            )
        except Exception as e:
            print(f"  业绩预告第 {page} 页失败: {e}")
            break

        if not data.get("success"):
            break

        result = data["result"]
        if total_pages is None:
            total_pages = result["pages"]
            print(f"  业绩预告: {result['count']} 条, {total_pages} 页")

        all_rows.extend(result["data"])

        if page >= total_pages:
            break
        page += 1
        time.sleep(0.1)

    # 统计预告类型分布
    from collections import Counter
    types = Counter(r.get("PREDICT_TYPE", "") for r in all_rows)
    good = sum(types.get(t, 0) for t in ["预增", "扭亏", "略增"])
    bad = sum(types.get(t, 0) for t in ["预减", "首亏", "续亏", "略减"])
    print(f"  业绩预告: {len(all_rows)} 条 (利好{good}条, 利空{bad}条)")

    save_data(all_rows, "earnings_forecast", CSV_FIELDS, CSV_HEADERS, date_str)
    return all_rows


def _tof(val):
    try: return float(val) if val else 0.0
    except: return 0.0

def transform(date_str):
    """聚合为选股用格式: {code: {type, type_score, growth_lower, growth_upper}}"""
    from .base import load_json

    rows = load_json(date_str, "earnings_forecast")
    if not rows:
        return {}

    result = {}
    for r in rows:
        code = r.get("SECURITY_CODE", "")
        if not code or code in result:
            continue
        pred_type = r.get("PREDICT_TYPE", "")
        result[code] = {
            "type": pred_type,
            "type_score": TYPE_SCORE.get(pred_type, 0.5),
            "growth_lower": _tof(r.get("ADD_AMP_LOWER")),
            "growth_upper": _tof(r.get("ADD_AMP_UPPER")),
            "notice_date": r.get("NOTICE_DATE", ""),
        }
    print(f"  业绩预告(选股): {len(result)} 只")
    return result
