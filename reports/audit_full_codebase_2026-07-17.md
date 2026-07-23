# TradingAgents 全链路代码审计报告

**审计日期**: 2026-07-17
**审计范围**: 数据层 → Agent/LangGraph 层 → 决策引擎 → 规则引擎 → 执行引擎 → 回测主循环
**审计维度**: ① 前后端（分析端↔执行端）关联漏洞 ② 程序执行逻辑漏洞 ③ 不符合股票投资常识的漏洞
**方法**: 4 路并行深度代码走查（逐行阅读），关键 P0 结论已由主审计人二次验证代码原文

---

## 执行摘要

共发现 **41 项新问题**（不含此前已修复的已知问题）：**P0 × 12，P1 × 16，P2 × 13**。

最严重的结论是：**两条回测路径各存在一个"致命断点"**——

- `backtest_engine.py` 路径：`RuleParser.parse()` 签名不匹配，每次决策必然抛 TypeError 降级为 HOLD（E1）；
- `backtest_hybrid.py` 路径：`MA(close,200)` / `MA(volume,20)` 因预计算列缺失而退化为自比较（恒 False），PM prompt 强制推荐的核心趋势条件**全部静默失效**（A1）。

叠加"数值校验节点整体不可达"（G1）与"财报按会计期而非发布日过滤"（D2）两个 P0，当前回测结果与 PM 真实意图偏离严重，且含系统性前视偏差，**在修复前不应作为策略有效性的依据**。

---

## 第一部分：P0 — 使回测结果失真/失效的致命问题

### 【E1】RuleParser.parse() 签名不匹配 → 独立回测路径决策全部降级 HOLD

- **位置**: `backtest/trading_rules.py:604` vs `backtest/decision_engine.py:269-297`（4 处调用）
- **验证**: 已核实源码。签名为 `def parse(self, pm_text: str, **kwargs)`，只接受 1 个位置参数；调用为 `parse(pm_text, decision.price_cond, decision.direction, use_llm=False)` 传 3 个位置参数 → `TypeError` → 被 `run_decision_chain:247-250` 兜底捕获 → **每次决策返回 _default_decision（HOLD、零规则）**。
- **连带**: `__init__:77` 中 `_llm_injected = False` 漏写 `self.`，`:296` 引用 `self._llm_injected` 将 AttributeError（当前被 E1 掩盖，修 E1 后会暴露）。
- **修复**: `parse` 签名改为 `parse(self, pm_text, price_cond=None, direction=None, *, use_llm=False)`；同时修 `self._llm_injected`。

### 【A1】MA() 回退返回"当日原始值" → MA200/量能规则恒 False

- **位置**: `backtest/trading_rules.py:67-79` + `backtest/data_layer.py:258-265`
- **验证**: 已核实。data_layer 仅预计算 `close_5/20/50_sma`（context 还注册了 `ma60='close_60_sma'` 但该列同样没算）。`MA(close,200)` → `row.get('close_200_sma')` = None → 回退 `row.get('close')` → 返回**当日收盘价本身**：`close < MA(close,200)` 变成 `close < close` 恒 False。`MA(volume,20)` 不走 sma 分支，直接返回当日 volume：`volume > MA(volume,20)*1.5` 恒 False。
- **后果**: PM prompt（portfolio_manager.py:108,126）明确要求 LLM 用 MA200 止损、量能确认入场 —— 这些规则**永不触发**，回测实际由少数纯价格阈值规则+默认 trailing stop 驱动，与 PM 意图严重脱节。这是典型的"前端（PM 分析端）与后端（执行端）关联断裂"。
- **修复**: (1) data_layer 预计算 60/120/200 SMA 和 volume SMA；(2) MA() 回退分支改为用已注入的 `_df`/`_idx` 现场计算窗口均值；(3) 找不到列时返回 NaN 并告警，**绝不返回原始值**。

### 【A2】eval 上下文白名单与字段别名表不一致 → 规则静默死亡

- **位置**: `backtest/trading_rules.py:41-57`（_CONTEXT_FIELDS）vs `:244/:262/:321/:338`（FIELD_ALIAS_MAP）
- **问题**: `换手率→turn`（context 无 turn）、`KDJ_K值→kdjk`（context 无 kdjk；而 `kdj_k` 有注册但 row 实际列名是 `kdjk`，取到 NaN）、基本面别名 `商誉占比→annual_goodwill_ratio` 等均不在 context → NameError → `evaluate_all:509-514` 吞掉返回 False。规则**静默失效**，仅留一行 error 日志。
- **修复**: context 直接全量注入 `row_dict`（None→NaN），废弃手工白名单；对 NameError 单列统计并在回测报告中呈现"从未评估成功的规则清单"。

### 【G1】三个数值校验节点整体不可达 — Numeric Guard 第 2 层被旁路

- **位置**: `tradingagents/graph/conditional_logic.py:52-73` vs `setup.py:128-176`
- **验证**: 已核实。`should_continue_debate` 只返回 `"Research Manager"/"Bear Researcher"/"Bull Researcher"`，从不返回 path_map 中注册的 `"Numeric Check Bull/Bear"`；`should_continue_risk_analysis` 同理从不返回 `"Numeric Check Risk"`。`numeric_validation.py` 三个校验节点是**死节点**，宣称的三层数值防御第 2 层从未执行。
- **连带（P1-7）**: `numeric_violations` 字段全仓库无任何消费者——RM/PM prompt 不读、报告落盘不写；`build_guard_prompt`/`build_cross_type_warning` 定义了却无人调用。即使修复路由，违规结果仍 100% 被丢弃。
- **修复**: 辩论终止分支改为 `return "Numeric Check Bear"` / `"Numeric Check Risk"`（终止总发生在 Bear/Neutral 之后）；并在 RM/PM prompt 注入 `build_guard_prompt(state["numeric_violations"])`。

### 【D2】财报按"会计期截止日"而非"发布日"过滤 → 年报提前 2-3 个月可用

- **位置**: `tradingagents/dataflows/akshare_data.py:566-593`（_filter_by_report_date）、`tradingagents/l1/data_loader_fixed.py:184-198`（_filter_abstract_periods）
- **问题**: 过滤用报表第一列（会计期截止日，如 2025-12-31）与 cutoff 比较。回测运行在 2026-01-15 时，`2025-12-31 <= 2026-01-15` → 2025 年报被判定"已可用"，而实际发布日约 2026-03/04。**系统性前视 2-3 个月**，影响 get_fundamentals / get_balance_sheet / get_cashflow / L1 全部基本面分析。
- **修复**: 用 baostock `query_profit_data` 的 `pubDate`（`backtest/data_layer.py:363-417` 的 `get_latest_report_date` 已正确使用）建立"报告期→发布日"映射，过滤时按发布日判断可用性。

### 【D7】估值指标 PE 使用可能未发布的年报 EPS

- **位置**: `akshare_data.py:607-704`（_append_valuation_metrics，尤其 :636-642）
- **问题**: 直接搜索 `12-31` 结尾的最新年报行取 EPS 计算 PE，未对 `curr_date` 过滤 → 年初回测时 PE 用尚未发布的上年 EPS。
- **修复**: 对 fa_df 按发布日（或至少 curr_date）过滤后再取 EPS。

### 【D1】stockstats 清洗中 bfill() 用未来价格回填历史缺口

- **位置**: `tradingagents/dataflows/stockstats_utils.py:83`
- **验证**: 已核实 `data[price_cols] = data[price_cols].ffill().bfill()`，且发生在 `load_ohlcv` 的 `<= curr_date` 过滤**之前**（全量 5 年数据上执行）。停牌缺口会被未来价格回填，污染所有技术指标。
- **修复**: 删除 `.bfill()` 只保留 `ffill()`；或把日期过滤挪到清洗之前。

### 【D3】FA 缓存键不含分析日期 → 缓存中毒式前视

- **位置**: `backtest/decision_engine.py:777/:804`（`f"{symbol}_{report_period}"`）、`backtest/cache_manager.py:62-92` 同型
- **问题**: 较晚日期生成的完整 L1 结果，会被较早回测日期命中复用。多次回测/断点续跑场景必然发生。
- **修复**: 缓存键加入 analysis_date（或至少加入"该期报告发布日是否已过"判定）。

### 【M2】memory/reflection 链路泄露未来信息

- **位置**: `tradingagents/graph/trading_graph.py:294-316/377`、`tradingagents/agents/utils/memory.py:71-96`
- **问题**: ① `_resolve_pending_entries` 用 `entry_date + holding_days + 7` 的**未来行情**结算历史决策并生成 reflection；② `get_past_context()` 完全没有日期过滤，只按文件倒序取条目——多标的批量回测/重跑时，晚于当前模拟日的决策连同**已实现收益**被注入 PM prompt。叠加 memory 幂等检查只认 `| pending]`（memory.py:41-47），重跑同日会把"本题答案"喂给"本题重考"。
- **修复**: `get_past_context(ticker, as_of_date)` 增加日期过滤；回测模式只结算 `entry_date + holding_days <= 当前模拟日` 的条目；幂等检查放宽为同 (date, ticker) 前缀即跳过。

### 【X1】T+1 制度未强制执行

- **位置**: `backtest/execution_engine.py:49-175`
- **问题**: `PortfolioState` 无"今日买入不可卖"标记，buy_add 与 stop_loss 同日触发时当日买入当日卖出——A 股 T+1 下不可复现。
- **修复**: 增加 `shares_available_today` / `shares_settling` 字段，卖出量以可卖股数为上限。

### 【X2】涨跌停硬编码 ±9.9%，不区分板块；跌停板上仍可卖出且填价虚构

- **位置**: `execution_engine.py:884-896`（±9.9% 硬编码）；`:128-134/:314-316/:505`（跌停日 SELL 仍产出，填价 `max(stop_loss, open)`）；`:511-515`（止盈门控误用 `at_limit_down`，疑似复制粘贴错误）
- **问题**: ST ±5%、创业板/科创板 ±20% 全部误判；跌停封板日实际无法卖出，回测却以高于开盘的止损价成交——经典"纸上富贵"。
- **修复**: 按代码前缀/ST 标记确定 limit_pct；跌停日跳过卖出并记录"尝试失败"事件顺延次日；止盈门控改 `at_limit_up`。

### 【X6】前复权数据 vs PM 绝对价格阈值系统性错配

- **位置**: `backtest/data_layer.py:140`（adjustflag="2" 前复权）vs `execution_engine.py:311-317`（绝对价止损）
- **问题**: PM 决策时基于当时价格设绝对止损价；分红除权后前复权历史价格整体下移，绝对阈值条件"永远满足"或填价虚高（以不存在的价格成交）。且前复权按"今天"基点重算本身就隐含未来分红信息。
- **修复**: PM 绝对价在入执行层前转换为相对建仓价的百分比；或改用后复权数据。

### 【X9】HybridBacktestEngine 硬编码 stop_loss=0 → 兜底风控不存在

- **位置**: `backtest_hybrid.py:170-172` vs `execution_engine.py:502-504`
- **问题**: `build_weekly_decision_from_rules` 固定 `stop_loss=0.0, take_profit=0.0`，注释称由"ExecutionEngine 的 _safe_price_condition 兜底"——**该方法在 DecisionEngine 里，ExecutionEngine 根本没有**。PM 一旦遗漏止损规则，持仓零风控裸奔（仅剩默认 trailing 0.12）。
- **修复**: 构建 WeeklyDecision 时检查是否含 stop_loss 规则，缺失时以 `current_price * 0.92` 生成兜底。

---

## 第二部分：P1 — 显著扭曲结果的问题

### 前后端（分析端↔执行端）关联类

| # | 问题 | 位置 | 要点 |
|---|------|------|------|
| P1-a | **"降至30%仓位"被执行为"卖出30%"** | schemas.py:267-273 / portfolio_manager.py:187-192 / decision_engine.py:538-544 | 三处提取都无差别抓第一个 `\d+%`；正确语义是卖 70%。风控力度被削弱一倍以上 |
| P1-b | **pct_from_md 按 action 名共享** | portfolio_manager.py:174-192 | 多条 buy_add 规则共享最后一个百分比；:178 的 `pct_key` 是没写完的死代码 |
| P1-c | **fallback prompt 硬编码 "000423 东阿阿胶"** | portfolio_manager.py:203 | 任何股票的规则兜底生成都自称东阿阿胶，跨标的污染 |
| P1-d | **评级解析对中文/全角失效，默认 Hold** | rating.py:27-50 | `：`全角冒号不匹配、`买入/卖出`不在词表 → 卖出决策静默变 Hold；反向风险：全文扫描 "buy" 会把 "do not buy" 解析成 Buy |
| P1-e | **PM 分析看不到组合状态** | backtest_hybrid.py:310 | propagate 只传 code+date，PM 不知当前持仓/成本/现金，生成的仓位规则与实际组合脱节 |
| P1-f | **quick 刷新整体覆盖季度规则** | backtest_hybrid.py:683-691 | market-only 分析常无规则输出 → 季度止损规则被清空而非过期；且规则无去重 |
| P1-g | **L1 路径不写 fundamentals_structured；缓存无日期键** | akshare_data.py:707-729 / agent_utils.py:92-107 | 数值校验恒无 source；同进程多日期回测跨日串档 |

### 执行逻辑类

| # | 问题 | 位置 | 要点 |
|---|------|------|------|
| P1-h | **移动止损因 current_date 未设置永不触发** | backtest_engine.py:345 vs execution_engine.py:516-534 | `pd.Timestamp("")` 抛异常被吞，days_held 恒 0 < 3 → BacktestEngine 主入口 trailing stop 完全失效（hybrid 路径 :877 是对的） |
| P1-i | **移动止损高水位包含建仓前价格** | execution_engine.py:701-721 | 固定 60 日窗口起点早于建仓日，高水位虚高 → 止损触发系统性偏晚 |
| P1-j | **当日收盘指标触发按当日收盘成交** | execution_engine.py:95-97 | 收盘 SMA 收盘后才可知，却按当日 close 成交（日内前视）；应顺延次日开盘 |
| P1-k | **buy_add 可每日重复触发，无累计上限** | execution_engine.py:424-468 | 条件持续满足时每天消耗 20% 现金加仓 = 无限 martingale |
| P1-l | **eval() 执行 LLM 字符串** | trading_rules.py:204 | 清空 `__builtins__` 挡不住 `().__class__.__bases__[0].__subclasses__()` 逃逸和 `9**9**9` DoS；已有的安全解析器 rule_expression.py 在生产路径闲置未用 |
| P1-m | **无 CROSSUNDER/前日数据；中文条件必死** | trading_rules.py（_df/_idx 注入但从未使用）/ decision_engine.py:499 | "金叉/死叉"退化为水平比较；trigger_sql 为空时回退中文描述直接 SyntaxError 静默 False |
| P1-n | **季度触发按日历季度，与财报发布错位** | backtest_hybrid.py:186-202/:832 | 7月1日全量分析时中报未出；新财报发布只触发 market-only quick 分析，基本面驱动的评级更新缺失 |

### 数据与业务合理性类

| # | 问题 | 位置 | 要点 |
|---|------|------|------|
| P1-o | **交易成本费率过时+买入端漏过户费** | models.py:174-179 / execution_engine.py:781 | 印花税应为万五（现写千一，高 100%）；过户费应为万0.1（现写万0.2）；买入端未扣过户费；资金预算也漏 |
| P1-p | **StockTwits/Reddit 实时抓取时间穿越** | sentiment_analyst.py:56-58 | prompt 声称覆盖模拟窗口，实际返回抓取时刻最新帖子（原已知问题的 agent 层新暴露面） |
| P1-q | **get_l1_analysis 日期可选且 prompt 示例不带日期** | fundamental_data_tools.py:181 / fundamentals_analyst.py:35 | LLM 照示例调用 → analysis_date=None → 未来财报期混入 |
| P1-r | **基准=个股买入持有，alpha 名不副实** | backtest_engine.py:349-397/:461 | 应对比指数（上证/沪深300）；"alpha"实为 vs 持仓不动的超额收益 |
| P1-s | **选股清单硬编码 6 只蓝筹 — 生存者偏差** | run_batch_analysis.py:25-32 | 结论只能代表"优质大盘股+择时"，不可外推全市场 |
| P1-t | **_select_target_period 用 datetime.now()** | l1_data_layer.py:336 | 回测中"当前月份<4 退用上年年报"的保护按真实系统时间判断，失效 |

---

## 第三部分：P2 — 次要问题（摘要）

1. **夏普比率不一致**：hybrid 不扣无风险利率、用总体标准差；两引擎结果不可比（backtest_hybrid.py:947-957）。
2. **空报告与失败不可区分**：分析师工具失败 → decision_engine 默认 HOLD，与真实 Hold 评级无法区分；空 report 直接 f-string 进下游 prompt（market_analyst.py:86-93 等）。
3. **辩论结构偏差**：Bull 恒首发、Bear/Neutral 恒收尾，RM 顺序拼接 history 存在 recency bias。
4. **_reasoning 审计字段读取不存在的 state 键**（decision_engine.py:230-237）：`market_analyst_report` 等 4 键均不存在，审计链恒空。
5. **_calc_reduce_shares 的 max(100,...)**：小仓位"减仓10%"被放大为卖 100 股。
6. **entry_price/pnl 漏计买入侧成本**（execution_engine.py:807/:853）：胜率、盈亏比系统性高估。
7. **季度缓存 _load_cache 死逻辑**：判断 `trading_rules_json` 键不存在（实存 `trading_rules`）；反序列化丢 source_sentence 使 SELL_PCT 文本兜底失效（backtest_hybrid.py:383-394）。
8. **trailing_stop 缓存缺省 0.08 vs PriceCondition 默认 0.12**：缓存命中与否风控参数不同（decision_engine.py:735）。
9. **L2 异常日不入 state_history**：汇总丢日、年化/夏普轻微失真；decision_stale_days 用交易日索引实现"15 自然天"语义（backtest_engine.py:219/:312-331）。
10. **rule_type 无枚举约束**：LLM 输出中文/变体时优先级静默回落 50（schemas.py:199-209）。
11. **长期停牌净值虚构**、**停牌行 dropna 影响指标窗口**、**复牌连续封板未处理**。
12. **news ToolNode 注册了 news_analyst 未绑定的工具**、**tools_social 死路径**、**辩论 state 缺 judge_decision 键**等死配置/契约问题。
13. **风险辩论 path_map 防御性缺口**：speaker 标签前缀匹配无常量保护，历史上已发生过一次重命名事故。

---

## 第四部分：修复路线图（建议顺序）

### 第一批：恢复系统"名义功能"（不修则回测无意义）
1. **E1** RuleParser.parse 签名（1 行改动，影响整个 backtest_engine 路径）
2. **A1+A2** MA()/eval 上下文（决定 PM 规则是否真的在执行）
3. **X9** hybrid 兜底止损
4. **G1** Numeric Check 路由 + numeric_violations 消费

### 第二批：消除前视偏差（不修则收益虚高）
5. **D2** 财报发布日过滤（影响最广）
6. **D1** bfill 前视
7. **D3** FA 缓存日期键
8. **M2** memory 日期过滤
9. **D7** PE 未发布 EPS

### 第三批：对齐 A 股市场现实（不修则实盘不可复现）
10. **X1** T+1
11. **X2** 涨跌停分板块 + 跌停禁卖
12. **X6** 前复权 vs 绝对价（建议全面改为相对建仓价百分比）
13. **P1-o** 交易成本费率修正

### 第四批：投资逻辑健全性
14. **P1-a** "降至X%"语义、**P1-k** 加仓上限、**P1-e** PM 感知持仓、**P1-h/i** 移动止损、**P1-r** 指数基准

---

## 附录：验证记录

以下 P0 结论由主审计人二次读取源码确认（非仅依赖子审计报告）：

| 结论 | 验证方式 | 结果 |
|------|---------|------|
| E1 parse 签名不匹配 | 读 trading_rules.py:604 与 decision_engine.py:269-297 | ✅ 确认：`parse(self, pm_text, **kwargs)` vs 3 位置参数调用 |
| A1 MA() 回退返回原值 | 读 trading_rules.py:60-79 与 data_layer.py:258-265 | ✅ 确认：仅预计算 5/20/50 SMA；回退 `row.get(field_str)` |
| G1 Numeric Check 不可达 | 读 conditional_logic.py:52-73 | ✅ 确认：返回值集合不含任何 "Numeric Check *" |
| D1 bfill 前视 | 读 stockstats_utils.py:75-85 | ✅ 确认：`ffill().bfill()` 在日期过滤前执行 |
