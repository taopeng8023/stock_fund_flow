#!/usr/bin/env python3
"""
全量A股K线数据采集 — 双API + 限频 + 断点续传

流程:
  1. 获取A股全量代码列表 (新浪批量验证)
  2. 双API拉取250日日线 OHLCV
  3. 存储到 kline_data/{code}.json

用法:
  python scripts/fetch_all_kline.py                  # 全量采集
  python scripts/fetch_all_kline.py --resume          # 续传(跳过已有)
  python scripts/fetch_all_kline.py --top 200         # 只采Top200
"""
import argparse
import json
import os
import sys
import time
import logging
from datetime import datetime
from pathlib import Path

import requests

# ── 配置 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
KLINE_DIR = PROJECT_ROOT / "kline_data"
KLINE_DIR.mkdir(parents=True, exist_ok=True)

STOCK_LIST_FILE = KLINE_DIR / "_stock_list.json"
LOG_FILE = KLINE_DIR / "_fetch.log"

# API 端点
TENCENT_URL = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
SINA_QUOTE_URL = "https://hq.sinajs.cn/list="

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}
SINA_HEADERS = {**HEADERS, "Referer": "https://finance.sina.com.cn"}

# 频率控制
STOCK_LIST_BATCH = 80      # 每批验证80只
STOCK_LIST_DELAY = 0.5     # 批次间延迟
KLINE_DELAY = 0.3          # K线请求间延迟
RETRY_DELAY = 2.0          # 重试延迟
MAX_RETRIES = 2

# A股代码范围
CODE_RANGES = [
    ("sh", 600000, 605999, "沪市主板"),
    ("sh", 688000, 689999, "科创板"),
    ("sz",    1,  4999, "深市主板"),
    ("sz", 300000, 301999, "创业板"),
    ("bj", 830000, 839999, "北交所"),
    ("bj", 870000, 879999, "北交所2"),
    ("bj", 920000, 929999, "北交所3"),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
# Phase 1: 获取全量股票列表
# ═══════════════════════════════════════════

def _format_code(code_num: int) -> str:
    """数字 → 6位代码字符串"""
    if code_num < 10000:
        return f"{code_num:06d}"  # 000001-009999
    return str(code_num)


def _validate_batch(prefix: str, codes: list[str]) -> list[str]:
    """通过新浪行情API批量验证代码有效性"""
    symbols = ",".join(f"{prefix}{c}" for c in codes)
    url = SINA_QUOTE_URL + symbols

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=SINA_HEADERS, timeout=15)
            resp.encoding = "gb2312"
            valid = []
            for line in resp.text.strip().split("\n"):
                if '=""' in line or not line.strip():
                    continue
                # 格式: var hq_str_sh600519="名称,开盘,..."
                if "var hq_str_" in line:
                    code = line.split("_")[-1].split("=")[0]  # sh600519
                    valid.append(code)
            return valid
        except Exception as e:
            if attempt < MAX_RETRIES:
                logger.debug(f"  验证重试 {attempt+1}: {e}")
                time.sleep(RETRY_DELAY)
            else:
                logger.warning(f"  验证失败: {e}")
    return []


def scan_all_stocks(resume: bool = True) -> list[dict]:
    """
    扫描全市场A股代码。

    返回: [{"code": "600519", "prefix": "sh", "name": "贵州茅台"}, ...]
    """
    if resume and STOCK_LIST_FILE.exists():
        logger.info(f"从缓存加载股票列表: {STOCK_LIST_FILE}")
        with open(STOCK_LIST_FILE) as f:
            return json.load(f)

    all_stocks = []
    total_codes = sum(end - start + 1 for _, start, end, _ in CODE_RANGES)
    scanned = 0

    for prefix, start, end, label in CODE_RANGES:
        logger.info(f"扫描 {label}: {prefix}{_format_code(start)}~{prefix}{_format_code(end)}")
        codes = [_format_code(i) for i in range(start, end + 1)]

        for i in range(0, len(codes), STOCK_LIST_BATCH):
            batch = codes[i:i + STOCK_LIST_BATCH]
            valid = _validate_batch(prefix, batch)

            for sym in valid:
                # sym格式: sh600519, 去掉前缀
                pure_code = sym.replace(prefix, "")
                all_stocks.append({"code": pure_code, "prefix": prefix})

            scanned += len(batch)
            pct = scanned / total_codes * 100
            # 进度条
            bar = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
            sys.stdout.write(f"\r  [{bar}] {pct:.1f}%  有效:{len(all_stocks)}")
            sys.stdout.flush()

            if i + STOCK_LIST_BATCH < len(codes):
                time.sleep(STOCK_LIST_DELAY)

        print()  # 换行

    logger.info(f"全市场扫描完成: {len(all_stocks)} 只有效股票")
    # 保存缓存
    with open(STOCK_LIST_FILE, "w") as f:
        json.dump(all_stocks, f, ensure_ascii=False)
    return all_stocks


# ═══════════════════════════════════════════
# Phase 2: 拉取K线数据
# ═══════════════════════════════════════════

def _fetch_tencent(prefix: str, code: str, days: int = 250):
    """腾讯K线 — 前复权"""
    param = f"{prefix}{code},day,,,{days},qfq"
    try:
        resp = requests.get(TENCENT_URL, params={"param": param}, headers=HEADERS, timeout=10)
        data = resp.json()
        key = f"{prefix}{code}"
        raw = data.get("data", {}).get(key, {}).get("qfqday")
        if not raw:
            return None
        bars = []
        for k in raw:
            if len(k) < 6:
                continue
            bars.append({
                "date": str(k[0]),
                "open": float(k[1]),
                "close": float(k[2]),
                "high": float(k[3]),
                "low": float(k[4]),
                "volume": float(k[5]) * 100 if k[5] else 0,
            })
        return bars
    except Exception as e:
        logger.debug(f"腾讯 {code}: {e}")
        return None


def _fetch_sina(prefix: str, code: str, days: int = 250):
    """新浪K线 — fallback用"""
    symbol = f"{prefix}{code}"
    try:
        resp = requests.get(SINA_KLINE_URL, params={
            "symbol": symbol, "scale": 240, "ma": "no", "datalen": days
        }, headers=HEADERS, timeout=10)
        data = resp.json()
        if not data:
            return None
        bars = []
        for d in data:
            bars.append({
                "date": d["day"],
                "open": float(d["open"]),
                "high": float(d["high"]),
                "low": float(d["low"]),
                "close": float(d["close"]),
                "volume": float(d["volume"]),
            })
        return bars
    except Exception as e:
        logger.debug(f"新浪 {code}: {e}")
        return None


def fetch_one_stock(stock: dict, days: int = 250):
    """
    双API拉取单只股票K线。

    返回: {"code": "600519", "source": "tencent", "bars": [...]}
    """
    prefix = stock["prefix"]
    code = stock["code"]

    # 主: 腾讯
    bars = _fetch_tencent(prefix, code, days)
    source = "tencent"

    # 备: 新浪
    if not bars:
        logger.info(f"腾讯失败, fallback新浪: {code}")
        bars = _fetch_sina(prefix, code, days)
        source = "sina"

    if not bars:
        logger.warning(f"双API均失败: {code}")
        return None

    # 交叉校验 (腾讯时用新浪验证最新价)
    if source == "tencent" and len(bars) >= 1:
        try:
            sina_bars = _fetch_sina(prefix, code, 2)
            if sina_bars and sina_bars[-1]["close"] > 0:
                diff = abs(bars[-1]["close"] - sina_bars[-1]["close"]) / sina_bars[-1]["close"]
                if diff > 0.02:
                    logger.warning(
                        f"交叉校验差异大: {code} 腾讯={bars[-1]['close']:.2f} "
                        f"新浪={sina_bars[-1]['close']:.2f} ({diff*100:.1f}%)"
                    )
        except Exception:
            pass

    return {"code": code, "source": source, "bars": bars, "count": len(bars)}


def save_stock(stock_data: dict):
    """保存单只股票K线到 JSON 文件"""
    path = KLINE_DIR / f"{stock_data['code']}.json"
    with open(path, "w") as f:
        json.dump(stock_data, f, ensure_ascii=False)


def load_existing_codes() -> set:
    """已下载的股票代码集合"""
    existing = set()
    for f in KLINE_DIR.glob("*.json"):
        if not f.name.startswith("_"):
            existing.add(f.stem)
    return existing


def fetch_all_kline(stocks: list[dict], resume: bool = True, limit: int = None):
    """批量拉取全量K线数据"""
    if limit:
        stocks = stocks[:limit]

    existing = load_existing_codes() if resume else set()
    if existing:
        logger.info(f"已有 {len(existing)} 只, 跳过")

    total = len(stocks)
    success = 0
    failed = 0
    skipped = 0
    start_time = time.time()

    for i, stock in enumerate(stocks):
        code = stock["code"]

        # 续传跳过
        if code in existing:
            skipped += 1
            continue

        # 限频
        if i > 0 and (i - skipped) > 0:
            time.sleep(KLINE_DELAY)

        # 拉取
        data = fetch_one_stock(stock)
        if data and len(data["bars"]) >= 60:  # 至少60根才存
            save_stock(data)
            success += 1
        else:
            failed += 1

        # 进度
        done = i + 1
        elapsed = time.time() - start_time
        rate = (done - skipped) / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate if rate > 0 else 0

        bar = "█" * (done * 30 // total) + "░" * (30 - done * 30 // total)
        sys.stdout.write(
            f"\r  [{bar}] {done}/{total} "
            f"✓{success} ✗{failed} ↷{skipped} "
            f"{rate:.1f}只/s ETA:{eta:.0f}s  "
        )
        sys.stdout.flush()

    print()
    elapsed = time.time() - start_time
    logger.info(
        f"采集完成: {success}成功 {failed}失败 {skipped}跳过 "
        f"耗时 {elapsed/60:.1f}分钟"
    )
    # 汇总
    all_files = list(KLINE_DIR.glob("*.json"))
    stock_files = [f for f in all_files if not f.name.startswith("_")]
    total_bars = 0
    for f in stock_files[:10]:
        with open(f) as fh:
            d = json.load(fh)
            total_bars += d.get("count", 0)
    logger.info(f"总文件: {len(stock_files)} 个, 示例前10均线数: {total_bars//10 if stock_files else 0}")


# ═══════════════════════════════════════════
# Main
# ═══════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="全量A股K线数据采集")
    parser.add_argument("--resume", action="store_true", default=True,
                        help="续传模式,跳过已有数据")
    parser.add_argument("--no-resume", action="store_true",
                        help="强制重新下载全部")
    parser.add_argument("--top", type=int, default=0,
                        help="只采集前N只(测试用)")
    parser.add_argument("--scan-only", action="store_true",
                        help="只扫描股票列表,不下载K线")
    parser.add_argument("--days", type=int, default=250,
                        help="K线天数")
    args = parser.parse_args()

    resume = not args.no_resume

    logger.info(f"{'='*50}")
    logger.info(f"全量A股K线采集")
    logger.info(f"数据目录: {KLINE_DIR}")
    logger.info(f"模式: {'续传' if resume else '全量重采'} | K线天数: {args.days}")
    logger.info(f"{'='*50}")

    # Phase 1: 股票列表
    stocks = scan_all_stocks(resume=resume)
    logger.info(f"股票总数: {len(stocks)}")

    if args.scan_only:
        return

    # Phase 2: K线采集
    limit = args.top if args.top > 0 else None
    fetch_all_kline(stocks, resume=resume, limit=limit)


if __name__ == "__main__":
    main()
