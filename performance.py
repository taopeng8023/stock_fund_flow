"""
绩效追踪 — 记录选股 + 回填收益，供 pipeline 调用。
回溯分析请用 backtest.py。
"""
import json
import os
import sys
from datetime import datetime
from fetchers.base import DATA_ROOT, BJS_TZ, load_json

PERF_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "performance.json")


def get_today_str():
    return datetime.now(BJS_TZ).strftime("%Y%m%d")


def load_performance():
    if os.path.exists(PERF_FILE):
        with open(PERF_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"records": [], "summary": {}}


def save_performance(perf):
    with open(PERF_FILE, "w", encoding="utf-8") as f:
        json.dump(perf, f, ensure_ascii=False, indent=2)


def update(date_str=None):
    """用今日数据回填历史选股的次日收益"""
    if date_str is None:
        date_str = get_today_str()

    perf = load_performance()
    pending = [r for r in perf["records"]
               if r.get("next_ret") is None and r.get("pick_date") != date_str]

    if not pending:
        print("  无待回测记录")
        return perf

    updated = 0
    for record in pending:
        pick_date = record["pick_date"]
        pick_code = record["code"]
        today_rows = load_json(date_str, "fund_flow")
        if today_rows is None:
            continue
        found = next((r for r in today_rows if r.get("f12") == pick_code), None)
        if found:
            close = found.get("f2") or 0
            prev_close = found.get("f18") or 0
            if prev_close > 0:
                ret_1d = (close - prev_close) / prev_close * 100
            else:
                ret_1d = found.get("f3") or 0
            record["next_ret"] = round(ret_1d, 2)
            record["next_close"] = close
            record["eval_date"] = date_str
            updated += 1

    if updated:
        completed = [r for r in perf["records"] if r.get("next_ret") is not None]
        if completed:
            rets = [r["next_ret"] for r in completed]
            wins = sum(1 for r in rets if r > 0)
            perf["summary"] = {
                "total_picks": len(completed),
                "wins": wins,
                "losses": len(completed) - wins,
                "win_rate": round(wins / len(completed) * 100, 1),
                "avg_return": round(sum(rets) / len(rets), 2),
                "max_gain": round(max(rets), 2),
                "max_loss": round(min(rets), 2),
                "cum_return": round(sum(rets), 2),
                "last_updated": date_str,
            }
        save_performance(perf)
        print(f"  更新 {updated} 条记录, 累计 {perf['summary'].get('total_picks', 0)} 笔")

    return perf


def record_picks(picks, date_str=None):
    """记录当日选股到绩效文件"""
    if date_str is None:
        date_str = get_today_str()
    perf = load_performance()
    for row in picks:
        code = row.get("f12", "")
        exists = any(
            r.get("pick_date") == date_str and r.get("code") == code
            for r in perf["records"]
        )
        if not exists:
            perf["records"].append({
                "pick_date": date_str,
                "code": code,
                "name": row.get("f14", ""),
                "score": row.get("_score", 0),
                "next_ret": None,
                "next_close": None,
                "eval_date": None,
            })
    save_performance(perf)
    print(f"  已记录 {len(picks)} 只选股到绩效追踪")


def get_summary():
    """程序化接口：返回绩效摘要"""
    perf = load_performance()
    s = perf.get("summary", {})
    records = perf.get("records", [])
    completed = [r for r in records if r.get("next_ret") is not None]

    recent = [{"pick_date": r["pick_date"], "code": r["code"],
               "name": r.get("name", ""), "score": r.get("score", 0),
               "next_ret": r.get("next_ret")} for r in completed[-20:]]

    factor_analysis = {}
    if len(completed) >= 10:
        hi = sorted(completed, key=lambda r: r.get("score", 0), reverse=True)[:len(completed) // 2]
        lo = sorted(completed, key=lambda r: r.get("score", 0))[:len(completed) // 2]
        hi_avg = sum(r.get("next_ret", 0) for r in hi) / len(hi) if hi else 0
        lo_avg = sum(r.get("next_ret", 0) for r in lo) / len(lo) if lo else 0
        hi_win = sum(1 for r in hi if r.get("next_ret", 0) > 0) / len(hi) * 100 if hi else 0
        lo_win = sum(1 for r in lo if r.get("next_ret", 0) > 0) / len(lo) * 100 if lo else 0
        factor_analysis = {
            "high_score_avg": round(hi_avg, 2),
            "low_score_avg": round(lo_avg, 2),
            "high_score_win_rate": round(hi_win, 1),
            "low_score_win_rate": round(lo_win, 1),
            "factor_edge": round(hi_avg - lo_avg, 2),
        }

    return {
        "total_picks": s.get("total_picks", 0),
        "wins": s.get("wins", 0),
        "losses": s.get("losses", 0),
        "win_rate": s.get("win_rate", 0),
        "avg_return": s.get("avg_return", 0),
        "max_gain": s.get("max_gain", 0),
        "max_loss": s.get("max_loss", 0),
        "cum_return": round(s.get("cum_return", 0), 2),
        "last_updated": s.get("last_updated", ""),
        "recent_picks": recent,
        "factor_analysis": factor_analysis,
    }


def main():
    if "--update" in sys.argv:
        date_str = get_today_str()
        for arg in sys.argv:
            if arg.startswith("--date="):
                date_str = arg.split("=")[1]
        update(date_str)
    elif "--summary" in sys.argv:
        print(json.dumps(get_summary(), ensure_ascii=False, indent=2))
    else:
        print("用法: python performance.py --update | --summary")
        print("回溯分析请用 python backtest.py")


if __name__ == "__main__":
    main()
