#!/usr/bin/env python
"""
全市场历史 K 线全量拉取（每只股票独立文件，多进程并行）

用法:
    python fetch_all_history.py                  # 全量
    python fetch_all_history.py --no-minute      # 仅日/周/月线
    python fetch_all_history.py --workers=4      # 指定进程数
    python fetch_all_history.py 20260630         # 指定日期
"""
import sys
import os
import csv
import time
import socket
from collections import Counter
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor

# 避免 baostock 服务器不可达时无限挂死
socket.setdefaulttimeout(15)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from baostock_data.config import BJS_TZ, BAOSTOCK_DATA_ROOT as DATA_ROOT, classify_code

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


# ============================================================
# 工具
# ============================================================
def to_bs_date(s):
    if "-" in s:
        return s
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def now_str():
    return datetime.now(BJS_TZ).strftime("%Y%m%d")


def fmt_duration(seconds):
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds // 60:.0f}m{seconds % 60:.0f}s"
    else:
        return f"{seconds // 3600:.0f}h{(seconds % 3600) // 60:.0f}m"


# ============================================================
# 主进程：股票列表
# ============================================================
def bs_login():
    import baostock as bs
    for attempt in range(3):
        try:
            lg = bs.login()
            if lg.error_code == "0":
                return True
        except Exception:
            pass
        time.sleep(2 + attempt * 2)
    return False


def classify_stock(code: str) -> str:
    """根据代码前缀分类: 个股 / 指数 / ETF（包装 classify_code，返回中文标签）。"""
    _map = {"stock": "个股", "index": "指数", "etf": "ETF"}
    return _map.get(classify_code(code), "个股")


def get_stocks():
    import baostock as bs
    print("[登录] BaoStock ...", flush=True)
    if not bs_login():
        raise RuntimeError("BaoStock 登录失败，服务器不可用")

    print("[查询] 股票列表 ...", flush=True)
    for offset in range(10):
        d = (datetime.now(BJS_TZ) - timedelta(days=offset))
        ds = d.strftime("%Y%m%d")
        print(f"  尝试日期: {ds} ...", flush=True)
        rs = bs.query_all_stock(day=to_bs_date(ds))
        if rs.error_code != "0":
            print(f"    错误: {rs.error_msg}", flush=True)
            continue
        stocks = []
        while (rs.error_code == "0") & rs.next():
            row = rs.get_row_data()
            if row[1] == "1":
                stocks.append({"code": row[0], "code_name": row[2]})
        if stocks:
            print(f"[完成] {len(stocks)} 只 A 股", flush=True)
            bs.logout()
            return stocks
    raise RuntimeError("无法获取股票列表")


# ============================================================
# 子进程 worker
# ============================================================
def _read_last_date(csv_path):
    """读取 CSV 最后一行的日期，用于增量更新。"""
    if not os.path.exists(csv_path):
        return None
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            header = f.readline()
            if not header:
                return None
            last_line = None
            for line in f:
                if line.strip():
                    last_line = line
            if last_line:
                return last_line.split(",")[0].strip()
    except Exception:
        pass
    return None


def _worker_fetch(args):
    """
    子进程：独立登录 → 逐只拉取（增量追加） + 更新共享进度 → 写 CSV。
    每个子进程拥有独立的 baostock 连接，真正并行。

    args: (chunk_id, chunk, frequency, start_date, end_date,
           adjustflag, fields, headers, outdir, progress_done, progress_rows)
    """
    import baostock as bs

    (chunk_id, chunk, frequency, start_date, end_date,
     adjustflag, fields, headers, outdir,
     progress_done, progress_rows) = args

    fields_str = ",".join(fields)
    name_insert_pos = 3 if frequency in ("5", "15", "30", "60") else 2
    failed = 0
    row_count = 0

    # 登录
    for attempt in range(3):
        try:
            lg = bs.login()
            if lg.error_code == "0":
                break
        except Exception:
            pass
        time.sleep(2 + attempt * 2)
    else:
        progress_done.value += len(chunk)
        return chunk_id, len(chunk), 0, len(chunk)

    # 逐只拉取
    for stock in chunk:
        code = stock["code"]
        name = stock["code_name"]
        # daily/ 目录按类型分子目录: stocks/ etfs/ indices/
        if "daily" in outdir and "stocks" not in outdir:
            typ = classify_code(code)
            sub = {"stock": "stocks", "etf": "etfs", "index": "indices"}.get(typ, "stocks")
            stock_outdir = os.path.join(outdir, sub)
            os.makedirs(stock_outdir, exist_ok=True)
        else:
            stock_outdir = outdir
        outpath = os.path.join(stock_outdir, f"{code}.csv")

        # 增量：检查已有数据，只拉新数据
        stock_start = start_date
        is_append = False
        last_date = _read_last_date(outpath)
        if last_date and last_date >= end_date:
            # 已是最新，跳过
            progress_done.value += 1
            continue
        if last_date:
            try:
                last_dt = datetime.strptime(last_date.replace("-", "")[:8], "%Y%m%d")
                stock_start = (last_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            except Exception:
                pass
            is_append = True

        # API 调用（带重试）
        rows = []
        for attempt in range(3):
            try:
                rs = bs.query_history_k_data_plus(
                    code=code, fields=fields_str,
                    start_date=stock_start, end_date=end_date,
                    frequency=frequency, adjustflag=adjustflag,
                )
                if rs.error_code != "0":
                    time.sleep(1 + attempt)
                    continue
                while (rs.error_code == "0") & rs.next():
                    rows.append(rs.get_row_data())
                break
            except Exception:
                time.sleep(1 + attempt)
                try:
                    bs.login()
                except Exception:
                    pass

        # 写 CSV（新建或追加）
        mode = "a" if is_append else "w"
        with open(outpath, mode, encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            if not is_append:
                writer.writerow(headers)
            for row in rows:
                row.insert(name_insert_pos, name)
                writer.writerow(row)
                row_count += 1

        if not rows and not is_append:
            failed += 1

        # 实时更新共享进度（每只股票）
        progress_done.value += 1
        progress_rows.value += len(rows)

    try:
        bs.logout()
    except Exception:
        pass

    return chunk_id, len(chunk), row_count, failed


# ============================================================
# 多进程批量拉取
# ============================================================
def run_parallel(stocks, frequency, start_date, end_date, adjustflag,
                 fields, headers, subdir, num_workers, data_root):
    """多进程拉取，共享进度计数器，实时刷新。输出到 data_root/<subdir>/。"""
    from multiprocessing import Manager

    outdir = os.path.join(data_root, subdir)
    os.makedirs(outdir, exist_ok=True)

    total = len(stocks)
    chunk_size = max(1, total // num_workers)
    chunks = [stocks[i:i + chunk_size] for i in range(0, total, chunk_size)]
    n_workers = len(chunks)

    # 共享进度计数器（跨进程原子更新）
    manager = Manager()
    progress_done = manager.Value("i", 0)
    progress_rows = manager.Value("i", 0)

    print(f"\n{'─' * 50}", flush=True)
    print(f"  {frequency} K线 — {total} 只 → {n_workers} 进程", flush=True)
    print(f"  输出: {outdir}", flush=True)
    print(f"  范围: {start_date} → {end_date}", flush=True)
    print(f"{'─' * 50}", flush=True)

    # 构建任务（每进程一个大块，减少登录次数）
    tasks = [(cid, chunk, frequency, start_date, end_date,
              adjustflag, fields, headers, outdir,
              progress_done, progress_rows)
             for cid, chunk in enumerate(chunks)]

    t0 = time.time()
    total_failed = 0

    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = [executor.submit(_worker_fetch, t) for t in tasks]

        # 轮询进度（每秒），不等待进程结束
        last_done = 0
        while not all(f.done() for f in futures):
            time.sleep(1)
            done = progress_done.value
            if done == last_done:
                continue  # 无变化，跳过打印
            last_done = done

            elapsed = time.time() - t0
            pct = done / total * 100
            bar_w = 30
            filled = int(bar_w * done / total)
            bar = "█" * filled + "░" * (bar_w - filled)
            speed = done / elapsed if elapsed > 0 else 0
            eta = (total - done) / speed if speed > 0 else 0
            alive = sum(1 for f in futures if f.running())
            print(f"  [{bar}] {pct:5.1f}% {done}/{total} "
                  f"| {fmt_duration(elapsed)} | ETA {fmt_duration(eta)} "
                  f"| {progress_rows.value}行 | 进程{alive}",
                  flush=True)

        # 汇总结果
        for f in futures:
            try:
                _, _, _, failed = f.result()
                total_failed += failed
            except Exception as e:
                print(f"  ⚠ 进程异常: {e}", flush=True)

    elapsed = time.time() - t0
    file_count = len([f for f in os.listdir(outdir) if f.endswith(".csv")])
    print(f"  ✅ {subdir}/ — {file_count} 文件, {progress_rows.value} 行, "
          f"失败 {total_failed} 只, 耗时 {fmt_duration(elapsed)}", flush=True)
    manager.shutdown()


# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    with_minute = True
    num_workers = min(8, os.cpu_count() or 4)  # 默认 = CPU 核数

    for arg in sys.argv[1:]:
        if arg == "--no-minute":
            with_minute = False
        elif arg.startswith("--workers="):
            num_workers = int(arg.split("=")[1])
        # 忽略旧的日期参数（向后兼容）
        elif not arg.startswith("--"):
            pass  # date_str no longer used

    end_date = datetime.now(BJS_TZ).strftime("%Y-%m-%d")
    today_str = now_str()

    print("═" * 60, flush=True)
    print(f"  BaoStock 全量历史 K 线（{num_workers} 进程，本机 {os.cpu_count()} 核）", flush=True)
    print(f"  增量模式：已有数据自动跳过，仅追加新交易日", flush=True)
    print(f"  日/周/月线: 1990-12-19 → {end_date}", flush=True)
    if with_minute:
        print(f"  分钟线: 2019-01-02 → {end_date} (5/15/30/60)", flush=True)
    else:
        print("  分钟线: 跳过（--no-minute）", flush=True)
    print("═" * 60, flush=True)

    t_total = time.time()

    # 0. 股票列表（按优先级排序: 个股 > 指数 > ETF）
    print("\n[0/3] 获取股票列表", flush=True)
    stocks = get_stocks()
    # 按分类优先级排序
    priority = {"个股": 0, "指数": 1, "ETF": 2}
    stocks.sort(key=lambda s: priority.get(classify_stock(s["code"]), 3))
    cnt = Counter(classify_stock(s["code"]) for s in stocks)
    print(f"  分类: 个股{cnt.get('个股',0)} 指数{cnt.get('指数',0)} ETF{cnt.get('ETF',0)} (按此顺序更新)", flush=True)
    os.makedirs(DATA_ROOT, exist_ok=True)
    with open(os.path.join(DATA_ROOT, "stock_list.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["代码", "名称", "类型"])
        for s in stocks:
            code = s["code"]
            # 根据 BaoStock 类型字段 + 代码前缀双重分类
            bs_type = s.get("type", "1")
            if bs_type == "1":
                stock_type = classify_stock(code)
            elif bs_type == "2":
                stock_type = "指数"
            elif bs_type == "3":
                stock_type = "其他"
            else:
                stock_type = classify_stock(code)
            w.writerow([code, s["code_name"], stock_type])
    print(f"  -> stock_list.csv ({len(stocks)} 条)", flush=True)

    # 1. 日线
    print("\n[1/3] 日线", flush=True)
    run_parallel(stocks, "d", "1990-12-19", end_date, "2",
                 KLINE_FIELDS, KLINE_HEADERS, "daily",
                 num_workers, DATA_ROOT)

    # 2. 周线 + 月线
    print("\n[2/3] 周线 + 月线", flush=True)
    run_parallel(stocks, "w", "1990-12-19", end_date, "2",
                 KLINE_FIELDS, KLINE_HEADERS, "weekly",
                 num_workers, DATA_ROOT)
    run_parallel(stocks, "m", "1990-12-19", end_date, "2",
                 KLINE_FIELDS, KLINE_HEADERS, "monthly",
                 num_workers, DATA_ROOT)

    # 3. 分钟线
    if with_minute:
        print("\n[3/3] 分钟线", flush=True)
        for freq in ["5", "15", "30", "60"]:
            run_parallel(stocks, freq, "2019-01-02", end_date, "2",
                         KLINE_FIELDS_MINUTE, KLINE_HEADERS_MINUTE,
                         f"minute_{freq}", num_workers, DATA_ROOT)

    # 指数（主进程串行，数据量小，增量追加）
    print("\n[指数]", flush=True)
    if bs_login():
        import baostock as bs
        idx_fields = ["date", "code", "open", "high", "low", "close", "preclose",
                       "volume", "amount", "pctChg"]
        idx_headers = ["日期", "代码", "名称", "开盘", "最高", "最低", "收盘", "前收盘",
                        "成交量", "成交额", "涨跌幅"]
        idx_outdir = os.path.join(DATA_ROOT, "index")
        os.makedirs(idx_outdir, exist_ok=True)
        for code, name in INDEX_CODES.items():
            outpath = os.path.join(idx_outdir, f"{code}.csv")

            # 增量：检查已有数据
            idx_start = "2006-01-01"
            is_append = False
            last_date = _read_last_date(outpath)
            if last_date and last_date >= end_date:
                print(f"    {code} ({name}) ... 已是最新，跳过", flush=True)
                continue
            if last_date:
                try:
                    last_dt = datetime.strptime(last_date.replace("-", "")[:8], "%Y%m%d")
                    idx_start = (last_dt + timedelta(days=1)).strftime("%Y-%m-%d")
                except Exception:
                    pass
                is_append = True

            print(f"    {code} ({name}) ...", end=" ", flush=True)
            rows = []
            for attempt in range(3):
                try:
                    rs = bs.query_history_k_data_plus(
                        code=code, fields=",".join(idx_fields),
                        start_date=idx_start, end_date=end_date,
                        frequency="d", adjustflag="3",
                    )
                    if rs.error_code == "0":
                        while (rs.error_code == "0") & rs.next():
                            rows.append(rs.get_row_data())
                        break
                except Exception:
                    time.sleep(1 + attempt)
            cnt = 0
            mode = "a" if is_append else "w"
            with open(outpath, mode, encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                if not is_append:
                    w.writerow(idx_headers)
                for row in rows:
                    row.insert(1, name)
                    w.writerow(row)
                    cnt += 1
            print(f"{cnt} 行", flush=True)
        bs.logout()

    print(f"\n{'═' * 60}", flush=True)
    print(f"  ✅ 全量拉取完成 — {today_str}", flush=True)
    print(f"  总耗时: {fmt_duration(time.time() - t_total)}", flush=True)
    print(f"  数据目录: {DATA_ROOT}/", flush=True)
    print(f"{'═' * 60}", flush=True)
