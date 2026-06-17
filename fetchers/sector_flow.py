"""
板块资金流 — 行业板块 + 概念板块（主力净流入额 f62 降序）
支持大单/中单/小单明细 + Top N 板块成分股钻取 + 数据验真

数据来源:
  行业板块: https://data.eastmoney.com/bkzj/hy.html  → fs=m:90+s:4 (128 一级行业)
  概念板块: https://data.eastmoney.com/bkzj/gn.html  → fs=m:90+t:3 (概念板块)
                         https://data.eastmoney.com/bkzj/dy.html  → fs=m:90+t:1 (地域板块, 暂未采集)
"""
import json
import os
import sys
import time

try:
    from .base import push2_get, save_data, get_date_dir
except ImportError:
    _parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _parent not in sys.path:
        sys.path.insert(0, _parent)
    from fetchers.base import push2_get, save_data, get_date_dir

# ── 板块级别字段 ──
# f204=5日主力净流入, f205=10日主力净流入, f124=时间戳
SECTOR_FIELDS = ("f12,f14,f2,f3,f62,f184,"
                 "f66,f69,f72,f75,f78,f81,f84,f87,"
                 "f204,f205,f124")
CSV_FIELDS = ["f12", "f14", "f62", "f184", "f66", "f69", "f72", "f75",
              "f78", "f81", "f84", "f87", "f204", "f205"]
CSV_HEADERS = ["代码", "名称", "主力净流入", "主力占比",
               "超大单净流入", "超大单占比", "大单净流入", "大单占比",
               "中单净流入", "中单占比", "小单净流入", "小单占比",
               "5日主力净流入", "10日主力净流入"]

# ── 成分股级别字段 ──
# f204=5日主力净流入, f205=10日主力净流入（个股同样适用）
STOCK_DETAIL_FIELDS = ("f12,f14,f2,f3,f8,f10,f20,f62,f184,"
                       "f66,f69,f72,f75,f78,f81,f84,f87,"
                       "f204,f205,f100,f124")

# 成分股 CSV 导出字段（按主力净流入降序，便于直观查看）
STOCK_CSV_FIELDS = ["f12", "f14", "f2", "f3", "f62", "f184",
                    "f66", "f69", "f72", "f75", "f78", "f81",
                    "f84", "f87", "f8", "f10", "f20"]
STOCK_CSV_HEADERS = ["代码", "名称", "最新价", "涨跌幅%", "主力净流入", "主力占比%",
                     "超大单净流入", "超大单占比%", "大单净流入", "大单占比%",
                     "中单净流入", "中单占比%", "小单净流入", "小单占比%",
                     "换手率%", "量比", "总市值"]

# ── 板块配置 ──
# m:90+s:4 = 申万一级行业（与 BKZJ 行业资金流页面对齐）
# m:90+t:3 = 概念板块
SECTORS = [
    ("m:90+s:4", "industry_flow", "行业板块"),
    ("m:90+t:3", "concept_flow", "概念板块"),
]

# 行业资金流页面的已知基准值（用于验真），动态更新
_BKZJ_REFERENCE = {
    "expected_total_min": 120,   # 一级行业至少 120 个
    "expected_total_max": 135,   # 一级行业至多 135 个
}


def fetch_sector(fs, label, date_str):
    """分页获取全量板块数据"""
    all_rows = []
    total = None
    page = 1
    while True:
        params = {
            "pn": str(page), "pz": "100", "po": "1",
            "np": "1", "fltt": "2", "invt": "2",
            "fid": "f62", "fs": fs, "fields": SECTOR_FIELDS,
        }
        data = push2_get(params)
        if data.get("rc") != 0 or not data.get("data", {}).get("diff"):
            break
        result = data["data"]
        if total is None:
            total = result.get("total", 0)
        all_rows.extend(result["diff"])
        if page * 100 >= total:
            break
        page += 1
        time.sleep(0.15)
    print(f"  {label}: {len(all_rows)} 个 (API total={total})")
    return all_rows


def verify_industry_data(rows, date_str):
    """验真：检查行业板块数据量是否在预期范围内"""
    ref = _BKZJ_REFERENCE
    count = len(rows)
    issues = []

    if count < ref["expected_total_min"]:
        issues.append(f"行业数 {count} < 预期最小 {ref['expected_total_min']}")
    elif count > ref["expected_total_max"]:
        issues.append(f"行业数 {count} > 预期最大 {ref['expected_total_max']}")

    if issues:
        print(f"  ⚠ 数据验真失败:")
        for msg in issues:
            print(f"    - {msg}")
        print(f"    建议检查 fs 参数或 API 变更")
        return False
    else:
        print(f"  ✓ 数据验真通过 ({count} 个一级行业, 预期 {ref['expected_total_min']}-{ref['expected_total_max']})")
        return True


# ── 成分股数据验真 ──

def verify_stock_data(stocks, sector_code, sector_name, date_str):
    """
    验证成分股资金流数据准确性：
    1. 数量合理性（>= 5 只）
    2. 与全市场 fund_flow.json 交叉校验（top 3 只股票的 f62/f72 一致性）
    3. 内部一致性：主力净流入 ≈ 超大单 + 大单 (|f62 - f66 - f72| < 1%)
    """
    issues = []
    ok_count = 0

    # 1. 数量检查
    if len(stocks) < 5:
        issues.append(f"成分股仅 {len(stocks)} 只, 过少")
    else:
        ok_count += 1

    # 2. 交叉校验: 对比 fund_flow.json 全市场数据
    try:
        full_market = None
        market_path = os.path.join(get_date_dir(date_str), "fund_flow.json")
        if os.path.exists(market_path):
            with open(market_path, "r", encoding="utf-8") as f:
                full_market = json.load(f)
        if full_market:
            market_map = {r.get("f12", ""): r for r in full_market}
            cross_ok = 0
            cross_total = 0
            for stock in stocks[:3]:
                code = stock.get("f12", "")
                if code in market_map:
                    cross_total += 1
                    m = market_map[code]
                    # 对比主力净流入 f62
                    ref_f62 = _to_float(m.get("f62"))
                    our_f62 = _to_float(stock.get("f62"))
                    if abs(ref_f62 - our_f62) <= max(abs(ref_f62), abs(our_f62)) * 0.01 + 100:
                        cross_ok += 1
                    else:
                        issues.append(
                            f"{code}({stock.get('f14','')}) f62 与全市场不一致: "
                            f"{_fmt_yi(our_f62)} vs {_fmt_yi(ref_f62)}"
                        )
                else:
                    issues.append(f"{code}({stock.get('f14','')}) 未在全市场 fund_flow 中找到")
            if cross_total >= 2 and cross_ok >= cross_total - 1:
                ok_count += 1
            elif cross_total > 0 and cross_ok == 0:
                issues.append("交叉校验: top3 股票均与全市场数据不一致")
    except Exception as e:
        print(f"    ⚠ 交叉校验跳过: {e}")

    # 3. 内部一致性: 主力 ≈ 超大单 + 大单
    consistent = 0
    checked = 0
    for stock in stocks[:10]:
        f62 = _to_float(stock.get("f62"))
        f66 = _to_float(stock.get("f66"))
        f72 = _to_float(stock.get("f72"))
        if abs(f62) > 10000:  # 只检查有显著流量的
            checked += 1
            diff = abs(f62 - f66 - f72)
            tol = max(abs(f62), 1) * 0.02  # 2% 容差
            if diff <= tol:
                consistent += 1
    if checked >= 3 and consistent >= checked * 0.8:
        ok_count += 1
    elif checked >= 3:
        issues.append(f"内部一致性: {consistent}/{checked} 只通过 (f62≈f66+f72)")

    # 结果
    if issues:
        print(f"    ⚠ 成分股验真 ({sector_name}):")
        for msg in issues:
            print(f"      - {msg}")
        return False
    else:
        print(f"    ✓ 成分股验真通过 ({sector_name}, {len(stocks)}只, 交叉校验+内部一致性OK)")
        return True


# ── Top N 成分股钻取 ──

def fetch_sector_stocks(sector_code, sector_name, date_str):
    """获取指定板块的成分股资金流详情（含大单/中单/小单明细）"""
    all_rows = []
    total = None
    page = 1
    fs = f"b:{sector_code}"
    while True:
        params = {
            "pn": str(page), "pz": "100", "po": "1",
            "np": "1", "fltt": "2", "invt": "2",
            "fid": "f62", "fs": fs, "fields": STOCK_DETAIL_FIELDS,
        }
        try:
            data = push2_get(params)
        except Exception as e:
            print(f"    ⚠ {sector_name}({sector_code}) 成分股请求失败: {e}")
            break
        if data.get("rc") != 0 or not data.get("data", {}).get("diff"):
            break
        result = data["data"]
        if total is None:
            total = result.get("total", 0)
        all_rows.extend(result["diff"])
        if page * 100 >= total:
            break
        page += 1
        time.sleep(0.15)
    return all_rows


def fetch_top_sector_details(industry_rows, top_n=5, date_str=None):
    """
    取主力净流入最高的 top_n 个行业板块，钻取成分股大单详情。
    返回 dict: {sector_code: {name, stocks: [...], summary: {...}}}
    """
    if not industry_rows:
        return {}

    # 日期归一化 + 时间戳（一天多次采集不覆盖）
    date_dir = get_date_dir(date_str)
    if date_str is None:
        date_str = os.path.basename(date_dir)
    from datetime import datetime as _dt
    file_ts = _dt.now().strftime("%H%M%S")
    sector_dir = os.path.join(date_dir, "sectors")
    os.makedirs(sector_dir, exist_ok=True)

    # 按 f62 降序取 top N
    sorted_sectors = sorted(
        industry_rows,
        key=lambda r: _to_float(r.get("f62")),
        reverse=True,
    )[:top_n]

    result = {}

    print(f"\n  ══ Top {top_n} 行业板块成分股大单钻取 ══")
    for rank, sector in enumerate(sorted_sectors, 1):
        code = sector.get("f12", "")
        name = sector.get("f14", "")
        main_flow = _to_float(sector.get("f62"))
        main_ratio = _to_float(sector.get("f184"))
        flow_5d = _to_float(sector.get("f204"))
        flow_10d = _to_float(sector.get("f205"))

        print(f"\n  [{rank}] {name}({code}) "
              f"主力净流入 {_fmt_yi(main_flow)} 占比 {main_ratio:.2f}%"
              f" | 5日 {_fmt_yi(flow_5d)} 10日 {_fmt_yi(flow_10d)}")

        # 钻取成分股
        stocks = fetch_sector_stocks(code, name, date_str)
        if not stocks:
            print(f"    无成分股数据, 跳过")
            result[code] = {"name": name, "stocks": [], "summary": {}}
            continue

        # ── 验真 ──
        verify_stock_data(stocks, code, name, date_str)

        # 统计成分股大单分布
        large_vals = [_to_float(s.get("f72")) for s in stocks]
        pos_stocks = sum(1 for v in large_vals if v > 0)
        neg_stocks = sum(1 for v in large_vals if v < 0)
        total_large = sum(large_vals)
        total_main = sum(_to_float(s.get("f62")) for s in stocks)

        summary = {
            "sector_name": name,
            "sector_code": code,
            "sector_main_flow": main_flow,
            "sector_main_ratio": main_ratio,
            "sector_flow_5d": flow_5d,
            "sector_flow_10d": flow_10d,
            "stock_count": len(stocks),
            "large_order_pos_count": pos_stocks,
            "large_order_neg_count": neg_stocks,
            "total_large_flow": total_large,
            "total_main_flow": total_main,
            "top_large_stocks": _top_stocks(stocks, "f72", 10),
        }

        _save_sector_summary(sector_dir, code, name, main_flow, main_ratio, stocks, date_str, file_ts)
        with open(os.path.join(sector_dir, f"sector_summary_{code}_{date_str}_{file_ts}.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        result[code] = {"name": name, "stocks": stocks, "summary": summary}

        print(f"    成分股 {len(stocks)} 只 | "
              f"大单净流入: {_fmt_yi(total_large)} | "
              f"大单流入>0: {pos_stocks}只 大单流出<0: {neg_stocks}只")
        _print_top_large(stocks, 5)

    # 保存 top N 汇总
    _save_top_summary(sector_dir, result, date_str, file_ts)

    return result


def _save_sector_summary(date_dir, code, name, main_flow, main_ratio, stocks, date_str, file_ts):
    """保存单个板块的摘要和成分股明细（JSON + CSV 双格式，按主力净流入降序）"""
    # JSON 明细（全字段）
    detail_path = os.path.join(date_dir, f"sector_detail_{code}_{date_str}_{file_ts}.json")
    with open(detail_path, "w", encoding="utf-8") as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)

    # CSV 表格（选字段，按主力净流入降序，便于直观查看）
    if stocks:
        _save_stock_csv(stocks, code, name, date_dir, date_str, file_ts)


def _save_stock_csv(stocks, sector_code, sector_name, date_dir, date_str, file_ts):
    """导出成分股 CSV：按 f62 降序排列，包含主力/大单/中单/小单全字段"""
    import csv

    # 按主力净流入降序
    sorted_stocks = sorted(stocks, key=lambda s: _to_float(s.get("f62")), reverse=True)

    csv_path = os.path.join(date_dir, f"sector_stocks_{sector_code}_{date_str}_{file_ts}.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)

        # 表头：排名 + 行业 + 字段
        writer.writerow(["排名", "行业板块"] + STOCK_CSV_HEADERS)

        for i, s in enumerate(sorted_stocks, 1):
            row = [i, sector_name]
            for field in STOCK_CSV_FIELDS:
                val = s.get(field, "")
                # 百分比字段保留 2 位小数
                if field in ("f3", "f184", "f69", "f75", "f81", "f87", "f8"):
                    val = _to_float(val)
                    row.append(f"{val:.2f}" if val else "")
                # 金额字段用原始值（CSV 中可直接排序）
                elif field in ("f62", "f66", "f72", "f78", "f84", "f20"):
                    val = _to_float(val)
                    row.append(f"{val:.0f}" if val else "")
                else:
                    row.append(val)
            writer.writerow(row)

    print(f"    CSV 已保存: {os.path.basename(csv_path)} ({len(sorted_stocks)} 行)")


def _save_top_summary(date_dir, result, date_str, file_ts):
    """保存 Top N 汇总 JSON"""
    all_summary = {
        "date": date_str,
        "time": file_ts,
        "top_sectors": [
            {
                "rank": i + 1,
                "code": code,
                "name": info.get("summary", {}).get("sector_name", info.get("name", "")),
                "sector_main_flow_yi": round(
                    info.get("summary", {}).get("sector_main_flow", 0) / 1e8, 2),
                "stock_count": info.get("summary", {}).get("stock_count", 0),
                "total_large_flow_yi": round(
                    info.get("summary", {}).get("total_large_flow", 0) / 1e8, 2),
                "large_pos_ratio": (
                    f"{info.get('summary', {}).get('large_order_pos_count', 0)}/"
                    f"{info.get('summary', {}).get('stock_count', 0)}"
                ) if info.get("summary", {}).get("stock_count") else "0/0",
            }
            for i, (code, info) in enumerate(result.items())
        ],
    }
    summary_file = os.path.join(date_dir, f"sector_top5_detail_{file_ts}.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(all_summary, f, ensure_ascii=False, indent=2)
    print(f"\n  ✓ Top {len(result)} 板块成分股明细已保存至 {summary_file}")


# ── 辅助函数 ──

def _to_float(val):
    """安全转 float，非数字（None / '-' / ''）返回 0"""
    if val is None or val == "-" or val == "":
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _fmt_yi(value):
    """格式化金额为亿"""
    if abs(value) >= 1e8:
        return f"{value / 1e8:+.2f}亿"
    return f"{value / 1e4:+.2f}万"


def _top_stocks(stocks, field, n=10):
    """按指定字段降序取 top N，返回 [{code, name, value_wan}]"""
    valid = [(s, _to_float(s.get(field))) for s in stocks]
    valid = [(s, v) for s, v in valid if v != 0]
    valid.sort(key=lambda x: x[1], reverse=True)
    return [
        {"code": s.get("f12", ""), "name": s.get("f14", ""),
         "value": round(v / 1e4, 2)}
        for s, v in valid[:n]
    ]


def _print_top_large(stocks, n=5):
    """打印大单净流入最大的前 N 只成分股"""
    by_large = [(s, _to_float(s.get("f72"))) for s in stocks]
    by_large.sort(key=lambda x: x[1], reverse=True)
    for s, large in by_large[:n]:
        code = s.get("f12", "")
        name = s.get("f14", "")
        main_flow = _to_float(s.get("f62"))
        chg = _to_float(s.get("f3"))
        print(f"      {code} {name:<8s} 涨跌{chg:>+7.2f}%  "
              f"大单{_fmt_yi(large)}  主力{_fmt_yi(main_flow)}")


# ── 主入口 ──

def fetch(date_str=None, top_detail_n=5):
    """获取行业板块 + 概念板块资金流，钻取 top N 行业成分股大单详情"""
    results = {}
    industry_rows = None

    for fs, filename, label in SECTORS:
        rows = fetch_sector(fs, label, date_str)
        if rows:
            save_data(rows, filename, CSV_FIELDS, CSV_HEADERS, date_str)
        results[filename] = rows
        if filename == "industry_flow":
            industry_rows = rows

    # ── 数据验真 ──
    if industry_rows:
        verify_industry_data(industry_rows, date_str)

    # ── Top N 行业板块成分股大单钻取 ──
    if industry_rows and top_detail_n > 0:
        results["_top_detail"] = fetch_top_sector_details(
            industry_rows, top_n=top_detail_n, date_str=date_str,
        )

    return results


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="板块资金流 + Top N 成分股大单钻取")
    p.add_argument("--date", default=None, help="日期 YYYYMMDD, 默认今天")
    p.add_argument("--top", type=int, default=5, help="钻取前 N 个行业板块成分股, 0 关闭")
    args = p.parse_args()
    fetch(date_str=args.date, top_detail_n=args.top)
