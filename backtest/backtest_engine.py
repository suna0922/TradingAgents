"""回测系统主引擎 — 编排 L0 → L1 → L2 三层架构。

职责：
1. 加载数据（DataLayer）并计算技术指标
2. 遍历每个交易日，按三层架构执行：
   - L0: 检测季度边界，需要时调用 DecisionEngine.run_fundamentals_analysis()
   - L1: 检查混合触发条件（价格波动≥±10% OR 超过N天未更新），
        需要时调用 DecisionEngine.run_decision_chain()
   - L2: 每日调用 ExecutionEngine.execute() 执行纯规则交易
3. 维护 PortfolioState，记录完整交易历史
4. 计算基准对比（买入持有策略）
5. 输出汇总统计

这是整个回测系统的"指挥官"，协调数据层、决策引擎、执行引擎协同工作。
"""

import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Tuple

import pandas as pd

from backtest.models import (
    BacktestConfig, PortfolioState, DailyState, TradeRecord,
    WeeklyDecision, MarketSnapshot,
)
from backtest.data_layer import DataLayer
from backtest.cache_manager import CacheManager
from backtest.decision_engine import DecisionEngine
from backtest.execution_engine import ExecutionEngine

logger = logging.getLogger(__name__)


class BacktestEngine:
    """A 股回测主引擎。

    Usage::

        config = BacktestConfig(symbol="000960", start_date="2024-01-02", end_date="2026-05-20")
        engine = BacktestEngine(config)
        results = engine.run()
        print(results["summary"])
    """

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.cache = CacheManager(config.output_dir)
        self.data_layer = DataLayer(
            symbol=config.symbol,
            start_date=config.start_date,
            end_date=config.end_date,
        )
        self.decision_engine = DecisionEngine(config, self.cache)
        self.execution_engine = ExecutionEngine(config, self.data_layer)

        # 运行时状态
        self.portfolio = PortfolioState(cash=config.initial_cash)
        self.benchmark_portfolio = PortfolioState(cash=config.initial_cash)  # 买入持有基准

        # 数据
        self.df: pd.DataFrame = None  # OHLCV + 技术指标

    # ── 主入口 ─────────────────────────────────────────────────────

    def run(self) -> Dict:
        """运行完整回测。

        Returns:
            包含以下键的字典：
            - "summary": 汇总统计 dict
            - "daily_states": 每日组合快照列表
            - "trade_history": 交易记录列表
            - "benchmark_daily": 基准每日快照列表
            - "config": 回测配置（脱敏后）
            - "decisions": 所有决策点记录
        """
        start_time = datetime.now()
        logger.info("=" * 60)
        logger.info(f"[Backtest] START {self.config.symbol} "
                     f"{self.config.start_date} ~ {self.config.end_date}")
        logger.info(f"[Backtest] Initial cash: ¥{self.config.initial_cash:,.0f}")
        logger.info("=" * 60)

        # Phase 1: 加载数据
        self._load_data()

        if self.df is None or len(self.df) == 0:
            logger.error("[Backtest] No data loaded, aborting")
            return {"error": "No data available for the given date range"}

        # Phase 2: 运行回测循环
        decisions_log = []  # 记录所有决策点
        try:
            self._run_loop(decisions_log)
        except Exception as e:
            logger.critical(f"[Backtest] _run_loop CRASHED: {e}", exc_info=True)
            logger.critical(f"[Backtest] Partial decisions saved: {len(decisions_log)} entries")

        # Phase 3: 运行买入持有基准
        try:
            benchmark_states = self._run_benchmark()
        except Exception as e:
            logger.critical(f"[Backtest] Benchmark crashed: {e}", exc_info=True)
            benchmark_states = []

        # Phase 4: 汇总统计
        try:
            summary = self._compute_summary(benchmark_states)
        except Exception as e:
            logger.critical(f"[Backtest] Summary computation crashed: {e}", exc_info=True)
            summary = {
                "symbol": self.config.symbol,
                "error": f"Summary crashed: {e}",
                "partial_decisions": len(decisions_log),
            }

        elapsed = (datetime.now() - start_time).total_seconds()
        summary["elapsed_seconds"] = round(elapsed, 1)
        logger.info(f"[Backtest] DONE in {elapsed:.1f}s | "
                     f"Trades={len(self.portfolio.trade_history)}")

        # 保存结果
        result = {
            "summary": summary,
            "daily_states": [self._daily_state_to_dict(s) for s in self.portfolio.state_history],
            "trade_history": [self._trade_to_dict(t) for t in self.portfolio.trade_history],
            "benchmark_daily": [self._daily_state_to_dict(s) for s in benchmark_states],
            "config": self._config_to_dict(),
            "decisions": decisions_log,
        }

        # 持久化到磁盘
        output_file = f"{self.config.output_dir}/backtest_result_{self.config.symbol}.json"
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"[Backtest] Results saved to {output_file}")
        except IOError as e:
            logger.warning(f"[Backtest] Failed to save results: {e}")

        return result

    # ── Phase 1: 数据加载 ─────────────────────────────────────────

    def _load_data(self) -> None:
        """加载 OHLCV 数据 + 技术指标。"""
        logger.info(f"[Phase1] Loading data for {self.config.symbol}...")
        self.df = self.data_layer.fetch_ohlcv()

        if self.df is not None and len(self.df) > 0:
            self.df = self.data_layer.compute_indicators()
            # 重置索引使 date 成为列（供后续 iterrows 使用）
            self.df = self.df.reset_index()
            logger.info(f"[Phase1] Loaded {len(self.df)} bars | "
                         f"range: {self.df.iloc[0]['date']} ~ {self.df.iloc[-1]['date']}")
            # 缓存 OHLCV
            self.cache.save_ohlcv(self.config.symbol, self.df)
        else:
            logger.warning("[Phase1] No data returned from data source")

    # ── Phase 2: 主回测循环 ────────────────────────────────────────

    def _run_loop(self, decisions_log: List) -> None:
        """遍历每个交易日执行 L0/L1/L2 逻辑。"""
        portfolio = self.portfolio
        active_decision: Optional[WeeklyDecision] = None
        last_decision_price = 0.0
        last_decision_idx = -self.config.decision_stale_days - 10  # 确保第一天会触发
        force_decision_next_day = False  # ALERT_ONLY 触发后强制下一天复评

        total_days = len(self.df)

        for idx in range(total_days):
            row = self.df.iloc[idx]
            date_str = str(row["date"])
            close = float(row["close"])

            # 2-G 修复：每日循环第一行设置 current_date，确保 trailing stop 可用
            portfolio.current_date = date_str

            # ── L0: 季度基本面检测 ───────────────────────────────
            if self.config.fa_quarterly:
                if self.cache.is_quarter_start(self.config.symbol, date_str):
                    logger.info(f"[L0] Quarter boundary detected @ {date_str}")
                    try:
                        fa_result = self.decision_engine.run_fundamentals_analysis(
                            self.config.symbol, date_str
                        )
                        # FA 结果可以影响 fundamental_guards，这里暂存到 log
                        if fa_result:
                            decisions_log.append({
                                "type": "FA",
                                "date": date_str,
                                "period": fa_result.get("report_period"),
                                "signal": fa_result.get("signal"),
                                # 完整 FA 报告（审计用）
                                "fundamentals_report": fa_result.get("fundamentals_report", "")[:5000],
                                "final_trade_decision": fa_result.get("final_trade_decision", "")[:3000],
                            })
                    except Exception as e:
                        logger.critical(f"[L0] FA crashed @ {date_str}: {e}", exc_info=True)
                        decisions_log.append({
                            "type": "FA_CRASH",
                            "date": date_str,
                            "error": str(e),
                        })

            # ── L1: 决策触发检测 ─────────────────────────────────
            should_run_decision = False
            trigger_reason = ""

            # 条件A: 价格波动超过阈值
            if last_decision_price > 0:
                price_change = abs(close - last_decision_price) / last_decision_price
                if price_change >= self.config.price_change_threshold:
                    should_run_decision = True
                    trigger_reason = f"price_change={price_change:.1%}"

            # 条件B: 超过 N 天未更新
            days_since_last = idx - last_decision_idx
            if days_since_last >= self.config.decision_stale_days:
                should_run_decision = True
                trigger_reason = trigger_reason or f"stale={days_since_last}d"

            # 条件C: 第一天强制跑一次
            if idx == 0:
                should_run_decision = True
                trigger_reason = "first_day"

            # 条件D: 前一天触发了 ALERT_ONLY 规则 → 强制复评
            if force_decision_next_day:
                should_run_decision = True
                trigger_reason = trigger_reason or "alert_triggered"
                force_decision_next_day = False

            if should_run_decision:
                logger.info(f"[L1] Trigger: {trigger_reason} @ {date_str}")
                try:
                    new_decision = self.decision_engine.run_decision_chain(
                        symbol=self.config.symbol,
                        date_str=date_str,
                        last_decision_price=last_decision_price,
                        current_price=close,
                        days_since_last_decision=days_since_last,
                    )
                    active_decision = new_decision
                    portfolio.active_decision = new_decision
                    # 重置决策执行标记，确保新决策会被执行
                    portfolio.last_decision_executed_date = ""
                    last_decision_price = close
                    last_decision_idx = idx

                    # 提取量化字段（L2 执行引擎实际消费的字段）
                    pc = new_decision.price_cond
                    buy_low, buy_high = pc.buy_range if pc.buy_range else (0.0, 0.0)

                    # ★ 序列化 trading_rules（SQL 形式规则）
                    trading_rules_serial = []
                    for rule in new_decision.trading_rules:
                        try:
                            trading_rules_serial.append(rule.to_dict())
                        except Exception as e:
                            logger.warning(f"[Backtest] Failed to serialize trading_rule: {e}")

                    decisions_log.append({
                        # ── 量化决策字段（主视图） ──
                        "type": "Decision",
                        "date": date_str,
                        "direction": new_decision.direction.value,
                        "position_pct": round(new_decision.position_pct, 4),
                        "signal_raw": new_decision.signal_raw,
                        "pm_rating": new_decision.pm_rating,
                        "parsed_ok": new_decision.parsed_ok,
                        "trigger": trigger_reason,
                        # 价格约束（执行层直接读取）
                        "stop_loss": round(pc.stop_loss, 2),
                        "take_profit": round(pc.take_profit, 2),
                        "buy_range_low": round(buy_low, 2),
                        "buy_range_high": round(buy_high, 2),
                        "trailing_stop_pct": round(pc.trailing_stop_pct, 4),
                        # ★ 复合交易规则（SQL 形式，供执行引擎使用）
                        "trading_rules": trading_rules_serial,
                        "rules_parsed_ok": new_decision.rules_parsed_ok,
                        # ── 详细文本（审计/调试用，不作为主展示） ──
                        "_detail": {
                            "pm_raw_output": (new_decision.pm_raw_output or "")[:3000],
                            "reasoning_chain": new_decision.reasoning_chain,
                        },
                    })
                except Exception as e:
                    logger.critical(f"[L1] Decision chain crashed @ {date_str}: {e}", exc_info=True)
                    decisions_log.append({
                        "type": "Decision_CRASH",
                        "date": date_str,
                        "trigger": trigger_reason,
                        "error": str(e),
                    })

            # ── L2: 每日规则执行 ─────────────────────────────────
            # ★ 注入当前季度 FA 指标（年报+季报全量标量字段）
            try:
                fa_metrics = self.decision_engine.get_fa_metrics(
                    self.config.symbol, date_str
                )
                daily_state = self.execution_engine.execute(
                    portfolio=portfolio,
                    decision=active_decision,
                    row=row,
                    idx=idx,
                    df=self.df,
                    fa_metrics=fa_metrics,
                )
            except Exception as e:
                logger.error(f"[L2] Execution crashed @ {date_str}: {e}", exc_info=True)
                # 创建最小 daily_state 以维持连续性
                from backtest.models import DailyState
                pos_value = portfolio.shares * close
                total = portfolio.cash + pos_value
                daily_state = DailyState(
                    date=date_str,
                    close=close,
                    cash=portfolio.cash,
                    shares=portfolio.shares,
                    position_value=pos_value,
                    total_value=total,
                    position_pct=pos_value / total if total > 0 else 0.0,
                    action="HOLD",
                    action_price=0.0,
                    action_shares=0,
                    triggered_rules=[],
                    alert_triggered=False,
                )

            # ★ 检测 ALERT_ONLY 规则触发 → 下一天强制复评
            if daily_state.alert_triggered:
                force_decision_next_day = True
                logger.info(f"[ALERT] Rule alert triggered @ {date_str} → force re-evaluation tomorrow")

            # 定期保存快照（每30天或最后一天）
            if idx % 30 == 0 or idx == total_days - 1:
                self.cache.save_snapshot(
                    self.config.symbol, date_str,
                    self._portfolio_snapshot(portfolio),
                )
                # 2-G 修复：current_date 已在循环开头设置，此处不再需要

    # ── Phase 3: 买入持有基准 ────────────────────────────────────

    def _run_benchmark(self) -> List[DailyState]:
        """模拟买入持有策略作为基准。

        在回测第一天以收盘价满仓买入，持有至结束。
        """
        benchmark = self.benchmark_portfolio
        states = []

        if self.df is None or len(self.df) == 0:
            return states

        first_close = float(self.df.iloc[0]["close"])
        shares = (benchmark.cash // first_close // 100) * 100  # 按手取整
        if shares <= 0:
            logger.warning("[Benchmark] Cannot buy even 1 lot, skipping")
            return states

        cost = shares * first_close
        commission = max(cost * self.config.commission_rate, self.config.min_commission)
        benchmark.cash -= (cost + commission)
        benchmark.shares = shares

        logger.info(f"[Benchmark] Bought {shares} shares @ {first_close:.2f} "
                     f"(cost={cost + commission:.2f})")

        for idx in range(len(self.df)):
            row = self.df.iloc[idx]
            date_str = str(row["date"])
            close = float(row["close"])

            pos_value = benchmark.shares * close
            total = benchmark.cash + pos_value
            pos_pct = pos_value / total if total > 0 else 0.0

            states.append(DailyState(
                date=date_str,
                close=close,
                cash=benchmark.cash,
                shares=benchmark.shares,
                position_value=pos_value,
                total_value=total,
                position_pct=pos_pct,
                action="BUY" if idx == 0 else "HOLD",
                action_price=first_close if idx == 0 else 0.0,
                action_shares=shares if idx == 0 else 0,
            ))

        benchmark.current_date = str(self.df.iloc[-1]["date"])
        return states

    # ── Phase 4: 统计计算 ─────────────────────────────────────────

    def _compute_summary(self, benchmark_states: List[DailyState]) -> Dict:
        """计算回测关键指标。

        包括：总收益/年化收益/最大回撤/夏普比率/胜率/交易次数等。
        """
        my_states = self.portfolio.state_history
        bm_states = benchmark_states

        # ── 策略指标 ─────────────────────────────────────────────
        if len(my_states) < 2:
            return {"error": "Insufficient data points"}

        initial_value = self.config.initial_cash
        final_value = my_states[-1].total_value
        total_return = (final_value - initial_value) / initial_value

        # 年化收益
        days_count = len(my_states)
        annual_return = (1 + total_return) ** (252 / days_count) - 1 if days_count > 1 else 0

        # 最大回撤
        max_drawdown, dd_peak, dd_trough = self._calc_max_drawdown(my_states)

        # 夏普比率（假设无风险利率 3%）
        sharpe = self._calc_sharpe_ratio(my_states, risk_free_rate=0.03)

        # 交易胜率
        win_rate, avg_win, avg_loss, profit_factor = self._calc_trade_stats()

        # ── 基准指标 ─────────────────────────────────────────────
        bm_final = bm_states[-1].total_value if bm_states else initial_value
        bm_total_return = (bm_final - initial_value) / initial_value
        bm_annual_return = (1 + bm_total_return) ** (252 / len(bm_states)) - 1 if len(bm_states) > 1 else 0
        bm_max_drawdown, _, _ = self._calc_max_drawdown(bm_states)

        return {
            "symbol": self.config.symbol,
            "period": f"{self.config.start_date} ~ {self.config.end_date}",
            "trading_days": days_count,

            # 策略表现
            "initial_capital": initial_value,
            "final_value": round(final_value, 2),
            "total_return": round(total_return, 4),
            "annual_return": round(annual_return, 4),
            "max_drawdown": round(max_drawdown, 4),
            "sharpe_ratio": round(sharpe, 4),

            # 交易统计
            "total_trades": len([t for t in self.portfolio.trade_history if t.direction == "SELL"]),
            "win_rate": round(win_rate, 4),
            "avg_win_pct": round(avg_win, 4),
            "avg_loss_pct": round(avg_loss, 4),
            "profit_factor": round(profit_factor, 2),

            # 基准对比
            "benchmark_final_value": round(bm_final, 2),
            "benchmark_total_return": round(bm_total_return, 4),
            "benchmark_annual_return": round(bm_annual_return, 4),
            "benchmark_max_drawdown": round(bm_max_drawdown, 4),
            "alpha": round(total_return - bm_total_return, 4),  # 超额收益
        }

    # ── 统计辅助方法 ─────────────────────────────────────────────

    @staticmethod
    def _calc_max_drawdown(states: List[DailyState]) -> Tuple[float, float, float]:
        """计算最大回撤及发生时间。

        Returns:
            (max_dd, peak_value, trough_value)
        """
        peak = states[0].total_value if states else 0
        max_dd = 0.0
        peak_val = peak
        trough_val = peak

        for s in states:
            if s.total_value > peak:
                peak = s.total_value
            dd = (peak - s.total_value) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                peak_val = peak
                trough_val = s.total_value

        return max_dd, peak_val, trough_val

    @staticmethod
    def _calc_sharpe_ratio(
        states: List[DailyState], risk_free_rate: float = 0.03
    ) -> float:
        """计算年化夏普比率。"""
        if len(states) < 2:
            return 0.0

        # 日收益率序列
        daily_returns = []
        for i in range(1, len(states)):
            prev = states[i - 1].total_value
            curr = states[i].total_value
            if prev > 0:
                daily_returns.append((curr - prev) / prev)

        if not daily_returns:
            return 0.0

        import statistics
        mean_r = statistics.mean(daily_returns)
        std_r = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 0.0001

        # 年化
        annual_mean = mean_r * 252
        annual_std = std_r * (252 ** 0.5)

        if annual_std == 0:
            return 0.0

        return (annual_mean - risk_free_rate) / annual_std

    def _calc_trade_stats(self) -> Tuple[float, float, float, float]:
        """计算交易统计数据：胜率、平均盈亏、盈亏比。"""
        sells = [t for t in self.portfolio.trade_history if t.direction == "SELL"]

        if not sells:
            return 0.0, 0.0, 0.0, 0.0

        wins = [t for t in sells if t.pnl > 0]
        losses = [t for t in sells if t.pnl <= 0]

        win_rate = len(wins) / len(sells) if sells else 0
        avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0.0

        # 盈亏比 = 平均盈利 / 平均亏损绝对值
        total_profit = sum(t.pnl for t in wins)
        total_loss = abs(sum(t.pnl for t in losses))
        profit_factor = total_profit / total_loss if total_loss > 0 else float("inf")

        return win_rate, avg_win, avg_loss, profit_factor

    # ── 序列化辅助方法 ─────────────────────────────────────────────

    @staticmethod
    def _daily_state_to_dict(s: DailyState) -> Dict:
        return {
            "date": s.date,
            "close": s.close,
            "cash": s.cash,
            "shares": s.shares,
            "position_value": s.position_value,
            "total_value": s.total_value,
            "position_pct": s.position_pct,
            "action": s.action,
            "action_price": s.action_price,
            "action_shares": s.action_shares,
        }

    @staticmethod
    def _trade_to_dict(t: TradeRecord) -> Dict:
        return {
            "entry_date": t.entry_date,
            "exit_date": t.exit_date,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "shares": t.shares,
            "direction": t.direction,
            "pnl": t.pnl,
            "pnl_pct": t.pnl_pct,
            "exit_reason": t.exit_reason,
        }

    def _config_to_dict(self) -> Dict:
        """返回脱敏的配置（不含 API key 等）。"""
        return {
            "symbol": self.config.symbol,
            "start_date": self.config.start_date,
            "end_date": self.config.end_date,
            "initial_cash": self.config.initial_cash,
            "llm_provider": self.config.llm_provider,
            "deep_think_llm": self.config.deep_think_llm,
            "quick_think_llm": self.config.quick_think_llm,
            "price_change_threshold": self.config.price_change_threshold,
            "decision_stale_days": self.config.decision_stale_days,
        }

    @staticmethod
    def _portfolio_snapshot(p: PortfolioState) -> Dict:
        """组合状态快照用于断点续跑。"""
        return {
            "cash": p.cash,
            "shares": p.shares,
            "current_date": p.current_date,
            "trade_count": len(p.trade_history),
        }
