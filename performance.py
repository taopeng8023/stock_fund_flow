"""
绩效追踪系统 — 记录每日选股 + N日后涨跌，计算胜率/均收益/最大回撤
用法:
  python performance.py --update      用今日数据更新昨日选股表现
  python performance.py --report      查看累计绩效
  python performance.py --date=20260520 --update  指定日期更新
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from fetchers.base import DATA_ROOT, BJS_TZ, load_json, format_amount

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
    """用今日数据回测历史选股表现（跳过当日选股，至少需要次日才有收益）"""
    if date_str is None:
        date_str = get_today_str()

    perf = load_performance()

    # 找到已过期但未回测的记录
    pending = [r for r in perf["records"]
               if r.get("next_ret") is None and r.get("pick_date") != date_str]

    if not pending:
        print("  无待回测记录（当日选股需等下一个交易日）")
        return perf

    # 对每个待回测记录，查找 date_str 数据计算从 pick 日到现在的收益
    updated = 0
    for record in pending:
        pick_date = record["pick_date"]
        pick_code = record["code"]

        today_rows = load_json(date_str, "fund_flow")
        if today_rows is None:
            print(f"  今日({date_str})数据不可用，跳过 {pick_date} 的选股回测")
            continue

        found = None
        for r in today_rows:
            if r.get("f12") == pick_code:
                found = r
                break

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
        # 重新计算汇总
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
    else:
        print(f"  无数据可更新")

    return perf


def report():
    """展示累计绩效"""
    perf = load_performance()
    s = perf.get("summary", {})
    records = perf.get("records", [])

    if not s:
        print("  无绩效数据，请先运行 python performance.py --update")
        return

    completed = [r for r in records if r.get("next_ret") is not None]

    print(f"\n{'=' * 70}")
    print(f"  选股绩效追踪 [{s.get('last_updated', '?')}]")
    print(f"{'=' * 70}\n")

    print(f"  累计选股: {s['total_picks']} 只")
    print(f"  胜率:     {s['win_rate']}%  ({s['wins']}赢 / {s['losses']}输)")
    print(f"  均收益:   {s['avg_return']:+.2f}%")
    print(f"  累计收益: {s['cum_return']:+.2f}%")
    print(f"  最大涨幅: {s['max_gain']:+.2f}%")
    print(f"  最大跌幅: {s['max_loss']:+.2f}%")

    # 最近 20 笔
    print(f"\n  {'─' * 55}")
    print(f"  最近交易记录:")
    print(f"  {'日期':<10} {'代码':<8} {'名称':<8} {'得分':<6} {'次日涨跌':<10} {'结果':<4}")
    print(f"  {'─' * 50}")
    for r in completed[-20:]:
        ret = r.get("next_ret", 0)
        mark = "✅" if ret > 0 else ("➖" if ret == 0 else "❌")
        print(f"  {r['pick_date']:<10} {r['code']:<8} {r.get('name','?'):<8} "
              f"{r.get('score',0):.4f} {ret:>+8.2f}%  {mark}")

    # 因子有效性分析
    if len(completed) >= 10:
        print(f"\n  {'─' * 55}")
        print(f"  因子有效性分析 (高分 vs 低分):")
        hi = sorted(completed, key=lambda r: r.get("score", 0), reverse=True)[:len(completed)//2]
        lo = sorted(completed, key=lambda r: r.get("score", 0))[:len(completed)//2]
        hi_avg = sum(r.get("next_ret", 0) for r in hi) / len(hi) if hi else 0
        lo_avg = sum(r.get("next_ret", 0) for r in lo) / len(lo) if lo else 0
        hi_win = sum(1 for r in hi if r.get("next_ret", 0) > 0) / len(hi) * 100 if hi else 0
        lo_win = sum(1 for r in lo if r.get("next_ret", 0) > 0) / len(lo) * 100 if lo else 0
        print(f"  高分组(前50%): 均收益 {hi_avg:+.2f}%, 胜率 {hi_win:.0f}%")
        print(f"  低分组(后50%): 均收益 {lo_avg:+.2f}%, 胜率 {lo_win:.0f}%")
        print(f"  因子区分度: {hi_avg - lo_avg:+.2f}%")

    print(f"\n  ⚠️  历史表现不保证未来收益\n")


def record_picks(picks, date_str=None):
    """记录当日选股到绩效文件（供 stock_picker.py 调用）"""
    if date_str is None:
        date_str = get_today_str()

    perf = load_performance()

    for row in picks:
        code = row.get("f12", "")
        # 避免同日重复
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


def main():
    if "--update" in sys.argv:
        date_str = get_today_str()
        for arg in sys.argv:
            if arg.startswith("--date="):
                date_str = arg.split("=")[1]
        print(f"更新绩效数据 ({date_str})...")
        update(date_str)
        report()
    elif "--report" in sys.argv:
        report()
    elif "--summary" in sys.argv:
        import json
        print(json.dumps(get_summary(), ensure_ascii=False, indent=2))
    else:
        print("用法:")
        print("  python performance.py --update   更新昨日选股的今日表现")
        print("  python performance.py --report   查看累计绩效")
        print("  python performance.py --summary  输出 JSON 格式绩效摘要")
        print("  python performance.py --date=20260520 --update  指定日期")


def get_summary():
    """程序化接口：返回绩效摘要 dict"""
    perf = load_performance()
    s = perf.get("summary", {})
    records = perf.get("records", [])
    completed = [r for r in records if r.get("next_ret") is not None]

    recent = []
    for r in completed[-20:]:
        recent.append({
            "pick_date": r["pick_date"],
            "code": r["code"],
            "name": r.get("name", ""),
            "score": r.get("score", 0),
            "next_ret": r.get("next_ret"),
        })

    # 高分组 vs 低分组
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


if __name__ == "__main__":
    main()
