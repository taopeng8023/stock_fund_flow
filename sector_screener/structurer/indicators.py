"""
经典技术指标 — 纯 Python 实现

- EMA / SMA / MA 族
- MACD (DIF/DEA/柱)
- RSI (14)
- Bollinger Bands (20, 2σ)
- ATR (14)
"""
import math
from .price_loader import DailyBar


# ═══════════════════════════════════════════
# 基础均线
# ═══════════════════════════════════════════

def calc_sma(closes: list[float], period: int) -> list[float]:
    """简单移动平均。长度不足的位返回 None"""
    result = [0.0] * len(closes)
    for i in range(len(closes)):
        if i < period - 1:
            result[i] = float("nan")
        else:
            result[i] = sum(closes[i - period + 1:i + 1]) / period
    return result


def calc_ema(closes: list[float], period: int) -> list[float]:
    """指数移动平均 — 递推公式 k=2/(period+1)"""
    if not closes:
        return []
    k = 2.0 / (period + 1)
    ema = [closes[0]]
    for price in closes[1:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


# ═══════════════════════════════════════════
# MACD
# ═══════════════════════════════════════════

def calc_macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """
    MACD 指标。

    返回:
        dif:  list[float] — EMA(fast) - EMA(slow)
        dea:  list[float] — EMA(signal) of DIF
        hist: list[float] — DIF - DEA (MACD 柱)
    """
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)

    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = calc_ema(dif, signal)
    hist = [d - e for d, e in zip(dif, dea)]

    return dif, dea, hist


# ═══════════════════════════════════════════
# RSI
# ═══════════════════════════════════════════

def calc_rsi(closes: list[float], period: int = 14) -> list[float]:
    """
    RSI — Wilder's smoothing 方法。

    返回 0-100 的 RSI 序列，前 period 个值为 nan
    """
    if len(closes) < period + 1:
        return [float("nan")] * len(closes)

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    rsi = [float("nan")] * (period + 1)

    # 初始平均
    gains = sum(max(c, 0) for c in changes[:period])
    losses = sum(max(-c, 0) for c in changes[:period])
    avg_gain = gains / period
    avg_loss = losses / period

    rsi.append(_rsi_from_avg(avg_gain, avg_loss))

    # Wilder smoothing
    for i in range(period, len(changes)):
        change = changes[i]
        gain = max(change, 0)
        loss = max(-change, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rsi.append(_rsi_from_avg(avg_gain, avg_loss))

    return rsi


def _rsi_from_avg(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ═══════════════════════════════════════════
# Bollinger Bands
# ═══════════════════════════════════════════

def calc_bollinger(closes: list[float], period: int = 20, std_mult: float = 2.0):
    """
    Bollinger Bands。

    返回:
        mid:   list[float] — 中轨 (MA20)
        upper: list[float] — 上轨 (mid + 2σ)
        lower: list[float] — 下轨 (mid - 2σ)
        width: list[float] — 带宽百分比 (upper-lower)/mid
    """
    n = len(closes)
    mid = calc_sma(closes, period)
    upper = [float("nan")] * n
    lower = [float("nan")] * n
    width = [float("nan")] * n

    for i in range(period - 1, n):
        window = closes[i - period + 1:i + 1]
        mean = mid[i]
        var = sum((x - mean) ** 2 for x in window) / period
        std = math.sqrt(var)
        upper[i] = mean + std_mult * std
        lower[i] = mean - std_mult * std
        width[i] = (upper[i] - lower[i]) / mean if mean > 0 else 0

    return mid, upper, lower, width


# ═══════════════════════════════════════════
# ATR
# ═══════════════════════════════════════════

def calc_atr(bars: list[DailyBar], period: int = 14) -> list[float]:
    """
    Average True Range — Wilder's smoothing。

    True Range = max(H-L, |H-C_prev|, |L-C_prev|)
    """
    n = len(bars)
    tr = [float("nan")]

    for i in range(1, n):
        h, l, c_prev = bars[i].high, bars[i].low, bars[i - 1].close
        tr.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))

    atr = [float("nan")] * period
    if n < period + 1:
        return atr + [float("nan")] * (n - period)

    # 初始平均
    atr.append(sum(tr[1:period + 1]) / period)

    # Wilder smoothing
    for i in range(period + 1, n):
        atr.append((atr[-1] * (period - 1) + tr[i]) / period)

    return atr


# ═══════════════════════════════════════════
# MA 族
# ═══════════════════════════════════════════

def calc_ma_family(closes: list[float]):
    """
    计算 MA5/10/20/60 四条均线。

    返回 dict: {"MA5": [...], "MA10": [...], "MA20": [...], "MA60": [...]}
    """
    return {
        "MA5": calc_sma(closes, 5),
        "MA10": calc_sma(closes, 10),
        "MA20": calc_sma(closes, 20),
        "MA60": calc_sma(closes, 60),
    }


# ═══════════════════════════════════════════
# 预计算全部指标 (方便批量调用)
# ═══════════════════════════════════════════

def compute_all(bars: list[DailyBar]) -> dict:
    """
    对一组日线数据预计算全部指标，返回 dict:

    {
      "closes": [...],
      "macd_dif": [...], "macd_dea": [...], "macd_hist": [...],
      "rsi": [...],
      "bb_mid": [...], "bb_upper": [...], "bb_lower": [...],
      "atr": [...],
      "ma": {"MA5": [...], "MA10": [...], "MA20": [...], "MA60": [...]},
    }
    """
    closes = [b.close for b in bars]
    dif, dea, hist = calc_macd(closes)
    rsi = calc_rsi(closes)
    bb_mid, bb_upper, bb_lower, _ = calc_bollinger(closes)
    atr = calc_atr(bars)
    ma = calc_ma_family(closes)

    return {
        "closes": closes,
        "macd_dif": dif,
        "macd_dea": dea,
        "macd_hist": hist,
        "rsi": rsi,
        "bb_mid": bb_mid,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "atr": atr,
        "ma": ma,
    }
