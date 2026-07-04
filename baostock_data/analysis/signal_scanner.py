#!/usr/bin/env python3
"""
稳定选股扫描器 v1 — 多引擎信号交叉验证 + K线形态实时扫描

核心逻辑：
    1. 加载训练报告中的 13 个 ≥85% WR 达标形态
    2. 加载 discover_price_volume 的高胜率涨跌量价信号
    3. 对今日 K线逐一扫描，多引擎交叉验证
    4. 输出：买入候选列表（信号强度 + 置信度）

用法:
    python signal_scanner.py --date 20260704
    python signal_scanner.py --date 20260704 --top 20 --min-consensus 2
"""
import argparse
import csv
import os
import sys
import warnings
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── 路径 ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DAILY_DIR = os.path.join(PROJECT_ROOT, "baostock_data", "data", "daily")

try:
    from stock_filter import load_stock_files, load_main_board_files, print_filter_summary
except ImportError:
    from baostock_data.analysis.stock_filter import load_stock_files, load_main_board_files, print_filter_summary

# ── 选股约束 ──
MIN_WR_TARGET = 85.0  # 最低胜率要求
MAIN_BOARD_ONLY = True  # 仅主板（排除科创/创业/北交所）

try:
    from result_store import save_results
    HAS_RESULT_STORE = True
except ImportError:
    HAS_RESULT_STORE = False

MIN_DAYS = 80
HOLD_PERIODS = [1, 2, 3, 5, 10, 15]

# ═══════════════════════════════════════
# 训练验证的高胜率信号（来自 TRAINING_REPORT）
# ═══════════════════════════════════════

# 形态信号 (kline_discovery): 形态名 → (持仓天数, 参考胜率, 参考均收益)
# 仅保留训练验证 ≥80% WR 的形态
PATTERN_SIGNALS = {
    "三日连涨_量递增_逼60日高":     (2, 100.0, 4.65),
    "深跌35%_涨停_巨量_突破MA20":    (5, 100.0, 7.18),
    "启明星_MA金叉收敛_放量阳":     (3, 90.0, 4.01),
    "涨停_放量横盘_缩量企稳_多头":    (10, 90.0, 12.90),
    "急跌12%_长下影_放量收阳_低位":  (10, 87.5, 8.89),
}

# 涨跌量价信号 (discover_price_volume): 条件 → (持仓, 参考胜率)
PRICE_VOL_SIGNALS = {
    "低位缩量微涨":  {"chg_min": 0.01, "chg_max": 0.03, "vol_max": 0.8,
                      "pos_max": 0.15, "hold": 5, "wr": 100.0},
    "深跌放量反弹":  {"chg_min": -0.30, "chg_max": -0.10, "vol_min": 1.2,
                      "pos_max": 0.35, "hold": 3, "wr": 88.9},
    "中涨爆量追高":  {"chg_min": 0.05, "chg_max": 0.10, "vol_min": 3.0,
                      "pos_min": 0.5, "pos_max": 0.7, "hold": 5, "wr": 81.8},
}

# ── 质量过滤阈值 ──
MIN_TURNOVER_YI = 1.0      # 最低成交额 (亿)
MIN_PRICE = 5.0             # 最低价格 (排除准仙股)
MAX_POS_FOR_ENTRY = 0.92   # 不追太高位置

# ═══════════════════════════════════════
# 技术指标计算
# ═══════════════════════════════════════

def _ma(series, window):
    return series.rolling(window, min_periods=window).mean()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l, v = df["收盘"], df["最高"], df["最低"], df["成交量"]
    o = df["开盘"]

    df["ma5"] = c.rolling(5, min_periods=5).mean()
    df["ma10"] = c.rolling(10, min_periods=10).mean()
    df["ma20"] = c.rolling(20, min_periods=20).mean()
    df["ma60"] = c.rolling(60, min_periods=20).mean()
    df["vol_ma5"] = v.rolling(5, min_periods=5).mean()
    df["vol_ma20"] = v.rolling(20, min_periods=20).mean()
    df["vol_ratio"] = (v / df["vol_ma5"].replace(0, np.nan)).fillna(1.0)
    df["vol_ratio_vs20"] = (v / df["vol_ma20"].replace(0, np.nan)).fillna(1.0)
    df["is_yang"] = c > o
    df["body_ratio"] = (c - o).abs() / np.where(c != o, (c - o).abs(), 1.0)
    df["amplitude"] = (h - l) / l.replace(0, np.nan).fillna(1.0)
    df["pct_chg"] = c.pct_change()

    roll_high = h.rolling(60, min_periods=20).max()
    roll_low = l.rolling(60, min_periods=20).min()
    df["pos_60"] = (c - roll_low) / (roll_high - roll_low).replace(0, np.nan).fillna(1.0)

    body_bot = o.combine(c, min)
    df["lower_shadow"] = (body_bot - l) / (h - l).replace(0, np.nan).fillna(1.0)
    df["ma_bull"] = (df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"]) & (c > df["ma5"])
    df["real_body"] = (c - o).abs()
    df["upper_shadow"] = h - o.combine(c, max)

    # 连续上涨天数 (量增价涨)
    up_streak = pd.Series(0, index=df.index)
    streak = 0
    for i in range(len(df)):
        if i > 0 and c.iloc[i] > c.iloc[i-1] and v.iloc[i] > v.iloc[i-1]:
            streak += 1
        elif c.iloc[i] > c.iloc[i-1]:
            streak = 1
        else:
            streak = 0
        up_streak.iloc[i] = streak
    df["up_streak"] = up_streak

    # 缺口头寸
    df["gap_up"] = (df["开盘"] > c.shift(1) * 1.02).astype(int)
    df["gap_dn"] = (df["开盘"] < c.shift(1) * 0.98).astype(int)

    return df


# ═══════════════════════════════════════
# 形态检测
# ═══════════════════════════════════════

def detect_3day_surge(df, i):
    """三日连涨_量递增_逼60日高"""
    if i < 3: return False
    c = df["收盘"].values; v = df["成交量"].values
    if not (c[i] > c[i-1] > c[i-2] > c[i-3]): return False
    if not (v[i] > v[i-1] > v[i-2]): return False
    pos = df["pos_60"].values[i]
    return pos > 0.75 and not pd.isna(pos)


def detect_deep_fall_reversal(df, i):
    """深跌35%_涨停_巨量_突破MA20"""
    if i < 60: return False
    c = df["收盘"].values; h = df["最高"].values
    v = df["成交量"].values; ma20 = df["ma20"].values
    high_60 = max(h[i-60:i])
    ret_60 = (c[i] - high_60) / high_60 if high_60 > 0 else 0
    if ret_60 > -0.35: return False
    chg_today = (c[i] - c[i-1]) / c[i-1] if i > 0 and c[i-1] > 0 else 0
    if chg_today < 0.095: return False
    if v[i] < df["vol_ma20"].values[i] * 2: return False
    return c[i] > ma20[i] if not pd.isna(ma20[i]) else False


def detect_morning_star_ma(df, i):
    """启明星 + MA金叉收敛"""
    if i < 5: return False
    c = df["收盘"].values; o = df["开盘"].values
    v = df["成交量"].values; l = df["最低"].values
    # 启明星: 阴线→小十字星→放量阳线
    if not (c[i-2] < o[i-2]): return False  # T-2 阴线
    body_t1 = abs(c[i-1] - o[i-1])
    body_t0 = abs(c[i] - o[i])
    if not (body_t1 < body_t0 * 0.5 and body_t0 > 0): return False  # 十字星
    if not (c[i] > o[i]): return False  # T 阳线
    if not (v[i] > v[i-1] * 1.2): return False  # 放量
    # MA 金叉
    ma5, ma10 = df["ma5"].values, df["ma10"].values
    if pd.isna(ma5[i]) or pd.isna(ma10[i]): return False
    return ma5[i] > ma10[i] and ma5[i-2] <= ma10[i-2]


def detect_limit_up_consolidation(df, i):
    """涨停_放量横盘_缩量企稳_多头"""
    if i < 10: return False
    c = df["收盘"].values; v = df["成交量"].values
    dead_low = df["最低"].values
    # 找最近的涨停日
    limit_up_idx = None
    for j in range(i-5, i-15, -1):
        if j < 0: break
        chg = (c[j] - c[j-1]) / c[j-1] if j > 0 and c[j-1] > 0 else 0
        if chg >= 0.095 and v[j] > df["vol_ma20"].values[j] * 2:
            limit_up_idx = j
            break
    if limit_up_idx is None: return False
    # 涨停后3日横盘（不破涨停日低点）
    if i - limit_up_idx < 3: return False
    post_low = min(dead_low[limit_up_idx+1:i])
    if post_low < dead_low[limit_up_idx] * 0.97: return False
    # 缩量企稳
    if not (v[i] < v[limit_up_idx] * 0.6): return False
    # MA多头
    return bool(df["ma_bull"].values[i])


def detect_sharp_fall_hammer(df, i):
    """急跌12%_长下影_放量收阳_低位"""
    if i < 20: return False
    c = df["收盘"].values; l = df["最低"].values
    h = df["最高"].values; v = df["成交量"].values
    o = df["开盘"].values
    # 近10日最大跌幅 ≥12%
    peak_10 = max(h[i-10:i])
    trough = min(l[i-10:i])
    drop = (trough - peak_10) / peak_10 if peak_10 > 0 else 0
    if drop > -0.12: return False
    # 长下影线
    body_bot = min(o[i], c[i])
    lower = (body_bot - l[i]) / (h[i] - l[i]) if h[i] > l[i] else 0
    if lower < 0.4: return False
    # 收阳
    if not (c[i] > o[i]): return False
    # 放量
    if not (v[i] > df["vol_ma5"].values[i] * 1.3): return False
    # 低位
    pos = df["pos_60"].values[i]
    return pos < 0.25 and not pd.isna(pos)


def detect_ma_golden_cross(df, i):
    """MA5金叉MA20 + 放量 + 站上MA20"""
    if i < 10: return False
    ma5, ma20 = df["ma5"].values, df["ma20"].values
    v = df["成交量"].values; vol_ma5 = df["vol_ma5"].values
    c = df["收盘"].values
    if pd.isna(ma5[i]) or pd.isna(ma20[i]): return False
    golden = ma5[i] > ma20[i] and ma5[i-2] <= ma20[i-2]
    if not golden: return False
    if not (c[i] > ma20[i]): return False
    return v[i] > vol_ma5[i] * 1.2 if vol_ma5[i] > 0 else False


def detect_yang_shrink_breakout(df, i):
    """连阳缩量_蓄力突破: 2+连阳 + 缩量 + 价格在MA20附近"""
    if i < 5: return False
    c = df["收盘"].values; o = df["开盘"].values
    v = df["成交量"].values; vol_ma5 = df["vol_ma5"].values
    ma20 = df["ma20"].values
    # 连阳
    if not (c[i] > o[i] and c[i-1] > o[i-1]): return False
    # 缩量
    if not (v[i] < vol_ma5[i] * 1.1): return False
    # 在MA20附近(±5%)
    if pd.isna(ma20[i]) or ma20[i] <= 0: return False
    dist = abs(c[i] - ma20[i]) / ma20[i]
    return dist < 0.05


def detect_low_open_high_close(df, i):
    """低开高走_阳包阴: 今日低开 >2% + 收阳 + 覆盖昨日阴线"""
    if i < 2: return False
    c = df["收盘"].values; o = df["开盘"].values
    prev_c = df["收盘"].values[i-1]
    # 低开
    gap = (o[i] - prev_c) / prev_c if prev_c > 0 else 0
    if gap > -0.01 or gap < -0.05: return False  # 低开1-5%
    # 收阳 + 包昨日阴线
    if not (c[i] > o[i]): return False
    if prev_c < o[i-1] and c[i] > o[i-1]: return True  # 阳包阴
    return c[i] > prev_c  # 至少覆盖昨日收盘


# 形态检测注册表
PATTERN_DETECTORS = {
    "三日连涨_量递增_逼60日高":     (detect_3day_surge, 2),
    "深跌35%_涨停_巨量_突破MA20":    (detect_deep_fall_reversal, 5),
    "启明星_MA金叉收敛_放量阳":     (detect_morning_star_ma, 3),
    "涨停_放量横盘_缩量企稳_多头":    (detect_limit_up_consolidation, 10),
    "急跌12%_长下影_放量收阳_低位":  (detect_sharp_fall_hammer, 10),
}


# ═══════════════════════════════════════
# 涨跌量价信号检测
# ═══════════════════════════════════════

def check_price_vol_signal(df, i):
    """检测涨跌量价信号，返回 [(信号名, 持仓, 胜率)]。"""
    c = df["收盘"].values
    chg = df["pct_chg"].values[i] if i > 0 and not pd.isna(df["pct_chg"].values[i]) else 0
    vol_r = df["vol_ratio"].values[i] if not pd.isna(df["vol_ratio"].values[i]) else 1.0
    pos = df["pos_60"].values[i] if not pd.isna(df["pos_60"].values[i]) else 0.5

    signals = []
    for name, cfg in PRICE_VOL_SIGNALS.items():
        if "chg_min" in cfg and chg < cfg["chg_min"]: continue
        if "chg_max" in cfg and chg > cfg["chg_max"]: continue
        if "vol_min" in cfg and vol_r < cfg["vol_min"]: continue
        if "vol_max" in cfg and vol_r > cfg["vol_max"]: continue
        if "pos_min" in cfg and pos < cfg["pos_min"]: continue
        if "pos_max" in cfg and pos > cfg["pos_max"]: continue
        signals.append((name, cfg["hold"], cfg["wr"]))
    return signals


# ═══════════════════════════════════════
# 主扫描器
# ═══════════════════════════════════════

def scan_stocks(data_dir: str, target_date: str,
                min_consensus: int = 1, top_n: int = 30, lookback: int = 5):
    """扫描所有个股，返回买入候选列表。

    lookback: 回溯天数，在前 N 天内如果形态曾触发过，且今日仍在合理区间内，即视为有效信号。
    信号衰减: 当天触发=1.0, 1天前=0.85, 2天前=0.7, 3天前=0.55, 4+天前=0.4
    """
    stock_files = load_main_board_files(data_dir) if MAIN_BOARD_ONLY else load_stock_files(data_dir)
    if not stock_files:
        print("无个股数据"); return []

    print_filter_summary(data_dir, main_board_only=MAIN_BOARD_ONLY)
    print(f"扫描日期: {target_date} | 回溯: {lookback}天 | 最少共识: {min_consensus} 引擎")
    print(f"约束: 仅主板 | 胜率≥{MIN_WR_TARGET:.0f}% | 最多输出: {top_n}")
    print()

    candidates = []
    processed = 0
    matched = 0
    kline_date = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}"

    for fp in stock_files:
        try:
            df = pd.read_csv(fp)
            if len(df) < MIN_DAYS: continue
            df["日期"] = pd.to_datetime(df["日期"], format="%Y-%m-%d")
            df = df.sort_values("日期").reset_index(drop=True)
            df = df[df["成交量"] > 0].copy()
            if len(df) < MIN_DAYS: continue
        except Exception:
            continue

        # 找目标日期索引
        idx = None
        for j in range(len(df)-1, -1, -1):
            d = df["日期"].iloc[j]
            if str(d.date()) in (target_date, kline_date):
                idx = j
                break
        if idx is None:  # 取最近日期
            idx = len(df) - 1

        if idx < MIN_DAYS: continue

        df = compute_indicators(df)
        code = os.path.splitext(os.path.basename(fp))[0]
        name = str(df["名称"].iloc[0]) if "名称" in df.columns else code

        # ── 多日回溯检测 ──
        # 扫描过去 lookback 天，找到所有曾触发的信号
        best_signals = {}  # (type, name) → (days_ago, hold, wr)
        for offset in range(lookback + 1):
            check_idx = idx - offset
            if check_idx < MIN_DAYS: continue

            # 形态信号
            for pname, (detector, hold) in PATTERN_DETECTORS.items():
                try:
                    if detector(df, check_idx):
                        wr_ref = PATTERN_SIGNALS.get(pname, (hold, 0, 0))[1]
                        key = ("pattern", pname)
                        if key not in best_signals or offset < best_signals[key][0]:
                            best_signals[key] = (offset, hold, wr_ref)
                except Exception:
                    continue

            # 涨跌量价信号
            for sig_name, sig_hold, sig_wr in check_price_vol_signal(df, check_idx):
                key = ("pv", sig_name)
                if key not in best_signals or offset < best_signals[key][0]:
                    best_signals[key] = (offset, sig_hold, sig_wr)

        # 信号衰减加权
        def decay(offset):
            return {0: 1.0, 1: 0.85, 2: 0.70, 3: 0.55}.get(offset, 0.40)

        pattern_signals = [(k[1], v[1], v[2], decay(v[0]))
                           for k, v in best_signals.items() if k[0] == "pattern"]
        pv_signals = [(k[1], v[1], v[2], decay(v[0]))
                      for k, v in best_signals.items() if k[0] == "pv"]

        total_signals = len(pattern_signals) + len(pv_signals)
        if total_signals < min_consensus:
            processed += 1
            continue

        # 85% WR 过滤: 至少一个信号的参考胜率 ≥85%
        all_wr = [p[2] for p in pattern_signals] + [p[2] for p in pv_signals]
        max_wr = max(all_wr) if all_wr else 0
        if max_wr < MIN_WR_TARGET:
            processed += 1
            continue

        matched += 1

        c = df["收盘"].values[idx]
        pos_60 = float(df["pos_60"].values[idx]) if not pd.isna(df["pos_60"].values[idx]) else 0.5
        ma_bull = bool(df["ma_bull"].values[idx])
        turnover = float(df["成交量"].values[idx]) * c / 1e8
        chg_today = float(df["pct_chg"].values[idx] * 100) if not pd.isna(df["pct_chg"].values[idx]) else 0

        # ── 综合评分 (衰减加权) ──
        # 形态分 = SUM(wr * decay) / SUM(decay)
        p_wr_sum = sum(wr * d for _, _, wr, d in pattern_signals)
        p_d_sum = sum(d for _, _, _, d in pattern_signals)
        pattern_score = p_wr_sum / p_d_sum if p_d_sum > 0 else 0

        pv_wr_sum = sum(wr * d for _, _, wr, d in pv_signals)
        pv_d_sum = sum(d for _, _, _, d in pv_signals)
        pv_score = pv_wr_sum / pv_d_sum if pv_d_sum > 0 else 0

        # 加权综合: 形态 60% + 涨跌量价 40%
        composite = pattern_score * 0.6 + pv_score * 0.4 if pv_signals else pattern_score
        # 共识密度加分 (多信号 = 更可靠)
        density_bonus = min(total_signals * 5, 20)
        # 新鲜度加分 (今天触发 > 几天前触发)
        min_offset = min((v[0] for v in best_signals.values()), default=lookback)
        freshness = {0: 10, 1: 5, 2: 0, 3: -5}.get(min_offset, -10)
        # MA 多头加分
        ma_bonus = 8 if ma_bull else 0
        # 位置惩罚: 太高(追高)或太低(弱势)
        if pos_60 > 0.90: pos_penalty = -5
        elif pos_60 < 0.15: pos_penalty = -3
        else: pos_penalty = 0

        final_score = composite + density_bonus + freshness + ma_bonus + pos_penalty

        candidates.append({
            "code": code, "name": name, "price": round(c, 2),
            "score": round(final_score, 1),
            "chg_today": round(chg_today, 2),
            "turnover_yi": round(turnover, 1),
            "pos_60": round(pos_60, 2),
            "ma_bull": ma_bull,
            "patterns": [p[0] for p in pattern_signals],
            "pv_signals": [p[0] for p in pv_signals],
            "n_signals": total_signals,
            "n_patterns": len(pattern_signals),
            "n_pv": len(pv_signals),
            "days_ago": min_offset,
            "best_hold": pattern_signals[0][1] if pattern_signals else (pv_signals[0][1] if pv_signals else 5),
            "best_wr": round(max([p[2] for p in pattern_signals] +
                                 [p[2] for p in pv_signals]), 1) if total_signals else 0,
        })

        processed += 1
        if processed % 500 == 0:
            print(f"  已扫描: {processed}/{len(stock_files)}  命中: {matched}", flush=True)

    print(f"  扫描完成: {processed} 只 → 命中: {matched} 只\n")

    # 排序: 得分降序
    candidates.sort(key=lambda x: -x["score"])
    return candidates[:top_n]


def print_results(candidates, target_date, min_consensus):
    """格式化输出买入候选。"""
    print("═" * 95)
    print(f"  🎯 主板选股信号扫描 [{target_date}]")
    print(f"  约束: 仅主板 | WR≥{MIN_WR_TARGET:.0f}% | 共识≥{min_consensus} | 形态×涨跌量价双引擎")
    print("═" * 95)

    if not candidates:
        print("\n  (无符合条件的买入候选)")
        return

    print(f"\n  {'#':<3} {'代码':<10} {'名称':<8} {'得分':<6} {'涨跌':<7} "
          f"{'成交额亿':<8} {'位置':<5} {'MA':<4} {'信号':>3} {'天前':>3} "
          f"{'形态':>2} {'量价':>2} {'最优持仓':<8} {'WR':<6}")
    print(f"  {'─'*93}")

    for i, c in enumerate(candidates):
        hold_str = f"T+{c['best_hold']}"
        ago = c.get("days_ago", "?")
        print(f"  {i+1:<3} {c['code']:<10} {str(c['name']):<8} {c['score']:<6.1f} "
              f"{c['chg_today']:>+.1f}%  {c['turnover_yi']:<8.1f} {c['pos_60']:<5.2f} "
              f"{'✅' if c['ma_bull'] else '❌':<4} {c['n_signals']:>3d} {str(ago):>3s} "
              f"{c['n_patterns']:>2d} {c['n_pv']:>2d} {hold_str:<8} {c['best_wr']:.0f}%")

    # 信号详情
    print(f"\n  ── 信号详情 ──")
    for i, c in enumerate(candidates):
        patterns_str = " + ".join(c["patterns"]) if c["patterns"] else "—"
        pv_str = " + ".join(c["pv_signals"]) if c["pv_signals"] else "—"
        print(f"  {i+1}. {c['code']} {c['name']}")
        print(f"     形态: {patterns_str}")
        print(f"     量价: {pv_str}")

    print()


# ═══════════════════════════════════════
# CLI
# ═══════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="稳定选股扫描器 — 多引擎交叉验证")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="扫描日期")
    parser.add_argument("--top", type=int, default=30, help="输出候选数")
    parser.add_argument("--min-consensus", type=int, default=1,
                        help="最少引擎命中数 (1=宽松 2=严格)")
    parser.add_argument("--lookback", type=int, default=5, help="信号回溯天数")
    parser.add_argument("--save", action="store_true", help="保存结果到 JSON")
    args = parser.parse_args()

    if not os.path.isdir(DAILY_DIR):
        print(f"错误: K线数据目录不存在: {DAILY_DIR}")
        sys.exit(1)

    candidates = scan_stocks(DAILY_DIR, args.date,
                             min_consensus=args.min_consensus, top_n=args.top,
                             lookback=args.lookback)
    print_results(candidates, args.date, args.min_consensus)

    if args.save and candidates:
        path = os.path.join(SCRIPT_DIR, f"scan_{args.date}.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[
                "code", "name", "price", "score", "chg_today", "turnover_yi",
                "pos_60", "ma_bull", "n_signals", "days_ago", "n_patterns", "n_pv",
                "best_hold", "best_wr", "patterns", "pv_signals"
            ], extrasaction="ignore")
            w.writeheader()
            for c in candidates:
                c["patterns"] = ",".join(c["patterns"])
                c["pv_signals"] = ",".join(c["pv_signals"])
                w.writerow(c)
        print(f"  💾 CSV 已保存: {path}")

    # 结果持久化
    if HAS_RESULT_STORE and candidates:
        save_results("signal_scanner", {
            "date": args.date,
            "min_consensus": args.min_consensus,
            "total_candidates": len(candidates),
            "candidates": [
                {"code": c["code"], "name": c["name"], "score": c["score"],
                 "price": c["price"], "best_wr": c["best_wr"],
                 "patterns": c["patterns"], "pv_signals": c["pv_signals"]}
                for c in candidates
            ],
        })


if __name__ == "__main__":
    main()
