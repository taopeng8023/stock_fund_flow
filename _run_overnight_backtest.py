import json, sys, os
sys.path.insert(0, '/Users/taopeng/PycharmProjects/stock_fund_flow')
os.chdir('/Users/taopeng/PycharmProjects/stock_fund_flow')
from daily_pipeline.backtest_overnight import backtest_single
from collections import defaultdict

pairs = [
    ("20260617", "20260618"),
    ("20260618", "20260622"),
]

results = {}
all_trades = []
for pick_date, eval_date in pairs:
    r = backtest_single(pick_date, eval_date, top_n=30)
    day_result = {
        "pick_date": pick_date,
        "eval_date": eval_date,
        "valid_n": r.get("valid_n", 0),
        "wr": r.get("wr"),
        "avg_ret": r.get("avg_ret"),
        "total_ret": r.get("total_ret"),
        "data_quality_warning": r.get("data_quality_warning"),
        "exit_snap_count": r.get("exit_snap_count", 0),
        "note": r.get("note", ""),
        "trades": []
    }
    for t in r.get("trades", []):
        if t.get("ret") is not None:
            day_result["trades"].append({
                "code": t["code"],
                "name": t["name"],
                "tier": t.get("tier", "?"),
                "entry": t["entry"],
                "exit": t["exit"],
                "ret": t["ret"],
                "win": t["win"],
                "snapshot_quality": t.get("snapshot_quality", "?"),
                "ex_dividend_possible": t.get("ex_dividend_possible"),
            })
    results[f"{pick_date}_{eval_date}"] = day_result
    for td in day_result["trades"]:
        all_trades.append(td)
    print(f"  {pick_date}->{eval_date}: {day_result['valid_n']}笔 WR={day_result['wr']}% avg={day_result['avg_ret']}% snaps={day_result['exit_snap_count']}")

n = len(all_trades)
if n:
    wins = sum(1 for t in all_trades if t["win"])
    wr = round(wins/n*100, 1)
    avg_ret = round(sum(t["ret"] for t in all_trades)/n, 2)
    tiers = defaultdict(lambda: {"n":0,"w":0,"r":[]})
    for t in all_trades:
        tk = t["tier"].split(":")[0] if ":" in t["tier"] else t["tier"]
        tiers[tk]["n"] += 1
        if t["win"]: tiers[tk]["w"] += 1
        tiers[tk]["r"].append(t["ret"])
    tier_summary = {}
    for tk, tv in tiers.items():
        tier_summary[tk] = {"n":tv["n"], "wr":round(tv["w"]/tv["n"]*100,1), "avg":round(sum(tv["r"])/tv["n"],2)}

    results["_summary"] = {"n":n, "wr":wr, "avg_ret":avg_ret, "tiers":tier_summary}
    print(f"\n  Summary: {n}笔 WR={wr}% avg={avg_ret}%")

output_path = "/Users/taopeng/PycharmProjects/stock_fund_flow/research_data/backtest/overnight_v2/agent_A_results.json"
os.makedirs(os.path.dirname(output_path), exist_ok=True)
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2, default=str)
print(f"\nSaved to {output_path}")
