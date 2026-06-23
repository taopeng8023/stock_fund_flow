# A股量化选股系统增强计划

## Context（为什么）

### 当前状态

项目已有一套完整的**买入端**量化选股系统：

- **数据采集层**：10 个 fetcher 覆盖全市场个股资金流、板块资金流、北向资金、龙虎榜、分析师预测、大宗交易、机构调研、业绩预告、限售解禁
- **评分引擎**：22 维多因子模型，含牛熊市权重自适应、P0-P33 事后修正因子、盘中轨迹追踪
- **市场诊断**：8 模块综合诊断（宽度、资金全景、板块轮动、北向、体制识别、风险预警、仓位建议、情绪温度计）
- **盘中 pipeline**：`daily_pipeline/` 每 30 分钟采集快照，14:31 触发全市场评分
- **表现追踪**：`performance.py` 记录次日涨跌幅，`daily_pipeline/backtest.py` 做分组回测

### 核心缺口

| 需求 | 现状 | 缺口 |
|------|------|------|
| 14:30 后买入推荐 | 评分存在但分散，`filter_overnight.py` 独立运行 | 无集成的"买入决策"模块（评分 + 风控门禁 + 仓位分配 + 行业分散） |
| 持仓卖出指示 | **完全不存在** | 无止损/止盈/时间退出/信号衰减/体制退出逻辑 |
| 黑天鹅规避 | `market_diagnosis.py` 有风险等级，但仅在选股时做门禁 | 无持续仓位级风险监控，无个股事件检测，无熔断机制 |
| 通知触达 | **完全不存在** | 无企业微信/邮件/任何告警通道 |
| 持仓数据库 | 全部基于文件（JSON/CSV） | 无结构化持仓记录、交易日志、每日评分快照 |
| 调度守护 | `daily_pipeline/run.sh` 需手动启动 | 无 APScheduler 守护进程，无交易日历，`daily_run.sh` 被引用但不存在 |

### 目标

在不修改 `data_collector/` 和 `sector_screener/` 核心逻辑的前提下，**增量构建**四个新能力：

1. **尾盘买入推荐** — 14:30 后自动输出带仓位分配的可执行买入清单
2. **持仓卖出指示** — 每日评估已持仓个股，按止损/止盈/时间/信号衰减/体制变化生成卖出信号
3. **黑天鹅识别** — 持续监控市场宽度、资金外逃、流动性冻结、北向恐慌等极端信号
4. **企业微信通知** — 买入推荐、卖出告警、黑天鹅预警、每日市场摘要自动推送

---

## Approach（怎么做）

### 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│                    调度层 scheduler/daemon.py                  │
│  交易日历 → 09:31-15:01 盘中快照 → 14:35 评分 → 14:40 决策     │
└───────────────────────────┬──────────────────────────────────┘
                            │ 调用
    ┌───────────────────────┼───────────────────────┐
    │                       ▼                       │
    │  ┌─────────────────────────────────────────┐  │
    │  │          决策层（NEW）                    │  │
    │  │  buy_engine  │ sell_engine │ black_swan  │  │
    │  └──────────────────┬──────────────────────┘  │
    │                     │                         │
    │  ┌──────────────────▼──────────────────────┐  │
    │  │        持仓数据库 portfolio/db.py         │  │
    │  │   Position │ Trade │ DailyScore │ Diag   │  │
    │  └──────────────────┬──────────────────────┘  │
    │                     │                         │
    ┌─────────────────────┼─────────────────────────┐
    │  现有系统（不改动）   │                         │
    │  data_collector/     │  sector_screener/       │
    │  daily_pipeline/     │  market_diagnosis.py    │
    └─────────────────────┴─────────────────────────┘
                            │
                    ┌───────▼────────┐
                    │  notify/wecom  │
                    │  企业微信通知    │
                    └────────────────┘
```

**设计原则**：
- `data_collector/` 和 `sector_screener/scorers/` **零修改**，只读取其输出
- 仅在 `daily_pipeline/score.py` 和 `market_diagnosis.py` 各加 ~5 行的数据库写入 hook
- 所有新代码放在 `portfolio/`、`notify/`、`scheduler/` 三个新包中
- SQLite + Peewee ORM，单文件 `portfolio.db`（已加入 `.gitignore`）

### 功能拆分 & 实现顺序

#### Phase 1：持仓数据库基础（预计 1-2 天）

**新建文件**：
- `portfolio/__init__.py`
- `portfolio/db.py` — 6 个 Peewee Model（Position / Trade / DailyScore / DiagnosisSnapshot / NotificationLog）+ `init_db()`
- `portfolio/db_migrate.py` — CLI 建表入口 `python -m portfolio.db_migrate`

**依赖安装**：`pip install peewee`（已全局安装，需同步到 `.venv/`）

**数据库表**：

| 表 | 用途 | 关键字段 |
|----|------|---------|
| Position | 当前持仓 | code, entry_date, entry_price, stop_loss_pct, take_profit_pct, trailing_stop_peak, status, pnl_pct |
| Trade | 交易日志 | code, action(BUY/SELL), price, reason, → Position |
| DailyScore | 每日评分快照 | date, code, composite_score, early_score, signals |
| DiagnosisSnapshot | 每日市场诊断 | date, regime, risk_level, up_ratio, sentiment_score |
| NotificationLog | 通知发送记录 | date, event_type, message_hash, success |

**验证**：`python -m portfolio.db_migrate && sqlite3 portfolio.db .schema`

---

#### Phase 2：表现追踪增强（预计 1-2 天）

**新建文件**：
- `portfolio/performance_tracker.py` — 替换 `performance.py` 的单文件追踪

**功能**：
- `record_picks()` — 将每日选股写入 Position/Trade 表
- `update_positions()` — 用当日收盘数据更新持仓浮动盈亏
- `close_position()` — 记录卖出并结算 P&L
- `get_equity_curve()` — 生成每日权益曲线
- `get_metrics()` — 胜率 / 平均收益 / 最大回撤 / 夏普比率

**修改文件**：
- `daily_pipeline/score.py` — 评分后自动写入 DailyScore 表（~5 行 hook）
- `market_diagnosis.py` — 诊断后自动写入 DiagnosisSnapshot 表（~5 行 hook）
- `performance.py` — 添加 `from portfolio.performance_tracker import *` 桥接

**验证**：运行一次完整 pipeline，检查 `portfolio.db` 中 DailyScore 和 DiagnosisSnapshot 有数据

---

#### Phase 3：买入推荐引擎（预计 2-3 天）

**新建文件**：
- `portfolio/buy_engine.py` — CLI 入口 `python -m portfolio.buy_engine --date=20260623`

**逻辑流程**：
1. 加载 `research_data/<date>/scores.csv` 全市场评分
2. 加载市场诊断（体制 + 风险等级）
3. 风险门禁：`risk_level == "critical"` → 直接返回空列表
4. 应用隔夜筛选（复用 `filter_overnight.py` 逻辑，重构为可 import 函数）：
   - `score > 0.38`, `capital > 0.50`, `trend > 0.50`, `sector > 0.45`
   - `vol_ratio > 1.2`, `turnover < 15%`
   - 排除不良信号（高换手出货、散户主导、拉高出货、融券压力）
5. 仓位分配：

| 市场体制 | 单票基础仓位 | 最大持仓数 |
|---------|-------------|-----------|
| bull（多头） | 20% | 5 |
| bull_bias（偏多） | 16% | 5 |
| range（震荡） | 12% | 4 |
| bear_bias（偏空） | 8% | 3 |
| bear（空头） | 0-5% | 0-2 |

6. 风险折扣：`low=1.0x, medium=0.75x, high=0.50x, critical=0x`
7. 行业分散：同行业最多 2 只
8. 去重：排除已在 Position 表中的持仓

**输出格式**（JSON + 企业微信 Markdown 消息）：
```json
{
  "date": "20260623",
  "regime": "偏多震荡",
  "risk_level": "中",
  "suggested_position": 50,
  "buys": [
    {
      "rank": 1, "code": "000001", "name": "平安银行",
      "score": 0.783, "chg_pct": 3.2, "allocation_pct": 12,
      "stop_loss": -5.0, "take_profit": 15.0,
      "sub_scores": {"capital": 0.82, "trend": 0.76, "start": 0.68},
      "industry": "银行", "reasons": ["资金强度高", "趋势确认", "板块共振"]
    }
  ]
}
```

**修改文件**：
- `daily_pipeline/filter_overnight.py` — 重构 `filter_overnight()` 为可导入函数

**验证**：`python -m portfolio.buy_engine --date=20260623`，检查输出 JSON 合理性

---

#### Phase 4：卖出信号引擎（预计 2-3 天）

**新建文件**：
- `portfolio/sell_engine.py` — CLI 入口 `python -m portfolio.sell_engine`
- `portfolio/sell_rules.py` — 15 条卖出规则函数

**卖出规则矩阵**：

| 类别 | 规则ID | 触发条件 | 紧急度 |
|------|--------|---------|--------|
| **止损** | SL-1 硬止损 | 现价 ≤ 成本价 × (1 - 5%) | HIGH |
| | SL-2 移动止盈回撤 | 从最高点回撤 ≥ 8%（仅在盈利 > 10% 后激活） | HIGH |
| **止盈** | TP-1 目标止盈 | 现价 ≥ 成本价 × 1.15 | MEDIUM |
| | TP-2 快盈锁仓 | 持有 ≤ 3 天且涨幅 ≥ 8% | MEDIUM |
| **时间退出** | TE-1 死钱退出 | 持有 ≥ 10 天且涨跌幅 ≤ 2% | MEDIUM |
| | TE-2 水下过久 | 持有 ≥ 5 天且亏损 ≥ 2% | HIGH |
| **信号衰减** | SD-1 评分崩塌 | 综合得分较买入时下降 0.15+，且连续 2 天 < 0.35 | HIGH |
| | SD-2 持续恶化 | 连续 3 天得分下降且累计降幅 ≥ 0.10 | MEDIUM |
| **体制退出** | RE-1 空头清仓 | 市场体制 = bear 且个券得分 < 0.30 | HIGH |
| | RE-2 危急撤离 | 风险等级 = critical 且持仓亏损 | URGENT |
| | RE-3 偏空止损 | 体制 = bear_bias 且亏损 ≥ 3% 且正流比 < 35% | MEDIUM |
| **模式识别** | PI-1 信号反转 | 出现 P35_short_pressure 或 P29_high_turnover 信号 | MEDIUM |
| | PI-2 板块崩溃 | 所属行业板块资金排名单日下滑 20+ 位 | HIGH |
| | PI-3 北向出逃 | 北向连续 2 日净流出 > 100 亿 | MEDIUM |
| **再平衡** | RC-1 行业集中 | 同行业持仓 > 2 只，卖出得分最低的那只 | LOW |

**紧急度定义**：
- **URGENT**：下一交易日开盘即卖
- **HIGH**：强烈卖出信号，9:31 执行
- **MEDIUM**：建议卖出，盘中择机
- **LOW**：再平衡建议，自行决定

**逻辑流程**：
1. 从 Position 表查询所有 `status="active"` 的持仓
2. 从 DailyScore 表加载最近 10 天评分历史
3. 从 DiagnosisSnapshot 表加载最近市场体制
4. 逐条规则评估，收集触发信号
5. 按紧急度排序输出，记录到 Trade 表

**验证**：手动插入测试持仓 → 运行 sell engine → 检查信号合理性

---

#### Phase 5：黑天鹅监控（预计 1-2 天）

**新建文件**：
- `portfolio/black_swan.py` — CLI 入口 `python -m portfolio.black_swan`

**检测规则**：

| 规则ID | 条件 | 严重度 | 动作 |
|--------|------|--------|------|
| BS-1 宽度熔断 | 上涨比 < 10% 且跌停 > 100 | CRITICAL | 禁止买入，标记紧急卖出 |
| BS-2 资金出逃 | 主力净流出 > 500 亿 且正流比 < 15% | CRITICAL | 禁止买入，减仓 |
| BS-3 连续暴跌 | 上证/深证/创业板任一日跌幅 ≥ 2.5% 连续 2 天 | SEVERE | 禁止买入，告警 |
| BS-4 流动性冻结 | 跌停 > 300 且成交额 > 2 倍正常值 | CRITICAL | 禁止一切交易 |
| BS-5 北向恐慌 | 北向连续 2 日净流出 > 100 亿 | SEVERE | 禁止买入 |
| BS-6 板块雪崩 | Top5 行业全部从流入翻转为流出 | HIGH | 新仓减半 |
| BS-7 波动率爆炸 | 中位涨跌幅绝对值 > 5% | SEVERE | 禁止买入 |
| BS-8 融资恐慌 | 融资正流比 < 20% | HIGH | 新仓打 7 折 |
| BS-9 情绪冰冻 | 情绪温度计 < 15/100 | CRITICAL | 全部卖出建议 |

**响应级别**：

| 级别 | 触发条件 | 动作 |
|------|---------|------|
| Level 0 正常 | 无规则触发 | 正常运行 |
| Level 1 关注 | 1 条 HIGH | 新仓打 7 折 |
| Level 2 警告 | 1 条 SEVERE 或 3+ HIGH | 禁止新买入，止损收紧至 -3% |
| Level 3 紧急 | 1 条 CRITICAL 或 3+ SEVERE | 全部持仓标记审查，发送 URGENT 通知 |

**验证**：对照历史极端行情日期（如 2024-02-05 千股跌停），验证检测率

---

#### Phase 6：企业微信通知系统 ✅ 已提前实施（预计 2-3 天 → 实际 1 天）

> **2026-06-23 已实施**，比计划提前。原计划用飞书，用户选择企业微信 Webhook。

**已创建文件**：
- `notify/__init__.py` — 模块入口，导出 `WeComSender`
- `notify/config.py` — webhook URL 配置（从环境变量读取 `QUANT_WECOM_WEBHOOK`）
- `notify/wecom_sender.py` — `WeComSender` 类，支持 `send_text()` / `send_markdown()` / `send_news()` / `send_file()` / `send_image()`
- `notify/message_builder.py` — 6 类消息模板构建器 + 去重键生成

**通知事件矩阵**：

| 事件 | 消息类型 | 频率 | 说明 |
|------|---------|------|------|
| 每日买入推荐 | markdown | 每交易日 1 次 | 股票列表、得分、分配、止损止盈 |
| 卖出信号 | markdown | 触发时发送 | 红色主题（持仓信息、触发原因、持有天数、盈亏） |
| 黑天鹅预警 | text + @all | 立即 | 触发规则、建议动作，Level 2+ @all |
| 每日市场摘要 | markdown | 每交易日 1 次 | 诊断摘要表格（体制、宽度、资金、板块、风险） |
| Pipeline 错误 | text | 失败时 | 错误阶段 + 错误信息 + 堆栈截断 |
| 周度表现总结 | markdown | 每周五收盘后 | 周收益、胜率、最大回撤、夏普、最佳/最差 |

**去重机制**：
- 对 `(event_type + date + stock_code)` 做 SHA256
- 内存级去重（set 存储最近 10000 条 hash）
- Phase 1 建表后迁移到 `NotificationLog` 表

**验证**：
```bash
QUANT_WECOM_WEBHOOK="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY" \
  python -c "from notify.wecom_sender import send_text; send_text('✅ 量化系统通知测试')"
```

---

#### Phase 7：调度守护进程（预计 2-3 天）

**新建文件**：
- `scheduler/__init__.py`
- `scheduler/daemon.py` — APScheduler 主循环
- `scheduler/trading_calendar.py` — A 股交易日历（调休检测）
- `scheduler/run_daily.sh` — 手动/bash 包装脚本
- `daily_run.sh` — 项目根目录入口（补充被引用但缺失的文件）

**调度时间表**（北京时间）：

| 时间 | 动作 | 说明 |
|------|------|------|
| 09:05 | 盘前采集（可选） | 当日公告/事件采集 |
| 09:31 | 盘中快照 #1 | 开盘价采集 |
| 10:01-14:01 | 盘中快照 #2-#7 | 每 30 分钟 |
| 14:31 | 盘中快照 #8 | 尾盘前最后快照 |
| 14:35 | 全市场评分 | `score_all_stocks(cutoff="1431")` |
| 14:38 | 市场诊断 | `get_diagnosis()` |
| 14:40 | 黑天鹅检测 | `BlackSwanDetector.check()` |
| 14:42 | 买入推荐 | `generate_buy_recommendations()` |
| 14:44 | 卖出信号 | `generate_sell_signals()` |
| 14:48 | 企业微信推送 | 买入清单 + 卖出信号 + 诊断摘要 |
| 15:01 | 收盘快照 | 最终数据采集 |
| 18:00 | 全量数据采集（可选） | 补采当日完整数据 |

**交易日历**：
- 调休检测：调用 `push2delay.eastmoney.com` 查当日是否为交易日
- 节假日跳过：自动静默，不做任何操作
- 手动模式：`python -m scheduler.daemon --date=20260623 --manual` 单日执行

**启动方式**：
```bash
# 前台运行（调试）
python -m scheduler.daemon

# 后台运行（macOS launchd）
cp scheduler/com.app.quant.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.app.quant.plist

# 手动单次
bash daily_run.sh
```

**修改文件**：
- `daily_pipeline/run.sh` — 顶部添加注释指向 `scheduler/daemon.py`

**验证**：工作日 14:30 前启动 daemon（前台模式），观察完整流程执行

---

#### Phase 8：Dashboard 增强（预计 2-3 天）

**修改文件**：
- `web_dashboard/web_dashboard/web_dashboard.py`

**新增 Tab**：

| Tab | 内容 |
|-----|------|
| 持仓管理 | Position 表实时展示（代码、名称、入场价、现价、浮盈%、持有天数、当前得分、活跃信号） |
| 交易历史 | Trade 表筛选查询（日期范围、买卖方向、盈亏排序） |
| 风险监控 | 黑天鹅指标仪表盘（宽度、资金流向、北向、情绪温度计实时状态）+ 最近告警 feed |
| 通知配置 | webhook URL 配置、通知开关、测试发送按钮 |

**状态扩展**：`DashboardState` 新增 `positions`, `trades`, `risk_status`, `alerts`, `notify_config`

**验证**：`reflex run` → 浏览器 localhost:8000 → 查看新 tab

---

### 依赖安装清单

```bash
source .venv/bin/activate
pip install peewee        # ORM
pip install apscheduler   # 调度守护
pip install numpy         # 数值计算（buy_engine 仓位分配）
```

---

## Verification（怎么验证）

### 端到端验证流程

```
1. 建表
   python -m portfolio.db_migrate
   sqlite3 portfolio.db ".tables"  # 应输出 6 张表

2. 单日完整数据采集
   python -m data_collector.main --date=20260623
   # 检查 data/20260623/ 下有所需文件

3. 全市场评分 + 数据库回写
   python -m daily_pipeline.score --date=20260623 --cutoff=1431
   # 检查 research_data/20260623/scores.csv 有数据
   # 检查 portfolio.db DailyScore 表有当日记录

4. 市场诊断 + 数据库回写
   python market_diagnosis.py --date=20260623
   # 检查 portfolio.db DiagnosisSnapshot 表有当日记录

5. 买入推荐
   python -m portfolio.buy_engine --date=20260623
   # 输出 JSON 买入清单，检查：
   #   - 买入数量 ≤ 最大持仓数
   #   - 同行业 ≤ 2 只
   #   - 每只都有 stop_loss / take_profit
   #   - risk_level=="critical" 时返回空

6. 卖出信号（手动插入测试持仓后）
   python -m portfolio.sell_engine --date=20260623
   # 检查：
   #   - 持仓亏损 ≥ 5% 触发 SL-1
   #   - 盈利 ≥ 15% 触发 TP-1
   #   - 持有 ≥ 10 天收益 ≤ 2% 触发 TE-1

7. 黑天鹅检测
   python -m portfolio.black_swan --date=20260623
   # 正常交易日应输出 Level 0
   # 极端行情日应触发相应级别

8. 企业微信通知测试
   QUANT_WECOM_WEBHOOK="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..." python -c "from notify.wecom_sender import send_text; send_text('测试')"
   # 企业微信群应收到测试消息

9. 调度守护（前台模式，交易日 14:30 前）
   python -m scheduler.daemon --manual --date=20260623
   # 观察日志输出，确认每一步执行成功

10. Dashboard
    reflex run
    # 浏览器 localhost:8000 → 持仓管理 tab 应展示数据
```

### 回测验证

对历史 N 个交易日重放 pipeline，对比：
- **纯买入持有** vs **买入 + 卖出规则** 的收益曲线
- 最大回撤改善幅度
- 胜率变化
- 夏普比率变化

```bash
# 批量回测脚本（Phase 4 后可用）
python -m portfolio.backtest --start=20260101 --end=20260623
```

### 监控指标

系统上线后持续关注：
- 每日买入推荐数量（正常 3-8 只，过多/过少需检查）
- 卖出信号触发频率（正常每日 0-3 条）
- 黑天鹅误报率（正常每月 < 2 次 Level 2+）
- 企业微信通知到达率（应 100%）
- 调度守护可用率（应 > 95%）

---

## 文件清单汇总

### 新建文件（20 个）

```
portfolio/
    __init__.py
    db.py                     # Peewee 模型 + init_db()
    db_migrate.py             # CLI 建表
    buy_engine.py             # 买入推荐引擎
    sell_engine.py            # 卖出信号引擎
    sell_rules.py             # 15 条卖出规则
    black_swan.py             # 黑天鹅检测
    performance_tracker.py    # 多日持仓追踪

notify/
    __init__.py
    config.py                 # 企业微信 Webhook 配置
    wecom_sender.py           # 企业微信消息发送
    message_builder.py        # 消息模板构建

scheduler/
    __init__.py
    daemon.py                 # APScheduler 主循环
    trading_calendar.py       # A股交易日历
    run_daily.sh              # Bash 包装

daily_run.sh                  # 项目根目录入口（补充缺失文件）

config/
    thresholds.py             # 集中阈值配置（可选）
```

### 修改文件（6 个）

```
daily_pipeline/filter_overnight.py    # 重构为可导入函数
daily_pipeline/score.py               # 加 ~5 行 DailyScore 写入 hook
daily_pipeline/run.sh                 # 顶部注释指向 scheduler
market_diagnosis.py                   # 加 ~5 行 DiagnosisSnapshot 写入 hook
performance.py                        # 桥接到 portfolio/performance_tracker
web_dashboard/web_dashboard/web_dashboard.py  # 新增 4 个 tab
```

### 不修改的核心模块

```
data_collector/            # 10 个 fetcher，零改动
sector_screener/scorers/   # 22 个 scorer，零改动
sector_screener/main.py    # 零改动
daily_pipeline/collect.py  # 零改动
```
