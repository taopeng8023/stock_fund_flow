#!/usr/bin/env python3
"""全量主板个股回测 — 信号驱动退出"""
import sys, os, time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
sys.path.insert(0, SCRIPT_DIR)

from buy_sell_backtest import BuySellBacktest
from stock_filter import load_main_board_files
from kline_discovery import load_stock_csv
import numpy as np

data_dir = os.path.join(PROJECT_ROOT, 'baostock_data', 'data', 'daily')
files = load_main_board_files(data_dir)
total_n = len(files)

print(f'主板个股总数: {total_n}')
print(f'参数: 止盈30% 止损6% 移动止损8% 信号驱动(兜底200日)')
print(f'退出优先级: 止盈 → K线信号 → 移动止损 → 止损 → 安全兜底')
print(f'预计耗时: ~{total_n * 3.5 / 60:.0f} 分钟')
print(flush=True)

bt = BuySellBacktest(
    take_profit=0.30, stop_loss=-0.06,
    trailing=-0.08, max_hold=200,
    label='signal_driven'
)
t0 = time.time()
total_sig = 0
skipped = 0
done = 0

for i, fpath in enumerate(files):
    df = load_stock_csv(fpath)
    if df is None or len(df) < 200:
        skipped += 1
        continue
    try:
        total_sig += bt.run_stock(fpath, df)
    except Exception:
        skipped += 1
        continue
    done += 1

    if (i + 1) % 100 == 0:
        e = time.time() - t0
        rate = (i + 1) / e if e > 0 else 1
        eta = (total_n - i - 1) / rate if rate > 0 else 0
        print(f'[{i+1}/{total_n}] {total_sig}笔交易 | 跳过{skipped} | {e:.0f}s | ETA {eta:.0f}s', flush=True)

elapsed = time.time() - t0
s = bt.summary()

print(f'\n{"="*60}')
print(f'  全量回测完成 ({done}只有效个股, {elapsed:.0f}s)')
print(f'{"="*60}')
print(f'交易: {s["total_trades"]}笔 | 胜率: {s["win_rate"]}% | 均值: {s["avg_return"]}%')
print(f'20%+: {s["hit_20pct_n"]}次({s["hit_20pct"]}%) | 10%+: {s["hit_10pct_n"]}次({s["hit_10pct"]}%)')
print(f'峰值: {s["avg_peak_return"]}% | 夏普: {s["sharpe"]} | 盈亏比: {s["profit_factor"]}')
if s["total_trades"] > 0:
    hit_30 = sum(1 for t in bt.trades if t["return_pct"] >= 30.0)
    hit_50 = sum(1 for t in bt.trades if t["return_pct"] >= 50.0)
    print(f'30%+大赢家: {hit_30}次({round(hit_30/s["total_trades"]*100,1)}%) | 50%+: {hit_50}次({round(hit_50/s["total_trades"]*100,1)}%)')
print(f'最大连亏: {s["max_loss_streak"]} | 持仓: {s["avg_hold_days"]}d')
print(f'退出: {s["reason_dist"]}')
print(f'过滤: 熊市{s["bear_filtered"]} 量质{s["quality_filtered"]}')

ps = bt.pattern_summary(5)
if not ps.empty:
    print(f'\n{"="*60}')
    print(f'Top 15 信号组合 (按20%+命中率)')
    print(f'{"="*60}')
    for _, row in ps.head(15).iterrows():
        print(f'  {row["pattern"][:55]:<55s} N={row["n"]:>4d} WR={row["wr"]:>5.1f}% 20%+={row["hit_20pct"]:>5.1f}% Peak={row["avg_peak"]:>+5.1f}%')

out_dir = bt.save_trades()
print(f'\n交易记录: {out_dir}')
