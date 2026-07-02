#!/usr/bin/env python3
"""验证V4: 滚动窗口验证 — 120日训练 → 20日前向测试, 滚动N次, 验证信号时间稳定性"""
import json, random
from pathlib import Path
from collections import defaultdict
from statistics import mean, stdev

KLINE_DIR = Path(__file__).resolve().parent.parent / "kline_data"
WINDOW_TRAIN = 120   # 训练窗口
WINDOW_TEST  = 20    # 测试窗口
MIN_SIGNALS  = 20    # 最少信号数
random.seed(4242)

def load_all():
    m = {}
    for fp in KLINE_DIR.glob("*.json"):
        if fp.name.startswith("_"): continue
        try:
            with open(fp) as f: d = json.load(f)
            bars = d.get("bars", [])
            if len(bars) >= 200: m[fp.stem] = bars  # 至少200根支持滚动
        except: continue
    return m

def ma(closes, p):
    r = [None]*len(closes)
    for i in range(p-1, len(closes)):
        r[i] = sum(closes[i-p+1:i+1])/p
    return r

def eval_window_with_test(full_segment, test_offset, signals_def):
    """在 full_segment 上计算指标, 只在 [test_offset, -1) 区间评估信号"""
    results = defaultdict(lambda: {"count":0,"wins":0})
    closes = [b["close"] for b in full_segment]
    opens  = [b["open"] for b in full_segment]
    highs  = [b["high"] for b in full_segment]
    lows   = [b["low"] for b in full_segment]
    vols   = [b["volume"] for b in full_segment]
    n = len(full_segment)
    ma5  = ma(closes,5); ma10 = ma(closes,10)
    ma20 = ma(closes,20); ma60 = ma(closes,60)

    # 只在测试段评估: 从 max(60, test_offset) 到 n-1
    eval_start = max(60, test_offset)
    for i in range(eval_start, n-1):
        c = closes[i]; o = opens[i]
        ret = (closes[i+1]-c)/c
        avg20v = sum(vols[i-19:i+1])/20
        vr = vols[i]/avg20v if avg20v>0 else 1.0
        if not (ma5[i] and ma10[i] and ma20[i] and ma60[i]): continue

        n60 = min(60,i+1)
        h60 = max(highs[j] for j in range(i-n60+1,i+1))
        l60_ = min(lows[j] for j in range(i-n60+1,i+1))
        pos60 = (c-l60_)/(h60-l60_) if h60>l60_ else 0.5
        align = sum([ma5[i]>ma10[i], ma10[i]>ma20[i], ma20[i]>ma60[i], c>ma5[i]])
        new_high = (i>=1 and c>=h60 and c>highs[i-1])

        for sig_name, sig_fn in signals_def:
            if sig_fn(align, pos60, vr, new_high):
                r = results[sig_name]; r["count"]+=1
                if ret>0: r["wins"]+=1
    return results

def main():
    print("V4 滚动窗口验证: 加载数据...")
    all_bars = load_all()
    print(f"  {len(all_bars)} 只 (≥200根K线)")

    # 核心信号定义
    signals_def = [
        ("新高+多头+量≤0.5x",
         lambda a,p,vr,nh: nh and a>=3 and vr<=0.5),
        ("完美多头+位≥80%+量≤0.5x",
         lambda a,p,vr,nh: a>=4 and p>=0.80 and vr<=0.5),
        ("完美多头+位≥70%+量≤0.3x",
         lambda a,p,vr,nh: a>=4 and p>=0.70 and vr<=0.3),
        ("多头≥3+位≥80%+量≤0.3x",
         lambda a,p,vr,nh: a>=3 and p>=0.80 and vr<=0.3),
        ("新高+多头+量≤1.0x",
         lambda a,p,vr,nh: nh and a>=3 and vr<=1.0),
        ("完美多头+量≤0.5x不限位",
         lambda a,p,vr,nh: a>=4 and vr<=0.5),
    ]

    # 找所有股票中最大的K线数
    max_bars = max(len(bars) for bars in all_bars.values())
    # 滚动: 从 WINDOW_TRAIN 开始, 每次前进 WINDOW_TEST
    windows = []
    start = WINDOW_TRAIN
    while start + WINDOW_TEST <= max_bars:
        windows.append((start - WINDOW_TRAIN, start, start + WINDOW_TEST))
        start += WINDOW_TEST

    print(f"  滚动窗口: {len(windows)} 个 ({WINDOW_TRAIN}训/{WINDOW_TEST}测)")

    # 每个窗口内汇总所有股票的信号
    window_results = defaultdict(lambda: defaultdict(list))  # signal → [wr1, wr2, ...]

    for wi, (train_start, split, test_end) in enumerate(windows):
        sig_window = defaultdict(lambda: {"count":0,"wins":0})
        for code, bars in all_bars.items():
            if len(bars) < test_end: continue
            # 用 train+test 段计算指标, 但只在 test 段评估信号
            full_segment = bars[train_start:test_end]  # 包含训练+测试
            test_offset = split - train_start           # 测试段在 full_segment 中的起始位置
            res = eval_window_with_test(full_segment, test_offset, signals_def)
            for sig_name, r in res.items():
                sig_window[sig_name]["count"] += r["count"]
                sig_window[sig_name]["wins"]  += r["wins"]

        for sig_name, r in sig_window.items():
            if r["count"] >= MIN_SIGNALS:
                wr = r["wins"]/r["count"]*100
                window_results[sig_name]["wrs"].append(wr)
                window_results[sig_name]["counts"].append(r["count"])

    # 输出
    print(f"\n{'='*100}")
    print(f"V4 滚动窗口验证 ({len(windows)}窗 × {WINDOW_TRAIN}训/{WINDOW_TEST}测)")
    print(f"{'='*100}")
    print(f"  {'信号':<35s} {'窗数':>5s} {'均值WR':>7s} {'最低WR':>7s} {'最高WR':>7s} {'标准差':>7s} {'判定':>6s}")
    print(f"  {'─'*35} {'─'*5} {'─'*7} {'─'*7} {'─'*7} {'─'*7} {'─'*6}")

    robust = 0; weak = 0
    for sig_name in sorted(window_results.keys()):
        data = window_results[sig_name]
        wrs = data["wrs"]
        if len(wrs) < 3: continue
        avg_wr = mean(wrs); min_wr = min(wrs); max_wr = max(wrs); std_wr = stdev(wrs)
        status = "ROBUST" if min_wr >= 60 else ("OK" if avg_wr >= 60 else "WEAK")
        if status == "ROBUST": robust += 1
        elif status == "WEAK": weak += 1
        print(f"  {sig_name:<35s} {len(wrs):>5d} {avg_wr:>6.1f}% {min_wr:>6.1f}% {max_wr:>6.1f}% {std_wr:>6.1f}% {status:>6s}")

    print(f"\n  ROBUST(所有窗口≥60%): {robust} | WEAK(均值<60%): {weak}")
    print(f"  说明: 滚动窗口模拟真实交易中'换个时间入场'的稳健性")

if __name__ == "__main__":
    main()
