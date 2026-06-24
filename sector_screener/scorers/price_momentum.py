"""
因子 #23: 多日价格动量 — 5/10/20日价格回报百分位

区别于 #7 多日累计（资金流维度），本因子仅看价格本身：
  - 5日回报：捕捉短期趋势强度
  - 10日回报：中期动量确认
  - 20日回报：排除长期阴跌股

与资金流因子互补：
  资金流入 + 价格上涨 = 真强势（双因子共振高分）
  资金流入 + 价格不涨 = 吸筹/出货不确定（本因子低分降权）
  资金流出 + 价格上涨 = 量价背离（本因子无法救）
"""
from sector_screener.config import to_float, pct_rank


def _compute_returns(closes):
    """从收盘价序列计算 5/10/20 日回报。closes[0]=最新价, closes[1]=昨收..."""
    ret = {"ret_5d": 0.0, "ret_10d": 0.0, "ret_20d": 0.0}
    n = len(closes)

    for label, days in [("ret_5d", 4), ("ret_10d", 9), ("ret_20d", 19)]:
        if n > days and closes[days] and closes[days] > 0:
            ret[label] = (closes[0] - closes[days]) / closes[days]
    return ret


def score_price_momentum(stock, context):
    """返回 0~1

    权重分配:
      - 5日回报 40%（短期趋势，波动大但有预测力）
      - 10日回报 35%（中期动量，稳定性更好）
      - 20日回报 25%（长期趋势，排除阴跌股）

    与 #7 多日累计的区分:
      #7 看的是资金（主力真金白银）
      #23 看的是价格（市场投票结果）
      两者正交——资金进+价格涨是确认信号，资金进+价格不涨是警示信号
    """
    code = stock.get("f12", "")
    closes = context.get("price_history", {}).get(code, [])

    if not closes or closes[0] <= 0:
        return 0.5

    ret = _compute_returns(closes)

    # 从 context 获取全候选池的预计算 percentile 数组
    s5 = pct_rank(context.get("_ret5d_vals", [ret["ret_5d"]]), ret["ret_5d"])
    s10 = pct_rank(context.get("_ret10d_vals", [ret["ret_10d"]]), ret["ret_10d"])
    s20 = pct_rank(context.get("_ret20d_vals", [ret["ret_20d"]]), ret["ret_20d"])

    return round(s5 * 0.40 + s10 * 0.35 + s20 * 0.25, 4)
