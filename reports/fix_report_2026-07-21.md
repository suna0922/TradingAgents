# TradingAgents 审计修复报告

**生成日期**: 2026-07-21  
**源审计报告**: `reports/audit_full_codebase_2026-07-20.md` (45项: P0×14, P1×17, P2×14)  
**修复完成**: 19/45 项（前四批核心项）  
**修复策略**: 按审计推荐的四批次顺序 — 恢复功能 → 消除前视 → 对齐市场 → 健全逻辑

---

## 第一批：恢复系统名义功能（4/4 ✅）

### FIX-1.1【E1】RuleParser.parse() 签名不匹配 → TypeError → 所有决策降级 HOLD

| 项目 | 详情 |
|------|------|
| **文件** | `backtest/trading_rules.py:604`, `backtest/decision_engine.py:77` |
| **原因** | `parse(self, pm_text: str, **kwargs)` 仅 1 位置参数；4 处调用传 3 个 |

**修复**:
- `trading_rules.py`: `parse()` 签名改为 `parse(self, pm_text: str, price_cond=None, direction=None, **kwargs)`
- `decision_engine.py`: `_llm_injected = False` → `self._llm_injected = False`（漏写 `self.`，修 E1 后暴露）

---

### FIX-1.2【A1】MA() 缺预计算列回退返回原始值 → 趋势规则静默失效

| 项目 | 详情 |
|------|------|
| **文件** | `backtest/trading_rules.py:67-79`, `backtest/data_layer.py:258-265` |
| **原因** | MA(close,200) 列不存在→返回当日 close；MA(volume,20)→返回当日 volume，所有比较恒 False |

**修复**:
- `trading_rules.py` `MA()`: 三层回退策略
  1. 预计算 SMA 列读取（`close_{period}_sma`）
  2. 通用 SMA 列查找（`{field}_{period}_sma`）
  3. `_df`/`_idx` 现场计算滚动均值
  4. 全部失败返回 NaN + 日志告警（绝不返回当日原始值）
- `data_layer.py`: indicator_cols 新增 `close_60/120/200/250_sma` + `volume_5/10/20_sma`
- `trading_rules.py`: `_CONTEXT_FIELDS` + `ma120/ma200/ma250` 别名

---

### FIX-1.3【G1】Numeric Check 三节点不可达 → 数值防线永不执行

| 项目 | 详情 |
|------|------|
| **文件** | `tradingagents/graph/conditional_logic.py:52-76`, `tradingagents/graph/setup.py:127-175` |
| **原因** | `should_continue_debate` 只返回 Bull/Bear/Manager；`should_continue_risk_analysis` 只返回分析师/PM |

**修复**:
- `conditional_logic.py`:
  - `should_continue_debate`: 辩论终止返回 `"Numeric Check Bull"` → 链入 `Bull → Bear → RM`
  - `should_continue_risk_analysis`: 风控终止返回 `"Numeric Check Risk"`
- `setup.py`:
  - 串接 `Numeric Check Bull → Numeric Check Bear → Research Manager`
  - Aggressive/Conservative Analyst path_map 增加 `"Numeric Check Risk"`
  - 所有 path_map 覆盖条件函数全部返回值（防止 LangGraph KeyError）

---

### FIX-1.4【X9】HybridBacktestEngine stop_loss=0 硬编码 → 兜底风控不存在

| 项目 | 详情 |
|------|------|
| **文件** | `backtest_hybrid.py:152-210` |
| **原因** | `build_weekly_decision_from_rules` 所有决策 `stop_loss=0.0`；注释称 ExecutionEngine 兜底但方法在 DecisionEngine |

**修复**:
- `build_weekly_decision_from_rules`: 扫描 trading_rules，提取 `STOP_LOSS`/`TAKE_PROFIT` 规则的止损/止盈价
- 新增 `_extract_price_from_condition()`: 从 `"close < 48.50"` 提取 `48.50`

---

## 第二批：消除前视偏差（5/5 ✅）

### FIX-2.1【D2】财报按会计期截止日过滤 → 年报提前 2-3 月可用

| 项目 | 详情 |
|------|------|
| **文件** | `tradingagents/dataflows/akshare_data.py:566-620`, `tradingagents/l1/data_loader_fixed.py:18-33` |
| **原因** | `df.columns[0]` 是会计期截止日（如 2025-12-31），cur_date=2026-01-15 时 2025 年报被判定"已可用" |

**修复**:
- `akshare_data.py` `_estimate_publish_date()`: 用保守监管截止日估算发布日期
  - 年报(12-31) → 次年 4/30 | Q1(03-31) → 4/30 | 中报(06-30) → 8/31 | Q3(09-30) → 10/31
- `_filter_by_report_date`: 比较估算发布日替代会计期截止日
- `data_loader_fixed.py` `_est_pub_date()`: 同逻辑本地实现

---

### FIX-2.2【D1】stockstats bfill() 在日期过滤前执行 → 用未来价格回填

| 项目 | 详情 |
|------|------|
| **文件** | `tradingagents/dataflows/stockstats_utils.py:152-155` |
| **原因** | `_clean_dataframe(data)` 的 `.bfill()` 在 line 83；日期过滤 `[data["Date"] <= curr_date_dt]` 在 line 155 |

**修复**: 日期过滤行移到 `_clean_dataframe(data)` 之前执行，确保 bfill 仅用 ≤ curr_date 的数据

---

### FIX-2.3【2-E】PE 使用未发布年报 EPS → PE 系统性偏低

| 项目 | 详情 |
|------|------|
| **文件** | `tradingagents/dataflows/akshare_data.py:667-685` |
| **原因** | `_append_valuation_metrics` 搜索最新 "12-31" 行取 EPS，未对 fa_df 做 curr_date 过滤 |

**修复**: PE 计算时对年报行调用 `_estimate_publish_date()` 检查是否已发布；未发布跳过查更早年份

---

### FIX-2.4【2-F】FA 缓存键不含 analysis_date → 缓存中毒式前视

| 项目 | 详情 |
|------|------|
| **文件** | `backtest/decision_engine.py:778-821`, `backtest/cache_manager.py:62-92` |
| **原因** | `cache_key = f"{symbol}_{report_period}"` 无日期成分 → 跨运行/跨日期缓存复用 |

**修复**:
- `decision_engine.py` `_generate_fa_metrics`: 缓存键 `{symbol}_{report_period}_{date_str}`
- `decision_engine.py` `get_fa_metrics`: 三层查找（dated → undated 兼容 → prefix 回退）
- `cache_manager.py` `get/save_fa_report`: 支持可选 `analysis_date` 参数
- 调用方传入 `analysis_date=date_str`

---

### FIX-2.5【1-H】Memory 用未来行情结算 + 无日期过滤 → past_context 含未来收益

| 项目 | 详情 |
|------|------|
| **文件** | `tradingagents/graph/trading_graph.py:324-348`, `tradingagents/agents/utils/memory.py:71-86` |
| **原因** | `_fetch_returns` 用 `trade_date + holding_days + 7` 天的未来行情结算；`get_past_context` 无日期过滤 |

**修复**:
- `trading_graph.py` `_resolve_pending_entries`: 加 `current_date` 参数，只结算 `entry_date + holding_days ≤ current_date` 的条目
- `trading_graph.py` `propagate()`: 传入 `current_date=trade_date`
- `memory.py` `get_past_context`: 加 `as_of_date` 参数，只返回 `date ≤ as_of_date` 的已结算条目
- `trading_graph.py` `_run_graph_stream`: 调用 `get_past_context(company_name, as_of_date=trade_date)`

---

## 第三批：对齐 A 股市场现实（4/4 ✅）

### FIX-3.1【3-A】T+1 制度未强制执行

| 项目 | 详情 |
|------|------|
| **文件** | `backtest/models.py:144`, `backtest/execution_engine.py:82,804-806,835-845` |
| **原因** | PortfolioState 无"今日买入不可卖"字段；buy_add + stop_loss 同日可触发当日卖出 |

**修复**:
- `PortfolioState`: 新增 `shares_settling: int = 0`
- `execute()`: 日初 `portfolio.shares_settling = 0`（昨夜买入已结算）
- `_execute_buy()`: `portfolio.shares_settling += shares`
- `_execute_sell()`: 可卖量 = `shares - shares_settling`，为 0 则跳过

---

### FIX-3.2【3-B】涨跌停硬编码 ±9.9% 不分板块 + 跌停可卖

| 项目 | 详情 |
|------|------|
| **文件** | `backtest/execution_engine.py:91-92,132-138,520,897-930` |
| **原因** | `pct_chg >= 9.9` → ST(5%)/科创创业板(20%)全部误判；跌停日仍可卖出；止盈门控误用 `at_limit_down` |

**修复**:
- `_get_limit_pct(symbol)`: 按代码前缀返回 主板10%/创业板300 20%/科创板688 20%/ST 5%
- `_is_limit_up_sym/down_sym`: 符号感知的涨跌停检测
- `execute()`: 改用 `_is_limit_up_sym/down_sym`
- 跌停日跳过 `_check_exit_signals`（禁止卖出）
- 止盈门控: `at_limit_down` → `at_limit_up`

---

### FIX-3.3【3-C】前复权数据 vs PM 绝对价格阈值错配

| 项目 | 详情 |
|------|------|
| **文件** | `backtest/models.py:27`, `backtest/execution_engine.py:517-527` |
| **原因** | baostock 前复权→历史价格下移；除权后 PM 绝对止损价虚高→假止损；前复权隐含未来分红信息 |

**修复**:
- `PriceCondition`: 新增 `stop_loss_pct: float = 0.08`（默认 -8% 百分比止损）
- `_check_exit_signals`: 增加百分比止损兜底 — 当 `close/entry_price - 1 ≤ -stop_loss_pct` 时触发
- 与绝对止损并行，互补防御

---

### FIX-3.4【3-D】交易成本费率修正 + 买入端补过户费

| 项目 | 详情 |
|------|------|
| **文件** | `backtest/models.py:177-180`, `backtest/execution_engine.py:791-796,809-811,658-661` |
| **原因** | 印花税千一→应万五(2023-08减半)；过户费万0.2→应万0.1(2022-04)；买入端漏过户费 |

**修复**:
- `stamp_duty_rate`: `0.001` → `0.0005`
- `transfer_fee_rate`: `0.00002` → `0.00001`
- `_execute_buy`: 加过户费 `gross_value * transfer_fee_rate`（仅 SH 市场）
- 资金调整后补算过户费
- `_check_entry_signals` 买力预算补过户费

---

## 第四批：投资逻辑健全性（首批 6/32 ✅）

### FIX-4.1【1-B】"降至X%"语义修正

| 项目 | 详情 |
|------|------|
| **文件** | `backtest/decision_engine.py:580-586`, `backtest/execution_engine.py:387-394` |
| **原因** | "降至30%"语义是卖 70%，代码提取 30%→只卖 30%，风控削弱一倍以上 |

**修复**: 两个 `_extract_pct_from_text` 方法增加"降至/降到/减至"前缀检测 → `return 1.0 - raw_pct`

---

### FIX-4.2【2-K】buy_add 加仓上限

| 项目 | 详情 |
|------|------|
| **文件** | `backtest/models.py:149`, `backtest/trading_rules.py:520`, `backtest/execution_engine.py` |
| **原因** | 条件持续满足时每日消耗 20% 现金加仓 → 无上限 → 无限平均加仓 |

**修复**:
- `TradingRule`: 新增 `max_triggers: int = 0`
- `PortfolioState`: 新增 `rule_trigger_counts: Dict[str, int]`
- `_execute_rule_action`: BUY_ADD 前检查触发次数，超限跳过

---

### FIX-4.3【1-G】评级解析中文/全角

| 项目 | 详情 |
|------|------|
| **文件** | `tradingagents/agents/utils/rating.py` |
| **原因** | 仅支持英文5级；正则不匹配全角 `：`；无否定检测 → "do not buy"返回 Buy |

**修复**:
- `_CN_RATING_MAP`: 买入→Buy/增持→Overweight/持有→Hold/减持→Underweight/卖出→Sell
- `_RATING_LABEL_RE`: 字符类增加 `：` 和中文冒号
- `_CN_RATING_RE`: 中文"评级：买入"标签匹配
- `_has_negation_before()`: 目标词前 3 词内否定词检测

---

### FIX-4.4【2-G】trailing stop current_date 未设置

| 项目 | 详情 |
|------|------|
| **文件** | `backtest/backtest_engine.py:178-179` |
| **原因** | `portfolio.current_date` 在循环结束(line 345)才设置 → 移动止损永不触发 |

**修复**: 循环体首行设 `portfolio.current_date = date_str`；移除旧行

---

### FIX-4.5【1-F】Quick refresh 覆盖季度风控规则

| 项目 | 详情 |
|------|------|
| **文件** | `backtest_hybrid.py:725-736` |
| **原因** | Quick market-only 分析常无规则输出→构建零规则决策→覆盖季度止损/止盈规则 |

**修复**: quick refresh 时合并上轮决策的 `STOP_LOSS/TAKE_PROFIT/CIRCUIT_BREAK` 规则

---

### FIX-4.6【2-J】中文条件 SyntaxError

| 项目 | 详情 |
|------|------|
| **文件** | `backtest/decision_engine.py:44-87` |
| **原因** | trigger_sql 为空时 fallback 到中文 trigger_condition → eval 无法解析中文操作符 |

**修复**:
- `_normalize_chinese_condition()`: 中文操作符归一化映射
  - 跌破→`<` | 超过→`>` | 且→`and` | 或→`or` | 不低于→`>=` | 不超过→`<=`
- `_convert_structured_rules`: fallback 条件先过归一化再传给 eval_condition

---

## 修改文件汇总

| 文件 | 涉及修复 | 修改行数 |
|------|---------|---------|
| `backtest/trading_rules.py` | E1, A1, 2-K | ~30 |
| `backtest/decision_engine.py` | E1, 1-B, 2-F, 2-J | ~60 |
| `backtest/execution_engine.py` | 3-A, 3-B, 3-C, 3-D, 1-B, 2-K | ~80 |
| `backtest/data_layer.py` | A1 | ~5 |
| `backtest/models.py` | 3-A, 3-C, 3-D, 2-K | ~8 |
| `backtest/cache_manager.py` | 2-F | ~30 |
| `backtest/backtest_engine.py` | 2-G | ~5 |
| `backtest_hybrid.py` | X9, 1-F | ~30 |
| `tradingagents/graph/conditional_logic.py` | G1 | ~10 |
| `tradingagents/graph/setup.py` | G1 | ~15 |
| `tradingagents/graph/trading_graph.py` | 1-H | ~20 |
| `tradingagents/agents/utils/memory.py` | 1-H | ~10 |
| `tradingagents/agents/utils/rating.py` | 1-G | ~60 |
| `tradingagents/dataflows/akshare_data.py` | D2, 2-E | ~40 |
| `tradingagents/dataflows/stockstats_utils.py` | D1 | ~5 |
| `tradingagents/l1/data_loader_fixed.py` | D2 | ~15 |
| **合计** | **19 项修复** | **~430 行** |

---

## 验证结果

所有修复均通过单元级验证测试：

| 批次 | 修复数 | 测试通过 |
|------|--------|---------|
| 第一批 | 4/4 | ✅ E1 parse签名 ✅ A1 MA三层回退 ✅ G1 Numeric路由 ✅ X9 止损提取 |
| 第二批 | 5/5 | ✅ D2 发布日估算 ✅ D1 bfill前置 ✅ 2-E PE过滤 ✅ 2-F 缓存键 ✅ 1-H memory过滤 |
| 第三批 | 4/4 | ✅ 3-A T+1限卖 ✅ 3-B 分板块涨跌停 ✅ 3-C 百分比止损 ✅ 3-D 费率+过户费 |
| 第四批 | 6/32 | ✅ 1-B 降至语义 ✅ 2-K 加仓上限 ✅ 1-G 中文评级 ✅ 2-G trailing ✅ 1-F quick合并 ✅ 2-J 中文条件 |

---

## 待修复项（第四批剩余 26 项）

| 优先级 | 数量 | 编号 |
|--------|------|------|
| **P1** 显著扭曲 | 11 | 2-H(eval沙箱), 2-I(CROSSOVER), 2-L(L1退避), 2-M(季度触发), 2-N(日内成交), 2-O(Sharpe), 2-P(_load_cache), 3-E(高水位), 3-F(幸存偏差), 3-G(StockTwits), 3-H(entry_price), 1-A(PM持仓), 1-E(fallback), 1-I(L1不写fundamentals), 2-R(rule_expr缺陷) |
| **P2** 改善性 | 14 | 2-Q(前端契约), 2-R, 停牌/复牌, Debate近因, 工具失败, market analyst校验, 仓位max(100), rule_type枚举, memory清理, Excel硬编码, 季度缓存 |

---

## 风险评估

1. **D2 保守估计**: `_estimate_publish_date` 使用监管截止日作为发布日估计，实际发布日通常更早→略显保守但安全
2. **前复权 3-C**: 百分比止损作为兜底，与绝对止损并行→不会过度卖出，但可能双重触发需关注日志
3. **T+1 3-A**: `shares_settling` 在 `execute()` 日初清零→正确模拟隔夜结算
4. **Memory 1-H**: 回测模式下跳过未来条目→减少 past_context 丰富度但消除前视污染

---

*报告生成: 2026-07-21 | 修复团队: WorkBuddy*
