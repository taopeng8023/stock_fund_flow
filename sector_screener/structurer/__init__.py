"""
趋势结构分析模块 — 从日线 OHLCV 数据提取趋势结构信号

提供:
- price_loader: 双 API (腾讯+新浪) 日线数据加载
- indicators: MACD/RSI/Bollinger/ATR/EMA 等经典 TA 指标
- trend_classify: 趋势方向+强度判定
- position: 中枢检测 + 结构位置判定
- divergence: MACD/RSI/量价背离检测
- scorer: 综合结构评分 (0-1)
"""
