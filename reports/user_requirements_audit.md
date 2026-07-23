# 用户核心需求达成度审计

**日期**: 2026-07-22
**审计人**: 系统
**审计目标**: 评审四条核心需求在 TradingAgents 代码中是否达标

---

## 需求 1：预测系统

> "每天收盘后执行，给出第二天交易规则，第二天我盘中自己盯盘执行"

### 达标情况：⚠️ 7/10 — 框架完备，数据对齐和自动化缺失

| 子项 | 状态 | 说明 |
|------|------|------|
| 分析产生规则 | ✅ | PM 通过 `propagate()` → `trading_rules_structured` → `TradingRule` 输出含 condition_str / action / priority 的规则 |
| 规则可人工阅读 | ✅ | 每条规则有 `name`、`source_sentence`、`trigger_sql`（如 `close < MA(close,200)`），可打印为表格 |
| 分析用数据截止到昨天 | ❌ | 当前 `propagate("000423", "2026-07-22")` → agents 内部 `end_date="2026-07-22"` → 能拿到 7/22 的 OHLCV（收盘后数据）→ **多看了 1 天**。用户要求 T-1 收盘后分析，应显式传入前一个交易日 |
| 每日自动运行 | ❌ | CLI、Web 面板都是手动触发。无 cron/APScheduler 每日执行机制 |
| quick 分析过期天数 | ❌ | 当前 `decision_stale_days=15`，用户要求 25 天 |
| 规则覆盖指标 | ✅ | 规则条件含 `close/open/high/low/volume/MA/RSI/MACD/BOLL/KDJ` 等所有常用指标 |
| 盘中手动执行 | ✅ | 规则输出可导出为表格/文本，用户在盯盘软件中对照执行 |

### 需要修的点

1. **数据窗口对齐**：回测 L1 触发时传入前一个交易日的日期（`trade_date - 1`），使 live 和 backtest 的 agents 看到相同的数据截止点
2. **自动化**：新增调度脚本 `run_daily_analysis.sh`/`prediction_daemon.py`
3. **默认 stale_days**：`BacktestConfig.decision_stale_days` 从 15 改为 25

---

## 需求 2：回测系统

> "回测逻辑与预测一致，T-1 收盘后分析，T/T+1...按收盘价执行，规则覆盖量价均线基本面"

### 达标情况：⚠️ 8/10 — 核心逻辑完整，执行时机和 stale_days 需调

| 子项 | 状态 | 说明 |
|------|------|------|
| full 分析在新季报发布日 | ✅ | D2 v2 修复后，`_new_quarterly_report_available()` 用 baostock 真实 pubDate 检测，财报真正发布日触发 full |
| quick 分析在超时/价格变化 | ⚠️ | `_should_trigger_l1()` 已支持 `stale_days` 和 `price_change > threshold`。但 stale_days=15 不是用户要的 25 |
| T-1 收盘后分析 | ❌ | 当前回测中 L1 用 `date_str`（当天），不是前一天。见需求 1 的同源问题 |
| 按收盘价顺序执行 | ⚠️ | 回测用 `row["close"]` 判断条件并成交，有 close-price 前视。次日 open 方案已在 `reports/next_day_open_execution_spec.md` |
| 规则覆盖量价均线基本面 | ✅ | `eval_condition` 的 context 含 annual_*/quarter_* 全部 129 个 FA 指标 + 全部 OHLCV 字段 |
| 逻辑一致性 | ❌ | 需求 1 + 需求 2 的数据窗口不一致（见上），修完数据对齐后才一致 |

### 需要修的点

1. **回测 L1 数据窗口**：L1 触发时传入 `analysis_date = previous_trading_day(date_str)`
2. **stale_days**：15 → 25
3. **执行价**：规则类交易改为次日 open（已有方案 `reports/next_day_open_execution_spec.md`）

### 回测一致性总结

```
修完后:
  预测系统 7/22 收盘后 → propagate(7/21) → 基于 7/21 数据出规则 → 7/23 执行
  回测系统 7/22 循环   → propagate(7/21) → 基于 7/21 数据出规则 → 7/22 模拟执行
                                                                    ↑
                                          回测比 live 早 1 天执行（正常，回测能"穿越"）
                                          但分析基于相同数据窗口 → 一致 ✅
```

---

## 需求 3：大师理论注入

> "每个角色可注入大师理论或用户自定义 prompt，自定义优先于备选"

### 达标情况：✅ 10/10 — 已完美实现

| 子项 | 状态 | 说明 |
|------|------|------|
| 多大师可选 | ✅ | 28 位大师 YAML（Buffett/Graham/Livermore/张磊...），每个含 compatible_roles |
| 每个角色独立分配大师 | ✅ | `master_config = {role: master_id}` 字典，12 个角色可各自分配不同大师 |
| 自定义理论优先 | ✅ | `custom_theory_config[role]` 优先级高于 `master_config[role]` |
| 用户无想法时用大师兜底 | ✅ | `master_config[role]` 设为 `"default"` = 无注入（默认）；设为大师名 = 注入大师方法论 |
| prompt 格式清晰 | ✅ | 注入块标记 `=== 方法论注入 ===`，LLM 能明确区分角色定位和附加理论 |
| 行业预设 | ✅ | 10 个行业（科技/新能源/消费/医药/金融...）各有预设角色-大师配对 |
| Web 面板支持 | ✅ | 圆桌拖拽座位系统，每个座位可拖入大师或填入自定义理论 |
| 12 个角色全覆盖 | ✅ | 全部 analysis/researchers/debaters/managers/trader 12 个角色均接入 |
| 可让某角色侧重某指标 | ✅ | 用户在 custom_theory 写"重点关注 ROE 和自由现金流" → LLM 在分析时会侧重 |

### 无需修改

这是四条需求中唯一已完全达标的。

---

## 需求 4：用户工作流

> "先用回测调试出可行理论，再用预测系统指导买卖"

### 达标情况：✅ 8/10 — 流程通畅，需修完需求 1+2 的对齐问题

| 子项 | 状态 | 说明 |
|------|------|------|
| 回测可测试策略 | ✅ | `run_backtest.py` / `backtest_hybrid.py` 支持指定参数跑完整回测 |
| 大师理论可切换 | ✅ | 改 `master_config` 或 `custom_theory_config` 即可切换，无需改代码 |
| 回测结果可视化 | ✅ | Excel 报告含 Sharpe/MaxDD/胜率 + 每日状态 + 交易记录 |
| 预测系统输出可执行 | ✅ | L1 输出结构化规则 → 人工执行 |
| 回测 = 预测逻辑一致 | ❌ | 需先修完需求 1+2 的对齐问题 |

### 典型工作流（修完后）

```
1. 用户设 custom_theory = "重点关注低 PE + 高 ROE + MA200 趋势"
2. 跑回测 backtest_hybrid.py --start 2024-01-01 --end 2025-12-31
3. 看回测报告 Excel → Sharpe 1.2, MaxDD -18% → 可行
4. 每天 15:05 自动跑分析 → 输出规则 → 次日盯盘执行
```

---

## 总结

| 需求 | 达成度 | 关键缺口 |
|------|--------|---------|
| ① 预测系统 | ⚠️ 7/10 | 数据窗口差 1 天 + 无自动化 + stale_day 默认值不对 |
| ② 回测系统 | ⚠️ 8/10 | 数据窗口差 1 天 + stale_day 默认值不对 + close-price 前视 |
| ③ 大师注入 | ✅ 10/10 | 无 |
| ④ 用户工作流 | ✅ 8/10 | 需修完 ①+② 自动一致 |

### 修复排期（按依赖关系）

| 顺序 | 修什么 | 文件 | 工作量 |
|------|--------|------|--------|
| 1 | stale_days: 15 → 25 | `backtest/models.py:173` | 1 行 |
| 2 | 新增 `_get_prev_trading_day()` | `backtest_hybrid.py` | ~10 行 |
| 3 | 回测 L1 触发用 `analysis_date = prev_day(date_str)` | `backtest_hybrid.py:699-739` | ~3 行 |
| 4 | 预测脚本 `run_daily_prediction.py` | 新文件 | ~50 行 |
| 5 | L2 次日 open 成交（已有方案） | `execution_engine.py` | ~35 行 |

---

*注: 需求 ③（大师注入）已经完美，不需要任何代码改动。需求 ④ 的流程体验在修完 ①+② 后自然成立。*
