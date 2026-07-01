"""
BaoStock 数据获取器 — 全市场 A 股历史行情拉取与本地存储

用法:
    from baostock_data import BaoStockFetcher

    fetcher = BaoStockFetcher()
    fetcher.fetch_daily_kline("20260630")     # 日线
    fetcher.fetch_minute_kline("20260630", freq="5")  # 5分钟线
    fetcher.fetch_all(date_str="20260630")    # 全量拉取
"""
import csv
import json
import os
import time
from datetime import datetime

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


class BaoStockFetcher:
    """BaoStock 数据获取器"""

    def __init__(self, data_root=None):
        self.data_root = data_root or BAOSTOCK_DATA_ROOT
        self._logged_in = False

    # ============================================================
    # 连接管理
    # ============================================================
    def login(self):
        """登录 BaoStock"""
        if self._logged_in:
            return
        lg = bs.login()
        if lg.error_code != "0":
            raise ConnectionError(f"BaoStock 登录失败: {lg.error_msg}")
        self._logged_in = True
        print(f"[BaoStock] 登录成功")

    def logout(self):
        """登出"""
        if self._logged_in:
            bs.logout()
            self._logged_in = False
            print(f"[BaoStock] 已登出")

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

    @staticmethod
    def _to_storage_date(date_str):
        """YYYY-MM-DD -> YYYYMMDD（本地存储格式）"""
        return date_str.replace("-", "")

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

    def _save_csv(self, rows, filepath, headers):
        """保存 CSV (UTF-8-BOM)"""
        with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for row in rows:
                writer.writerow(row)
        print(f"  -> {filepath} ({len(rows)} 条)")

    def _save_json(self, data, filepath):
        """保存 JSON"""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ============================================================
    # 股票列表
    # ============================================================
    def get_stock_list(self, date_str=None):
        """获取全市场证券列表（自动回退到最近有效交易日）"""
        self.login()
        if date_str is None:
            date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

        # 尝试最近 10 天，避免当日数据未就绪
        from datetime import timedelta
        for offset in range(10):
            try_date = (datetime.strptime(date_str, "%Y%m%d") - timedelta(days=offset))
            try_date_str = try_date.strftime("%Y%m%d")
            print(f"  尝试日期: {try_date_str} ...")
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
                print(f"[BaoStock] 全市场证券: {len(stocks)} 只 (日期: {try_date_str})")
                return stocks

        raise RuntimeError(f"查询股票列表失败: 近10天无有效数据")

    def fetch_stock_list(self, date_str=None):
        """保存全市场股票列表到本地"""
        date_str = date_str or datetime.now(BJS_TZ).strftime("%Y%m%d")
        stocks = self.get_stock_list(date_str)

        date_dir = self.get_date_dir(date_str)
        filepath = os.path.join(date_dir, "stock_list.csv")
        self._save_csv(
            [[s["code"], s["code_name"], s["type"]] for s in stocks],
            filepath,
            ["代码", "名称", "类型"],
        )
        return stocks

    def get_active_stocks(self, date_str=None):
        """获取正常交易的 A 股列表（type=1，排除指数/其它）"""
        stocks = self.get_stock_list(date_str)
        return [s for s in stocks if s["type"] == "1"]

    # ============================================================
    # K线数据 — 单只股票
    # ============================================================
    def _fetch_kline_single(self, code, start_date, end_date, frequency, fields,
                            adjustflag="2"):
        """拉取单只股票 K 线"""
        rs = bs.query_history_k_data_plus(
            code=code,
            fields=",".join(fields),
            start_date=start_date,
            end_date=end_date,
            frequency=frequency,
            adjustflag=adjustflag,
        )
        if rs.error_code != "0":
            return []

        rows = []
        while (rs.error_code == "0") & rs.next():
            rows.append(rs.get_row_data())
        return rows

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
    # K线数据 — 日/周/月（全市场批量）
    # ============================================================
    def fetch_kline_batch(self, date_str=None, frequency="d",
                          start_date="1990-12-19", end_date=None,
                          adjustflag="2", stocks=None):
        """
        批量拉取全市场 K 线

        Args:
            date_str: 存储日期标签
            frequency: d/w/m
            start_date: 起始日期
            end_date: 截止日期（默认当天）
            adjustflag: 1=后复权 2=前复权 3=不复权
            stocks: 预查询的股票列表（复用，避免重复查询）
        """
        if frequency not in FREQUENCIES_DAILY_AND_ABOVE:
            raise ValueError(f"不支持的频率: {frequency}，可选: {FREQUENCIES_DAILY_AND_ABOVE}")

        self.login()
        date_str = date_str or datetime.now(BJS_TZ).strftime("%Y%m%d")
        end_date = end_date or datetime.now(BJS_TZ).strftime("%Y-%m-%d")

        if stocks is None:
            stocks = self.get_active_stocks(date_str)
        active_stocks = stocks
        print(f"[BaoStock] 开始拉取 {frequency} K线，{len(active_stocks)} 只")

        all_rows = []
        failed = []
        t0 = time.time()

        for i, stock in enumerate(active_stocks):
            code = stock["code"]
            try:
                rows = self._fetch_kline_single(
                    code, start_date, end_date, frequency, KLINE_FIELDS, adjustflag
                )
                all_rows.extend(rows)
            except Exception as e:
                failed.append(code)
                print(f"  [{i+1}/{len(active_stocks)}] {code} {stock['code_name']} 失败: {e}")
                continue

            if (i + 1) % 500 == 0:
                elapsed = time.time() - t0
                print(f"  [{i+1}/{len(active_stocks)}] 已拉 {len(all_rows)} 行, "
                      f"耗时 {elapsed:.0f}s, 失败 {len(failed)}")

        elapsed = time.time() - t0
        print(f"[BaoStock] {frequency} K线完成: {len(all_rows)} 行, "
              f"失败 {len(failed)} 只, 耗时 {elapsed:.0f}s")

        # 保存
        filename = FREQUENCY_MAP[frequency]
        date_dir = self.get_date_dir(date_str)

        csv_path = os.path.join(date_dir, f"{filename}.csv")
        self._save_csv(all_rows, csv_path, KLINE_HEADERS)

        # 元信息
        meta = {
            "frequency": frequency,
            "start_date": start_date,
            "end_date": end_date,
            "stock_count": len(active_stocks),
            "row_count": len(all_rows),
            "failed_count": len(failed),
            "failed_codes": failed,
            "elapsed_seconds": round(elapsed, 1),
        }
        self._save_json(meta, os.path.join(date_dir, f"{filename}_meta.json"))

        return all_rows

    # ============================================================
    # K线数据 — 分钟线（全市场批量）
    # ============================================================
    def fetch_minute_kline(self, date_str=None, freq="5",
                           start_date=None, end_date=None,
                           adjustflag="2", stocks=None):
        """
        批量拉取全市场分钟 K 线

        Args:
            date_str: 存储日期标签
            freq: 5/15/30/60
            start_date: 起始日期（默认 2019-01-02）
            end_date: 截止日期（默认当天）
            adjustflag: 1=后复权 2=前复权 3=不复权
            stocks: 预查询的股票列表（复用）
        """
        if freq not in FREQUENCIES_MINUTE:
            raise ValueError(f"不支持的分钟频率: {freq}，可选: {FREQUENCIES_MINUTE}")

        self.login()
        date_str = date_str or datetime.now(BJS_TZ).strftime("%Y%m%d")
        start_date = start_date or MINUTE_START_DATE
        end_date = end_date or datetime.now(BJS_TZ).strftime("%Y-%m-%d")

        if stocks is None:
            stocks = self.get_active_stocks(date_str)
        active_stocks = stocks
        print(f"[BaoStock] 开始拉取 {freq}min K线，{len(active_stocks)} 只")

        all_rows = []
        failed = []
        t0 = time.time()

        for i, stock in enumerate(active_stocks):
            code = stock["code"]
            try:
                rows = self._fetch_kline_single(
                    code, start_date, end_date, freq, KLINE_FIELDS_MINUTE, adjustflag
                )
                all_rows.extend(rows)
            except Exception as e:
                failed.append(code)

            if (i + 1) % 200 == 0:
                elapsed = time.time() - t0
                print(f"  [{i+1}/{len(active_stocks)}] 已拉 {len(all_rows)} 行, "
                      f"耗时 {elapsed:.0f}s, 失败 {len(failed)}")

            # 分钟线 QPS 限速更严格，每只之间 sleep
            time.sleep(0.05)

        elapsed = time.time() - t0
        print(f"[BaoStock] {freq}min K线完成: {len(all_rows)} 行, "
              f"失败 {len(failed)} 只, 耗时 {elapsed:.0f}s")

        filename = FREQUENCY_MAP[freq]
        date_dir = self.get_date_dir(date_str)

        csv_path = os.path.join(date_dir, f"{filename}.csv")
        self._save_csv(all_rows, csv_path, KLINE_HEADERS_MINUTE)

        meta = {
            "frequency": f"{freq}min",
            "start_date": start_date,
            "end_date": end_date,
            "stock_count": len(active_stocks),
            "row_count": len(all_rows),
            "failed_count": len(failed),
            "failed_codes": failed,
            "elapsed_seconds": round(elapsed, 1),
        }
        self._save_json(meta, os.path.join(date_dir, f"{filename}_meta.json"))

        return all_rows

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

        all_rows = []
        for code, name in INDEX_CODES.items():
            print(f"[BaoStock] 拉取指数 {code} ({name})")
            rows = self._fetch_kline_single(
                code, start_date, end_date, "d", fields, adjustflag="3"
            )
            for r in rows:
                r.insert(1, name)  # 插入指数名称
            all_rows.extend(rows)

        date_dir = self.get_date_dir(date_str)
        csv_path = os.path.join(date_dir, "index_kline.csv")
        self._save_csv(all_rows, csv_path, headers)

        return all_rows

    # ============================================================
    # 一键全量拉取
    # ============================================================
    def fetch_all(self, date_str=None, include_minute=True):
        """
        一键拉取全量数据

        Args:
            date_str: 存储日期
            include_minute: 是否包含分钟线（耗时较长）
        """
        self.login()
        date_str = date_str or datetime.now(BJS_TZ).strftime("%Y%m%d")
        print(f"═══ BaoStock 全量拉取 {date_str} ═══")

        # 0. 股票列表（只查一次）
        print("\n[0/4] 股票列表")
        stocks = self.get_active_stocks(date_str)
        self.fetch_stock_list(date_str)  # 保存到文件

        # 1. 日线
        print("\n[1/4] 日线 K线")
        self.fetch_kline_batch(date_str, frequency="d", stocks=stocks)

        # 2. 周线 + 月线
        print("\n[2/4] 周线/月线 + 指数")
        self.fetch_kline_batch(date_str, frequency="w", stocks=stocks)
        self.fetch_kline_batch(date_str, frequency="m", stocks=stocks)
        self.fetch_index_kline(date_str)

        # 3. 分钟线
        if include_minute:
            print("\n[3/4] 分钟线 (5/15/30/60)")
            for freq in FREQUENCIES_MINUTE:
                self.fetch_minute_kline(date_str, freq=freq, stocks=stocks)

        self.logout()
        print(f"\n═══ 全量拉取完成 {date_str} ═══")


# ============================================================
# 便捷函数
# ============================================================
def fetch_daily(date_str=None):
    """快速拉取日线"""
    with BaoStockFetcher() as f:
        f.fetch_stock_list(date_str)
        f.fetch_kline_batch(date_str, frequency="d")


def fetch_minute(date_str=None, freq="5"):
    """快速拉取分钟线"""
    with BaoStockFetcher() as f:
        f.fetch_minute_kline(date_str, freq=freq)
