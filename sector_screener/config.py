"""
板块增强选股 — 配置中心
所有阈值、权重、常量集中管理

2026-06-26 优化（5 Agent 回测验证驱动）:
  - 移除 4 个零区分度因子: analyst, technical, sector_diversity, limitup_proximity
  - 降权冗余簇: capital 从 0.19→0.12, intra_sector 从 0.04→0.02
  - 提权独立Alpha: sector 从 0.05→0.12 (|r|=0.29, 唯一独立因子)
  - 提权最稳日内: intraday_trend 从 0.02→0.06 (排名轨迹 std=0.010)
  - 保留资金流核心: multiday 0.08 (2.11x正向最强), capital 0.12
  - 移除趋势过滤陷阱: trend 权重不增 (叠加趋势摧毁收益)
"""
from collections import defaultdict

# ═══════════════════════════════════════
# 风控阈值
# ═══════════════════════════════════════
MIN_PRICE       = 4.0
MAX_PRICE       = 100.0   # 优化: 200→100 (P_high_price胜率仅31.7%)
LIMIT_UP_PCT    = 9.8
CANDIDATE_MAX_CHG = 9.5
MIN_MAIN_FLOW   = 3000_0000
MIN_MAIN_RATIO  = 1.0
MIN_TURNOVER    = 2.0
MAX_TURNOVER    = 25.0
MIN_VOL_RATIO   = 1.0
MIN_MCAP_YI     = 30
MAX_MCAP_YI     = 2000

MAIN_BOARD_PREFIXES = ("000", "001", "002", "003", "300", "600", "601", "603", "605", "688")

# ═══════════════════════════════════════
# 25 维度权重 — 回测优化版 (2026-06-26)
# ═══════════════════════════════════════
WEIGHTS_BASE = {
    # ── 核心Alpha (资金流维度) ──
    "capital":       0.12,   # 优化: 0.19→0.12 降冗余簇权重
    "multiday":      0.08,   # 优化: 0.06→0.08 多日累计2.11x正向最强
    "start_signal":  0.10,   # 优化: 0.13→0.10 降冗余簇

    # ── 独立Alpha (板块维度, |r|=0.29) ──
    "sector":        0.12,   # 优化: 0.05→0.12 唯一独立信号, standalone WR=35.9%

    # ── 日内维度 (排名轨迹 std=0.010) ──
    "intraday_trend":0.06,   # 优化: 0.02→0.06 最稳定日内因子

    # ── 辅助维度 ──
    "trend":         0.08,   # 趋势确认 (不增权-陷阱已验证)
    "position":      0.06,   # 位置健康
    "price_momentum":0.04,   # 优化: 0.03→0.04 多日价格回报
    "sector_price":  0.04,   # 优化: 0.03→0.04 板块价格共振

    # ── 事件驱动 ──
    "dragon_tiger":  0.04,   # 优化: 0.03→0.04 龙虎榜
    "block_trade":   0.03,   # 大宗交易
    "org_research":  0.03,   # 机构调研
    "earnings":      0.03,   # 业绩预告
    "lockup_expiry": 0.02,   # 限售解禁惩罚

    # ── 融资融券 ──
    "margin_net":    0.03,   # 融资净买入
    "margin_short":  0.02,   # 融券压力
    "margin_long":   0.02,   # 融资力度

    # ── 微观结构 ──
    "flow_accel":    0.02,   # 优化: 0.01→0.02 资金加速度
    "volume_quality":0.02,   # 成交额质量
    "intra_sector":  0.02,   # 优化: 0.04→0.02 属冗余簇
    "north_flow":    0.02,   # 北向资金

    # ── 已移除 (零区分度 cardinality=1) ──
    # "analyst": 0 (所有股票=0.5)
    # "technical": 0 (所有股票=0.5)
    # "sector_diversity": 0 (所有股票=1.0)
    # "limitup_proximity": 0 (cardinality极低)
}

WEIGHTS_BULL = {**WEIGHTS_BASE,
    "trend": 0.10, "dragon_tiger": 0.06, "position": 0.05,
    "start_signal": 0.12, "capital": 0.10, "margin_net": 0.04,
    "block_trade": 0.04, "org_research": 0.04, "earnings": 0.03,
    "margin_long": 0.03, "volume_quality": 0.03, "intraday_trend": 0.07,
    "price_momentum": 0.05, "sector_price": 0.05,
    "sector": 0.14,  # 牛市加板块权重
}

WEIGHTS_BEAR = {**WEIGHTS_BASE,
    "position": 0.10, "north_flow": 0.04, "start_signal": 0.12,
    "trend": 0.06, "dragon_tiger": 0.02, "capital": 0.10,
    "block_trade": 0.01, "org_research": 0.01, "earnings": 0.04,
    "margin_short": 0.05, "margin_net": 0.02,
    "price_momentum": 0.02, "sector_price": 0.02,
    "sector": 0.12,  # 熊市板块同样重要
    "intraday_trend": 0.05,
}

# ═══════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════

def to_float(val):
    if val is None or val == "-" or val == "":
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0

def pct_rank(values, target):
    """percentile rank: 0~1"""
    if not values or max(values) == min(values):
        return 0.5
    return sum(1 for v in values if v <= target) / len(values)

def range_score(value, ideal_min, ideal_max, floor, ceil):
    """区间评分：理想区间内=1.0"""
    if ideal_min <= value <= ideal_max:
        return 1.0
    if value < ideal_min:
        return max(0.0, (value - floor) / (ideal_min - floor)) if ideal_min > floor else 0.0
    return max(0.0, (ceil - value) / (ceil - ideal_max)) if ceil > ideal_max else 0.0

def fmt_yi(v):
    if abs(v) >= 1e8:
        return f"{v/1e8:+.2f}亿"
    return f"{v/1e4:+.0f}万"

def is_main_board(code):
    return isinstance(code, str) and code.startswith(MAIN_BOARD_PREFIXES)
