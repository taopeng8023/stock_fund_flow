"""
步骤2: 收盘全市场评分 — 对所有股票打分，保存 scores.csv
使用 fund_flow 字段 + 价格历史，权重对齐 sector_screener
"""
import csv
import json
import os
import statistics
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta

BJS_TZ = timezone(timedelta(hours=8))
RESEARCH_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data"
)

# ── 评分权重（对齐 sector_screener 优化后权重） ──
WEIGHTS = {
    "capital": 0.19, "start_signal": 0.13, "trend": 0.10,
    "position": 0.07, "multiday": 0.06, "sector": 0.05,
    "analyst": 0.01, "technical": 0.05, "intra_sector": 0.04,
    "margin_net": 0.03, "flow_accel": 0.01,
    "flow_stability": 0.03, "intraday_accel": 0.03,
    "rank_trajectory": 0.02, "vwap_position": 0.02,
    "sector_trajectory": 0.02,
}

# ── 刚启动检测权重（降资金权重，升启动/位置/趋势）──
EARLY_WEIGHTS = {
    "capital": 0.10, "start_signal": 0.25, "trend": 0.15,
    "position": 0.15, "multiday": 0.08, "sector": 0.07,
    "analyst": 0.00, "technical": 0.05, "intra_sector": 0.05,
    "margin_net": 0.03, "flow_accel": 0.02,
    "flow_stability": 0.02, "intraday_accel": 0.02,
    "rank_trajectory": 0.02, "vwap_position": 0.02,
    "sector_trajectory": 0.03,
}

# ── scores.csv 输出列 ──
# ── 综合得分信号 ──
COMPOSITE_SIGNALS = {
    "P32_ratio_accel":    "主力占比温和加速(今日>5日>10日)",
    "P32_pump_risk":      "单日脉冲风险(今日占比高但5日低迷)",
    "P32_extreme":        "占比极端高位(均值回归压力)",
    "P29_high_turnover":  "高换手+弱资金(出货嫌疑)",
    "P_low_liquidity":    "换手过低(流动性不足)",
    "P_low_vol_ratio":    "量比不足(交投清淡)",
    "P_small_cap":        "小市值风险(<30亿)",
    "P6_retail":          "散户主导(小单>30%且涨幅小)",
    "P_high_price":       "高价股(>200元)",
    "P33_margin_strong":  "融资买入占比高(>8%,杠杆资金看多)",
    "P33_margin_moderate":"融资买入占比中等(3-8%)",
    "P33_margin_weak":    "融资买入占比为负(<-5%,杠杆资金撤退)",
    "P34_gap_strong":     "高开高走(开盘缺口>2%且收涨>2%,回测+0.76%最强)",
    "P34_gap_reverse":    "低开反转(开盘缺口<-2%但收涨>1%,回测-0.72%已降权)",
    "P34_gap_trap":       "高开陷阱(开盘缺口>3%但收跌)",
    "P35_short_cover":    "融券空头回补(净卖出<-1亿)",
    "P35_short_pressure": "融券强做空(净卖出>3亿)",
    "P35_short_moderate": "融券中度做空(净卖出>1亿)",
    "P35_short_heavy":    "融券/主力比>3(做空压力大)",
    "P36_overheat":       "全维度过热(资金>0.85+趋势>0.7+多日>0.85,反转风险)",
    "P37_momentum_up":    "得分动量向上(较前日改善>0.05,资金加速)",
    "P37_momentum_down":  "得分动量向下(较前日恶化>0.05,资金撤退)",
}

# ── 启动得分专属信号 ──
EARLY_SIGNALS = {
    "E1_low_start":       "低位启动(位置<0.25且资金加速)",
    "E2_moderate_start":  "温和启动(资金0.4-0.7+启动因子>0.6)",
    "E3_strong_start":    "强势启动(启动因子>0.8+资金>0.5)",
    "E4_gap_start":       "缺口启动(高开高走/低开反转+低位)",
    "E5_ratio_early":     "占比早期加速(占比5-8%温和上升)",
    "E6_short_squeeze":   "逼空启动(融券回补+资金流入)",
}

SCORE_HEADERS = [
    "代码", "名称", "最新价", "行业", "综合得分", "启动得分",
    "资金得分", "趋势得分", "启动因子", "板块得分", "位置得分",
    "分析师得分", "多日得分", "技术面得分", "行业内得分",
    "融资得分", "加速度得分", "占比趋势得分",
    "日内稳定", "日内加速", "排名轨迹", "VWAP位置", "板块轨迹",
    "涨跌幅", "换手率", "量比", "总市值",
    "综合信号", "综合信号说明", "启动信号", "启动信号说明",
]

# ── 因子中文说明 ──
FACTOR_INFO = {
    "资金得分":   "主力资金强度 — f62主力净流入+f184主力占比+f69超大单占比 的百分位综合",
    "趋势得分":   "趋势质量 — 量比15%+换手率25%+单日动量25%+中期动量20%+短趋势15%",
    "启动得分":   "资金加速信号 — 5日/10日流加速度+超大单质量，检测资金刚启动的股票",
    "板块得分":   "板块共振 — 所属行业板块在全市场资金流中的排名位置",
    "位置得分":   "价格位置 — 当前价在60日高低区间内的位置，低位=均值回归机会，高位=回调风险",
    "分析师得分": "分析师共识度(暂用中性值，待接入分析师数据)",
    "多日得分":   "多日资金持续性 — 5日/10日累计主力净流入百分位+方向一致性加分",
    "技术面得分": "技术面强度 — MA均线多头排列60%+60日突破信号40%",
    "行业内得分": "行业内龙头 — 主力净流入在同行业内的百分位排名",
    "融资得分":   "融资动向 — f168融资净买入全市场百分位",
    "加速度得分": "流加速度 — 5日流/10日流比值，衡量资金流入在加速还是减速",
    "占比趋势得分": "三周期主力占比趋势 — 今日/5日/10日占比方向(P32信号)",
}

# 腾讯K线 API (获取价格历史)
KLINE_URL = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _tof(val, default=0.0):
    if val is None or val == "" or val == "-": return default
    try: return float(val)
    except: return default


def _range_score(value, ideal_min, ideal_max, floor, ceil):
    if ideal_min <= value <= ideal_max: return 1.0
    if value < ideal_min:
        return max(0.0, (value - floor) / (ideal_min - floor)) if ideal_min > floor else 0.0
    return max(0.0, (ceil - value) / (ceil - ideal_max)) if ceil > ideal_max else 0.0


def _pct_rank(values, target):
    if not values or max(values) == min(values): return 0.5
    return sum(1 for v in values if v <= target) / len(values)


def _load_stock_prices(codes, max_stocks=200):
    """从腾讯K线获取价格历史（近60日），限制请求数"""
    prices = {}
    count = 0
    for code in codes:
        if count >= max_stocks: break
        mkt = "sh" if code.startswith("6") else "sz"
        url = f"{KLINE_URL}?param={mkt}{code},day,,,65,qfq"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            key = f"{mkt}{code}"
            if data.get("data") and data["data"].get(key) and data["data"][key].get("qfqday"):
                closes = [float(k[2]) for k in data["data"][key]["qfqday"]]
                if len(closes) >= 20:
                    prices[code] = closes
                    count += 1
        except:
            pass
    return prices


def _load_latest_fund_flow(date_str):
    """加载最新的盘中快照 CSV"""
    intraday_dir = os.path.join(RESEARCH_ROOT, date_str, "intraday")
    if not os.path.isdir(intraday_dir): return None

    files = sorted(
        [f for f in os.listdir(intraday_dir) if f.startswith("fund_flow_") and f.endswith(".csv")],
        reverse=True,
    )
    if not files: return None

    path = os.path.join(intraday_dir, files[0])
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def _load_sector_flows(date_str):
    """加载最新行业板块流，按名称索引"""
    intraday_dir = os.path.join(RESEARCH_ROOT, date_str, "intraday")
    if not os.path.isdir(intraday_dir): return {}, {}

    # 行业流 — 用"名称"列做 key（stock的f100是行业名称，非代码）
    ind_files = sorted(
        [f for f in os.listdir(intraday_dir) if f.startswith("industry_flow_") and f.endswith(".csv")],
        reverse=True,
    )
    sector_map = {}
    if ind_files:
        with open(os.path.join(intraday_dir, ind_files[0]), encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                name = r.get("名称", "")
                flow = _tof(r.get("主力净流入"))
                if name:
                    sector_map[name] = flow

    # 概念流
    con_files = sorted(
        [f for f in os.listdir(intraday_dir) if f.startswith("concept_flow_") and f.endswith(".csv")],
        reverse=True,
    )
    concept_map = {}
    if con_files:
        with open(os.path.join(intraday_dir, con_files[0]), encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                name = r.get("名称", "")
                flow = _tof(r.get("主力净流入"))
                if name:
                    concept_map[name] = flow

    return sector_map, concept_map


def _build_price_history(date_str, stocks):
    """从 research_data 跨日累积收盘价，构建 {code: [close_0, close_1, ...]} (0=最新)"""
    price_hist = {}

    # 1. 当前日: 从 stocks 中取 f2(最新价) + f15(最高) + f16(最低)
    for s in stocks:
        code = s.get("代码", "")
        close = _tof(s.get("最新价"))
        high = _tof(s.get("最高"))
        low = _tof(s.get("最低"))
        if code and close > 0:
            price_hist[code] = {
                "closes": [close],
                "high_60": high if high > 0 else close,
                "low_60": low if low > 0 else close,
            }

    # 2. 历史日: 读取之前日期的 fund_flow CSV
    date_dirs = sorted([
        d for d in os.listdir(RESEARCH_ROOT)
        if os.path.isdir(os.path.join(RESEARCH_ROOT, d)) and d.isdigit() and d < date_str
    ], reverse=True)

    for prev_date in date_dirs[:60]:  # 最多回溯60天
        intraday_dir = os.path.join(RESEARCH_ROOT, prev_date, "intraday")
        if not os.path.isdir(intraday_dir):
            continue
        # 取最晚的快照（最接近收盘价）
        files = sorted([
            f for f in os.listdir(intraday_dir)
            if f.startswith("fund_flow_") and f.endswith(".csv")
        ], reverse=True)
        if not files:
            continue

        path = os.path.join(intraday_dir, files[0])
        try:
            with open(path, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    code = r.get("代码", "")
                    close = _tof(r.get("最新价"))
                    high = _tof(r.get("最高"))
                    low = _tof(r.get("最低"))
                    if code and close > 0:
                        if code not in price_hist:
                            price_hist[code] = {"closes": [], "high_60": 0, "low_60": float("inf")}
                        price_hist[code]["closes"].append(close)
                        if high > 0:
                            price_hist[code]["high_60"] = max(price_hist[code]["high_60"], high)
                        if low > 0:
                            price_hist[code]["low_60"] = min(price_hist[code]["low_60"], low)
        except Exception:
            continue

    # 清理无限值
    for code in price_hist:
        if price_hist[code]["low_60"] == float("inf"):
            price_hist[code]["low_60"] = price_hist[code]["closes"][0] if price_hist[code]["closes"] else 0
        if price_hist[code]["high_60"] == 0:
            price_hist[code]["high_60"] = price_hist[code]["closes"][0] if price_hist[code]["closes"] else 0

    n_with_history = sum(1 for v in price_hist.values() if len(v["closes"]) >= 3)
    print(f"  价格历史: {n_with_history} 只 (≥3天), 共{len(price_hist)}只有数据")
    return price_hist


def _load_all_snapshots(date_str, cutoff=None):
    """加载全天所有快照,构建时序结构。
    cutoff='1430' 只取 ≤1430 的快照。返回 (snapshots, time_series)
      snapshots: [{ts, stocks: [{code, ...}]}]
      time_series: {code: {ts: {f2, f62, f184, f8, f3}}}
    """
    intraday_dir = os.path.join(RESEARCH_ROOT, date_str, "intraday")
    if not os.path.isdir(intraday_dir): return [], {}

    files = sorted([
        f for f in os.listdir(intraday_dir)
        if f.startswith("fund_flow_") and f.endswith(".csv")
    ])
    if not files: return [], {}

    # cutoff 过滤
    if cutoff:
        files = [f for f in files if f.replace("fund_flow_","").replace(".csv","") <= cutoff]
    if not files: return [], {}

    snapshots = []
    time_series = defaultdict(dict)  # {code: {ts: {fields}}}

    for f in files:
        ts = f.replace("fund_flow_", "").replace(".csv", "")
        path = os.path.join(intraday_dir, f)
        stocks_ts = {}
        with open(path, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                code = r.get("代码", "")
                if not code: continue
                data = {
                    "f2": _tof(r.get("最新价")), "f3": _tof(r.get("涨跌幅")),
                    "f62": _tof(r.get("主力净流入")), "f184": _tof(r.get("主力占比")),
                    "f8": _tof(r.get("换手率")), "f10": _tof(r.get("量比")),
                }
                stocks_ts[code] = data
                time_series[code][ts] = data
        snapshots.append({"ts": ts, "stocks": stocks_ts})

    n_multi = sum(1 for v in time_series.values() if len(v) >= 3)
    print(f"  快照: {len(snapshots)}个 [{snapshots[0]['ts']}→{snapshots[-1]['ts']}]"
          f"{' 截止'+cutoff if cutoff else ''}  多快照≥3: {n_multi}只")
    return snapshots, dict(time_series)


def _load_sector_snapshots(date_str, cutoff=None):
    """加载全天行业+概念板块流快照，返回时序数据
    {sector_name: {ts: {flow, rank}}, ...}
    """
    intraday_dir = os.path.join(RESEARCH_ROOT, date_str, "intraday")
    if not os.path.isdir(intraday_dir): return {}, {}

    def _load_sector_type(prefix):
        files = sorted([f for f in os.listdir(intraday_dir)
                        if f.startswith(prefix) and f.endswith(".csv")])
        if cutoff:
            files = [f for f in files if f.replace(prefix, "").replace(".csv", "") <= cutoff]
        if len(files) < 2: return {}
        snapshots = []
        for f in files:
            ts = f.replace(prefix, "").replace(".csv", "")
            flows = {}
            with open(os.path.join(intraday_dir, f), encoding="utf-8-sig") as fh:
                for r in csv.DictReader(fh):
                    name = r.get("名称", "")
                    flow = _tof(r.get("主力净流入"))
                    if name: flows[name] = flow
            snapshots.append((ts, flows))
        # 计算每个时点的排名
        result = defaultdict(dict)
        for ts, flows in snapshots:
            ranked = sorted(flows.items(), key=lambda x: -x[1])
            for rank, (name, flow) in enumerate(ranked):
                if name not in result: result[name] = {}
                result[name][ts] = {"flow": flow, "rank": rank + 1}
        return dict(result)

    industry_ts = _load_sector_type("industry_flow_")
    concept_ts = _load_sector_type("concept_flow_")
    n_ind = sum(1 for v in industry_ts.values() if len(v) >= 2)
    n_con = sum(1 for v in concept_ts.values() if len(v) >= 2)
    print(f"  板块快照: 行业{n_ind}个有轨迹, 概念{n_con}个有轨迹")
    # 同时提取最新静态流: {name: flow} (替代 _load_sector_flows)
    sector_static = {}
    concept_static = {}
    for name, ts_data in industry_ts.items():
        if ts_data:
            latest_ts = max(ts_data.keys())
            sector_static[name] = ts_data[latest_ts]["flow"]
    for name, ts_data in concept_ts.items():
        if ts_data:
            latest_ts = max(ts_data.keys())
            concept_static[name] = ts_data[latest_ts]["flow"]
    return industry_ts, concept_ts, sector_static, concept_static


def _multi_snapshot_factors(code, time_series, all_f62_per_ts):
    """多快照因子: (flow_stability, intraday_accel, rank_trajectory, vwap_pos)"""
    if code not in time_series or len(time_series[code]) < 2:
        return 0.5, 0.5, 0.5, 0.5

    ts_data = time_series[code]
    sorted_ts = sorted(ts_data.keys())
    n = len(sorted_ts)

    f62_seq = [ts_data[ts]["f62"] for ts in sorted_ts]
    f184_seq = [ts_data[ts]["f184"] for ts in sorted_ts]
    price_seq = [ts_data[ts]["f2"] for ts in sorted_ts]

    # 1. flow_stability (3%): 低波动=机构持续, 高波动=游资
    if n >= 3:
        mean_f62 = statistics.mean(f62_seq)
        std_f62 = statistics.stdev(f62_seq) if n > 2 else abs(f62_seq[-1] - f62_seq[0]) / 2
        flow_stab = 1.0 - min(1.0, std_f62 / max(abs(mean_f62), 1))
    else:
        flow_stab = 0.5

    # 2. intraday_accel (3%): 后半段 vs 前半段
    if n >= 4:
        mid = n // 2
        first_half = statistics.mean(f62_seq[:mid])
        second_half = statistics.mean(f62_seq[mid:])
        denom = max(abs(first_half), 1)
        accel_raw = (second_half - first_half) / denom
        intraday_accel = 0.5 + max(-0.5, min(0.5, accel_raw * 2))
    else:
        intraday_accel = 0.5

    # 3. rank_trajectory (2%): 全市场f62排名的改善
    if n >= 4 and all_f62_per_ts:
        rank_seq = []
        for ts in sorted_ts:
            if ts in all_f62_per_ts and all_f62_per_ts[ts]:
                rank = sum(1 for v in all_f62_per_ts[ts] if v > f62_seq[sorted_ts.index(ts)]) / len(all_f62_per_ts[ts])
                rank_seq.append(rank)
        if len(rank_seq) >= 4:
            # 排名从高到低 = 好, 用 1-rank 转换
            rank_improve = rank_seq[0] - rank_seq[-1]  # 正=改善
            rank_traj = 0.5 + max(-0.5, min(0.5, rank_improve * 3))
        else:
            rank_traj = 0.5
    else:
        rank_traj = 0.5

    # 4. vwap_position (2%): 最新价 vs 日内均价
    if n >= 2 and price_seq:
        vwap = sum(price_seq) / len(price_seq)
        latest = price_seq[-1]
        if vwap > 0:
            vwap_dev = latest / vwap
            # <1=低估(加分), >1.03=追高(减分)
            if vwap_dev < 0.98: vwap_pos = 0.70
            elif vwap_dev < 1.00: vwap_pos = 0.60
            elif vwap_dev <= 1.02: vwap_pos = 0.50
            elif vwap_dev <= 1.05: vwap_pos = 0.40
            else: vwap_pos = 0.30
        else:
            vwap_pos = 0.5
    else:
        vwap_pos = 0.5

    return round(flow_stab, 3), round(intraday_accel, 3), round(rank_traj, 3), round(vwap_pos, 3)


def _load_prev_scores(date_str):
    """加载前一日评分，返回 {code: score}"""
    date_dirs = sorted([
        d for d in os.listdir(RESEARCH_ROOT)
        if os.path.isdir(os.path.join(RESEARCH_ROOT, d)) and d.isdigit() and d < date_str
    ], reverse=True)
    for prev_date in date_dirs:
        path = os.path.join(RESEARCH_ROOT, prev_date, "scores.csv")
        if os.path.exists(path):
            prev = {}
            with open(path, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    prev[r["代码"]] = float(r["综合得分"])
            if prev:
                print(f"  前次评分: {prev_date} ({len(prev)}只)")
                return prev
    return {}


def score_all_stocks(date_str=None, snapshot_cutoff=None):
    """全市场评分，返回 [{...}, ...] + 保存 scores.csv"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    mode_label = f"14:30买入" if snapshot_cutoff else "收盘评分"
    print(f"全市场评分 [{date_str}] {mode_label}")

    # ── 加载多快照数据 ──
    snapshots, time_series = _load_all_snapshots(date_str, snapshot_cutoff)
    if not snapshots:
        print("  ✗ 无盘中数据")
        return []

    # 最后一个快照的完整CSV数据（保留所有字段）
    latest_file = [f for f in os.listdir(os.path.join(RESEARCH_ROOT, date_str, "intraday"))
                   if f.startswith("fund_flow_") and f.endswith(".csv")]
    if snapshot_cutoff:
        latest_file = [f for f in latest_file if f.replace("fund_flow_","").replace(".csv","") <= snapshot_cutoff]
    latest_file = sorted(latest_file)[-1] if latest_file else None

    stocks = []
    if latest_file:
        with open(os.path.join(RESEARCH_ROOT, date_str, "intraday", latest_file), encoding="utf-8-sig") as fh:
            stocks = list(csv.DictReader(fh))

    # 预计算每个快照的f62数组(用于排名轨迹)
    all_f62_per_ts = {}
    for sn in snapshots:
        all_f62_per_ts[sn["ts"]] = [d["f62"] for d in sn["stocks"].values()]

    sector_trajectory, concept_trajectory, sector_flows, concept_flows = _load_sector_snapshots(date_str, snapshot_cutoff)
    print(f"  个股: {len(stocks)} 只, API行业: {len(sector_flows)} 个")

    # ── 方案B: 用f100自建行业分类(与个股行业名100%匹配) ──
    f100_sector_flows = defaultdict(float)
    for s in stocks:
        ind = s.get("行业", "")
        if ind:
            f100_sector_flows[ind] += _tof(s.get("主力净流入"))
    # 合并API行业流(补充f100没有的)和f100自建流
    merged_sector_flows = dict(sector_flows)  # API数据
    for ind, flow in f100_sector_flows.items():
        if ind not in merged_sector_flows or abs(flow) > abs(merged_sector_flows.get(ind, 0)):
            merged_sector_flows[ind] = flow  # f100数据优先
    print(f"  合并行业: {len(merged_sector_flows)} 个 (API{sector_flows and len(sector_flows)} + f100自建{len(f100_sector_flows)})")

    # ── 加载前一日评分 (用于得分动量) ──
    prev_scores = _load_prev_scores(date_str)

    # ── 轻量过滤（只去掉明显异常值，保留研究样本）──
    stocks = [s for s in stocks if _tof(s.get("最新价")) >= 2.0]      # 去掉仙股
    stocks = [s for s in stocks if _tof(s.get("总市值")) / 1e8 >= 10]  # 去掉微型壳
    print(f"  全量评分: {len(stocks)} 只 (价格≥2, 市值≥10亿)")

    # 预计算百分位数组 (过滤后)
    f62_vals = [_tof(s.get("主力净流入")) for s in stocks]
    f184_vals = [_tof(s.get("主力占比")) for s in stocks]
    f168_vals = [_tof(s.get("融资净买入")) for s in stocks]
    f169_vals = [_tof(s.get("融资净买入占比")) for s in stocks]     # 融资买入占比
    f170_vals = [_tof(s.get("融券净卖出")) for s in stocks]          # 融券净卖出

    # 行业内排名: 按行业分组
    sector_groups = defaultdict(list)
    for s in stocks:
        sector_groups[s.get("行业", "其他")].append(_tof(s.get("主力净流入")))

    # ── 构建价格历史 ──
    # 从已有 intraday CSV 中提取每日收盘价（跨日累积）
    price_hist = _build_price_history(date_str, stocks)
    # ── 逐只评分 ──
    results = []
    for s in stocks:
        code = s.get("代码", "")
        name = s.get("名称", "")
        f2 = _tof(s.get("最新价"))
        f3 = _tof(s.get("涨跌幅"))
        f8_val = _tof(s.get("换手率"))
        f10_val = _tof(s.get("量比"))
        f20 = _tof(s.get("总市值"))
        f62_val = _tof(s.get("主力净流入"))
        f184_val = _tof(s.get("主力占比"))
        f66_val = _tof(s.get("超大单净流入"))
        f69_val = _tof(s.get("超大单占比"))
        f87_val = _tof(s.get("小单占比"))
        f168_val = _tof(s.get("融资净买入"))
        f169_val = _tof(s.get("融资净买入占比"))                     # 融资买入占比
        f170_val = _tof(s.get("融券净卖出"))                         # 融券净卖出
        f172_val = _tof(s.get("融券卖出量"))                         # 融券卖出量
        f17_val = _tof(s.get("开盘"))                                # 开盘价
        f18_val = _tof(s.get("昨收"))                                # 昨收价
        f165_val = _tof(s.get("5日主力占比"))
        f175_val = _tof(s.get("10日主力占比"))
        f164_val = _tof(s.get("5日主力净流入"))
        f174_val = _tof(s.get("10日主力净流入"))
        industry = s.get("行业", "")

        sub = {}
        comp_sigs = []   # 综合得分信号
        early_sigs = []  # 启动得分信号

        # ── capital (19%) ── 多快照: 用全天f62均值替代最后快照f62
        if code in time_series and len(time_series[code]) >= 2:
            ts_f62_vals = [d["f62"] for d in time_series[code].values()]
            avg_f62 = statistics.mean(ts_f62_vals)
        else:
            avg_f62 = f62_val
        cap = _pct_rank(f62_vals, avg_f62) * 0.60
        cap += _pct_rank(f184_vals, f184_val) * 0.25
        cap += (_pct_rank([_tof(x.get("超大单占比")) for x in stocks], f69_val)) * 0.15
        sub["capital"] = round(cap, 3)

        # ── start_signal (13%) — 流加速度 + 超大单质量 ──
        # 去f62冗余: 专注5日vs10日流加速 + 超大单/大单占比
        accel_score = 0.5
        if f164_val != 0 and f174_val != 0:
            # 5日流 vs 10日流 方向一致性
            if f164_val > 0 and f174_val > 0:
                accel_score = min(1.0, f164_val / max(f174_val, 1) / 3.0)
            elif f164_val > 0:  # 短期转正
                accel_score = 0.65
            else:
                accel_score = 0.35
        # 超大单质量: f66/f62 比例
        big_order_quality = abs(f66_val) / max(abs(f62_val), 1) if f62_val != 0 else 0.5
        start = accel_score * 0.55 + min(1.0, big_order_quality) * 0.45
        sub["start_signal"] = round(start, 3)

        # ── trend (10%) ──
        tr = _range_score(f10_val, 1.5, 4.0, 0.8, 8.0) * 0.15
        tr += _range_score(f8_val, 5.0, 18.0, 2.0, 25.0) * 0.25
        tr += _range_score(f3, 2.5, 7.0, -2.0, 9.5) * 0.25
        # 中期动量 from price history
        ph = price_hist.get(code, {})
        closes_hist = ph.get("closes", [])
        if closes_hist and len(closes_hist) >= 10:
            ret_10d = (closes_hist[0] - closes_hist[9]) / closes_hist[9] * 100 if closes_hist[9] > 0 else 0
            tr += _range_score(ret_10d, 3, 15, -5, 25) * 0.55 * 0.20
        if closes_hist and len(closes_hist) >= 20:
            ret_20d = (closes_hist[0] - closes_hist[19]) / closes_hist[19] * 100 if closes_hist[19] > 0 else 0
            tr += _range_score(ret_20d, 5, 25, -5, 35) * 0.45 * 0.20
        if not closes_hist or len(closes_hist) < 10:
            tr += 0.5 * 0.20
        tr += 0.5 * 0.15  # short_trend default
        sub["trend"] = round(tr, 3)

        # ── sector (5%) ── 行业排名70% + 概念共振30%  (方案B: f100自建分类)
        if merged_sector_flows and industry:
            all_sf = list(merged_sector_flows.values())
            industry_flow = merged_sector_flows.get(industry, 0)
            sec = _pct_rank(all_sf, industry_flow) if all_sf else 0.5
        else:
            sec = 0.5
        # 概念共振: 行业名可能在概念流中也存在(如"半导体"既是行业也是概念)
        s_concept = 0.5
        if concept_flows and industry:
            all_cf = list(concept_flows.values())
            if industry in concept_flows:
                s_concept = _pct_rank(all_cf, concept_flows[industry])
            elif all_cf:
                # 无直接匹配: 用概念流均值百分位作为市场概念热度
                s_concept = _pct_rank(all_cf, statistics.mean(all_cf))
        sub["sector"] = round(sec * 0.70 + s_concept * 0.30, 3)

        # ── position (7%) ── 连续线性插值
        ph = price_hist.get(code, {})
        closes_hist = ph.get("closes", [f2])
        high_60d = ph.get("high_60", f2)
        low_60d = ph.get("low_60", f2)
        if high_60d > low_60d:
            pos = (f2 - low_60d) / (high_60d - low_60d)
            pos = max(0.0, min(1.0, pos))
            # 连续分段线性: 极低位0→0.25, 低位0.25→0.80, 中位0.60→0.45, 高位0.85→0.15
            if pos < 0.10:
                p_score = 0.15 + (pos / 0.10) * 0.10  # 0.15→0.25
            elif pos < 0.25:
                p_score = 0.25 + ((pos - 0.10) / 0.15) * 0.55  # 0.25→0.80
            elif pos < 0.40:
                p_score = 0.80 + ((pos - 0.25) / 0.15) * (-0.25)  # 0.80→0.55
            elif pos < 0.65:
                p_score = 0.55 + ((pos - 0.40) / 0.25) * (-0.10)  # 0.55→0.45
            elif pos < 0.85:
                p_score = 0.45 + ((pos - 0.65) / 0.20) * (-0.10)  # 0.45→0.35
            else:
                p_score = 0.35 + ((pos - 0.85) / 0.15) * (-0.20)  # 0.35→0.15
        else:
            p_score = 0.5
        sub["position"] = round(max(0.0, min(1.0, p_score)), 3)

        # ── analyst (5%) — 简化: 无数据给中性 ──
        sub["analyst"] = 0.5

        # ── multiday (6%) — 5d/10d累计流 + 方向一致性 ──
        # 去f62冗余: 只看多日累计，不重复今日
        md = 0.5
        # 5日方向
        f164_pct = _pct_rank([_tof(x.get("5日主力净流入")) for x in stocks], f164_val)
        # 10日方向
        f174_pct = _pct_rank([_tof(x.get("10日主力净流入")) for x in stocks], f174_val)
        # 持续正流检测
        if f164_val > 0 and f174_val > 0:
            md = f164_pct * 0.45 + f174_pct * 0.30 + 0.25  # 双正 +0.25 bonus
        elif f164_val > 0:
            md = f164_pct * 0.60 + 0.10  # 仅5日正
        else:
            md = f164_pct * 0.40 + f174_pct * 0.30
        sub["multiday"] = round(md, 3)

        # ── technical (5%) ──
        ph = price_hist.get(code, {})
        closes_hist = ph.get("closes", [])
        if closes_hist and len(closes_hist) >= 20:
            ma5 = sum(closes_hist[:5]) / 5
            ma10 = sum(closes_hist[:10]) / 10
            ma20 = sum(closes_hist[:20]) / 20
            align = sum([ma5 > ma10, ma10 > ma20, ma5 > ma20, f2 > ma5]) / 4
            # 突破检测
            if len(closes_hist) >= 60:
                h60 = max(closes_hist[:60])
                breakout = 1.0 if f2 > h60 * 0.98 else 0.4
            elif len(closes_hist) >= 20:
                h20 = max(closes_hist[:20])
                breakout = 0.8 if f2 > h20 * 0.98 else 0.4
            else:
                breakout = 0.4
            tech = align * 0.60 + breakout * 0.40
        else:
            tech = 0.5
        sub["technical"] = round(tech, 3)

        # ── intra_sector (4%) ──
        if industry in sector_groups:
            sub["intra_sector"] = round(_pct_rank(sector_groups[industry], f62_val), 3)
        else:
            sub["intra_sector"] = 0.5

        # ── margin_net (3%) ──
        sub["margin_net"] = round(_pct_rank(f168_vals, f168_val), 3)

        # ── flow_accel (2%) ──
        if f164_val > 0 and f174_val > 0 and abs(f174_val) > 1:
            accel = min(2.0, max(0.3, f164_val / max(abs(f174_val), 1)))
        else:
            accel = 1.0
        sub["flow_accel"] = round(_range_score(accel, 1.3, 2.5, 0.3, 4.0), 3)

        # ── 多快照因子 (4个) ──
        flow_stab, intraday_accel, rank_traj, vwap_pos = _multi_snapshot_factors(
            code, time_series, all_f62_per_ts
        )
        sub["flow_stability"] = flow_stab
        sub["intraday_accel"] = intraday_accel
        sub["rank_trajectory"] = rank_traj
        sub["vwap_position"] = vwap_pos

        # ── 板块日内轨迹 ──
        sub["sector_trajectory"] = 0.5
        if industry and industry in sector_trajectory:
            traj = sector_trajectory[industry]
            ranks = [d["rank"] for d in traj.values()]
            flows = [d["flow"] for d in traj.values()]
            if len(ranks) >= 3:
                # 排名改善: 从高排名到低排名(数字变小=变好)
                rank_improve = ranks[0] - ranks[-1]  # 正=改善
                rank_score = 0.5 + max(-0.3, min(0.3, rank_improve / max(len(traj), 1) * 5))
                # 流入加速
                if len(flows) >= 4:
                    mid = len(flows) // 2
                    accel = statistics.mean(flows[mid:]) - statistics.mean(flows[:mid])
                    accel_score = 0.5 + max(-0.3, min(0.3, accel / max(abs(statistics.mean(flows)), 1) * 2))
                else:
                    accel_score = 0.5
                sub["sector_trajectory"] = round(rank_score * 0.50 + accel_score * 0.50, 3)

        # ── 综合得分 ──
        total = sum(sub.get(k, 0.5) * WEIGHTS.get(k, 0) for k in WEIGHTS)
        for k in WEIGHTS:
            if k not in sub:
                total += 0.5 * WEIGHTS[k]

        # ── 启动得分（刚启动检测: 降资金权重，升启动/位置/趋势）──
        early = sum(sub.get(k, 0.5) * EARLY_WEIGHTS.get(k, 0) for k in EARLY_WEIGHTS)
        for k in EARLY_WEIGHTS:
            if k not in sub:
                early += 0.5 * EARLY_WEIGHTS[k]

        # ── P32: 占比趋势 ──
        ratio_score = 0.0
        if f165_val > 0 and f184_val > f165_val * 1.5 and f165_val > f175_val and 5 <= f184_val <= 12:
            ratio_score = 0.05
            comp_sigs.append("P32_ratio_accel"); early_sigs.append("E5_ratio_early")
        elif f184_val > 10 and f165_val < f184_val * 0.3:
            ratio_score = -0.05
            comp_sigs.append("P32_pump_risk")
        elif f184_val > 12 and f165_val > 6:
            ratio_score = -0.04
            comp_sigs.append("P32_extreme")
        sub["ratio_trend"] = round(ratio_score, 3)
        total += ratio_score
        early += ratio_score  # 启动得分也受P32影响

        # ── P33: 融资买入占比 (融资质量) ──
        margin_quality = 0.0
        if f169_val > 8:
            margin_quality = 0.03; comp_sigs.append("P33_margin_strong")
        elif f169_val > 3:
            margin_quality = 0.01; comp_sigs.append("P33_margin_moderate")
        elif f169_val < -5:
            margin_quality = -0.03; comp_sigs.append("P33_margin_weak")
        total += margin_quality
        early += margin_quality

        # ── P34: 开盘缺口 (隔夜信号) ──
        gap_signal = 0.0
        if f18_val > 0 and f17_val > 0:
            gap = (f17_val - f18_val) / f18_val * 100  # 开盘缺口%
            if gap > 2 and f3 > 2:
                gap_signal = 0.04; comp_sigs.append("P34_gap_strong")       # 回测+0.76%最强信号
            elif gap < -2 and f3 > 1:
                gap_signal = 0.01; comp_sigs.append("P34_gap_reverse")      # 回测-0.72%降权
            elif gap > 3 and f3 < 0:
                gap_signal = -0.04; comp_sigs.append("P34_gap_trap")
        total += gap_signal
        early += gap_signal

        # ── P35: 融券压力 (做空检测) ──
        short_signal = 0.0
        if f170_val < -1_0000_0000:
            short_signal = 0.02; comp_sigs.append("P35_short_cover")
        if f170_val > 3_0000_0000:
            short_signal = -0.04; comp_sigs.append("P35_short_pressure")
        elif f170_val > 1_0000_0000:
            short_signal = -0.02; comp_sigs.append("P35_short_moderate")
        if f172_val > 0 and f62_val > 0:
            short_ratio = abs(f170_val) / max(abs(f62_val), 1)
            if short_ratio > 3:
                short_signal -= 0.03; comp_sigs.append("P35_short_heavy")
        total += short_signal
        early += short_signal

        # ── 风险惩罚（综合+启动均适用）──
        mcap_yi = f20 / 1e8
        penalty = 0.0
        if f8_val > 13 and cap < 0.75: penalty -= 0.06; comp_sigs.append("P29_high_turnover")
        if f8_val < 2.0: penalty -= 0.04; comp_sigs.append("P_low_liquidity")
        if f10_val < 1.0: penalty -= 0.03; comp_sigs.append("P_low_vol_ratio")
        if mcap_yi < 30: penalty -= 0.04; comp_sigs.append("P_small_cap")
        if f87_val > 30 and f3 < 3: penalty -= 0.08; comp_sigs.append("P6_retail")
        if f2 > 200: penalty -= 0.02; comp_sigs.append("P_high_price")
        total += penalty
        early += penalty

        total = max(0.0, min(1.0, total))
        early = max(0.0, min(1.0, early))

        # ── 启动专属信号 ──
        # E1: 低位启动 (位置<0.25 + 启动因子>0.5)
        if p_score < 0.55 and sub.get("start_signal", 0.5) > 0.5:
            early_sigs.append("E1_low_start")
        # E2: 温和启动 (资金0.4-0.7 + 启动因子>0.6)
        if 0.4 < cap < 0.7 and sub.get("start_signal", 0.5) > 0.6:
            early_sigs.append("E2_moderate_start")
        # E3: 强势启动 (启动因子>0.8 + 资金>0.5)
        if sub.get("start_signal", 0.5) > 0.8 and cap > 0.5:
            early_sigs.append("E3_strong_start")
        # E4: 缺口启动 (P34 + 位置<0.4)
        if ("P34_gap_strong" in comp_sigs or "P34_gap_reverse" in comp_sigs) and p_score < 0.55:
            early_sigs.append("E4_gap_start")
        # E6: 逼空启动 (P35回补 + 资金>0.3)
        if "P35_short_cover" in comp_sigs and cap > 0.3:
            early_sigs.append("E6_short_squeeze")

        # ── P36: 全维度过热保护 (回测: P32+P36叠加→-1.45%, 互斥处理) ──
        if cap > 0.85 and sub.get("trend", 0.5) > 0.7 and sub.get("multiday", 0.5) > 0.85:
            total -= 0.06; comp_sigs.append("P36_overheat")
            early -= 0.06
            # P32_accel与P36互斥: 过热股不享受占比加速加分
            if ratio_score > 0:
                total -= ratio_score; early -= ratio_score  # 撤回P32加分

        # ── P37: 得分动量 (改善>0.05:+0.03, 恶化<-0.05:-0.03) ──
        if code in prev_scores:
            score_change = total - prev_scores[code]
            if score_change > 0.05:
                total += 0.03; comp_sigs.append("P37_momentum_up")
                early += 0.03
            elif score_change < -0.05:
                total -= 0.03; comp_sigs.append("P37_momentum_down")
                early -= 0.03

        results.append({
            "代码": code, "名称": name, "最新价": f2, "行业": industry,
            "综合得分": round(total, 4), "启动得分": round(early, 4),
            "资金得分": sub.get("capital", 0.5), "趋势得分": sub.get("trend", 0.5),
            "启动因子": round(sub.get("start_signal", 0.5), 3), "板块得分": sub.get("sector", 0.5),
            "位置得分": sub.get("position", 0.5), "分析师得分": sub.get("analyst", 0.5),
            "多日得分": sub.get("multiday", 0.5), "技术面得分": sub.get("technical", 0.5),
            "行业内得分": sub.get("intra_sector", 0.5), "融资得分": sub.get("margin_net", 0.5),
            "加速度得分": sub.get("flow_accel", 0.5), "占比趋势得分": ratio_score,
            "日内稳定": sub.get("flow_stability", 0.5), "日内加速": sub.get("intraday_accel", 0.5),
            "排名轨迹": sub.get("rank_trajectory", 0.5), "VWAP位置": sub.get("vwap_position", 0.5),
            "板块轨迹": sub.get("sector_trajectory", 0.5),
            "涨跌幅": f3, "换手率": f8_val, "量比": f10_val, "总市值": mcap_yi,
            "综合信号": ",".join(comp_sigs),
            "综合信号说明": "; ".join(COMPOSITE_SIGNALS[s] for s in comp_sigs if s in COMPOSITE_SIGNALS),
            "启动信号": ",".join(early_sigs),
            "启动信号说明": "; ".join(EARLY_SIGNALS[s] for s in early_sigs if s in EARLY_SIGNALS),
        })

    # ── 排序 + 保存 CSV ──
    results.sort(key=lambda x: -x["综合得分"])
    csv_path = os.path.join(RESEARCH_ROOT, date_str, "scores.csv")
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SCORE_HEADERS, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)

    # 统计
    top50 = results[:50]
    avg_score = statistics.mean(r["综合得分"] for r in results)
    print(f"  ✓ scores.csv ({len(results)} 只, 均分{avg_score:.3f}, "
          f"Top50均分{statistics.mean(r['综合得分'] for r in top50):.3f})")

    # 因子说明
    print(f"\n  评分因子说明:")
    for fname, fdesc in FACTOR_INFO.items():
        print(f"    {fname}: {fdesc}")
    print(f"\n  综合信号说明:")
    for sig, sdesc in COMPOSITE_SIGNALS.items():
        print(f"    {sig}: {sdesc}")
    print(f"\n  启动信号说明:")
    for sig, sdesc in EARLY_SIGNALS.items():
        print(f"    {sig}: {sdesc}")
    return results


def score_sectors(date_str=None, snapshot_cutoff=None):
    """行业/概念板块评分 — 判断哪些板块资金在流入"""
    if date_str is None:
        date_str = datetime.now(BJS_TZ).strftime("%Y%m%d")

    mode_label = f"14:30" if snapshot_cutoff else "收盘"
    print(f"板块评分 [{date_str}] {mode_label}")

    sector_traj, concept_traj, sector_static, concept_static = _load_sector_snapshots(date_str, snapshot_cutoff)

    def _score_one_sector(name, traj, static_flow):
        """板块综合评分 0~1 (6维度: 排名+趋势+持续性+加速度+集中度+稳定性)"""
        if not traj or len(traj) < 2:
            return 0.5

        ranks = [d["rank"] for d in traj.values()]
        flows = [d["flow"] for d in traj.values()]
        n = len(ranks)
        total_sectors = max(len(traj), 10)

        # 1. 最新排名 (20%) — 排名越小越好
        rank_score = 1.0 - min(1.0, (ranks[-1] - 1) / total_sectors)

        # 2. 排名趋势 (25%) — 排名持续改善=主力持续关注
        if n >= 3:
            # 斜率 + 最新方向
            xs = list(range(n)); ys = ranks
            slope = (n * sum(x*y for x,y in zip(xs,ys)) - sum(xs)*sum(ys)) / (n*sum(x*x for x in xs) - sum(xs)**2) if n > 2 else 0
            # 负斜率 = 排名改善
            trend_raw = -slope / max(total_sectors/5, 1)
            # 最近2次的变化方向
            recent_dir = 1 if ranks[-1] < ranks[-2] else (-1 if ranks[-1] > ranks[-2] else 0)
            trend_score = 0.5 + max(-0.5, min(0.5, trend_raw * 3 + recent_dir * 0.15))
        else:
            trend_score = 0.5

        # 3. 资金持续性 (20%) — 连续正流入快照占比
        pos_ratio = sum(1 for f in flows if f > 0) / n
        # 最近连续正流入次数
        conseq = 0
        for f in reversed(flows):
            if f > 0: conseq += 1
            else: break
        persist_score = pos_ratio * 0.5 + min(1.0, conseq / n) * 0.5

        # 4. 流入加速度 (15%) — 后半vs前半, 正=资金加速涌入
        if n >= 4:
            mid = n // 2
            first_avg = statistics.mean(flows[:mid])
            second_avg = statistics.mean(flows[mid:])
            denom = max(abs(first_avg), 1e8)
            accel = (second_avg - first_avg) / denom
            accel_score = 0.5 + max(-0.5, min(0.5, accel * 5))
        else:
            accel_score = 0.5

        # 5. 集中度 (10%) — 流入占全市场比例
        all_flows = [d["flow"] for t in (sector_traj | concept_traj).values() for d in t.values()]
        total_flow = sum(f for f in all_flows if f > 0)
        sector_flow = flows[-1] if flows[-1] > 0 else 0
        concentration = sector_flow / max(total_flow, 1e8)
        conc_score = min(1.0, concentration * 50)  # 占2%以上=满分

        # 6. 排名稳定性 (10%) — 低波动=机构主导
        if n >= 3:
            std_rank = statistics.stdev(ranks) if n > 2 else 0
            stability = 1.0 - min(1.0, std_rank / max(total_sectors/2, 3))
        else:
            stability = 0.5

        return round(
            rank_score * 0.20 + trend_score * 0.25 + persist_score * 0.20 +
            accel_score * 0.15 + conc_score * 0.10 + stability * 0.10, 3
        )

    # 评分
    results = []
    for name, traj in {**sector_traj, **concept_traj}.items():
        static_flow = sector_static.get(name) or concept_static.get(name, 0)
        s = _score_one_sector(name, traj, static_flow)
        is_concept = name in concept_traj
        # 计算详细指标
        ranks = [d["rank"] for d in traj.values()] if traj else []
        flows = [d["flow"] for d in traj.values()] if traj else []
        n = len(ranks)
        pos_ratio = sum(1 for f in flows if f > 0) / max(n, 1)
        rank_change = ranks[0] - ranks[-1] if n >= 2 else 0

        results.append({
            "名称": name, "类型": "概念" if is_concept else "行业",
            "得分": s, "快照数": n,
            "最新排名": ranks[-1] if ranks else 0,
            "最新流入(亿)": round(flows[-1] / 1e8, 1) if flows else 0,
            "排名变化": rank_change,
            "正流占比": round(pos_ratio, 2),
        })

    # ── 加载前日板块得分用于跨日对比 ──
    prev_sector = {}
    date_dirs = sorted([d for d in os.listdir(RESEARCH_ROOT) if os.path.isdir(os.path.join(RESEARCH_ROOT, d)) and d.isdigit() and d < date_str], reverse=True)
    if date_dirs:
        prev_path = os.path.join(RESEARCH_ROOT, date_dirs[0], "sector_scores.csv")
        if os.path.exists(prev_path):
            with open(prev_path, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    prev_sector[r["名称"]] = {
                        "得分": float(r["得分"]), "排名": int(r["最新排名"]),
                        "流入": float(r.get("最新流入(亿)", 0) or 0),
                    }

    # 添加跨日对比列
    for r in results:
        name = r["名称"]
        if name in prev_sector:
            p = prev_sector[name]
            r["昨日排名"] = p["排名"]
            r["排名跨日变化"] = p["排名"] - r["最新排名"]
            r["昨日流入(亿)"] = round(p["流入"], 1)
            r["流入跨日变化(亿)"] = round(r["最新流入(亿)"] - p["流入"], 1)
        else:
            r["昨日排名"] = 0; r["排名跨日变化"] = 0
            r["昨日流入(亿)"] = 0; r["流入跨日变化(亿)"] = 0

    results.sort(key=lambda x: -x["得分"])

    # 保存
    csv_path = os.path.join(RESEARCH_ROOT, date_str, "sector_scores.csv")
    fields = ["名称","类型","得分","快照数","最新排名","昨日排名","排名跨日变化","最新流入(亿)","昨日流入(亿)","流入跨日变化(亿)","排名变化","正流占比"]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)

    # 输出
    print(f"\n{'='*65}")
    print(f"  板块资金流评分 Top 20")
    if prev_sector:
        print(f"  对比前日: {date_dirs[0]}")
    print(f"{'='*65}")
    for i, r in enumerate(results[:20]):
        bar = "█" * int(r["得分"] * 20)
        trend = "↑" if r["排名变化"] > 0 else ("↓" if r["排名变化"] < 0 else "→")
        cross = ""
        if r["排名跨日变化"] > 3: cross = " 🔥新进"
        elif r["排名跨日变化"] > 0: cross = " ↗"
        elif r["排名跨日变化"] < -3: cross = " ↘退潮"
        elif r["排名跨日变化"] < 0: cross = " ↓"
        flow_diff = r.get("流入跨日变化(亿)", 0)
        flow_str = f"资金{flow_diff:+.1f}亿" if flow_diff != 0 else ""
        print(f"  {i+1:>2}. {r['名称']:<10s} {r['类型']} {r['得分']:.3f} "
              f"今#{r['最新排名']}(昨#{r['昨日排名'] or '新'}){trend} 流入{r['最新流入(亿)']:+.1f}亿 "
              f"{flow_str} 正流{r['正流占比']:.0%}{cross} {bar}")

    # 跨日轮动信号
    if prev_sector:
        new_top10 = [r for r in results[:20] if r["排名跨日变化"] > 5]
        fading = [r for r in results if r["排名跨日变化"] < -10]
        if new_top10:
            print(f"\n  🔥 新进Top20: {', '.join(r['名称'] for r in new_top10[:5])}")
        if fading:
            print(f"  ↘ 退潮板块: {', '.join(r['名称'] for r in fading[:5])}")

    print(f"\n  ✓ {csv_path} ({len(results)} 个板块)")
    return results


if __name__ == "__main__":
    import sys
    if "--sectors" in sys.argv:
        score_sectors()
    else:
        score_all_stocks()
