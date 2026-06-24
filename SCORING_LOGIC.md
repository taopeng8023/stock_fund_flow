# 盘中全市场评分逻辑

> 文件: `daily_pipeline/score.py`
> 版本: v3 — 19 因子 + 体制感知 + 黑天鹅联动
> 最后更新: 2026-06-24

---

## 一、架构概览

```
research_data/<date>/intraday/fund_flow_*.csv    → 个股盘中快照
research_data/<date>/intraday/industry_flow_*.csv → 行业板块快照
research_data/<date>/intraday/concept_flow_*.csv  → 概念板块快照
                                                  │
                                                  ▼
                                    score_all_stocks(date_str)
                                                  │
                           ┌──────────────────────┼──────────────────────┐
                           │                      │                      │
                    市场体制检测            黑天鹅检测              19因子评分
                    bull/bear/range       Level 0-3 强制防御         加权求和
                           │                      │                      │
                           └──────────────────────┴──────────────────────┘
                                                  │
                                                  ▼
                                    research_data/<date>/scores.csv
                                    (全市场 ~5400 只股票 × 31 列)
```

---

## 二、数据加载

### 2.1 个股快照
- 来源: `research_data/<date>/intraday/fund_flow_*.csv`
- 取**最新**时间戳的快照（最接近收盘价）
- 字段: 代码、名称、最新价、涨跌幅、主力净流入、主力占比、超大单净流入、超大单占比、换手率、量比、总市值、行业、融资净买入、融资净买入占比、融券净卖出、融券卖出量、开盘价、昨收价、5日主力净流入、10日主力净流入、5日主力占比、10日主力占比

### 2.2 价格历史
```python
_build_price_history(date_str, stocks)
```
- 从当前日快照中提取 f2（最新价）作为第 0 天
- 回溯最多 60 个历史交易日，从 `research_data/<date>/intraday/` 中的 fund_flow CSV 提取收盘价
- 同时累积 60 日最高价/最低价（用于位置因子）
- 输出: `{code: {"closes": [c0, c1, ...], "high_60": h, "low_60": l}}`

### 2.3 多快照时序
```python
_load_all_snapshots(date_str)
```
- 加载全天所有 fund_flow 快照（约 8 个时间点）
- 构建 `time_series: {code: {ts: {f2, f62, f184, f8, f3}}}`
- 用于日内轨迹因子（flow_stability、intraday_accel、rank_trajectory、vwap_position）

### 2.4 板块快照
```python
_load_sector_snapshots(date_str)
```
- 加载全天 industry_flow_*.csv 和 concept_flow_*.csv
- 构建 `{sector_name: {ts: {flow, rank}}}`
- 用于 sector 和 sector_trajectory 因子

---

## 三、市场体制检测

```python
_detect_regime(date_str) → (weights, regime)
```

基于全市场涨跌比和主力资金流向:

| 条件 | 体制 | 策略 |
|------|------|------|
| 上涨比 > 55% 且 正流比 > 50% 且 中位涨幅 > +0.3% | **bull** | WEIGHTS_BULL |
| 上涨比 < 30% 或 (中位 < -1.0% 且 正流比 < 35%) | **bear** | WEIGHTS_BEAR |
| 其他 | **range** | WEIGHTS_BASE |

**黑天鹅联动**: 如果 BlackSwanDetector 检测到 Level ≥ 2，强制切换到 WEIGHTS_BEAR。

---

## 四、19 因子详解

### 因子 1: capital (资金强度) — 权重 19%

```python
capital = pct_rank(全市场f62, avg_f62) × 0.60
        + pct_rank(全市场f184, f184) × 0.25
        + pct_rank(全市场f69, f69) × 0.15
```

- **avg_f62**: 如有多快照数据，使用全天 f62 均值（更稳定）
- 评分: 0~1 percentile rank

### 因子 2: start_signal (启动信号) — 权重 13%

```python
accel_score = 看 f164(5日流) vs f174(10日流) 方向
  - 双正: min(1.0, f164/f174/3)
  - 仅5日正: 0.65
  - 其他: 0.35

big_order_quality = abs(f66) / max(abs(f62), 1)  # 超大单占比

start = accel_score × 0.55 + big_order_quality × 0.45
```

检测资金是否**刚开始**加速流入的状态。

### 因子 3: trend (趋势确认) — 权重 10%

```python
trend = range_score(量比, 1.5-4.0) × 0.15
      + range_score(换手率, 5-18) × 0.25
      + range_score(涨跌幅, 2.5-7.0) × 0.25
      + 10日价格回报分 × 0.11  (有价格历史时)
      + 20日价格回报分 × 0.09  (有价格历史时)
      + 短期趋势分 × 0.15
```

range_score 在理想区间内 = 1.0，超出后线性衰减。

### 因子 4: position (位置健康) — 权重 6%

连续分段线性函数，基于 60 日高低位:

| 位置 | 得分 | 含义 |
|------|------|------|
| < 10% (极低位) | 0.15→0.25 | 超跌但风险大 |
| 10-25% (低位) | 0.25→0.80 | **最佳买入区** |
| 25-40% (偏低) | 0.80→0.55 | |
| 40-65% (中位) | 0.55→0.45 | 中性 |
| 65-85% (偏高) | 0.45→0.35 | |
| > 85% (高位) | 0.35→0.15 | 回调风险大 |

### 因子 5: multiday (多日累计) — 权重 6%

```python
f164_pct = pct_rank(全市场5日主力净流入)
f174_pct = pct_rank(全市场10日主力净流入)

if f164 > 0 and f174 > 0:       # 双正方向
    multiday = f164_pct×0.45 + f174_pct×0.30 + 0.25
elif f164 > 0:                   # 仅5日正
    multiday = f164_pct×0.60 + 0.10
else:                            # 偏弱
    multiday = f164_pct×0.40 + f174_pct×0.30
```

### 因子 6: sector (板块共振) — 权重 5%

```python
# 方案B: f100 自建行业分类 + API 行业流合并
industry_flow = merged_sector_flows[行业名]
sector = pct_rank(全行业流, industry_flow) × 0.70
       + 概念共振分 × 0.30
```

### 因子 7: technical (技术形态) — 权重 5%

```python
# MA 多头排列 (需 ≥20 天价格历史)
align = (MA5>MA10 + MA10>MA20 + MA5>MA20 + f2>MA5) / 4
# 突破检测
breakout = 1.0 if f2 > 60日高×0.98 else 0.4

tech = align × 0.60 + breakout × 0.40
```

无价格历史 → 默认 0.5。

### 因子 8: intra_sector (行业内排名) — 权重 4%

```python
intra_sector = pct_rank(同行业f62数组, my_f62)
```

### 因子 9: margin_net (融资动向) — 权重 3%

```python
margin_net = pct_rank(全市场f168, my_f168)
```

f168 = 融资净买入额。

### 因子 10: flow_accel (流加速度) — 权重 1%

```python
if f164 > 0 and f174 > 0:
    accel = f164 / abs(f174)  # 5日流 / 10日流
    flow_accel = range_score(accel, 1.3, 2.5)
```

5 日资金流大于 10 日流 = 资金在加速流入。

### 因子 11: flow_stability (资金稳定性) — 权重 3%

```python
# 日内快照的f62波动率
flow_stability = 1.0 - min(1.0, std(f62_seq) / abs(mean(f62_seq)))
```

低波动 = 机构持续买入，高波动 = 游资短线。

### 因子 12: intraday_accel (日内加速) — 权重 3%

```python
# 后半段 vs 前半段 f62 均值比
accel_raw = (second_half - first_half) / max(abs(first_half), 1)
intraday_accel = 0.5 + clamp(accel_raw * 2, -0.5, 0.5)
```

> 0.5 = 下午资金加速流入。

### 因子 13: rank_trajectory (排名轨迹) — 权重 2%

```python
# 全市场f62排名在日内快照中的改善
rank_improve = rank_first - rank_last  # 正=排名上升
rank_trajectory = 0.5 + clamp(rank_improve * 3, -0.5, 0.5)
```

### 因子 14: vwap_position (VWAP位置) — 权重 2%

```python
vwap = 日内均价
dev = f2 / vwap

<0.98 → 0.70 (低估)
<1.00 → 0.60
≤1.02 → 0.50
≤1.05 → 0.40
>1.05 → 0.30 (追高)
```

### 因子 15: sector_trajectory (板块轨迹) — 权重 2%

```python
# 所在行业的日内排名改善 + 流入加速度
rank_score = 0.5 + clamp(rank_improve/len*5, -0.3, 0.3)
accel_score = 0.5 + clamp((后半场均流-前半场均流)/均值, -0.3, 0.3)
sector_trajectory = rank_score × 0.50 + accel_score × 0.50
```

### 因子 16 🆕: price_momentum (价格动量) — 权重 3%

```python
ret_5d  = range_score((close - close_4ago)/close_4ago × 100, 3, 15, -10, 30)
ret_10d = range_score((close - close_9ago)/close_9ago × 100, 3, 15, -10, 30)
ret_20d = range_score((close - close_19ago)/close_19ago × 100, 3, 15, -10, 30)
price_momentum = mean(ret_5d, ret_10d, ret_20d)
```

需要 ≥5 天价格历史。

### 因子 17 🆕: limitup_proximity (涨停邻近惩罚) — 权重 2%

| 今日涨幅 | 得分 | 逻辑 |
|------|------|------|
| ≥ 9.5% (涨停) | 0.1 | 涨停买不到 |
| 8-9.5% | 0.5→0.2 (线性降) | 次日回调风险 |
| 6-8% | **0.7→0.85** | 最佳区间 |
| 3-6% | 0.5→0.7 | 温和上涨 |
| 0-3% | 0.4→0.5 | 中性 |
| < 0% | 0.4→0.3 | 下跌 |

### 因子 18 🆕: sector_diversity (行业分散度) — 权重 2%

```python
ratio = 该行业在全市场中的占比

≤10% → 1.0  (稀缺，加分)
10-20% → 0.8 (正常)
20-30% → 0.5 (偏多，轻微惩罚)
>30% → 0.3  (拥挤，明显惩罚)
```

### 因子 19 🆕: sector_price (板块价格共振) — 权重 3%

```python
# 从 price_hist 聚合行业的中位 5 日价格回报
sector_ret = median(ret_5d of all stocks in same industry)
sector_price = pct_rank(全行业中位回报, sector_ret)
```

---

## 五、综合得分

```python
total = Σ sub[k] × weights[k]   (k ∈ 19 factors)
# 默认 missing factor = 0.5
```

### 三套权重

| 因子 | BASE | BULL | BEAR |
|------|------|------|------|
| capital | 0.19 | 0.19 | 0.19 |
| start_signal | 0.13 | **0.15** | 0.13 |
| trend | 0.10 | **0.12** | 0.10 |
| position | 0.06 | 0.06 | **0.08** |
| multiday | 0.06 | 0.06 | 0.06 |
| sector | 0.05 | 0.05 | 0.05 |
| technical | 0.05 | 0.05 | 0.05 |
| intra_sector | 0.04 | 0.04 | 0.04 |
| margin_net | 0.03 | 0.03 | 0.03 |
| flow_accel | 0.01 | **0.02** | 0.01 |
| flow_stability | 0.03 | 0.03 | 0.03 |
| intraday_accel | 0.03 | 0.03 | 0.03 |
| rank_trajectory | 0.02 | 0.02 | 0.02 |
| vwap_position | 0.02 | 0.02 | 0.02 |
| sector_trajectory | 0.02 | 0.02 | 0.02 |
| price_momentum | 0.03 | **0.04** | 0.03 |
| limitup_proximity | 0.02 | 0.02 | **0.04** |
| sector_diversity | 0.02 | 0.02 | **0.03** |
| sector_price | 0.03 | **0.04** | 0.03 |
| **总和** | **0.94** | **1.00** | **0.97** |

**设计意图**:
- **牛市**: 提高趋势、启动、价格动量 → 跟涨
- **熊市**: 提高位置、涨停邻近、行业分散 → 防御

---

## 六、P 因子修正（总分 ±0.15）

在综合得分基础上，19 个 P 因子对总分进行修正:

### 加分项
| 信号 | 触发条件 | 加减分 |
|------|------|------|
| P32_ratio_accel | 主力占比温和加速(今日>5日>10日) | +0.05 |
| P33_margin_strong | 融资买入占比 > 8% | +0.03 |
| P33_margin_moderate | 融资买入占比 3-8% | +0.01 |
| P34_gap_strong | 开盘缺口 > 2% 且收涨 > 2% | **+0.04** |
| P34_gap_reverse | 开盘缺口 < -2% 但收涨 > 1% | +0.01 |
| P35_short_cover | 融券净卖出 < -1亿 (空头回补) | +0.02 |
| P37_momentum_up | 得分较前日改善 > 0.05 | +0.03 |

### 扣分项
| 信号 | 触发条件 | 加减分 |
|------|------|------|
| P6_retail | 小单占比 > 30% 且涨幅 < 3% | -0.08 |
| P29_high_turnover | 换手率 > 13% 且资金弱 | -0.06 |
| P_low_liquidity | 换手率 < 1% | -0.04 |
| P_low_vol_ratio | 量比 < 0.8 | -0.03 |
| P_small_cap | 市值 < 30亿 | -0.04 |
| P_high_price | 股价 > 200元 | -0.02 |
| P32_pump_risk | 单日脉冲风险(今日占比高但5日低迷) | -0.05 |
| P32_extreme | 占比极端高位 | -0.04 |
| P33_margin_weak | 融资买入占比 < -5% | -0.03 |
| P34_gap_trap | 高开缺口 > 3% 但收跌 | -0.04 |
| P35_short_pressure | 融券净卖出 > 3亿 | -0.04 |
| P35_short_moderate | 融券净卖出 > 1亿 | -0.02 |
| P35_short_heavy | 融券/主力比 > 3 | -0.03 |
| P36_overheat | 全维度过热(资金>0.85+趋势>0.7+多日>0.85) | -0.06 |
| P37_momentum_down | 得分较前日恶化 > 0.05 | -0.03 |

### 互斥规则
- `P32_ratio_accel` 和 `P36_overheat` 互斥: 过热股撤销 P32 加分
- `total = clamp(total, 0.0, 1.0)`

---

## 七、启动得分（独立维度）

额外计算一个 `early_score`，使用不同的权重 `EARLY_WEIGHTS`:

| 因子 | 基准权重 | 启动权重 | 变化 |
|------|------|------|------|
| capital | 0.19 | **0.10** | ↓ 降资金 |
| start_signal | 0.13 | **0.25** | ↑ 升启动 |
| position | 0.06 | **0.15** | ↑ 升位置 |
| trend | 0.10 | **0.15** | ↑ 升趋势 |

**设计意图**: 资金正在从底部启动的股票，在 `early_score` 中得分更高。

启动专属信号:
- E1: 低位启动 (位置<0.25 + 启动>0.5)
- E2: 温和启动 (资金0.4-0.7 + 启动>0.6)
- E3: 强势启动 (启动>0.8 + 资金>0.5)
- E4: 缺口启动 (P34 + 低位)
- E5: 占比早期加速
- E6: 逼空启动 (融券回补 + 资金流入)

---

## 八、板块评分（独立模块）

```python
score_sectors(date_str)
```

6 维度板块评分 (0~1):
1. **最新排名** (20%): 全行业资金流排名
2. **排名趋势** (25%): 日内排名改善斜率
3. **资金持续性** (20%): 连续正流入快照占比
4. **流入加速度** (15%): 后半段 vs 前半段
5. **集中度** (10%): 该行业流入占全市场比例
6. **排名稳定性** (10%): 排名波动越小越好

输出: `research_data/<date>/sector_scores.csv`

---

## 九、输出格式

`scores.csv` 31 列:
```
代码,名称,最新价,行业,综合得分,启动得分,
资金得分,趋势得分,启动因子,板块得分,位置得分,
分析师得分,多日得分,技术面得分,行业内得分,
融资得分,加速度得分,占比趋势得分,
日内稳定,日内加速,排名轨迹,VWAP位置,板块轨迹,
价格动量,涨停邻近,行业分散,板块价格,
涨跌幅,换手率,量比,总市值,
综合信号,综合信号说明,启动信号,启动信号说明
```
