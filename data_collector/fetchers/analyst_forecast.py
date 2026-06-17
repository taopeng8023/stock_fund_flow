"""
分析师盈利预测 + 评级 — 全市场
数据页面: https://data.eastmoney.com/report/stock.jshtml
"""
import sys
import time
from .base import datacenter_get, save_data

CSV_FIELDS = ["SECURITY_CODE", "SECURITY_NAME_ABBR", "RATING_ORG_NUM",
              "RATING_BUY_NUM", "RATING_ADD_NUM", "RATING_NEUTRAL_NUM",
              "RATING_REDUCE_NUM", "RATING_SALE_NUM",
              "EPS1", "EPS2", "EPS3", "EPS4",
              "YEAR_MARK1", "YEAR_MARK2", "YEAR_MARK3", "YEAR_MARK4",
              "DEC_AIMPRICEMAX", "DEC_AIMPRICEMIN", "INDUSTRY_BOARD"]
CSV_HEADERS = ["代码", "名称", "评级机构数", "买入数", "增持数", "中性数",
               "减持数", "卖出数",
               "EPS第1年", "EPS第2年", "EPS第3年", "EPS第4年",
               "Y1标记", "Y2标记", "Y3标记", "Y4标记",
               "目标价高", "目标价低", "行业"]


def fetch(date_str=None):
    """获取全市场分析师盈利预测 + 评级"""
    all_rows = []
    page = 1
    total_pages = None

    while True:
        try:
            data = datacenter_get(
                report_name="RPT_WEB_RESPREDICT",
                columns="ALL",
                page=page,
                size=500,
                sort_cols="SECURITY_CODE",
                sort_types="1",
            )
        except Exception as e:
            print(f"  分析师预测第 {page} 页请求失败: {e}", file=sys.stderr)
            break

        if not data.get("success"):
            print(f"  分析师预测API失败: {data.get('message', '')}")
            break

        result = data["result"]
        if total_pages is None:
            total_pages = result["pages"]
            print(f"  共 {result['count']} 条, {total_pages} 页")

        all_rows.extend(result["data"])

        if page % 20 == 0:
            print(f"    第 {page}/{total_pages} 页, {len(all_rows)} 条")
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.1)

    print(f"  分析师预测: {len(all_rows)} 条")
    save_data(all_rows, "analyst_forecast", CSV_FIELDS, CSV_HEADERS, date_str)
    return all_rows


def _tof(val):
    try: return float(val) if val else 0.0
    except: return 0.0

def transform(date_str):
    from .base import load_json
    rows = load_json(date_str, "analyst_forecast")
    if not rows: return {}
    result = {}
    for r in rows:
        code = r.get("SECURITY_CODE", "")
        org_num = r.get("RATING_ORG_NUM") or 0
        buy, add, neutral, reduce_, sale = [r.get(k) or 0 for k in ("RATING_BUY_NUM","RATING_ADD_NUM","RATING_NEUTRAL_NUM","RATING_REDUCE_NUM","RATING_SALE_NUM")]
        total = buy + add + neutral + reduce_ + sale
        consensus = (buy*1.0+add*0.75+neutral*0.25+reduce_*0.0+sale*(-0.5))/total if (org_num>=3 and total>0) else 0.5
        eps1, eps2 = r.get("EPS1") or 0, r.get("EPS2") or 0
        eps_growth = max(-0.5, min(2.0, (eps2-eps1)/abs(eps1))) if (eps1 and abs(eps1)>0.01) else 0.0
        result[code] = {"consensus": round(consensus,3), "eps_growth": round(eps_growth,3), "org_num": org_num}
    return result
