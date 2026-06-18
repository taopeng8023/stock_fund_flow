"""板块数据加载 — sectors/ 目录驱动，全板块全快照按时间顺序处理"""
import csv
import glob
import os
import re
from collections import defaultdict
from data_collector.fetchers.base import DATA_ROOT, load_json
from sector_screener.config import to_float

# 成分股 CSV 列名 → f-field 映射（对齐 data_collector/fetchers/sector_flow.py _save_sector_stocks_csv）
_STOCK_CSV_FIELD_MAP = {
    "最新价": "f2", "涨跌幅": "f3", "换手率": "f8", "量比": "f10",
    "代码": "f12", "名称": "f14", "最高价": "f15", "最低价": "f16",
    "开盘价": "f17", "昨收": "f18", "总市值": "f20",
    "今日主力净流入": "f62", "今日超大单净流入": "f66",
    "今日大单净流入": "f72", "今日中单净流入": "f78",
    "今日小单净流入": "f84", "今日主力占比": "f184",
    "5日主力净流入": "f164", "5日超大单净流入": "f166", "5日大单净流入": "f168",
    "10日主力净流入": "f174", "10日超大单净流入": "f176", "10日大单净流入": "f178",
}


def _parse_ts(filename):
    """从文件名提取时间戳: sector_stocks_BK0448_20260618_144929.csv → 144929"""
    m = re.search(r"_(\d{6})\.csv$", filename)
    return m.group(1) if m else "000000"


def _discover_sectors(date_str):
    """扫描 sectors/ 目录，返回 {sector_code: [csv_path_asc_by_time], ...}"""
    sector_dir = os.path.join(DATA_ROOT, date_str, "sectors")
    if not os.path.isdir(sector_dir):
        return {}
    sectors = defaultdict(list)
    pattern = os.path.join(sector_dir, "sector_stocks_*.csv")
    for f in sorted(glob.glob(pattern), key=_parse_ts):
        m = re.search(r"sector_stocks_(BK\d+)_", os.path.basename(f))
        if m:
            sectors[m.group(1)].append(f)
    return dict(sectors)


def _read_snapshot_csv(csv_path):
    """读取单个快照 CSV，返回 [(code, row_dict), ...] 按 今日排名 升序"""
    stocks = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = row.get("代码", "")
            if not code:
                continue
            s = {}
            for csv_col, f_field in _STOCK_CSV_FIELD_MAP.items():
                val = row.get(csv_col, "")
                if val != "" and val is not None:
                    s[f_field] = to_float(val) if f_field not in ("f12", "f14") else val
            s["_intra_rank"] = int(row.get("今日排名", 0) or 0)
            s["_rank_5d"] = int(row.get("5日排名", 0) or 0)
            s["_rank_10d"] = int(row.get("10日排名", 0) or 0)
            stocks.append((code, s))
    return stocks


def _load_concept_membership(date_str):
    """从 concept_sectors/ 读取概念板块成分股，构建 {stock_code: [concept_name, ...]} 反向索引"""
    sector_dir = os.path.join(DATA_ROOT, date_str, "concept_sectors")
    if not os.path.isdir(sector_dir):
        return {}

    # 从 concept_flow CSV 获取 BK 代码→概念名称 映射
    code_to_name = {}
    concept_flow_pattern = os.path.join(DATA_ROOT, date_str, "concept_flow_*.csv")
    cf_files = sorted(glob.glob(concept_flow_pattern), key=os.path.getmtime, reverse=True)
    if cf_files:
        with open(cf_files[0], "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                bk = row.get("代码", "")
                name = row.get("名称", "")
                if bk and name:
                    code_to_name[bk] = name

    membership = defaultdict(list)
    pattern = os.path.join(sector_dir, "sector_stocks_*.csv")
    # 每个概念板块取最新快照
    by_sector = defaultdict(list)
    for f in sorted(glob.glob(pattern), key=_parse_ts):
        m = re.search(r"sector_stocks_(BK\d+)_", os.path.basename(f))
        if m:
            by_sector[m.group(1)].append(f)
    for bk_code, files in by_sector.items():
        latest = files[-1]
        concept_name = code_to_name.get(bk_code, bk_code)
        stock_codes = set()
        with open(latest, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                sc = row.get("代码", "")
                if sc:
                    stock_codes.add(sc)
        for sc in stock_codes:
            membership[sc].append(concept_name)
    if membership:
        print(f"  概念板块成分股索引: {len(by_sector)} 个板块, {len(membership)} 只个股")
    return dict(membership)


def _enrich_from_fund_flow(stocks, date_str):
    """用 fund_flow.json 补全 CSV 缺失字段"""
    if not stocks:
        return
    try:
        fund_rows = load_json(date_str, "fund_flow")
    except Exception:
        return
    if not fund_rows:
        return
    ff_map = {r.get("f12", ""): r for r in fund_rows if r.get("f12")}
    protected = {"_sector_code", "_sector_name", "_intra_rank", "_rank_5d", "_rank_10d",
                 "_intraday_rank_trend", "_intraday_flow_trend", "_intraday_snapshots",
                 "_intraday_rank_first", "_intraday_rank_last", "_intraday_flow_delta_pct",
                 "_concept_names"}
    for s in stocks:
        ff = ff_map.get(s.get("f12", ""))
        if ff is None:
            continue
        for k, v in ff.items():
            if k not in s and k not in protected:
                s[k] = v


def _compute_intraday_metrics(all_snapshots, base_stocks):
    """根据全快照时间序列计算日内指标，注入 base_stocks 的 dict 中。
    all_snapshots: [(ts, [(code, row_dict), ...]), ...] 按时间升序
    base_stocks: [stock_dict, ...] 最终返回的个股列表（来自最新快照）
    """
    if len(all_snapshots) < 2:
        return
    # 建立 per-code 时间序列
    timeline = defaultdict(list)  # {code: [(ts, rank, f62, f184), ...]}
    for ts, snap in all_snapshots:
        for code, row in snap:
            timeline[code].append((ts, row.get("_intra_rank", 99), row.get("f62", 0), row.get("f184", 0)))
    # 注入到 base_stocks
    base_map = {s.get("f12", ""): s for s in base_stocks}
    for code, series in timeline.items():
        s = base_map.get(code)
        if s is None:
            continue
        if len(series) < 2:
            s["_intraday_snapshots"] = 1
            s["_intraday_rank_first"] = series[0][1]
            s["_intraday_rank_last"] = series[-1][1]
            s["_intraday_rank_trend"] = 0.0
            s["_intraday_flow_trend"] = 0.0
            s["_intraday_flow_delta_pct"] = 0.0
            continue
        ranks = [r for _, r, _, _ in series]
        flows = [f for _, _, f, _ in series]
        first_flow = flows[0] if flows[0] != 0 else 1.0
        # 排名趋势: -1→+1，负值=排名上升（变好）
        rank_slope = (ranks[-1] - ranks[0]) / max(len(series) - 1, 1)
        rank_trend = max(-1.0, min(1.0, -rank_slope / 10.0))
        # 资金趋势: -1→+1，正值=资金加速流入
        flow_delta_pct = (flows[-1] - flows[0]) / max(abs(first_flow), 1.0)
        flow_trend = max(-1.0, min(1.0, flow_delta_pct))
        s["_intraday_snapshots"] = len(series)
        s["_intraday_rank_first"] = ranks[0]
        s["_intraday_rank_last"] = ranks[-1]
        s["_intraday_rank_trend"] = round(rank_trend, 3)
        s["_intraday_flow_trend"] = round(flow_trend, 3)
        s["_intraday_flow_delta_pct"] = round(flow_delta_pct, 4)


# ── 公开接口 ──

def load_sector_top_codes(date_str, top_n=5):
    """从 sectors/ 目录发现板块，按最新快照板块总主力净流入排序，返回 top N 代码。
    sectors/ 为空时 fallback 到 industry_flow CSV。"""
    sectors = _discover_sectors(date_str)

    if sectors:
        # 板块名称映射（优先从 industry_flow CSV，再由成分股 CSV 名称列兜底）
        sector_names = _load_sector_names(date_str)
        # 按最新快照板块总主力净流入排序
        scored = []
        for code, files in sectors.items():
            latest_path = files[-1]  # 时间升序，最后一个最新
            total_f62 = 0.0
            with open(latest_path, "r", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    total_f62 += to_float(row.get("今日主力净流入", 0))
            name = sector_names.get(code, code)
            scored.append((code, name, total_f62))
        scored.sort(key=lambda x: x[2], reverse=True)
        codes = []
        for code, name, flow in scored[:top_n]:
            codes.append(code)
            flow_yi = flow / 1e8
            n_snap = len(sectors[code])
            print(f"  Top板块: {code} {name} 主力{flow_yi:+.2f}亿 ({n_snap}个快照)")
        print(f"  sectors/ 共发现 {len(sectors)} 个板块, 选取 top {len(codes)}")
        return codes

    # fallback: industry_flow CSV
    patterns = [
        os.path.join(DATA_ROOT, date_str, "industry_flow_*.csv"),
        os.path.join(DATA_ROOT, date_str, "industry_flow.csv"),
    ]
    all_matches = []
    for pat in patterns:
        all_matches.extend(glob.glob(pat))
    csv_path = sorted(all_matches, key=os.path.getmtime, reverse=True)[0] if all_matches else ""
    codes = []
    if csv_path and os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        sorted_rows = sorted(rows, key=lambda r: to_float(r.get("主力净流入")), reverse=True)
        for r in sorted_rows[:top_n]:
            code = r.get("代码", "")
            codes.append(code)
            flow_yi = to_float(r.get("主力净流入")) / 1e8
            print(f"  Top板块: {code} {r.get('名称','')} 主力{flow_yi:+.2f}亿")
    else:
        print(f"  ✗ 未找到板块数据: data/{date_str}/")
    return codes


def _load_sector_names(date_str):
    """BK代码→板块名称 映射（从 industry_flow CSV）"""
    patterns = [
        os.path.join(DATA_ROOT, date_str, "industry_flow_*.csv"),
        os.path.join(DATA_ROOT, date_str, "industry_flow.csv"),
    ]
    all_matches = []
    for pat in patterns:
        all_matches.extend(glob.glob(pat))
    csv_path = sorted(all_matches, key=os.path.getmtime, reverse=True)[0] if all_matches else ""
    if csv_path and os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            return {r["代码"]: r["名称"] for r in csv.DictReader(f)}
    return {}


def load_sector_stocks(sector_codes, date_str=None):
    """加载板块成分股：按快照时间顺序处理 sectors/ CSV，计算日内轨迹指标。
    找不到 CSV 时 fallback 到实时 API。"""
    all_stocks = []
    seen = set()
    sector_names = _load_sector_names(date_str) if date_str else {}
    api_codes = []

    if date_str:
        sectors = _discover_sectors(date_str)
        for code in sector_codes:
            files = sectors.get(code, [])
            if not files:
                api_codes.append(code)
                continue

            # 加载全快照（时间升序）
            all_snapshots = []  # [(ts, [(code, row_dict), ...]), ...]
            for f in files:
                ts = _parse_ts(f)
                snap = _read_snapshot_csv(f)
                all_snapshots.append((ts, snap))

            # 最新快照 → base 数据
            latest_ts, latest_snap = all_snapshots[-1]
            for stock_code, row in latest_snap:
                if stock_code and stock_code not in seen:
                    seen.add(stock_code)
                    row["_sector_code"] = code
                    row["_sector_name"] = sector_names.get(code, code)
                    all_stocks.append(row)

            # 用全快照计算日内指标
            _compute_intraday_metrics(all_snapshots, all_stocks)

            n_snap = len(files)
            n_stocks = len(latest_snap)
            # 日内指标统计
            with_trend = sum(1 for s in all_stocks
                             if s.get("_sector_code") == code and s.get("_intraday_snapshots", 0) >= 2)
            print(f"    板块 {code}: {n_snap}个快照 {n_stocks}只个股"
                  f"（{with_trend}只有日内轨迹） [{all_snapshots[0][0]}→{latest_ts}]")

        if all_stocks:
            csv_count = len(all_stocks)
            # 概念板块成分股标签
            concept_membership = _load_concept_membership(date_str)
            if concept_membership:
                for s in all_stocks:
                    code = s.get("f12", "")
                    if code in concept_membership:
                        s["_concept_names"] = concept_membership[code]
                tagged = sum(1 for s in all_stocks if "_concept_names" in s)
                print(f"  概念标签: {tagged}/{csv_count} 只个股有概念板块归属")

            _enrich_from_fund_flow(all_stocks, date_str)
            enriched = sum(1 for s in all_stocks if "f5" in s)
            print(f"  CSV 模式: {csv_count} 只个股（{len(sector_codes) - len(api_codes)} 个板块）"
                  f"，fund_flow 补全 {enriched}/{csv_count}")
    else:
        api_codes = list(sector_codes)

    # fallback: 实时 API
    if api_codes:
        from data_collector.fetchers.sector_flow import fetch_sector_stocks
        for code in api_codes:
            print(f"  ⚠ CSV 未覆盖 {code}，实时拉取...", end=" ", flush=True)
            stocks = fetch_sector_stocks(code, "", None)
            if not stocks:
                print("无数据")
                continue
            for s in stocks:
                stock_code = s.get("f12", "")
                if stock_code and stock_code not in seen:
                    seen.add(stock_code)
                    s["_sector_code"] = code
                    s["_sector_name"] = sector_names.get(code, code)
                    all_stocks.append(s)
            print(f"{len(stocks)} 只")

    print(f"  去重后共计 {len(all_stocks)} 只个股（{len(sector_codes)} 个板块）")
    return all_stocks


def load_sector_multiday(date_str):
    """行业板块今日/5日/10日排名 → 板块新鲜度 + 持续性"""
    patterns = [
        os.path.join(DATA_ROOT, date_str, "industry_flow_*.csv"),
        os.path.join(DATA_ROOT, date_str, "industry_flow.csv"),
    ]
    all_matches = []
    for pat in patterns:
        all_matches.extend(glob.glob(pat))
    csv_path = sorted(all_matches, key=os.path.getmtime, reverse=True)[0] if all_matches else ""
    if not csv_path:
        return {}, {}, {}
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {}, {}, {}

    by_f62 = sorted(rows, key=lambda r: to_float(r.get("主力净流入")), reverse=True)
    by_5d = sorted(rows, key=lambda r: to_float(r.get("5日主力净流入")), reverse=True)
    by_10d = sorted(rows, key=lambda r: to_float(r.get("10日主力净流入")), reverse=True)

    rank_today = {r.get("代码"): i + 1 for i, r in enumerate(by_f62)}
    rank_5d = {r.get("代码"): i + 1 for i, r in enumerate(by_5d)}
    rank_10d = {r.get("代码"): i + 1 for i, r in enumerate(by_10d)}
    total = len(rows)

    sector_freshness = {}
    sector_persistence = {}
    for r in rows:
        code = r.get("代码", "")
        r_today = rank_today.get(code, total)
        r_5d = rank_5d.get(code, total)
        r_10d = rank_10d.get(code, total)
        jump_5d = (r_5d - r_today) / total
        jump_10d = (r_10d - r_today) / total
        freshness = max(0.0, min(1.0, (jump_5d * 0.6 + jump_10d * 0.4 + 0.3)))
        sector_freshness[code] = round(freshness, 3)
        persistence = 1.0 if r_5d <= total * 0.3 else (0.7 if r_5d <= total * 0.5 else 0.4)
        sector_persistence[code] = persistence

    return sector_freshness, rank_today, sector_persistence


def load_sector_intraday(date_str):
    """从 industry_flow_*.csv 全部日内快照计算板块级资金轨迹。
    返回 {sector_code: {flow_trend, snapshots, flow_first, flow_last, flow_delta_pct}}
    flow_trend: +1=日内加速流入, -1=日内加速流出
    """
    import re
    pattern = os.path.join(DATA_ROOT, date_str, "industry_flow_*.csv")
    # 只取时间戳快照（6位数字），排除 5d/10d 汇总文件
    all_files = glob.glob(pattern)
    files = sorted(
        [f for f in all_files if re.search(r"_(\d{6})\.csv$", f)],
        key=lambda f: re.search(r"_(\d{6})\.csv$", f).group(1)
    )
    if len(files) < 2:
        return {}

    # 读取所有快照
    snapshots = []  # [(ts, {code: total_f62}), ...]
    for f in files:
        m = re.search(r"_(\d{6})\.csv$", f)
        ts = m.group(1) if m else "000000"
        sector_flows = {}
        with open(f, "r", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                code = row.get("代码", "")
                flow = to_float(row.get("主力净流入"))
                if code:
                    sector_flows[code] = flow
        snapshots.append((ts, sector_flows))

    # 计算每个板块的日内轨迹
    trajectory = {}
    all_codes = set()
    for _, sf in snapshots:
        all_codes.update(sf.keys())

    for code in all_codes:
        flows = []
        for ts, sf in snapshots:
            if code in sf:
                flows.append(sf[code])
        if len(flows) < 2:
            continue
        first_flow = flows[0]
        last_flow = flows[-1]
        denom = max(abs(first_flow), 1.0)
        flow_delta_pct = (last_flow - first_flow) / denom
        flow_trend = max(-1.0, min(1.0, flow_delta_pct))
        trajectory[code] = {
            "flow_trend": round(flow_trend, 3),
            "snapshots": len(flows),
            "flow_first": first_flow,
            "flow_last": last_flow,
            "flow_delta_pct": round(flow_delta_pct, 4),
        }

    if trajectory:
        accelerating = sum(1 for v in trajectory.values() if v["flow_trend"] > 0.1)
        decelerating = sum(1 for v in trajectory.values() if v["flow_trend"] < -0.1)
        print(f"  板块日内轨迹: {len(trajectory)} 个板块（{accelerating}↑加速 {decelerating}↓减速）"
              f" [{snapshots[0][0]}→{snapshots[-1][0]}]")
    return trajectory
