"""全市场 fund_flow 交叉引用 — 行业内排名(f100) + 融资净买入(f168)"""
from collections import defaultdict
from data_collector.fetchers.base import load_json
from sector_screener.config import to_float


def load_fund_flow_cross_ref(date_str):
    """返回 (intra_sector_rank, margin_net_map)"""
    rows = load_json(date_str, "fund_flow")
    if not rows:
        print(f"  全市场 fund_flow 不可用，行业内排名/融资因子降级为中性")
        return {}, {}

    # ── 行业内相对强度：按 f100 分组 ──
    industry_groups = defaultdict(list)
    for r in rows:
        ind = r.get("f100", "") or ""
        f62 = to_float(r.get("f62"))
        if isinstance(f62, (int, float)):
            industry_groups[ind].append(f62)

    intra_sector_rank = {}
    for r in rows:
        code = r.get("f12", "")
        ind = r.get("f100", "") or ""
        f62 = to_float(r.get("f62"))
        group_vals = industry_groups.get(ind, [f62])
        if len(group_vals) > 1 and max(group_vals) > min(group_vals):
            intra_sector_rank[code] = sum(1 for v in group_vals if v <= f62) / len(group_vals)
        else:
            intra_sector_rank[code] = 0.5

    # ── 融资净买入：全市场 f168 percentile ──
    f168_pairs = []
    for r in rows:
        f168 = r.get("f168")
        if isinstance(f168, (int, float)):
            f168_pairs.append((r.get("f12", ""), f168))
        elif isinstance(f168, str):
            try:
                f168_pairs.append((r.get("f12", ""), float(f168)))
            except (ValueError, TypeError):
                pass
    f168_vals = [v for _, v in f168_pairs]
    margin_net_map = {}
    if f168_vals and max(f168_vals) > min(f168_vals):
        for code, f168 in f168_pairs:
            pct = sum(1 for v in f168_vals if v <= f168) / len(f168_vals)
            margin_net_map[code] = {"f168": f168, "percentile": round(pct, 4)}
    else:
        for code, f168 in f168_pairs:
            margin_net_map[code] = {"f168": f168, "percentile": 0.5}

    print(f"  全市场交叉引用: {len(intra_sector_rank)} 只行业内排名, "
          f"{len(margin_net_map)} 只融资数据")
    return intra_sector_rank, margin_net_map
