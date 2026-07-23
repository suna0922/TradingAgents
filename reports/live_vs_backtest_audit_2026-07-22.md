# 实盘 vs 回测逻辑差异审计

**日期**: 2026-07-22  
**审计范围**: 全项目（数据源、执行引擎、Graph 管线、Memory、配置、Broker、调度）  
**结论**: TradingAgents 是纯回测/信号生成系统，**无线上交易能力**。以下逐层列出差异和缺口。

---

## 一、总览

| 层级 | 回测 | 实盘 | 差异 |
|------|------|------|------|
| 数据源 | akshare/baostock 历史日线 | **需** 实时行情（Tick/分时/WebSocket） | 无实时数据接入 |
| 执行引擎 | 内存模拟（`portfolio.cash -= x`） | **需** 券商 API 下单/撤单/持仓同步 | 无券商集成 |
| Graph 管线 | `propagate(symbol, historical_date)` | 可复用但缺 `position_state` 注入 | Web 路径缺参数 |
| Memory | 有 `current_date`/`as_of_date` 前视防护 | 不需要防护但结算逻辑一致 | `propagate_stream()` 缺参数 |
| 触发调度 | `for idx in range(total_days)` 顺序遍历 | **需** 定时器（每日收盘/开盘触发） | 无调度器 |
| 风控 | 涨跌停/T+1 模拟 | **需** 券商级风控 + 实时涨跌停状态 | 模拟 ≠ 真实 |
| 配置 | 无模式标志 | 无模式标志 | 同 |

---

## 二、逐层审计

### 2.1 数据源：无实时行情

**现状**:
- `akshare_data.py` 全部调用历史 API（`ak.stock_zh_a_hist`、`baostock.query_history_k_data_plus`）
- `load_ohlcv()` 缓存 5 年日线到本地，有 `curr_date` 前视防护
- 无 Tick、1min/5min K 线、Level-2、WebSocket 实时推流

**回测行为**:
```
load_ohlcv(symbol, curr_date="2026-03-15")
→ 从缓存文件读取截至 2026-03-15 的日线
→ bfill 用 ≤ curr_date 的数据（D1 修复后）
→ 条件判断用 T 日 close
```

**实盘需求**:
```
subscribe(symbol)
→ WebSocket 实时推送 1min K 线
→ 缓存最近 200 根 K 线用于 MA/RSI 计算
→ 触发条件时用最新价
```

| 差异点 | 回测 | 实盘 | 风险 |
|--------|------|------|------|
| 数据时效 | T 日 15:00 后一次性获取 | 实时推送 | 回测用收盘价成交 = 前视 |
| 粒度 | 日线 (OHLCV) | 需分时 | 日内 MA/RSI 无法在回测中复现 |
| 数据范围 | 按 `curr_date` 截断 | 无截断需求 | 实盘中不需要前视防护 |

---

### 2.2 执行引擎：无真实交易

**现状**:
- `ExecutionEngine.execute()` 纯内存操作：
  ```python
  portfolio.cash -= total_cost
  portfolio.shares += shares
  ```
- 无 `place_order()` / `cancel_order()` / `sync_position()`
- A 股规则（T+1、涨跌停、按手取整）是**模拟**行为

**回测行为**:
```
T 日: 规则触发 BUY → _execute_buy() → cash -= cost, shares += 1000
结果: 内存中的 PortfolioState 更新，零延迟成交
```

**实盘需求**:
```
T 日: 规则触发 BUY → place_order(symbol, price=limit, qty=1000)
     → 券商返回 order_id → 轮询成交状态 → 部分成交/撤单/废单处理
T+1 日: sync_position() → 确认成交数量 → 更新内存 PortfolioState
```

| 差异点 | 回测 | 实盘 | 风险 |
|--------|------|------|------|
| 成交假设 | 100% 按指定价成交 | 可能部分成交/不成交 | 回测高估流动性 |
| 滑点 | 固定 `slippage_pct=0.1%` | 实际盘口滑点 | 大盘股 OK，小盘股偏差大 |
| 手续费 | 固定费率 | 券商实际费率（可能不同） | 微小差异 |
| T+1 | 模拟 `shares_settling` | 券商系统强制 T+1 | 回测正确但不可卖检查在内存 |
| 涨跌停 | `pct_chg >= limit` 判断 | 封板深度、排队成交 | 回测假设封板即不可交易，实盘排到就能买 |

---

### 2.3 Graph 管线：可复用但参数不全

**`propagate()` 签名**:
```python
def propagate(self, company_name, trade_date, asset_type="stock", position_state=""):
```
- `trade_date` 可以是今天 → 单次分析可跑
- `position_state` 实盘必须传入（当前仅 `backtest_hybrid.py` 传入）
- `cli/main.py` 和 `main.py` 未传 `position_state`

**`propagate_stream()` 缺陷**:
```python
# 第 448 行: 缺少 current_date 参数
self._resolve_pending_entries(company_name)  # ← 应该传 current_date=trade_date
```
- Web SSE 流式分析路径缺少前视防护
- 实盘场景下影响小（不会有"未来"数据），但参数一致性缺失

| 差异点 | 回测 | 实盘 CLI/Web | 风险 |
|--------|------|-------------|------|
| `position_state` | ✅ 传入 | ❌ 未传入 | PM 不知道当前持仓 |
| `current_date` (resolve) | ✅ 传入 | Web ❌ 未传入 | Web 路径 memory 结算无截止 |
| `trade_date` | 历史日期 | 今天 | 都能工作 |

---

### 2.4 Memory / Reflection：Web 路径缺参数

**已修复的回测路径**（`trading_graph.py:408`）:
```python
self._resolve_pending_entries(company_name, current_date=trade_date)
self.memory_log.get_past_context(company_name, as_of_date=trade_date)
```

**未修复的 Web 路径**（`trading_graph.py:448`）:
```python
self._resolve_pending_entries(company_name)  # 缺 current_date!
```

**实盘影响**:
- 实盘中不会有"持有期超前的条目"（因为没有未来数据），所以不传 `current_date` 不会出错
- 但参数一致性缺失 → 如果被调用方在实盘中用了历史 memory 数据，可能结算到不应该现在就知道的结果

---

### 2.5 调度机制：纯手动

**回测调度**:
```python
# backtest_hybrid.py 的 run() 方法
for idx in range(total_days):
    row = self.df.iloc[idx]
    date_str = str(row["date"])
    # → 顺序遍历历史每一天，模拟时间推进
```

**实盘需要**:
```
# 不存在这些代码
scheduler.every().day.at("09:25").do(run_pre_market_analysis)   # 集合竞价后
scheduler.every().day.at("15:05").do(run_post_market_analysis)  # 收盘后
```

| 差异点 | 回测 | 实盘 | 缺口 |
|--------|------|------|------|
| 时间推进 | `for idx in range(N)` | 真实时钟 | 需 crontab/systemd timer |
| 触发粒度 | 每日一次（遍历） | 可多时段 | 需设计调度策略 |
| 状态持久化 | 内存 `PortfolioState` | 需跨进程持久化 | 需 DB/文件存储 |

---

### 2.6 前视防护：回测有、实盘不需要、但历史分析需要

**回测已有防护**:

| 防护项 | 机制 | 实盘是否需要 |
|--------|------|------------|
| bfill 日期过滤 (D1) | OHLCV 先按 `curr_date` 截断再清洗 | ❌ 不需要（实时数据天然无未来） |
| 财报发布日过滤 (D2) | `_estimate_publish_date()` + `set_report_pub_dates()` | ❌ 不需要（但历史查询时需要） |
| FA 缓存键含日期 (2-F) | `{symbol}_{period}_{date}.json` | ❌ 不需要 |
| Memory as_of_date (1-H) | `get_past_context(as_of_date=)` | ✅ 需要（防止历史记忆泄露） |
| PE 年报未发布过滤 (2-E) | `pub_date > cutoff → 跳过` | ❌ 不需要 |

**关键洞察**: 前视防护是回测专有的。如果将来实盘做了"基于历史数据的分析"（如"最近 3 年的同类走势"），前视防护需要重新启用。

---

### 2.7 状态管理：无持久化

**回测状态**:
```python
class PortfolioState:
    cash: float = 1_000_000.0
    shares: int = 0
    shares_settling: int = 0
    # ... 全部在内存中，进程结束即丢失
```

**实盘需要**:
```python
# 需要持久化层
class PortfolioStateDB:
    def save(self): ...     # 写入 SQLite/Postgres
    def load(self): ...     # 启动时恢复
    def snapshot(self): ... # 每日快照
```

| 差异点 | 回测 | 实盘 | 风险 |
|--------|------|------|------|
| 持仓 | 内存 | 需 DB 持久化 | 进程崩溃丢失所有状态 |
| 交易历史 | `List[TradeRecord]` 内存 | 需审计级持久化 | 税务/合规需求 |
| 跨日状态 | 同一进程连续跑 | 可能跨进程/跨天 | shares_settling 需持久化 |

---

### 2.8 结论：实盘缺失清单

| 序号 | 模块 | 缺失 |
|------|------|------|
| 1 | 数据源 | 实时行情 API（东财/同花顺 websocket） |
| 2 | 券商 | 下单/撤单/持仓同步 API |
| 3 | 调度 | 定时器（cron/systemd/APScheduler） |
| 4 | 状态 | PortfolioState 持久化（DB/文件） |
| 5 | 风控 | 券商级风控 + 实时涨跌停状态 |
| 6 | Web 路径 | `propagate_stream()` 缺 `current_date` |
| 7 | CLI 路径 | `propagate()` 缺 `position_state` |
| 8 | 成交模拟 | 滑点/流动性/部分成交模型 |
| 9 | 监控 | 异常告警、日志聚合、健康检查 |

---

### 2.9 可直接复用的部分（零改动）

| 组件 | 可直接用于实盘 | 原因 |
|------|-------------|------|
| `eval_condition()` | ✅ | 接收 dict，无论实时/历史数据都可调用 |
| `RuleParser.parse()` | ✅ | 解析 PM 输出的结构化规则，无时序依赖 |
| `TradingRule.evaluate_all()` | ✅ | 条件评估，数据来源抽象 |
| 费率配置 | ✅ | `stamp_duty_rate`、`commission_rate` 等 |
| `parse_rating()` | ✅ | 评级解析，纯文本输入 |
| Graph Agent 管线 | ✅ (需补参数) | 分析逻辑与数据源解耦 |

---

## 三、路线图建议

| 阶段 | 内容 | 工作量 |
|------|------|--------|
| **Phase 1** (现在就做) | 补 `propagate_stream()` 的 `current_date` + CLI/Web 的 `position_state` | 0.5 天 |
| **Phase 2** (实盘探索) | 接入东财 WebSocket 实时行情 + 写数据适配层 | 2-3 天 |
| **Phase 3** (实盘核心) | 券商 API 对接 + PortfolioState 持久化 + 调度器 | 3-5 天 |
| **Phase 4** (实盘上线) | 风控层 + 监控 + 异常处理 + 灰度验证 | 5-10 天 |

---

*注: 本审计基于 2026-07-22 代码快照。D1/D2/1-H 等前视防护修复后，回测已有完善的防作弊机制。但这些防护在实盘场景下反过来变成了"过度防御"——需要评估是否保留为可选模式。*
