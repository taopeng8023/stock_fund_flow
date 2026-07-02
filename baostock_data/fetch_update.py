#!/usr/bin/env python
"""
K线数据多线程增量更新脚本

特性:
  - 基于本机 CPU 核心数自动配置线程池（I/O 密集型，线程数 = CPU * 2）
  - 增量拉取：已有 CSV 自动跳过，仅追加缺失的交易日
  - 断点续跑：进度文件追踪完成状态，中断后自动跳过已完成股票
  - 支持日线/周线/月线/分钟线/指数，所有频率并行更新
  - 每个线程独立 BaoStock 连接，真正的 I/O 并行

用法:
    python fetch_update.py                 # 默认：近5日，自动线程数
    python fetch_update.py 3               # 近3日
    python fetch_update.py --workers=8     # 指定8线程
    python fetch_update.py --no-minute     # 跳过分钟线
    python fetch_update.py 3 -w 8          # 近3日 + 8线程
"""
import sys
import os
import csv
import time
import json
import socket
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Set, Dict

socket.setdefaulttimeout(15)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from baostock_data.config import (
    BJS_TZ, BAOSTOCK_DATA_ROOT, STOCK_LIST_PATH,
    DAILY_DIR, WEEKLY_DIR, MONTHLY_DIR,
    MINUTE_5_DIR, MINUTE_15_DIR, MINUTE_30_DIR, MINUTE_60_DIR,
    INDEX_DIR, FREQ_DIR_MAP, FREQUENCIES_MINUTE, INDEX_CODES,
    KLINE_FIELDS, KLINE_HEADERS,
    KLINE_FIELDS_MINUTE, KLINE_HEADERS_MINUTE,
)


# ============================================================
# 工具函数
# ============================================================
def to_bs_date(date_str: str) -> str:
    """YYYYMMDD → YYYY-MM-DD"""
    if "-" in date_str:
        return date_str
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"


def now_str() -> str:
    return datetime.now(BJS_TZ).strftime("%Y%m%d")


def fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds // 60:.0f}m{seconds % 60:.0f}s"
    else:
        return f"{seconds // 3600:.0f}h{(seconds % 3600) // 60:.0f}m"


def read_last_date(csv_path: str) -> Optional[str]:
    """读取 CSV 最后一行的日期"""
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


# ============================================================
# 进度追踪器（线程安全）
# ============================================================
class ProgressTracker:
    """共享进度计数器，线程安全"""

    def __init__(self, total: int, progress_path: str):
        self.total = total
        self.done_count = 0
        self.row_count = 0
        self.failed_codes: Set[str] = set()
        self.completed_codes: Set[str] = set()
        self._lock = threading.Lock()
        self.progress_path = progress_path
        self.t0 = time.time()
        self.last_print_ts = 0.0
        self._load()

    def _load(self) -> None:
        """加载断点进度"""
        if os.path.exists(self.progress_path):
            try:
                with open(self.progress_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.completed_codes = set(data.get("completed_codes", []))
                self.failed_codes = set(data.get("failed_codes", []))
                self.done_count = len(self.completed_codes)
                if self.done_count > 0:
                    print(
                        f"  [断点续跑] 已完成 {self.done_count} 只, "
                        f"失败 {len(self.failed_codes)} 只, "
                        f"剩余 {self.total - self.done_count} 只",
                        flush=True,
                    )
            except Exception:
                pass

    def is_completed(self, code: str) -> bool:
        return code in self.completed_codes

    def add_done(self, code: str, rows: int) -> None:
        with self._lock:
            self.completed_codes.add(code)
            self.done_count = len(self.completed_codes)
            self.row_count += rows
            self._save()
            self._maybe_print()

    def add_failed(self, code: str) -> None:
        with self._lock:
            self.failed_codes.add(code)

    def _save(self) -> None:
        try:
            with open(self.progress_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "completed_codes": sorted(self.completed_codes),
                        "failed_codes": sorted(self.failed_codes),
                        "done": self.done_count,
                        "rows": self.row_count,
                        "updated_at": datetime.now(BJS_TZ).isoformat(),
                    },
                    f,
                )
        except Exception:
            pass

    def _maybe_print(self) -> None:
        now = time.time()
        if now - self.last_print_ts < 10:
            return
        self.last_print_ts = now
        elapsed = now - self.t0
        pct = self.done_count / self.total * 100 if self.total > 0 else 0
        bar_w = 30
        filled = int(bar_w * self.done_count / self.total) if self.total > 0 else 0
        bar = "█" * filled + "░" * (bar_w - filled)
        speed = self.done_count / elapsed if elapsed > 0 else 0
        eta = (self.total - self.done_count) / speed if speed > 0 else 0
        print(
            f"  [{bar}] {pct:5.1f}% {self.done_count}/{self.total} "
            f"| {fmt_duration(elapsed)} | ETA {fmt_duration(eta)} "
            f"| {self.row_count}行 | 失败{len(self.failed_codes)}",
            flush=True,
        )

    def finish(self) -> None:
        elapsed = time.time() - self.t0
        print(
            f"  ✅ 完成 — {self.done_count}/{self.total} 只, "
            f"{self.row_count} 行, 失败 {len(self.failed_codes)} 只, "
            f"耗时 {fmt_duration(elapsed)}",
            flush=True,
        )
        if os.path.exists(self.progress_path):
            try:
                os.remove(self.progress_path)
            except Exception:
                pass


# ============================================================
# 线程 worker — 每个线程独立 BaoStock 连接
# ============================================================
class UpdateWorker:
    """单线程 worker：独立登录 → 拉取 chunk → 写 CSV → 登出"""

    def __init__(
        self,
        out_dir: str,
        frequency: str,
        start_date: str,
        end_date: str,
        fields: List[str],
        headers: List[str],
        progress: ProgressTracker,
        minute_mode: bool = False,
    ):
        self.out_dir = out_dir
        self.frequency = frequency
        self.start_date = start_date
        self.end_date = end_date
        self.fields = fields
        self.headers = headers
        self.progress = progress
        self.minute_mode = minute_mode
        self._bs = None

    def _login(self) -> bool:
        import baostock as bs

        for attempt in range(3):
            try:
                lg = bs.login()
                if lg.error_code == "0":
                    self._bs = bs
                    return True
            except Exception:
                pass
            time.sleep(2 + attempt * 2)
        return False

    def _logout(self) -> None:
        if self._bs:
            try:
                self._bs.logout()
            except Exception:
                pass

    def process(self, chunk: List[dict]) -> None:
        """处理一批股票"""
        if not self._login():
            for s in chunk:
                self.progress.add_failed(s["code"])
            return

        name_insert_pos = 3 if self.minute_mode else 2
        fields_str = ",".join(self.fields)

        for stock in chunk:
            code = stock["code"]
            name = stock.get("code_name", "")

            # 跳过断点已完成
            if self.progress.is_completed(code):
                continue

            csv_path = os.path.join(self.out_dir, f"{code}.csv")

            # 增量：确定拉取起点
            stock_start = self.start_date
            is_append = False
            last_date = read_last_date(csv_path)
            if last_date and last_date >= self.end_date:
                # 已是最新
                self.progress.add_done(code, 0)
                continue
            if last_date:
                try:
                    last_dt = datetime.strptime(
                        last_date.replace("-", "")[:8], "%Y%m%d"
                    )
                    stock_start = (last_dt + timedelta(days=1)).strftime("%Y-%m-%d")
                except Exception:
                    pass
                is_append = True

            # API 调用（带重试 + 断连恢复）
            rows: List[List[str]] = []
            for attempt in range(3):
                try:
                    rs = self._bs.query_history_k_data_plus(
                        code=code,
                        fields=fields_str,
                        start_date=stock_start,
                        end_date=self.end_date,
                        frequency=self.frequency,
                        adjustflag="2",
                    )
                    if rs.error_code != "0":
                        time.sleep(1 + attempt)
                        continue
                    while (rs.error_code == "0") & rs.next():
                        rows.append(rs.get_row_data())
                    break
                except (BrokenPipeError, ConnectionError, OSError):
                    if attempt < 2:
                        time.sleep(1 + attempt * 2)
                        try:
                            self._bs.login()
                        except Exception:
                            pass
                except Exception:
                    time.sleep(1 + attempt)

            # 写 CSV
            try:
                mode = "a" if is_append else "w"
                with open(csv_path, mode, encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f)
                    if not is_append:
                        writer.writerow(self.headers)
                    for row in rows:
                        row.insert(name_insert_pos, name)
                        writer.writerow(row)
            except Exception:
                self.progress.add_failed(code)
                continue

            self.progress.add_done(code, len(rows))

            # 分钟线限速（避免触发频率限制）
            if self.minute_mode:
                time.sleep(0.05)

        self._logout()


# ============================================================
# 股票列表获取（主线程执行，复用连接）
# ============================================================
def get_stocks() -> List[dict]:
    """获取全市场 A 股列表"""
    import baostock as bs

    print("[登录] BaoStock ...", flush=True)
    for attempt in range(3):
        try:
            lg = bs.login()
            if lg.error_code == "0":
                break
        except Exception:
            pass
        if attempt < 2:
            time.sleep(2 + attempt * 2)
    else:
        raise RuntimeError("BaoStock 登录失败，服务器不可用")

    print("[查询] 股票列表 ...", flush=True)
    for offset in range(10):
        d = datetime.now(BJS_TZ) - timedelta(days=offset)
        ds = d.strftime("%Y%m%d")
        print(f"  尝试日期: {ds} ...", flush=True)
        rs = bs.query_all_stock(day=to_bs_date(ds))
        if rs.error_code != "0":
            continue
        stocks: List[dict] = []
        while (rs.error_code == "0") & rs.next():
            row = rs.get_row_data()
            if row[1] == "1":  # type=1 A股
                stocks.append({"code": row[0], "code_name": row[2] if len(row) > 2 else ""})
        if stocks:
            print(f"[完成] {len(stocks)} 只 A 股", flush=True)
            bs.logout()
            return stocks

    raise RuntimeError("无法获取股票列表")


# ============================================================
# 多线程批量拉取
# ============================================================
def run_parallel(
    stocks: List[dict],
    frequency: str,
    start_date: str,
    end_date: str,
    fields: List[str],
    headers: List[str],
    out_dir: str,
    num_workers: int,
    label: str,
) -> None:
    """多线程并行拉取，每个线程独立连接"""
    os.makedirs(out_dir, exist_ok=True)
    total = len(stocks)

    # 进度文件
    progress_path = os.path.join(out_dir, f"_update_progress_{frequency}.json")
    progress = ProgressTracker(total, progress_path)

    # 过滤已完成股票
    pending = [s for s in stocks if not progress.is_completed(s["code"])]
    if not pending:
        print(f"  [{label}] 所有股票已是最新，跳过", flush=True)
        progress.finish()
        return

    # 分块：每线程平均分配
    chunk_size = max(1, len(pending) // num_workers)
    chunks = [pending[i : i + chunk_size] for i in range(0, len(pending), chunk_size)]
    n_workers = len(chunks)

    minute_mode = frequency in FREQUENCIES_MINUTE

    print(
        f"\n{'─' * 55}\n"
        f"  [{label}] {frequency} K线 — {total} 只 → {n_workers} 线程\n"
        f"  输出: {out_dir}\n"
        f"  范围: {start_date} → {end_date}\n"
        f"{'─' * 55}",
        flush=True,
    )

    # 创建 worker 池（每个线程独立 UpdateWorker + 独立连接）
    workers = [
        UpdateWorker(
            out_dir=out_dir,
            frequency=frequency,
            start_date=start_date,
            end_date=end_date,
            fields=list(fields),
            headers=list(headers),
            progress=progress,
            minute_mode=minute_mode,
        )
        for _ in range(n_workers)
    ]

    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = [
            executor.submit(workers[i].process, chunks[i]) for i in range(n_workers)
        ]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"  ⚠ 线程异常: {e}", flush=True)

    progress.finish()


# ============================================================
# 指数更新（主线程，数据量小无需并行）
# ============================================================
def update_index(
    start_date: str, end_date: str, stocks: List[dict]
) -> None:
    """更新指数日线（主线程串行，数据量小）"""
    import baostock as bs

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
        print("  ⚠ 指数登录失败，跳过", flush=True)
        return

    idx_fields = [
        "date", "code", "open", "high", "low", "close", "preclose",
        "volume", "amount", "pctChg",
    ]
    idx_headers = [
        "日期", "代码", "名称", "开盘", "最高", "最低", "收盘", "前收盘",
        "成交量", "成交额", "涨跌幅",
    ]

    os.makedirs(INDEX_DIR, exist_ok=True)
    total_rows = 0

    for code, name in INDEX_CODES.items():
        csv_path = os.path.join(INDEX_DIR, f"{code}.csv")

        idx_start = start_date
        is_append = False
        last_date = read_last_date(csv_path)
        if last_date and last_date >= end_date:
            print(f"    {code} ({name}) ... 已是最新，跳过", flush=True)
            continue
        if last_date:
            try:
                last_dt = datetime.strptime(
                    last_date.replace("-", "")[:8], "%Y%m%d"
                )
                idx_start = (last_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            except Exception:
                pass
            is_append = True

        print(f"    {code} ({name}) ...", end=" ", flush=True)
        rows: List[List[str]] = []
        for attempt in range(3):
            try:
                rs = bs.query_history_k_data_plus(
                    code=code,
                    fields=",".join(idx_fields),
                    start_date=idx_start,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="3",
                )
                if rs.error_code != "0":
                    time.sleep(1 + attempt)
                    continue
                while (rs.error_code == "0") & rs.next():
                    rows.append(rs.get_row_data())
                break
            except Exception:
                time.sleep(1 + attempt)

        mode = "a" if is_append else "w"
        with open(csv_path, mode, encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            if not is_append:
                writer.writerow(idx_headers)
            for row in rows:
                row.insert(1, name)
                writer.writerow(row)
        print(f"{len(rows)} 行", flush=True)
        total_rows += len(rows)

    print(f"  ✅ index/ — {total_rows} 行", flush=True)
    bs.logout()


# ============================================================
# 主入口
# ============================================================
def main() -> None:
    days_back = 5
    num_workers = min(16, os.cpu_count() * 2) if os.cpu_count() else 8
    with_minute = True

    # 解析命令行参数
    for arg in sys.argv[1:]:
        if arg == "--no-minute":
            with_minute = False
        elif arg.startswith("--workers="):
            num_workers = int(arg.split("=")[1])
        elif arg.startswith("-w"):
            # -w8 或 -w 8
            if "=" in arg:
                num_workers = int(arg.split("=")[1])
            elif len(arg) > 2:
                num_workers = int(arg[2:])
            else:
                # next arg
                idx = sys.argv.index(arg)
                if idx + 1 < len(sys.argv):
                    num_workers = int(sys.argv[idx + 1])
        elif arg.isdigit():
            days_back = int(arg)

    num_workers = max(1, min(num_workers, 16))  # 限制 1-16

    end_date_str = datetime.now(BJS_TZ).strftime("%Y-%m-%d")
    start_date_str = (
        datetime.now(BJS_TZ) - timedelta(days=days_back * 2)
    ).strftime("%Y-%m-%d")
    minute_start_str = (
        datetime.now(BJS_TZ) - timedelta(days=days_back)
    ).strftime("%Y-%m-%d")

    cpu_count = os.cpu_count() or "?"

    print("═" * 60, flush=True)
    print(f"  BaoStock 多线程增量更新", flush=True)
    print(f"  本机 {cpu_count} 核 → {num_workers} 线程", flush=True)
    print(f"  更新范围: 近 {days_back} 个交易日", flush=True)
    print(f"  日线: {start_date_str} → {end_date_str}", flush=True)
    if with_minute:
        print(f"  分钟线: {minute_start_str} → {end_date_str} (5/15/30/60)", flush=True)
    else:
        print(f"  分钟线: 跳过 (--no-minute)", flush=True)
    print("═" * 60, flush=True)

    t_total = time.time()

    # 0. 股票列表
    print("\n[0/4] 获取股票列表", flush=True)
    stocks = get_stocks()
    os.makedirs(BAOSTOCK_DATA_ROOT, exist_ok=True)
    with open(STOCK_LIST_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["代码", "名称", "类型"])
        all_stocks_saved = set()
        for s in stocks:
            writer.writerow([s["code"], s["code_name"], "1"])
            all_stocks_saved.add(s["code"])
    print(f"  -> stock_list.csv ({len(stocks)} 条)", flush=True)

    # 1. 日线
    print("\n[1/4] 日线", flush=True)
    run_parallel(
        stocks=stocks,
        frequency="d",
        start_date=start_date_str,
        end_date=end_date_str,
        fields=KLINE_FIELDS,
        headers=KLINE_HEADERS,
        out_dir=DAILY_DIR,
        num_workers=num_workers,
        label="日线",
    )

    # 2. 周线 + 月线
    print("\n[2/4] 周线 + 月线", flush=True)
    run_parallel(
        stocks=stocks,
        frequency="w",
        start_date=start_date_str,
        end_date=end_date_str,
        fields=KLINE_FIELDS,
        headers=KLINE_HEADERS,
        out_dir=WEEKLY_DIR,
        num_workers=num_workers,
        label="周线",
    )
    run_parallel(
        stocks=stocks,
        frequency="m",
        start_date=start_date_str,
        end_date=end_date_str,
        fields=KLINE_FIELDS,
        headers=KLINE_HEADERS,
        out_dir=MONTHLY_DIR,
        num_workers=num_workers,
        label="月线",
    )

    # 3. 分钟线
    if with_minute:
        print("\n[3/4] 分钟线", flush=True)
        for freq in FREQUENCIES_MINUTE:
            run_parallel(
                stocks=stocks,
                frequency=freq,
                start_date=minute_start_str,
                end_date=end_date_str,
                fields=KLINE_FIELDS_MINUTE,
                headers=KLINE_HEADERS_MINUTE,
                out_dir=FREQ_DIR_MAP[freq],
                num_workers=num_workers,
                label=f"{freq}分钟",
            )

    # 4. 指数
    print("\n[4/4] 指数", flush=True)
    update_index(start_date=start_date_str, end_date=end_date_str, stocks=stocks)

    print(f"\n{'═' * 60}", flush=True)
    print(f"  ✅ 多线程增量更新完成 — {now_str()}", flush=True)
    print(f"  总耗时: {fmt_duration(time.time() - t_total)}", flush=True)
    print(f"  数据目录: {BAOSTOCK_DATA_ROOT}/", flush=True)
    print(f"{'═' * 60}", flush=True)


if __name__ == "__main__":
    main()
