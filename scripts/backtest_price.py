"""
价格因子 1年回测 — 基于腾讯财经K线API
用法:
  python scripts/backtest_price.py                       默认50只随机股, 250天
  python scripts/backtest_price.py --stocks=100 --days=250
  python scripts/backtest_price.py --universe=hs300      沪深300成分股
"""
import urllib.request
import json
import random
import math
import statistics
import time
import sys
import os

# ── 工具函数 ──
def sf(val, default=0.0):
    if val is None or val == "" or val == "-":
        return default
    try: return float(val)
    except: return default

# ── K线获取 ──
def fetch_kline(code, days=250):
    """腾讯财经K线API（前复权日线），返回 [{date, open, close, high, low, vol}, ...]"""
    mkt = "sh" if code.startswith("6") else "sz"
    url = (f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={mkt}{code},day,,,{days},qfq")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        key = f"{mkt}{code}"
        if data.get("data") and data["data"].get(key) and data["data"][key].get("qfqday"):
            return [
                {
                    "date": k[0],
                    "open": float(k[1]),
                    "close": float(k[2]),
                    "high": float(k[3]),
                    "low": float(k[4]),
                    "vol": float(k[5]),
                }
                for k in data["data"][key]["qfqday"]
            ]
    except Exception as e:
        print(f"  ⚠ {code}: {e}")
    return None


# ── 评分函数 ──
def price_score(klines, idx):
    """基于60日K线窗口计算价格因子得分（0~1），模拟趋势+位置+技术面维度"""
    if idx < 60:
        return 0.5
    recent = klines[max(0, idx - 60) : idx + 1]
    today = recent[-1]
    close = today["close"]
    vol = today["vol"]

    score = 0.0

    # 短期动量（趋势维度 ~10%）
    if len(recent) >= 6:
        ret_5d = (close - recent[-6]["close"]) / recent[-6]["close"] * 100
        if 2 <= ret_5d <= 10:
            score += 0.10
        elif 0 < ret_5d < 2:
            score += 0.05

    # 量比（趋势维度 ~5%）
    if len(recent) >= 6:
        avg_vol = sum(d["vol"] for d in recent[-6:-1]) / 5
        vr = vol / avg_vol if avg_vol > 0 else 1
        if 1.5 <= vr <= 4.0:
            score += 0.05

    # 60日价格位置（位置维度 ~7%）
    high_60 = max(d["high"] for d in recent)
    low_60 = min(d["low"] for d in recent)
    if high_60 > low_60:
        pos = (close - low_60) / (high_60 - low_60)
        if 0.25 <= pos <= 0.65:
            score += 0.07       # 中位区最优
        elif 0.10 <= pos < 0.25:
            score += 0.04       # 低位区次优
        elif 0.65 < pos <= 0.85:
            score += 0.03       # 偏高位
        elif pos > 0.85:
            score -= 0.02       # 接近新高，回调风险

    # MA排列（技术面维度 ~4%）
    if len(recent) >= 20:
        mas = [sum(d["close"] for d in recent[-n:]) / n for n in (5, 10, 20)]
        align = sum([mas[0] > mas[1], mas[1] > mas[2], mas[0] > mas[2], close > mas[0]]) / 4
        score += 0.04 * align

    # 中期动量
    if len(recent) >= 11:
        ret_10d = (close - recent[-11]["close"]) / recent[-11]["close"] * 100
        if 3 <= ret_10d <= 15:
            score += 0.04
        elif ret_10d > 15:
            score -= 0.02
    if len(recent) >= 21:
        ret_20d = (close - recent[-21]["close"]) / recent[-21]["close"] * 100
        if 5 <= ret_20d <= 25:
            score += 0.03

    # 振幅洗盘
    if today["high"] > today["low"]:
        amp = (today["high"] - today["low"]) / close * 100
        if 5 <= amp <= 12:
            # 计算当日涨跌幅
            if idx > 0 and klines[idx - 1]["close"] > 0:
                chg = (close - klines[idx - 1]["close"]) / klines[idx - 1]["close"] * 100
            else:
                chg = 0
            if chg > 1:
                score += 0.04

    # 波动率惩罚
    if len(recent) >= 20:
        rets = [
            (recent[i]["close"] - recent[i - 1]["close"]) / recent[i - 1]["close"] * 100
            for i in range(1, min(21, len(recent)))
        ]
        if rets and statistics.stdev(rets) > 3.5:
            score -= 0.03

    return max(0.0, min(1.0, score))


# ── 股票池 ──
def build_universe(source="random", size=50):
    """构建股票池。source: random | hs300 | file:<path>"""
    if source.startswith("file:"):
        filepath = source[5:]
        with open(filepath) as f:
            codes = [line.strip() for line in f if line.strip()]
        return random.sample(codes, min(size, len(codes)))

    # 从最近的 fund_flow 文件读取股票列表
    data_root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    )
    date_dirs = sorted(
        [d for d in os.listdir(data_root) if os.path.isdir(os.path.join(data_root, d)) and d.isdigit()],
        reverse=True,
    )
    fund_path = None
    for d in date_dirs:
        p = os.path.join(data_root, d, "fund_flow.json")
        if os.path.exists(p):
            fund_path = p
            break

    if not fund_path:
        print("未找到 fund_flow.json，请先运行数据采集")
        return []

    with open(fund_path) as f:
        fund_data = json.load(f)

    codes = [
        s.get("f12", "")
        for s in fund_data
        if 50 < sf(s.get("f20")) / 1e8 < 2000
        and s.get("f12", "").startswith(
            ("000", "002", "300", "600", "601", "603", "688")
        )
    ]

    if source == "hs300":
        # 取市值最大的300只
        stocks = [
            (s.get("f12", ""), sf(s.get("f20")) / 1e8)
            for s in fund_data
            if s.get("f12", "").startswith(
                ("000", "002", "300", "600", "601", "603", "688")
            )
        ]
        stocks.sort(key=lambda x: -x[1])
        codes = [c for c, _ in stocks[:300]]

    return random.sample(codes, min(size, len(codes)))


# ── 主流程 ──
def run(stocks=50, days=250, source="random", top_n=10):
    print(f"价格因子回测")
    print(f"  股票池: {source}  目标: {stocks}只  回溯: {days}天  Top: {top_n}")
    print()

    # 1. 选股
    selected = build_universe(source, stocks)
    if not selected:
        return
    random.seed(42)
    print(f"选取 {len(selected)} 只")

    # 2. 获取K线
    print("获取K线（腾讯API）...")
    klines = {}
    for i, code in enumerate(selected):
        d = fetch_kline(code, days)
        if d and len(d) >= 120:
            # 计算涨跌幅
            for j in range(len(d)):
                if j > 0 and d[j - 1]["close"] > 0:
                    d[j]["chg_pct"] = (
                        (d[j]["close"] - d[j - 1]["close"]) / d[j - 1]["close"] * 100
                    )
                else:
                    d[j]["chg_pct"] = 0.0
            klines[code] = d
        if (i + 1) % 20 == 0:
            print(f"  {i + 1}/{len(selected)} ({len(klines)}只)")
        time.sleep(0.05)

    print(f"成功: {len(klines)} 只\n")
    if len(klines) < 10:
        print("样本不足，终止")
        return

    # 3. 对齐交易日
    common = None
    for kl in klines.values():
        d = set(k["date"] for k in kl)
        common = common & d if common else d
    dates = sorted(common)[60:]  # 60天预热
    print(f"对齐后交易日: {len(dates)} 天")

    # 4. 滚动回测
    daily_rets, mkt_rets = [], []
    for di in range(len(dates) - 1):
        dt, dn = dates[di], dates[di + 1]
        scores = []
        for code, kl in klines.items():
            idx = next(i for i, k in enumerate(kl) if k["date"] == dt)
            s = price_score(kl, idx)
            nidx = next(i for i, k in enumerate(kl) if k["date"] == dn)
            scores.append({"score": s, "next_ret": kl[nidx]["chg_pct"]})
        scores.sort(key=lambda x: -x["score"])
        daily_rets.append(statistics.mean(s["next_ret"] for s in scores[:top_n]))
        mkt_rets.append(statistics.mean(s["next_ret"] for s in scores))

        if (di + 1) % 40 == 0:
            cm = math.prod(1 + r / 100 for r in daily_rets)
            ck = math.prod(1 + r / 100 for r in mkt_rets)
            wr = sum(1 for r in daily_rets if r > 0) / len(daily_rets) * 100
            print(f"  {di + 1}/{len(dates) - 1}d  模型{cm - 1:+.1%}  市场{ck - 1:+.1%}  WR{wr:.0f}%")

    # 5. 结果
    cm = math.prod(1 + r / 100 for r in daily_rets)
    ck = math.prod(1 + r / 100 for r in mkt_rets)
    wins = sum(1 for r in daily_rets if r > 0)
    avg = statistics.mean(daily_rets)
    std = statistics.stdev(daily_rets) if len(daily_rets) > 1 else 0
    sharpe = avg / std * math.sqrt(250) if std > 0 else 0

    peak = 1.0
    max_dd = 0.0
    for r in daily_rets:
        peak = max(peak, 1 + r / 100)
        max_dd = min(max_dd, (1 + r / 100) / peak - 1)

    print(f"\n{'=' * 60}")
    print(f"  回测结果: {len(klines)}只, {len(daily_rets)}天, Top{top_n}")
    print(f"{'=' * 60}")
    print(f"  模型累计: {cm - 1:+.2%}")
    print(f"  市场均值: {ck - 1:+.2%}")
    print(f"  超额收益: {cm / ck - 1:+.2%}")
    print(f"  日均收益: {avg:+.3f}%")
    print(f"  胜率:     {wins}/{len(daily_rets)} ({wins / len(daily_rets) * 100:.1f}%)")
    print(f"  夏普:     {sharpe:+.2f}")
    print(f"  最大回撤: {max_dd:+.2%}")

    # 月度
    mon = {}
    for i, r in enumerate(daily_rets):
        m = dates[i][:7]
        mon.setdefault(m, []).append(r)
    print(f"\n  月度收益:")
    for m in sorted(mon)[-14:]:
        mc = math.prod(1 + r / 100 for r in mon[m]) - 1
        bar = "█" * int(abs(mc) * 50) if mc > 0 else "▁" * int(abs(mc) * 50)
        print(f"    {m}: {mc:>+6.2%}  {bar}")

    # 十分位
    all_s = []
    for di in range(len(dates) - 1):
        dt, dn = dates[di], dates[di + 1]
        for code, kl in klines.items():
            idx = next(i for i, k in enumerate(kl) if k["date"] == dt)
            s = price_score(kl, idx)
            nidx = next(i for i, k in enumerate(kl) if k["date"] == dn)
            all_s.append({"score": s, "next_ret": kl[nidx]["chg_pct"]})
    all_s.sort(key=lambda x: -x["score"])
    dn_val = len(all_s) // 10
    print(f"\n  十分位 (全期 {len(all_s)} 条):")
    for i in range(10):
        g = all_s[i * dn_val : (i + 1) * dn_val if i < 9 else len(all_s)]
        rets = [x["next_ret"] for x in g]
        avg_r = statistics.mean(rets)
        wr = sum(1 for r in rets if r > 0) / len(rets)
        bar = "█" * int(max(0, avg_r) * 40) if avg_r > 0 else "▁" * int(abs(avg_r) * 40)
        print(f"    D{i + 1}: {avg_r:>+6.3f}%  WR={wr:.1%}  {bar}")


if __name__ == "__main__":
    stocks = 50
    days = 250
    source = "random"
    top_n = 10

    for arg in sys.argv:
        if arg.startswith("--stocks="):
            stocks = int(arg.split("=")[1])
        if arg.startswith("--days="):
            days = int(arg.split("=")[1])
        if arg.startswith("--universe="):
            source = arg.split("=")[1]
        if arg.startswith("--top="):
            top_n = int(arg.split("=")[1])
        if arg == "--hs300":
            source = "hs300"

    run(stocks=stocks, days=days, source=source, top_n=top_n)
