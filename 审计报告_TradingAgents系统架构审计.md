# TradingAgents 系统架构审计报告

> 审计日期：2026-07-15 | 审计范围：全代码库（主程序、回测系统、调优系统、Web 前后端）

---

## 一、核心架构问题：Web 后端 ≠ 主程序逻辑

**这是本次审计发现的最严重架构背离。**

你的设计要求是：**"Web 的后端逻辑应该和主程序完全相同，只不过多了可展示的界面。"** 但实际情况是，Web 后端与主程序是两套**完全独立、互不相同的实现**。

### 1.1 执行框架完全不同

| 维度 | 主程序（main.py / CLI） | Web 后端（agent_service.py） |
|------|----------------------|---------------------------|
| **执行引擎** | LangGraph `StateGraph(AgentState)` 编译的状态图 | 简单的 `asyncio.gather` + 顺序 `await` |
| **辩论轮次** | 可配置多轮循环（Bull↔Bear, 三方 Risk） | 固定单轮，无循环 |
| **工具调用** | Agent 通过 `ToolNode` 在图中调用 12 个数据工具 | 数据从 Python 侧预取为 Markdown 文本注入 prompt |
| **条件路由** | `ConditionalLogic` 控制多轮辩论和工具重试 | 硬编码顺序，无路由 |
| **检查点/断线续跑** | 支持 SqliteSaver checkpoint | 无 |

### 1.2 缺失的 Agent

Web 端比主程序少了以下关键 Agent：

| 缺失的 Agent | 在主程序中的作用 | 对决策的影响 |
|------------|---------------|------------|
| **Sentiment Analyst** | 分析社交媒体情绪 | 失去市场情绪维度 |
| **News Analyst** | 分析新闻、全球宏观、内部人交易 | 失去新闻和事件驱动信息 |
| **Research Manager** | 综合 Bull/Bear 辩论，给出结构化研究计划 | Bull/Bear 辩论后无"裁判"，直接跳到 Risk |
| **Trader（独立）** | 基于研究团队结论制定交易计划 | Web 用修改 prompt 的 Market Analyst 充当做市商 |

### 1.3 缺失的系统功能

| 功能 | 主程序 | Web |
|------|--------|-----|
| **L1 深度财务分析**（50+ 指标、杜邦拆解、现金流质量等） | ✅ | ❌ |
| **数值验证**（交叉校验 LLM 引用的财务数字） | ✅ | ❌ |
| **结构化 PM 输出**（PortfolioDecision + trading_rules + trigger_sql） | ✅ | ❌（自由文本） |
| **记忆/反思系统**（历史决策学习） | ✅ | ❌ |
| **双层 LLM 模型**（deep_think + quick_think 分角色） | ✅ | ❌（所有角色用同一个模型） |
| **可配置大师风格**（27 位投资大师 YAML 配置） | ✅ | ❌（硬编码简化版） |

### 1.4 配置漂移

Web 忽略了主程序的以下配置：
- `max_debate_rounds` / `max_risk_discuss_rounds`（永远固定 1 轮）
- `output_language`（永远硬编码中文）
- `master_config`（使用自己的硬编码版本）
- `memory_log_*`（完全不记录）
- `deep_think_llm` vs `quick_think_llm`（所有角色硬编码 `deepseek-chat`）
- `temperature`（硬编码 0，主程序使用默认值）

### 1.5 数据源重复

Web 在 `web_app/backend/services/stock_service.py` 中有自己独立的数据获取代码，直接调用原始 akshare API（`ak.stock_financial_abstract_ths`），绕过了主程序 `tradingagents/dataflows/akshare_data.py` 中的抽象工具函数及其日期过滤、缓存和结构化处理逻辑。

### 🔧 修复建议

**方案 A（推荐）**：将 `TradingAgentsGraph.propagate()` 包装为异步服务，Web 后端直接调用它并流式推送中间结果。这是唯一能保证"相同逻辑"的方式。

**方案 B**：至少将 Web 的 `RoundtableSession` 替换为基于 `TradingAgentsGraph` 的调用，保持 Agent 团队、辩论轮次、数据结构完全一致。

---

## 二、回测系统 Bug 清单（共 17 个）

### 🔴 CRITICAL（严重）— 5 个

| # | 问题 | 文件 | 影响 |
|---|------|------|------|
| **BUG-1** | `_llm_injected` 是局部变量而非实例属性，导致 LLM 辅助规则解析永远被禁用 | `backtest/decision_engine.py:77` | 复杂场景下规则提取失败时无 LLM 兜底 |
| **BUG-2** | `_safe_price_condition()` 在回退路径中被零参数调用，止损/止盈默认值从不生效 | `backtest/decision_engine.py:461,479` | **持仓裸奔无止损保护** |
| **BUG-3** | `eval_condition` 中字段别名映射与 eval 上下文变量名不一致（换手率 `turn` vs `turnover`，KDJ `kdjk` vs `kdj_k`） | `backtest/trading_rules.py:38,60,267,284` | 规则静默返回 False，看似触发实际未执行 |
| **BUG-4** | FIFO 成本计算用平均价而非批次价扣减 | `backtest/execution_engine.py:913-930` | PnL 计算精度偏差 |
| **BUG-5** | Hybrid 引擎 `convert_structured_rules` 缺少 5 个 action 映射（`rating_adjustment`、`sell_all` 等） | `backtest_hybrid.py:95-111` | 部分规则类型无法执行 |

### 🟠 HIGH（高优先级）— 7 个

| # | 问题 | 文件 | 影响 |
|---|------|------|------|
| **BUG-6** | Hybrid 引擎决策 `stop_loss=0.0, take_profit=0.0`，完全没有止损/止盈 | `backtest_hybrid.py:170-173` | **Hybrid 模式所有持仓无止损** |
| **BUG-7** | Hybrid 和主引擎对 ALERT_ONLY 触发后的处理不一致 | `backtest_hybrid.py:727-748` vs `backtest_engine.py:333-336` | Hybrid 模式价格突破关键位后不触发复评 |
| **BUG-8** | `trailing_stop_pct` 默认值不一致（dataclass 12% vs 反序列化 8%） | `backtest/models.py:29` vs `backtest/decision_engine.py:735` | 新旧缓存数据移动止损参数不同 |
| **BUG-9** | 移动止损用收盘价而非最高价计算峰值 | `backtest/execution_engine.py:712` | 低估真实峰值，移动止损触发过晚 |
| **BUG-10** | 涨跌停只硬编码 9.9%，不区分主板/创业板/科创板/ST | `backtest/execution_engine.py:884-896` | 创业板（20%）和科创板（20%）漏判涨跌停 |
| **BUG-11** | `_daily_state_to_dict` 遗漏 `triggered_rules` 和 `alert_triggered` 字段 | `backtest/backtest_engine.py:544-557` | 审计者无法知道每天触发了哪些规则 |
| **BUG-12** | 决策与执行在同一天（潜在 Look-Ahead Bias） | `backtest/backtest_engine.py:235-253` | 可能高估策略表现 |

### 🟡 MEDIUM（中等）— 5 个

| # | 问题 | 文件 |
|---|------|------|
| **BUG-13** | `convert_structured_rules` 在 DecisionEngine 和 Hybrid 中完全重复（~70 行），同步维护风险 | `decision_engine.py:489-560` / `hybrid.py:78-149` |
| **BUG-14** | `_extract_pct_from_text()` 在 DecisionEngine 和 ExecutionEngine 中完全重复 | 同上两个文件 |
| **BUG-15** | `_config_to_dict` 遗漏交易成本配置字段 | `backtest/backtest_engine.py:573-585` |
| **BUG-16** | `create_initial_state` 未初始化 `trading_rules_structured` | `tradingagents/graph/propagation.py:18-64` |
| **BUG-17** | 数据源全部失败时异常传播路径不清晰 | `backtest/data_layer.py:220-225` |

### 📊 回测系统架构问题

**三个独立的回测实现**，不共享核心逻辑：

| 实现 | 入口 | 使用的引擎 |
|------|------|-----------|
| 实现 1 | `run_backtest.py` | `BacktestEngine` → `DecisionEngine` + `ExecutionEngine` |
| 实现 2 | `backtest_hybrid.py` | `HybridBacktestEngine` → `L1Analyzer` + `ExecutionEngine` |
| 实现 3 | `run_batch_backtest.py` | **完全内联**，不调用任何引擎 |

对实现 1 的 Bug 修复不会自动传播到实现 2 和 3。特别是实现 3（`run_batch_backtest.py`）有自己完整的 OHLCV 获取、指标计算、规则评估、投资组合管理逻辑 —— 335 行代码完全独立于共享基础设施。

---

## 三、调优系统 Bug 清单（共 6 个）

### 🔴 CRITICAL — 3 个

| # | 问题 | 文件 | 影响 |
|---|------|------|------|
| **OPT-1** | `HarnessOptimizer.run()` 在所有路径中返回 None，最优参数从未被选择 | `tools/harness_optimizer.py:487-535` | **整个调优管道失效** — CLI 总是输出 "Optimization failed" 并 `exit(1)` |
| **OPT-2** | 调优器使用 `deepseek-reasoner`/`deepseek-chat`，实际回测使用 `deepseek-v4-pro`/`deepseek-v4-flash` | `backtest_hybrid.py:522-524` vs `backtest/models.py:161-163` | **调优结果对实际回测无效** — 不同模型产生不同结果 |
| **OPT-3** | `_run_one_backtest` 异常路径不恢复被修改的类变量 | `tools/harness_optimizer.py:178-242` | 类默认值泄漏到后续试验，污染结果 |

### 🟠 HIGH — 2 个

| # | 问题 | 影响 |
|---|------|------|
| **OPT-4** | `master_aggressive`、`master_conservative`、`master_neutral` 从未被采样 | 这些角色的最优大师永远找不出来 |
| **OPT-5** | 并行模式下不保存增量报告，进程崩溃数据全丢 | 长时间运行无容错 |

### 🟡 MEDIUM — 1 个

| # | 问题 |
|---|------|
| **OPT-6** | 死代码/重构遗留（`futures = {}` 缩进在 `else` 块内，但 `def _run_parallel` 是类级别） |

---

## 四、内存/日志系统 Bug

### 🔴 CRITICAL — 1 个

| # | 问题 | 影响 |
|---|------|------|
| **MEM-1** | **所有回测运行共享同一个内存日志文件**（`trading_memory.md`）。不同股票的回测会交叉污染历史决策、反思和 past_context。并发运行会导致数据竞争。 | 决策基于其他股票的历史，产生错误的"学习" |

### 🟠 HIGH — 1 个

| # | 问题 | 影响 |
|---|------|------|
| **MEM-2** | 内存日志的待处理条目在不同回测框架间被静默解析。回测重跑会拾取并解析不相关执行的历史条目。 | 跨运行的反射假设被混合 |

---

## 五、安全漏洞（Web 端）

### 🔴 CRITICAL — 5 个

| # | 问题 | 位置 |
|---|------|------|
| **SEC-1** | **无任何认证机制** — 任何人知道 URL 即可访问和触发分析 | 全局 |
| **SEC-2** | **可预测的会话 ID**（`uuid4()[:8]`，仅 4 字节熵），可遍历读取其他用户的会话 | `agent_service.py:470` |
| **SEC-3** | **会话零隔离** — 知道 session_id 即可读取、修改席位、查看报告 | `agent_service.py` |
| **SEC-4** | **SSE 错误消息暴露完整 Python 回溯**（文件路径、行号、内部细节） | `routes/analyze.py:132` |
| **SEC-5** | **无会话过期/清理** — `_sessions` 全局字典永不清除，必然内存泄漏 | `agent_service.py:470` |

### 🟠 HIGH — 4 个

| # | 问题 |
|---|------|
| **SEC-6** | **CORS 配置致命错误** — `allow_origins=["*"]` 与 `allow_credentials=True` 互斥，浏览器拒绝此组合 |
| **SEC-7** | **无 CSRF 保护** — POST 请求可跨站触发 |
| **SEC-8** | **无速率限制** — 可无限创建会话和流（触发昂贵的 LLM 调用） |
| **SEC-9** | API 返回原始异常消息给客户端，暴露内部错误细节 |

### 🟡 MEDIUM — 4 个

| # | 问题 |
|---|------|
| **SEC-10** | 股票代码直接插入 LLM prompt，可能提示注入 |
| **SEC-11** | `/test` 调试路由暴露在生产环境 |
| **SEC-12** | 无请求体大小限制 |
| **SEC-13** | 无安全 HTTP 头（HSTS、CSP、X-Frame-Options 等） |

### 其他 Web 问题

| 问题 |
|------|
| **数据重复获取** — `get_fundamentals_data` 和 `get_technical_data` 被调用了两次（SSE 路由一次 + 前端预取一次） |
| **无 SSE 重连逻辑** — 连接断开后用户必须手动重试 |
| **阶段失败不中止** — 即使早期阶段产生垃圾内容，管道也总是运行到结束 |
| **全局错误处理会替换整个 React 应用** — 没有恢复路径 |

---

## 六、配置系统问题

| # | 问题 |
|---|------|
| **CFG-1** | 每个入口点通过 `DEFAULT_CONFIG.copy()` 独立创建配置，无单例管理对象 |
| **CFG-2** | Hybrid 引擎硬编码模型名称，不可通过 CLI 配置 |
| **CFG-3** | Web 完全忽略 `DEFAULT_CONFIG` 中的大部分配置项 |
| **CFG-4** | 三个回测入口（`run_backtest.py`、`backtest_hybrid.py`、`run_batch_backtest.py`）各有一套 CLI 参数解析，无共享框架 |
| **CFG-5** | `run_batch_backtest.py` 猴子补丁 `yfinance` 为假模块（`sys.modules["yfinance"] = _ymod`），污染全局 Python 进程状态 |

---

## 七、代码重复与维护风险

| 重复代码 | 出现次数 | 位置 |
|---------|---------|------|
| `convert_structured_rules()` | 2 处 | `decision_engine.py` + `backtest_hybrid.py` |
| `_extract_pct_from_text()` | 2 处 | `decision_engine.py` + `execution_engine.py` |
| OHLCV 获取逻辑 | 3 处 | `DataLayer` + `akshare_data.py` + `run_batch_backtest.py`（内联） |
| 技术指标计算 | 3 处 | `DataLayer` + `agent_utils.py` + `run_batch_backtest.py`（内联） |
| 信号提取（rating 解析） | 2 处 | `signal_processing.py` + `agent_service.py` |
| 大师配置 | 2 处 | YAML loader + Web 硬编码 |

---

## 八、按影响面的修复优先级

### 🚨 必须立即修复（影响系统正确性）

1. **Web 与主程序逻辑统一**（第三节）— 当前 Web 产生不同决策
2. **OPT-1** — 优化器 `run()` 永远返回 None，整个调优系统白跑了
3. **OPT-2** — 调优用的模型和实际回测用的模型不同，调优结果无效
4. **BUG-2** — 回退路径无止损保护，持仓裸奔
5. **BUG-3** — 规则评估静默失败（字段别名不匹配）
6. **BUG-6** — Hybrid 模式完全无止损

### ⚡ 尽快修复（影响结果精度或可用性）

7. **MEM-1** — 跨股票内存日志污染
8. **CFG-5** — yfinance 猴子补丁污染全局状态
9. **BUG-1** — LLM 辅助解析失效
10. **BUG-5** — Hybrid 缺少 action 映射
11. **BUG-7** — Hybrid ALERT 行为不一致
12. **BUG-9** — 移动止损用收盘价非最高价

### 📋 计划修复（提升工程质量）

13. **SEC-1~SEC-13** — Web 安全漏洞
14. 代码重复合并（`convert_structured_rules`、`_extract_pct_from_text`、信号提取等）
15. 三套回测实现统一为一个
16. **BUG-10** — 涨跌停板块差异化
17. **BUG-4** — FIFO 成本计算

---

## 九、总结

你的系统存在**三个层面的背离**：

```
你期望的架构：
  主程序 ──(相同逻辑)──→ 回测系统 ──(参数扫描)──→ 调优系统
    │
    └──(相同逻辑+界面)──→ Web 后端

实际架构：
  主程序 (LangGraph 完整辩论图)
    ├── 回测系统 1 (DecisionEngine 包装)
    ├── 回测系统 2 (HybridBacktestEngine 独立实现)
    ├── 回测系统 3 (run_batch_backtest.py 完全内联)
    ├── 调优系统 (调用 HybridBacktestEngine，但 run() 返回 None)
    └── Web 后端 (完全重写，不调用主程序任何逻辑)
```

**最核心的三个问题**：
1. **Web 后端不是"主程序+界面"，而是完全独立的重写**，Agent 更少、辩论更浅、输出非结构化、无数据验证
2. **调优系统从不返回最优结果**（`run()` 永远返回 None），且使用的模型与实际回测不同
3. **三套回测实现互不共享核心逻辑**，Bug 修复不会自动传播
