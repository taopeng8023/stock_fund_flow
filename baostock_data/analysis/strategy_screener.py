#!/usr/bin/env python3
"""
6 策略选股器 — 基于 K 线形态发现引擎的达标形态
验证每只策略胜率 ≥85%，输出当日/近期信号

用法:
    python strategy_screener.py --date 20260702           # 验证 + 选股
    python strategy_screener.py --date 20260702 --today   # 仅当日信号
    python strategy_screener.py --date 20260702 --verify  # 仅验证胜率
    python strategy_screener.py --date 20260702 --top 20  # Top 20 推荐
"""
import argparse
import os
import sys
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from glob import glob
from typing import List, Dict, Tuple, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ============================================================
# 6 个达标策略定义
# ============================================================
# (名称, 形态检测函数引用, 确认级别, 持仓周期, 目标胜率)
STRATEGIES: List[dict] = []

MIN_DAYS = 80
WIN_THRESHOLD = 0.005  # 最小盈利阈值


@dataclass
class SignalResult:
    code: str
    name: str
    strategy: str
    signal_date: str
    confirm_level: str
    holding_days: int
    entry_price: float
    target_win_rate: float


@dataclass
class VerifyResult:
    strategy: str
    holding_days: int
    confirm_level: str
    total: int = 0
    wins: int = 0
    returns: List[float] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.wins / self.total * 100 if self.total > 0 else 0

    @property
    def avg_return(self) -> float:
        return np.mean(self.returns) * 100 if self.returns else 0


# ============================================================
# 指标计算（复用 discovery 引擎逻辑）
# ============================================================
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df["收盘"]; o = df["开盘"]; h = df["最高"]; l = df["最低"]
    v = df["成交量"]; pc = df["前收盘"]
    n = len(df)

    for w in [5, 10, 20, 60]:
        df[f"ma{w}"] = c.rolling(w, min_periods=w).mean()
    for w in [5, 20]:
        df[f"vol_ma{w}"] = v.rolling(w, min_periods=w).mean()

    df["candle_body"] = c - o
    df["candle_range"] = h - l
    df["body_ratio"] = np.where(df["candle_range"] > 0,
                                np.abs(df["candle_body"]) / df["candle_range"], 0)
    df["upper_shadow"] = np.where(df["candle_range"] > 0,
                                  (h - np.maximum(o, c)) / df["candle_range"], 0)
    df["lower_shadow"] = np.where(df["candle_range"] > 0,
                                  (np.minimum(o, c) - l) / df["candle_range"], 0)
    df["is_yang"] = (c > o).astype(int)
    df["is_yin"] = (c < o).astype(int)
    df["amplitude"] = np.where(pc > 0, df["candle_range"] / pc, 0)
    df["change_pct"] = np.where(pc > 0, (c - pc) / pc, 0)

    df["vol_ratio_vs5"] = np.where(df["vol_ma5"] > 0, v / df["vol_ma5"], 1)
    df["vol_ratio_vs20"] = np.where(df["vol_ma20"] > 0, v / df["vol_ma20"], 1)

    df["ma_bull"] = ((df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"])).astype(int)

    for w in [5, 10, 20, 60]:
        ma_col = f"ma{w}"
        if ma_col in df.columns:
            df[f"dist_ma{w}"] = np.where(df[ma_col].notna() & (df[ma_col] > 0),
                                         (c - df[ma_col]) / df[ma_col], 0)
    for w in [10, 20, 60]:
        df[f"high_{w}d"] = h.rolling(w, min_periods=w).max()
        df[f"low_{w}d"] = l.rolling(w, min_periods=w).min()
        df[f"close_high_{w}d"] = c.rolling(w, min_periods=w).max()
        df[f"close_low_{w}d"] = c.rolling(w, min_periods=w).min()

    df["yang_streak"] = 0; df["yin_streak"] = 0
    ys = 0; ns = 0
    for i in range(n):
        if df["is_yang"].iloc[i]:
            ys += 1; ns = 0
        else:
            ns += 1; ys = 0
        df.iloc[i, df.columns.get_loc("yang_streak")] = ys
        df.iloc[i, df.columns.get_loc("yin_streak")] = ns

    for j in range(20, n):
        rng = df["close_high_20d"].values[j] - df["close_low_20d"].values[j]
        df.loc[df.index[j], "close_rank_20"] = (
            (c.values[j] - df["close_low_20d"].values[j]) / rng if rng > 0 else 0.5)

    df["volatility_20"] = df["change_pct"].rolling(20, min_periods=20).std()
    return df


# ============================================================
# 确认逻辑
# ============================================================
def confirm_standard(df: pd.DataFrame, signal_i: int) -> bool:
    """T+1 收盘 > T 收盘"""
    n = len(df)
    j = signal_i + 1
    if j >= n: return False
    c = df["收盘"].values; l = df["最低"].values; o = df["开盘"].values
    vol_r5 = df["vol_ratio_vs5"].values
    if o[j] > 0 and (c[j] - o[j]) / o[j] < -0.06: return False
    if l[j] < l[signal_i] * 0.97: return False
    if vol_r5[j] < 0.25: return False
    return c[j] > c[signal_i]


def confirm_strict(df: pd.DataFrame, signal_i: int) -> bool:
    """T+1 收盘 > T 最高 + T+1 收阳 + T+1 放量"""
    n = len(df)
    j = signal_i + 1
    if j >= n: return False
    c = df["收盘"].values; h = df["最高"].values
    l = df["最低"].values; o = df["开盘"].values
    v = df["成交量"].values; is_yang = df["is_yang"].values
    vol_r5 = df["vol_ratio_vs5"].values
    if o[j] > 0 and (c[j] - o[j]) / o[j] < -0.06: return False
    if l[j] < l[signal_i] * 0.97: return False
    if vol_r5[j] < 0.25: return False
    if c[j] <= h[signal_i]: return False
    if not is_yang[j]: return False
    if v[j] <= v[signal_i]: return False
    return True


def confirm_bull(df: pd.DataFrame, signal_i: int) -> bool:
    """▲ + MA20 上升 + 价在 MA20 上"""
    if not confirm_strict(df, signal_i): return False
    ma20 = df["ma20"].values; c = df["收盘"].values
    if signal_i < 5: return False
    if pd.isna(ma20[signal_i]) or pd.isna(ma20[signal_i - 5]): return False
    if not (ma20[signal_i] > ma20[signal_i - 5]): return False
    if c[signal_i] < ma20[signal_i]: return False
    return True


# ============================================================
# 6 个策略的形态检测
# ============================================================
def detect_combo_h1g2(df: pd.DataFrame, i: int) -> bool:
    """
    策略 1: ◆三连阳温和放量突破MA20 + MA5金叉MA10三线收敛放量阳
    确认: ▲  |  持仓: T+5  |  目标胜率: 91.7%
    """
    c = df["收盘"].values; v = df["成交量"].values
    is_yang = df["is_yang"].values
    yang_streak = df["yang_streak"].values
    vol_r5 = df["vol_ratio_vs5"].values
    amp = df["amplitude"].values
    ma5 = df["ma5"].values; ma10 = df["ma10"].values; ma20 = df["ma20"].values

    # H1: 三连阳 + 量递增 + 温和涨幅 + 突破MA20
    h1 = (yang_streak[i] >= 3 and v[i] > v[i-1] > v[i-2] and
          i >= 5 and c[i-5] > 0 and (c[i] - c[i-5]) / c[i-5] < 0.15 and
          not pd.isna(ma20[i]) and c[i] > ma20[i] and c[i-2] <= ma20[i-2])

    # G2: MA5金叉MA10 + 三线收敛 + 放量阳
    g2 = (i >= 1 and not pd.isna(ma5[i]) and not pd.isna(ma10[i]) and
          not pd.isna(ma20[i]) and not pd.isna(ma5[i-1]) and not pd.isna(ma10[i-1]) and
          ma5[i-1] <= ma10[i-1] and ma5[i] > ma10[i] and
          ma5[i] > 0 and
          max(np.abs(ma5[i]-ma10[i]), np.abs(ma10[i]-ma20[i]), np.abs(ma5[i]-ma20[i])) / ma5[i] < 0.025 and
          vol_r5[i] > 1.1 and is_yang[i])

    return h1 and g2


def detect_morning_star(df: pd.DataFrame, i: int) -> bool:
    """
    策略 2: ★启明星_三连阴缩量_十字星_放量阳
    确认: ★ (Bull Regime)  |  持仓: T+15  |  目标胜率: 90.0%
    """
    c = df["收盘"].values; o = df["开盘"].values
    v = df["成交量"].values; is_yang = df["is_yang"].values
    yin_streak = df["yin_streak"].values
    vol_r5 = df["vol_ratio_vs5"].values
    body_r = df["body_ratio"].values

    if not (i >= 3 and yin_streak[i-1] >= 3 and v[i-3] > v[i-2] > v[i-1]):
        return False
    if not (body_r[i-1] < 0.2 and is_yang[i] and vol_r5[i] > 1.1):
        return False
    if not (c[i] > (o[i-3] + c[i-3]) / 2):
        return False
    return True


def detect_crash_rebound(df: pd.DataFrame, i: int) -> bool:
    """
    策略 3: 急跌12%_长下影_放量收阳_低位
    确认: Standard  |  持仓: T+15  |  目标胜率: 87.5%
    """
    c = df["收盘"].values; is_yang = df["is_yang"].values
    vol_r5 = df["vol_ratio_vs5"].values
    lower_s = df["lower_shadow"].values
    close_rank_20 = df["close_rank_20"].values

    if not (i >= 5 and c[i-5] > 0 and (c[i] - c[i-5]) / c[i-5] < -0.12):
        return False
    if not (lower_s[i] > 0.6 and is_yang[i] and vol_r5[i] > 1.5):
        return False
    if not (not pd.isna(close_rank_20[i]) and close_rank_20[i] < 0.25):
        return False
    return True


def detect_bull_pullback(df: pd.DataFrame, i: int) -> bool:
    """
    策略 4: ▲强多头_回踩MA20_极致缩量_启稳
    确认: ▲ (Ultra-Strict)  |  持仓: T+2  |  目标胜率: 87.5%
    """
    c = df["收盘"].values; is_yang = df["is_yang"].values
    vol_r5 = df["vol_ratio_vs5"].values
    amp = df["amplitude"].values; body_r = df["body_ratio"].values
    ma20 = df["ma20"].values; ma_bull = df["ma_bull"].values
    dist_ma20 = df["dist_ma20"].values

    if not (ma_bull[i] and not pd.isna(ma20[i]) and ma20[i] > 0):
        return False
    if not (np.abs(dist_ma20[i]) < 0.015 and vol_r5[i] < 0.45):
        return False
    if not (body_r[i] < 0.35 and amp[i] < 0.025 and is_yang[i] and c[i] > ma20[i]):
        return False
    return True


def detect_anti_engulf(df: pd.DataFrame, i: int) -> bool:
    """
    策略 5: ★反包_阳包阴包阳_放量
    确认: ★ (Bull Regime)  |  持仓: T+10  |  目标胜率: 86.7%
    """
    c = df["收盘"].values; o = df["开盘"].values
    is_yang = df["is_yang"].values; is_yin = df["is_yin"].values
    vol_r5 = df["vol_ratio_vs5"].values

    if not (i >= 2 and is_yin[i-1] and is_yang[i-2]):
        return False
    if not (c[i-1] < o[i-2] and o[i-1] > c[i-2]):  # 阴包阳
        return False
    if not (is_yang[i] and c[i] > o[i-1] and o[i] < c[i-1]):  # 阳包阴
        return False
    if not (vol_r5[i] > 1.3):
        return False
    return True


def detect_momentum_accel(df: pd.DataFrame, i: int) -> bool:
    """
    策略 6: ▲三日连涨_量递增_逼60日高
    确认: ▲ (Ultra-Strict)  |  持仓: T+5  |  目标胜率: 85.0%
    """
    c = df["收盘"].values; v = df["成交量"].values
    amp = df["amplitude"].values; change_pct = df["change_pct"].values

    if not (i >= 2 and all(change_pct[j] > 0.015 for j in range(i-2, i+1))):
        return False
    if not (v[i] > v[i-1] > v[i-2]):
        return False
    if not (not pd.isna(df["high_60d"].values[i]) and c[i] > df["high_60d"].values[i] * 0.97):
        return False
    if not (amp[i] > 0.03):
        return False
    return True


# ============================================================
# 策略注册表
# ============================================================
STRATEGIES = [
    {
        "name": "三连阳突破MA20+MA5金叉MA10",
        "alias": "◆ H1+G2 双形态叠加",
        "detector": detect_combo_h1g2,
        "confirm": confirm_strict,
        "confirm_label": "▲",
        "hold": 5,
        "target_wr": 91.7,
    },
    {
        "name": "启明星_三连阴缩_十字星_放量阳",
        "alias": "★ D1 启明星",
        "detector": detect_morning_star,
        "confirm": confirm_bull,
        "confirm_label": "★",
        "hold": 15,
        "target_wr": 90.0,
    },
    {
        "name": "急跌12%_长下影_放量收阳_低位",
        "alias": "A3 超跌反弹",
        "detector": detect_crash_rebound,
        "confirm": confirm_standard,
        "confirm_label": "",
        "hold": 15,
        "target_wr": 87.5,
    },
    {
        "name": "强多头_回踩MA20_极致缩量_启稳",
        "alias": "▲ B1 均线回调",
        "detector": detect_bull_pullback,
        "confirm": confirm_strict,
        "confirm_label": "▲",
        "hold": 2,
        "target_wr": 87.5,
    },
    {
        "name": "反包_阳包阴包阳_放量",
        "alias": "★ D2 反包形态",
        "detector": detect_anti_engulf,
        "confirm": confirm_bull,
        "confirm_label": "★",
        "hold": 10,
        "target_wr": 86.7,
    },
    {
        "name": "三日连涨_量递增_逼60日高",
        "alias": "▲ I1 趋势加速",
        "detector": detect_momentum_accel,
        "confirm": confirm_strict,
        "confirm_label": "▲",
        "hold": 5,
        "target_wr": 85.0,
    },
]


# ============================================================
# 数据加载
# ============================================================
def load_stock_csv(filepath: str) -> Optional[pd.DataFrame]:
    try:
        df = pd.read_csv(filepath)
        if len(df) < MIN_DAYS:
            return None
        df["日期"] = pd.to_datetime(df["日期"], format="%Y-%m-%d")
        df = df.sort_values("日期").reset_index(drop=True)
        df = df[df["成交量"] > 0].copy()
        return df if len(df) >= MIN_DAYS else None
    except Exception:
        return None


def compute_forward_return(df: pd.DataFrame, entry_idx: int, hold: int) -> Optional[float]:
    n = len(df)
    c = df["收盘"].values
    exit_idx = entry_idx + hold
    if exit_idx < n and c[entry_idx] > 0:
        return (c[exit_idx] - c[entry_idx]) / c[entry_idx]
    return None


# ============================================================
# 主流程
# ============================================================
def verify_and_screen(data_dir: str, target_wr: float = 85.0,
                       today_only: bool = False, top_n: int = 0):
    csv_files = sorted(glob(os.path.join(data_dir, "sh.*.csv")) +
                       glob(os.path.join(data_dir, "sz.*.csv")))
    if not csv_files:
        print("错误: 未找到数据文件")
        sys.exit(1)

    print(f"数据目录: {data_dir}")
    print(f"可用股票: {len(csv_files)} 只")
    print(f"策略数量: {len(STRATEGIES)} 个")
    print(f"目标胜率: {target_wr}%")
    print()

    # 验证数据结构
    verify_results: Dict[str, VerifyResult] = {}
    for s in STRATEGIES:
        key = f"{s['confirm_label']}{s['name']}_T+{s['hold']}"
        verify_results[key] = VerifyResult(
            strategy=s["alias"], holding_days=s["hold"],
            confirm_level=s["confirm_label"],
        )

    # 信号收集
    all_signals: List[SignalResult] = []

    # 逐只扫描
    processed = 0
    for fp in csv_files:
        df = load_stock_csv(fp)
        if df is None:
            continue
        code = os.path.splitext(os.path.basename(fp))[0]
        name = df["名称"].iloc[0] if "名称" in df.columns else code

        df = compute_indicators(df)
        n_days = len(df)
        dates = df["日期"].dt.strftime("%Y-%m-%d").values
        close_prices = df["收盘"].values

        # 扫描每一天
        for i in range(70, n_days - 2):
            for s in STRATEGIES:
                try:
                    if not s["detector"](df, i):
                        continue
                    if not s["confirm"](df, i):
                        continue
                except Exception:
                    continue

                entry_idx = i + 1  # T+1 收盘入场
                ret = compute_forward_return(df, entry_idx, s["hold"])
                if ret is None:
                    continue

                key = f"{s['confirm_label']}{s['name']}_T+{s['hold']}"
                vr = verify_results[key]
                vr.total += 1
                vr.returns.append(ret)
                if ret > WIN_THRESHOLD:
                    vr.wins += 1

                # 记录信号
                signal_date = dates[entry_idx]
                signal = SignalResult(
                    code=code, name=str(name),
                    strategy=s["alias"],
                    signal_date=signal_date,
                    confirm_level=s["confirm_label"],
                    holding_days=s["hold"],
                    entry_price=close_prices[entry_idx],
                    target_win_rate=s["target_wr"],
                )
                all_signals.append(signal)

        processed += 1
        if processed % 200 == 0:
            print(f"  扫描: {processed}/{len(csv_files)} ({processed/len(csv_files)*100:.0f}%)", flush=True)

    print(f"  扫描完成: {processed} 只", flush=True)
    print()

    # ── 验证报告 ──
    print("═" * 70)
    print("  策略胜率验证报告")
    print("═" * 70)
    print(f"  {'策略':38s} {'周期':>4s} {'胜率':>7s} {'样本':>6s} {'均收益':>8s} {'达标':>5s}")
    print("-" * 70)

    all_pass = True
    for key, vr in verify_results.items():
        wr = vr.win_rate
        avg_r = vr.avg_return
        target = next((s["target_wr"] for s in STRATEGIES
                       if f"{s['confirm_label']}{s['name']}_T+{s['hold']}" == key), target_wr)
        passed = "✅" if wr >= target else "❌"
        if wr < target:
            all_pass = False
        print(f"  {key:38s} T+{vr.holding_days:<2d} {wr:6.1f}% {vr.total:5d}  {avg_r:+7.2f}% {passed:>4s}")

    print("-" * 70)
    if all_pass:
        print(f"  ✅ 全部 {len(STRATEGIES)} 个策略验证通过 (胜率 ≥ {target_wr}%)")
    else:
        failed = sum(1 for key, vr in verify_results.items() if vr.win_rate < target_wr)
        print(f"  ⚠ {failed}/{len(STRATEGIES)} 个策略未达标")
    print()

    # ── 近期信号 ──
    if all_signals:
        # 找最近交易日的信号
        df_signals = pd.DataFrame([{
            "代码": s.code, "名称": s.name, "策略": s.strategy,
            "日期": s.signal_date, "确认": s.confirm_level,
            "持仓": s.holding_days, "入场价": s.entry_price,
            "目标胜率": s.target_win_rate,
        } for s in all_signals])

        latest_dates = sorted(df_signals["日期"].unique(), reverse=True)
        recent_date = latest_dates[0] if latest_dates else ""

        recent = df_signals[df_signals["日期"] == recent_date]

        print("═" * 70)
        print(f"  最新信号日: {recent_date} — {len(recent)} 个信号")
        print("═" * 70)

        if len(recent) > 0:
            recent = recent.sort_values(["目标胜率", "策略"], ascending=[False, True])
            if top_n > 0:
                recent = recent.head(top_n)

            print(f"  {'代码':10s} {'名称':8s} {'策略':25s} {'确认':4s} {'持仓':4s} {'入场价':>8s} {'目标胜率':>8s}")
            print("-" * 70)
            for _, row in recent.iterrows():
                print(f"  {row['代码']:10s} {str(row['名称']):8s} {row['策略']:25s} "
                      f"{row['确认']:4s} T+{int(row['持仓']):<3d} "
                      f"{row['入场价']:8.2f} {row['目标胜率']:7.1f}%")
        else:
            print("  (近期无信号)")
    else:
        print("未发现任何信号")

    print()
    print("═" * 70)

    return all_pass


def main():
    parser = argparse.ArgumentParser(description="6 策略 K 线选股器")
    parser.add_argument("--date", default="20260702", help="数据日期 YYYYMMDD")
    parser.add_argument("--target", type=float, default=85.0, help="目标胜率")
    parser.add_argument("--today", action="store_true", help="仅显示当日信号")
    parser.add_argument("--verify", action="store_true", help="仅验证胜率")
    parser.add_argument("--top", type=int, default=0, help="Top N 推荐")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    baostock_root = os.path.dirname(script_dir)
    data_dir = os.path.join(baostock_root, "data", args.date, "daily")

    if not os.path.isdir(data_dir):
        print(f"错误: 数据目录不存在: {data_dir}")
        sys.exit(1)

    all_pass = verify_and_screen(
        data_dir, args.target,
        today_only=args.today,
        top_n=args.top,
    )

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
