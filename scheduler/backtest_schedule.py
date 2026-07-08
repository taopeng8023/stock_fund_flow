#!/usr/bin/env python3
"""
schedule_main_capital.py 逻辑回测验证 — 懒加载版
  用 research_data/fund_flow 数据预筛选 → K线信号确认 → 信号驱动退出

用法:
  python backtest_schedule.py                     # 全量
  python backtest_schedule.py --from 20260617 --to 20260703
"""
import argparse, os, sys, time
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "baostock_data", "analysis"))
sys.path.insert(0, SCRIPT_DIR)

from schedule_main_capital import (
    evaluate_entry_signal, _detect_regime,
    TARGET_BULL, TARGET_RANGE, TARGET_BEAR,
)
from stock_filter import load_main_board_files
from kline_discovery import (
    load_stock_csv, compute_indicators, pattern_signal_at, confirm_entry,
    exit_signal_at, exit_signal_confirm,
)

DAILY_DIR = os.path.join(PROJECT_ROOT, "baostock_data", "data", "daily")
RESEARCH_DIR = os.path.join(PROJECT_ROOT, "research_data")
MIN_HISTORY = 100


def get_research_dates() -> list[str]:
    if not os.path.isdir(RESEARCH_DIR):
        return []
    dates = []
    for d in sorted(os.listdir(RESEARCH_DIR)):
        dpath = os.path.join(RESEARCH_DIR, d)
        intraday = os.path.join(dpath, "intraday")
        if os.path.isdir(intraday) and any(f.startswith("fund_flow_") for f in os.listdir(intraday)):
            dates.append(d)
    return dates


def short_to_full_code(short: str) -> str:
    """短码 → 全码: 2050 → sz.002050, 600000 → sh.600000"""
    s = str(short).strip().zfill(6)
    if s.startswith(("6", "68")):
        return f"sh.{s}"
    elif s.startswith(("8", "4")):
        return f"bj.{s}"  # 北交所
    else:
        return f"sz.{s}"


def load_fund_flow_codes(date_str: str) -> set:
    """从 fund_flow 数据获取有主力资金流入的股票全码."""
    intraday = os.path.join(RESEARCH_DIR, date_str, "intraday")
    ff_files = sorted([f for f in os.listdir(intraday) if f.startswith("fund_flow_")])
    if not ff_files:
        return set()
    last_file = ff_files[-1]
    try:
        df = pd.read_csv(os.path.join(intraday, last_file))
    except Exception:
        return set()

    codes = set()
    code_col = "代码" if "代码" in df.columns else df.columns[0]
    main_col = next((c for c in df.columns if "主力净流入" in str(c)), None)
    pct_col = next((c for c in df.columns if "主力占比" in str(c)), None)

    for _, row in df.iterrows():
        short = str(row.get(code_col, "")).strip()
        if not short.isdigit():
            continue
        full = short_to_full_code(short)
        try:
            main_net = float(row.get(main_col, 0)) if main_col else 0
            main_pct = float(row.get(pct_col, 0)) if pct_col else 0
            if main_net > 0 or main_pct > 0:
                codes.add(full)
        except (ValueError, TypeError):
            if main_col is None and pct_col is None:
                codes.add(full)  # 无列名时全加入
    return codes


def find_idx(df, ds):
    target = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"
    dates = df["日期"].astype(str)
    for i in range(len(dates)):
        if dates.iloc[i] == target:
            return i
    return None


def sim_exit(df, entry_idx, entry_price, regime):
    n = len(df)
    end_idx = min(entry_idx + 200 + 1, n)
    peak = entry_price
    has_hl = "最高" in df.columns

    tp = { "bull": 0.30, "bear": 0.15 }.get(regime, 0.25)
    sl = { "bull": -0.05, "bear": -0.06 }.get(regime, -0.06)
    trail = { "bull": -0.10, "bear": -0.06 }.get(regime, -0.08)

    for j in range(entry_idx + 1, end_idx):
        c = float(df["收盘"].values[j])
        h = float(df["最高"].values[j]) if has_hl else c
        l = float(df["最低"].values[j]) if has_hl else c
        if h > peak: peak = h
        ret = (c - entry_price) / entry_price
        pr = (peak - entry_price) / entry_price

        if ret >= tp:
            return {"ret": tp * 100, "reason": "take_profit", "win": True, "hold": j - entry_idx, "peak": pr * 100}
        if (l - entry_price) / entry_price <= sl:
            return {"ret": sl * 100, "reason": "stop_loss", "win": False, "hold": j - entry_idx, "peak": pr * 100}
        if pr > 0.05 and (c - peak) / peak <= trail:
            return {"ret": ret * 100, "reason": "trailing_stop", "win": ret > 0, "hold": j - entry_idx, "peak": pr * 100}
        if j - entry_idx >= 5 and ret > 0.03:
            es = exit_signal_at(df, entry_idx, j, entry_price)
            if es and exit_signal_confirm(df, j):
                return {"ret": ret * 100, "reason": f"signal_{es}", "win": ret > 0, "hold": j - entry_idx, "peak": pr * 100}

    lc = float(df["收盘"].values[end_idx - 1])
    lr = (lc - entry_price) / entry_price
    return {"ret": lr * 100, "reason": "safety_timeout", "win": lr > 0, "hold": end_idx - 1 - entry_idx, "peak": (peak - entry_price) / entry_price * 100}


def run(dates, stock_files, top_n=10):
    """懒加载回测: fund_flow预筛选 → K线按需计算."""
    all_trades = []
    n_files = len(stock_files)

    # 预加载原始CSV (不计算指标, 快)
    print(f"📊 加载 {n_files} 只个股原始数据...")
    raw_data = {}
    for i, fp in enumerate(stock_files):
        df = load_stock_csv(fp)
        if df is not None and len(df) >= MIN_HISTORY:
            code = os.path.splitext(os.path.basename(fp))[0]
            raw_data[code] = df
        if (i + 1) % 500 == 0:
            print(f"  加载: {i+1}/{n_files}", flush=True)
    print(f"  有效: {len(raw_data)} 只", flush=True)

    for di, date_str in enumerate(dates):
        regime = _detect_regime()

        # 1. fund_flow 预筛选 (主力资金流入的股票)
        ff_codes = load_fund_flow_codes(date_str)
        candidate_codes = ff_codes & set(raw_data.keys())
        if not candidate_codes:
            print(f"  [{di+1}/{len(dates)}] {date_str} fund_flow无有效候选", flush=True)
            continue

        # 2. 按需计算指标 + 检测信号
        candidates = []
        for code in candidate_codes:
            df = raw_data[code]
            idx = find_idx(df, date_str)
            if idx is None or idx < 70 or idx >= len(df) - 1:
                continue

            c = float(df["收盘"].values[idx])
            v = float(df["成交量"].values[idx])
            if c < 5 or v == 0:
                continue

            # 懒加载: 只在有候选时计算指标
            if "ma5" not in df.columns:
                df = compute_indicators(df)
                raw_data[code] = df

            pattern = pattern_signal_at(df, idx)
            if pattern is None:
                continue
            if not confirm_entry(df, idx, strict=True):
                continue

            # 3日内信号窗口
            signals = []
            for si in range(max(70, idx - 3), idx + 1):
                p = pattern_signal_at(df, si)
                if p:
                    signals.append(p)

            ev = evaluate_entry_signal(signals, 1.5, regime)  # 量比默认1.5
            if ev["level"] in ("skip",):
                continue

            candidates.append({
                "code": code, "name": df["名称"].iloc[0] if "名称" in df.columns else code,
                "price": c, "patterns": signals, "eval": ev,
            })

        # 3. 排序取 top N
        candidates.sort(key=lambda x: x["eval"]["score"], reverse=True)
        picks = candidates[:top_n]

        # 4. 模拟退出
        for pick in picks:
            code = pick["code"]
            df = raw_data[code]
            entry_idx = find_idx(df, date_str)
            if entry_idx is None:
                continue
            if "ma20" not in df.columns:  # 确保指标已计算
                df = compute_indicators(df)
                raw_data[code] = df

            er = sim_exit(df, entry_idx, pick["price"], regime)
            all_trades.append({
                "date": date_str, "code": code, "name": pick["name"],
                "entry_price": pick["price"],
                "regime": regime,
                "patterns": pick["patterns"],
                "eval_score": pick["eval"]["score"],
                "eval_level": pick["eval"]["level"],
                "exit_reason": er["reason"],
                "return_pct": round(er["ret"], 2),
                "peak_return": round(er["peak"], 2),
                "hold_days": er["hold"],
                "win": er["win"],
            })

        if (di + 1) % 3 == 0 or di == len(dates) - 1:
            if all_trades:
                recent = all_trades[-200:]
                wr = sum(1 for t in recent if t["win"]) / len(recent) * 100
                avg = np.mean([t["return_pct"] for t in recent])
            else:
                wr = avg = 0
            print(f"  [{di+1}/{len(dates)}] {date_str} {len(picks)}只 "
                  f"| 累计{len(all_trades)}笔 | WR={wr:.1f}% avg={avg:+.2f}%", flush=True)

    return all_trades


def analyze(trades):
    if not trades:
        print("无交易"); return
    n = len(trades)
    rets = [t["return_pct"] for t in trades]
    w = sum(1 for t in trades if t["win"])
    h20 = sum(1 for r in rets if r >= 20)
    h30 = sum(1 for r in rets if r >= 30)
    w_rets = [r for r in rets if r > 0]; l_rets = [abs(r) for r in rets if r <= 0]
    pf = sum(w_rets)/sum(l_rets) if l_rets and sum(l_rets) > 0 else 0

    print(f"\n{'='*60}")
    print(f"  📊 schedule_main_capital.py 回测: {n}笔")
    print(f"{'='*60}")
    print(f"  胜率: {w/n*100:.1f}% | 均值: {np.mean(rets):+.2f}% | 中位数: {np.median(rets):+.2f}%")
    print(f"  20%+: {h20}次({h20/n*100:.1f}%) | 30%+: {h30}次({h30/n*100:.1f}%)")
    print(f"  峰值: {np.mean([t['peak_return'] for t in trades]):+.1f}% | "
          f"持仓: {np.mean([t['hold_days'] for t in trades]):.1f}d | 盈亏比: {pf:.2f}")

    print(f"\n  ── 按评分 ──")
    for lv in ["strong_buy", "buy", "watch"]:
        lt = [t for t in trades if t["eval_level"] == lv]
        if len(lt) < 2: continue
        lr = [t["return_pct"] for t in lt]
        print(f"  {lv:<12s} N={len(lt):>3d} WR={sum(1 for t in lt if t['win'])/len(lt)*100:>5.1f}% "
              f"avg={np.mean(lr):>+5.2f}% 20%+={sum(1 for r in lr if r>=20)/len(lt)*100:>4.1f}%")

    print(f"\n  ── 按体制 ──")
    for reg in ["bull", "range", "bear"]:
        rt = [t for t in trades if t["regime"] == reg]
        if len(rt) < 2: continue
        rr = [t["return_pct"] for t in rt]
        print(f"  {reg:<8s} N={len(rt):>3d} WR={sum(1 for t in rt if t['win'])/len(rt)*100:>5.1f}% "
              f"avg={np.mean(rr):>+5.2f}%")

    print(f"\n  ── 出场 ──")
    ed = defaultdict(list)
    for t in trades: ed[t["exit_reason"]].append(t["return_pct"])
    for r, rs in sorted(ed.items(), key=lambda x: np.mean(x[1]), reverse=True):
        if len(rs) < 2: continue
        print(f"  {r:<35s} N={len(rs):>3d} avg={np.mean(rs):>+5.2f}%")


def main():
    p = argparse.ArgumentParser(description="schedule_main_capital.py 逻辑回测")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--from", dest="fd", default="")
    p.add_argument("--to", dest="td", default="")
    args = p.parse_args()

    dates = get_research_dates()
    if args.fd: dates = [d for d in dates if d >= args.fd]
    if args.td: dates = [d for d in dates if d <= args.td]
    if not dates:
        print("❌ 无数据"); return

    print(f"📂 {len(dates)} 个交易日: {dates[0]} → {dates[-1]}")

    files = load_main_board_files(DAILY_DIR)
    print(f"📊 主板个股文件: {len(files)}")

    t0 = time.time()
    trades = run(dates, files, top_n=args.top)
    analyze(trades)
    print(f"\n  耗时: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
