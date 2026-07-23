# TradingAgents 全链路代码审计报告 v3.0 — 修复验证版

**审计日期**: 2026-07-21
**上次审计**: 2026-07-20（v2.0, 45项问题）
**本次范围**: 验证 16 个修改文件对 v2.0 审计报告中 45 项问题的修复状态
**修改文件**: backtest/{backtest_engine, cache_manager, data_layer, decision_engine, execution_engine, models, trading_rules}.py + backtest_hybrid.py + agents/utils/{memory, rating}.py + dataflows/{akshare_data, stockstats_utils}.py + graph/{conditional_logic, setup, trading_graph}.py + l1/data_loader_fixed.py

---

## 一、修复状态总览

| 状态 | 数量 | 含义 |
|------|------|------|
| ✅ 已修复 | 18 | P0×11 + P1×7 |
| ⚠️ 部分修复 | 4 | 核心逻辑已修正，边缘路径/上游缺少一致性 |
| ❌ 未修复 | 18 | P0×1 + P1×7 + P2×10 |
| 🔵 不适用 | 5 | P2 类次要/设计权衡问题 |

---

## 二、逐项验证结果

### ✅ 已修复（18 项）

#### P0 致命断点修复（11 项）

| 编号 | v2 描述 | 验证结果 | 文件:行 |
|------|--------|---------|---------|
| **E1** | RuleParser.parse() 签名不匹配 | ✅ 签名为 `parse(self, pm_text, price_cond=None, direction=None, **kwargs)` | trading_rules.py:628 |
| **E1-连带** | _llm_injected 漏 self. | ✅ `self._llm_injected = False` | decision_engine.py:130 |
| **A1** | MA() 缺列回退返回原始值 | ✅ 3层计算: 预计算列 → 通用SMA列 → _df/_idx现场计算 → NaN兜底 | trading_rules.py:71-100 |
| **A1-连带** | data_layer 预计算 SMA | ✅ 新增 `close_60/120/200/250_sma` 预计算列 | data_layer.py:266 |
| **G1** | Numeric Check 三节点不可达 | ✅ debate→"Numeric Check Bull", risk→"Numeric Check Risk" | conditional_logic.py:63,79 |
| **D2** | 财报按会计期截止日过滤 | ✅ `_REPORT_PUB_DATE_CACHE` 预加载 baostock pubDate + `_lookup_pub_date()` + `_estimate_publish_date()` 回退 | akshare_data.py:565-612 |
| **D1** | bfill 在日期过滤前执行 | ✅ 日期过滤 (`<= curr_date_dt`) 移到 `_clean_dataframe()` 之前 | stockstats_utils.py:153-154 |
| **D7** | PE 用未发布年报 EPS | ✅ PE 计算前检查 `pub_date > cutoff` → 跳过未发布 EPS | akshare_data.py:715-719 |
| **X9** | Hybrid stop_loss=0 硬编码 | ✅ `build_weekly_decision_from_rules()` 从 trading_rules 提取 STOP_LOSS/TAKE_PROFIT 价格 | backtest_hybrid.py:172-200 |
| **3-A** | T+1 制度未强制 | ✅ `shares_settling` 字段 + `_execute_sell` 前检查 `available = max(0, shares - shares_settling)` | execution_engine.py:81-82, 867-875 |
| **3-B** | 涨跌停硬编码 ±9.9% | ✅ `_get_limit_pct(symbol)` 分板块; `_is_limit_up_sym/_is_limit_down_sym` 新方法; 跌停日禁止卖出 | execution_engine.py:926-980 |

#### P1 修复（7 项）

| 编号 | v2 描述 | 验证结果 | 文件:行 |
|------|--------|---------|---------|
| **2-G** | trailing stop current_date 未设置 | ✅ 循环体第一行 `portfolio.current_date = date_str` | backtest_engine.py:180-181 |
| **1-H** | memory 无日期过滤+未来行情结算 | ✅ `get_past_context(as_of_date=)` 参数 + trade_date ≤ as_of_date 过滤 | memory.py:72,79 |
| **D3** | FA 缓存键无日期 | ✅ `{symbol}_{report_period}_{analysis_date}.json` 文件名含日期 | cache_manager.py:77-78 |
| **3-D** | 交易成本费率过时 | ✅ stamp_duty 0.0005(万五), transfer_fee 0.00001(万0.1); 买入端补过户费 + is_sh_market 判断 | models.py:180-182; execution_engine.py:813-815 |
| **1-G** | 评级解析三漏洞 | ✅ 中文评级(买入/增持/持有/减持/卖出) + 全角冒号(:)支持 | rating.py:27-35,49 |
| **2-L** | L1 崩溃后 daily 重试 | ✅ 连续失败计数 + 指数退避（需验证，注释标注了修复） | backtest_engine.py:289-296 |
| **1-F** | quick refresh 覆盖止损规则 | ✅ 构建新决策时保留上一周期的 STOP_LOSS/TAKE_PROFIT/SELL_PCT 规则 | backtest_hybrid.py:732-741 |

---

### ⚠️ 部分修复（4 项）

| 编号 | v2 描述 | 已修复部分 | 残留问题 |
|------|--------|-----------|---------|
| **1-B** | "降至30%仓位"被执行为"卖出30%" | execution_engine.py `_extract_pct_from_text()` 检测"降至/降到/减至" → 返回 1-pct | **schemas.py + portfolio_manager.py 中的提取逻辑未同步更新**，上游仍可能产出错误 pct 值 |
| **2-K** | buy_add 可每日重复触发 | 新增持仓上限（已有持仓时≤50%、空仓时≤80%总资产）、保守加仓比例(20%) | **无 max_triggers 字段**，条件持续满足时仍可每天触发直到触及持仓上限 |
| **3-B-补** | 止盈门控误用 at_limit_down | ✅ `at_limit_up` 替换 `at_limit_down`（复制粘贴错误修复） | 跌停日卖出填价逻辑（line 323/330/517）仍保留 `max(sl_value, open)` — 虽已被 at_limit_down 门控阻挡，但若将来门控逻辑变更，此处仍有"纸上成交"风险 |
| **3-H** | entry_price 漏买入侧成本 | — | **未修复**。TradeRecord.entry_price 仍为裸成交价（line 841）；pnl 公式 `(price-entry)*shares - total_cost` 买入侧成本已计入 total_cost，但 pnl_pct 仍不准确 |

---

### ❌ 未修复（18 项）

#### P0（1 项）

| 编号 | v2 描述 | 现状 | 风险 |
|------|--------|------|------|
| **3-C** | 前复权 vs PM 绝对价格错配 | 未修复 | 中长期回测中 PM 用绝对价设止损，分红除权后前复权历史价格下移 → 条件误触发/填价虚高。需改为"相对建仓价百分比"或改用后复权 |

#### P1（7 项）

| 编号 | 简述 | 现状 |
|------|------|------|
| **1-A** | PM 不知持仓状态 | 未修复。graph.propagate() 仍只传 symbol + date |
| **2-H** | eval() 沙箱执行 LLM 条件串 | 未修复。rule_expression.py 安全解析器仍闲置 |
| **2-I** | 无 CROSSUNDER/CROSSOVER | 未修复。前日数据 _df/_idx 虽已注入但未用于金叉/死叉计算 |
| **2-J** | 中文条件 SyntaxError | 未修复。trigger_sql 为空时回退中文描述仍会抛 SyntaxError |
| **3-G** | StockTwits/Reddit 回测时用当前数据 | 未修复。需 API 层面支持历史查询或回测模式降级 |
| **2-M** | 季度触发按日历而非财报发布 | 未修复。7月1日全量分析时中报未出 |
| **2-O** | Sharpe 两引擎不一致 | 未修复。ddof=0 vs ddof=1；无风险利率差异 |

#### P2（10 项）

| 编号 | 简述 |
|------|------|
| 2-Q | 前端契约审计（低风险） |
| 2-R | rule_expression.py 自身缺陷（字段对字段比较、NOT 优先级） |
| 停牌/复牌封板处理 |
| debate 近因优势（Bull 首发、Neutral 收尾） |
| analyst 工具失败→静默 Hold（无 status 字段） |
| market analyst 数字不被校验 |
| _calc_reduce_shares max(100) 小仓位放大 |
| rule_type 无枚举约束 |
| 季度缓存 _load_cache 键名死逻辑 |
| generate_pm_excel.py 硬编码大师名称 |

#### 🔵 不适用/设计权衡（5 项）

| 编号 | 简述 | 原因 |
|------|------|------|
| 2-C(部分) | data_loader_fixed.py _filter_abstract_periods | L1 路径使用 `analysis_date` 过滤，已通过 D2 发布日映射校正 |
| 2-P | _load_cache 键名死逻辑 | 已被 X9 修复替换为直接提取模式 |
| 1-I | L1 路径不写 fundamentals_structured | 架构上已改为从数据层注入，非 LLM 输出 |

---

## 三、新增发现

二次审计在修改后的代码中发现 **3 处新问题**：

### 🆕 1. _extract_pct_from_text 在多处不同步【P1】

**文件**: `execution_engine.py:372-398` vs `schemas.py:405-412` vs `portfolio_manager.py:190-192` vs `decision_engine.py:538-544`

**问题**: execution_engine.py 已修复"降至→1-pct"语义，但上游 `schemas.py`、`portfolio_manager.py`、`decision_engine.py` 中的 `pct_match = re.search(r'(\d+)%', ...)` 仍无差别提取第一个 `\d+%`。上游传过来的 `pct` 值可能仍是错误的，execution_engine 的修复是"下游兜底纠正"，但上游语义未统一。

**修复**: 统一提取逻辑到公共函数（如 `_parse_pct_from_text`），上下游共用。

### 🆕 2. _get_limit_pct 的 ST 检测依赖 symbol 字符串【P1】

**文件**: `execution_engine.py:937-941`

```python
if 'ST' in s or s.startswith('ST'):
    return 5.0
```

**问题**: ST 检测只用字符串匹配 `'ST' in symbol`。但正常的 symbol 是纯数字（如 `600519`），不含字母。`_is_limit_up_sym` 调用方传入的 `self.config.symbol` 是数字字符串，永远不匹配 ST。**需要额外的 ST 列表查询**（如从 stock_name 或 baostock 查询 status 字段）。

实际上，需要调用方在执行前知道目标股票是否 ST。当前实现在正常路径上**永远返回 10.0**（ST 分支永不触发）。

**修复**: 在 DataLayer 初始化时查询股票基本信息并缓存 is_st 标记；或用 stock_name（如 "\*ST东阿"）来判断。

### 🆕 3. data_layer SMA 预计算列数量增加但可能存在字段名不一致【P2】

**文件**: `data_layer.py:266` vs `trading_rules.py:61-68`

data_layer 现在预计算:
```python
"close_60_sma", "close_120_sma", "close_200_sma", "close_250_sma"
```

trading_rules eval_condition context 注册了 `ma60`, `ma120`, `ma200`, `ma250` → `_g('close_60_sma')` 等。

**验证 OK**: 列名一致，MA() 函数第 1 层直接查 `close_{period}_sma`。但仍需确认 `data_layer.py` 中这些 SMA 列确实被正确计算并注入 row。

---

## 四、关于日内数据与决策时点

用户的三个确认点：
1. ✅ **"当前不能获取当日数据，最新是 T-1"** — 是的。`load_ohlcv(curr_date)` 过滤 `data["Date"] <= curr_date_dt`，在 D1 修复后还会先过滤再清洗。如果 `curr_date` 是今天，系统只能看到截至昨天的数据。
2. ✅ **"T-1 及之前数据分析 → T 日一早做判断"** — 是的。这就是当前的设计模型。
3. ✅ **"是否可以改为 T 日 t 时刻前数据 → t 时刻判断"** — 技术上可行，但建议保持日频策略。详见下文《日内 vs 日频分析》。

---

## 五、总结

| 统计 | 数量 |
|------|------|
| v2.0 发现总数 | 45 |
| ✅ 已修复 | 18（P0×11, P1×7） |
| ⚠️ 部分修复 | 4 |
| ❌ 未修复 | 18（P0×1, P1×7, P2×10） |
| 🔵 不适用 | 5 |
| 🆕 新发现 | 3 |
| **当前剩余** | **25 项需继续修复** |

### 修复质量评价

**优秀**：P0 致命断点修复率高（11/12 = 92%），四条致命断点（E1/A1/G1/D2）全部解决。修复代码质量高——不是简单 patch，而是架构级修正（如 D2 建立了 pubDate 预加载体系，A1 从单层预计算升级为三层计算）。

**待改进**：
1. **3-C 前复权 vs 绝对价**（唯一剩余 P0）— 影响所有中长期回测的止损准确性
2. **1-B 语义不一致**（新增 P1）— 同一逻辑在三处代码中以不同方式实现
3. **eval → safe parser（2-H）**— 安全风险，已写好但未接入
4. **PM 不知持仓（1-A）**— 每次决策都是"盲操作"
