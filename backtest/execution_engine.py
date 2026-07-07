"""回测系统执行引擎（L2 层 — 纯规则驱动，零 LLM 调用）。

职责：
- 每日检查持仓状态，基于量价数据 + 技术指标 + 基本面红线执行交易
- 止损/止盈/移动止损的精确计算
- A股交易规则：涨跌停检测、停牌跳过、按手取整(100股)、交易成本
- 产生 TradeRecord 和 DailyState，供 BacktestEngine 汇总和 ReportEngine 可视化

设计原则：
1. 纯确定性逻辑：相同输入 → 相同输出，无随机性
2. 所有判断条件可配置（通过 TechnicalTriggers / FundamentalGuards）
3. 严格 A 股交易规则：T+1、涨跌停、最小交易单位
4. 成本精确计算：佣金(万三)+印花税千一(卖)+过户费万0.2(沪)
"""

import logging
import re
from typing import Optional, List, Tuple, Dict, Any
from datetime import datetime

import pandas as pd

from backtest.models import (
    BacktestConfig, PortfolioState, DailyState, TradeRecord,
    TradeDirection, PriceCondition, TechnicalTriggers,
    FundamentalGuards, WeeklyDecision, RuleAction, TradingRule,
)
from backtest.data_layer import DataLayer

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """L2 执行引擎 — 纯规则驱动的日频交易执行器。

    每个交易日调用一次 ``execute()`` 方法，根据当前持仓、价格数据、
    技术指标和生效中的 WeeklyDecision 判断是否需要交易。

    Usage::

        engine = ExecutionEngine(config, data_layer)
        daily_state = engine.execute(portfolio_state, decision, row_data, indicators)
    """

    def __init__(self, config: BacktestConfig, data_layer: DataLayer):
        self.config = config
        self._dl = data_layer

    def execute(
        self,
        portfolio: PortfolioState,
        decision: Optional[WeeklyDecision],
        row: pd.Series,
        idx: int,
        df: pd.DataFrame,
        fa_metrics: Optional[Dict[str, Any]] = None,
    ) -> DailyState:
        """执行单个交易日的规则判断。

        Args:
            portfolio: 当前组合状态（会被修改：cash/shares 更新）
            decision: 当前生效的 WeeklyDecision（可能为 None）
            row: 当日 OHLCV 数据 (Series)
            idx: 当日在 DataFrame 中的索引位置
            df: 完整 OHLCV+指标 DataFrame
            fa_metrics: 展平后的 L1 基本面指标字典（由 DecisionEngine 提供），
                       如 {"annual_roe": 22.5, "quarter_ocf_to_netprofit": 0.95}
                       注入到 row_dict 供 Condition.evaluate() 解析 FA 条件。

        Returns:
            DailyState: 当日快照（含 action/action_price/action_shares）
        """
        date_str = str(row["date"]) if "date" in row.index else row.name
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        volume = float(row.get("volume", 0))
        pct_chg = float(row.get("pct_chg", 0))
        open_price = float(row["open"])

        # ── 前置检查 ───────────────────────────────────────────

        # 1. 停牌检测
        if self._is_suspended(volume):
            return self._make_daily_state(
                date_str, close, portfolio, "NONE", 0, 0
            )

        # 2. 涨跌停检测（无法买入/卖出）
        at_limit_up = self._is_limit_up(pct_chg)
        at_limit_down = self._is_limit_down(pct_chg)

        # ── 核心决策逻辑 ───────────────────────────────────────

        action = "HOLD"
        action_price = close
        action_shares = 0
        exit_reason = ""

        has_position = portfolio.shares > 0

        triggered_rules: List[str] = []
        alert_triggered = False

        # ★ 优先检查复合交易规则（在简单阈值逻辑之前）
        force_reeval = False  # RATING_REEVAL 触发标记
        if decision is not None and decision.trading_rules:
            rule_action, rule_price, rule_shares, rule_reason, triggered_rules, alert_triggered = (
                self._check_trading_rules(
                    portfolio, decision, close, low, high,
                    open_price, at_limit_down, at_limit_up, row,
                    df, idx,
                    fa_metrics=fa_metrics or {},
                )
            )
            if rule_action != "HOLD":
                # 处合规则触发 → 直接进入执行阶段
                action = rule_action
                action_price = rule_price
                action_shares = rule_shares
                exit_reason = rule_reason
            elif rule_reason.startswith("rating_reeval:"):
                # RATING_REEVAL 规则触发 → 标记强制复评
                force_reeval = True

        # ── 如果复合规则未触发，走原有的简单阈值逻辑 ────────
        if action == "HOLD":
            if has_position:
                # ── 持仓中：优先检查卖出信号 ────────────────────────
                action, action_price, action_shares, exit_reason = (
                    self._check_exit_signals(
                        portfolio, decision, close, low, high,
                        open_price, at_limit_down, row, df, idx
                    )
                )
            else:
                # ── 空仓中：检查买入信号 ────────────────────────────
                if not at_limit_up:
                    action, action_price, action_shares = self._check_entry_signals(
                        portfolio, decision, close, high, low,
                        open_price, row, df, idx
                    )

        # ── 执行交易 ───────────────────────────────────────────

        if action == "BUY" and action_shares > 0:
            portfolio = self._execute_buy(
                portfolio, action_price, action_shares, date_str
            )
        elif action == "SELL" and action_shares > 0:
            portfolio, exit_reason = self._execute_sell(
                portfolio, action_price, action_shares, exit_reason, date_str
            )

        # 记录每日快照
        position_value = portfolio.shares * close
        total_value = portfolio.cash + position_value
        pos_pct = position_value / total_value if total_value > 0 else 0.0

        daily_state = DailyState(
            date=date_str,
            close=close,
            cash=portfolio.cash,
            shares=portfolio.shares,
            position_value=position_value,
            total_value=total_value,
            position_pct=pos_pct,
            action=action,
            action_price=action_price if action != "HOLD" else 0.0,
            action_shares=action_shares,
            triggered_rules=triggered_rules,
            alert_triggered=alert_triggered or force_reeval,
        )

        portfolio.state_history.append(daily_state)
        return daily_state

    # ── 复合交易规则检查（★ 新增）──────────────────────────────

    def _check_trading_rules(
        self,
        portfolio: PortfolioState,
        decision: WeeklyDecision,
        close: float,
        low: float,
        high: float,
        open_price: float,
        at_limit_down: bool,
        at_limit_up: bool,
        row: pd.Series,
        df: pd.DataFrame,
        idx: int,
        fa_metrics: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, float, int, str, List[str], bool]:
        """遍历所有复合规则，按优先级逐条 evaluate。

        Args:
            fa_metrics: 展平后的 L1 基本面指标（由 DecisionEngine 提供），
                       注入到 row_dict 中供基本面条件评估。
            df: 完整 OHLCV DataFrame（供 MA/RSI/BOLL 等函数计算）
            idx: 当前日在 df 中的索引

        Returns:
            (action, price, shares, reason, triggered_rules, alert_triggered)
            triggered_rules: 当天所有触发的规则名列表
            alert_triggered: 是否有 ALERT_ONLY 规则被触发
        """
        # 构建 row dict（包含量价+技术指标）
        row_dict = row.to_dict() if hasattr(row, "to_dict") else dict(row)

        # 注入额外字段（row 中没有但规则可能引用的）
        row_dict["_close"] = close
        row_dict["_high"] = high
        row_dict["_low"] = low

        # ★ 注入历史数据引用（供 MA/RSI/BOLL 等函数计算）
        row_dict["_df"] = df
        row_dict["_idx"] = idx

        # ★ 注入 L1 基本面指标（全部 annual_* + quarter_* 字段）
        if fa_metrics:
            row_dict.update(fa_metrics)

        triggered_rules: List[str] = []
        alert_triggered = False

        for rule in decision.trading_rules:
            if not rule.enabled:
                continue

            if rule.evaluate_all(row_dict):
                triggered_rules.append(rule.name)
                if rule.action == RuleAction.ALERT_ONLY:
                    alert_triggered = True
                    logger.info(
                        f"[RULE] ALERT triggered: {rule.name} "
                        f"@ {close} (priority={rule.priority})"
                    )
                    continue  # ALERT 规则不立即执行，继续检查更高优先级规则
                elif rule.action == RuleAction.RATING_REEVAL:
                    alert_triggered = True  # 复用 alert_triggered 机制强制复评
                    logger.info(
                        f"[RULE] RATING_REEVAL triggered: {rule.name} "
                        f"@ {close} (priority={rule.priority})"
                    )
                    # 返回 HOLD 但标记需要复评
                    return ("HOLD", 0.0, 0, f"rating_reeval:{rule.name}", triggered_rules, alert_triggered)
                # ★ 空仓跳过卖出类规则（避免 SELL 0 股日志噪音）
                if portfolio.shares == 0 and rule.action in (
                    RuleAction.SELL_PCT, RuleAction.SELL_ALL,
                    RuleAction.STOP_LOSS, RuleAction.TAKE_PROFIT,
                ):
                    continue
                logger.info(
                    f"[RULE] Triggered: {rule.description} "
                    f"@ {close} (priority={rule.priority})"
                )
                action_result = self._execute_rule_action(
                    rule, portfolio, close, low, high,
                    open_price, at_limit_down, at_limit_up, row_dict,
                )
                return (*action_result, triggered_rules, alert_triggered)

        return ("HOLD", 0.0, 0, "", triggered_rules, alert_triggered)

    def _execute_rule_action(
        self,
        rule: TradingRule,
        portfolio: PortfolioState,
        close: float,
        low: float,
        high: float,
        open_price: float,
        at_limit_down: bool,
        at_limit_up: bool,
        row_dict: Dict,
    ) -> Tuple[str, float, int, str]:
        """根据触发规则的 action 类型计算具体执行参数。

        Args:
            rule: 已触发的 TradingRule
            portfolio: 当前组合
            close/low/high/open_price: 当日价格
            at_limit_down/at_limit_up: 涨跌停状态
            row_dict: 当日完整数据字典

        Returns:
            (action, price, shares, reason)
        """
        action = rule.action
        price = close
        shares = 0
        reason = f"rule:{rule.name}"

        if action == RuleAction.SELL_ALL:
            # 全部清仓
            shares = portfolio.shares
            return ("SELL", price, shares, reason)

        elif action == RuleAction.SELL_PCT:
            # 按 pct 减仓
            if rule.pct > 0:
                reduce_pct = rule.pct
            else:
                # PM 未指定 pct，从 action_detail 或 source_sentence 中尝试提取
                reduce_pct = self._extract_pct_from_text(rule.source_sentence) or 0.3  # 默认保守减仓30%
                logger.warning(f"[RULE] SELL_PCT pct not specified, using default {reduce_pct:.0%} for rule: {rule.name}")
            shares = self._calc_reduce_shares(portfolio, close, pct=reduce_pct)
            return ("SELL", price, shares, reason)

        elif action == RuleAction.STOP_LOSS:
            # 止损出场（用止损价或 close）
            # 从 condition_str 中提取止损阈值: "close < 48.50"
            sl_value = self._extract_price_from_condition(rule.condition_str, default=close)
            sell_price = max(sl_value, open_price) if at_limit_down else sl_value
            shares = portfolio.shares
            return ("SELL", sell_price, shares, reason)

        elif action == RuleAction.TAKE_PROFIT:
            # 止盈出场
            tp_value = self._extract_price_from_condition(rule.condition_str, default=close)
            sell_price = min(tp_value, open_price) if at_limit_down else tp_value
            shares = portfolio.shares
            return ("SELL", sell_price, shares, reason)

        elif action == RuleAction.BUY_ADD:
            # 加仓（检查不在涨停板）
            if at_limit_up:
                return ("HOLD", 0.0, 0, "")
            shares = self._calc_buy_shares(portfolio, close, rule)
            return ("BUY", price, shares, reason) if shares > 0 else ("HOLD", 0.0, 0, "")

        elif action == RuleAction.NO_LEFT_BUY:
            # 禁止左侧加仓 → 记录日志但不执行动作
            logger.info(f"[RULE] No-left-buy guard active (rule={rule.name})")
            return ("HOLD", 0.0, 0, reason)

        elif action == RuleAction.CIRCUIT_BREAK:
            # 基本面熔断清仓（最高优先级！无条件执行）
            shares = portfolio.shares
            logger.warning(f"[RULE] ⚠ CIRCUIT BREAK triggered: {rule.name} — force clear position!")
            return ("SELL", price, shares, "circuit_break:" + rule.name)

        elif action == RuleAction.ALERT_ONLY:
            # 观察锚点触发 → 不执行交易，但标记需要复评
            logger.info(f"[RULE] ALERT_ONLY triggered: {rule.name} — will force re-evaluation")
            return ("HOLD", 0.0, 0, f"alert:{rule.name}")

        elif action == RuleAction.RATING_REEVAL:
            # 评级重新评估 → 不执行交易，但标记需要强制复评
            logger.info(f"[RULE] RATING_REEVAL triggered: {rule.name} — force PM re-evaluation")
            return ("HOLD", 0.0, 0, f"rating_reeval:{rule.name}")

        else:
            # RuleAction.HOLD 或未知动作
            return ("HOLD", 0.0, 0, reason)

    @staticmethod
    def _extract_pct_from_text(text: str) -> Optional[float]:
        """从规则文本中提取百分比数字。

        匹配模式：
        - "减仓30%" → 0.30
        - "降至30%仓位" → 0.30
        - "加仓20%" → 0.20
        - "建立40%底仓" → 0.40
        - "卖出50%" → 0.50
        - "建仓20%目标仓位" → 0.20
        - "加仓25%目标仓位" → 0.25
        - "加仓剩余55%目标仓位" → 0.55

        Returns:
            提取到的百分比（0.0~1.0），或 None（未找到）
        """
        if not text:
            return None
        # 匹配中文描述中的百分比
        patterns = [
            r'(?:减仓|减持|卖出|降至|建立|加仓|买入|增至|建仓)[\D]*(\d+)%',
            r'(\d+)%[\D]*(?:仓位|底仓|持仓|比例|目标仓位)',
            r'(?:剩余|加仓|建仓)[\D]*(\d+)%',
        ]
        for pat in patterns:
            match = re.search(pat, text)
            if match:
                return float(match.group(1)) / 100.0
        return None

    @staticmethod
    def _extract_price_from_condition(condition_str: str, default: float = 0.0) -> float:
        """从 condition_str 中提取价格阈值。

        匹配模式:
        - "close < 48.50" → 48.50
        - "close > 52.0 AND volume > ..." → 52.0
        - 含 MA/RSI 等函数的条件不包含数字价格, 返回 default
        """
        import re
        # 匹配纯数字价格阈值（不是函数参数）
        # 优先匹配 "close < NUMBER" 或 "close > NUMBER"
        m = re.search(r"close\s*[<>]\s*(\d+\.?\d*)", condition_str)
        if m:
            return float(m.group(1))
        return default

    @staticmethod
    def _calc_reduce_shares(portfolio: PortfolioState, close: float, pct: float = 0.5) -> int:
        """计算减仓股数。

        Args:
            portfolio: 当前组合
            close: 当前价格
            pct: 减仓比例（0.5 = 减半）

        Returns:
            按手取整后的卖出股数
        """
        target_shares = int(portfolio.shares * (1 - pct))
        sell_shares = portfolio.shares - target_shares
        if sell_shares <= 0:
            return 0
        return max(100, (sell_shares // 100) * 100)  # 最少卖1手

    def _calc_buy_shares(
        self, portfolio: PortfolioState, close: float, rule: TradingRule,
    ) -> int:
        """计算加仓股数。

        加仓策略：
        1. 用可用资金的 30%~50%（保守）
        2. 不超过总资产的 80%
        3. 按手取整

        Args:
            portfolio: 当前组合
            close: 当前价格
            rule: 触发的规则（可能含 pct 参数）

        Returns:
            可买入的股数（按手取整，100 的整数倍）
        """
        if close <= 0:
            return 0

        if rule.pct > 0:
            buy_pct = rule.pct
        else:
            # PM 未指定 pct，从 action_detail 或 source_sentence 中尝试提取
            buy_pct = self._extract_pct_from_text(rule.source_sentence) or 0.2  # 默认保守加仓20%
            logger.warning(f"[RULE] BUY_ADD pct not specified, using default {buy_pct:.0%} for rule: {rule.name}")
        available = portfolio.cash * buy_pct

        cost_rate = (self.config.commission_rate + self.config.slippage_pct)
        effective_funds = available / (1 + cost_rate)

        shares = self._round_lot(int(effective_funds / close))

        # 安全限制：单一加仓不超过当前持仓的 50%（已有持仓时），或不超过总资产的 80%（空仓时）
        if portfolio.shares > 0:
            max_add = self._round_lot(int(portfolio.shares * 0.5))
            shares = min(shares, max_add)
        else:
            # 空仓时首次建仓：不超过总资产的 80%
            max_value = portfolio.cash * 0.8
            max_shares = self._round_lot(int(max_value / close))
            shares = min(shares, max_shares)

        return shares

    # ── 卖出信号检查 ─────────────────────────────────────────────

    def _check_exit_signals(
        self,
        portfolio: PortfolioState,
        decision: Optional[WeeklyDecision],
        close: float,
        low: float,
        high: float,
        open_price: float,
        at_limit_down: bool,
        row: pd.Series,
        df: pd.DataFrame,
        idx: int,
    ) -> Tuple[str, float, int, str]:
        """检查所有卖出触发条件。

        优先级（从高到低）：
        1. 固定止损 (stop_loss)
        2. 固定止盈 (take_profit)
        3. 移动止损 (trailing_stop)
        4. 决策方向变为 SELL/Underweight
        5. 基本面红线触碰

        Returns:
            (action, price, shares, reason)
        """
        entry_price = self._get_avg_entry_price(portfolio)
        shares = portfolio.shares
        cond = decision.price_cond if decision else PriceCondition()
        guards = decision.fundamental_guards if decision else FundamentalGuards()

        # 1. 固定止损
        if cond.stop_loss > 0 and low <= cond.stop_loss:
            # 当日最低价触及止损线 → 以止损价或开盘价卖出
            sell_price = max(cond.stop_loss, open_price) if at_limit_down else cond.stop_loss
            logger.info(f"[EXIT] Stop-loss triggered @ {sell_price} "
                        f"(limit={cond.stop_loss})")
            return ("SELL", sell_price, shares, "stop_loss")

        # 2. 固定止盈
        if cond.take_profit > 0 and high >= cond.take_profit:
            sell_price = min(cond.take_profit, open_price) if at_limit_down else cond.take_profit
            logger.info(f"[EXIT] Take-profit triggered @ {sell_price} "
                        f"(target={cond.take_profit})")
            return ("SELL", sell_price, shares, "take_profit")

        # 3. 移动止损（最少持仓3个交易日才启动）
        if cond.trailing_stop_pct > 0 and entry_price > 0:
            entry_date = self._find_entry_date(portfolio)
            days_held = 0
            if entry_date:
                try:
                    days_held = (pd.Timestamp(portfolio.current_date) - pd.Timestamp(entry_date)).days
                except Exception:
                    pass
            if days_held < 3:
                pass  # skip: held too short
            else:
                trailing_trigger = self._check_trailing_stop(
                    portfolio, close, high, entry_price, cond.trailing_stop_pct, df, idx
                )
                if trailing_trigger is not None:
                    logger.info(f"[EXIT] Trailing-stop triggered @ {trailing_trigger}")
                    return ("SELL", trailing_trigger, shares, "trailing_stop")

        # 4. 决策方向变化（只在决策变化当天执行一次）
        if decision is not None:
            direction = decision.direction
            target_pct = decision.position_pct

            # 检查是否是新决策（决策日期 > 最后执行日期）
            is_new_decision = False
            if hasattr(portfolio, 'last_decision_executed_date') and portfolio.last_decision_executed_date:
                try:
                    from datetime import datetime
                    # 处理带时间部分的日期字符串
                    decision_date_str = decision.decision_date.split()[0] if ' ' in decision.decision_date else decision.decision_date
                    executed_date_str = portfolio.last_decision_executed_date.split()[0] if ' ' in portfolio.last_decision_executed_date else portfolio.last_decision_executed_date
                    decision_dt = datetime.strptime(decision_date_str, "%Y-%m-%d")
                    executed_dt = datetime.strptime(executed_date_str, "%Y-%m-%d")
                    is_new_decision = decision_dt > executed_dt
                except (ValueError, TypeError) as e:
                    logger.warning(f"[DEBUG] Date parse error: {e}, decision_date={decision.decision_date}, executed={portfolio.last_decision_executed_date}")
                    is_new_decision = True
            else:
                is_new_decision = True

            logger.info(f"[DEBUG] Decision check: date={decision.decision_date}, "
                        f"last_executed={portfolio.last_decision_executed_date}, "
                        f"is_new={is_new_decision}, direction={direction}, target_pct={target_pct}")

            if is_new_decision and direction == TradeDirection.SELL and target_pct >= 0:
                # SELL + target_pct = 减仓信号（只在决策变化当天执行）
                if target_pct == 0.0:
                    # 目标仓位为 0 → 清仓
                    portfolio.last_decision_executed_date = decision.decision_date
                    return ("SELL", close, shares, "decision_change")
                elif 0 < target_pct < 1.0:
                    # 减仓到目标仓位（如 SELL 30% 表示保留 30% 仓位）
                    current_shares = shares
                    target_shares = int((shares * target_pct))
                    sell_shares = self._round_lot(current_shares - target_shares)
                    if sell_shares > 0 and sell_shares < current_shares:
                        portfolio.last_decision_executed_date = decision.decision_date
                        return ("SELL", close, sell_shares, "decision_change")
                    elif sell_shares >= current_shares:
                        # 计算后需要卖出全部，直接清仓
                        portfolio.last_decision_executed_date = decision.decision_date
                        return ("SELL", close, shares, "decision_change")
            elif is_new_decision and direction == TradeDirection.HOLD and target_pct == 0.0:
                # Hold 但目标仓位为 0 → 清仓（只在决策变化当天执行）
                portfolio.last_decision_executed_date = decision.decision_date
                return ("SELL", close, shares, "decision_change")

        # 5. 基本面红线（如果有 FA 数据的话）
        # 注意：基本面红线在 execution 层通常由外部注入的 guard 值触发，
        # 这里预留接口，实际值由 backtest_engine 在调用前更新到 decision 中

        return ("HOLD", close, 0, "")

    # ── 买入信号检查 ─────────────────────────────────────────────

    def _check_entry_signals(
        self,
        portfolio: PortfolioState,
        decision: Optional[WeeklyDecision],
        close: float,
        high: float,
        low: float,
        open_price: float,
        row: pd.Series,
        df: pd.DataFrame,
        idx: int,
    ) -> Tuple[str, float, int]:
        """检查买入条件。

        条件（全部满足才买入）：
        1. 决策方向为 BUY/Overweight
        2. 价格在买入区间内（如有设定）
        3. 技术指标不处于超买状态
        4. 有足够资金

        Returns:
            (action, price, shares)
        """
        if decision is None:
            return ("HOLD", close, 0)

        direction = decision.direction
        if direction not in (TradeDirection.BUY,):
            return ("HOLD", close, 0)

        # 检查买入区间
        cond = decision.price_cond
        if cond.buy_range is not None:
            buy_low, buy_high = cond.buy_range
            if high < buy_low:
                # 还没跌到买入区间
                return ("HOLD", close, 0)
            elif low > buy_high:
                # 已涨过买入区间
                return ("HOLD", close, 0)

        # 技术指标超买检查
        triggers = decision.technical_triggers
        if self._is_overbought(row, triggers):
            logger.debug("[ENTRY] Skipped: overbought conditions detected")
            return ("HOLD", close, 0)

        # 计算可买股数（默认 10% 轻仓，与 PM 的"轻仓先行"策略对齐）
        target_pct = decision.position_pct
        if target_pct < 0:
            target_pct = 0.10  # 未指定时默认 10% 仓位

        total_value = portfolio.cash  # 空仓时 total_value ≈ cash
        target_investment = total_value * target_pct

        # 考虑交易成本后的可用资金
        cost_rate = (self.config.commission_rate + self.config.slippage_pct)
        available = portfolio.cash / (1 + cost_rate)

        # 以收盘价计算（或用区间上限），优先用区间上限（更保守）
        buy_price = cond.buy_range[1] if cond.buy_range else close
        buy_price = min(buy_price, close)  # 不超过当前价

        shares = self._round_lot(int(available / buy_price)) if buy_price > 0 else 0

        # 不允许超过目标投入
        max_shares_by_target = self._round_lot(
            int(target_investment / buy_price)
        ) if buy_price > 0 else 0
        shares = min(shares, max_shares_by_target)

        if shares > 0:
            logger.info(f"[ENTRY] BUY signal: {shares} shares @ ~{buy_price:.2f}")
            return ("BUY", buy_price, shares)

        return ("HOLD", close, 0)

    # ── 移动止损计算 ─────────────────────────────────────────────

    def _check_trailing_stop(
        self,
        portfolio: PortfolioState,
        close: float,
        high: float,
        entry_price: float,
        trail_pct: float,
        df: pd.DataFrame,
        idx: int,
    ) -> Optional[float]:
        """计算移动止损触发。

        算法：
        1. 从建仓日起追踪最高价
        2. 从最高价回落超过 trail_pct 时触发
        3. 返回触发价格

        Args:
            portfolio: 组合状态（用于获取建仓信息）
            close: 当前收盘价
            high: 当日最高价
            entry_price: 平均建仓价
            trail_pct: 回撤百分比（如 0.08 = 8%）
            df: 完整数据
            idx: 当前索引

        Returns:
            触发时的卖出价格，或 None（未触发）
        """
        if len(portfolio.trade_history) == 0:
            return None

        last_trade = portfolio.trade_history[-1]
        # 找到建仓日在 df 中的位置
        # 简化方案：用最近 N 天的最高价
        lookback = min(idx, 60)  # 最多回看 60 个交易日
        if lookback < 5:
            return None  # 持仓太短，不启动移动止损

        # 计算回顾期内的最高收盘价
        recent_high = df.iloc[idx - lookback : idx + 1]["close"].max()

        # 从最高点的回撤幅度
        drawdown = (recent_high - close) / recent_high if recent_high > 0 else 0

        if drawdown >= trail_pct:
            # 触发移动止损，以当前价卖出
            return close

        return None

    # ── 技术指标辅助判断 ─────────────────────────────────────────

    @staticmethod
    def _is_overbought(row: pd.Series, triggers: TechnicalTriggers) -> bool:
        """检查是否处于超买状态（阻止买入）。"""
        checks = []

        # RSI 超买
        rsi = row.get("rsi")
        if rsi is not None and not pd.isna(rsi):
            checks.append(float(rsi) > triggers.rsi_overbought)

        # KDJ K 值超买 (stockstats 列名: kdjk)
        kdj_k = row.get("kdjk")
        if kdj_k is not None and not pd.isna(kdj_k):
            checks.append(float(kdj_k) > triggers.kdj_k)

        # 布林带上轨突破
        boll_upper = row.get("boll_ub")
        close = row.get("close")
        if (boll_upper is not None and close is not None
                and not pd.isna(boll_upper) and not pd.isna(close)):
            checks.append(float(close) > float(boll_upper))

        # 多数超买指标为 True → 判定为超买
        return sum(checks) >= 2 if checks else False

    @staticmethod
    def _is_oversold(row: pd.Series, triggers: TechnicalTriggers) -> bool:
        """检查是否处于超卖状态（可作为加仓信号）。"""
        checks = []

        rsi = row.get("rsi")
        if rsi is not None and not pd.isna(rsi):
            checks.append(float(rsi) < triggers.rsi_oversold)

        kdj_k = row.get("kdjk")  # stockstats 列名: kdjk
        if kdj_k is not None and not pd.isna(kdj_k):
            checks.append(float(kdj_k) < triggers.kdj_d)

        boll_lower = row.get("boll_lb")
        close = row.get("close")
        if (boll_lower is not None and close is not None
                and not pd.isna(boll_lower) and not pd.isna(close)):
            checks.append(float(close) < float(boll_lower))

        return sum(checks) >= 2 if checks else False

    # ── 交易执行 ─────────────────────────────────────────────────

    def _execute_buy(
        self, portfolio: PortfolioState, price: float, shares: int, date_str: str
    ) -> PortfolioState:
        """执行买入操作，更新组合状态。"""
        if shares <= 0 or price <= 0:
            return portfolio

        # 计算交易成本
        gross_value = shares * price
        slippage_cost = gross_value * self.config.slippage_pct
        commission = max(gross_value * self.config.commission_rate, self.config.min_commission)

        total_cost = gross_value + slippage_cost + commission

        if total_cost > portfolio.cash:
            # 资金不足，调整股数
            affordable_shares = self._adjust_for_cash(portfolio.cash, price)
            if affordable_shares <= 0:
                logger.warning(f"[BUY] Insufficient funds: need={total_cost:.0f}, "
                               f"have={portfolio.cash:.0f}")
                return portfolio
            shares = affordable_shares
            gross_value = shares * price
            slippage_cost = gross_value * self.config.slippage_pct
            commission = max(gross_value * self.config.commission_rate, self.config.min_commission)
            total_cost = gross_value + slippage_cost + commission

        portfolio.cash -= total_cost
        portfolio.shares += shares

        # 记录交易
        trade = TradeRecord(
            entry_date=date_str,
            exit_date="",  # 尚未平仓
            entry_price=price,
            exit_price=0.0,
            shares=shares,
            direction="BUY",
            pnl=0.0,
            pnl_pct=0.0,
            exit_reason="",
        )
        portfolio.trade_history.append(trade)

        logger.info(f"[EXEC] BOUGHT {shares} shares of {self.config.symbol} "
                     f"@ {price:.2f} | cost={total_cost:.2f}, cash={portfolio.cash:.2f}")
        return portfolio

    def _execute_sell(
        self,
        portfolio: PortfolioState,
        price: float,
        shares: int,
        exit_reason: str,
        date_str: str,
    ) -> Tuple[PortfolioState, str]:
        """执行卖出操作，更新组合状态并计算盈亏。"""
        if shares <= 0 or price <= 0 or portfolio.shares <= 0:
            return portfolio, exit_reason

        # 实际卖出数量不能超过持有量
        shares = min(shares, portfolio.shares)

        # 计算收入和成本
        gross_income = shares * price
        slippage_cost = gross_income * self.config.slippage_pct
        commission = max(gross_income * self.config.commission_rate, self.config.min_commission)
        stamp_duty = gross_income * self.config.stamp_duty_rate  # 印花税（仅卖）

        transfer_fee = 0.0
        if self.config.is_sh_market:
            transfer_fee = gross_income * self.config.transfer_fee_rate

        total_cost = slippage_cost + commission + stamp_duty + transfer_fee
        net_income = gross_income - total_cost

        portfolio.cash += net_income

        # 计算盈亏（FIFO: 用最早的买入记录匹配）
        entry_price = self._get_avg_entry_price(portfolio)
        pnl = (price - entry_price) * shares - total_cost
        pnl_pct = (price - entry_price) / entry_price if entry_price > 0 else 0.0

        portfolio.shares -= shares

        # 更新交易记录（找到对应的买入记录并补全）
        trade = TradeRecord(
            entry_date=self._find_entry_date(portfolio),
            exit_date=date_str,
            entry_price=entry_price,
            exit_price=price,
            shares=shares,
            direction="SELL",
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 4),
            exit_reason=exit_reason,
        )
        portfolio.trade_history.append(trade)

        logger.info(f"[EXEC] SOLD {shares} shares of {self.config.symbol} "
                     f"@ {price:.2f} | PnL={pnl:.2f} ({pnl_pct:.2%}), "
                     f"reason={exit_reason}")
        return portfolio, exit_reason

    # ── A 股交易规则辅助 ─────────────────────────────────────────

    @staticmethod
    def _is_suspended(volume: float) -> bool:
        """停牌检测：成交量 = 0 视为停牌。"""
        return volume == 0 or pd.isna(volume)

    @staticmethod
    def _is_limit_up(pct_chg: float) -> bool:
        """涨停检测（≥9.9% 视为涨停）。"""
        if pd.isna(pct_chg):
            return False
        return pct_chg >= 9.9

    @staticmethod
    def _is_limit_down(pct_chg: float) -> bool:
        """跌停检测（≤ -9.9% 视为跌停）。"""
        if pd.isna(pct_chg):
            return False
        return pct_chg <= -9.9

    @staticmethod
    def _round_lot(shares: int) -> int:
        """A 股按手取整（1手 = 100 股）。"""
        return (shares // 100) * 100

    def _adjust_for_cash(self, cash: float, price: float) -> int:
        """根据可用资金调整购买股数。"""
        if price <= 0:
            return 0
        raw_shares = int(cash / price / (1 + self.config.slippage_pct
                                         + self.config.commission_rate))
        return self._round_lot(raw_shares)

    # ── 持仓分析辅助 ─────────────────────────────────────────────

    @staticmethod
    def _get_avg_entry_price(portfolio: PortfolioState) -> float:
        """计算平均建仓价（从交易历史中加权平均）。"""
        total_cost = 0.0
        total_shares = 0
        for trade in portfolio.trade_history:
            if trade.direction == "BUY":
                total_cost += trade.entry_price * trade.shares
                total_shares += trade.shares
            elif trade.direction == "SELL":
                # FIFO 减去对应的成本
                sell_cost = trade.entry_price * trade.shares
                total_cost -= min(sell_cost, total_cost)
                total_shares -= trade.shares

        if total_shares > 0:
            return total_cost / total_shares
        return 0.0

    @staticmethod
    def _find_entry_date(portfolio: PortfolioState) -> str:
        """找到最近一笔买入的日期。"""
        for trade in reversed(portfolio.trade_history):
            if trade.direction == "BUY":
                return trade.entry_date
        return ""

    # ── 快照生成 ─────────────────────────────────────────────────

    @staticmethod
    def _make_daily_state(
        date_str: str, close: float, portfolio: PortfolioState,
        action: str, action_price: float, action_shares: int,
    ) -> DailyState:
        """生成一个简单的每日快照。"""
        position_value = portfolio.shares * close
        total_value = portfolio.cash + position_value
        pos_pct = position_value / total_value if total_value > 0 else 0.0

        return DailyState(
            date=date_str,
            close=close,
            cash=portfolio.cash,
            shares=portfolio.shares,
            position_value=position_value,
            total_value=total_value,
            position_pct=pos_pct,
            action=action,
            action_price=action_price,
            action_shares=action_shares,
        )
