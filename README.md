# TradingAgents Hybrid Backtest System

基于 LLM (DeepSeek) 的 A 股多 Agent 混合回测系统。季度 L1 基本面分析 + 每日 L2 规则执行。

## 快速开始

```bash
# 单只股票回测
.venv/bin/python backtest_hybrid.py \
    --symbol 000423 \
    --start 2026-01-01 \
    --end 2026-07-06 \
    --stale-days 15 \
    --output-dir backtest_results/my_run

# 指定日期跑 L1 分析（不跑完整回测）
.venv/bin/python backtest_hybrid.py \
    --symbol 000423 \
    --date 2026-04-01 \
    --l1-only \
    --output-dir backtest_results/l1_check

# 批量回测
for s in 000333 000423 000568 600887 601225 601318; do
    .venv/bin/python backtest_hybrid.py \
        --symbol $s --start 2026-01-01 --end 2026-07-06 \
        --stale-days 15 --output-dir backtest_results/multi/$s \
        > logs/${s}.log 2>&1 &
done
```

## 输出结构

```
backtest_results/<run_name>/
├── <symbol>/
│   ├── l1_analysis/          # 每次 L1 分析的规则 (JSON)
│   │   ├── 20260105_full.json
│   │   └── ...
│   ├── result_*.json         # 每日执行结果 + 权益曲线
│   └── summary.json          # 汇总指标
├── graph_results/<symbol>/   # LangGraph 完整状态日志
│   └── TradingAgentsStrategy_logs/
│       └── full_states_log_*.json  # 每份 PM 决策报告
└── l1_cache/                 # 季度分析缓存 (避免重复调用 LLM)
```

## 架构

```
backtest_hybrid.py (入口)
├── L0: 数据加载
│   └── backtest/data_layer.py
│       ├── baostock (A股 OHLCV, 主数据源)
│       ├── akshare (fallback)
│       └── stockstats (MA/RSI/MACD/BOLL 技术指标)
│
├── L1: 季度分析 (LLM Agent)
│   └── tradingagents/graph/trading_graph.py
│       ├── LangGraph 多 Agent 辩论
│       │   ├── Analysts (fundamentals/market/news/sentiment)
│       │   ├── Researchers (bull/bear)
│       │   ├── Risk Mgmt (aggressive/conservative/neutral)
│       │   └── Portfolio Manager → 最终决策 + 规则
│       └── PM 两阶段规则生成
│           ├── 阶段1: function_calling → PortfolioDecision
│           └── 阶段2: 精简 prompt 单独生成规则 (fallback)
│
└── L2: 每日规则执行
    └── backtest/execution_engine.py
        ├── _check_trading_rules()   # 复合规则检查 (按优先级)
        ├── _execute_rule_action()   # 执行买入/卖出
        └── daily_states_log         # 每日权益 + 技术指标
```

## 调度策略

```
触发条件                    优先级
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
季度首日 (Q1/Q2/Q3)         最高    全量分析 (fundamentals + market)
新季报发布 (baostock预加载) 高      全量分析
价格变动 > 10%              中      quick 分析 (market only)
决策过期 (stale=15d)        低      quick 分析
Alert 触发复评              按需    次日强制全量分析
```

## 规则体系

每条规则包含：`rule_type`, `action`, `trigger_sql`, `priority`, `pct`

```python
# 止损 (priority=90)
close < 45.00 → stop_loss (清仓)

# 减仓 (priority=75)
close < MA(close,50) AND RSI(14) < 35 → sell_pct(30%)

# 入场 (priority=40)
close >= 48.00 AND close <= 48.50 AND volume > MA(volume,20)*0.8 → buy_add(20%)
```

action 优先级: `stop_loss > sell_all > sell_pct > take_profit > rating_reeval > alert_only > buy_add`

## 关键设计

| 特性 | 实现 |
|------|------|
| 仓位风控 | 默认 10% 仓位, PM-Fallback 默认 30% pct |
| 小仓位 | `_calc_reduce_shares` 最少卖 100 股 |
| 新季报触发 | 启动时预加载 baostock 报告日, O(1) 每日查询 |
| pct 解析 | 从 markdown `THEN buy_add(40%)` 提取百分比 |
| 空仓保护 | 持仓=0 时跳过卖出规则检查 |
| 技术指标 | 每日注入 MA20/MA50/MA200/MACD/RSI14/BOLL |
| 跨期对比 | 禁止年报/季报数值直接比对 |
| Look-ahead 防护 | OHLCV/fundamental 均按 report_date 过滤 |

## 依赖

```bash
uv sync  # Python 3.13 + akshare/baostock/stockstats/langgraph/pandas
```

## 环境变量

```bash
export DEEPSEEK_API_KEY=sk-xxx    # DeepSeek API 密钥
```
