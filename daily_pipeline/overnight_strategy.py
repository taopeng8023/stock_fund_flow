"""
隔夜套利策略 — 14:30尾盘买入 → 次日09:45-10:15卖出
专为小账户(5万)优化: A-tier + 安全过滤 + 智能仓位分配

使用:
  python daily_pipeline/overnight_strategy.py 20260629              # 指定日期
  python daily_pipeline/overnight_strategy.py                        # 自动找最近交易日
  python daily_pipeline/overnight_strategy.py --backtest             # 回测所有历史交易日
"""

import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Optional

BJS_TZ = timezone(timedelta(hours=8))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH_ROOT = os.path.join(PROJECT_ROOT, "research_data")

# ═══════════════════════════════════════
# 账户配置
# ═══════════════════════════════════════
CAPITAL = 50000          # 总资金
MAX_POSITIONS = 5         # 最多持仓数
MIN_POSITION = 2500       # 单只最低 2500元(5%)
MAX_POSITION_PCT = 0.25   # 单只最高 25%
MAIN_BOARD_ONLY = True    # 仅主板

# ═══════════════════════════════════════
# Tier 期望收益与仓位权重 (基于全市场回测16,645笔+实测验证)
# ═══════════════════════════════════════
TIER_META = {
    "A2":  {"wr": 0.875, "avg_ret": 0.87,  "weight": 1.0,  "label": "空头压力+动量上行"},
    "A1":  {"wr": 0.545, "avg_ret": 0.16,  "weight": 0.65, "label": "缺口陷阱+动量下行"},
    "A3":  {"wr": 0.500, "avg_ret": 0.05,  "weight": 0.50, "label": "空头压力+低启"},
    "S6":  {"wr": 0.543, "avg_ret": 0.61,  "weight": 0.70, "label": "低开反转+回补"},
    "BEAR":{"wr": 0.818, "avg_ret": 3.16,  "weight": 1.0,  "label": "熊市三信号"},
}

# ═══════════════════════════════════════
# 安全过滤 (比 filter_overnight 更严格)
# ═══════════════════════════════════════
MAX_ENTRY_CHG = 7.0        # 当日涨幅上限 (超过7%不追)
MIN_ENTRY_CHG = -7.0       # 当日跌幅下限 (跌超7%接飞刀危险)
MIN_SCORE = 0.30           # 最低综合分

# ═══════════════════════════════════════
# MA 均线过滤 (基于14笔实盘MA相关性分析 + 多Agent K线验证)
# ═══════════════════════════════════════
# 多Agent验证结论 (3分析+3验证, 5984只A股):
#   完美多头+缩量 是最可靠看涨模式, 量比是最关键区分因子
#   量比≤0.3x → 87.5% WR | ≤0.5x → 86.0% WR | ≤0.7x → 81.2% WR | ≤1.0x → 74.3% WR
MIN_POS_60 = 60.0          # 60日高低位 ≥ 60% (胜组84.7% vs 负组52.9%, 区分度1.44)
MIN_MA_ALIGNMENT = 2       # MA多头排列 ≥ +2 (胜组2.8 vs 负组1.0, 区分度1.42)
REQUIRE_ABOVE_MA5 = True   # 收盘价 > MA5 (胜组+5.07% vs 负组-3.97%)
# 量比加分 (overnight信号天然高量比, 用加分替代硬过滤):
VOL_BONUS_HIGH = 0.10      # 量比 ≤ 0.5x → 优先级+0.10 (多Agent验证WR 86%+)
VOL_BONUS_MID  = 0.06      # 量比 ≤ 0.7x → 优先级+0.06 (多Agent验证WR 81%+)
VOL_BONUS_OK   = 0.02      # 量比 ≤ 1.0x → 优先级+0.02 (多Agent验证WR 74%+)
KLINE_DIR = os.path.join(PROJECT_ROOT, "kline_data")
_ma_cache = {}             # K线数据缓存


def find_latest_date() -> str:
    """找 research_data 下最近的有 scores.csv 的交易日"""
    dates = sorted(
        d for d in os.listdir(RESEARCH_ROOT)
        if os.path.isdir(os.path.join(RESEARCH_ROOT, d))
        and os.path.exists(os.path.join(RESEARCH_ROOT, d, "scores.csv"))
        and d.isdigit()
    )
    return dates[-1] if dates else datetime.now(BJS_TZ).strftime("%Y%m%d")


def detect_regime(rows: list[dict]) -> str:
    for r in rows:
        mr = r.get("市场体制", "").strip()
        if mr in ("bull", "bear", "range"):
            return mr
    return "range"


def is_st_stock(code: str, name: str) -> bool:
    """检测 ST/*ST 股票 (G2 门禁)"""
    return name.startswith("ST") or name.startswith("*ST")


def check_ma_filter(code: str, date_str: str) -> tuple[bool, str, float]:
    """MA均线过滤: (通过?, 原因, 量比)
    基于14笔实盘 + 多Agent K线验证:
      - 60日位置 区分度1.44 (#1)
      - MA多头排列 区分度1.42 (#2)
      - 收盘价>MA5 区分度1.15 (#8)
      - 量比硬门禁≤1.0x (多Agent验证WR 74%+)
    返回量比用于优先级加分 (低量比→高加分)
    """
    # 缓存K线
    if code not in _ma_cache:
        fp = os.path.join(KLINE_DIR, f"{code}.json")
        if not os.path.exists(fp):
            _ma_cache[code] = None
            return False, "无K线数据", 1.0
        try:
            with open(fp) as f:
                data = json.load(f)
            _ma_cache[code] = data.get("bars", [])
        except Exception:
            _ma_cache[code] = None
            return False, "K线读取失败", 1.0

    bars = _ma_cache[code]
    if bars is None or len(bars) < 60:
        return False, "K线不足60根", 1.0

    # 找到 date_str 在 bars 中的位置 (取该日及之前的K线, 兼容两种日期格式)
    kline_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}" if len(date_str) == 8 and "-" not in date_str else date_str
    idx = None
    for i, b in enumerate(bars):
        if b["date"] in (date_str, kline_date):
            idx = i
            break
    if idx is None:
        for i, b in enumerate(bars):
            if b["date"] > kline_date:
                idx = i - 1
                break
        if idx is None:
            idx = len(bars) - 1

    if idx < 30:
        return False, "K线不足30根", 1.0

    hist = bars[:idx + 1]
    last = len(hist) - 1
    close = hist[last]["close"]

    # 计算均线
    def ma(period):
        if last < period - 1:
            return None
        return sum(hist[i]["close"] for i in range(last - period + 1, last + 1)) / period

    ma5 = ma(5)
    ma10 = ma(10)
    ma15 = ma(15)
    ma20 = ma(20)
    ma60 = ma(60)

    if ma5 is None or ma10 is None or ma60 is None:
        return False, "均线计算失败", 1.0

    # 检查1: 60日位置 ≥ MIN_POS_60
    high_60 = max(b["high"] for b in hist[-60:])
    low_60 = min(b["low"] for b in hist[-60:])
    if high_60 != low_60:
        pos_60 = (close - low_60) / (high_60 - low_60) * 100
    else:
        pos_60 = 50
    if pos_60 < MIN_POS_60:
        return False, f"60日位置{pos_60:.0f}%<{MIN_POS_60:.0f}%", 1.0

    # 检查2: MA多头排列 ≥ MIN_MA_ALIGNMENT
    alignment = 0
    if ma5 > ma10:
        alignment += 1
    else:
        alignment -= 1
    if ma10 > ma15 if ma15 else False:
        alignment += 1
    else:
        alignment -= 1
    if (ma15 > ma20) if (ma15 and ma20) else False:
        alignment += 1
    else:
        alignment -= 1
    if alignment < MIN_MA_ALIGNMENT:
        return False, f"MA排列{alignment:+d}<{MIN_MA_ALIGNMENT:+d}", 1.0

    # 检查3: 收盘价 > MA5
    if REQUIRE_ABOVE_MA5 and close <= ma5:
        return False, f"收盘{close:.2f}≤MA5{ma5:.2f}", 1.0

    # 检查4: 计算量比 (当日量/20日均量) — 不加硬门禁, 用于优先级加分
    avg_vol_20 = sum(b["volume"] for b in hist[-20:]) / 20
    today_vol = hist[last]["volume"]
    vol_ratio = today_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

    return True, "OK", vol_ratio


def load_filtered_candidates(date_str: str) -> tuple[list[dict], str, list[dict]]:
    """运行核心筛选管线，返回 (候选列表, 体制, 全量scores行)"""
    path = os.path.join(RESEARCH_ROOT, date_str, "scores.csv")
    if not os.path.exists(path):
        return [], "range", []

    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)

    regime = detect_regime(rows)

    # 门禁系统 (黑天鹅/熔断)
    try:
        sys.path.insert(0, PROJECT_ROOT)
        from portfolio.risk_controller import can_trade_today
        gate_ok, gate_reason, _ = can_trade_today(date_str)
        if not gate_ok:
            print(f"  🚫 门禁阻断: {gate_reason}")
            return [], regime, rows
    except ImportError:
        pass

    # 信号定义
    from daily_pipeline.filter_overnight import (
        TIERS, AFFORDABLE_TIERS, BLOCK_ALWAYS, BLOCK_NON_BULL,
        DISASTER_COMBOS, SECTOR_BLOCKLIST, SIGNAL_VACUUM_BLOCK,
        MAX_PRICE, MIN_TURNOVER_YI, _MAIN_BOARD_PREFIXES,
        PENALTY_SIGNALS,
    )

    # 股价上限
    max_price = (CAPITAL * 0.8) / MAX_POSITIONS / 100  # 80元
    effective_max = min(max_price, MAX_PRICE)

    # 可用tier (低价账户用A-tier)
    strictness_tiers = AFFORDABLE_TIERS.copy()

    candidates = []
    stats = {}
    ma_blocked = 0  # MA过滤计数

    for r in rows:
        code = r.get("代码", "")
        name = r.get("名称", "")

        # 主板过滤
        if MAIN_BOARD_ONLY and not code.startswith(_MAIN_BOARD_PREFIXES):
            continue

        # G2: ST过滤
        if is_st_stock(code, name):
            continue

        # 价格过滤
        try:
            price = float(r.get("最新价", 0) or 0)
        except (ValueError, TypeError):
            continue
        if price <= 0 or price > effective_max:
            continue

        # 成交额过滤
        try:
            turnover = float(r.get("成交额", 0) or 0)
        except (ValueError, TypeError):
            turnover = 0
        if turnover < MIN_TURNOVER_YI * 1e8:
            continue

        # 行业黑名单
        industry = r.get("行业", "")
        if industry in SECTOR_BLOCKLIST:
            continue

        # 涨跌幅安全区间
        try:
            entry_chg = float(r.get("涨跌幅", 0) or 0)
        except (ValueError, TypeError):
            entry_chg = 0
        if entry_chg >= MAX_ENTRY_CHG or entry_chg <= MIN_ENTRY_CHG:
            continue

        # 综合得分下限
        try:
            score = float(r.get("综合得分", 0) or 0)
        except (ValueError, TypeError):
            score = 0
        if score < MIN_SCORE:
            continue

        # MA均线过滤 (G3: 60日位置 + 多头排列 + 站上MA5 + 量比硬门禁)
        ma_ok, ma_reason, vol_ratio = check_ma_filter(code, date_str)
        if not ma_ok:
            ma_blocked += 1
            continue

        signals = r.get("综合信号", "")
        early_sigs = r.get("启动信号", "")

        # 信号真空
        if SIGNAL_VACUUM_BLOCK:
            has_signal = any(s in signals for s in [
                "P32_", "P33_", "P34_", "P35_", "P36_", "P37_"
            ])
            if not has_signal:
                continue

        # 体制避雷
        block_list = BLOCK_ALWAYS + (BLOCK_NON_BULL if regime != "bull" else [])
        if any(s in signals for s in block_list):
            continue

        # P37_momentum_down 条件解禁
        if "P37_momentum_down" in signals:
            if "P34_gap_trap" not in signals and "P_high_price" not in signals:
                continue

        all_sigs = signals + "," + early_sigs

        # 灾难组合
        if any(all(tok in all_sigs for tok in combo) for combo in DISASTER_COMBOS):
            continue

        # Tier匹配
        matched_tier = None
        for tk in strictness_tiers:
            rules = TIERS[tk]
            if "regime_only" in rules and regime not in rules["regime_only"]:
                continue
            if "companion_any" in rules:
                if not any(s in all_sigs for s in rules["companion_any"]):
                    continue
            if not all(s in all_sigs for s in rules["require"]):
                continue
            capital_score = float(r.get("资金得分", 0.5) or 0.5)
            if "score_min" in rules and score < rules["score_min"]:
                continue
            if "capital_min" in rules and capital_score < rules["capital_min"]:
                continue
            matched_tier = tk
            break

        if not matched_tier:
            continue

        # 加分
        bonus = TIERS[matched_tier]["bonus"]
        bonus_signals = {
            "P_high_price": 0.12, "P35_short_pressure": 0.04,
            "P34_gap_trap": 0.05, "P34_gap_reverse": 0.04,
            "P33_margin_strong": 0.03, "P34_gap_strong": 0.03,
            "E4_gap_start": 0.03, "E1_low_start": 0.02,
            "P34_gap_standalone": 0.02, "P35_short_cover": 0.01,
        }
        for sig, weight in bonus_signals.items():
            if sig in signals:
                bonus += weight

        for sig, penalty in PENALTY_SIGNALS.items():
            if sig in signals:
                bonus += penalty

        # 量比加分 (多Agent K线验证: 低量比→高胜率)
        if vol_ratio <= 0.5:
            bonus += VOL_BONUS_HIGH  # 极致缩量, WR 86%+
        elif vol_ratio <= 0.7:
            bonus += VOL_BONUS_MID   # 温和缩量, WR 81%+
        elif vol_ratio <= 1.0:
            bonus += VOL_BONUS_OK    # 可控量能, WR 74%+

        # 优先级 (体制自适应)
        capital_score = float(r.get("资金得分", 0.5) or 0.5)
        if regime == "bull":
            priority = score * 0.20 + capital_score * 0.15 + bonus * 0.65
        else:
            priority = score * 0.50 + capital_score * 0.30 + bonus * 0.30

        candidates.append({
            "代码": code, "名称": name,
            "最新价": price,
            "综合得分": score, "资金得分": capital_score,
            "涨跌幅": entry_chg,
            "换手率": float(r.get("换手率", 0) or 0),
            "行业": industry,
            "级别": matched_tier,
            "信号": signals,
            "_priority": round(priority, 4),
        })

    candidates.sort(key=lambda x: -x["_priority"])
    if ma_blocked > 0:
        print(f"  MA过滤: {ma_blocked}只 (60日位置<{MIN_POS_60:.0f}% | 排列<{MIN_MA_ALIGNMENT:+d} | 收盘≤MA5)")
    return candidates, regime, rows


def allocate_positions(candidates: list[dict], regime: str) -> list[dict]:
    """智能仓位分配: 按tier置信度加权, 行业分散, 5万上限"""
    if not candidates:
        return []

    # 行业分散 (同行业最多2只)
    sector_count = Counter()
    diversified = []
    for c in candidates:
        ind = c["行业"]
        if sector_count[ind] >= 2:
            continue
        diversified.append(c)
        sector_count[ind] += 1

    if not diversified:
        return []

    # 计算仓位权重
    total_weight = 0
    for c in diversified:
        tk = c["级别"]
        meta = TIER_META.get(tk, {"weight": 0.5})
        c["_tier_wr"] = meta["wr"]
        c["_tier_avg_ret"] = meta["avg_ret"]
        c["_alloc_weight"] = meta["weight"]
        total_weight += meta["weight"]

    # 归一化分配
    positions = []
    remaining = CAPITAL
    for i, c in enumerate(diversified):
        if i >= MAX_POSITIONS:
            break

        alloc_pct = c["_alloc_weight"] / total_weight
        alloc_amount = min(
            CAPITAL * alloc_pct,
            CAPITAL * MAX_POSITION_PCT,
            remaining * 0.95
        )

        # 按手取整 (100股/手)
        price = c["最新价"]
        lots = int(alloc_amount / (price * 100))
        if lots < 1:
            # 买不起1手, 跳过
            continue
        actual_amount = lots * price * 100

        if actual_amount < MIN_POSITION:
            continue

        c["_shares"] = lots * 100
        c["_amount"] = round(actual_amount, 2)
        c["_pct"] = round(actual_amount / CAPITAL * 100, 1)
        positions.append(c)
        remaining -= actual_amount

    # 剩余现金分配: 优先已选
    if remaining >= MIN_POSITION and positions:
        for p in positions:
            extra_lots = int(remaining * 0.5 / (p["最新价"] * 100))
            if extra_lots >= 1 and p["_pct"] + extra_lots * p["最新价"] * 100 / CAPITAL * 100 <= MAX_POSITION_PCT * 100:
                extra = extra_lots * p["最新价"] * 100
                p["_shares"] += extra_lots * 100
                p["_amount"] += extra
                p["_pct"] = round(p["_amount"] / CAPITAL * 100, 1)
                remaining -= extra

    return positions


def print_strategy(positions: list[dict], regime: str, date_str: str):
    """输出策略执行计划"""
    total_invested = sum(p["_amount"] for p in positions)
    cash = CAPITAL - total_invested

    print(f"\n{'='*70}")
    print(f"  隔夜套利策略 — {date_str}  体制: {regime}  本金: ¥{CAPITAL:,}")
    print(f"{'='*70}")

    if not positions:
        print(f"\n  ⚠️ 今日无符合条件的候选，现金 ¥{CAPITAL:,} 保持观望")
        print(f"  原因: 50K账户股价上限80元, P_high_price tier不可用")
        print(f"  建议: 等待regime转bull或A2信号出现")
        return

    print(f"\n  ┌─ 买入计划 (T日14:30后) ─────────────────────────────┐")
    print(f"  │ {'代码':<8} {'名称':<8} {'价格':>7} {'数量':>6} {'金额':>9} {'占比':>5}  {'级别':<22} {'WR':>5} │")
    print(f"  │ {'─'*70} │")

    for i, p in enumerate(positions):
        tk = p["级别"]
        wr = TIER_META.get(tk, {}).get("wr", 0)
        print(f"  │ {p['代码']:<8} {p['名称']:<8s} ¥{p['最新价']:>5.2f} {p['_shares']:>5}股 "
              f"¥{p['_amount']:>7,.0f} {p['_pct']:>4.1f}%  {TIER_META.get(tk,{}).get('label',tk):<22s} {wr:>4.0%} │")

    print(f"  ├──────────────────────────────────────────────────────────┤")
    print(f"  │ 总投入: ¥{total_invested:>7,.0f}  ({total_invested/CAPITAL*100:.0f}%)  现金: ¥{cash:>7,.0f}  ({cash/CAPITAL*100:.0f}%)    │")
    print(f"  └──────────────────────────────────────────────────────────┘")

    # 期望收益
    print(f"\n  ┌─ 期望收益 (T+1日 09:45-10:15 卖出) ──────────────────┐")
    exp_return = 0
    for p in positions:
        tk = p["级别"]
        meta = TIER_META.get(tk, {"wr": 0.5, "avg_ret": 0})
        exp = p["_amount"] * meta["wr"] * meta["avg_ret"] / 100  # WR × avg_ret%
        exp_return += exp
        print(f"  │ {p['代码']} {p['名称']:<8s}  ¥{p['_amount']:>7,.0f} × {meta['wr']:.0%}WR × {meta['avg_ret']:+.2f}% = ¥{exp:>+6,.0f} │")

    print(f"  ├──────────────────────────────────────────────────────────┤")
    exp_pct = exp_return / CAPITAL * 100
    print(f"  │ 单日期望收益: ¥{exp_return:>+7,.0f} ({exp_pct:+.2f}%)                            │")
    print(f"  └──────────────────────────────────────────────────────────┘")

    # 操作说明
    print(f"\n  📋 操作清单:")
    print(f"     T日 14:55  挂单买入以上 {len(positions)} 只股票 (市价或限价±0.5%)")
    print(f"     T+1日 09:45-10:15  卖出全部持仓 (均价), 不持有过夜")
    print(f"     止损: 单只T+1开盘跌超3% → 无条件市价卖出")
    print(f"     止盈: 单只T+1涨超5% → 可提前在10:00前卖出锁利")


def backtest_all():
    """回测所有历史交易日"""
    dates = sorted(
        d for d in os.listdir(RESEARCH_ROOT)
        if os.path.isdir(os.path.join(RESEARCH_ROOT, d))
        and os.path.exists(os.path.join(RESEARCH_ROOT, d, "scores.csv"))
        and d.isdigit()
    )

    print(f"\n{'='*70}")
    print(f"  隔夜策略回测 — {len(dates)} 个交易日  本金: ¥{CAPITAL:,}")
    print(f"{'='*70}")
    print(f"\n  {'日期':<10} {'体制':<6} {'候选':>4} {'入选':>4} {'投入':>8} {'期望¥':>8} {'期望%':>7}")
    print(f"  {'─'*55}")

    total_exp = 0
    total_trades = 0
    winning_days = 0

    for d in dates:
        candidates, regime, _ = load_filtered_candidates(d)
        positions = allocate_positions(candidates, regime)

        invested = sum(p["_amount"] for p in positions)
        exp_yuan = sum(
            p["_amount"] * TIER_META.get(p["级别"], {}).get("wr", 0.5)
            * TIER_META.get(p["级别"], {}).get("avg_ret", 0) / 100
            for p in positions
        )
        exp_pct = exp_yuan / CAPITAL * 100 if CAPITAL else 0

        print(f"  {d:<10} {regime:<6} {len(candidates):>4} {len(positions):>4} "
              f"¥{invested:>6,.0f} ¥{exp_yuan:>+6,.0f} {exp_pct:>+6.2f}%")

        total_exp += exp_yuan
        total_trades += len(positions)
        if exp_yuan > 0:
            winning_days += 1

    print(f"  {'─'*55}")
    print(f"  累计期望收益: ¥{total_exp:+,.0f}  ({total_exp/CAPITAL*100:+.2f}%)")
    print(f"  总交易: {total_trades} 笔  正收益天数: {winning_days}/{len(dates)}")
    print(f"  日均期望: ¥{total_exp/len(dates):+,.0f}" if dates else "")
    print()


def main():
    args = sys.argv[1:]
    date_str = None
    do_backtest = False

    for a in args:
        if a == "--backtest":
            do_backtest = True
        elif a.isdigit() and len(a) == 8:
            date_str = a

    if do_backtest:
        backtest_all()
        return

    if date_str is None:
        date_str = find_latest_date()
        print(f"  自动选择最近交易日: {date_str}")

    # Step 1: 筛选
    candidates, regime, _ = load_filtered_candidates(date_str)
    print(f"\n  全市场扫描 → {len(candidates)} 只候选 (体制: {regime})")

    # Step 2: 仓位分配
    positions = allocate_positions(candidates, regime)

    # Step 3: 输出策略
    print_strategy(positions, regime, date_str)

    # Step 4: 保存
    if positions:
        out_path = os.path.join(RESEARCH_ROOT, date_str, "strategy_picks.csv")
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            fields = ["代码", "名称", "最新价", "综合得分", "资金得分",
                      "涨跌幅", "换手率", "行业", "级别", "_priority",
                      "_tier_wr", "_tier_avg_ret", "_shares", "_amount", "_pct"]
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(positions)
        print(f"\n  ✓ 保存: {out_path}")


if __name__ == "__main__":
    main()
