"""CSV 结果输出"""
import csv
import os
from datetime import datetime
from data_collector.fetchers.base import DATA_ROOT
from sector_screener.config import to_float

_PICKS_FIELDS = [
    "排名", "代码", "名称", "综合得分",
    "涨跌幅", "最新价", "主力净流入", "主力占比",
    "5日主力净流入", "10日主力净流入",
    "超大单净流入", "大单净流入",
    "换手率", "量比", "总市值",
    "启动得分", "资金得分", "趋势得分", "板块得分", "位置得分",
    "分析师得分", "多日得分", "技术面得分",
    "龙虎榜得分", "北向得分", "占比排名得分",
    "行业内得分", "融资得分", "加速度得分",
    "分析师家数", "均线排列", "突破20日",
    "所属板块",
]

_LIMIT_FIELDS = [
    "代码", "名称", "涨跌幅", "最新价",
    "主力净流入", "主力占比",
    "超大单净流入", "大单净流入",
    "换手率", "量比", "总市值",
    "封板力度", "所属板块", "观察要点",
]


def save_csv(scored, limit_up, date_str, top_n=10):
    """保存 CSV 到 data/<date>/picks/"""
    date_dir = os.path.join(DATA_ROOT, date_str)
    picks_dir = os.path.join(date_dir, "picks")
    os.makedirs(picks_dir, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S")

    _write_picks(scored[:top_n], picks_dir, ts)
    _write_limit(limit_up, picks_dir, ts)


def _write_picks(candidates, picks_dir, ts):
    path = os.path.join(picks_dir, f"enhanced_picks_{ts}.csv")
    rows = []
    for s in candidates:
        rows.append({
            "排名": s.get("_rank", ""),
            "代码": s.get("f12", ""), "名称": s.get("f14", ""),
            "综合得分": s.get("_score", ""),
            "涨跌幅": to_float(s.get("f3")),
            "最新价": to_float(s.get("f2")),
            "主力净流入": to_float(s.get("f62")),
            "主力占比": to_float(s.get("f184")),
            "5日主力净流入": s.get("_f62_5d", 0),
            "10日主力净流入": s.get("_f62_10d", 0),
            "超大单净流入": to_float(s.get("f66")),
            "大单净流入": to_float(s.get("f72")),
            "换手率": to_float(s.get("f8")),
            "量比": to_float(s.get("f10")),
            "总市值": to_float(s.get("f20")),
            "启动得分": s.get("_score_start", ""),
            "资金得分": s.get("_score_capital", ""),
            "趋势得分": s.get("_score_trend", ""),
            "板块得分": s.get("_score_sector", ""),
            "位置得分": s.get("_score_position", ""),
            "分析师得分": s.get("_score_analyst", ""),
            "多日得分": s.get("_score_multiday", ""),
            "技术面得分": s.get("_score_technical", ""),
            "龙虎榜得分": s.get("_s_dragon_tiger", ""),
            "北向得分": s.get("_s_north_flow", ""),
            "占比排名得分": s.get("_s_ratio_rank", ""),
            "行业内得分": s.get("_s_intra_sector", ""),
            "融资得分": s.get("_s_margin_net", ""),
            "加速度得分": s.get("_s_flow_accel", ""),
            "分析师家数": s.get("_analyst_num", ""),
            "均线排列": s.get("_ma_align", ""),
            "突破20日": "是" if s.get("_breakout_20d") else "",
            "所属板块": s.get("_sector_name", ""),
        })
    _write_csv(path, _PICKS_FIELDS, rows)


def _write_limit(limit_up, picks_dir, ts):
    path = os.path.join(picks_dir, f"enhanced_limit_up_{ts}.csv")
    rows = []
    for s in sorted(limit_up, key=lambda x: to_float(x.get("f62")), reverse=True):
        f184 = to_float(s.get("f184"))
        f72 = to_float(s.get("f72"))
        f8 = to_float(s.get("f8"))
        if f184 > 8 and f8 < 10:
            seal, note = "强", "封板坚决,关注次日高开"
        elif f184 > 4:
            seal, note = "中", "主力有分歧,等开板回踩"
        else:
            seal, note = "弱", "封板力度弱,谨慎追高"
        note += " | 大单净流入" if f72 > 0 else " | 大单流出,注意承接"
        rows.append({
            "代码": s.get("f12", ""), "名称": s.get("f14", ""),
            "涨跌幅": to_float(s.get("f3")),
            "最新价": to_float(s.get("f2")),
            "主力净流入": to_float(s.get("f62")),
            "主力占比": f184,
            "超大单净流入": to_float(s.get("f66")),
            "大单净流入": f72,
            "换手率": f8, "量比": to_float(s.get("f10")),
            "总市值": to_float(s.get("f20")),
            "封板力度": seal, "所属板块": s.get("_sector_name", ""),
            "观察要点": note,
        })
    _write_csv(path, _LIMIT_FIELDS, rows)


def _write_csv(path, fields, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(fields)
        for row in rows:
            writer.writerow([row.get(k, "") for k in fields])
