"""
板块轮动动量 — 从日内板块资金流快照检测轮动早期信号

三个维度:
  1. 日内加速度: 后半段 vs 前半段资金流均值差
  2. 跨日排名跃升: 今日排名 vs 昨日排名变化  
  3. 连续正流天数: 持续资金净流入天数

用法:
  from daily_pipeline.sector_momentum import compute_sector_momentum
  scores = compute_sector_momentum("20260624")
  # → {industry_name: sector_rotation_score (0~1)}
"""
import csv, os, statistics
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta

RESEARCH_ROOT = Path(__file__).parent.parent / "research_data"

def _load_sector_flows(date_str):
    intraday = RESEARCH_ROOT / date_str / "intraday"
    if not intraday.exists(): return {}
    files = sorted([f for f in os.listdir(intraday)
                    if f.startswith("industry_flow_") and f.endswith(".csv")])
    if not files: return {}
    sectors = defaultdict(lambda: {"flows": [], "ranks": []})
    for f in files:
        with open(intraday / f, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        ranked = sorted(rows, key=lambda r: -float(r.get("主力净流入", 0) or 0))
        for rank, r in enumerate(ranked):
            name = r.get("名称", "")
            flow = float(r.get("主力净流入", 0) or 0)
            if name:
                sectors[name]["flows"].append(flow)
                sectors[name]["ranks"].append(rank + 1)
    return dict(sectors)

def _find_prev_date(date_str):
    dt = datetime.strptime(date_str, "%Y%m%d")
    for _ in range(14):
        dt -= timedelta(days=1)
        if dt.weekday() >= 5: continue
        prev = dt.strftime("%Y%m%d")
        if (RESEARCH_ROOT / prev / "intraday").exists():
            return prev
    return None

def compute_sector_momentum(date_str):
    """计算每个行业的轮动动量得分 0~1"""
    today = _load_sector_flows(date_str)
    if not today: return {}
    prev_date = _find_prev_date(date_str)
    yesterday = _load_sector_flows(prev_date) if prev_date else {}

    total_sectors = max(len(today), 1)
    scores = {}

    for name, data in today.items():
        flows = data["flows"]
        ranks = data["ranks"]
        n = len(flows)
        if n < 2: scores[name] = 0.5; continue

        # 1. 日内加速度 (40%): 后半段/前半段均值差
        mid = n // 2
        first_half = statistics.mean(flows[:mid])
        second_half = statistics.mean(flows[mid:])
        denom = max(abs(first_half), 1e8)
        accel = (second_half - first_half) / denom
        accel_score = 0.5 + max(-0.5, min(0.5, accel * 3))

        # 2. 跨日排名跃升 (35%): 昨日排名 → 今日排名
        rank_jump = 0
        if yesterday and name in yesterday:
            prev_ranks = yesterday[name]["ranks"]
            prev_last = prev_ranks[-1] if prev_ranks else total_sectors
            today_last = ranks[-1]
            rank_jump = (prev_last - today_last) / total_sectors  # 正=排名上升
        jump_score = 0.5 + max(-0.5, min(0.5, rank_jump * 5))

        # 3. 连续正流检测 (25%): 今天+昨天+前天正流天数
        conseq = 1 if flows[-1] > 0 else 0
        yesterday_flow = yesterday[name]["flows"][-1] if yesterday and name in yesterday else 0
        if yesterday_flow > 0: conseq += 1
        # 再往前一天
        if prev_date:
            prev2 = _find_prev_date(prev_date)
            if prev2:
                y2 = _load_sector_flows(prev2)
                if y2 and name in y2 and y2[name]["flows"][-1] > 0:
                    conseq += 1
        conseq_score = min(1.0, conseq / 3.0)

        scores[name] = round(accel_score * 0.40 + jump_score * 0.35 + conseq_score * 0.25, 3)

    return scores
