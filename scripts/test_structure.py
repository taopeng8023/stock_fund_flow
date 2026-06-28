"""
结构评分回测验证 — IC 分析 + 分层收益

用法:
    # 单日验证: 结构评分 vs 次日收益
    python scripts/test_structure.py --date 20260624 --eval 20260626

    # 分层回测
    python scripts/test_structure.py --date 20260624 --mode decile
"""
import argparse
import csv
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sector_screener.structurer.price_loader import load_daily_bars_from_local
from sector_screener.structurer.scorer import structure_score


def load_candidate_codes(date_str: str, max_stocks: int = 100) -> list[dict]:
    """从 scores.csv 或 fund_flow CSV 中加载候选股票列表"""
    research_root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data"
    )

    # 优先用 scores.csv
    scores_path = os.path.join(research_root, date_str, "scores.csv")
    if os.path.exists(scores_path):
        codes = []
        with open(scores_path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                codes.append({"代码": r["代码"], "名称": r.get("名称", "")})
                if len(codes) >= max_stocks:
                    break
        return codes

    # 备选: 从 intraday fund_flow CSV 获取
    intraday_dir = os.path.join(research_root, date_str, "intraday")
    if os.path.isdir(intraday_dir):
        files = sorted([
            f for f in os.listdir(intraday_dir)
            if f.startswith("fund_flow_") and f.endswith(".csv")
        ], reverse=True)
        if files:
            path = os.path.join(intraday_dir, files[0])
            codes = []
            with open(path, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    code = r.get("代码", "")
                    if code:
                        codes.append({"代码": code, "名称": r.get("名称", "")})
                        if len(codes) >= max_stocks:
                            break
            return codes

    return []


def load_next_day_returns(codes: list[str], eval_date: str) -> dict[str, float]:
    """加载次日涨跌幅（从 eval_date 的 fund_flow CSV）"""
    research_root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data"
    )
    intraday_dir = os.path.join(research_root, eval_date, "intraday")
    if not os.path.isdir(intraday_dir):
        return {}

    files = sorted([
        f for f in os.listdir(intraday_dir)
        if f.startswith("fund_flow_") and f.endswith(".csv")
    ])
    if not files:
        return {}

    path = os.path.join(intraday_dir, files[-1])  # 最晚快照 ≈ 收盘
    returns = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = r.get("代码", "")
            pct = r.get("涨跌幅", "")
            if code and pct:
                try:
                    returns[code] = float(pct)
                except ValueError:
                    continue
    return returns


def compute_ic(scores: list[float], returns: list[float]) -> dict:
    """计算 Information Coefficient (Pearson + Spearman)"""
    n = len(scores)
    if n < 5:
        return {"pearson": 0, "spearman": 0, "n": n}

    # Pearson
    mean_s = sum(scores) / n
    mean_r = sum(returns) / n
    cov = sum((scores[i] - mean_s) * (returns[i] - mean_r) for i in range(n))
    std_s = (sum((s - mean_s) ** 2 for s in scores) / n) ** 0.5
    std_r = (sum((r - mean_r) ** 2 for r in returns) / n) ** 0.5
    pearson = cov / (std_s * std_r * n) if std_s > 0 and std_r > 0 else 0

    # Spearman
    def rank(arr):
        sorted_idx = sorted(range(n), key=lambda i: arr[i])
        ranks = [0] * n
        for i, idx in enumerate(sorted_idx):
            ranks[idx] = i + 1
        return ranks

    rank_s = rank(scores)
    rank_r = rank(returns)
    d2 = sum((rank_s[i] - rank_r[i]) ** 2 for i in range(n))
    spearman = 1 - (6 * d2) / (n * (n * n - 1))

    return {"pearson": round(pearson, 4), "spearman": round(spearman, 4), "n": n}


def decile_analysis(scores: list[float], returns: list[float]) -> dict:
    """分层分析: Top10% vs Bottom10%"""
    n = len(scores)
    if n < 10:
        return {"top_avg": 0, "bottom_avg": 0, "spread": 0, "n": n}

    # 按评分排序
    paired = sorted(zip(scores, returns), key=lambda x: -x[0])
    k = max(1, n // 10)

    top = paired[:k]
    bottom = paired[-k:]

    top_avg = sum(r for _, r in top) / k
    bottom_avg = sum(r for _, r in bottom) / k

    return {
        "top_avg": round(top_avg, 4),
        "bottom_avg": round(bottom_avg, 4),
        "spread": round(top_avg - bottom_avg, 4),
        "top_n": k,
        "n": n,
    }


def main():
    parser = argparse.ArgumentParser(description="结构评分回测验证")
    parser.add_argument("--date", required=True, help="评分日期 YYYYMMDD")
    parser.add_argument("--eval", default=None, help="评估日期 YYYYMMDD (默认 date+1)")
    parser.add_argument("--mode", default="ic", choices=["ic", "decile", "full"])
    parser.add_argument("--top", type=int, default=50, help="分析股票数")
    args = parser.parse_args()

    # eval 日期默认次日
    if not args.eval:
        from datetime import datetime, timedelta
        d = datetime.strptime(args.date, "%Y%m%d")
        args.eval = (d + timedelta(days=1)).strftime("%Y%m%d")

    print(f"=== 结构评分回测: {args.date} → {args.eval} ===\n")

    # 1. 加载候选股
    candidates = load_candidate_codes(args.date, max_stocks=args.top)
    if not candidates:
        print(f"ERROR: {args.date} 无候选股数据")
        sys.exit(1)
    print(f"候选股: {len(candidates)} 只")

    # 2. 加载次日收益
    all_codes = [c["代码"] for c in candidates]
    next_returns = load_next_day_returns(all_codes, args.eval)
    print(f"次日收益数据: {len(next_returns)} 只")

    # 3. 逐股计算结构评分
    print(f"\n计算结构评分...")
    results = []
    for i, cand in enumerate(candidates):
        code = cand["代码"]
        if code not in next_returns:
            continue
        bars = load_daily_bars_from_local(code, days=250)
        if not bars or len(bars) < 60:
            continue
        struc = structure_score(code, bars)
        results.append({
            "code": code,
            "name": cand.get("名称", ""),
            "score": struc["score"],
            "trend": struc["trend_type"],
            "pos": struc["position_score"],
            "div": struc["divergence_score"],
            "ret": next_returns[code],
        })
        if (i + 1) % 10 == 0:
            print(f"  进度: {i+1}/{len(candidates)}")

    print(f"有效结果: {len(results)}")

    if len(results) < 10:
        print("数据不足, 无法分析")
        return

    # 4. IC 分析
    scores = [r["score"] for r in results]
    rets = [r["ret"] for r in results]
    ic = compute_ic(scores, rets)
    print(f"\n{'='*60}")
    print(f"  IC 分析")
    print(f"{'='*60}")
    print(f"  Pearson  IC: {ic['pearson']:+.4f}")
    print(f"  Spearman IC: {ic['spearman']:+.4f}")
    print(f"  N: {ic['n']}")

    # 5. 分层分析
    decile = decile_analysis(scores, rets)
    print(f"\n{'='*60}")
    print(f"  分层分析 (Top{decile['top_n']} vs Bottom{decile['top_n']})")
    print(f"{'='*60}")
    print(f"  Top 平均收益: {decile['top_avg']:+.2f}%")
    print(f"  Bottom 平均收益: {decile['bottom_avg']:+.2f}%")
    print(f"  Spread: {decile['spread']:+.2f}%")

    # 6. 趋势分层
    print(f"\n{'='*60}")
    print(f"  趋势分类统计")
    print(f"{'='*60}")
    from collections import Counter
    trend_counts = Counter(r["trend"] for r in results)
    for t in ["uptrend", "range", "downtrend"]:
        subset = [r for r in results if r["trend"] == t]
        if subset:
            avg_ret = sum(r["ret"] for r in subset) / len(subset)
            avg_score = sum(r["score"] for r in subset) / len(subset)
            print(f"  {t:10s}: {len(subset):3d}只  均分={avg_score:.3f}  均收益={avg_ret:+.2f}%")

    # 7. 详细 Top10
    print(f"\n{'='*60}")
    print(f"  Top 10 结构评分")
    print(f"{'='*60}")
    top10 = sorted(results, key=lambda x: -x["score"])[:10]
    for r in top10:
        print(f"  {r['code']} {r['name']:6s} score={r['score']:.3f} {r['trend']:9s} pos={r['pos']:.3f} ret={r['ret']:+.2f}%")


if __name__ == "__main__":
    main()
