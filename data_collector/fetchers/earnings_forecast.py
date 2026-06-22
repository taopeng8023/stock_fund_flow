"""
业绩预告 — 最新业绩变动（增量采集 + 历史合并）
API: RPT_PUBLIC_OP_NEWPREDICT
决策信号: 预增/扭亏=利好, 预减/首亏=利空
"""
import os
import time
from .base import datacenter_get, save_data, today_str, BJS_TZ, load_json, DATA_ROOT
from datetime import datetime, timedelta

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

# 增量采集参数
_LOOKBACK_DAYS = 30                             # 从最新公告日往前回溯天数
_MERGE_KEY_FIELDS = ["SECUCODE", "NOTICE_DATE", "PREDICT_FINANCE_CODE", "REPORT_DATE"]


def _find_previous_date(current_date_str):
    """遍历 data/ 目录找最近一个有 earnings_forecast.json 的日期（倒序，跳过当前及未来）"""
    if not os.path.isdir(DATA_ROOT):
        return None
    date_dirs = sorted(
        [d for d in os.listdir(DATA_ROOT)
         if os.path.isdir(os.path.join(DATA_ROOT, d))
         and len(d) == 8 and d.isdigit()],
        reverse=True,
    )
    for d in date_dirs:
        if d >= current_date_str:
            continue
        if os.path.exists(os.path.join(DATA_ROOT, d, "earnings_forecast.json")):
            return d
    return None


def _get_latest_notice_date(records):
    """返回记录列表中最新的 NOTICE_DATE（'YYYY-MM-DD'），无则返回 None"""
    dates = [r.get("NOTICE_DATE", "")[:10] for r in records if r.get("NOTICE_DATE")]
    return max(dates) if dates else None


def _make_merge_key(record):
    """去重叠复合 key：同一股票 + 同一公告日 + 同预测指标 = 同一条预告"""
    return tuple(str(record.get(k, "")) for k in _MERGE_KEY_FIELDS)


def fetch(date_str=None, force_full=False):
    """获取最新业绩预告（增量模式：仅拉取新公告，合并历史数据）

    Args:
        date_str: 目标日期 YYYYMMDD，默认今天
        force_full: True 时强制全量拉取，忽略历史数据
    """
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    # ── Step 1: 加载前次累积数据 ──
    prev_date = None if force_full else _find_previous_date(date_str)
    prev_data = None
    if prev_date:
        prev_data = load_json(prev_date, "earnings_forecast")
        if prev_data:
            print(f"  增量模式: 加载历史数据 {prev_date} ({len(prev_data)} 条)")
        else:
            print(f"  历史文件 {prev_date} 为空, 切换为全量模式")

    # ── Step 2: 确定 API filter（从最新公告日前溯 N 天） ──
    filter_str = None
    if prev_data:
        latest = _get_latest_notice_date(prev_data)
        if latest:
            start = datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=_LOOKBACK_DAYS)
            start_date = start.strftime("%Y-%m-%d")
            filter_str = f"(NOTICE_DATE>='{start_date}')"
            print(f"  增量拉取: NOTICE_DATE >= {start_date} "
                  f"(最新公告 {latest}, 回溯{_LOOKBACK_DAYS}天)")
    if filter_str is None:
        print("  全量模式: 无历史数据或强制全量")

    # ── Step 3: 分页拉取 API ──
    new_rows = []
    page = 1
    total_pages = None

    while True:
        try:
            data = datacenter_get(
                report_name=REPORT_NAME, columns="ALL",
                page=page, size=500,
                sort_cols="NOTICE_DATE", sort_types="-1",
                filter_str=filter_str,
            )
        except Exception as e:
            print(f"  业绩预告第 {page} 页失败: {e}")
            break

        if not data.get("success"):
            print(f"  业绩预告API失败: {data.get('message', '')}")
            break

        result = data["result"]
        if total_pages is None:
            total_pages = result["pages"]
            print(f"  业绩预告: {result['count']} 条, {total_pages} 页")

        new_rows.extend(result["data"])

        if page >= total_pages:
            break
        page += 1
        time.sleep(0.1)

    # ── Step 4: 合并去重（新记录覆盖旧记录） ──
    if prev_data is not None and new_rows:
        merged = {_make_merge_key(r): r for r in prev_data}
        new_count = 0
        update_count = 0
        for r in new_rows:
            key = _make_merge_key(r)
            if key not in merged:
                new_count += 1
            else:
                update_count += 1
            merged[key] = r
        all_rows = list(merged.values())
        print(f"  合并: {len(prev_data)} 历史 + {new_count} 新增 "
              f"+ {update_count} 更新 = {len(all_rows)} 条")
    elif prev_data is not None and not new_rows:
        all_rows = prev_data
        print(f"  合并: 无新数据, 保持 {len(prev_data)} 条")
    else:
        all_rows = new_rows

    # ── Step 5: 统计 + 保存 ──
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
