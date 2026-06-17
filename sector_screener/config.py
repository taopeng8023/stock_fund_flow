"""
板块增强选股 — 配置中心
所有阈值、权重、常量集中管理
"""
from collections import defaultdict

# ═══════════════════════════════════════
# 风控阈值
# ═══════════════════════════════════════
MIN_PRICE       = 4.0
MAX_PRICE       = 200.0
LIMIT_UP_PCT    = 9.8
CANDIDATE_MAX_CHG = 9.5
MIN_MAIN_FLOW   = 3000_0000
MIN_MAIN_RATIO  = 1.0
MIN_TURNOVER    = 2.0
MAX_TURNOVER    = 25.0
MIN_VOL_RATIO   = 1.0
MIN_MCAP_YI     = 30
MAX_MCAP_YI     = 2000

MAIN_BOARD_PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")

# ═══════════════════════════════════════
# 14 维度权重 — 三市场景自适应
# ═══════════════════════════════════════
WEIGHTS_BASE = {
    "start_signal":  0.19,
    "capital":       0.16,
    "trend":         0.10,
    "sector":        0.07,
    "position":      0.07,
    "analyst":       0.04,
    "multiday":      0.04,
    "technical":     0.04,
    "dragon_tiger":  0.03,
    "north_flow":    0.02,
    "ratio_rank":    0.01,
    "intra_sector":  0.03,
    "margin_net":    0.03,
    "flow_accel":    0.03,
    "block_trade":   0.02,   # 🆕 大宗交易溢价信号
    "org_research":  0.02,   # 🆕 机构调研热度
    "earnings":      0.02,   # 🆕 业绩预告类型
    "lockup_expiry": 0.02,   # 🆕 限售解禁风险
    "margin_short":  0.02,   # 🆕 融券压力(已有字段f170/f172)
    "margin_long":   0.02,   # 🆕 融资力度(已有字段f174/f175/f169)
    "volume_quality":0.02,   # 🆕 成交额质量(已有字段f5/f6)
}

WEIGHTS_BULL = {**WEIGHTS_BASE,
    "trend": 0.12, "dragon_tiger": 0.05, "analyst": 0.03, "position": 0.06,
    "start_signal": 0.17, "capital": 0.17, "intra_sector": 0.05, "margin_net": 0.04,
    "block_trade": 0.03, "org_research": 0.03, "earnings": 0.02, "lockup_expiry": 0.01,
    "margin_long": 0.03, "volume_quality": 0.02,
}

WEIGHTS_BEAR = {**WEIGHTS_BASE,
    "analyst": 0.07, "north_flow": 0.04, "position": 0.10, "start_signal": 0.16,
    "trend": 0.09, "dragon_tiger": 0.02, "capital": 0.14, "intra_sector": 0.04,
    "block_trade": 0.01, "org_research": 0.01, "earnings": 0.03, "lockup_expiry": 0.03,
    "margin_short": 0.04, "volume_quality": 0.01,
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
