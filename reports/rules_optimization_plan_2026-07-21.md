# 交易规则优化点梳理

**日期**: 2026-07-21
**来源**: 2026-07-21 全链路代码审计 + 用户讨论

---

## 总览

| 优先级 | 数量 | 类型 |
|--------|------|------|
| 高 | 5 | 安全/功能缺陷 |
| 中 | 10 | 语义/健壮性 |
| 低 | 5 | 设计/监控 |

---

## 一、高优先级（安全 / 功能缺陷，不修可能导致回测结果不可信）

### 1.1 eval() 执行 LLM 字符串 → 需替换为 AST 安全解析器

- **位置**: `backtest/trading_rules.py:260`
- **问题**: `eval(condition_clean, {"__builtins__": {}}, context)` 清空 `__builtins__` 挡不住属性链逃逸（`().__class__.__bases__[0].__subclasses__()` 可达 `os.system`）和 DoS（`9**9**9` 卡死 CPU）
- **现状**: 已有完整的安全解析器 `backtest/rule_expression.py`（839 行，AST 白名单），但在生产路径闲置未用
- **修复**: `eval_condition()` 改用 `RuleExpression.evaluate(row_dict)`

### 1.2 RATING_REEVAL 应无条件触发复评，而非绑死"新季报"

- **位置**: `backtest_hybrid.py:814-828`
- **问题**: RATING_REEVAL 触发后检查 `report_period` 是否变化 → 没变化就跳过 → 写死日志 "skipping (no new fundamentals)"。这与 RATING_REEVAL 的设计初衷矛盾——"市场条件变化到需要重新评估"不应等价于"新财报到了"
- **修复**: 去掉 `report_period` 判前判断，RATING_REEVAL 触发时无条件设 `force_decision_next_day = True`

### 1.3 15 天过期后应重新跑全量 Agent 管线，但基本面数据可复用缓存

- **位置**: `backtest_hybrid.py:747`
- **问题**: `stale_days=15` 到期 → 调用 `run_quick_analysis`（仅 market）→ 基本面规则（PE/ROE 阈值）90 天才更新一次
- **设计**:
  - 每 15 天：重新跑**全部 Agent**（bull/bear/debate/risk/PM）生成新的辩论和交易规则
  - 技术面分析师：**始终重跑**（OHLCV 每日变化）
  - 基本面分析师：**仅在新季报/年报发布后重跑**，否则复用上次缓存的分析报告和 `fundamentals_structured` 数据
  - 下游 agents 拿到：新鲜技术面报告 + 缓存/当前基本面报告 → 在最新价格水平上重新辩论
- **收益**: 不浪费 LLM token 在"无新数据的基本面分析"上，但保证了整体决策是基于最新价格和技术面指标的，旧规则被全面替换

### 1.4 CIRCUIT_BREAK 在 PM prompt 中不存在 → LLM 不会生成

- **位置**: `tradingagents/agents/managers/portfolio_manager.py`（整个 prompt 没有 circuit_break 关键词）
- **问题**: 已实现的高优先级基本面熔断机制（无条件清仓），但 PM 完全不知道其存在 → 永远不会被触发
- **修复**: 在 PM prompt 的"最小必需规则"段落中增加 CIRCUIT_BREAK 条目，示例：`annual_roe < 5 → circuit_break`

### 1.5 规则触发后的执行 action_price=close → 日内前视

- **位置**: `backtest/execution_engine.py:95`
- **问题**: 收盘 SMA/RSI/MACD 收盘后才能得知，却以当日 close 成交。现实中早盘低开已跌破止损线时，不可能以昨日收盘价卖出
- **修复**: 规则类交易顺延次日开盘价成交（止损/止盈用盘中 low/high 触价路径已正确，无需改）

---

## 二、中优先级（语义 / 健壮性，不修可能扭曲回测结果）

### 2.1 sell_pct 百分比提取三处代码不同步

- **位置**: `execution_engine.py:387-398` vs `schemas.py:405-412` vs `portfolio_manager.py:190-192` vs `decision_engine.py:538-544`
- **问题**: 只有 `execution_engine.py` 的 `_extract_pct_from_text()` 正确处理"降至→1-pct"语义。上游 `schemas.py`/`portfolio_manager.py`/`decision_engine.py` 仍用 `re.search(r'(\d+)%', …)` 无差别取第一个 `\d+%` → 上游传来的 `pct` 值可能错误
- **修复**: 统一提取逻辑到一个公共函数（如 `_parse_rule_pct(text, action) → float`），上下游共用

### 2.2 基本面字段缺值时规则静默失效，无日志区分

- **位置**: `backtest/trading_rules.py:35-39`（`_g` 函数：None → NaN → 比较恒 False）
- **问题**: PM 写了 `quarter_interest_coverage < 2`，但 L1 季报路径没有计算这个字段 → 值是 NaN → 条件恒 False → 规则永不触发，日志只有一条 `eval_condition` 不区分"条件不满足"和"数据不可用"
- **修复**: 在 `_check_trading_rules` 开头，遍历所有规则，预检每个条件的 `trigger_sql` 引用了哪些字段，标记缺失字段 → 日志告警 + 回测报告统计

### 2.3 首次触发即返回 → 同优先级多规则互相屏蔽

- **位置**: `backtest/execution_engine.py:270`（`return` 在第一个触发规则处）
- **问题**: 若 CIRCUIT_BREAK（P0）和 STOP_LOSS（P1）同日触发 → 当前因优先级不同 OK。但若两条 BUY_ADD 同日触发，只有第一条执行 → 第二条被跳过 → PM 设计的"分批次入场"计划仅执行了一半
- **修复**: 方案 A（保守）— 增加 `max_triggers: int` 配置；方案 B（准确）— 允许同日触发多条规则，按优先级顺序执行，上限 N 条

### 2.4 当前持仓状态以自由文本传入 PM → 应结构化

- **位置**: `tradingagents/graph/trading_graph.py` → `state["position_state"]`
- **问题**: 当前 `position_state` 是字符串（如 `"shares=0, cash=100000"`）→ PM 看到的只是一段文本，无法结构化解构。修复团队已加了注入逻辑（1-A），但格式太粗糙
- **修复**: 结构化为 `{"shares": 0, "avg_cost": 0, "current_price": 0, "pnl_pct": 0, "cash": 100000, "days_held": 0}`，在 PM prompt 中以格式化表格呈现

### 2.5 entry_price 不含买入侧成本 → pnl 偏乐观

- **位置**: `backtest/execution_engine.py:841` + `:893-894`
- **问题**: `TradeRecord.entry_price = price`（裸成交价）。`pnl = (price - entry_price) * shares - 卖出侧成本` → 买入侧佣金/滑点/过户费从未摊入成本 → pnl_pct 系统性偏高
- **修复**: entry_price 改为 `(gross_value + slippage + commission + transfer_fee) / shares` 的加权均价

### 2.6 规则生命周期：缺少 expires_after_days 机制

- **位置**: `backtest/trading_rules.py`（TradingRule 数据类）
- **问题**: 一条季度决策产生的止损规则（如 `close < 48.50`），在股票涨到 80 后仍然有效。虽会被新决策替换，但如果新决策恰好没有覆盖，老规则可能活 90 天
- **修复**: `TradingRule` 加 `since_date: str` + `expires_after_days: int` 字段，`_check_trading_rules` 开头自动禁用过期规则

### 2.7 规则反序列化后 source_sentence 丢失

- **位置**: `backtest_hybrid.py:383-394`（`_load_cache` 手动构造代码）
- **问题**: 缓存命中时 `source_sentence=""` → `SELL_PCT` 缺 pct 时的文本提取兜底失效 → 只能走默认 30%
- **修复**: 手动构造代码中补 `source_sentence=r.get("source_sentence", "")`

### 2.8 fa_metrics 季报路径需验证全部字段覆盖

- **位置**: `backtest/fa_cache.py` + `tradingagents/l1/analyzer_l1_enhanced_complete.py`
- **问题**: `interest_coverage`/`cash_coverage` 等字段在 L1 年报路径肯定计算，季报路径未必（季度报告数据粒度不如年报）。如果 L1 季报结果缺这些键 → fa_metrics 无对应 `quarter_*` 字段 → PM 写了规则但永不触发
- **修复**: 打印一份"季度分析实际产出的字段清单"与 PM prompt 的字段列表做 diff，缺失的要么补（计算），要么在 prompt 中标注"仅年报可用"

### 2.9 _get_limit_pct ST 检测依赖纯数字 symbol 永不触发

- **位置**: `backtest/execution_engine.py:937-941`
- **问题**: `if 'ST' in s` — 但 `self.config.symbol` 是 `"600519"`（纯数字），永不含字母。所有股票走 10% 路径，ST 分支死代码
- **修复**: 在 DataLayer 初始化时查询 baostock `query_stock_basic` 获取 is_st 标记；或从 stock_name（如 "*ST 东阿"）判断

### 2.10 条件中的 NOT 操作符在 NaN 下语义反转

- **位置**: `backtest/trading_rules.py:130` → `condition_clean = condition_clean.replace('NOT', 'not')`
- **问题**: `NOT (close < MA200)` → 缺数据时 MA200=NaN → `close < NaN` = False → `not False` = True → **误触发**。与用户意图相反——"数据不可用时不动作"的正确语义是 NaN 传播到整个表达式
- **修复**: eval 前独立处理 NOT：先求值内层 → NaN 时整条规则返回 False（而非求反）

---

## 三、低优先级（设计 / 监控 / 性能）

### 3.1 规则评估缺少性能保护

- **问题**: PM 输出 50+ 条规则时，每天 50 次 `eval()` 调用，无上限、无缓存、无性能计时
- **修复**: 加 `MAX_RULES_PER_DECISION` 限制（如 30）；按"当日是否已经为 False"跳过短期重复评估

### 3.2 规则触发日志缺少结构化统计

- **问题**: 无法回答"这次回测中，规则 X 触发了多少次？规则 Y 从未触发是为什么？"
- **修复**: 在 DailyState 或 execution_log 中记录每条规则的 `evaluated`/`triggered` 计数；回测报告加"规则命中率"统计

### 3.3 PM prompt 字段列表与 eval_condition 上下文自动同步

- **问题**: PM prompt 和 eval_condition 各维护一份 `annual_*`/`quarter_*` 字段清单。新增一个指标时容易漏改一端
- **修复**: 从 `fa_cache.py` 导出标准字段列表，PM prompt 和 eval_condition 都引用同一份

### 3.4 规则解析失败应有降级策略

- **问题**: `convert_structured_rules` 中单条规则解析失败 → 跳过该条 → 整组规则缺失。无"解析失败条数"监控
- **修复**: 记录解析失败计数，超过阈值（如 30% 规则解析失败）→ 标记整个决策无效 → 回退到默认风控兜底

### 3.5 eval 条件白名单增强

- **问题**: 当前 `_CONTEXT_FIELDS` 只暴露字段名，不暴露列/类型信息。如果 PM 写了 `annual_roe > "good"`（类型错误），eval 直接抛 TypeError
- **修复**: 在 eval 前对条件做类型一致性检查（数值字段只允许数值比较）

---

## 四、修复建议排期

| 批次 | 内容 | 预估工作量 |
|------|------|-----------|
| **第一批** | 1.1(安全解析器) + 1.2(RATING_REEVAL) + 1.3(15天full分析) + 1.4(CIRCUIT_BREAK prompt) | 1-2天 |
| **第二批** | 2.1(pct统一) + 2.2(缺值日志) + 2.3(多规则触发) + 2.4(持仓结构化) | 1-2天 |
| **第三批** | 1.5(次日开盘成交) + 2.5(entry价格) + 2.6(规则过期) + 2.7(反序列化) | 1天 |
| **第四批** | 2.8(fa字段覆盖) + 2.9(ST检测) + 2.10(NOT/NaN) | 1天 |
| **第五批** | 3.1-3.5(监控/工具) | 按需 |
