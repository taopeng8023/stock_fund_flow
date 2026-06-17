"""大宗交易数据加载 → {code: {avg_premium, total_amt, buyer_count}}"""
from collections import defaultdict
from data_collector.fetchers.base import load_json
from sector_screener.config import to_float


def load_block_trade(date_str):
    """汇总当日大宗交易 → 每只股票的溢价情况和买方特征"""
    rows = load_json(date_str, "block_trade")
    if not rows:
        return {}

    result = {}
    stock_groups = defaultdict(list)
    for r in rows:
        code = r.get("SECURITY_CODE", "")
        if code:
            stock_groups[code].append(r)

    for code, trades in stock_groups.items():
        premiums = [to_float(t.get("PREMIUM_RATIO")) for t in trades]
        amts = [to_float(t.get("DEAL_AMT")) for t in trades]
        avg_premium = sum(premiums) / len(premiums) if premiums else 0
        total_amt = sum(amts)

        # 买方特征: 机构/营业部
        buyers = [t.get("BUYER_NAME", "") for t in trades]
        inst_buy = sum(1 for b in buyers if "机构" in str(b) or "专用" in str(b))

        result[code] = {
            "count": len(trades),
            "avg_premium": round(avg_premium, 2),
            "total_amt": total_amt,
            "inst_buy_count": inst_buy,
            "has_premium_buy": any(p > 0 for p in premiums),
            "has_deep_discount": any(p < -8 for p in premiums),
        }

    print(f"  大宗交易: {len(result)} 只股票")
    return result
