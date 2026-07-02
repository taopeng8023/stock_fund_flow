"""
日线 OHLCV 数据加载 — 双 API 交叉验证

腾讯 K 线 (主): 前复权, 250+ 日, volume=手
新浪 K 线 (备): 不复权, 交叉校验+fallback
"""
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
KLINE_DIR = PROJECT_ROOT / "kline_data"

logger = logging.getLogger(__name__)

# ── API 端点 ──
TENCENT_URL = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SINA_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
REQUEST_DELAY = 0.3  # 请求间隔秒


@dataclass
class DailyBar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float       # 股数
    source: str = "tencent"


def _code_to_market(code: str) -> tuple:
    """代码 → (腾讯市场前缀, 新浪前缀)"""
    if code.startswith("6"):
        return ("sh", "sh")
    elif code.startswith(("0", "3")):
        return ("sz", "sz")
    elif code.startswith("8") or code.startswith("4"):
        return ("bj", "bj")
    return ("sh", "sh")


def _try_tencent(code: str, days: int = 250) -> Optional[list[DailyBar]]:
    """腾讯 K 线 API — 前复权日线"""
    mkt, _ = _code_to_market(code)
    param = f"{mkt}{code},day,,,{days},qfq"
    try:
        resp = requests.get(TENCENT_URL, params={"param": param}, headers=HEADERS, timeout=10)
        data = resp.json()
        key = f"{mkt}{code}"
        raw = data.get("data", {}).get(key, {}).get("qfqday")
        if not raw:
            return None
        bars = []
        for k in raw:
            if len(k) < 6:
                continue
            # 格式: [date, open, close, high, low, volume(lots), (除权信息dict)]
            close_val = float(k[2])
            open_val = float(k[1])
            high_val = float(k[3])
            low_val = float(k[4])
            vol_lots = float(k[5]) if k[5] else 0
            bars.append(DailyBar(
                date=str(k[0]),
                open=open_val,
                high=max(high_val, open_val, close_val),
                low=min(low_val, open_val, close_val),
                close=close_val,
                volume=vol_lots * 100,  # 手 → 股
                source="tencent",
            ))
        return bars if bars else None
    except Exception as e:
        logger.warning(f"腾讯 K 线 {code}: {e}")
        return None


def _try_sina_close(code: str) -> Optional[float]:
    """新浪 K 线 API — 仅取最新收盘价 (轻量校验)"""
    _, sina_prefix = _code_to_market(code)
    symbol = f"{sina_prefix}{code}"
    try:
        resp = requests.get(SINA_URL, params={
            "symbol": symbol, "scale": 240, "ma": "no", "datalen": 1
        }, headers=HEADERS, timeout=10)
        data = resp.json()
        if data:
            return float(data[-1]["close"])
    except Exception as e:
        logger.debug(f"新浪校验 {code}: {e}")
    return None


def _try_sina_full(code: str, days: int = 250) -> Optional[list[DailyBar]]:
    """新浪 K 线 API — 全量日线 (fallback 用)"""
    _, sina_prefix = _code_to_market(code)
    symbol = f"{sina_prefix}{code}"
    try:
        resp = requests.get(SINA_URL, params={
            "symbol": symbol, "scale": 240, "ma": "no", "datalen": days
        }, headers=HEADERS, timeout=10)
        data = resp.json()
        if not data:
            return None
        bars = []
        for d in data:
            bars.append(DailyBar(
                date=d["day"],
                open=float(d["open"]),
                high=float(d["high"]),
                low=float(d["low"]),
                close=float(d["close"]),
                volume=float(d["volume"]),
                source="sina",
            ))
        return bars if bars else None
    except Exception as e:
        logger.warning(f"新浪 K 线 {code}: {e}")
        return None


def load_daily_bars(code: str, days: int = 250) -> Optional[list[DailyBar]]:
    """
    加载单股日线 OHLCV。

    策略: 腾讯(前复权)优先 → 新浪 fallback → 交叉校验最新收盘价
    """
    bars = _try_tencent(code, days)

    if not bars:
        logger.info(f"{code}: 腾讯 API 失败, fallback 新浪")
        bars = _try_sina_full(code, days)
        if not bars:
            logger.error(f"{code}: 双 API 均失败")
            return None
        return bars

    # 交叉校验最新收盘价（腾讯前复权 vs 新浪不复权，最新一天一致）
    sina_close = _try_sina_close(code)
    if sina_close and sina_close > 0:
        diff_pct = abs(bars[-1].close - sina_close) / sina_close
        if diff_pct > 0.01:
            logger.warning(
                f"{code}: 腾讯/新浪收盘价差异 {diff_pct*100:.2f}% "
                f"(腾讯={bars[-1].close:.2f} 新浪={sina_close:.2f})"
            )

    return bars


def load_daily_bars_from_local(code: str, days: int = 250) -> Optional[list[DailyBar]]:
    """
    从本地 kline_data/{code}.json 加载日线 OHLCV。
    仅本地文件，不联网。
    """
    kline_path = KLINE_DIR / f"{code}.json"
    if not kline_path.exists():
        return None
    try:
        with open(kline_path) as f:
            data = json.load(f)
        bars = []
        for b in data.get("bars", []):
            bars.append(DailyBar(
                date=b["date"],
                open=b["open"],
                high=b["high"],
                low=b["low"],
                close=b["close"],
                volume=b.get("volume", 0),
                source=data.get("source", "local"),
            ))
        if len(bars) >= 60:
            return bars[-days:]
    except Exception as e:
        logger.warning(f"本地K线读取失败 {code}: {e}")
    return None


def load_multi_bars_local(codes: list[str], days: int = 250) -> dict[str, list[DailyBar]]:
    """批量从本地加载日线，无网络请求"""
    result = {}
    for code in codes:
        bars = load_daily_bars_from_local(code, days)
        if bars:
            result[code] = bars
    logger.info(f"本地K线加载: {len(result)}/{len(codes)} 只成功")
    return result


def load_multi_bars(codes: list[str], days: int = 250, delay: float = REQUEST_DELAY) -> dict[str, list[DailyBar]]:
    """
    批量加载多股日线 OHLCV。

    返回 {code: [DailyBar, ...]}
    """
    result = {}
    for i, code in enumerate(codes):
        bars = load_daily_bars(code, days)
        if bars:
            result[code] = bars
        if delay and i < len(codes) - 1:
            time.sleep(delay)
    logger.info(f"日线加载: {len(result)}/{len(codes)} 只成功")
    return result
