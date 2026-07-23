# 回测刷新逻辑重构 — 修复说明

**日期**: 2026-07-22
**目标文件**: `backtest_hybrid.py`（为主，1 个文件）
**改动范围**: 约 30 行，删除/替换/新增各若干

---

## 目标架构

```
每天循环:
  if 新季报/年报发布:
    → run_full_analysis（基本面+技术面+辩论+PM，完整管线）
  elif stale=15d 或 price_change>10% 或 force_decision_next_day:
    → run_quick_analysis（缓存的基本面报告 + 新技术面 + 辩论 + PM）
  → L2 每日规则执行
```

`_is_quarter_start` 日历季度检测 → **删除**，不再驱动任何分析。

---

## 改动清单

### 改动 1: 删除季度触发（~第 700 行）

**当前代码**（`backtest_hybrid.py:699-739`）:
```python
            # ── 季度检测 → L1 完整分析 ──
            is_q_start, current_period = _is_quarter_start(date_str, self.last_quarter_period)
            if is_q_start:
                logger.info(...)
                try:
                    result = self.l1_analyzer.run_full_analysis(self.symbol, date_str)
                    self.last_quarter_period = current_period
                    # ... 更新 active_decision ...
```

**改为**:
```python
            # ── L1 完整分析：仅在新季报/年报发布时触发 ──
            is_new_report = self._new_quarterly_report_available(date_str)
            if is_new_report:
                logger.info(f"\n{'─'*40}")
                logger.info(f"[SCHEDULE] New report available @ {date_str}")
                logger.info(f"{'─'*40}")
                try:
                    result = self.l1_analyzer.run_full_analysis(self.symbol, date_str)
                    # ★ 保存基本面报告文本，供后续 quick 分析复用
                    self._last_fundamentals_report = result.fundamentals_report
                    self.last_quarter_period = current_period  # 见备注: current_period 改为从 _compute_report_period 获取

                    # 更新基本面指标 & 记录报告期
                    self.fa_metrics = result.fa_metrics
                    self._last_fa_report_period = self._compute_report_period(date_str)

                    # 构建 WeeklyDecision → 更新 active_decision
                    new_decision = build_weekly_decision_from_rules(
                        result.trading_rules,
                        signal_raw=result.signal,
                        pm_raw_output=result.pm_raw_output,
                        decision_date=date_str,
                    )
                    self.active_decision = new_decision
                    self.portfolio.active_decision = new_decision
                    self.portfolio.last_decision_executed_date = ""
                    self.last_decision_price = close
                    self.last_decision_idx = actual_idx
                    self.force_decision_next_day = False

                    self.l1_analyses_log.append(result.to_dict())
                    self.decisions_log.append({
                        "date": date_str,
                        "type": "full",
                        "trigger": f"new_report={self._last_report_pub_date}",
                        "signal": result.signal,
                        "rules_count": len(result.trading_rules),
                        "rules": [r.name for r in result.trading_rules],
                    })
                    logger.info(f"[L1-FULL] New decision: {result.signal}, "
                                f"{len(result.trading_rules)} rules")
                except Exception as e:
                    logger.error(f"[L1-FULL] Failed: {e}")
```

**要点**:
- `_new_quarterly_report_available` 已被调用并会更新 `self._last_report_pub_date`
- 新增 `self._last_fundamentals_report` 保存基本面报告文本
- `current_period` 变量需从 `self._compute_report_period(date_str)` 获取（与 `_last_fa_report_period` 一致）

---

### 改动 2: 从 quick 触发条件中移除 new_report（~第 891 行）

**当前代码**（`backtest_hybrid.py:886-904`）:
```python
        # Alert 触发次日强制
        if self.force_decision_next_day:
            return True

        # 新季报发布 → 立即重评
        if self._new_quarterly_report_available(date_str):
            return True

        # 价格变动超过阈值
        if self.last_decision_price > 0:
            price_change = abs(close - self.last_decision_price) / self.last_decision_price
            if price_change >= self.price_change_threshold:
                return True

        # 决策过期
        if idx - self.last_decision_idx >= self.stale_days:
            return True

        return False
```

**改为**:
```python
        # Alert 触发次日强制
        if self.force_decision_next_day:
            return True

        # 价格变动超过阈值
        if self.last_decision_price > 0:
            price_change = abs(close - self.last_decision_price) / self.last_decision_price
            if price_change >= self.price_change_threshold:
                return True

        # 决策过期
        if idx - self.last_decision_idx >= self.stale_days:
            return True

        return False
```

**要点**: 仅删除 `_new_quarterly_report_available` 三段（含注释）。

---

### 改动 3: 同步删除 `_trigger_reason` 中的 new_report 分支（~第 944 行）

**当前代码**:
```python
    def _trigger_reason(self, idx: int, close: float) -> str:
        if idx == 0:
            return "first_day"
        if self.force_decision_next_day:
            return "alert_triggered"
        # 新季报优先于价格变动判断
        if self._last_report_pub_date and idx - self.last_decision_idx < self.stale_days:
            return f"new_report={self._last_report_pub_date}"
        if self.last_decision_price > 0:
            ...
```

**改为**:
```python
    def _trigger_reason(self, idx: int, close: float) -> str:
        if idx == 0:
            return "first_day"
        if self.force_decision_next_day:
            return "alert_triggered"
        if self.last_decision_price > 0:
            ...
```

**要点**: 删除 new_report 分支（因为新季报现在走 full，不进这个函数）。

---

### 改动 4: L1Analyzer 加缓存字段 + quick 注入基本面报告（~第 284 行 + ~第 318 行）

**A. `L1Analyzer.__init__`** 加字段:
```python
    def __init__(self, config: Dict[str, Any], output_dir: Path):
        self.config = config
        self.output_dir = output_dir
        self._cache: Dict[str, L1AnalysisResult] = {}
        self._last_fundamentals_report: str = ""   # ★ 新增：quick 分析时复用
```

**B. `L1Analyzer.run_quick_analysis`** 改为跑 `["market"]` 但注入缓存的 fundamentals_report:
```python
    def run_quick_analysis(self, symbol: str, date_str: str) -> L1AnalysisResult:
        """运行快速分析（缓存的fundamentals_report + fresh market + 辩论 + PM）。

        与 run_full_analysis 的区别：
        - 不重新跑 fundamentals analyst（用缓存的基本面报告文本）
        - market analyst 始终重跑（OHLCV 每日变化）
        - 下游辩论和PM仍然完整执行
        """
        logger.info(f"[L1-QUICK] {symbol} @ {date_str} | analysts=[market] (fundamentals from cache)")

        result = self._run_analysis(symbol, date_str, ["market"], deep_model=False)

        # ★ 注入缓存的基本面报告（full 分析时保存的）
        if self._last_fundamentals_report:
            result.fundamentals_report = self._last_fundamentals_report
            logger.info(f"[L1-QUICK] Injected cached fundamentals_report ({len(self._last_fundamentals_report)} chars)")

        return result
```

**注意**: 这里有一个重要前提——下游 graph 中的 Bull/Bear/PM 在调用时，`state["fundamentals_report"]` 已经是空字符串（因为没跑 fundamentals analyst）。仅靠 L1AnalysisResult 层面的字段覆盖不够——需要确认 graph propagate 前能把缓存的报告注入初始 state。如果 `graph.propagate()` 不支持，另一种方案是在 `_run_analysis` 中将报告文本注入到 `selected_analysts` 对应的上游。

---

### 改动 5（可选）: 清理 `last_quarter_period` 和 `_is_quarter_start`

`last_quarter_period` 在改动1中仍然被更新（通过 `_compute_report_period`），用于避免同报告期重复触发 full。`_is_quarter_start` 函数可以保留不删（其他地方可能引用），或不保留均可。建议保留以最小化改动面。

---

## 验证要点

1. 新季报发布的第一个交易日 → full_analysis 触发（`[L1-FULL] New report` 日志）
2. 同一报告期内不再重复触发 full
3. 15 天 stale / 价格波动 / rating_reeval → quick_analysis 触发，日志显示 `Injected cached fundamentals_report`
4. 季度切换（如 7 月 1 日）若无新季报，不触发任何 L1 分析
