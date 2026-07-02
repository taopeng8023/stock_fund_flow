"""
BaoStock 数据获取器 — 全市场 A 股历史行情拉取与本地存储

特性:
  - 分批增量写入 CSV（崩溃不丢数据）
  - 断点续跑（进度文件追踪已完成的股票）
  - 实时进度条 + ETA

用法:
    from baostock_data import BaoStockFetcher

    with BaoStockFetcher() as f:
        f.fetch_all("20260630")                     # 全量拉取
        f.fetch_incremental("20260630", days_back=5) # 增量更新
"""
import csv
import json
import os
import sys
import time
import socket
from datetime import datetime, timedelta

# 避免 baostock 服务器不可达时无限挂死
socket.setdefaulttimeout(15)

import baostock as bs

from .config import (
    BAOSTOCK_DATA_ROOT,
    BJS_TZ,
    KLINE_FIELDS,
    KLINE_HEADERS,
    KLINE_FIELDS_MINUTE,
    KLINE_HEADERS_MINUTE,
    FREQUENCIES_DAILY_AND_ABOVE,
    FREQUENCIES_MINUTE,
    FREQUENCY_MAP,
    MINUTE_START_DATE,
    INDEX_CODES,
)


# ============================================================
# 进度条工具
# ============================================================
def _format_duration(seconds):
    """秒数转可读格式"""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds // 60:.0f}m{seconds % 60:.0f}s"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h:.0f}h{m:.0f}m"


def _progress_bar(current, total, elapsed, failed, bar_width=30):
    """生成进度行字符串（无 \r，纯新行输出）"""
    pct = current / total if total > 0 else 0
    filled = int(bar_width * pct)
    bar = "█" * filled + "░" * (bar_width - filled)

    if pct > 0 and current > 0:
        eta_sec = (elapsed / current) * (total - current)
        eta_str = _format_duration(eta_sec)
    else:
        eta_str = "..."

    line = (f"  [{bar}] {pct * 100:5.1f}% "
            f"{current}/{total} "
            f"| 耗时 {_format_duration(elapsed)} "
            f"| ETA {eta_str} "
            f"| 失败 {failed}")
    return line


class BaoStockFetcher:
    """BaoStock 数据获取器"""

    def __init__(self, data_root=None):
        self.data_root = data_root or BAOSTOCK_DATA_ROOT
        self._logged_in = False

    # ============================================================
    # 连接管理
    # ============================================================
    def login(self):
        """登录 BaoStock（带超时保护）"""
        if self._logged_in:
            return
        try:
            lg = bs.login()
            if lg.error_code != "0":
                raise ConnectionError(f"BaoStock 登录失败: {lg.error_msg} (code={lg.error_code})")
            self._logged_in = True
            print(f"[BaoStock] 登录成功")
        except (socket.timeout, TimeoutError, OSError) as e:
            raise ConnectionError(
                f"BaoStock 服务器连接超时 (15s)，服务器可能不可用。\n"
                f"  服务器: public-api.baostock.com:10030\n"
                f"  原始错误: {e}"
            )

    def logout(self):
        """登出"""
        if self._logged_in:
            bs.logout()
            self._logged_in = False
            print(f"\n[BaoStock] 已登出")

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *args):
        self.logout()

    # ============================================================
    # 日期格式转换
    # ============================================================
    @staticmethod
    def _to_bs_date(date_str):
        """YYYYMMDD -> YYYY-MM-DD（BaoStock API 格式）"""
        if "-" in date_str:
            return date_str
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    # ============================================================
    # 路径工具
    # ============================================================
    def get_date_dir(self, date_str=None):
        """获取日期子目录，自动创建"""
        if date_str is None:
            date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")
        d = os.path.join(self.data_root, date_str)
        os.makedirs(d, exist_ok=True)
        return d

    # ============================================================
    # 日志刷新（强制实时输出，解决后台运行看不到输出的问题）
    # ============================================================
    @staticmethod
    def _flush_print(*args, **kwargs):
        print(*args, **kwargs)
        sys.stdout.flush()

    # ============================================================
    # 股票列表
    # ============================================================
    def get_stock_list(self, date_str=None):
        """获取全市场证券列表（自动回退到最近有效交易日）"""
        self.login()
        if date_str is None:
            date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

        for offset in range(10):
            try_date = (datetime.strptime(date_str, "%Y%m%d") - timedelta(days=offset))
            try_date_str = try_date.strftime("%Y%m%d")
            self._flush_print(f"  尝试日期: {try_date_str} ...")
            rs = bs.query_all_stock(day=self._to_bs_date(try_date_str))
            if rs.error_code != "0":
                continue
            stocks = []
            while (rs.error_code == "0") & rs.next():
                row = rs.get_row_data()
                stocks.append({
                    "code": row[0],
                    "type": row[1] if len(row) > 1 else "",
                    "code_name": row[2] if len(row) > 2 else "",
                })
            if stocks:
                self._flush_print(f"[BaoStock] 全市场证券: {len(stocks)} 只 (日期: {try_date_str})")
                return stocks

        raise RuntimeError("查询股票列表失败: 近10天无有效数据")

    def fetch_stock_list(self, date_str=None):
        """保存全市场股票列表到本地"""
        date_str = date_str or datetime.now(BJS_TZ).strftime("%Y%m%d")
        stocks = self.get_stock_list(date_str)
        date_dir = self.get_date_dir(date_str)
        filepath = os.path.join(date_dir, "stock_list.csv")
        with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["代码", "名称", "类型"])
            for s in stocks:
                writer.writerow([s["code"], s["code_name"], s["type"]])
        self._flush_print(f"  -> stock_list.csv ({len(stocks)} 条)")
        return stocks

    def get_active_stocks(self, date_str=None):
        """获取正常交易的 A 股列表（type=1）"""
        stocks = self.get_stock_list(date_str)
        return [s for s in stocks if s["type"] == "1"]

    # ============================================================
    # K线数据 — 单只股票
    # ============================================================
    def _fetch_kline_single(self, code, start_date, end_date, frequency, fields,
                            adjustflag="2", max_retries=3):
        """拉取单只股票 K 线（带重试 + 断连恢复）"""
        for attempt in range(max_retries):
            try:
                rs = bs.query_history_k_data_plus(
                    code=code,
                    fields=",".join(fields),
                    start_date=start_date,
                    end_date=end_date,
                    frequency=frequency,
                    adjustflag=adjustflag,
                )
                if rs.error_code != "0":
                    time.sleep(1 + attempt)
                    continue

                rows = []
                while (rs.error_code == "0") & rs.next():
                    rows.append(rs.get_row_data())
                return rows

            except (BrokenPipeError, ConnectionError, OSError):
                # 断连：跳过 logout（可能卡死），直接重登录
                if attempt < max_retries - 1:
                    wait = 1 + attempt * 2
                    self._flush_print(f"\n  ⚠ {code} 断连，{wait}s 后重试 ({attempt + 1}/{max_retries})")
                    time.sleep(wait)
                    try:
                        bs.login()
                    except Exception:
                        time.sleep(1)
                        try:
                            bs.login()
                        except Exception:
                            pass
                    continue
                return []

            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1 + attempt)
                    continue
                self._flush_print(f"\n  ⚠ {code} 异常: {e}")
                return []

    def fetch_single_daily(self, code, start_date, end_date, adjustflag="2"):
        """单只股票日线"""
        return self._fetch_kline_single(
            code, start_date, end_date, "d", KLINE_FIELDS, adjustflag
        )

    def fetch_single_minute(self, code, start_date, end_date, freq="5", adjustflag="2"):
        """单只股票分钟线"""
        if freq not in FREQUENCIES_MINUTE:
            raise ValueError(f"不支持的分钟频率: {freq}，可选: {FREQUENCIES_MINUTE}")
        return self._fetch_kline_single(
            code, start_date, end_date, freq, KLINE_FIELDS_MINUTE, adjustflag
        )

    # ============================================================
    # 断点续跑 + 增量写入 核心方法
    # ============================================================
    def _load_progress(self, progress_path):
        """加载进度文件，返回已完成的代码集合"""
        if os.path.exists(progress_path):
            with open(progress_path, "r") as f:
                data = json.load(f)
            return set(data.get("completed_codes", []))
        return set()

    def _save_progress(self, progress_path, completed_codes, failed_codes, total_rows):
        """保存进度"""
        with open(progress_path, "w") as f:
            json.dump({
                "completed_codes": list(completed_codes),
                "failed_codes": list(failed_codes),
                "total_rows": total_rows,
                "updated_at": datetime.now(BJS_TZ).isoformat(),
            }, f)

    def _batch_fetch_with_progress(self, stocks, frequency, start_date, end_date,
                                    adjustflag, fields, headers, date_str, filename,
                                    flush_interval=100, minute_mode=False):
        """
        批量拉取 + 增量写入 + 断点续跑 + 实时进度

        核心改进:
          1. 每 flush_interval 只股票 flush 一次 CSV（不丢数据）
          2. 进度文件追踪完成/失败，重启自动跳过
          3. 实时进度条 + ETA
        """
        self.login()
        date_dir = self.get_date_dir(date_str)
        csv_path = os.path.join(date_dir, f"{filename}.csv")
        progress_path = os.path.join(date_dir, f"{filename}_progress.json")
        meta_path = os.path.join(date_dir, f"{filename}_meta.json")

        # 代码 → 名称 映射
        code_name_map = {s["code"]: s.get("code_name", "") for s in stocks}
        # 分钟线 row 结构: [date, time, code, ...] → name 插在 code 后 (index 3)
        # 日线 row 结构:   [date, code, ...]      → name 插在 code 后 (index 2)
        name_insert_pos = 3 if minute_mode else 2

        # 断点续跑：跳过已完成
        completed_codes = self._load_progress(progress_path)
        pending_stocks = [s for s in stocks if s["code"] not in completed_codes]

        if completed_codes:
            self._flush_print(f"  [断点续跑] 已完成 {len(completed_codes)} 只, "
                              f"剩余 {len(pending_stocks)} 只")

        total_count = len(stocks)
        done_count = len(completed_codes)
        failed_codes = set()

        # 首个 batch 决定是否重写 CSV
        is_first_write = (done_count == 0)
        buffer = []
        total_rows = 0
        t0 = time.time()
        last_flush = time.time()

        # 统计用
        batch_elapsed = 0.0
        batch_count = 0
        last_progress_print = 0  # 上次打印进度的时间戳

        for i, stock in enumerate(pending_stocks):
            code = stock["code"]
            try:
                rows = self._fetch_kline_single(
                    code, start_date, end_date, frequency, fields, adjustflag
                )
                # 注入股票名称
                name = code_name_map.get(code, "")
                for row in rows:
                    row.insert(name_insert_pos, name)
                buffer.extend(rows)
                if rows:
                    batch_count += 1
            except Exception as e:
                failed_codes.add(code)
                self._flush_print(f"\n  ⚠ {code} {stock.get('code_name', '')} 异常: {e}")
                continue

            # 分批 flush
            if len(buffer) >= flush_interval or (i + 1) == len(pending_stocks):
                mode = "w" if is_first_write else "a"
                with open(csv_path, mode, encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f)
                    if is_first_write:
                        writer.writerow(headers)
                        is_first_write = False
                    for row in buffer:
                        writer.writerow(row)

                total_rows += len(buffer)
                done_count += batch_count
                batch_elapsed += (time.time() - last_flush)
                last_flush = time.time()

                # 更新进度
                new_completed = {s["code"] for s in pending_stocks[:i + 1]}
                all_completed = completed_codes | new_completed
                self._save_progress(progress_path, all_completed, failed_codes, total_rows)

                # 实时进度（每 15 秒输一行，避免刷屏）
                now_ts = time.time()
                if now_ts - last_progress_print >= 15:
                    elapsed = time.time() - t0
                    progress_line = _progress_bar(done_count, total_count, elapsed, len(failed_codes))
                    self._flush_print(progress_line)
                    last_progress_print = now_ts

                buffer = []
                batch_count = 0

            # 分钟线限速
            if minute_mode:
                time.sleep(0.05)

        # 完成
        elapsed = time.time() - t0
        self._flush_print()  # 换行
        self._flush_print(f"  ✅ {filename}.csv — {total_rows} 行, "
                          f"耗时 {_format_duration(elapsed)}, "
                          f"失败 {len(failed_codes)} 只")

        # 写入元信息
        meta = {
            "frequency": frequency,
            "start_date": start_date,
            "end_date": end_date,
            "stock_count": total_count,
            "row_count": total_rows,
            "failed_count": len(failed_codes),
            "failed_codes": list(failed_codes),
            "elapsed_seconds": round(elapsed, 1),
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        # 完成后删除进度文件
        if os.path.exists(progress_path):
            os.remove(progress_path)

        return total_rows

    # ============================================================
    # K线数据 — 日/周/月（全市场批量）
    # ============================================================
    def fetch_kline_batch(self, date_str=None, frequency="d",
                          start_date="1990-12-19", end_date=None,
                          adjustflag="2", stocks=None):
        """批量拉取全市场 K 线（带增量写入+断点续跑）"""
        if frequency not in FREQUENCIES_DAILY_AND_ABOVE:
            raise ValueError(f"不支持的频率: {frequency}，可选: {FREQUENCIES_DAILY_AND_ABOVE}")

        self.login()
        date_str = date_str or datetime.now(BJS_TZ).strftime("%Y%m%d")
        end_date = end_date or datetime.now(BJS_TZ).strftime("%Y-%m-%d")

        if stocks is None:
            stocks = self.get_active_stocks(date_str)

        filename = FREQUENCY_MAP[frequency]
        self._flush_print(f"\n{'=' * 50}")
        self._flush_print(f"  {frequency} K线 — {len(stocks)} 只 | {start_date} → {end_date}")
        self._flush_print(f"{'=' * 50}")

        return self._batch_fetch_with_progress(
            stocks=stocks,
            frequency=frequency,
            start_date=start_date,
            end_date=end_date,
            adjustflag=adjustflag,
            fields=KLINE_FIELDS,
            headers=KLINE_HEADERS,
            date_str=date_str,
            filename=filename,
            flush_interval=200,
            minute_mode=False,
        )

    # ============================================================
    # K线数据 — 分钟线（全市场批量）
    # ============================================================
    def fetch_minute_kline(self, date_str=None, freq="5",
                           start_date=None, end_date=None,
                           adjustflag="2", stocks=None):
        """批量拉取全市场分钟 K 线（带增量写入+断点续跑）"""
        if freq not in FREQUENCIES_MINUTE:
            raise ValueError(f"不支持的分钟频率: {freq}，可选: {FREQUENCIES_MINUTE}")

        self.login()
        date_str = date_str or datetime.now(BJS_TZ).strftime("%Y%m%d")
        start_date = start_date or MINUTE_START_DATE
        end_date = end_date or datetime.now(BJS_TZ).strftime("%Y-%m-%d")

        if stocks is None:
            stocks = self.get_active_stocks(date_str)

        filename = FREQUENCY_MAP[freq]
        self._flush_print(f"\n{'=' * 50}")
        self._flush_print(f"  {freq}min K线 — {len(stocks)} 只 | {start_date} → {end_date}")
        self._flush_print(f"{'=' * 50}")

        return self._batch_fetch_with_progress(
            stocks=stocks,
            frequency=freq,
            start_date=start_date,
            end_date=end_date,
            adjustflag=adjustflag,
            fields=KLINE_FIELDS_MINUTE,
            headers=KLINE_HEADERS_MINUTE,
            date_str=date_str,
            filename=filename,
            flush_interval=100,
            minute_mode=True,
        )

    # ============================================================
    # 指数数据
    # ============================================================
    def fetch_index_kline(self, date_str=None, start_date="2006-01-01", end_date=None):
        """拉取主要指数日线"""
        self.login()
        date_str = date_str or datetime.now(BJS_TZ).strftime("%Y%m%d")
        end_date = end_date or datetime.now(BJS_TZ).strftime("%Y-%m-%d")

        fields = ["date", "code", "open", "high", "low", "close", "preclose",
                   "volume", "amount", "pctChg"]
        headers = ["日期", "代码", "名称", "开盘", "最高", "最低", "收盘", "前收盘",
                    "成交量", "成交额", "涨跌幅"]

        self._flush_print(f"\n{'=' * 50}")
        self._flush_print(f"  指数日线 — {len(INDEX_CODES)} 只 | {start_date} → {end_date}")
        self._flush_print(f"{'=' * 50}")

        date_dir = self.get_date_dir(date_str)
        csv_path = os.path.join(date_dir, "index_kline.csv")

        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for code, name in INDEX_CODES.items():
                self._flush_print(f"  {code} ({name}) ...", end=" ")
                rows = self._fetch_kline_single(
                    code, start_date, end_date, "d", fields, adjustflag="3"
                )
                for r in rows:
                    r.insert(1, name)
                    writer.writerow(r)
                self._flush_print(f"{len(rows)} 行")

        return True

    # ============================================================
    # 一键全量拉取
    # ============================================================
    def fetch_all(self, date_str=None, include_minute=True):
        """一键拉取全量数据（带增量写入+断点续跑）"""
        self.login()
        date_str = date_str or datetime.now(BJS_TZ).strftime("%Y%m%d")
        self._flush_print(f"\n{'═' * 60}")
        self._flush_print(f"  BaoStock 全量拉取 — {date_str}")
        self._flush_print(f"{'═' * 60}")

        # 0. 股票列表（只查一次，复用给后续所有批次）
        self._flush_print(f"\n[0/4] 股票列表")
        stocks = self.get_active_stocks(date_str)
        all_stocks = self.get_stock_list(date_str)  # 全类型保存
        date_dir = self.get_date_dir(date_str)
        filepath = os.path.join(date_dir, "stock_list.csv")
        with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["代码", "名称", "类型"])
            for s in all_stocks:
                writer.writerow([s["code"], s["code_name"], s["type"]])
        self._flush_print(f"  -> stock_list.csv ({len(all_stocks)} 条)")
        self._flush_print(f"  A股 (type=1): {len(stocks)} 只")

        # 1. 日线
        self._flush_print(f"\n[1/4] 日线 K线")
        self.fetch_kline_batch(date_str, frequency="d", stocks=stocks)

        # 2. 周线 + 月线
        self._flush_print(f"\n[2/4] 周线/月线 + 指数")
        self.fetch_kline_batch(date_str, frequency="w", stocks=stocks)
        self.fetch_kline_batch(date_str, frequency="m", stocks=stocks)
        self.fetch_index_kline(date_str)

        # 3. 分钟线
        if include_minute:
            self._flush_print(f"\n[3/4] 分钟线 (5/15/30/60)")
            for freq in FREQUENCIES_MINUTE:
                self.fetch_minute_kline(date_str, freq=freq, stocks=stocks)

        self.logout()
        self._flush_print(f"\n{'═' * 60}")
        self._flush_print(f"  ✅ 全量拉取完成 — {date_str}")
        self._flush_print(f"{'═' * 60}")

    # ============================================================
    # 增量更新（当日数据）
    # ============================================================
    def fetch_incremental(self, date_str=None, days_back=5):
        """增量拉取最近 N 个交易日的数据（日常更新用）"""
        self.login()
        date_str = date_str or datetime.now(BJS_TZ).strftime("%Y%m%d")
        end_date = datetime.now(BJS_TZ).strftime("%Y-%m-%d")
        start_date = (datetime.now(BJS_TZ) - timedelta(days=days_back * 2)).strftime("%Y-%m-%d")
        minute_start = (datetime.now(BJS_TZ) - timedelta(days=days_back)).strftime("%Y-%m-%d")

        self._flush_print(f"\n{'═' * 60}")
        self._flush_print(f"  BaoStock 增量更新 — {date_str} (近{days_back}日)")
        self._flush_print(f"{'═' * 60}")

        stocks = self.get_active_stocks(date_str)
        all_stocks = self.get_stock_list(date_str)
        date_dir = self.get_date_dir(date_str)
        filepath = os.path.join(date_dir, "stock_list.csv")
        with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["代码", "名称", "类型"])
            for s in all_stocks:
                writer.writerow([s["code"], s["code_name"], s["type"]])
        self._flush_print(f"  A股: {len(stocks)} 只")

        self._flush_print(f"\n[日线] {start_date} → {end_date}")
        self.fetch_kline_batch(date_str, frequency="d",
                               start_date=start_date, end_date=end_date,
                               stocks=stocks)

        self._flush_print(f"\n[分钟线] {minute_start} → {end_date}")
        for freq in FREQUENCIES_MINUTE:
            self.fetch_minute_kline(date_str, freq=freq,
                                    start_date=minute_start, end_date=end_date,
                                    stocks=stocks)

        self.fetch_index_kline(date_str, start_date=start_date, end_date=end_date)

        self.logout()
        self._flush_print(f"\n{'═' * 60}")
        self._flush_print(f"  ✅ 增量更新完成 — {date_str}")
        self._flush_print(f"{'═' * 60}")


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import sys
    date_str = sys.argv[1] if len(sys.argv) > 1 else None
    with_minute = "--no-minute" not in sys.argv
    with BaoStockFetcher() as f:
        f.fetch_all(date_str, include_minute=with_minute)
