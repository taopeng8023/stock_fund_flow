"""采集器注册表 — 定义采集顺序和依赖"""
from datetime import timezone, timedelta
from data_collector.fetchers.base import today_str, BJS_TZ

# 采集器注册表: 按顺序执行
# 每个采集器: {name, module, required, description}
COLLECTOR_REGISTRY = [
    {
        "name": "fund_flow",
        "module": "data_collector.collectors.fund_flow",
        "required": True,
        "description": "个股资金流（主力净流入额排名，全量）",
    },
    {
        "name": "sector_flow",
        "module": "data_collector.collectors.sector_flow",
        "required": False,
        "description": "板块资金流（行业+概念）+ Top5成分股钻取",
    },
    {
        "name": "ratio_ranking",
        "module": "data_collector.collectors.ratio_ranking",
        "required": False,
        "description": "主力占比排名（f184 降序，前300正流入）",
    },
    {
        "name": "analyst",
        "module": "data_collector.collectors.analyst",
        "required": False,
        "description": "分析师盈利预测 + 评级",
    },
    {
        "name": "dragon_tiger",
        "module": "data_collector.collectors.dragon_tiger",
        "required": False,
        "description": "龙虎榜上榜明细（机构/游资买卖拆解）",
    },
    {
        "name": "north_flow",
        "module": "data_collector.collectors.north_flow",
        "required": False,
        "description": "北向资金市场流向（沪深港通）",
    },
    {
        "name": "block_trade",
        "module": "data_collector.collectors.block_trade",
        "required": False,
        "description": "大宗交易每日明细（溢价率/成交额/买卖方）",
    },
    {
        "name": "org_research",
        "module": "data_collector.collectors.org_research",
        "required": False,
        "description": "近30日机构调研明细（调研次数/家数）",
    },
    {
        "name": "earnings_forecast",
        "module": "data_collector.collectors.earnings_forecast",
        "required": False,
        "description": "最新业绩预告（预增/预减/扭亏/首亏）",
    },
    {
        "name": "lockup_expiry",
        "module": "data_collector.collectors.lockup_expiry",
        "required": False,
        "description": "限售解禁明细（解禁日期/解禁占比）",
    },
]
