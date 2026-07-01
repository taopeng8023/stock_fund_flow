#!/usr/bin/env python
"""
全市场历史 K 线全量拉取（每只股票独立文件，多进程并行）

用法:
    python fetch_all_history.py                  # 全量，8 进程
    python fetch_all_history.py --no-minute      # 仅日/周/月线
    python fetch_all_history.py --workers=4      # 4 进程
    python fetch_all_history.py 20260630         # 指定日期
"""
import sys
import os
import csv
import json
import time
from datetime import datetime, timedelta
from multiprocessing import get_context

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import baostock as bs

BJS_TZ = __import__('baostock_data.config', fromlist=['BJS_TZ']).BJS_TZ
DATA_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

KLINE_FIELDS = [
    "date", "code", "open", "high", "low", "close", "preclose",
    "volume", "amount", "adjustflag", "turn", "tradestatus",
    "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM", "isST",
]
KLINE_HEADERS = [
    "日期", "代码", "名称", "开盘", "最高", "最低", "收盘", "前收盘",
    "成交量", "成交额", "复权类型", "换手率", "交易状态",
    "涨跌幅", "市盈率", "市净率", "市销率", "市现率", "是否ST",
]
KLINE_FIELDS_MINUTE = [
    "date", "time", "code", "open", "high", "low", "close",
    "volume", "amount", "adjustflag",
]
KLINE_HEADERS_MINUTE = [
    "日期", "时间", "代码", "名称", "开盘", "最高", "最低", "收盘",
    "成交量", "成交额", "复权类型",
]
INDEX_CODES = {
    "sh.000001": "上证指数", "sh.000016": "上证50",
    "sh.000300": "沪深300", "sh.000688": "科创50",
    "sh.000905": "中证500", "sh.000852": "中证1000",
    "sz.399001": "深证成指", "sz.399006": "创业板指",
    "sz.399005": "中小100",
}


def to_bs_date(s):
    if "-" in s: return s
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def now_str():
    return datetime.now(BJS_TZ).strftime("%Y%m%d")


def fmt_duration(seconds):
    if seconds < 60: return f"{seconds:.0f}s"
    elif seconds < 3600: return f"{seconds // 60:.0f}m{seconds % 60:.0f}s"
    else: return f"{seconds // 3600:.0f}h{(seconds % 3600) // 60:.0f}m"


def get_stocks():
    """获取全市场 A 股列表"""
    bs.login()
    for offset in range(10):
        d = (datetime.strptime(now_str(), "%Y%m%d") - timedelta(days=offset))
        ds = d.strftime("%Y%m%d")
        rs = bs.query_all_stock(day=to_bs_date(ds))
        if rs.error_code != "0": continue
        stocks = []
        while (rs.error_code == "0") & rs.next():
            row = rs.get_row_data()
            if row[1] == "1":
                stocks.append({"code": row[0], "code_name": row[2]})
        if stocks:
            print(f"[主进程] 股票列表: {len(stocks)} 只 (日期: {ds})")
            bs.logout()
            return stocks
    raise RuntimeError("无法获取股票列表")


# ============================================================
# Worker
# ============================================================
def worker_login():
    """登录，带重试"""
    for attempt in range(5):
        try:
            lg = bs.login()
            if lg.error_code == "0":
                return True
        except Exception:
            pass
        time.sleep(1 + attempt)
    return False


def fetch_one(code, fields_str, start_date, end_date, frequency, adjustflag):
    """拉取单只股票，断连自动重试"""
    for attempt in range(3):
        try:
            rs = bs.query_history_k_data_plus(
                code=code, fields=fields_str,
                start_date=start_date, end_date=end_date,
                frequency=frequency, adjustflag=adjustflag,
            )
            if rs.error_code != "0":
                time.sleep(1 + attempt)
                continue
            rows = []
            while (rs.error_code == "0") & rs.next():
                rows.append(rs.get_row_data())
            return rows
        except (BrokenPipeError, ConnectionError, OSError):
            wait = 1 + attempt * 2
            print(f"\n  [worker] {code} 断连, {wait}s 后重试", flush=True)
            time.sleep(wait)
            try:
                worker_login()
            except Exception:
                pass
        except Exception:
            time.sleep(1 + attempt)
    return []


def worker_run(chunk_id, stocks, frequency, start_date, end_date,
               adjustflag, fields, headers, outdir, progress_file):
    """
    子进程：每只股票写入独立 CSV，进度写入共享文件
    """
    fields_str = ",".join(fields)
    name_insert_pos = 3 if frequency in ("5", "15", "30", "60") else 2
    failed = []

    if not worker_login():
        return

    t0 = time.time()
    row_count = 0

    for i, stock in enumerate(stocks):
        code = stock["code"]
        name = stock["code_name"]
        outpath = os.path.join(outdir, f"{code}.csv")

        rows = fetch_one(code, fields_str, start_date, end_date, frequency, adjustflag)

        with open(outpath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for row in rows:
                row.insert(name_insert_pos, name)
                writer.writerow(row)
                row_count += 1

        if not rows:
            failed.append(code)

        # 每 10 秒写一次进度
        if (i + 1) % 20 == 0 or (i + 1) == len(stocks):
            elapsed = time.time() - t0
            with open(progress_file, "w") as pf:
                json.dump({
                    "chunk_id": chunk_id,
                    "done": i + 1, "total": len(stocks),
                    "rows": row_count, "failed": len(failed),
                    "elapsed": elapsed,
                }, pf)

        # 避免打爆服务端
        time.sleep(0.05)

    try:
        bs.logout()
    except Exception:
        pass


# ============================================================
# 主控
# ============================================================
def run_parallel(stocks, frequency, start_date, end_date, adjustflag,
                 fields, headers, date_str, subdir, num_workers):
    """分块 → 并行拉取，每只股票独立文件"""
    outdir = os.path.join(DATA_ROOT, date_str, subdir)
    os.makedirs(outdir, exist_ok=True)

    chunk_size = max(1, len(stocks) // num_workers)
    chunks = [stocks[i:i + chunk_size] for i in range(0, len(stocks), chunk_size)]
    num_workers = len(chunks)

    # 进度文件目录
    progress_dir = os.path.join(outdir, "_progress")
    os.makedirs(progress_dir, exist_ok=True)

    print(f"\n  {frequency} K线 — {len(stocks)} 只 → {num_workers} 进程")
    print(f"  输出目录: {outdir}")
    print(f"  每进程 ~{chunk_size} 只 | {start_date} → {end_date}")

    ctx = get_context("spawn")
    workers = []

    for cid, chunk in enumerate(chunks):
        progress_file = os.path.join(progress_dir, f"worker_{cid}.json")
        p = ctx.Process(target=worker_run, args=(
            cid, chunk, frequency, start_date, end_date,
            adjustflag, fields, headers, outdir, progress_file
        ))
        p.start()
        workers.append(p)
        time.sleep(0.5)  # 错开启动，避免同时冲击服务端

    # 进度监视
    t0 = time.time()
    while any(p.is_alive() for p in workers):
        time.sleep(10)
        elapsed = time.time() - t0

        # 汇总所有 worker 进度
        total_done = 0
        total_rows = 0
        total_failed = 0
        for cid in range(num_workers):
            pf = os.path.join(progress_dir, f"worker_{cid}.json")
            if os.path.exists(pf):
                try:
                    with open(pf) as f:
                        d = json.load(f)
                    total_done += d.get("done", 0)
                    total_rows += d.get("rows", 0)
                    total_failed += d.get("failed", 0)
                except Exception:
                    pass

        if total_done > 0:
            pct = total_done / len(stocks) * 100
            bar_w = 30
            filled = int(bar_w * total_done / len(stocks))
            bar = "█" * filled + "░" * (bar_w - filled)
            eta = (elapsed / total_done) * (len(stocks) - total_done)
            print(f"  [{bar}] {pct:5.1f}% {total_done}/{len(stocks)} "
                  f"| {fmt_duration(elapsed)} | ETA {fmt_duration(eta)} "
                  f"| {total_rows}行 | 失败{total_failed}", flush=True)

    for p in workers:
        p.join(timeout=5)

    # 清理进度文件
    import shutil
    shutil.rmtree(progress_dir, ignore_errors=True)

    # 统计
    file_count = len([f for f in os.listdir(outdir) if f.endswith(".csv")])
    print(f"  ✅ {subdir}/ — {file_count} 个文件, 耗时 {fmt_duration(time.time() - t0)}")


# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    date_str = None
    with_minute = True
    num_workers = 8

    for arg in sys.argv[1:]:
        if arg == "--no-minute": with_minute = False
        elif arg.startswith("--workers="): num_workers = int(arg.split("=")[1])
        elif not arg.startswith("--"): date_str = arg

    date_str = date_str or now_str()
    end_date = datetime.now(BJS_TZ).strftime("%Y-%m-%d")

    print("═" * 60)
    print(f"  BaoStock 全量历史 K 线（{num_workers} 进程，每只股票独立文件）")
    print(f"  日/周/月线: 1990-12-19 → {end_date}")
    if with_minute:
        print(f"  分钟线: 2019-01-02 → {end_date} (5/15/30/60)")
    else:
        print("  分钟线: 跳过（--no-minute）")
    print("═" * 60)

    t_total = time.time()

    # 0. 股票列表
    print("\n[0/3] 获取股票列表 ...")
    stocks = get_stocks()
    date_dir = os.path.join(DATA_ROOT, date_str)
    os.makedirs(date_dir, exist_ok=True)
    with open(os.path.join(date_dir, "stock_list.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["代码", "名称", "类型"])
        for s in stocks:
            w.writerow([s["code"], s["code_name"], "1"])

    # 1. 日线
    print("\n[1/3] 日线")
    run_parallel(stocks, "d", "1990-12-19", end_date, "2",
                 KLINE_FIELDS, KLINE_HEADERS, date_str,
                 "daily", num_workers)

    # 2. 周线 + 月线
    print("\n[2/3] 周线 + 月线")
    run_parallel(stocks, "w", "1990-12-19", end_date, "2",
                 KLINE_FIELDS, KLINE_HEADERS, date_str,
                 "weekly", num_workers)
    run_parallel(stocks, "m", "1990-12-19", end_date, "2",
                 KLINE_FIELDS, KLINE_HEADERS, date_str,
                 "monthly", num_workers)

    # 3. 分钟线
    if with_minute:
        print("\n[3/3] 分钟线")
        for freq in ["5", "15", "30", "60"]:
            run_parallel(stocks, freq, "2019-01-02", end_date, "2",
                         KLINE_FIELDS_MINUTE, KLINE_HEADERS_MINUTE, date_str,
                         f"minute_{freq}", num_workers)

    # 指数（串行）
    print("\n[指数]")
    bs.login()
    idx_fields = ["date", "code", "open", "high", "low", "close", "preclose",
                   "volume", "amount", "pctChg"]
    idx_headers = ["日期", "代码", "名称", "开盘", "最高", "最低", "收盘", "前收盘",
                    "成交量", "成交额", "涨跌幅"]
    idx_outdir = os.path.join(date_dir, "index")
    os.makedirs(idx_outdir, exist_ok=True)
    for code, name in INDEX_CODES.items():
        print(f"    {code} ({name}) ...", end=" ", flush=True)
        rows = fetch_one(code, ",".join(idx_fields), "2006-01-01", end_date, "d", "3")
        outpath = os.path.join(idx_outdir, f"{code}.csv")
        cnt = 0
        with open(outpath, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(idx_headers)
            for row in rows:
                row.insert(1, name)
                w.writerow(row)
                cnt += 1
        print(f"{cnt} 行")
    bs.logout()

    print(f"\n{'═' * 60}")
    print(f"  ✅ 全量拉取完成 — {date_str}")
    print(f"  总耗时: {fmt_duration(time.time() - t_total)}")
    print(f"  数据目录: {date_dir}")
    print(f"  结构: daily/sh.600000.csv, weekly/sh.600000.csv, ...")
    print(f"{'═' * 60}")
