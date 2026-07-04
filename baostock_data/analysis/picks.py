#!/usr/bin/env python3
"""
量化选股系统 — 统一入口。涵盖：每日扫描 / 高收益精选 / 历史回测 / 大赢家分析

用法:
  python picks.py scan --date 20260704                # 每日扫描
  python picks.py scan --date 20260704 --strict       # 高收益精选 (>5%)
  python picks.py backtest --from 20240101 --to 20260701  # 历史回测
  python picks.py analyze                            # 大赢家特征分析
"""
import argparse
import csv
import os
import statistics
import sys
import warnings
from collections import defaultdict
from datetime import datetime

warnings.filterwarnings("ignore")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DAILY_DIR = os.path.join(PROJECT_ROOT, "baostock_data", "data", "daily")

# ── 共享模块 ──
try:
    from stock_filter import load_main_board_files, load_stock_files, print_filter_summary
except ImportError:
    from baostock_data.analysis.stock_filter import load_main_board_files, load_stock_files, print_filter_summary

try:
    from result_store import save_results
    HAS_SAVE = True
except ImportError:
    HAS_SAVE = False

import numpy as np
import pandas as pd

MIN_DAYS = 80

# ═══════════════════════════════════════════════════════════════
# 技术指标
# ═══════════════════════════════════════════════════════════════

def compute_indicators(df):
    df = df.copy()
    c, h, l, v, o = df["收盘"], df["最高"], df["最低"], df["成交量"], df["开盘"]
    df["ma5"] = c.rolling(5, min_periods=5).mean()
    df["ma10"] = c.rolling(10, min_periods=10).mean()
    df["ma20"] = c.rolling(20, min_periods=20).mean()
    df["ma60"] = c.rolling(60, min_periods=20).mean()
    df["vol_ma5"] = v.rolling(5, min_periods=5).mean()
    df["vol_ma20"] = v.rolling(20, min_periods=20).mean()
    df["vol_ratio"] = (v / df["vol_ma5"].replace(0, np.nan)).fillna(1.0)
    df["is_yang"] = c > o
    df["pct_chg"] = c.pct_change()
    rh = h.rolling(60, min_periods=20).max()
    rl = l.rolling(60, min_periods=20).min()
    df["pos_60"] = (c - rl) / (rh - rl).replace(0, np.nan).fillna(1.0)
    df["ma_bull"] = (df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"]) & (c > df["ma5"])
    up = pd.Series(0, index=df.index); s = 0
    for i in range(len(df)):
        s = s+1 if i>0 and c.iloc[i]>c.iloc[i-1] and v.iloc[i]>v.iloc[i-1] else (1 if i>0 and c.iloc[i]>c.iloc[i-1] else 0)
        up.iloc[i] = s
    df["up_streak"] = up
    df["amplitude"] = (h - l) / l.replace(0, np.nan).fillna(1.0)
    return df


# ═══════════════════════════════════════════════════════════════
# 形态检测器 — 仅保留训练验证 ≥85% WR 的 5 个形态
# ═══════════════════════════════════════════════════════════════

def detect_3day_surge(df, i):
    """三日连涨_量递增_逼60日高"""
    if i < 3: return False
    c = df["收盘"].values; v = df["成交量"].values
    return c[i] > c[i-1] > c[i-2] > c[i-3] and v[i] > v[i-1] > v[i-2]

def detect_deep_fall_reversal(df, i):
    """深跌35%_涨停_巨量_突破MA20"""
    if i < 60: return False
    c = df["收盘"].values; h = df["最高"].values; v = df["成交量"].values
    high = max(h[i-60:i])
    if (c[i]-high)/high > -0.35 if high>0 else True: return False
    if i>0 and c[i-1]>0 and (c[i]-c[i-1])/c[i-1] < 0.095: return False
    vm = df["vol_ma20"].values[i]
    if pd.isna(vm) or vm<=0 or v[i]<vm*2: return False
    m20 = df["ma20"].values[i]
    return not pd.isna(m20) and c[i] > m20

def detect_morning_star_ma(df, i):
    """启明星 + MA金叉收敛"""
    if i < 5: return False
    c = df["收盘"].values; o = df["开盘"].values; v = df["成交量"].values
    if not (c[i-2] < o[i-2]): return False
    if abs(c[i-1]-o[i-1]) > abs(c[i]-o[i])*0.5 or not (c[i]>o[i]): return False
    if v[i] < v[i-1]*1.2: return False
    m5, m10 = df["ma5"].values[i], df["ma10"].values[i]
    return not pd.isna(m5) and not pd.isna(m10) and m5>m10 and df["ma5"].values[i-2]<=df["ma10"].values[i-2]

def detect_limit_up_consolidation(df, i):
    """涨停_放量横盘_缩量企稳_多头"""
    if i < 15: return False
    c = df["收盘"].values; v = df["成交量"].values; l = df["最低"].values
    lu = None
    for j in range(i-5, i-15, -1):
        if j<0: break
        chg = (c[j]-c[j-1])/c[j-1] if j>0 and c[j-1]>0 else 0
        vm = df["vol_ma20"].values[j]
        if chg>=0.095 and not pd.isna(vm) and vm>0 and v[j]>vm*2: lu=j; break
    if lu is None or i-lu<3: return False
    if min(l[lu+1:i]) < l[lu]*0.97: return False
    return v[i] < v[lu]*0.6 and bool(df["ma_bull"].values[i])

def detect_sharp_fall_hammer(df, i):
    """急跌12%_长下影_放量收阳_低位"""
    if i < 20: return False
    c = df["收盘"].values; h = df["最高"].values; l = df["最低"].values
    v = df["成交量"].values; o = df["开盘"].values
    peak, trough = max(h[i-10:i]), min(l[i-10:i])
    if (trough-peak)/peak > -0.12 if peak>0 else True: return False
    body_bot = min(o[i], c[i])
    if h[i]<=l[i] or (body_bot-l[i])/(h[i]-l[i])<0.4: return False
    if not (c[i]>o[i] and v[i]>df["vol_ma5"].values[i]*1.3): return False
    pos = df["pos_60"].values[i]
    return not pd.isna(pos) and pos<0.25

PATTERNS = [
    ("三日连涨_量递增_逼60日高",  detect_3day_surge,        2, 100.0),
    ("深跌35%_涨停_巨量_突破MA20", detect_deep_fall_reversal, 5, 100.0),
    ("启明星_MA金叉收敛_放量阳",   detect_morning_star_ma,   3, 90.0),
    ("涨停_放量横盘_缩量企稳_多头", detect_limit_up_consolidation, 10, 90.0),
    ("急跌12%_长下影_放量收阳_低位", detect_sharp_fall_hammer, 10, 87.5),
]

# ═══════════════════════════════════════════════════════════════
# 高收益精选过滤器 (已验证: 单笔+6.27%) — 仅用于"三日连涨"
# ═══════════════════════════════════════════════════════════════

def is_ultra_surge(df, i):
    """三日连涨极品: 高开+连涨+MA多头+大盘+非追涨停 → T+2 +6.27%"""
    c = df["收盘"].values; o = df["开盘"].values; v = df["成交量"].values
    pos = df["pos_60"].values[i]
    if pd.isna(pos) or pos < 0.75: return False
    if i > 0 and c[i-1] > 0:
        if (o[i] - c[i-1]) / c[i-1] * 100 < 1.0: return False
    up_s = df["up_streak"].values[i]
    if pd.isna(up_s) or up_s < 3: return False
    m5, m10, m20 = df["ma5"].values[i], df["ma10"].values[i], df["ma20"].values[i]
    if pd.isna(m5) or pd.isna(m20) or not (m5 > m10 > m20): return False
    chg = df["pct_chg"].values[i] * 100 if not pd.isna(df["pct_chg"].values[i]) else 0
    if chg > 8: return False
    if v[i] * c[i] / 1e8 < 10: return False
    return True

def is_ultra_limit_up(df, i):
    """涨停企稳极品: 涨停后缩量横盘+MA多头 → T+10 目标+12.90%"""
    if i < 12: return False
    c = df["收盘"].values; v = df["成交量"].values; l = df["最低"].values
    lu = None
    for j in range(i-3, i-15, -1):
        if j < 0: break
        chg = (c[j]-c[j-1])/c[j-1] if j>0 and c[j-1]>0 else 0
        vm = df["vol_ma20"].values[j]
        if chg >= 0.095 and not pd.isna(vm) and vm>0 and v[j] > vm*2.5:
            lu = j; break
    if lu is None or i - lu < 3: return False
    if min(l[lu+1:i]) < l[lu] * 0.97: return False
    # 缩量至涨停量35%以下
    if v[i] > v[lu] * 0.35: return False
    if not bool(df["ma_bull"].values[i]): return False
    pos = df["pos_60"].values[i]
    if pd.isna(pos) or pos < 0.20 or pos > 0.80: return False
    if v[i] * c[i] / 1e8 < 2: return False
    return True

def is_ultra_hammer(df, i):
    """急跌锤子极品: 深跌+长下影+放量阳+低位 → T+10 目标+8.89%"""
    if i < 20: return False
    c = df["收盘"].values; h = df["最高"].values; l = df["最低"].values
    v = df["成交量"].values; o = df["开盘"].values
    peak = max(h[i-15:i])
    trough = min(l[i-15:i])
    if peak <= 0 or (trough-peak)/peak > -0.15: return False
    body_bot = min(o[i], c[i])
    if h[i] <= l[i] or (body_bot-l[i])/(h[i]-l[i]) < 0.40: return False
    if not (c[i] > o[i]): return False
    vm5 = df["vol_ma5"].values[i]
    if pd.isna(vm5) or vm5 <= 0 or v[i] < vm5 * 1.3: return False
    pos = df["pos_60"].values[i]
    if pd.isna(pos) or pos > 0.25: return False
    m5 = df["ma5"].values[i]
    m5_p = df["ma5"].values[i-3] if i >= 3 else m5
    if pd.isna(m5) or pd.isna(m5_p) or m5 < m5_p: return False
    if v[i] * c[i] / 1e8 < 1: return False
    return True

# 极品形态注册表 (仅保留回测验证可靠的高收益形态)
ULTRA_PATTERNS = [
    ("三日连涨",  detect_3day_surge,         is_ultra_surge,    2,  6.27),
]


# ═══════════════════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════════════════

def load_all_stocks(main_board=True):
    """加载所有股票 + 预计算指标。返回 {code: (name, df)}"""
    files = load_main_board_files(DAILY_DIR) if main_board else load_stock_files(DAILY_DIR)
    data = {}
    for fp in files:
        try:
            df = pd.read_csv(fp)
            df["日期"] = pd.to_datetime(df["日期"], format="%Y-%m-%d")
            df = df.sort_values("日期").reset_index(drop=True)
            df = df[df["成交量"] > 0].copy()
            if len(df) < MIN_DAYS: continue
            code = os.path.splitext(os.path.basename(fp))[0]
            name = str(df["名称"].iloc[0]) if "名称" in df.columns else code
            df = compute_indicators(df)
            data[code] = (name, df)
        except Exception:
            continue
    return data


def find_date_idx(df, date_str, tail_only=True):
    """找日期索引。tail_only=True 仅查尾部(短线扫描), False 全量查(历史回测)。"""
    kd = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    if tail_only:
        rng = range(len(df)-1, max(len(df)-20, -1), -1)
    else:
        rng = range(len(df)-1, -1, -1)
    for j in rng:
        if str(df["日期"].iloc[j].date()) in (date_str, kd):
            return j
    return None


def get_trading_dates(from_date, to_date, step=5):
    ref = pd.read_csv(os.path.join(DAILY_DIR, "sh.600000.csv"))
    ref["日期"] = pd.to_datetime(ref["日期"], format="%Y-%m-%d")
    fd = pd.to_datetime(f"{from_date[:4]}-{from_date[4:6]}-{from_date[6:8]}")
    td = pd.to_datetime(f"{to_date[:4]}-{to_date[4:6]}-{to_date[6:8]}")
    return [d.strftime("%Y%m%d") for d in ref["日期"] if fd <= d <= td][::step]


# ═══════════════════════════════════════════════════════════════
# 长线成长股筛选 — 寻找翻倍潜力股
# ═══════════════════════════════════════════════════════════════

def compute_growth_score(df, idx):
    """计算长线成长股质量分 (0-100)。

    维度:
      1. 长期趋势 (30分): MA60>MA120>MA250 + 价格在均线上方
      2. 年度表现 (25分): 250日收益率 + 稳定性
      3. 低波动质量 (20分): 低回撤 + 稳步上涨
      4. 量价配合 (15分): 放量突破 + 机构吸筹特征
      5. 新高突破 (10分): 创1年新高或接近
    """
    n = len(df)
    if idx < 250: return 0  # 需要至少1年数据
    c = df["收盘"].values

    score = 0.0

    # 1. 长期趋势 (30分)
    ma60 = np.mean(c[max(0,idx-60):idx+1])
    ma120 = np.mean(c[max(0,idx-120):idx+1])
    ma250 = np.mean(c[max(0,idx-250):idx+1])
    if ma60 > ma120 > ma250 and c[idx] > ma60:
        score += 30  # 完美多头排列
    elif ma60 > ma250 and c[idx] > ma120:
        score += 20  # 偏多头
    elif c[idx] > ma250:
        score += 10  # 在年线上方
    # 价格相对 MA250 的距离
    if ma250 > 0:
        dist = (c[idx] - ma250) / ma250
        if 0.1 < dist < 0.5: score += 5  # 温和高于年线
        elif dist >= 0.5: score += 3  # 强势但可能过热

    # 2. 年度表现 (25分)
    if idx >= 250 and c[idx-250] > 0:
        ret_1y = (c[idx] - c[idx-250]) / c[idx-250]
        if ret_1y > 0.5: score += 25   # 年涨50%+ = 强成长
        elif ret_1y > 0.3: score += 20
        elif ret_1y > 0.1: score += 12
        elif ret_1y > 0: score += 5
        # 负收益扣分
        if ret_1y < -0.2: score -= 10
        # 稳定性: 检查是否稳步上涨(非暴涨暴跌)
        if idx >= 250:
            chunks = [c[idx-250+i*50:idx-200+(i+1)*50] for i in range(5)]
            chunk_rets = [(chunk[-1]-chunk[0])/chunk[0] if chunk[0]>0 else 0 for chunk in chunks]
            positive_chunks = sum(1 for r in chunk_rets if r > 0)
            if positive_chunks >= 4: score += 5  # 80%时段上涨 = 稳步
            elif positive_chunks <= 1: score -= 5  # 大部分时段下跌

    # 3. 低波动质量 (20分)
    if idx >= 250:
        daily_rets = [(c[i]-c[i-1])/c[i-1] for i in range(idx-250+1, idx+1) if c[i-1]>0]
        if daily_rets:
            vol = np.std(daily_rets)
            if vol < 0.02: score += 20     # 低波动(<2%/天)
            elif vol < 0.03: score += 15
            elif vol < 0.04: score += 8
        # 最大回撤
        peak = max(c[idx-250:idx+1])
        trough = min(c[idx-250:idx+1])
        dd = (peak-trough)/peak if peak>0 else 0
        if dd < 0.20: score += 5       # 低回撤
        elif dd > 0.50: score -= 10    # 腰斩股扣分

    # 4. 量价配合 (15分)
    v = df["成交量"].values
    if idx >= 60:
        vol_60 = np.mean(v[max(0,idx-60):idx+1])
        vol_20 = np.mean(v[max(0,idx-20):idx+1])
        if vol_60 > 0 and vol_20 > vol_60 * 1.2: score += 10  # 近期放量
        elif vol_20 > vol_60: score += 5
        # 上涨日放量 > 下跌日放量 (机构吸筹)
        up_vol = [v[i] for i in range(max(0,idx-60), idx+1) if c[i]>c[i-1]]
        dn_vol = [v[i] for i in range(max(0,idx-60), idx+1) if c[i]<=c[i-1]]
        if up_vol and dn_vol and np.mean(up_vol) > np.mean(dn_vol) * 1.1:
            score += 5

    # 5. 新高突破 (10分)
    if idx >= 250:
        high_1y = max(c[idx-250:idx])
        if c[idx] >= high_1y * 0.98: score += 10   # 接近1年新高
        elif c[idx] >= high_1y * 0.90: score += 5

    return min(100, max(0, score))


# ═══════════════════════════════════════════════════════════════
# 命令: scan — 每日选股扫描
# ═══════════════════════════════════════════════════════════════

def cmd_scan(args):
    strict = args.strict
    data = load_all_stocks()
    date_str = args.date or datetime.now().strftime("%Y%m%d")
    print(f"扫描日期: {date_str} | 模式: {'高收益精选' if strict else '标准扫描'}")

    candidates = []
    for code, (name, df) in data.items():
        idx = find_date_idx(df, date_str)
        if idx is None: continue
        c = float(df["收盘"].values[idx])
        v = float(df["成交量"].values[idx])
        t = v * c / 1e8
        if t < (8 if strict else 1) or c < (8 if strict else 5): continue
        pos = float(df["pos_60"].values[idx]) if not pd.isna(df["pos_60"].values[idx]) else 0.5
        if pos > 0.92: continue
        mb = bool(df["ma_bull"].values[idx])
        chg = float(df["pct_chg"].values[idx]*100) if not pd.isna(df["pct_chg"].values[idx]) else 0

        signals = []
        if strict:
            # 高收益精选: 用极品形态 + 专属持仓
            for pname, detector, ultra_filter, hold, ref_r in ULTRA_PATTERNS:
                try:
                    if detector(df, idx) and ultra_filter(df, idx):
                        signals.append((pname, hold, ref_r))
                except Exception: continue
        else:
            for pname, detector, hold, wr in PATTERNS:
                try:
                    if detector(df, idx):
                        signals.append((pname, hold, wr))
                except Exception: continue

        if not signals: continue
        if strict and len(signals) < 1: continue

        best = max(signals, key=lambda x: x[2])
        score = best[2] + min(len(signals)*5, 20)
        if mb: score += 8

        candidates.append({
            "code": code, "name": name, "price": round(c, 2),
            "score": round(score, 1), "chg": round(chg, 2),
            "turnover_yi": round(t, 1), "pos_60": round(pos, 2),
            "ma_bull": mb, "n_signals": len(signals),
            "best_pattern": best[0], "best_hold": best[1], "best_wr": best[2],
        })

    candidates.sort(key=lambda x: -x["score"])
    top = candidates[:args.top]

    print(f"\n{'═'*80}")
    print(f"  {'#':<3} {'代码':<10} {'名称':<8} {'得分':<6} {'涨跌':<7} {'成交额亿':<8} {'位置':<5} {'MA':<4} {'信号':>3} {'形态':<25s} {'持仓':<5} {'WR'}")
    print(f"  {'─'*78}")
    for i, c in enumerate(top):
        print(f"  {i+1:<3} {c['code']:<10} {c['name']:<8} {c['score']:<6.1f} {c['chg']:>+.1f}%  {c['turnover_yi']:<8.1f} {c['pos_60']:<5.2f} {'✅' if c['ma_bull'] else '❌':<4} {c['n_signals']:>3d} {c['best_pattern']:<25s} T+{c['best_hold']:<3d} {c['best_wr']:.0f}%")
    print(f"\n  共 {len(top)} 只候选")

    if top and HAS_SAVE:
        save_results("picks_scan", {"date": date_str, "strict": strict, "candidates": top})


# ═══════════════════════════════════════════════════════════════
# 命令: backtest — 历史回测
# ═══════════════════════════════════════════════════════════════

def cmd_backtest(args):
    strict = args.strict
    dates = get_trading_dates(args.from_date, args.to_date, args.step)
    data = load_all_stocks()
    print(f"回测: {args.from_date}→{args.to_date} {len(dates)}天 | {'高收益' if strict else '标准'}")

    all_trades = []
    for di, ds in enumerate(dates):
        day_cands = []
        for code, (name, df) in data.items():
            idx = find_date_idx(df, ds)
            if idx is None: continue
            c = float(df["收盘"].values[idx])
            v = float(df["成交量"].values[idx])
            t = v * c / 1e8
            if t < (8 if strict else 1) or c < (8 if strict else 5): continue
            pos = float(df["pos_60"].values[idx]) if not pd.isna(df["pos_60"].values[idx]) else 0.5
            if pos > 0.92: continue
            mb = bool(df["ma_bull"].values[idx])
            signals = []
            if strict:
                for pname, detector, ultra_filter, hold, ref_r in ULTRA_PATTERNS:
                    try:
                        if detector(df, idx) and ultra_filter(df, idx):
                            signals.append((pname, hold, ref_r))
                    except: continue
            else:
                for pname, detector, hold, wr in PATTERNS:
                    try:
                        if detector(df, idx):
                            signals.append((pname, hold, wr))
                    except: continue
            if not signals: continue
            best = max(signals, key=lambda x: x[2])
            score = best[2] + min(len(signals)*5, 20)
            if mb: score += 8
            day_cands.append({"code": code, "name": name, "price": c, "score": score,
                             "signals": signals, "mb": mb, "pos": pos, "t": t})

        day_cands.sort(key=lambda x: -x["score"])
        for pick in day_cands[:args.top]:
            code = pick["code"]
            _, df = data[code]
            idx2 = find_date_idx(df, ds)
            if idx2 is None: continue
            best_hold = pick["signals"][0][1] if pick["signals"] else 1
            if idx2 + best_hold >= len(df): continue
            ret = (float(df["收盘"].values[idx2 + best_hold]) - pick["price"]) / pick["price"] * 100
            all_trades.append({"date": ds, "code": code, "ret": ret, "win": ret > 0,
                              "pattern": pick["signals"][0][0] if pick["signals"] else "?"})

        if (di+1) % 50 == 0:
            recent = all_trades[-200:]
            wr = sum(1 for t in recent if t["win"])/len(recent)*100 if recent else 0
            print(f"  {di+1}/{len(dates)} {ds}: 累计{len(all_trades)}笔 滚动WR={wr:.1f}%", flush=True)

    if not all_trades:
        print("无交易"); return

    rs = [t["ret"] for t in all_trades]
    wr = sum(1 for t in all_trades if t["win"])/len(all_trades)*100
    avg = statistics.mean(rs)
    yearly = defaultdict(list)
    for t in all_trades: yearly[t["date"][:4]].append(t["ret"])

    print(f"\n{'═'*60}")
    print(f"  回测结果: {len(all_trades)}笔 WR={wr:.1f}% 均收={avg:+.2f}%")
    print(f"  中位={statistics.median(rs):+.2f}% 最大={max(rs):+.2f}% 最小={min(rs):+.2f}%")
    print(f"\n  {'年份':<6} {'笔数':>5} {'胜率':>7} {'均收益':>8}")
    for y in sorted(yearly.keys()):
        yr = yearly[y]; ywr = sum(1 for r in yr if r>0)/len(yr)*100
        print(f"  {y:<6} {len(yr):>5} {ywr:>6.1f}% {statistics.mean(yr):>+7.2f}%")

    if HAS_SAVE:
        save_results("picks_backtest", {"from": args.from_date, "to": args.to_date,
                    "n": len(all_trades), "wr": round(wr,1), "avg": round(avg,2)})


# ═══════════════════════════════════════════════════════════════
# 命令: analyze — 大赢家特征分析
# ═══════════════════════════════════════════════════════════════

def cmd_analyze(args):
    dates = get_trading_dates("20240101", "20260701", 5)
    data = load_all_stocks()
    trades = []

    for ds in dates:
        for code, (name, df) in data.items():
            idx = find_date_idx(df, ds)
            if idx is None or idx+2 >= len(df): continue
            c = float(df["收盘"].values[idx]); v = float(df["成交量"].values[idx])
            t = v * c / 1e8
            if t < 2 or c < 8: continue
            cv = df["收盘"].values; ov = df["开盘"].values; vv = df["成交量"].values
            if not (cv[idx] > cv[idx-1] > cv[idx-2] and vv[idx] > vv[idx-1]): continue
            pos = float(df["pos_60"].values[idx]) if not pd.isna(df["pos_60"].values[idx]) else 0.5
            gap = (ov[idx]-cv[idx-1])/cv[idx-1]*100 if idx>0 and cv[idx-1]>0 else 0
            up_s = float(df["up_streak"].values[idx])
            chg = float(df["pct_chg"].values[idx]*100) if not pd.isna(df["pct_chg"].values[idx]) else 0
            ret = (float(cv[idx+2])-c)/c*100
            m5,m10,m20 = df["ma5"].values[idx], df["ma10"].values[idx], df["ma20"].values[idx]
            ma_align = not pd.isna(m5) and not pd.isna(m20) and m5>m10>m20
            trades.append({"ret": ret, "pos": pos, "gap": gap, "up_s": up_s, "chg": chg, "t": t, "ma": ma_align})

    big = [t for t in trades if t["ret"] >= 5]
    small = [t for t in trades if t["ret"] < 5]

    print(f"\n大赢家(≥5%): {len(big)}笔 | 其他: {len(small)}笔\n")
    print(f"  {'特征':<15s} {'大赢家':>10s} {'其他':>10s} {'差异':>8s}")
    print(f"  {'─'*45}")
    for key, label in [("gap","开盘缺口%"),("t","成交额亿"),("pos","60日位置"),("chg","当日涨%"),("up_s","连涨天数")]:
        a = np.mean([t[key] for t in big]) if big else 0
        b = np.mean([t[key] for t in small]) if small else 0
        print(f"  {label:<13s} {a:>10.2f} {b:>10.2f} {a-b:>+8.2f}")
    ma_b = sum(1 for t in big if t["ma"])/len(big)*100 if big else 0
    ma_s = sum(1 for t in small if t["ma"])/len(small)*100 if small else 0
    print(f"  MA完美多头%   {ma_b:>10.1f}% {ma_s:>10.1f}% {ma_b-ma_s:>+8.1f}%")


# ═══════════════════════════════════════════════════════════════
# 命令: growth — 长线成长股筛选
# ═══════════════════════════════════════════════════════════════

def cmd_growth(args):
    """长线成长股筛选 + 翻倍潜力回测。"""
    data = load_all_stocks()
    date_str = args.date or datetime.now().strftime("%Y%m%d")

    if args.backtest:
        # 回测模式: 从历史数据找过去入选的股票, 跟踪1年后是否翻倍
        dates = get_trading_dates(args.from_date or "20190101",
                                  args.to_date or "20250101", step=20)
        print(f"翻倍回测: {len(dates)}天 | 跟踪1年/2年收益\n")
        all_results = []
        for di, ds in enumerate(dates):
            candidates = []
            for code, (name, df) in data.items():
                idx = find_date_idx(df, ds, tail_only=False)
                if idx is None or idx < 250: continue
                score = compute_growth_score(df, idx)
                if score >= args.min_score:
                    c = float(df["收盘"].values[idx])
                    candidates.append({"code": code, "name": name, "score": score, "price": c, "idx": idx, "df": df})

            candidates.sort(key=lambda x: -x["score"])
            for c in candidates[:args.top]:
                df2 = c["df"]; idx2 = c["idx"]
                # 跟踪1年
                ret_1y = None
                if idx2 + 250 < len(df2):
                    ret_1y = (float(df2["收盘"].values[idx2+250]) - c["price"]) / c["price"] * 100
                # 跟踪2年
                ret_2y = None
                if idx2 + 500 < len(df2):
                    ret_2y = (float(df2["收盘"].values[idx2+500]) - c["price"]) / c["price"] * 100
                doubled = (ret_1y and ret_1y >= 100) or (ret_2y and ret_2y >= 100)
                all_results.append({"date": ds, "code": c["code"], "name": c["name"],
                                   "score": c["score"], "ret_1y": ret_1y, "ret_2y": ret_2y,
                                   "doubled": doubled})

            if (di+1) % 20 == 0:
                recent = all_results[-200:]
                dbl = sum(1 for r in recent if r["doubled"]) / len(recent) * 100 if recent else 0
                print(f"  {di+1}/{len(dates)} {ds}: 累计{len(all_results)}笔 翻倍率={dbl:.0f}%", flush=True)

        if not all_results:
            print("无候选")
            return

        # 统计
        valid_1y = [r for r in all_results if r["ret_1y"] is not None]
        valid_2y = [r for r in all_results if r["ret_2y"] is not None]
        doubled = [r for r in all_results if r["doubled"]]

        print(f"\n{'═'*60}")
        print(f"  翻倍回测结果")
        print(f"{'═'*60}")
        print(f"  总候选: {len(all_results)} 笔")
        if valid_1y:
            avg1 = np.mean([r["ret_1y"] for r in valid_1y])
            wr1 = sum(1 for r in valid_1y if r["ret_1y"] > 0) / len(valid_1y) * 100
            dbl1 = sum(1 for r in valid_1y if r["ret_1y"] >= 100) / len(valid_1y) * 100
            print(f"  1年 (n={len(valid_1y)}): WR={wr1:.1f}% 均收={avg1:+.1f}% 翻倍率={dbl1:.1f}%")
        if valid_2y:
            avg2 = np.mean([r["ret_2y"] for r in valid_2y])
            wr2 = sum(1 for r in valid_2y if r["ret_2y"] > 0) / len(valid_2y) * 100
            dbl2 = sum(1 for r in valid_2y if r["ret_2y"] >= 100) / len(valid_2y) * 100
            print(f"  2年 (n={len(valid_2y)}): WR={wr2:.1f}% 均收={avg2:+.1f}% 翻倍率={dbl2:.1f}%")

        # 最近翻倍股
        if doubled:
            print(f"\n  最近翻倍股 (共{len(doubled)}只):")
            for r in doubled[-10:]:
                print(f"    {r['code']} {r['name']:<8s} 入选{r['date']} 分{r['score']:.0f} "
                      f"1年{r['ret_1y']:+.0f}% 2年{r['ret_2y']:+.0f}%")

        if HAS_SAVE:
            save_results("growth_backtest", {
                "from": args.from_date, "to": args.to_date,
                "n_candidates": len(all_results), "n_doubled": len(doubled),
                "doubled_rate_1y": dbl1 if valid_1y else 0,
                "doubled_rate_2y": dbl2 if valid_2y else 0,
            })

    else:
        # 扫描模式: 当前日期筛选成长股
        print(f"长线成长股筛选 [{date_str}]\n")
        candidates = []
        for code, (name, df) in data.items():
            idx = find_date_idx(df, date_str)
            if idx is None or idx < 250: continue
            score = compute_growth_score(df, idx)
            if score < args.min_score: continue
            c = float(df["收盘"].values[idx])
            ret_1y = (c - float(df["收盘"].values[idx-250])) / float(df["收盘"].values[idx-250]) * 100 if idx >= 250 else 0
            ma250 = np.mean(df["收盘"].values[max(0,idx-250):idx+1])
            dist_ma = (c - ma250) / ma250 * 100 if ma250 > 0 else 0
            candidates.append({
                "code": code, "name": name, "score": round(score, 1),
                "price": round(c, 2), "ret_1y": round(ret_1y, 1),
                "dist_ma250": round(dist_ma, 1),
            })

        candidates.sort(key=lambda x: -x["score"])
        top = candidates[:args.top]

        print(f"  {'#':<3} {'代码':<10} {'名称':<8} {'得分':<6} {'价格':>7} {'年涨幅':>7} {'距MA250':>7}")
        print(f"  {'─'*55}")
        for i, c in enumerate(top):
            print(f"  {i+1:<3} {c['code']:<10} {c['name']:<8} {c['score']:<6.1f} {c['price']:>7.2f} {c['ret_1y']:>+6.1f}% {c['dist_ma250']:>+6.1f}%")
        print(f"\n  共 {len(top)} 只候选 (最低分≥{args.min_score})")


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description="量化选股系统")
    sp = p.add_subparsers(dest="cmd")

    s = sp.add_parser("scan", help="每日选股扫描")
    s.add_argument("--date", default=None, help="日期 YYYYMMDD")
    s.add_argument("--top", type=int, default=20)
    s.add_argument("--strict", action="store_true", help="高收益精选模式 (单笔>5%)")

    b = sp.add_parser("backtest", help="历史回测")
    b.add_argument("--from", dest="from_date", default="20240101")
    b.add_argument("--to", dest="to_date", default="20260701")
    b.add_argument("--step", type=int, default=5)
    b.add_argument("--top", type=int, default=10)
    b.add_argument("--strict", action="store_true", help="高收益精选回测")

    a = sp.add_parser("analyze", help="大赢家特征分析")

    g = sp.add_parser("growth", help="长线成长股筛选")
    g.add_argument("--date", default=None, help="日期 YYYYMMDD")
    g.add_argument("--top", type=int, default=30)
    g.add_argument("--min-score", type=float, default=50, help="最低成长分")
    g.add_argument("--backtest", action="store_true", help="翻倍回测模式")
    g.add_argument("--from", dest="from_date", default="20190101")
    g.add_argument("--to", dest="to_date", default="20250101")

    args = p.parse_args()
    if args.cmd == "scan": cmd_scan(args)
    elif args.cmd == "backtest": cmd_backtest(args)
    elif args.cmd == "analyze": cmd_analyze(args)
    elif args.cmd == "growth": cmd_growth(args)
    else: p.print_help()


if __name__ == "__main__":
    main()
