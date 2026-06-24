"""
因子 #26: 行业分散度 — 热门行业拥挤惩罚

回测依据:
  20260617: Top20 半导体 7/20，去掉后其余 13 只均收益 -0.14% vs -0.48%
  20260618: Top20 半导体 7/20，去掉后其余 13 只均收益 +2.79% vs +0.41%

模型天然偏向选中热门行业的股票，但过度集中在单行业增加踩雷风险。
本因子按候选池中行业占比给予微调：占比越低越加分，占比越高轻微扣分。

注意：这是评分层的微调（±0.02），主力行业约束在 buy_engine 层硬控。
"""
from collections import Counter


def score_sector_diversity(stock, context):
    """返回 0~1

    基于候选池中该行业的占比：
      ≤10% → 1.0（稀缺，加分）
      10-20% → 0.8（正常）
      20-30% → 0.5（偏多，轻微惩罚）
      >30% → 0.3（拥挤，明显惩罚）

    不区分行业好坏——只惩罚拥挤度。
    """
    candidates = context.get("_candidates", [])
    sector_code = stock.get("_sector_code", "")

    if not candidates or not sector_code:
        return 0.5

    # 统计候选池中各行业数量
    sector_count = Counter(
        s.get("_sector_code", "") for s in candidates if s.get("_sector_code")
    )
    total_with_sector = sum(sector_count.values())
    if total_with_sector == 0:
        return 0.5

    my_count = sector_count.get(sector_code, 0)
    ratio = my_count / total_with_sector

    if ratio <= 0.10:
        return 1.0
    if ratio <= 0.20:
        return 0.8
    if ratio <= 0.30:
        return 0.5
    return 0.3
