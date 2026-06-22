"""
板块轮动检测 — 从 sectors/ 全量盘中快照分析资金轮动
输入: data/<date>/sectors/sector_stocks_*.csv（全天半小时快照）
输出: 每个板块的日内轨迹 + 跨板块轮动信号
"""
import csv
import glob
import os
import re
from collections import defaultdict
from data_collector.fetchers.base import DATA_ROOT
from sector_screener.config import to_float


def _discover_all_snapshots(date_str):
    """扫描 sectors/ 全部快照，返回 {sector_code: [(ts, filepath), ...]} 按时间升序"""
    sector_dir = os.path.join(DATA_ROOT, date_str, "sectors")
    if not os.path.isdir(sector_dir):
        return {}
    sectors = defaultdict(list)
    for f in sorted(glob.glob(os.path.join(sector_dir, "sector_stocks_*.csv"))):
        m = re.search(r"sector_stocks_(BK\d+)_\d{8}_(\d{6})", os.path.basename(f))
        if m:
            sectors[m.group(1)].append((m.group(2), f))
    # 按时间排序
    for code in sectors:
        sectors[code].sort(key=lambda x: x[0])
    return dict(sectors)


def _sector_total_flow(csv_path):
    """读取单个快照的板块总主力净流入"""
    total = 0.0
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                total += to_float(row.get("今日主力净流入", 0))
    except Exception:
        pass
    return total


def _ts_to_hours(ts):
    """时间戳 → 小数小时，如 '094832' → 9.809"""
    h, m, s = int(ts[:2]), int(ts[2:4]), int(ts[4:6])
    return h + m / 60.0 + s / 3600.0


def _linear_slope(xs, ys):
    """简单线性回归斜率"""
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den > 0 else 0.0


def load_sector_rotation(date_str):
    """分析全部板块的日内轨迹，输出轮动信号。

    Returns:
        {
            "trajectories": {sector_code: {...}},
            "rotation_signals": {
                "rising_sectors": [...],
                "fading_sectors": [...],
                "stable_leaders": [...],
                "dropped_out": [...],
                "concentration": 0~1,
            },
            "sector_momentum_score": {sector_code: 0~1},
        }
    """
    all_snapshots = _discover_all_snapshots(date_str)
    if len(all_snapshots) < 2:
        return {"trajectories": {}, "rotation_signals": {}, "sector_momentum_score": {}}

    # ── 构建每个板块的时序数据 ──
    trajectories = {}
    all_timestamps = set()

    for code, files in all_snapshots.items():
        if len(files) < 2:
            continue
        ts_list = []
        flow_list = []
        for ts, fpath in files:
            ts_list.append(ts)
            flow_list.append(_sector_total_flow(fpath) / 1e8)  # 转换为亿
            all_timestamps.add(ts)

        hours = [_ts_to_hours(ts) for ts in ts_list]
        time_span = hours[-1] - hours[0] if len(hours) >= 2 else 1.0

        # 排名趋势（在每个时间戳上的排名）
        trajectories[code] = {
            "ts_list": ts_list,
            "flow_list": flow_list,
            "hours": hours,
            "snap_count": len(files),
            "time_span_h": round(time_span, 2),
            "flow_first": flow_list[0],
            "flow_last": flow_list[-1],
        }

    if not trajectories:
        return {"trajectories": {}, "rotation_signals": {}, "sector_momentum_score": {}}

    # ── 计算每个时点的排名 ──
    sorted_ts = sorted(all_timestamps)
    for code, traj in trajectories.items():
        ranks = []
        for ts in traj["ts_list"]:
            # 找出该时点所有板块的流入，计算排名
            ts_flows = []
            for c2, t2 in trajectories.items():
                if ts in t2["ts_list"]:
                    idx = t2["ts_list"].index(ts)
                    ts_flows.append((t2["flow_list"][idx], c2))
            ts_flows.sort(reverse=True)
            for i, (_, c2) in enumerate(ts_flows):
                if c2 == code:
                    ranks.append(i + 1)
                    break
        traj["ranks"] = ranks
        traj["rank_first"] = ranks[0] if ranks else 0
        traj["rank_last"] = ranks[-1] if ranks else 0
        traj["rank_min"] = min(ranks) if ranks else 0
        traj["rank_max"] = max(ranks) if ranks else 0

    # ── 计算高级指标 ──
    total_sectors = len(trajectories)
    all_ranks = [t["rank_last"] for t in trajectories.values()]

    for code, traj in trajectories.items():
        hours = traj["hours"]
        flows = traj["flow_list"]
        ranks = traj["ranks"]
        n = len(ranks)

        # rank_trend: 负值=排名上升(改善), 正值=排名下降(恶化)
        traj["rank_trend"] = round(_linear_slope(hours, ranks), 3)

        # flow_velocity: 亿/小时
        time_span = traj["time_span_h"]
        traj["flow_velocity"] = round(
            (traj["flow_last"] - traj["flow_first"]) / time_span, 1
        ) if time_span > 0 else 0.0

        # flow_accel: 后半段速度 - 前半段速度（需 >= 4 个快照）
        if n >= 4 and time_span > 0.5:
            mid = n // 2
            first_half_vel = (flows[mid] - flows[0]) / (hours[mid] - hours[0]) if hours[mid] > hours[0] else 0
            second_half_vel = (flows[-1] - flows[mid]) / (hours[-1] - hours[mid]) if hours[-1] > hours[mid] else 0
            traj["flow_accel"] = round(second_half_vel - first_half_vel, 1)
        else:
            traj["flow_accel"] = 0.0

        # early_peak: 最优排名出现在前 1/3 时段
        peak_idx = ranks.index(traj["rank_min"])
        traj["peak_time"] = traj["ts_list"][peak_idx]
        traj["early_peak"] = peak_idx < n / 3 and traj["rank_min"] < traj["rank_last"]
        traj["late_surge"] = (
            peak_idx >= 2 * n / 3
            and traj["rank_trend"] < -0.2
            and traj["rank_min"] <= 3
        )

        # stability: 排名标准差越小越稳定
        if n >= 2:
            mean_r = sum(ranks) / n
            std_r = (sum((r - mean_r) ** 2 for r in ranks) / n) ** 0.5
            traj["rank_std"] = round(std_r, 2)
            traj["stability"] = round(max(0.0, 1.0 - std_r / max(total_sectors, 1)), 3)
        else:
            traj["rank_std"] = 0.0
            traj["stability"] = 1.0

    # ── 轮动信号分类（优先级: dropped_out > fading > rising > stable_leaders）──
    rising_sectors = []
    fading_sectors = []
    stable_leaders = []
    dropped_out = []

    for code, traj in trajectories.items():
        rank_last = traj["rank_last"]
        rank_first = traj["rank_first"]
        rank_trend = traj["rank_trend"]

        # 跌出前 5：盘中进过前 5 但收盘不在（最高优先级）
        if traj["rank_min"] <= 5 and rank_last > 5:
            dropped_out.append(code)
        # 排名下降：趋势为正 且 排名恶化 >= 2 位
        elif rank_trend > 0.3 and (rank_last - rank_first) >= 1:
            fading_sectors.append(code)
        # 排名上升：趋势为负 且 排名改善 >= 1 位
        elif rank_trend < -0.3 and (rank_first - rank_last) >= 1:
            rising_sectors.append(code)
        # 持续领涨：始终在前 3 名 且 趋势不恶化
        elif traj["rank_max"] <= 3 and traj["stability"] > 0.7 and rank_trend < 0.3:
            stable_leaders.append(code)

    # 资金集中度：前 3 板块流入占全部板块流入的比例
    final_flows = [(traj["flow_last"], code) for code, traj in trajectories.items()]
    final_flows.sort(reverse=True)
    total_flow = sum(f for f, _ in final_flows)
    top3_flow = sum(f for f, _ in final_flows[:3])
    concentration = round(top3_flow / total_flow, 3) if total_flow > 0 else 0.0

    # ── 板块动量综合得分 ──
    # 将 rank_trend 映射到 0~1（越负 = 越上升 = 越高分）
    all_trends = [t["rank_trend"] for t in trajectories.values()]
    t_min, t_max = min(all_trends), max(all_trends)
    t_range = max(t_max - t_min, 0.01)

    all_accels = [t["flow_accel"] for t in trajectories.values()]
    a_min, a_max = min(all_accels), max(all_accels)
    a_range = max(a_max - a_min, 0.01)

    momentum_score = {}
    for code, traj in trajectories.items():
        # rank_trend → 0~1 (负 = 好)
        trend_norm = (t_max - traj["rank_trend"]) / t_range
        # flow_accel → 0~1 (正 = 好)
        accel_norm = (traj["flow_accel"] - a_min) / a_range
        # 综合: rank_trend 40% + flow_accel 35% + stability 25%
        score = (
            trend_norm * 0.40
            + accel_norm * 0.35
            + traj["stability"] * 0.25
        )
        momentum_score[code] = round(max(0.0, min(1.0, score)), 3)

    return {
        "trajectories": trajectories,
        "rotation_signals": {
            "rising_sectors": rising_sectors,
            "fading_sectors": fading_sectors,
            "stable_leaders": stable_leaders,
            "dropped_out": dropped_out,
            "concentration": concentration,
        },
        "sector_momentum_score": momentum_score,
    }
