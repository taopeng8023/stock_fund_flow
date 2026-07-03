#!/usr/bin/env python3
"""
6 策略选股器 v9 — 终极版
- 直接内置 6 个核心形态检测（从 discovery 引擎精简提取）
- 使用报告中各自最优持仓周期 + 确认级别
- 质量评分阈值过滤 → 胜率 ≥85%

策略参数（来自 DISCOVERY_REPORT_20260702.md）:
  S1: ◆ H1+G2 → ▲ T+5    S2: ★ D1 → ★ T+15   S3: A3 → ◆ T+15
  S4: ▲ B1 → ▲ T+2        S5: ★ D2 → ★ T+10   S6: ▲ I1 → ▲ T+5

用法: python strategy_screener.py --sample 2000
"""
import argparse, os, random, sys, warnings
from collections import defaultdict
import numpy as np, pandas as pd

try:
    from baostock_data.analysis.stock_filter import load_stock_files, print_filter_summary
except ImportError:
    from stock_filter import load_stock_files, print_filter_summary

warnings.filterwarnings("ignore")
MIN_DAYS, WIN_THRESHOLD = 80, 0.005


# ═══════════════════════════════════════
# 指标计算（向量化优化版）
# ═══════════════════════════════════════
def compute_indicators(df):
    c = df["收盘"].values; o = df["开盘"].values
    h = df["最高"].values; l = df["最低"].values
    v = df["成交量"].values; pc = df["前收盘"].values
    n = len(df)

    s = pd.Series(c)
    for w in [5, 10, 20, 60]:
        df[f"ma{w}"] = s.rolling(w, min_periods=w).mean().values
    sv = pd.Series(v)
    for w in [5, 20]:
        df[f"vol_ma{w}"] = sv.rolling(w, min_periods=w).mean().values

    df["candle_body"] = c - o
    df["candle_range"] = h - l
    cr = df["candle_range"].values
    cb = np.abs(df["candle_body"].values)
    df["body_ratio"] = np.where(cr > 0, cb / cr, 0)
    df["upper_shadow"] = np.where(cr > 0, (h - np.maximum(o, c)) / cr, 0)
    df["lower_shadow"] = np.where(cr > 0, (np.minimum(o, c) - l) / cr, 0)
    df["is_yang"] = (c > o).astype(int)
    df["is_yin"] = (c < o).astype(int)
    df["amplitude"]  = np.where(pc > 0, df["candle_range"].values / pc, 0)
    df["change_pct"]  = np.where(pc > 0, (c - pc) / pc, 0)
    df["vol_ratio_vs5"]  = np.where(df["vol_ma5"].values > 0, v / df["vol_ma5"].values, 1)
    ma5v, ma10v, ma20v = df["ma5"].values, df["ma10"].values, df["ma20"].values
    df["ma_bull"] = ((ma5v > ma10v) & (ma10v > ma20v)).astype(int)
    for w in [5, 10, 20]:
        mv = df[f"ma{w}"].values
        df[f"dist_ma{w}"] = np.where((~np.isnan(mv)) & (mv > 0), (c - mv) / mv, 0)
    for w in [20, 60]:
        df[f"high_{w}d"] = pd.Series(h).rolling(w, min_periods=w).max().values
        df[f"close_high_{w}d"] = pd.Series(c).rolling(w, min_periods=w).max().values
        df[f"close_low_{w}d"]  = pd.Series(c).rolling(w, min_periods=w).min().values

    # 连阳/连阴 向量化
    is_y = df["is_yang"].values
    ys_arr = np.zeros(n, dtype=int); ns_arr = np.zeros(n, dtype=int)
    ys = ns = 0
    for i in range(n):
        if is_y[i]: ys += 1; ns = 0
        else: ns += 1; ys = 0
        ys_arr[i] = ys; ns_arr[i] = ns
    df["yang_streak"] = ys_arr; df["yin_streak"] = ns_arr

    ch20 = df["close_high_20d"].values; cl20 = df["close_low_20d"].values
    rank = np.where(ch20 - cl20 > 0, (c - cl20) / (ch20 - cl20), 0.5)
    rank[:20] = 0.5
    df["close_rank_20"] = rank
    df["volatility_20"] = pd.Series(df["change_pct"].values).rolling(20, min_periods=20).std().values
    return df


# ═══════════════════════════════════════
# 确认逻辑
# ═══════════════════════════════════════
def confirm_ultra(df, si):
    """▲: T+1 close > T high + yang + volume up"""
    n = len(df); j = si + 1
    if j >= n: return False
    cv, hv = df["收盘"].values, df["最高"].values
    lv, ov = df["最低"].values, df["开盘"].values
    vv = df["成交量"].values; is_y = df["is_yang"].values
    vr5 = df["vol_ratio_vs5"].values
    if ov[j] > 0 and (cv[j] - ov[j]) / ov[j] < -0.06: return False
    if lv[j] < lv[si] * 0.97: return False
    if vr5[j] < 0.25: return False
    return cv[j] > hv[si] and is_y[j] and vv[j] > vv[si]


def confirm_std(df, si):
    """Standard: T+1 close > T close"""
    n = len(df); j = si + 1
    if j >= n: return False
    cv = df["收盘"].values; lv = df["最低"].values
    ov = df["开盘"].values; vr5 = df["vol_ratio_vs5"].values
    if ov[j] > 0 and (cv[j] - ov[j]) / ov[j] < -0.06: return False
    if lv[j] < lv[si] * 0.97: return False
    if vr5[j] < 0.25: return False
    return cv[j] > cv[si]


def regime_ok(df, si, regime):
    if si < 5: return False if regime == "bull" else True
    m20 = df["ma20"].values; cv = df["收盘"].values
    # MA20 slope check — 基础趋势过滤（所有策略强制）
    if pd.isna(m20[si]) or pd.isna(m20[si-5]) or m20[si-5] <= 0:
        return False if regime == "bull" else True  # bull 必须有能力计算
    slope = (m20[si] - m20[si-5]) / m20[si-5]
    if slope < -0.07:  # MA20 暴跌中不做多
        return False
    if regime == "bull":
        return slope > 0 and cv[si] > m20[si]
    return True


def fwd_ret(df, entry_idx, hold):
    n = len(df); c = df["收盘"].values
    e = entry_idx + hold
    return (c[e] - c[entry_idx]) / c[entry_idx] if e < n and c[entry_idx] > 0 else None


# ═══════════════════════════════════════
# 6 个形态检测（复制自 kline_discovery.py）
# ═══════════════════════════════════════
def detect(d, i, name):
    c = d["收盘"].values; o = d["开盘"].values; h = d["最高"].values
    l = d["最低"].values; v = d["成交量"].values
    is_yang = d["is_yang"].values; is_yin = d["is_yin"].values
    ys = d["yang_streak"].values; ns = d["yin_streak"].values
    vr5 = d["vol_ratio_vs5"].values; amp = d["amplitude"].values
    br = d["body_ratio"].values; ls_ = d["lower_shadow"].values
    us_ = d["upper_shadow"].values; chg = d["change_pct"].values
    ma5 = d["ma5"].values; ma10 = d["ma10"].values; ma20 = d["ma20"].values
    cr = d["close_rank_20"].values; mab = d["ma_bull"].values
    dist5 = d["dist_ma5"].values; dist10 = d["dist_ma10"].values
    dist20 = d["dist_ma20"].values

    # H1: 三连阳 + 量递增 + 温和涨幅 + 突破MA20
    if name == "H1":
        return (ys[i] >= 3 and v[i] > v[i-1] > v[i-2] and
                i >= 5 and c[i-5] > 0 and (c[i]-c[i-5])/c[i-5] < 0.15 and
                not pd.isna(ma20[i]) and c[i] > ma20[i] and c[i-2] <= ma20[i-2])

    # G2: MA5金叉MA10 + 三线收敛 + 放量阳
    if name == "G2":
        return (i >= 1 and not pd.isna(ma5[i]) and not pd.isna(ma10[i]) and
                not pd.isna(ma20[i]) and not pd.isna(ma5[i-1]) and not pd.isna(ma10[i-1]) and
                ma5[i-1] <= ma10[i-1] and ma5[i] > ma10[i] and ma5[i] > 0 and
                max(abs(ma5[i]-ma10[i]),abs(ma10[i]-ma20[i]),abs(ma5[i]-ma20[i]))/ma5[i] < 0.025 and
                vr5[i] > 1.1 and is_yang[i])

    # D1: 启明星
    if name == "D1":
        return (i >= 3 and ns[i-1] >= 3 and v[i-3] > v[i-2] > v[i-1] and
                br[i-1] < 0.2 and is_yang[i] and vr5[i] > 1.1 and
                c[i] > (o[i-3] + c[i-3]) / 2)

    # A3: 急跌12%
    if name == "A3":
        return (i >= 5 and c[i-5] > 0 and (c[i]-c[i-5])/c[i-5] < -0.12 and
                ls_[i] > 0.6 and is_yang[i] and vr5[i] > 1.5 and
                not pd.isna(cr[i]) and cr[i] < 0.25)

    # B1: 强多头回踩MA20
    if name == "B1":
        return (mab[i] and not pd.isna(ma20[i]) and ma20[i] > 0 and
                abs(dist20[i]) < 0.015 and vr5[i] < 0.45 and
                br[i] < 0.35 and amp[i] < 0.025 and is_yang[i] and c[i] > ma20[i])

    # D2: 反包
    if name == "D2":
        return (i >= 2 and is_yin[i-1] and is_yang[i-2] and
                c[i-1] < o[i-2] and o[i-1] > c[i-2] and
                is_yang[i] and c[i] > o[i-1] and o[i] < c[i-1] and vr5[i] > 1.3)

    # I1: 三日连涨逼60高
    if name == "I1":
        h60 = d["high_60d"].values
        return (i >= 2 and all(chg[j] > 0.015 for j in range(i-2, i+1)) and
                v[i] > v[i-1] > v[i-2] and
                not pd.isna(h60[i]) and c[i] > h60[i] * 0.97 and amp[i] > 0.03)

    return False


# ═══════════════════════════════════════
# 质量评分
# ═══════════════════════════════════════
def quality_score(df, i, strategy_name):
    """Per-strategy quality scoring — 基于校准数据的最优特征"""
    c = df["收盘"].values; v = df["成交量"].values
    h = df["最高"].values; mab = df["ma_bull"].values
    vr5 = df["vol_ratio_vs5"].values; cr_ = df["close_rank_20"].values
    volty = df["volatility_20"].values; amp = df["amplitude"].values
    br = df["body_ratio"].values; is_y = df["is_yang"].values
    ma20 = df["ma20"].values; ma5_v = df["ma5"].values
    j = i + 1
    t1_body = br[j] if j < len(df) and not pd.isna(br[j]) else 0
    t1_yang = is_y[j] if j < len(df) else 0

    if strategy_name == "S3_超跌反弹":
        # Core: MA20 slope + low vol + strong T+1 body
        s = 30.0
        if i>=5 and not pd.isna(ma20[i]) and not pd.isna(ma20[i-5]) and ma20[i-5]>0:
            slope = (ma20[i]-ma20[i-5])/ma20[i-5]
            if slope > -0.03: s += 25  # MA20 not crashing
            elif slope > -0.06: s += 10
        if t1_body > 0.4: s += 25
        elif t1_body > 0.25: s += 10
        if i>=20 and not pd.isna(volty[i]) and volty[i] < 0.05: s += 10
        if not pd.isna(cr_[i]) and cr_[i] < 0.15: s += 10  # extreme low rank is good for oversold
        return s

    elif strategy_name == "S2_启明星":
        # Critical: non-excessive volume, strong T+1 confirmation
        s = 20.0
        if not pd.isna(vr5[i]) and vr5[i] < 2.0: s += 20  # moderate volume better
        if t1_body > 0.5: s += 30
        elif t1_body > 0.3: s += 15
        if i>=20 and not pd.isna(volty[i]) and volty[i] < 0.04: s += 15
        if not pd.isna(cr_[i]) and cr_[i] < 0.30: s += 10  # low rank
        if mab[i]: s += 5  # bull alignment bonus
        return s

    elif strategy_name == "S5_反包":
        # Best in non-extreme bull environments, strong engulf
        s = 15.0
        if not mab[i]: s += 20  # non-bull works better (calibration data)
        if t1_body > 0.5: s += 25
        elif t1_body > 0.3: s += 10
        if not pd.isna(vr5[i]) and 1.3 < vr5[i] < 3.0: s += 15
        if i>=20 and not pd.isna(volty[i]) and volty[i] < 0.05: s += 10
        if not pd.isna(cr_[i]) and 0.15 < cr_[i] < 0.75: s += 10
        if t1_yang: s += 5
        return s

    elif strategy_name == "S6_趋势加速":
        # Strong momentum + moderate extension
        s = 15.0
        if mab[i]: s += 20
        if i>=5 and not pd.isna(ma20[i]) and not pd.isna(ma20[i-5]) and ma20[i-5]>0:
            if (ma20[i]-ma20[i-5])/ma20[i-5] > 0.005: s += 10
        if t1_body > 0.4: s += 15
        if not pd.isna(vr5[i]) and 1.2 < vr5[i] < 2.5: s += 10
        if i>=20 and not pd.isna(volty[i]):
            if 0.015 < volty[i] < 0.05: s += 10
        if not pd.isna(cr_[i]) and 0.3 < cr_[i] < 0.85: s += 10  # mid-to-high rank
        if t1_yang: s += 5
        if not pd.isna(amp[i]) and amp[i] > 0.035: s += 5  # strong amplitude
        return s

    elif strategy_name == "S1_双叠加":
        # Breakout quality: MA alignment + volume
        s = 10.0
        if not pd.isna(ma5_v[i]) and not pd.isna(ma20[i]) and ma5_v[i] > ma20[i]: s += 20
        if mab[i]: s += 15
        if i>=5 and not pd.isna(ma20[i]) and not pd.isna(ma20[i-5]) and ma20[i-5]>0:
            if (ma20[i]-ma20[i-5])/ma20[i-5] > 0: s += 10
        if t1_body > 0.4: s += 20
        elif t1_body > 0.25: s += 10
        if not pd.isna(vr5[i]) and 1.1 < vr5[i] < 2.5: s += 10
        if i>=20 and not pd.isna(volty[i]) and volty[i] < 0.045: s += 10
        if not pd.isna(cr_[i]) and 0.2 < cr_[i] < 0.70: s += 5
        return s

    else:  # S4_均线回调
        s = 10.0
        if mab[i]: s += 25
        if i>=5 and not pd.isna(ma20[i]) and not pd.isna(ma20[i-5]) and ma20[i-5]>0:
            if (ma20[i]-ma20[i-5])/ma20[i-5] > 0.005: s += 15
        if t1_body > 0.4: s += 20
        if t1_yang: s += 10
        if not pd.isna(vr5[i]) and vr5[i] < 0.35: s += 10  # extreme low vol is key
        if i>=20 and not pd.isna(volty[i]) and volty[i] < 0.03: s += 10
        return s


def load_stock_csv(fp):
    try:
        df = pd.read_csv(fp)
        if len(df) < MIN_DAYS: return None
        df["日期"] = pd.to_datetime(df["日期"], format="%Y-%m-%d")
        df = df.sort_values("日期").reset_index(drop=True)
        df = df[df["成交量"] > 0].copy()
        return df if len(df) >= MIN_DAYS else None
    except: return None


# ═══════════════════════════════════════
# 主流程
# ═══════════════════════════════════════
def run(data_dir, target_wr=85.0, sample=0, top_n=20, seed=42):
    random.seed(seed); np.random.seed(seed)
    stock_files = load_stock_files(data_dir)
    if not stock_files: print("错误: 无个股数据"); sys.exit(1)
    files = random.sample(stock_files, min(sample, len(stock_files))) if sample > 0 else stock_files

    print_filter_summary(data_dir)
    print(f"训练样本: {len(files)} 只 | 策略: 6 | 目标: ≥{target_wr}% | seed={seed}")

    # 策略: (name, detect_name_or_COMBO, confirm, regime, hold)
    strats = [
        ("S1_双叠加",  "COMBO",    confirm_ultra, "any",  5),
        ("S2_启明星",  "D1",       confirm_ultra, "bull", 15),
        ("S3_超跌反弹", "A3",       confirm_std,   "any",  15),
        ("S4_均线回调", "B1",       confirm_ultra, "any",  2),
        ("S5_反包",    "D2",       confirm_ultra, "bull", 10),
        ("S6_趋势加速", "I1",       confirm_ultra, "any",  5),
    ]

    signals = []; processed = 0
    for fp in files:
        df = load_stock_csv(fp)
        if df is None: continue
        code = os.path.splitext(os.path.basename(fp))[0]
        name = df["名称"].iloc[0] if "名称" in df.columns else code
        df = compute_indicators(df)
        nd = len(df); dt = df["日期"].dt.strftime("%Y-%m-%d").values; cp_ = df["收盘"].values

        # 先收集当天所有策略信号，再计算共识加分
        day_sigs = defaultdict(list)  # {entry_date: [(signal_idx, strategy, hold, qs)]}
        for i in range(70, nd - 2):
            for sn, dn, cfn, reg, hold in strats:
                try:
                    if dn == "COMBO":
                        if not (detect(df, i, "H1") and detect(df, i, "G2")): continue
                    else:
                        if not detect(df, i, dn): continue
                    if not regime_ok(df, i, reg): continue
                    if not cfn(df, i): continue
                except Exception: continue
                ei = i + 1; day = dt[ei]
                qs = quality_score(df, i, sn)
                day_sigs[day].append((ei, sn, hold, qs))
        # 共识加分: 同日多策略信号 +15分/额外策略
        for day, s_list in day_sigs.items():
            n_strats = len(set(s[1] for s in s_list))
            consensus_bonus = (n_strats - 1) * 20  # 2策略+20, 3策略+40...
            for ei, sn, hold, qs in s_list:
                ret = fwd_ret(df, ei, hold)
                if ret is not None:
                    signals.append({"qs": qs + consensus_bonus, "ret": ret,
                        "strategy": sn, "hold": hold,
                        "code": code, "name": name,
                        "date": day, "entry_price": cp_[ei],
                        "consensus": n_strats})

        processed += 1
        if processed % 200 == 0:
            print(f"  {processed}/{len(files)} 信号:{len(signals)}", flush=True)

    print(f"  完成: {processed} 只, 总信号: {len(signals)}\n")
    if not signals: print("无信号"); return False

    signals.sort(key=lambda x: x["qs"], reverse=True)
    rets = [s["ret"] for s in signals]

    print("═" * 65)
    print("  质量阈值扫描")
    print("═" * 65)
    print(f"  {'阈值≥':>6s} {'胜率':>7s} {'样本':>6s} {'均收益':>8s} {'达标':>5s}")
    print("-"*65)
    best_th, best_n = None, 0
    for pct in [90, 80, 70, 60, 50, 40, 30, 25, 20, 15, 10, 5]:
        nt = max(5, int(len(signals)*pct/100))
        th = signals[nt-1]["qs"] if nt > 0 else 0
        sub = rets[:nt]; wins = sum(1 for r in sub if r > WIN_THRESHOLD)
        wr = wins/len(sub)*100; ar = np.mean(sub)*100
        ok = "✅" if wr >= target_wr else ""
        if wr >= target_wr and len(sub) > best_n: best_th = th; best_n = len(sub)
        print(f"  {th:6.0f}  {wr:6.1f}%  {len(sub):5d}  {ar:+7.2f}%  {ok:>4s}")
    print("-"*65)

    all_pass = False
    if best_th is not None:
        print(f"  ✅ 阈值 ≥{best_th:.0f} → 胜率 ≥{target_wr}% (n={best_n})")
        all_pass = True
    else:
        bw, nn = 0, 0
        for nt in range(5, len(signals), max(1, len(signals)//50)):
            sub = rets[:nt]; wins = sum(1 for r in sub if r > WIN_THRESHOLD)
            wr = wins/len(sub)*100
            if wr > bw: bw = wr; nn = nt
        print(f"  ❌ 最高: {bw:.1f}% (n={nn})")
        best_th = signals[nn-1]["qs"] if nn > 0 else 0

    print(f"\n── 各策略 ──")
    for sn, dn, cfn, reg, hold in strats:
        ss = [s for s in signals if s["strategy"] == sn]
        if ss:
            qq = [s for s in ss if s["qs"] >= best_th]
            awr = sum(1 for s in ss if s["ret"] > WIN_THRESHOLD)/len(ss)*100
            qwr = sum(1 for s in qq if s["ret"] > WIN_THRESHOLD)/len(qq)*100 if qq else 0
            print(f"  {sn:10s} T+{hold:<3d} 总:{len(ss):4d} 胜率:{awr:5.1f}%  达标:{len(qq):3d} 胜率:{qwr:5.1f}%")
        else:
            print(f"  {sn:10s} T+{hold:<3d}  无信号")

    # 综合评估
    s3_sigs = [s for s in signals if s["strategy"] == "S3_超跌反弹"]
    s3_wr = sum(1 for s in s3_sigs if s["ret"] > WIN_THRESHOLD) / len(s3_sigs) * 100 if s3_sigs else 0
    print(f"\n── 综合评估 ──")
    if all_pass: print(f"  ✅ 质量阈值 ≥{best_th:.0f} 处胜率 ≥{target_wr}% (n={best_n})")
    elif s3_wr >= target_wr: print(f"  ⚠ S3策略独立达标 {s3_wr:.1f}% (n={len(s3_sigs)}), 综合最佳: {bw:.1f}%")
    else: print(f"  ⚠ 综合最佳胜率: {bw:.1f}% @ n={nn}")

    qualified = [s for s in signals if s["qs"] >= best_th]
    if qualified:
        qualified.sort(key=lambda x: (x["date"], x["qs"]), reverse=True)
        ld = max(s["date"] for s in qualified)
        recent = [s for s in qualified if s["date"] == ld]
        recent.sort(key=lambda x: x["qs"], reverse=True)
        print(f"\n══ 最新信号 (qs≥{best_th:.0f}) ══")
        print(f"  {ld} — {len(recent)} 个")
        print(f"  {'代码':10s} {'名称':8s} {'策略':10s} {'持仓':4s} {'共识':4s} {'质量分':>6s} {'入场价':>8s}")
        print("-"*55)
        for s in recent[:top_n]:
            print(f"  {s['code']:10s} {str(s['name']):8s} {s['strategy']:10s} "
                  f"T+{s['hold']:<3d} {s.get('consensus',1):4d}票 {s['qs']:6.0f} {s['entry_price']:8.2f}")

    print(f"\n{'═'*65}")
    return all_pass


def main():
    p = argparse.ArgumentParser(description="6策略选股器 v10")
    p.add_argument("--date", default="", help="数据日期 YYYYMMDD（仅用于标识，不影响数据路径）")
    p.add_argument("--target", type=float, default=85.0)
    p.add_argument("--sample", type=int, default=2000)
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(os.path.dirname(script_dir), "data", "daily")
    if not os.path.isdir(data_dir): print(f"错误: {data_dir}"); sys.exit(1)
    ok = run(data_dir, args.target, args.sample, args.top, args.seed)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
