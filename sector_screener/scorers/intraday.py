"""维度二十二: 日内轨迹动量 — 排名上升 + 资金加速流入 = 日内确认信号"""
from sector_screener.config import to_float


def score_intraday_trend(stock, context):
    """基于日内多快照时间序列的轨迹动量评分 (0~1)

    核心逻辑:
      - 板块内排名日内持续上升 → 资金在日内集中涌入该股
      - 主力净流入日内加速 → 买盘在加强而非衰竭
      - 覆盖快照数越多 → 信号越可靠

    无日内数据时返回 0.50（中性）。
    """
    snapshots = stock.get("_intraday_snapshots", 0)
    if snapshots < 2:
        return 0.50

    rank_trend = stock.get("_intraday_rank_trend", 0.0)    # +1=排名上升(好)
    flow_trend = stock.get("_intraday_flow_trend", 0.0)    # +1=资金加速(好)
    flow_delta = stock.get("_intraday_flow_delta_pct", 0.0)  # 资金变化%
    rank_first = stock.get("_intraday_rank_first", 99)
    rank_last = stock.get("_intraday_rank_last", 99)

    # ── 排名改善 ──
    # 排名上升(数字变小) = 资金在日内向该股集中
    rank_improve = rank_first - rank_last  # 正值=改善
    if rank_improve > 20:
        s_rank = 0.90
    elif rank_improve > 10:
        s_rank = 0.75
    elif rank_improve > 5:
        s_rank = 0.65
    elif rank_improve > 0:
        s_rank = 0.55
    elif rank_improve == 0:
        s_rank = 0.50
    elif rank_improve > -5:
        s_rank = 0.40   # 轻微下滑
    elif rank_improve > -10:
        s_rank = 0.25   # 明显下滑
    else:
        s_rank = 0.10   # 大幅下滑

    # ── 资金加速 ──
    # 日内资金加速流入 = 买盘在加强
    if flow_trend > 0.5:
        s_flow = 0.85
    elif flow_trend > 0.2:
        s_flow = 0.70
    elif flow_trend > 0.0:
        s_flow = 0.55
    elif flow_trend > -0.2:
        s_flow = 0.45
    elif flow_trend > -0.5:
        s_flow = 0.30
    else:
        s_flow = 0.15

    # ── 资金增幅 ──
    # 日内资金翻倍以上 = 强动量
    abs_delta = abs(flow_delta)
    if flow_delta > 1.0:
        s_delta = 0.80
    elif flow_delta > 0.5:
        s_delta = 0.65
    elif flow_delta > 0.2:
        s_delta = 0.55
    elif flow_delta > 0:
        s_delta = 0.50
    elif flow_delta > -0.2:
        s_delta = 0.45
    elif flow_delta > -0.5:
        s_delta = 0.35
    else:
        s_delta = 0.20

    # ── 快照覆盖度信心 ──
    # 7个快照(全天覆盖) vs 2个快照(片段)
    confidence = min(1.0, snapshots / 7.0)

    # ── 综合 ──
    raw = s_rank * 0.40 + s_flow * 0.35 + s_delta * 0.25
    # 信心调整: 高信心时向 raw 靠拢，低信心时向 0.5 回归
    score = 0.50 + (raw - 0.50) * confidence
    return round(max(0.0, min(1.0, score)), 4)
