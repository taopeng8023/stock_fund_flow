#!/usr/bin/env python3
"""
历史 K 线全量回测 — 对选股策略做跨年度验证。

原理:
  遍历历史交易日，扫描当日信号 → 跟踪次日收盘收益 → 累积统计

用法:
  python historical_backtest.py --from 20240101 --to 20260701
  python historical_backtest.py --from 20240101 --to 20260701 --step 5  # 每5天采样
  python historical_backtest.py --from 20200101 --to 20260701 --step 10 --top 10
"""
import argparse
import os
import statistics
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DAILY_DIR = os.path.join(PROJECT_ROOT, "baostock_data", "data", "daily")

try:
    from stock_filter import load_main_board_files, print_filter_summary
except ImportError:
    from baostock_data.analysis.stock_filter import load_main_board_files, print_filter_summary

from signal_scanner import (
    compute_indicators, PATTERN_DETECTORS, PATTERN_SIGNALS,
    PRICE_VOL_SIGNALS, check_price_vol_signal, MIN_DAYS, MIN_WR_TARGET,
    MIN_TURNOVER_YI, MIN_PRICE, MAX_POS_FOR_ENTRY,
)

try:
    from result_store import save_results
    HAS_RESULT_STORE = True
except ImportError:
    HAS_RESULT_STORE = False

BJS_TZ = None  # not needed for historical


def find_trading_dates(from_date: str, to_date: str,
                       step: int = 1) -> list[str]:
    """从 K 线数据中找可交易日期列表。"""
    ref_file = os.path.join(DAILY_DIR, "sh.600000.csv")
    if not os.path.exists(ref_file):
        print(f"参考文件不存在: {ref_file}"); return []

    df = pd.read_csv(ref_file)
    df["日期"] = pd.to_datetime(df["日期"], format="%Y-%m-%d")
    df = df.sort_values("日期")

    from_dt = datetime.strptime(from_date, "%Y%m%d")
    to_dt = datetime.strptime(to_date, "%Y%m%d")

    dates = []
    for _, row in df.iterrows():
        d = row["日期"]
        if from_dt <= d <= to_dt:
            dates.append(d.strftime("%Y%m%d"))

    return dates[::step]


def scan_date(code: str, df: pd.DataFrame, idx: int) -> list[dict]:
    """扫描单只股票在指定日期的信号。"""
    signals = []
    # 形态信号
    for pname, (detector, hold) in PATTERN_DETECTORS.items():
        try:
            if detector(df, idx):
                wr_ref = PATTERN_SIGNALS.get(pname, (hold, 0, 0))[1]
                signals.append({
                    "type": "pattern", "name": pname,
                    "hold": hold, "wr": wr_ref,
                })
        except Exception:
            continue

    # 涨跌量价信号
    for sig_name, sig_hold, sig_wr in check_price_vol_signal(df, idx):
        signals.append({
            "type": "pv", "name": sig_name,
            "hold": sig_hold, "wr": sig_wr,
        })

    return signals


def get_forward_return(df: pd.DataFrame, idx: int, days: int = 1) -> float | None:
    """获取 idx 之后 days 个交易日的收益率。"""
    if idx + days >= len(df):
        return None
    entry = df["收盘"].values[idx]
    exit_p = df["收盘"].values[idx + days]
    if entry <= 0:
        return None
    return (exit_p - entry) / entry


def run_backtest(from_date: str, to_date: str, step: int = 5,
                 top_n: int = 10, min_consensus: int = 1):
    """历史 K 线全量回测。"""
    # ── 确认日模式 ──
    CONFIRMATION_MODE = False  # T日触发, T+1确认后入场
    HOLD_DAYS = 1             # 确认后的持仓天数

    print("═" * 70)
    print(f"  历史K线回测: {from_date} → {to_date}  (步长: {step}天)")
    print(f"  约束: 主板 | WR≥{MIN_WR_TARGET:.0f}% | 共识≥{min_consensus} | Top{top_n}")
    if CONFIRMATION_MODE:
        print(f"  模式: 确认日 — T日触发 → T+1确认(收阳+放量) → T+1收盘入场")
    print("═" * 70)
    print()

    # 1. 加载主板个股
    stock_files = load_main_board_files(DAILY_DIR)
    print_filter_summary(DAILY_DIR, main_board_only=True)

    # 2. 找交易日
    all_dates = find_trading_dates(from_date, to_date, step)
    print(f"回测日期: {len(all_dates)} 天 (每{step}天采样)")
    print()

    if not all_dates:
        print("无交易日"); return

    # 3. 预加载 + 预计算所有股票的指标（一次加载）
    print("预加载K线数据 + 计算指标...")
    stock_data = {}
    processed = 0
    for fp in stock_files:
        try:
            df = pd.read_csv(fp)
            if len(df) < MIN_DAYS:
                continue
            df["日期"] = pd.to_datetime(df["日期"], format="%Y-%m-%d")
            df = df.sort_values("日期").reset_index(drop=True)
            df = df[df["成交量"] > 0].copy()
            if len(df) < MIN_DAYS:
                continue

            code = os.path.splitext(os.path.basename(fp))[0]
            name = str(df["名称"].iloc[0]) if "名称" in df.columns else code

            df = compute_indicators(df)
            stock_data[code] = (name, df)
            processed += 1

            if processed % 500 == 0:
                print(f"  {processed}/{len(stock_files)}", flush=True)
        except Exception:
            continue
    print(f"  加载完成: {processed} 只有效股票\n")

    # 3.5 技术面评分函数（fallback：无形态信号时的排名依据）
    def tech_score(df, idx):
        """纯技术面评分：MA多头 + 位置健康 + 放量 + 趋势。0-100分。"""
        try:
            c = df["收盘"].values
            pos = df["pos_60"].values[idx]
            ma_b = bool(df["ma_bull"].values[idx])
            vol_r = df["vol_ratio"].values[idx]
            up_s = df["up_streak"].values[idx]
            chg = df["pct_chg"].values[idx] * 100

            s = 0
            if ma_b: s += 30
            if 0.2 < pos < 0.8: s += 15  # 中间位置
            if pos > 0.5: s += 10  # 趋势向上
            if vol_r > 1.2: s += 10  # 放量
            if up_s >= 3: s += 10  # 连续上涨
            if not pd.isna(chg) and 0 < chg < 9: s += 10  # 温和上涨
            if vol_r > 0.5: s += 5  # 非僵尸股
            return s
        except Exception:
            return 0

    # 4. 逐日扫描 + 跟踪收益
    all_trades = []  # [{date, code, name, price, signals, next_ret, win}]
    daily_stats = []  # [{date, n_candidates, wr, avg_ret}]

    for di, date_str in enumerate(all_dates):
        day_candidates = []

        for code, (name, df) in stock_data.items():
            # 找目标日期索引
            idx = None
            kline_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            for j in range(len(df) - 1, max(len(df) - 20, -1), -1):
                d = str(df["日期"].iloc[j].date())
                if d in (date_str, kline_date):
                    idx = j
                    break
            if idx is None or idx < MIN_DAYS:
                continue

            c = float(df["收盘"].values[idx])
            pos_60 = float(df["pos_60"].values[idx]) if not pd.isna(df["pos_60"].values[idx]) else 0.5
            ma_bull = bool(df["ma_bull"].values[idx])

            # ── 质量过滤 ──
            v = float(df["成交量"].values[idx])
            turnover_yi = v * c / 1e8  # 成交额(亿)
            if turnover_yi < MIN_TURNOVER_YI:  # 流动性不足
                continue
            if c < MIN_PRICE:  # 准仙股
                continue
            if pos_60 > MAX_POS_FOR_ENTRY:  # 追高
                continue

            t_score = tech_score(df, idx)

            # 检测形态信号
            signals = scan_date(code, df, idx)
            all_wr = [s["wr"] for s in signals]
            max_wr = max(all_wr) if all_wr else 0
            has_signal = len(signals) >= min_consensus and max_wr >= MIN_WR_TARGET

            # 综合评分 = 技术面分+(形态加分)
            if has_signal:
                pattern_wr = [s["wr"] for s in signals if s["type"] == "pattern"]
                pv_wr = [s["wr"] for s in signals if s["type"] == "pv"]
                p_score = sum(pattern_wr) / max(len(pattern_wr), 1)
                pv_score = sum(pv_wr) / max(len(pv_wr), 1) if pv_wr else 0
                signal_bonus = p_score * 0.3 + pv_score * 0.2 if pv_wr else p_score * 0.5
                signal_bonus += min(len(signals) * 5, 20)
            else:
                signal_bonus = 0

            final_score = t_score + signal_bonus

            day_candidates.append({
                "code": code, "name": name, "price": c,
                "score": final_score, "tech_score": t_score,
                "n_signals": len(signals),
                "has_signal": has_signal,
                "pos_60": pos_60, "ma_bull": ma_bull,
                "pattern_names": [s["name"] for s in signals if s["type"] == "pattern"],
                "pv_names": [s["name"] for s in signals if s["type"] == "pv"],
                "best_wr": max_wr,
            })

        # 排序取 Top N
        # 排序取 Top N
        day_candidates.sort(key=lambda x: -x["score"])
        day_picks = day_candidates[:top_n]

        # 跟踪次日收益
        for pick in day_picks:
            code = pick["code"]
            _, df = stock_data.get(code, (None, None))
            if df is None:
                continue
            kline_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            idx = None
            for j in range(len(df) - 1, max(len(df) - 20, -1), -1):
                if str(df["日期"].iloc[j].date()) in (date_str, kline_date):
                    idx = j
                    break
            if idx is None:
                continue

            # -- 确认日机制 --
            if CONFIRMATION_MODE:
                if idx + 1 >= len(df):
                    continue
                t1_open = float(df["开盘"].values[idx + 1])
                t1_close = float(df["收盘"].values[idx + 1])
                t1_vol = float(df["成交量"].values[idx + 1])
                t0_vol = float(df["成交量"].values[idx])
                if not (t1_close > t1_open and t1_vol > t0_vol * 0.7):
                    continue
                entry_price = t1_close
                entry_idx = idx + 1
            else:
                entry_price = pick["price"]
                entry_idx = idx

            ret_1 = get_forward_return(df, entry_idx, 1)
            if ret_1 is None:
                continue

            ret_2 = get_forward_return(df, entry_idx, 2)
            ret_3 = get_forward_return(df, entry_idx, 3)
            ret_5 = get_forward_return(df, entry_idx, 5)

            all_trades.append({
                "date": date_str, "code": code, "name": pick["name"],
                "price": round(entry_price, 2),
                "signal_price": pick["price"],
                "score": round(pick["score"], 1),
                "n_signals": pick["n_signals"],
                "has_signal": pick["has_signal"],
                "confirmed": CONFIRMATION_MODE,
                "ret_1d": round(ret_1 * 100, 2),
                "ret_2d": round(ret_2 * 100, 2) if ret_2 is not None else None,
                "ret_3d": round(ret_3 * 100, 2) if ret_3 is not None else None,
                "ret_5d": round(ret_5 * 100, 2) if ret_5 is not None else None,
                "next_return": round(ret_1 * 100, 2),
                "win": ret_1 > 0,
                "best_wr": pick["best_wr"],
            })

        if day_picks:
            day_rets = [t["next_return"] for t in all_trades[-len(day_picks):]
                        if t["date"] == date_str]
            if day_rets:
                dwr = sum(1 for r in day_rets if r > 0) / len(day_rets) * 100
                daily_stats.append({
                    "date": date_str,
                    "n": len(day_rets),
                    "wr": round(dwr, 1),
                    "avg_ret": round(statistics.mean(day_rets), 2),
                })

        if (di + 1) % 50 == 0:
            recent = all_trades[-1000:]
            rwr = sum(1 for t in recent if t["win"]) / len(recent) * 100 if recent else 0
            ravg = statistics.mean(t["next_return"] for t in recent) if recent else 0
            print(f"  {di+1}/{len(all_dates)} {date_str}: "
                  f"日选{len(day_picks)}只  累计{len(all_trades)}笔  "
                  f"滚动WR={rwr:.1f}%  滚动均收={ravg:+.2f}%", flush=True)

    # 5. 统计输出
    if not all_trades:
        print("\n  无交易信号，无法统计")
        return

    n_trades = len(all_trades)
    wins = sum(1 for t in all_trades if t["win"])
    win_rate = wins / n_trades * 100
    avg_ret = statistics.mean(t["next_return"] for t in all_trades)
    median_ret = statistics.median(t["next_return"] for t in all_trades)
    rets = [t["next_return"] for t in all_trades]

    # 按年统计
    yearly = defaultdict(list)
    for t in all_trades:
        year = t["date"][:4]
        yearly[year].append(t["next_return"])

    print(f"\n{'═' * 70}")
    print(f"  回测结果汇总")
    print(f"{'═' * 70}")
    print(f"  回测区间: {from_date} → {to_date}")
    print(f"  交易天数: {len(daily_stats)} | 采样步长: {step}天")
    print(f"  总交易笔数: {n_trades}")
    print(f"  胜率: {win_rate:.1f}%")
    print(f"  均收益: {avg_ret:+.2f}%")
    print(f"  中位收益: {median_ret:+.2f}%")
    print(f"  标准差: {statistics.stdev(rets):.2f}%" if len(rets) > 1 else "")
    print(f"  最大单笔收益: {max(rets):+.2f}%")
    print(f"  最大单笔亏损: {min(rets):+.2f}%")

    # 按年统计
    print(f"\n  {'年份':<6} {'笔数':>5} {'胜率':>7} {'均收益':>8} {'中位':>8} {'信号笔':>6}")
    print(f"  {'─'*45}")
    for year in sorted(yearly.keys()):
        yr = yearly[year]
        yr_wr = sum(1 for r in yr if r > 0) / len(yr) * 100
        yr_avg = statistics.mean(yr)
        yr_med = statistics.median(yr)
        yr_sig = sum(1 for t in all_trades if t["date"][:4] == year and t.get("has_signal"))
        print(f"  {year:<6} {len(yr):>5} {yr_wr:>6.1f}% {yr_avg:>+7.2f}% {yr_med:>+7.2f}% {yr_sig:>6}")

    # 多周期统计
    print(f"\n  {'周期':<6} {'笔数':>5} {'胜率':>7} {'均收益':>8} {'中位':>8}")
    print(f"  {'─'*35}")
    for period, key in [("T+1", "ret_1d"), ("T+2", "ret_2d"), ("T+3", "ret_3d"), ("T+5", "ret_5d")]:
        vals = [t[key] for t in all_trades if t.get(key) is not None]
        if vals:
            wr = sum(1 for v in vals if v > 0) / len(vals) * 100
            avg = statistics.mean(vals)
            med = statistics.median(vals)
            print(f"  {period:<6} {len(vals):>5} {wr:>6.1f}% {avg:>+7.2f}% {med:>+7.2f}%")

    # 月度统计
    monthly = defaultdict(list)
    for t in all_trades:
        month = t["date"][:6]
        monthly[month].append(t["next_return"])
    month_wrs = [(m, sum(1 for r in rs if r > 0) / len(rs) * 100, statistics.mean(rs))
                 for m, rs in sorted(monthly.items())]
    positive_months = sum(1 for _, _, avg in month_wrs if avg > 0)
    print(f"\n  盈利月份: {positive_months}/{len(month_wrs)} "
          f"({positive_months/len(month_wrs)*100:.0f}%)")
    print(f"\n  {'月份':<8} {'笔数':>5} {'胜率':>7} {'均收益':>8}")
    print(f"  {'─'*30}")
    for m, wr, avg in month_wrs[-12:]:
        cnt = len(monthly[m])
        print(f"  {m:<8} {cnt:>5} {wr:>6.1f}% {avg:>+7.2f}%")

    # ── 信号 vs 技术面对比 ──
    signal_trades = [t for t in all_trades if t.get("has_signal")]
    tech_trades = [t for t in all_trades if not t.get("has_signal")]
    if signal_trades:
        s_wr = sum(1 for t in signal_trades if t["win"]) / len(signal_trades) * 100
        s_avg = statistics.mean(t["next_return"] for t in signal_trades)
        print(f"\n  信号组 ({len(signal_trades)}笔): WR={s_wr:.1f}%  均收益={s_avg:+.2f}%")
    if tech_trades:
        t_wr = sum(1 for t in tech_trades if t["win"]) / len(tech_trades) * 100
        t_avg = statistics.mean(t["next_return"] for t in tech_trades)
        print(f"  技术面组 ({len(tech_trades)}笔): WR={t_wr:.1f}%  均收益={t_avg:+.2f}%")

    # 信号组按年
    if signal_trades:
        print(f"\n  {'信号按年':<6} {'笔数':>5} {'胜率':>7} {'均收益':>8}")
        print(f"  {'─'*30}")
        for year in sorted(yearly.keys()):
            yr_sig = [t for t in signal_trades if t["date"][:4] == year]
            if yr_sig:
                yr_wr = sum(1 for t in yr_sig if t["win"]) / len(yr_sig) * 100
                yr_avg = statistics.mean(t["next_return"] for t in yr_sig)
                print(f"  {year:<6} {len(yr_sig):>5} {yr_wr:>6.1f}% {yr_avg:>+7.2f}%")

    # Top 推荐统计
    top5_trades = defaultdict(list)
    for t in all_trades:
        key = t["date"]
        if len(top5_trades[key]) < 5:
            top5_trades[key].append(t["next_return"])
    top5_all = [r for rs in top5_trades.values() for r in rs]
    top5_wr = sum(1 for r in top5_all if r > 0) / len(top5_all) * 100 if top5_all else 0
    top5_avg = statistics.mean(top5_all) if top5_all else 0
    print(f"\n  Top5 加权: WR={top5_wr:.1f}%  均收益={top5_avg:+.2f}% ({len(top5_all)}笔)")

    # 持久化
    if HAS_RESULT_STORE:
        save_results("historical_backtest", {
            "from_date": from_date, "to_date": to_date,
            "step": step, "top_n": top_n, "min_consensus": min_consensus,
            "n_trades": n_trades, "win_rate": round(win_rate, 1),
            "avg_return": round(avg_ret, 2),
            "median_return": round(median_ret, 2),
            "std": round(statistics.stdev(rets), 2) if len(rets) > 1 else 0,
            "max_return": round(max(rets), 2),
            "min_return": round(min(rets), 2),
            "top5_wr": round(top5_wr, 1),
            "top5_avg_return": round(top5_avg, 2),
            "positive_months": f"{positive_months}/{len(month_wrs)}",
            "monthly": [
                {"month": m, "n": len(monthly[m]), "wr": round(wr, 1), "avg_ret": round(avg, 2)}
                for m, wr, avg in month_wrs
            ],
        })

    return all_trades


def main():
    parser = argparse.ArgumentParser(description="历史K线全量回测")
    parser.add_argument("--from", dest="from_date", default="20240101", help="起始日期")
    parser.add_argument("--to", dest="to_date", default="20260701", help="截止日期")
    parser.add_argument("--step", type=int, default=5, help="采样步长(天)")
    parser.add_argument("--top", type=int, default=10, help="每日选股数")
    parser.add_argument("--min-consensus", type=int, default=1, help="最少信号数")
    args = parser.parse_args()

    if not os.path.isdir(DAILY_DIR):
        print(f"K线数据目录不存在: {DAILY_DIR}")
        sys.exit(1)

    run_backtest(args.from_date, args.to_date, step=args.step,
                 top_n=args.top, min_consensus=args.min_consensus)


if __name__ == "__main__":
    main()
