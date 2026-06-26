---
name: backtest-analyzer
description: >
  多 Agent 回测分析验证流水线。三种模式：(1) 单日信号分析 — 本地 Python 快速扫描；
  (2) 多快照回测 — 均匀随机采样次日盘中快照，评估日内稳定性；
  (3) 深度信号发现 — 3 Agent 独立上下文发现 + 1 Agent 验证 + 1 Agent 裁决。
  触发词：回测分析、信号验证、买入信号、因子分析、多Agent分析、多快照回测。
argument-hint: "[--date YYYYMMDD] [--dates YYYYMMDD,...] [--mode signals|snapshot|full]"
license: MIT
metadata:
  version: "2.0.0"
  requires:
    - research_data/<date>/scores.csv
    - research_data/<date>/intraday/fund_flow_*.csv
    - research_data/backtest/multi_snapshot/<date>/stats.json
  scripts:
    - daily_pipeline/backtest_multi_snapshot.py
    - daily_pipeline/verify_analysis.py
    - daily_pipeline/backtest.py
---

# 回测分析 Skill v2.0

## 三种运行模式

| 模式 | 触发条件 | 耗时 | 适用场景 |
|------|---------|------|---------|
| 模式 1: 单日信号分析 | 分析单日评分/信号质量 | 秒级 | 快速诊断 |
| 模式 2: 多快照回测 | 跨日稳定性/日内时机敏感性 | 分钟级（多 Agent 并行） | 回测评估 |
| 模式 3: 深度信号发现 | 发现新买入规则/因子交互 | 分钟级（5 Agent） | 策略研究 |

---

## 模式 1：单日信号分析（快速）

```bash
source .venv/bin/activate
python daily_pipeline/verify_analysis.py --date 20260626 --top 30
```

输出：行业板块 → 分析师推荐 → 验证对比 → 遗漏发现 → 最终推荐 → 分析师评价

---

## 模式 2：多快照回测（核心新增）

### 原理

评分日用 23 因子模型打分 → 下一交易日盘中随机抽取 3-6 个快照（均匀分布）→ 每个快照独立计算收益 → 汇总日内稳定性统计。

相比旧版单快照回测（仅取第一个快照），解决了**入场时机敏感性**和**单点采样偏差**问题。

### 手动运行（单日）

```bash
source .venv/bin/activate

# 单日多快照回测
python -m daily_pipeline.backtest_multi_snapshot 20260624

# 指定评估日 + 随机种子
python -m daily_pipeline.backtest_multi_snapshot 20260624 20260625 42
```

### 批量回测（多 Agent 并行，推荐）

当需要对多个日期批量回测时，使用 Workflow 编排：

```
Workflow: multi-snapshot-backtest
  Phase 1 — Backtest（N 个独立 Agent 并行）
    每个 Agent 独立上下文，处理一个日期对
    执行: python -m daily_pipeline.backtest_multi_snapshot <pick> <eval> <seed>
    不同 Agent 使用不同 seed（42 + i*7），确保快照采样多样化

  Phase 2 — Aggregate（1 Agent）
    收集所有 stats.json
    计算跨日均值/标准差/显著性检验
    分析日内模式（早盘 vs 午盘 vs 尾盘）
    输出 ANALYSIS_REPORT.md + MASTER_SUMMARY.csv
```

**关键参数**：
- 每个日期对的 seed 不同，保证多 Agent 采样不重复
- 快照采样数 k = random(3, min(6, 可用快照数))
- 采样策略：时间范围等宽分 k 个区间，每区间随机取 1 个快照
- 异常快照处理：时间戳超出 9:30-15:00 范围自动标注并可由分析 Agent 排除

### 输出结构

```
research_data/backtest/multi_snapshot/
├── MASTER_SUMMARY.csv              # 全日期汇总（跨日对比）
├── ANALYSIS_REPORT.md              # 完整分析报告（中文）
├── <YYYYMMDD>/
│   ├── snapshots/
│   │   ├── snapshot_HHMMSS.csv     # 每个快照的完整回测（全市场股票）
│   │   └── ...
│   ├── summary.csv                 # 当日各快照指标对比
│   └── stats.json                  # 结构化统计（含 aggregate 汇总）
```

### 统计指标说明

| 指标 | 含义 | 计算方式 |
|------|------|---------|
| avg_ret | 全市场平均收益 | 所有匹配股票 (快照价-评分日收盘)/评分日收盘 |
| win_rate | 全市场胜率 | 收益>0 的股票占比 |
| top50_ret | Top50 评分组合收益 | 综合得分前 50 名的平均收益 |
| top10_ret | Top10 评分组合收益 | 综合得分前 10 名的平均收益 |
| rand50_ret | 随机 50 对照组 | 随机抽取 50 只的平均收益（seed=42） |
| factor_edge | 因子区分度 | D1（前 10% 得分）平均收益 - D10（后 10%）平均收益 |
| deciles | 十分位分层 | 按综合得分分 10 组，每组平均收益和胜率 |

### 日内稳定性判定

- **Top50 收益 stdev / mean > 3**：日内时机敏感，入场时间决定盈亏
- **Top50 收益区间跨度 > 日均超额**：选时不当可完全抹杀 alpha
- **早/午/尾盘收益一致性**：若某时段系统性偏差 >1pp，说明存在日内模式

---

## 模式 3：深度信号发现（5 Agent 独立上下文）

启动 5 Agent 并行 Workflow：

**Phase 1 — Discover（3 Agent 并行）**：
- Agent A (score-signals): 测试分数阈值组合（得分+资金+板块+日内加速等 8-10 个）
- Agent B (token-signals): 测试信号标记（P34/P35/P37/E1/E6 等 14+ token 及组合）
- Agent C (factor-interactions): 测试因子交互（位置+资金、板块+动量、尾盘+VWAP 等）

每个 Agent 独立读取 CSV，输出 per-date win rate + cross-date stability。

**Phase 2 — Verify（1 Agent）**：
- 抽检三个报告 top 3 信号，对照原始 CSV 重算
- 标记样本夸大、胜率偏离 >5pp、不可能信号

**Phase 3 — Finalize（1 Agent）**：
- 排除被标记信号，编译 3-5 条可执行买入规则
- 附带买入前检查清单和仓位分配

---

## 信号可靠性评级

| 评级 | 条件 |
|------|------|
| S 级 | 胜率>40% + 样本>100 + 跨日波动<2pp + 均收益为正 |
| A 级 | 胜率>35% + 样本>100 + 波动<5pp |
| B 级 | 胜率>35% + 样本>50 + 波动<10pp |
| 排除 | 波动>10pp / 样本<30 / 80%+样本集中单日 / 验证员标记不准确 |

## 因子质量评级（新增）

| 评级 | 条件 |
|------|------|
| S 级 | 因子边缘>0.5% + 连续 3 日为正 + 日内 stdev<均值的 1/2 |
| A 级 | 因子边缘>0% + 多数日为正 + t 统计量>1.5 |
| B 级 | 因子边缘方向为正但 t<1.0 |
| 排除 | 因子边缘为负或日内 stdev > 2×均值 |

## 买入前检查清单

1. 市场体制非 bear/bear_bias
2. 标的成交额 > 5000万
3. 排除 >100元高价股（P_high_price 胜率仅 31.7%）
4. 不含 P37_momentum_down（最可靠负信号，胜率 23.7%）
5. 不叠加趋势过滤（A6→A7 胜率从 42% 暴跌至 26%）
6. 候选>20只时取综合得分前 10
7. 单票仓位 ≤ 总仓 10%

## 已知因子体系问题

- 零区分度：分析师得分/技术面得分/行业分散/拥挤度 (cardinality=1)
- 高度冗余：中线/启动/资金/短线/综合/行业内得分 (|r|=0.74-0.98)
- 最佳独立因子：板块得分 (|r|=0.29 with main cluster)
- 趋势过滤陷阱：叠加趋势条件摧毁收益率 (42%→26%)
- 市场体制依赖：牛转熊时信号失效，胜率从 62% 单调衰减至 16%
- 日内时机敏感性：单日跨度 1~2%，可完全抹杀日均超额

## Agent 编排原则

1. **独立上下文**：每个回测 Agent 处理一个日期对，互不干扰
2. **不同 seed**：确保各 Agent 快照采样不重复（seed = 42 + index * 7）
3. **结构化输出**：Agent 返回 JSON，聚合 Agent 做统计推断
4. **异常处理**：单快照日/盘后时间戳自动标注，聚合时选择性排除
5. **报告持久化**：聚合 Agent 可能无法写文件，主线程负责最终写入

## 相关文件

- 多快照回测脚本: `daily_pipeline/backtest_multi_snapshot.py`
- 旧版单快照回测: `daily_pipeline/backtest.py`
- 信号验证流水线: `daily_pipeline/verify_analysis.py`
- 输出目录: `research_data/backtest/multi_snapshot/`
- 记忆: [[buy-signals-20260626]] [[top-signals-20260624]]
