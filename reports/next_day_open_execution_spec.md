# 规则类交易顺延次日开盘价成交 — 方案设计

**日期**: 2026-07-22
**目标**: 消除"规则条件用当日 close 判断，当日 close 成交"的前视问题
**改动范围**: `backtest/execution_engine.py`（主，~30 行）+ `backtest/models.py`（~5 行）

---

## 一、问题分析

### 当前行为

```
T 日 15:00 收盘 → 计算 SMA/RSI/MACD → 规则触发 → 以 T 日 close 成交
                            ↑                          ↑
                      需收盘才能得知              收盘后才能成交
```

### 问题

1. **时序前视**：15:00 才知道 `close < MA(close,200)` 成立，但 15:00 已无法以当日 close 成交
2. **日内阻断**：早盘 9:35 就跌破 MA200 了，但系统等到 15:00 才"发现"
3. **回测失真**：最大差异可达一日涨跌幅（±10%~±20%）

### 正确的时序模型

```
T 日 15:00 收盘 → 计算 SMA/RSI/MACD → 规则触发 → 标记"待 T+1 开盘执行"
T+1 日 9:30 → 以 open 价成交
```

---

## 二、不影响的部分

以下路径已经正确使用盘中高低价，**无需改动**：

```
_execute_buy / _execute_sell → 内部的成交价由调用方传入，不自行决定
_check_exit_signals:
  - stop_loss:   low 触及止损线 → sell_price = max(stop, open)  ✅ 用 low 触价
  - take_profit: high 触及止盈线 → sell_price = min(tp, open)   ✅ 用 high 触价
  - trailing_stop: close 判断触发，但 stop 用盘中 high 计算     ✅ 
```

这些是**触价型**条件（"价格跌到某个值就卖"），本身就依赖盘中数据。不改。

---

## 三、改动范围

### 3.1 哪些交易需要延到次日

只有**规则类**交易（`_check_trading_rules` 触发的）才延次日 open：

| 规则动作 | 当前成交价 | 改为 |
|---------|-----------|------|
| `BUY_ADD` | close | 次日 open |
| `SELL_PCT` | close | 次日 open |
| `SELL_ALL` | close | 次日 open |
| `STOP_LOSS`（规则触发） | 从 condition 提取 | 保持不变（已用 low/high） |
| `TAKE_PROFIT`（规则触发） | 从 condition 提取 | 保持不变（已用 low/high） |
| `_check_exit_signals` 简单信号 | close / low / high | 保持不变 |
| `_check_entry_signals` 简单信号 | close | 次日 open |

### 3.2 实现方案：延迟执行队列

在 `PortfolioState` 加一个**待执行订单**字段：

```python
@dataclass
class PendingOrder:
    """待 T+1 开盘执行的订单。"""
    action: str          # "BUY" / "SELL"
    shares: int          # 股数
    limit_price: float   # 参考价（用于次日的涨跌停/资金检查）
    reason: str          # 触发原因

@dataclass  
class PortfolioState:
    # ... 现有字段 ...
    pending_order: Optional[PendingOrder] = None  # ★ 新增
```

### 3.3 执行流程变更

```
ExecutionEngine.execute(row, idx, ...):
  
  # 步骤 0: 检查是否有待执行的 T-1 订单
  if portfolio.pending_order is not None:
      order = portfolio.pending_order
      
      # 0a. 停牌/涨跌停检查
      if is_suspended or (order.action == "BUY" and at_limit_up):
          # 无法成交，订单保留到下一天（或取消，看设计）
          pass
      elif order.action == "SELL" and at_limit_down:
          pass  # 跌停无法卖，保留
      else:
          # 执行：以当日 open 价成交
          execute_pending_order(order, price=open_price)
          portfolio.pending_order = None
  
  # 步骤 1-3: 正常规则评估（与现在相同）
  # 但 BUY_ADD / SELL_PCT / BUY / SELL 不再当场执行，
  # 而是写入 portfolio.pending_order

  # 步骤 4: 如果有新订单且当日已有 pending_order →
  # 新订单覆盖旧订单（只保留最新的交易意图）
```

### 3.4 关键决策点

| 问题 | 决策 |
|------|------|
| 新旧订单冲突 | **覆盖**。如果 T-1 有一笔 BUY pending，T 日规则触发了一笔 SELL：SELL 覆盖 BUY。原因：T 日规则基于更新数据，更可信 |
| 连续两天同方向 | **覆盖**。T-1 BUY 1000股 pending，T 日又触发 BUY 500股 → pending 变为最新的一笔（500股）。原因：避免单日过度交易 |
| 停牌时 pending | **保留**。如果 T+1 停牌，pending 延续到 T+2。最多保留 3 天，超期取消 |
| 规则触发了但次日开盘无法成交 | 按当前 `_execute_buy`/`_execute_sell` 逻辑（资金不足→调股数、涨跌停→跳过） |

### 3.5 涨跌停保护

```
T 日 15:00: BUY 规则触发 → pending = (BUY, 1000 shares)
T+1 日 9:30: 开盘即涨停 → 无法买入 → pending 取消，不保留
```

已在 `execute()` 的步骤 0 中处理（涨跌停检查复用现有 `at_limit_up/down`）。

---

## 四、回测影响分析

### 正向影响

1. **消除时序前视**：不会出现"用 T 日 close 成交但条件判断也用 T 日 close"的矛盾
2. **与实盘对齐**：回测结果更接近真实交易表现
3. **规则日频更新自然衔接**：T 日分析 → T+1 生效 → 15 天后再分析 → 更新规则 → T+16 生效

### 负向影响

1. **增加一日滞后**：所有规则交易延迟 1 天执行 → 牛市可能错过一日涨幅，熊市可能减少一日损失（中性）
2. **同标的单策略回测**：日频数据粒度下，影响有限（±1 天窗口）
3. **连续触发**：T 日 BUY → T+1 开盘执行 → T+1 收盘又触发 → T+2 开盘执行 → 正常，非问题

### 回测对比建议

运行同一回测两版，对比 Sharpe/MaxDD/胜率：

```python
# 旧版（当前）
engine.execute(portfolio, decision, row, idx, df)
# → 规则成交价 = close

# 新版
engine.execute(portfolio, decision, row, idx, df)  
# → 规则成交价 = 次日 open（通过 pending_order 机制）
```

---

## 五、实施步骤

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `backtest/models.py` | 新增 `PendingOrder` 数据类 + `PortfolioState.pending_order` 字段 |
| 2 | `backtest/execution_engine.py:70-95` | `execute()` 开头增加"步骤 0：执行 T-1 pending order" |
| 3 | `backtest/execution_engine.py:144-156` | BUY/SELL 执行不再当场 `_execute_buy/sell`，改为写 `pending_order` |
| 4 | `backtest/execution_engine.py:235-350` | `_check_trading_rules` 中 BUY_ADD/SELL_PCT/SELL_ALL 同样写 pending |
| 5 | `backtest_hybrid.py:792-800` | `_run_daily_execution` 后的 daily_state 记录 pending 状态 |
| 6 | 回测对比 | 同一参数跑两版，对比结果 |

---

## 六、不做的范围

- ❌ 日内分时数据源（需要 tick/1min bar，数据库已有方案）
- ❌ 事件驱动规则引擎（实时价格触发，非日终评估）
- ❌ 多订单队列（当前只保留最新一笔）
- ❌ 止损/止盈改延迟（这些本就该盘中触发，不改）

---

*给修复团队的补充说明：这只是日频回测层面能做到的最佳折中。如果要追求真正的"日内 t 时刻触发"，需要切换到 event-driven 架构 + 分钟级数据源，那是另一个项目。*
