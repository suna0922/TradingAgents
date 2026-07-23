"""回测系统决策引擎（L0 + L1 层）。

职责：
- L0 基本面层：每季度调用 TradingAgentsGraph(selected_analysts=["fundamentals"])，
  使用 deep_think_llm (pro)，缓存复用。
- L1 决策层：混合触发（价格波动 ≥ ±10% OR 超过 N 天未更新）时调用
  TradingAgentsGraph(selected_analysts=["market"])，使用 quick_think_llm (flash)。
- PM 输出解析：三层递减策略 — 正则提取 → LLM 辅助提取 → 默认值兜底。

关键设计决策：
1. 两个独立的 Graph 实例：fa_graph（仅FA节点）和 decision_graph（仅Market Analyst），
   避免每次 propagate 都重建图。
2. 所有 LLM 调用结果缓存到磁盘，回测中断后可直接复用。
3. 解析失败时不阻塞回测，用保守默认值继续运行。
"""

import json
import logging
import re
from typing import Optional, Dict, Any, Tuple, List

from backtest.models import (
    BacktestConfig, WeeklyDecision, TradeDirection,
    PriceCondition, TechnicalTriggers, FundamentalGuards,
    RuleParser, TradingRule, RuleAction,
)
from backtest.cache_manager import CacheManager
from backtest.fa_cache import flatten_dual_period

logger = logging.getLogger(__name__)


# ── Rating → Direction 映射 ────────────────────────────────────────

_RATING_TO_DIRECTION = {
    "Buy": TradeDirection.BUY,
    "Overweight": TradeDirection.BUY,
    "Hold": TradeDirection.HOLD,
    "Underweight": TradeDirection.SELL,
    "Sell": TradeDirection.SELL,
}

# 2-J: 中文操作符 → Python 操作符归一化映射
_CHINESE_OP_MAP = {
    "跌破": "<",
    "低于": "<",
    "跌破于": "<",
    "跌穿": "<",
    "小于": "<",
    "少于": "<",
    "不到": "<",
    "不足": "<",
    "突破": ">",
    "高于": ">",
    "超过": ">",
    "大于": ">",
    "超过于": ">",
    "多于": ">",
    "升破": ">",
    "高过": ">",
    "等于": "==",
    "达到": ">=",
    "达到或超过": ">=",
    "不低于": ">=",
    "不高于": "<=",
    "不超过": "<=",
    "且": "and",
    "并且": "and",
    "同时": "and",
    "与": "and",
    "或": "or",
    "或者": "or",
}


def _normalize_chinese_condition(condition: str) -> str:
    """2-J 修复：将中文条件字符串归一化为 Python 可 eval 的表达式。
    
    示例:
        "跌破200日均线且缩量" → "close < MA(close,200) and volume < MA(volume,5)"
        "价格高于50且成交量突破1000万" → "close > 50 and volume > 10000000"
    
    注意：只做操作符替换，不处理字段名映射（字段名映射在 eval_condition 中处理）。
    """
    if not condition:
        return condition
    result = condition
    # 按长度降序替换（长模式先匹配，避免部分匹配）
    for cn_op in sorted(_CHINESE_OP_MAP, key=len, reverse=True):
        result = result.replace(cn_op, f" {_CHINESE_OP_MAP[cn_op]} ")
    # 归一化多余空格
    result = " ".join(result.split())
    return result


_RATING_TO_POSITION = {
    "Buy": 0.80,          # 重仓买入
    "Overweight": 0.60,   # 超配
    "Hold": -1.0,         # 不改变仓位（-1 表示保持现状）
    "Underweight": 0.30,  # 减仓
    "Sell": 0.00,         # 清仓
}


class DecisionEngine:
    """L0/L1 决策引擎。

    管理 TradingAgentsGraph 实例的生命周期，提供带缓存的 FA 和决策链调用，
    以及 PM 输出的结构化解析。

    Usage::

        engine = DecisionEngine(config, cache_manager)
        fa_result = engine.run_fundamentals_analysis("000960", "2024-05-15")
        decision = engine.run_decision_chain("000960", "2024-05-20", price_change=0.12)
    """

    def __init__(self, config: BacktestConfig, cache: CacheManager):
        self.config = config
        self.cache = cache

        # 规则解析器实例（复用，避免每次重建正则）
        self._rule_parser = RuleParser()

        # 延迟初始化 Graph 实例（首次需要时才创建，避免 import 开销）
        self._fa_graph = None
        self._decision_graph = None

        # LLM client 注入标志
        self._llm_injected = False

        # ── FA 指标缓存 ──
        # key = f"{symbol}_{report_period}"（如 "000423_2026Q1"）
        # value = flatten_dual_period() 的展平结果 dict
        self._fa_metrics_cache: Dict[str, Dict[str, Any]] = {}
        # 当前生效的 FA quarter key（用于 ExecutionEngine 按日查询）
        self._current_fa_quarter_key: Optional[str] = None

    # ── Graph 工厂方法 ─────────────────────────────────────────────

    def _get_fa_graph(self):
        """获取/创建 FA 专用 Graph（仅 fundamentals analyst，用 pro 模型）。"""
        if self._fa_graph is None:
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            fa_config = self._build_config(deep_model=True)
            self._fa_graph = TradingAgentsGraph(
                selected_analysts=["fundamentals"],
                debug=False,
                config=fa_config,
            )
            logger.info("[DecisionEngine] FA graph created (deepseek-v4-pro)")
        return self._fa_graph

    def _get_decision_graph(self):
        """获取/创建决策链 Graph（仅 market analyst，用 flash 模型）。"""
        if self._decision_graph is None:
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            decision_config = self._build_config(deep_model=False)
            self._decision_graph = TradingAgentsGraph(
                selected_analysts=["market"],
                debug=False,
                config=decision_config,
            )
            logger.info("[DecisionEngine] Decision graph created (deepseek-v4-flash)")
        return self._decision_graph

    def _build_config(self, deep_model: bool = True) -> Dict[str, Any]:
        """构建传给 TradingAgentsGraph 的 config dict。"""
        return {
            "llm_provider": self.config.llm_provider,
            "deep_think_llm": self.config.deep_think_llm,
            "quick_think_llm": self.config.quick_think_llm
            if not deep_model else self.config.deep_think_llm,
            "max_debate_rounds": self.config.max_debate_rounds,
            "max_risk_discuss_rounds": self.config.max_risk_discuss_rounds,
            "backend_url": None,
            "data_cache_dir": str(self.cache.base_dir / "graph_cache"),
            "results_dir": str(self.cache.base_dir / "graph_results"),
            "checkpoint_enabled": False,
            "benchmark_ticker": None,
        }

    # ── L0: 基本面分析 ─────────────────────────────────────────────

    def run_fundamentals_analysis(self, symbol: str, date_str: str) -> Optional[Dict]:
        """运行 L0 基本面分析（每季度一次）。

        Args:
            symbol: 股票代码，如 "000960"
            date_str: 当前日期 YYYY-MM-DD

        Returns:
            FA 分析结果的 state_dict（含 fundamentals_report 等），
            或 None 如果分析失败。
        """
        report_period = self.cache.get_latest_fa_period(date_str)

        # 检查缓存（带分析日期防止跨运行前视）
        cached = self.cache.get_fa_report(symbol, report_period, analysis_date=date_str)
        if cached is not None:
            logger.info(f"[L0-FA] Cache HIT {symbol} {report_period}")
            return cached

        # 需要新跑 FA
        logger.info(f"[L0-FA] Running FA for {symbol} @ {date_str} "
                     f"(period={report_period})")
        try:
            graph = self._get_fa_graph()
            state_dict, signal = graph.propagate(symbol, date_str)

            result = {
                "symbol": symbol,
                "date": date_str,
                "report_period": report_period,
                "signal": signal,
                "fundamentals_report": state_dict.get("fundamentals_report", ""),
                "final_trade_decision": state_dict.get("final_trade_decision", ""),
            }
            self.cache.save_fa_report(symbol, report_period, result, analysis_date=date_str)

            # ★ 并行提取结构化 L1 指标（用于 L2 执行引擎注入）
            self._extract_and_cache_fa_metrics(symbol, report_period, date_str=date_str)

            logger.info(f"[L0-FA] Done {symbol} {report_period} → signal={signal}")
            return result

        except Exception as e:
            logger.error(f"[L0-FA] Failed for {symbol} @ {date_str}: {e}", exc_info=True)
            return None

    # ── L1: 决策链（混合触发）──────────────────────────────────────

    def run_decision_chain(
        self,
        symbol: str,
        date_str: str,
        last_decision_price: float = 0.0,
        current_price: float = 0.0,
        days_since_last_decision: int = 999,
    ) -> WeeklyDecision:
        """运行 L1 决策链（按需触发）。

        触发条件由 BacktestEngine 在调用前判断，此方法只负责执行和解析。

        Args:
            symbol: 股票代码
            date_str: 当前日期 YYYY-MM-DD
            last_decision_price: 上次决策时的价格
            current_price: 当前价格
            days_since_last_decision: 距上次决策的天数

        Returns:
            WeeklyDecision 结构化决策指令
        """
        # 检查决策缓存
        cached = self.cache.get_decision(symbol, date_str)
        if cached is not None:
            logger.info(f"[L1-Decision] Cache HIT {symbol} on {date_str}")
            return self._dict_to_decision(cached)

        # 需要重新跑决策链
        logger.info(f"[L1-Decision] Running for {symbol} @ {date_str}"
                     f" (Δprice={self._pct_change(last_decision_price, current_price):.1f}%, "
                     f"stale={days_since_last_decision}d)")

        try:
            graph = self._get_decision_graph()
            state_dict, signal = graph.propagate(symbol, date_str)

            pm_output = state_dict.get("final_trade_decision", "")
            decision = self._parse_pm_output(pm_output, signal, date_str)

            # ★ 优先使用结构化 trading_rules（绕过 markdown 解析）
            structured_rules = state_dict.get("trading_rules_structured")
            if structured_rules:
                logger.info(f"[L1-Decision] Using {len(structured_rules)} structured trading_rules from PM")
                decision.trading_rules = self._convert_structured_rules(structured_rules)
                decision.rules_parsed_ok = len(decision.trading_rules) > 0

            # 缓存决策结果（包含完整推理链）
            cache_data = self._decision_to_dict(decision)
            # 附加上游 agent 输出，供审计
            cache_data["_reasoning"] = {
                "market_analyst_report": state_dict.get("market_analyst_report", "")[:3000],
                "bull_researcher": state_dict.get("bull_researcher_output", "")[:2000],
                "bear_researcher": state_dict.get("bear_researcher_output", "")[:2000],
                "debator_output": state_dict.get("debator_output", "")[:2000],
                "signal_raw": signal,
                "raw_state_keys": list(state_dict.keys()),
            }
            self.cache.save_decision(symbol, date_str, cache_data)

            # 将推理链挂载到 decision 对象（供 backtest engine 取用）
            decision.reasoning_chain = cache_data["_reasoning"]

            logger.info(f"[L1-Decision] Done {symbol} @ {date_str} → "
                        f"{decision.direction.value} pos={decision.position_pct}")
            return decision

        except Exception as e:
            logger.error(f"[L1-Decision] Failed for {symbol} @ {date_str}: {e}")
            # 返回保守默认决策：HOLD，不改变仓位
            return self._default_decision(date_str, str(e))

    # ── PM 输出解析（三层递减策略）─────────────────────────────────

    def _parse_pm_output(
        self, pm_text: str, signal: str, date_str: str
    ) -> WeeklyDecision:
        """将 PM markdown 输出解析为结构化 WeeklyDecision。

        三层策略：
        1. 正则直接提取（零成本）
        2. LLM 辅助 JSON 提取（低成本 flash 模型）
        3. 默认值兜底（基于 signal 字符串推断）

        每一层解析后都会调用 RuleParser 尝试提取复合交易规则。
        """
        if not pm_text or not pm_text.strip():
            logger.warning("[Parse] Empty PM output, using signal-based default")
            decision = self._signal_based_decision(signal, date_str, pm_text)
            decision.trading_rules = self._rule_parser.parse(
                pm_text, None, None, use_llm=False)
            decision.rules_parsed_ok = len(decision.trading_rules) > 0
            return decision

        # Layer 1: 正则提取
        decision = self._regex_parse(pm_text, signal, date_str)

        # ★ 在正则层之后立即尝试提取交易规则
        decision.trading_rules = self._rule_parser.parse(
            pm_text, decision.price_cond, decision.direction,
            use_llm=False,  # 正则层成功时不浪费 LLM token
        )
        decision.rules_parsed_ok = len(decision.trading_rules) > 0

        if decision.parsed_ok and decision.rules_parsed_ok:
            return decision

        if not decision.parsed_ok:
            # Layer 2: LLM 辅助提取（同时让 LLM 也尝试提规则）
            logger.warning("[Parse] Regex parse incomplete, trying LLM assist")
            try:
                decision = self._llm_assisted_parse(pm_text, signal, date_str)
                # LLM 层后再次尝试规则提取（启用 LLM 辅助）
                if not decision.rules_parsed_ok or len(decision.trading_rules) == 0:
                    decision.trading_rules = self._rule_parser.parse(
                        pm_text, decision.price_cond, decision.direction,
                        use_llm=self._llm_injected,
                    )
                    decision.rules_parsed_ok = len(decision.trading_rules) > 0
                if decision.parsed_ok and decision.rules_parsed_ok:
                    return decision
            except Exception as e:
                logger.warning(f"[Parse] LLM assist failed: {e}")

        # Layer 3: 默认值兜底
        logger.warning("[Parse] All parse layers failed, using signal-based default")
        decision = self._signal_based_decision(signal, date_str, pm_text)
        # 兜底：用默认规则生成器基于已有字段生成规则
        if not decision.rules_parsed_ok or len(decision.trading_rules) == 0:
            decision.trading_rules = self._rule_parser.parse(
                "", decision.price_cond, decision.direction,
                use_llm=False,
            )
            decision.rules_parsed_ok = len(decision.trading_rules) > 0
        return decision

    def _regex_parse(
        self, pm_text: str, signal: str, date_str: str
    ) -> WeeklyDecision:
        """Layer 1: 用正则从 PM markdown 中提取关键字段。"""
        direction = _RATING_TO_DIRECTION.get(signal, TradeDirection.HOLD)
        position_pct = _RATING_TO_POSITION.get(signal, -1.0)

        price_cond = self._safe_price_condition(
            stop_loss=self._extract_stop_loss(pm_text),
            take_profit=self._extract_take_profit(pm_text),
            buy_range=self._extract_entry_range(pm_text),
        )

        pm_rating = self._extract_rating_label(pm_text) or signal

        # 判断是否解析"足够好"——至少有 direction 和 rating
        parsed_ok = direction != TradeDirection.HOLD or signal == "Hold"

        return WeeklyDecision(
            direction=direction,
            position_pct=position_pct,
            price_cond=price_cond,
            technical_triggers=TechnicalTriggers(),
            fundamental_guards=FundamentalGuards(),
            decision_date=date_str,
            signal_raw=signal,
            pm_rating=pm_rating,
            pm_raw_output=pm_text,
            parsed_ok=parsed_ok,
        )

    def _llm_assisted_parse(
        self, pm_text: str, signal: str, date_str: str
    ) -> WeeklyDecision:
        """Layer 2: 用快速 LLM 从 PM 文本中提取结构化 JSON。"""
        prompt = f"""从以下投资决策文本中提取结构化JSON。只返回合法JSON，不要其他文字。

文本：
{pm_text[:2000]}

请提取并返回以下JSON格式（数值无法确定时用 null）：
{{
  "direction": "BUY/SELL/HOLD",
  "position_pct": 0.0~1.0 或 -1(不改变),
  "stop_loss": 止损价或null,
  "take_profit": 止盈价或null,
  "buy_range_low": 买入区间下限或null,
  "buy_range_high": 买入区间上限或null,
  "rating": "Buy/Overweight/Hold/Underweight/Sell",
  "parsed_ok": true/false
}}
"""
        try:
            from tradingagents.llm_clients import create_llm_client
            client = create_llm_client(
                provider=self.config.llm_provider,
                model=self.config.quick_think_llm,
            )
            llm = client.get_llm()
            response = llm.invoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)

            # 提取 JSON
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return WeeklyDecision(
                    direction=TradeDirection(data.get("direction", "HOLD")),
                    position_pct=data.get("position_pct", -1.0),
                    price_cond=self._safe_price_condition(
                        stop_loss=data.get("stop_loss") or 0.0,
                        take_profit=data.get("take_profit") or 0.0,
                        buy_range=self._tuple_from_dict(
                            data.get("buy_range_low"),
                            data.get("buy_range_high"),
                        ),
                    ),
                    technical_triggers=TechnicalTriggers(),
                    fundamental_guards=FundamentalGuards(),
                    decision_date=date_str,
                    signal_raw=signal,
                    pm_rating=data.get("rating", signal),
                    pm_raw_output=pm_text,
                    parsed_ok=data.get("parsed_ok", False),
                )
        except Exception as e:
            logger.warning(f"[LLM-Parse] Extraction failed: {e}")

        # LLM 解析也失败，返回未标记 parsed_ok 的决策
        return self._signal_based_decision(signal, date_str, pm_text)

    # ── 安全默认风控参数 ────────────────────────────────────────
    # PM 解析失败时的兜底值，防止 PriceCondition 默认 0.0 → 零风控
    DEFAULT_STOP_LOSS_PCT = 0.08   # 兜底止损 -8%
    DEFAULT_TAKE_PROFIT_PCT = 0.20 # 兜底止盈 +20%

    def _safe_price_condition(
        self,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        buy_range: Optional[Tuple[float, float]] = None,
        current_price: float = 0.0,
    ) -> PriceCondition:
        """创建 PriceCondition，对 0.0 值使用安全默认值。

        BH-3.1 修复：PM 解析失败时 PriceCondition(stop_loss=0.0, take_profit=0.0)
        会被 ExecutionEngine 的 stop_loss > 0 判断跳过 → 裸奔持仓。
        此方法确保在缺失风控参数时使用基于当前价格的兜底值。
        """
        safe_stop = stop_loss
        safe_take = take_profit

        if current_price > 0:
            if stop_loss <= 0:
                safe_stop = current_price * (1.0 - self.DEFAULT_STOP_LOSS_PCT)
                logger.warning(
                    f"[SafeGuard] stop_loss={stop_loss} → 使用兜底止损 "
                    f"{safe_stop:.2f} (-{self.DEFAULT_STOP_LOSS_PCT:.0%})"
                )
            if take_profit <= 0:
                safe_take = current_price * (1.0 + self.DEFAULT_TAKE_PROFIT_PCT)
                logger.warning(
                    f"[SafeGuard] take_profit={take_profit} → 使用兜底止盈 "
                    f"{safe_take:.2f} (+{self.DEFAULT_TAKE_PROFIT_PCT:.0%})"
                )

        return PriceCondition(
            stop_loss=safe_stop,
            take_profit=safe_take,
            buy_range=buy_range,
        )

    def _signal_based_decision(
        self, signal: str, date_str: str, pm_text: str
    ) -> WeeklyDecision:
        """Layer 3: 仅基于 signal 字符串生成默认决策。

        使用 _safe_price_condition 防止 stop_loss=0.0 → 零风控（BH-3.1 修复）。
        """
        direction = _RATING_TO_DIRECTION.get(signal, TradeDirection.HOLD)
        position_pct = _RATING_TO_POSITION.get(signal, -1.0)

        return WeeklyDecision(
            direction=direction,
            position_pct=position_pct,
            price_cond=self._safe_price_condition(),
            technical_triggers=TechnicalTriggers(),
            fundamental_guards=FundamentalGuards(),
            decision_date=date_str,
            signal_raw=signal,
            pm_rating=signal,
            pm_raw_output=pm_text,
            parsed_ok=False,
        )

    def _default_decision(self, date_str: str, error_reason: str) -> WeeklyDecision:
        """异常时的绝对保守默认决策。

        使用 _safe_price_condition 防止 stop_loss=0.0 → 零风控（BH-3.1 修复）。
        """
        return WeeklyDecision(
            direction=TradeDirection.HOLD,
            position_pct=-1.0,   # 不改变仓位
            price_cond=self._safe_price_condition(),
            technical_triggers=TechnicalTriggers(),
            fundamental_guards=FundamentalGuards(),
            decision_date=date_str,
            signal_raw="Hold",
            pm_rating="Hold",
            pm_raw_output=f"[ERROR] {error_reason}",
            parsed_ok=False,
        )

    def _convert_structured_rules(self, structured_rules: List[Dict]) -> List[TradingRule]:
        """将结构化 trading_rules 转换为 TradingRule 对象。

        直接从 PM 的 PortfolioDecision.trading_rules 获取，绕过 markdown 解析。
        确保 trigger_sql 被正确传递（而不是中文描述）。
        """
        rules = []
        for r_data in structured_rules:
            try:
                # 优先使用 trigger_sql（SQL 形式），fallback 到 trigger_condition
                condition = r_data.get("trigger_sql", "") or r_data.get("trigger_condition", "")
                # 2-J 修复：fallback 中文条件归一化（跌破→<, 超过→>，等）
                if condition and not r_data.get("trigger_sql", ""):
                    condition = _normalize_chinese_condition(condition)

                # 解析 action
                action_str = r_data.get("action", "hold")
                try:
                    action = RuleAction(action_str.lower())
                except ValueError:
                    # 映射常见别名
                    action_map = {
                        "stop_loss": RuleAction.STOP_LOSS,
                        "take_profit": RuleAction.TAKE_PROFIT,
                        "reduce_position": RuleAction.SELL_PCT,
                        "downgrade": RuleAction.SELL_PCT,
                        "sell_all": RuleAction.SELL_ALL,
                        "sell_pct": RuleAction.SELL_PCT,
                        "buy_add": RuleAction.BUY_ADD,
                        "add_position": RuleAction.BUY_ADD,
                        "alert_only": RuleAction.ALERT_ONLY,
                        "rating_reeval": RuleAction.RATING_REEVAL,
                        "rating_adjustment": RuleAction.RATING_REEVAL,
                        "no_left_buy": RuleAction.NO_LEFT_BUY,
                        "circuit_break": RuleAction.CIRCUIT_BREAK,
                        "hold": RuleAction.HOLD,
                    }
                    action = action_map.get(action_str.lower(), RuleAction.HOLD)

                # 解析 priority
                priority = r_data.get("priority", 50)
                # 根据 rule_type 映射标准优先级
                priority_map = {
                    "stop_loss": 90, "take_profit": 85,
                    "reduce_position": 75, "downgrade": 80,
                    "observation_anchor": 60, "entry_zone": 40,
                    "alert_only": 60, "rating_reeval": 70,
                }
                rule_type = r_data.get("rule_type", "").lower()
                if priority == 50 and rule_type in priority_map:
                    priority = priority_map[rule_type]

                # 解析 pct（直接取结构化字段，无正则兜底）
                pct = r_data.get("pct", 0.0)

                rule = TradingRule(
                    name=f"[{rule_type}] {condition[:40]}",
                    action=action,
                    condition_str=condition,
                    priority=priority,
                    pct=pct,
                    source_sentence=r_data.get("action_detail", ""),
                )
                rules.append(rule)
                logger.info(f"[StructuredRule] {rule.name} → action={action.value}, sql={condition[:60]}")
            except Exception as e:
                logger.warning(f"[StructuredRule] Failed to convert rule {r_data}: {e}")
        # 按优先级降序排序（高优先级先执行）
        rules.sort(key=lambda r: r.priority, reverse=True)
        return rules

    @staticmethod
    def _extract_pct_from_text(text: str) -> Optional[float]:
        """从规则文本中提取百分比数字。

        匹配模式：
        - "减仓30%" → 0.30
        - "降至30%仓位" → 0.70（1-B修复：降至 = 卖 1-N）
        - "加仓20%" → 0.20
        - "建立40%底仓" → 0.40
        - "卖出50%" → 0.50

        Returns:
            提取到的百分比（0.0~1.0），或 None（未找到）
        """
        if not text:
            return None
        # 匹配中文描述中的百分比
        patterns = [
            r'(?:减仓|减持|卖出|降至|降到|减至|建立|加仓|买入|增至)[\D]*(\d+)%',
            r'(\d+)%[\D]*(?:仓位|底仓|持仓|比例)',
        ]
        for pat in patterns:
            match = re.search(pat, text)
            if match:
                raw_pct = float(match.group(1)) / 100.0
                # 1-B 修复：降至/降到/减至 → 需要卖 (1-N)，而非 N
                if re.search(r'降至|降到|减至', text):
                    return 1.0 - raw_pct
                return raw_pct
        return None

    # ── 正则提取辅助方法 ──────────────────────────────────────────

    @staticmethod
    def _extract_stop_loss(text: str) -> float:
        """从文本中提取止损价。"""
        patterns = [
            r"止损\s*[价:：]\s*([\d.]+)",
            r"stop.?loss\s*[:\-]\s*([\d.]+)",
            r"SL\s*[:\-]\s*([\d.]+)",
            r"止损线\s*[为是]?\s*([\d.]+)",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    val = float(m.group(1))
                    if val > 0:
                        return val
                except ValueError:
                    continue
        return 0.0

    @staticmethod
    def _extract_take_profit(text: str) -> float:
        """从文本中提取止盈价。"""
        patterns = [
            r"止盈\s*[价:：]\s*([\d.]+)",
            r"take.?profit\s*[:\-]\s*([\d.]+)",
            r"TP\s*[:\-]\s*([\d.]+)",
            r"目标价\s*[为是]?\s*([\d.]+)",
            r"price\s*target\s*[:\-]\s*([\d.]+)",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    val = float(m.group(1))
                    if val > 0:
                        return val
                except ValueError:
                    continue
        return 0.0

    @staticmethod
    def _extract_entry_range(text: str) -> Optional[Tuple[float, float]]:
        """从文本中提取买入区间 (low, high)。"""
        patterns = [
            r"买入区间?\s*[\[（]?\s*([\d.]+)\s*[-~～至]+\s*([\d.]+)",
            r"entry\s*(?:range|zone)\s*[\[（]?\s*([\d.]+)\s*[-~–]+\s*([\d.]+)",
            r"建仓\s*价(?:格)?\s*[\[（]?\s*([\d.]+)\s*[-~～至]+\s*([\d.]+)",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    low, high = float(m.group(1)), float(m.group(2))
                    if 0 < low <= high:
                        return (low, high)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _extract_rating_label(text: str) -> Optional[str]:
        """从文本中显式的 **Rating**: X 标签提取评级。"""
        m = re.search(r"\*{1,2}Rating\*{1,2}\s*[:\-]\s*(\w+)", text, re.IGNORECASE)
        if m:
            return m.group(1).capitalize()
        return None

    @staticmethod
    def _tuple_from_dict(low, high) -> Optional[Tuple[float, float]]:
        """安全地从两个可能为 None 的值构建元组。"""
        if low is not None and high is not None:
            try:
                l, h = float(low), float(high)
                if 0 < l <= h:
                    return (l, h)
            except (ValueError, TypeError):
                pass
        return None

    @staticmethod
    def _pct_change(old_price: float, new_price: float) -> float:
        """计算价格变动百分比。"""
        if old_price == 0:
            return 0.0
        return (new_price - old_price) / old_price

    # ── 序列化 / 反序列化 ─────────────────────────────────────────

    def _decision_to_dict(self, d: WeeklyDecision) -> Dict:
        """WeeklyDecision → 可序列化 dict。"""
        data = {
            "direction": d.direction.value,
            "position_pct": d.position_pct,
            "stop_loss": d.price_cond.stop_loss,
            "take_profit": d.price_cond.take_profit,
            "buy_range": list(d.price_cond.buy_range) if d.price_cond.buy_range else None,
            "trailing_stop_pct": d.price_cond.trailing_stop_pct,
            "technical_triggers": {
                "atr_period": d.technical_triggers.atr_period,
                "rsi_oversold": d.technical_triggers.rsi_oversold,
                "rsi_overbought": d.technical_triggers.rsi_overbought,
                "ma_fast": d.technical_triggers.ma_fast,
                "ma_slow": d.technical_triggers.ma_slow,
            },
            "fundamental_guards": {
                "ocf_to_net_profit_min": d.fundamental_guards.ocf_to_net_profit_min,
                "gross_margin_min": d.fundamental_guards.gross_margin_min,
                "debt_ratio_max": d.fundamental_guards.debt_ratio_max,
            },
            "decision_date": d.decision_date,
            "signal_raw": d.signal_raw,
            "pm_rating": d.pm_rating,
            "pm_raw_output": d.pm_raw_output,
            "parsed_ok": d.parsed_ok,
            # ★ 复合交易规则（新增）
            "trading_rules": [r.to_dict() for r in d.trading_rules],
            "rules_parsed_ok": d.rules_parsed_ok,
        }
        if d.reasoning_chain:
            data["_reasoning"] = d.reasoning_chain
        return data

    def _dict_to_decision(self, data: Dict) -> WeeklyDecision:
        """可序列化 dict → WeeklyDecision。"""
        br = data.get("buy_range")

        # ★ 反序列化 trading_rules（向后兼容：旧缓存无此字段时为空列表）
        rules_raw = data.get("trading_rules", [])
        trading_rules = []
        if isinstance(rules_raw, list):
            for r_data in rules_raw:
                try:
                    trading_rules.append(TradingRule.from_dict(r_data))
                except (KeyError, TypeError, ValueError) as e:
                    logger.warning(f"[De] Failed to deserialize trading_rule: {e}")

        return WeeklyDecision(
            direction=TradeDirection(data["direction"]),
            position_pct=data["position_pct"],
            price_cond=PriceCondition(
                stop_loss=data.get("stop_loss", 0.0),
                take_profit=data.get("take_profit", 0.0),
                buy_range=tuple(br) if br else None,
                trailing_stop_pct=data.get("trailing_stop_pct", 0.08),
            ),
            technical_triggers=TechnicalTriggers(**data.get("technical_triggers", {})),
            fundamental_guards=FundamentalGuards(**data.get("fundamental_guards", {})),
            decision_date=data.get("decision_date", ""),
            signal_raw=data.get("signal_raw", ""),
            pm_rating=data.get("pm_rating", ""),
            pm_raw_output=data.get("pm_raw_output", ""),
            parsed_ok=data.get("parsed_ok", True),
            reasoning_chain=data.get("_reasoning"),
            trading_rules=trading_rules,
            rules_parsed_ok=data.get("rules_parsed_ok", len(trading_rules) > 0),
        )

    # ── FA 指标缓存 ──────────────────────────────────────────────────

    def _extract_and_cache_fa_metrics(
        self, symbol: str, report_period: str, date_str: str = None
    ) -> None:
        """运行 L1 分析并缓存展平后的结构化 FA 指标。

        绕过 LLM Graph，直接调用 L1 分析引擎获取 L1DualPeriodResult，
        然后展平为 {annual_xxx: val, quarter_xxx: val} 字典。

        Args:
            symbol: 股票代码
            report_period: 报告期标识（如 "2026Q1"）
            date_str: 分析日期 YYYY-MM-DD（用于过滤 look-ahead 数据）
        """
        try:
            from tradingagents.l1.data_loader_fixed import get_stock_profile_safe
            from tradingagents.l1.analyzer_l1_enhanced_complete import (
                L1FinancialAnalyzerEnhanced,
            )

            logger.info(f"[FA-Cache] Extracting structured metrics for {symbol} @ {report_period}")
            profile = get_stock_profile_safe(symbol, analysis_date=date_str)
            analyzer = L1FinancialAnalyzerEnhanced(debug=False)
            dual_result = analyzer.analyze_dual(symbol, name=symbol, profile=profile)

            # 展平
            metrics = flatten_dual_period(dual_result)
            # 2-F 修复：缓存键加入 analysis_date，防止跨日期/跨运行前视复用
            cache_key = f"{symbol}_{report_period}_{date_str}"
            self._fa_metrics_cache[cache_key] = metrics
            self._current_fa_quarter_key = cache_key

            scalar_count = len(metrics)
            logger.info(
                f"[FA-Cache] Cached {scalar_count} FA metrics "
                f"for {symbol} @ {report_period}"
            )
        except Exception as e:
            logger.warning(f"[FA-Cache] Failed to extract metrics for {symbol}: {e}")

    def get_fa_metrics(
        self, symbol: str, date_str: str
    ) -> Dict[str, Any]:
        """获取指定股票当前季度的展平 FA 指标。

        从缓存中查询，若缓存未命中返回空字典（不会阻塞回测）。

        Args:
            symbol: 股票代码
            date_str: 当前日期 YYYY-MM-DD（用于确定 quarter key）

        Returns:
            展平后的 FA 指标字典，如 {"annual_roe": 22.5, "quarter_ocf_to_netprofit": 0.95}
        """
        report_period = self.cache.get_latest_fa_period(date_str)
        # 2-F 修复：按日期感知的键查找，回退到向后兼容的无日期键
        prefix = f"{symbol}_{report_period}"
        dated_key = f"{prefix}_{date_str}"
        metrics = self._fa_metrics_cache.get(dated_key, {})
        if not metrics:
            # 回退：无日期键（向后兼容旧缓存）
            metrics = self._fa_metrics_cache.get(prefix, {})
        if not metrics:
            # 最后尝试：查找该 report_period 的最近日期匹配
            for k in sorted(self._fa_metrics_cache.keys(), reverse=True):
                if k.startswith(prefix):
                    metrics = self._fa_metrics_cache[k]
                    break
        if not metrics:
            logger.debug(f"[FA-Cache] Miss for {prefix} @ {date_str}")
        return metrics
