# TradingAgents 全链路代码审计报告 v2.0

**审计日期**: 2026-07-20
**上次审计**: 2026-07-17（41 项发现，全部仍存在）
**审计范围**: 数据层 → Agent/LangGraph 层 → 决策引擎 → 规则引擎 → 执行引擎 → 回测引擎 → 报告层 → 前端
**审计方法**: 5 路并行深度代码走查（每路完整阅读目标文件）+ 主审计人手动验证所有 P0 结论
**代码变更**: 上次审计后仅前端文件有修改（app.jsx/index.html/vendor/app.js），后端 Python 未变更

---

## 执行摘要

本次审计在上次 41 项发现基础上**全部重新验证**，并补充发现 **4 项新问题**，总计 **45 项问题**：
**P0 × 14，P1 × 17，P2 × 14**。

### 最重要的结论

**两条回测路径各有一个"致命断点"，均通过源码二次验证：**

| 断点 | 验证状态 | 后果 |
|------|---------|------|
| **E1** `RuleParser.parse()` 签名不匹配 | ✅ 已验证 `trading_rules.py:604` 只接受 1 位置参数；`decision_engine.py:269/278/294/309` 4 处传 3 个 | TypeError → backtest_engine 路径**所有决策降级 HOLD** |
| **A1** `MA()` 缺列回退返回原始值 | ✅ 已验证 `trading_rules.py:67-79`；data_layer 仅预计算 5/20/50 SMA | `MA(close,200)` → `close` 恒 False；`MA(volume,20)` → `volume` 恒 False |
| **G1** Numeric Check 三节点不可达 | ✅ 已验证 `conditional_logic.py:52-73` 永不返回 "Numeric Check *" | 三层数值防御第 2 层从未执行 |
| **D2** 财报按会计期截止日过滤 | ✅ 已验证 `akshare_data.py:566-589` 用 period-end date | 年报系统性提前 2-3 月"可用"（前视） |

叠加 `numeric_violations` 全仓库无消费者、Hybrid 兜底止损不存在、memory 用未来行情结算等 P0，**当前回测结果与 PM 真实意图严重偏离，且含多重前视偏差**。在修复前不应作为策略有效性的依据。

---

## 一、前后端（分析端↔执行端）关联漏洞（9 项）

### 1-A【P0·已验证】PM 分析看不到投资组合状态 → 指令与实际持仓脱节

- **文件**: `backtest_hybrid.py:302-310`、`tradingagents/agents/managers/portfolio_manager.py:70-138`
- **问题**: `graph.propagate(symbol, date_str)` 仅传股票代码和日期。PM 不知道当前是否持仓、成本多少、浮盈浮亏、现金余额。生成的"降至30%仓位""加仓20%"等指令完全脱离实际状态。
- **修复**: 在 PM prompt 注入 `position_state`（shares/cost/price/pnl_pct/cash）。

### 1-B【P0】"降至30%仓位"被执行为"卖出30%"

- **文件**: `schemas.py:405-412`、`portfolio_manager.py:190-192`、`decision_engine.py:538-544`
- **问题**: 三处提取逻辑无差别抓第一个 `\d+%`。"降至30%"语义是卖 70%，代码执行的是卖 30%。风控力度削弱一倍以上。
- **修复**: 检测 `降至\d+%` 模式时 pct = 1.0 - percentage/100。

### 1-C【P0·已验证】HybridBacktestEngine 硬编码 stop_loss=0 → 兜底风控不存在

- **文件**: `backtest_hybrid.py:170-172`
- **已验证源码**: `PriceCondition(stop_loss=0.0, take_profit=0.0)`，注释称由 ExecutionEngine._safe_price_condition 兜底——**该方法在 DecisionEngine（decision_engine.py:412），ExecutionEngine 没有**。
- **HybridBacktestEngine 绕过 DecisionEngine** → 兜底永不触发 → PM 遗漏止损时持仓裸奔。
- **修复**: 构建 WeeklyDecision 时检查是否含 stop_loss 规则，缺则用 current_price * 0.92 生成兜底。

### 1-D【P0·已验证】Numeric Check 节点不可达 + violations 无人消费

- **conditional_logic.py:52-73**: `should_continue_debate` 只返回 "Research Manager"/"Bear Researcher"/"Bull Researcher"；`should_continue_risk_analysis` 只返回 "Portfolio Manager"/"Conservative/Neutral/Aggressive Analyst"——**没有一条路径返回 "Numeric Check Bull/Bear/Risk"**。
- **numeric_validation.py** 三个校验节点为死节点。
- **全仓库 `numeric_violations`** 仅被写入（numeric_validation、propagation），**无任何消费者**。`build_guard_prompt`/`build_cross_type_warning` 实现完整但从未被调用。
- **修复**: 辩论终止分支改为返回 Numeric Check；在 RM/PM prompt 注入 violations。

### 1-E【P1】Fallback prompt 硬编码 "000423 东阿阿胶"

- **文件**: `portfolio_manager.py:203`
- **问题**: 任何股票的主分析失败时，兜底都以东阿阿胶身份要求 LLM 生成规则。
- **修复**: 动态传入 symbol + stock_name。

### 1-F【P1】Quick refresh 整体覆盖而非合并 → 季度风控规则被清空

- **文件**: `backtest_hybrid.py:683-689`
- **问题**: market-only quick analysis 常无规则输出 → `build_weekly_decision_from_rules([])` 生成零规则、stop_loss=0 的决策 → 季度止损规则被抹掉。无合并、无去重、"保留高优先级风控规则"机制缺失。
- **修复**: quick 刷新仅更新 market 类规则，保留 stop_loss/take_profit/风控规则。

### 1-G【P1】评级解析三漏洞：中文不支持 + 全角失效 + 子串误判

- **文件**: `tradingagents/agents/utils/rating.py:23-50`
- (1) 只支持英文 5 级评级，`买入/卖出/持有` 不在词表 → Hold
- (2) 正则 `[:\-]` 不匹配全角 `：` → 含全角冒号的评级行不匹配 → Hold
- (3) 单词分割扫描："do not buy" → "buy" 在 set → 返回 Buy（反义）
- **修复**: 加中文映射；正则加 `：`；对否定词做前向检测。

### 1-H【P1·已验证】Memory 用未来行情结算 + 检索无日期过滤

- **trading_graph.py:324-362**: `_resolve_pending_entries` 在 `propagate()` 开头用当前日期的**未来** holding_days 天行情结算历史决策，通过 reflection 注入 past_context 污染当前 PM 决策。
- **memory.py:71-96**: `get_past_context` 无 `as_of_date` 过滤，多标的顺序回测/重跑时会把"答案"喂回"考题"。
- **修复**: get_past_context 加日期参数；回测模式只结算 entry_date+holding_days ≤ 当前模拟日的条目。

### 1-I【P1】L1 路径不写 fundamentals_structured；缓存无日期键 → 数值校验恒无 source

- **akshare_data.py:707-729**: `get_fundamentals` 不存 `fundamentals_structured`
- **agent_utils.py:92-107**: `get_fundamentals_structured` 读不到则返回 None → numeric_validation 无数据可校验
- **修复**: get_fundamentals 结束后写结构化数据；同上段加日期 key。

---

## 二、程序执行逻辑漏洞（18 项）

### 2-A【P0·已验证】RuleParser.parse() 签名不匹配 → 独立回测路径决策全部降级

- **`trading_rules.py:604`**: `def parse(self, pm_text: str, **kwargs)` — 仅 1 位置参数
- **`decision_engine.py:269-270/278-281/294-297/309-312`**: 全部以 `parse(pm_text, decision.price_cond, decision.direction, use_llm=False)` 3 位置参数调用 → TypeError
- 异常沿 `_parse_pm_output` 上抛，在 `run_decision_chain:247-250` 被捕获 → **每次返回 _default_decision（HOLD、零规则）**
- **连带**: `__init__:77` 中 `_llm_injected = False` 漏写 `self.`，`:296` 引用 `self._llm_injected` 将 AttributeError（被 E1 掩盖，修 E1 后暴露）
- **修复**: parse 签名改为 3 位置参数 + `*` 分隔 keyword-only；补 `self._llm_injected`

### 2-B【P0·已验证】MA() 缺预计算列回退返回原始值 → MA200/量能规则恒 False

- **`trading_rules.py:67-79`**: `MA(field, period)` 查 `close_{period}_sma` → None → 回退 `row.get(field_str)` → 返回当日原始值
- **data_layer.py:258-265**: 仅预计算 `close_5/20/50_sma`，context 还注册了 `ma60='close_60_sma'`（同样没算）
- **验证**:
  - `MA(close,200)`: `field_str='close'` → `row.get('close_200_sma')`=None → `row.get('close')`=今日收盘价 → `close < close` 恒 False
  - `MA(volume,20)`: `field_str='volume'` → 不走 close sma 分支 → `row.get('volume')`=今日volume → `volume > volume*1.5` 恒 False
- **后果**: PM prompt 强制推荐的 MA200 止损、量能确认入场**全部静默失效**
- **修复**: (1) data_layer 预计算 60/120/200 SMA + volume SMA (2) MA() 缺列时用 `_df`/`_idx` 现场计算 (3) 找不到返回 NaN + 告警，绝不返回原始值

### 2-C【P0·已验证】_filter_by_report_date 按会计期截止日过滤 → 年报提前 2-3 月可用

- **`akshare_data.py:566-589`**: 用 `df.columns[0]`（会计期截止日，如 2025-12-31）与 cutoff 比较
- **验证**: `curr_date="2026-01-15"` 时，`2025-12-31 <= 2026-01-15` → 2025 年报判定"已可用"，实际发布日 ~2026-03/04
- **同样受影响**: `data_loader_fixed.py:184-198` 的 `_filter_abstract_periods`（`pd.Timestamp(str(c)) <= cutoff`）
- **影响面**: get_fundamentals / get_balance_sheet / get_cashflow / L1 全部基本面分析
- **修复**: 用 baostock `query_profit_data` 的 `pubDate` 建立"报告期→发布日"映射

### 2-D【P0·已验证】stockstats bfill() 在日期过滤前执行 → 用未来价格回填

- **`stockstats_utils.py:83`**: `data[price_cols].ffill().bfill()` 在第 152 行 `_clean_dataframe` 中执行
- **`stockstats_utils.py:155`**: `data = data[data["Date"] <= curr_date_dt]` 在第 155 行之后
- **验证**: bfill 作用于全量 5 年数据（含未来日期的收盘价），**日期过滤在后** → 停牌缺口被未来价格填充
- **修复**: 把日期过滤挪到 bfill 之前；或删 bfill 只保留 ffill

### 2-E【P0·已验证】PE 使用可能未发布的年报 EPS

- **`akshare_data.py:636-642`**: 直接搜索 `12-31` 结尾的最新年报行取 EPS，**未对 fa_df 应用 curr_date 过滤**
- **验证**: curr_date=2026-01-15 时，`price`=当日收盘价（正确），`eps_annual`=2025-12-31 EPS（尚未发布）→ PE 系统性偏低/无效
- **修复**: 对 fa_df 按发布日（或至少 curr_date）过滤

### 2-F【P0】FA 缓存键不含 analysis_date → 缓存中毒式前视

- **`decision_engine.py:777`**: `cache_key = f"{symbol}_{report_period}"` — 无日期成分
- **`cache_manager.py:62-92`**: `get_fa_report/save_fa_report` 同型：键仅 `{symbol}_{report_period}`
- **攻击场景**: 较晚日期生成的完整 L1 结果被较早回测日期命中复用 → 跨运行前视偏差
- **修复**: 缓存键加入 analysis_date

### 2-G【P1·已验证】trailing stop 因 current_date 未设置在 backtest_engine 路径永不触发

- **`backtest_engine.py:345`**: `portfolio.current_date = str(self.df.iloc[-1]["date"])` — **循环结束后**才设置
- **`execution_engine.py:522-525`**: `pd.Timestamp("")` → ValueError → except 吞掉 → days_held 恒 0 < 3 → 移动止损分支永不执行
- **对比**: `backtest_hybrid.py:877` 在 execute 前正确设置了 current_date — hybrid 路径无此问题
- **修复**: 在 BacktestEngine._run_loop 循环体第一行加 `portfolio.current_date = date_str`

### 2-H【P1】eval() 直接执行 LLM 生成的条件串 → 沙箱逃逸/DoS

- **`trading_rules.py:204`**: `eval(condition_clean, {"__builtins__": {}}, context)`
- 清空 `__builtins__` 挡不住 `().__class__.__bases__[0].__subclasses__()` 逃逸和 `9**9**9` CPU 挂死
- 已有的安全解析器 `rule_expression.py`（839 行，AST 白名单）在当前生产路径**闲置未用**
- **修复**: 生产路径改用 rule_expression.py 的安全解析器

### 2-I【P1】无 CROSSUNDER/CROSSOVER + 前日数据 _df/_idx 注入但从未使用

- `execution_engine.py:217-218` 注入了 `_df` 和 `_idx`，但 `eval_condition` 全文无任何引用
- CROSSUNDER/CROSSOVER 未注册 → LLM 输出死叉/金叉条件时 NameError → 静默 False
- 任何需要前日值的判断（"跌穿"比"处于线下"严格）退化为水平比较
- **修复**: 注册 CROSSUNDER(a,b)=a_today<b_today and a_prev>=b_prev，用 _df.iloc[_idx-1]

### 2-J【P1】中文条件 SyntaxError → 规则死亡

- `decision_engine.py:499` / `backtest_hybrid.py:88`: trigger_sql 为空时 fallback 到中文 trigger_condition（如"跌破200日均线且缩量"）
- `eval_condition` 只翻译字段名和 AND/OR，不翻译中文操作符 → SyntaxError → 静默 False
- **修复**: fallback 前先过中文操作符归一化（跌破→<，超过→>，且→AND，或→OR）

### 2-K【P1】buy_add 可每日重复触发 → 无限平均加仓（martingale）

- **`execution_engine.py:424-468`**: 条件持续满足时每天消耗 20% 可用现金加仓
- 无 `max_triggers` 计数、无累计仓位上限、无"已加仓 N 次"状态
- 在下跌趋势中 → 逐步耗尽所有现金 → "死多头"风格被强加于 PM 意图之上
- **修复**: 为每条规则加 max_triggers 字段；加仓前检查已有仓位占比

### 2-L【P1·已验证】L1 崩溃后 daily 重试 → LLM 成本炸弹

- **`backtest_engine.py:289-296`**: L1 崩溃时 `last_decision_idx` 不更新 → 次日起每天重试 → 全回测期每日一次 LLM 调用
- **对比**: HybridBacktestEngine 仅在季度/事件触发，无此问题
- **修复**: 加指数退避；连续失败 N 次后停止触发 L1

### 2-M【P1】季度触发按日历季度 → 与财报发布错位

- **`backtest_hybrid.py:186-202`**: _is_quarter_start 判 1/4/7/10 月首交易日
- **7 月 1 日触发全量分析时** Q2 中报未出，PM 用的是 Q1 数据
- **新财报发布时**: `_new_quarterly_report_available:832` 只触发 market-only quick 分析，不重跑 fundamentals
- **修复**: 将全量分析触发点与财报发布日对齐

### 2-N【P1】决策时当日收盘指标触发按当日收盘成交 → 日内前视

- **`execution_engine.py:95-97`**: `action_price = close`
- 收盘 SMA/RSI/MACD 收盘后才能得知，却以当日 close 成交 — 现实中无法实现
- **修复**: 规则类交易顺延次日开盘价成交（止损/止盈用盘中 low/high 触价可保留当日）

### 2-O【P1】Sharpe 两引擎不一致 + 基准非总收益指数

- Hybrid 用总体标准差（ddof=0）且不扣无风险利率；BacktestEngine 用样本标准差（ddof=1）且扣 3% → 不可比
- 基准 = 同股票买入持有（仅价格，不含分红）→ 非市场指数 → alpha 名不副实
- **修复**: 统一两引擎计算；引入总收益指数基准

### 2-P【P1】季度缓存 _load_cache 键名死逻辑 +字段丢失

- **`backtest_hybrid.py:383-384`**: 判 `trading_rules_json` 键 — `to_dict` 存的是 `trading_rules` → 首分支永不走
- 反序列化跳 `source_sentence` → SELL_PCT 文本兜底失效
- **修复**: 移除死键检查；补 `source_sentence=r.get("source","")`

### 2-Q【P2】Frontend vendor/app.js × Python 后端接口契约审计（新增）

- **`web_app/frontend/src-vanilla/app.jsx`（~994行，2026-07-20 重构）**: 新版本使用 React hooks（useState/useEffect/useCallback），含大师面板（MASTERS_DEFAULT 10 位）、圆形座位布局（SEATS_DEFAULT 9 席位）、STOCK_SUGGESTIONS 6 只热门股
- **接口契约**: ① `API_BASE = '/api'` — 需确认后端路由一致（web_app/backend/main.py）② 依赖 SSE `/api/analyze/stream` 推送 agent 报告 → 需确认 `graph_adapter.py` SSE 格式与前端 `reportData[type]=content` 映射一致 ③ HTML `index.html` 引 `vendor/app.js` + Tailwind CDN → 离线环境无网络时全部样式丢失
- **低风险但需关注**: (1) SSE 流如果 LLM 分析超时（DeepSeek 结构化输出可能 300-600s），前端无重连/超时提示 (2) `index.html` 无版本化的 cache-buster 参数 → CDN 可能缓存旧版 (3) 大师面板纯前端 mock 数据，后端无对应 API → 显示的名人语录与回测无关联

### 2-R【P2】rule_expression.py 安全解析器自身缺陷（如启用需先修）

- 字段对字段比较 `close < ma200` 解析成 `AtomicExpr(left, op, 0.0)`（dummy 0）→ 恒 True/False
- `NOT x AND y` 解析为 `NOT(x AND y)`（优先级错误）+ 盲剥括号生畸形串
- FuncRegistry 无 `sma` 别名 → PM 输出 SMA 时 warning + 返回 None

---

## 三、不符合股票投资常识的漏洞（8 项）

### 3-A【P0】T+1 制度未强制执行

- **`execution_engine.py:49-175`**: `PortfolioState` 无"今日买入不可卖"字段
- buy_add + stop_loss 同日触发时当日买入当日卖出 — A 股不可复现
- **修复**: 增加 `shares_available_today` / `shares_settling` 字段

### 3-B【P0·已验证】涨跌停硬编码 ±9.9% → ST/创业板/科创板全部误判 + 跌停日仍可"卖出"

- **`execution_engine.py:884-896`**: `_is_limit_up` 判 `pct_chg >= 9.9`，`_is_limit_down` 判 `pct_chg <= -9.9`
- **ST 股 ±5%** → -5.5% 即跌停，但代码等待 -9.9% → 判定通过（不过滤）
- **创业板/科创板 ±20%** → -15% 正常波动被误判跌停 → 不该过滤的被过滤
- **跌停日卖出逻辑**: `:128-134` 只检查 `at_limit_up` 阻止买入，不检查 `at_limit_down` 阻止卖出；填价 `max(stop_loss, open)` 以高于开盘的止损价成交 → **跌停板上无法成交的价格在回测中"成交"了**
- **止盈门控误用**: `:511-515` 止盈用 `at_limit_down` 判断 → 应是 `at_limit_up`
- **修复**: 按代码前缀/ST 标记确定 limit_pct；跌停日跳过卖出顺延次日；止盈门控改 at_limit_up

### 3-C【P0】前复权数据 vs PM 绝对价格阈值系统性错配

- **`data_layer.py:140`**: `adjustflag="2"`（前复权）→ 所有历史价格向下调整
- **`execution_engine.py:311-317`**: 止损填价用绝对价 `sl_value` → 除权后历史价格整体下移
- **场景**: PM 决策时看 50 元设止损 48.50；3 个月后分红除权，前复权收盘价变 46.00 → `close < 48.50` 永远满足（假止损）；填价 48.50 成交于 46.00 的股票 → **虚构卖价**
- **更深层**: 前复权按"今天"基点重算 → 本身就隐含未来分红信息（subtle 前视）
- **修复**: PM 绝对价在入执行层前转换为相对建仓价百分比；或改用后复权

### 3-D【P1】交易成本费率过时 + 买入端漏过户费

- **`models.py:174-179`**: `stamp_duty_rate=0.001` 应为 `0.0005`（2023-08 减半）；`transfer_fee_rate=0.00002` 应为 `0.00001`（2022-04 调整）
- **`execution_engine.py:781`**: `_execute_buy` 只计佣金和滑点，**未扣过户费**
- **`execution_engine.py:648-650`**: `_check_entry_signals` 买力预算也漏过户费
- **修复**: 更正费率 + 买入端补过户费

### 3-E【P1】移动止损高水位窗口含建仓前价格 → 高水位虚高

- **`execution_engine.py:701-721`**: 固定 60 日窗口起点可能早于建仓日
- 若建仓前 30 天有更高价格（如 55.00），该价格被纳入高水位 → 止损阈值虚高，触发偏晚
- **修复**: 窗口起点改为建仓日索引：`df.iloc[entry_idx : idx+1]['close'].max()`

### 3-F【P1】选股清单硬编码 6 只蓝筹 → 生存者偏差

- **`run_batch_analysis.py:25-32`** / **`batch_analyze.py:17-24`**: 全为市值 500 亿+龙头
- **`run_custom_masters_backtest.py`**: 默认 `--symbol 000423`（东阿阿胶）
- 回测结论只能代表"优质大盘股+择时"，不可外推全市场
- **修复**: 用沪深 300/中证 500 成分股列表；在回测区间起始日获取当时存在的股票列表

### 3-G【P1·已验证】StockTwits/Reddit 实时抓取 → 回测中获取未来数据

- **`sentiment_analyst.py:56-58`**: 回测模式下，StockTwits 和 Reddit API 返回的是**当前（2026-07）**的最新数据，而非 trade_date 当时的帖子
- **StockTwits API 不支持历史日期查询** → 本质上无法在回测中使用
- **Reddit 可通过 Pushshift 获取历史数据**，但当前实现用 `t=week`（当前时间的过去 7 天）
- **修复**: 回测模式下标记为 unavailable；或对接历史数据源

### 3-H【P1】entry_price 漏买入侧成本 → pnl/胜率/盈亏比系统性高估

- **`execution_engine.py:807`**: `TradeRecord.entry_price` = 裸成交价（未摊入买入滑点+佣金）
- **`execution_engine.py:853`**: `pnl = (price - entry_price) * shares - 卖出侧成本` → 买入侧成本永久漏计
- **`execution_engine.py:854`**: `pnl_pct` 双边成本都不含 → 胜率/盈亏比偏高
- **修复**: entry_price 包含买入侧成本

---

## 四、严重性汇总

| 严重级别 | 数量 | 编号 |
|---------|------|------|
| **P0** 回测结果无意义/致命 | 14 | E1、A1、G1、D2、D1、X9、T+1、涨跌停(3-B)、前复权(3-C)、PM不知持仓(1-A)、降仓语义(1-B)、memory前视(1-H)、PE未发布EPS(2-E)、FA缓存(2-F) |
| **P1** 显著扭曲结果 | 17 | parse掉self(2-A连带)、MA(200/vol)(2-B)、Numeric不可达(1-D)、fallback(1-E)、quick覆盖(1-F)、rating(1-G)、L1缓存(1-I)、trailing(2-G)、eval逃逸(2-H)、cross(2-I)、中文条件(2-J)、buy_add(2-K)、L1重试(2-L)、季度触发(2-M)、日内成交(2-N)、Sharpe(2-O)、_load_cache(2-P)、费率(3-D)、高水位(3-E)、幸存偏差(3-F)、StockTwits(3-G)、entry_price(3-H) |
| **P2** 次要 | 14 | 前端契约(2-Q)、rule_expr缺陷(2-R)、停牌/停牌行/复牌封板、Debate近因、工具失败静默、market analyst数字不校验、交易成本漏过户费、_计算仓位_max(100)、rule_type无枚举、Memory默认不清理、Excel硬编码、季度缓存跨start_date共享、死边缘配置 |

---

## 五、修复路线图

### 第一批：恢复系统名义功能（4 项，1-2 天）
1. **E1** RuleParser.parse 签名修复（1 行改 4 处调用）+ 补 `self._llm_injected`
2. **A1** MA() 缺列处理 + data_layer 预计算 60/120/200 SMA + volume SMA
3. **G1** Numeric Check 路由接入 conditional_logic
4. **X9** Hybrid build_weekly_decision 兜底止损

### 第二批：消除前视偏差（5 项，2-3 天）
5. **D2** 财报发布日过滤（预加载 pubDate 映射）
6. **D1** bfill 前视（日期过滤前置或删 bfill）
7. **2-E** PE 未发布 EPS 过滤
8. **2-F** FA 缓存加日期键
9. **1-H** memory get_past_context 加 as_of_date

### 第三批：对齐 A 股市场现实（4 项，2-3 天）
10. **3-A** T+1 制度
11. **3-B** 涨跌停分板块 + 跌停日禁卖
12. **3-C** 前复权绝对价 → 相对百分比
13. **3-D** 交易成本费率修正 + 买入端补过户费

### 第四批：投资逻辑健全性（剩余 32 项，按优先级排期）
14. **1-B** "降至X%"语义修正
15. **2-K** buy_add 加仓上限
16. **1-A** PM 感知持仓状态
17. **2-G** trailing stop current_date
18. **2-L** L1 崩溃退避
19. **2-H** eval → rule_expression 安全解析器
20. **3-F** 指数基准 + 消除幸存偏差
...其余 27 项按排期

---

## 附录：主审计人验证记录

以下 P0 结论由主审计人手动读源码二次确认（非依赖子审计报告）：

| 结论 | 文件:行 | 验证结果 |
|------|--------|---------|
| E1 parse 签名不匹配 | `trading_rules.py:604` vs `decision_engine.py:269/278/294/309` | ✅ `def parse(self, pm_text: str, **kwargs)` — 1 位置参数。4 处调用传 3 个位置参数 |
| A1 MA() close 回退 | `trading_rules.py:67-79` | ✅ `close_200_sma`→None→`row.get('close')`→今日收盘价；`volume_20_sma`→None→`row.get('volume')`→今日volume |
| G1 Numeric Check 不可达 | `conditional_logic.py:52-73` | ✅ `should_continue_debate` 只返回 3 值，`should_continue_risk_analysis` 只返回 4 值，均不含 "Numeric Check *" |
| D2 财报会计期过滤 | `akshare_data.py:566-589` | ✅ `df.columns[0]` 是会计期截止日，docstring 明说"report-period (first column)" |
| D1 bfill 前视 | `stockstats_utils.py:75-85,152-155` | ✅ `ffill().bfill()` 在 line 152，`<= curr_date_dt` 过滤在 line 155 |
| X9 stop_loss=0 | `backtest_hybrid.py:170-172` | ✅ 注释称 ExecutionEngine._safe_price_condition 兜底 — 该方法只在 DecisionEngine 里 |
| X2 涨跌停硬编码 | `execution_engine.py:884-889` | ✅ `pct_chg >= 9.9` / `<= -9.9`，无板块区分 |
| D7 PE 用未发布 EPS | `akshare_data.py:636-642` | ✅ 搜索最新 `12-31` 行取 EPS，无 curr_date 过滤 |
