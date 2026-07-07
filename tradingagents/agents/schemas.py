"""Pydantic schemas used by agents that produce structured output.

The framework's primary artifact is still prose: each agent's natural-language
reasoning is what users read in the saved markdown reports and what the
downstream agents read as context.  Structured output is layered onto the
three decision-making agents (Research Manager, Trader, Portfolio Manager)
so that:

- Their outputs follow consistent section headers across runs and providers
- Each provider's native structured-output mode is used (json_schema for
  OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic)
- Schema field descriptions become the model's output instructions, freeing
  the prompt body to focus on context and the rating-scale guidance
- A render helper turns the parsed Pydantic instance back into the same
  markdown shape the rest of the system already consumes, so display,
  memory log, and saved reports keep working unchanged
"""

from __future__ import annotations

import re
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Shared rating types
# ---------------------------------------------------------------------------


class PortfolioRating(str, Enum):
    """5-tier rating used by the Research Manager and Portfolio Manager."""

    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class TraderAction(str, Enum):
    """3-tier transaction direction used by the Trader.

    The Trader's job is to translate the Research Manager's investment plan
    into a concrete transaction proposal: should the desk execute a Buy, a
    Sell, or sit on Hold this round.  Position sizing and the nuanced
    Overweight / Underweight calls happen later at the Portfolio Manager.
    """

    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


# ---------------------------------------------------------------------------
# Research Manager
# ---------------------------------------------------------------------------


class ResearchPlan(BaseModel):
    """Structured investment plan produced by the Research Manager.

    Hand-off to the Trader: the recommendation pins the directional view,
    the rationale captures which side of the bull/bear debate carried the
    argument, and the strategic actions translate that into concrete
    instructions the trader can execute against.
    """

    recommendation: PortfolioRating = Field(
        description=(
            "The investment recommendation. Exactly one of Buy / Overweight / "
            "Hold / Underweight / Sell. Reserve Hold for situations where the "
            "evidence on both sides is genuinely balanced; otherwise commit to "
            "the side with the stronger arguments."
        ),
    )
    rationale: str = Field(
        description=(
            "Conversational summary of the key points from both sides of the "
            "debate, ending with which arguments led to the recommendation. "
            "Speak naturally, as if to a teammate."
        ),
    )
    strategic_actions: str = Field(
        description=(
            "Concrete steps for the trader to implement the recommendation, "
            "including position sizing guidance consistent with the rating."
        ),
    )


def render_research_plan(plan: ResearchPlan) -> str:
    """Render a ResearchPlan to markdown for storage and the trader's prompt context."""
    return "\n".join([
        f"**Recommendation**: {plan.recommendation.value}",
        "",
        f"**Rationale**: {plan.rationale}",
        "",
        f"**Strategic Actions**: {plan.strategic_actions}",
    ])


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


class TraderProposal(BaseModel):
    """Structured transaction proposal produced by the Trader.

    The trader reads the Research Manager's investment plan and the analyst
    reports, then turns them into a concrete transaction: what action to
    take, the reasoning that justifies it, and the practical levels for
    entry, stop-loss, and sizing.
    """

    action: TraderAction = Field(
        description="The transaction direction. Exactly one of Buy / Hold / Sell.",
    )
    reasoning: str = Field(
        description=(
            "The case for this action, anchored in the analysts' reports and "
            "the research plan. Two to four sentences."
        ),
    )
    entry_price: Optional[float] = Field(
        default=None,
        description="Optional entry price target in the instrument's quote currency.",
    )
    stop_loss: Optional[float] = Field(
        default=None,
        description="Optional stop-loss price in the instrument's quote currency.",
    )
    position_sizing: Optional[str] = Field(
        default=None,
        description="Optional sizing guidance, e.g. '5% of portfolio'.",
    )


def render_trader_proposal(proposal: TraderProposal) -> str:
    """Render a TraderProposal to markdown.

    The trailing ``FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`` line is
    preserved for backward compatibility with the analyst stop-signal text
    and any external code that greps for it.
    """
    parts = [
        f"**Action**: {proposal.action.value}",
        "",
        f"**Reasoning**: {proposal.reasoning}",
    ]
    if proposal.entry_price is not None:
        parts.extend(["", f"**Entry Price**: {proposal.entry_price}"])
    if proposal.stop_loss is not None:
        parts.extend(["", f"**Stop Loss**: {proposal.stop_loss}"])
    if proposal.position_sizing:
        parts.extend(["", f"**Position Sizing**: {proposal.position_sizing}"])
    parts.extend([
        "",
        f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**",
    ])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Portfolio Manager – Trading Rule sub-schema
# ---------------------------------------------------------------------------


class RuleAction(str, Enum):
    """Action to take when a trading rule is triggered.

    与 backtest.trading_rules.RuleAction 保持一致。
    PM 输出格式：action_name(pct%)，如 sell_pct(30%)、add_position(20%)。
    """

    STOP_LOSS = "stop_loss"           # 完全止损/清仓
    TAKE_PROFIT = "take_profit"        # 止盈/获利了结
    SELL_ALL = "sell_all"              # 全部清仓（无条件）
    SELL_PCT = "sell_pct"              # 按比例减仓（如 sell_pct(30%)）
    BUY_ADD = "buy_add"                # 加仓（如 buy_add(20%)）
    ALERT_ONLY = "alert_only"          # 仅观察/预警，不执行操作
    RATING_REEVAL = "rating_reeval"    # 评级重新评估（触发 PM 复评）
    NO_LEFT_BUY = "no_left_buy"        # 禁止左侧加仓
    CIRCUIT_BREAK = "circuit_break"    # 基本面熔断清仓
    HOLD = "hold"                      # 无动作


class TradingRuleItem(BaseModel):
    """A single machine-parseable trading rule extracted from the PM decision.

    Each item captures one IF-THEN rule with a SQL-like trigger expression
    that the execution engine can evaluate directly on daily OHLCV rows.
    The schema field descriptions serve as the LLM's output instructions.
    """

    rule_type: str = Field(
        description=(
            "Rule category. Exactly one of: "
            "'stop_loss' (止损/清仓线), "
            "'take_profit' (止盈目标价), "
            "'reduce_position' (减仓触发条件), "
            "'observation_anchor' (观察锚点/关键价位), "
            "'entry_zone' (入场区间), "
            "'rating_reeval' (评级重新评估)."
        ),
    )
    action: RuleAction = Field(
        description=(
            "The action to execute when this rule triggers. "
            "Pick the most specific action: stop_loss for liquidation, "
            "sell_pct for partial exit (put percentage in action_detail, e.g. '30%'), "
            "buy_add for adding position (put percentage in action_detail, e.g. '20%'), "
            "alert_only for watch-only levels, "
            "rating_reeval for forcing re-evaluation. "
            "IMPORTANT: If your decision includes entry/build-position plans (e.g. 'buy at 50 SMA', "
            "'add at Bollinger lower band'), you MUST use buy_add action with trigger_sql conditions. "
            "Examples: trigger_sql='close < MA(close,50)' → action=buy_add, action_detail='20%', "
            "trigger_sql='close < BOLL_LOWER(20)' → action=buy_add, action_detail='30%'. "
            "IMPORTANT: Include at least one trend-following entry: "
            "'close > MA(close,50) AND MACD() > 0 AND volume > MA(volume,20)*1.5' "
            "→ action=buy_add. This ensures you participate in confirmed uptrends, "
            "not just dips that may never arrive."
        ),
    )
    trigger_sql: str = Field(
        default="",
        description=(
            "SQL-like trigger expression that the engine evaluates directly. "
            "Use field names (close/open/high/low/volume/turn/rsi/macd/macdh/ma5/ma20/ma50/ma200/"
            "boll_upper/boll_lower/kdj_k/kdj_d/atr/volume_ratio/pct_chg), "
            "operators (< <= > >= == !=), AND/OR/NOT, and functions (MA(field,period), RSI(period), "
            "MACD(), BOLL_UPPER(period), BOLL_LOWER(period), ATR(period)). "
            "For technical indicators ALWAYS use function syntax — e.g. 'close < MA(close,200)' for '跌破200日均线', "
            "'RSI(14) < 30' for RSI oversold, 'close < BOLL_LOWER(20)' for Bollinger break. "
            "Only use fixed prices for absolute stop-loss/take-profit targets. "
            "For fundamental metrics, use annual_* or quarter_* field names: "
            "annual_roe, annual_gross_margin, annual_net_margin, annual_ocf_to_netprofit, "
            "annual_debt_ratio, annual_dividend_payout, annual_current_ratio, "
            "quarter_roe, quarter_gross_margin, quarter_net_margin, quarter_debt_ratio, etc. "
            "Examples: 'close < MA(close,200)', 'close < MA(close,50) AND volume > MA(volume,20)*1.2', "
            "'RSI(14) < 30', 'close < boll_lower AND volume_ratio > 1.5', "
            "'annual_dividend_payout > 50 AND annual_debt_ratio < 70', "
            "'quarter_ocf_to_netprofit < 0.5 OR annual_debt_ratio > 70'."
        ),
    )
    trigger_condition: str = Field(
        default="",
        description=(
            "Optional natural-language description of the trigger for human readability. "
            "If left empty, the system will auto-generate from trigger_sql."
        ),
    )
    price_threshold: Optional[float] = Field(
        default=None,
        description=(
            "The primary numeric price level for display/audit. "
            "Extracted from trigger_sql when applicable."
        ),
    )
    technical_reference: Optional[str] = Field(
        default=None,
        description="Optional technical indicator reference for display.",
    )
    action_detail: Optional[str] = Field(
        default=None,
        description=(
            "Additional detail about what the action entails. "
            "Examples: '无条件清仓', '降至30%仓位', '加仓至满仓'."
        ),
    )
    priority: int = Field(
        default=50,
        ge=0,
        le=100,
        description=(
            "Execution priority (0-100). Higher values execute first. "
            "Recommended defaults: stop_loss=90, take_profit=85, "
            "reduce_position=75, downgrade=80, observation_anchor=60, "
            "entry_zone=40, alert_only=60, rating_reeval=70."
        ),
    )

    @field_validator("action", mode="before")
    @classmethod
    def _clean_action(cls, v: str) -> str:
        """Strip parenthetical percentage from action strings.

        The PM LLM often outputs ``sell_pct(30%)`` / ``buy_add(20%)`` even
        though the enum values are ``sell_pct`` / ``buy_add``.  This validator
        normalises such inputs automatically so Pydantic validation succeeds.
        """
        if isinstance(v, str):
            # Remove trailing parenthetical: "sell_pct(30%)" → "sell_pct"
            v = re.sub(r"\([^)]*\)$", "", v.strip())
        return v


# ---------------------------------------------------------------------------
# Portfolio Manager
# ---------------------------------------------------------------------------


class PortfolioDecision(BaseModel):
    """Structured output produced by the Portfolio Manager.

    The model fills every field as part of its primary LLM call; no separate
    extraction pass is required. Field descriptions double as the model's
    output instructions, so the prompt body only needs to convey context and
    the rating-scale guidance.
    """

    rating: PortfolioRating = Field(
        description=(
            "The final position rating. Exactly one of Buy / Overweight / Hold / "
            "Underweight / Sell, picked based on the analysts' debate."
        ),
    )
    executive_summary: str = Field(
        description=(
            "A concise action plan covering entry strategy, position sizing, "
            "key risk levels, and time horizon. Two to four sentences."
        ),
    )
    investment_thesis: str = Field(
        description=(
            "Detailed reasoning anchored in specific evidence from the analysts' "
            "debate. If prior lessons are referenced in the prompt context, "
            "incorporate them; otherwise rely solely on the current analysis."
        ),
    )
    price_target: Optional[float] = Field(
        default=None,
        description="Optional target price in the instrument's quote currency.",
    )
    time_horizon: Optional[str] = Field(
        default=None,
        description="Optional recommended holding period, e.g. '3-6 months'.",
    )
    trading_rules: List[TradingRuleItem] = Field(
        default_factory=list,
        description=(
            "MANDATORY — List of structured trading rules with SQL-like trigger expressions. "
            "You MUST enumerate every actionable rule as a separate TradingRuleItem. "
            "For each rule, provide a trigger_sql expression using field names and operators "
            "that the engine can evaluate directly against daily OHLCV data. "
            "Include at minimum: "
            "(1) ENTRY rules if your decision is BUY or add-position: "
            "    'close < MA(close,50)' → action=buy_add(20%), "
            "    'close < BOLL_LOWER(20)' → action=buy_add(30%), "
            "    'RSI(14) < 30 AND close < MA(close,20)' → action=buy_add(15%). "
            "    NEVER omit entry rules — they are as important as stop-loss rules. "
            "(2) a stop-loss rule (e.g. 'close < 45.48' or 'low < 44.12'), "
            "(3) any reduce-position rules (e.g. 'close < 50 AND volume > 20000'), "
            "(4) take-profit rules if applicable. "
            "Use AND for multiple conditions, OR for alternatives, NOT for negation. "
            "Use MA(field,period) for moving averages, RSI(period) for RSI, "
            "BOLL_UPPER(period)/BOLL_LOWER(period) for Bollinger Bands. "
            "If no specific rules can be derived, return an empty list."
        ),
    )


def render_pm_decision(decision: PortfolioDecision) -> str:
    """Render a PortfolioDecision back to the markdown shape the rest of the system expects.

    Memory log, CLI display, and saved report files all read this markdown,
    so the rendered output preserves the exact section headers (``**Rating**``,
    ``**Executive Summary**``, ``**Investment Thesis**``) that downstream
    parsers and the report writers already handle.

    The **Trading Rules** section is rendered in a machine-parseable format:
    each rule on its own line with a structured ``[TYPE] IF <condition>
    THEN <action> (<price>)`` template, making downstream extraction
    reliable without fragile regex.
    """
    parts = [
        f"**Rating**: {decision.rating.value}",
        "",
        f"**Executive Summary**: {decision.executive_summary}",
        "",
        f"**Investment Thesis**: {decision.investment_thesis}",
    ]
    if decision.price_target is not None:
        parts.extend(["", f"**Price Target**: {decision.price_target}"])
    if decision.time_horizon:
        parts.extend(["", f"**Time Horizon**: {decision.time_horizon}"])

    # --- Structured trading rules (machine-parseable SQL-like) ---
    if decision.trading_rules:
        parts.extend(["", "**Trading Rules:**"])
        for idx, rule in enumerate(decision.trading_rules, 1):
            # Build action detail suffix
            detail_suffix = f" — {rule.action_detail}" if rule.action_detail else ""
            # Price string
            price_str = f"{rule.price_threshold}元" if rule.price_threshold is not None else "N/A"
            # Use trigger_sql if available, otherwise fallback to trigger_condition
            trigger_expr = rule.trigger_sql if rule.trigger_sql else rule.trigger_condition
            # Compose the parseable line with SQL expression
            # Format: - [RULE1] [stop_loss] WHEN close < 45.48 THEN stop_loss — 清仓 (@ 45.48元)
            # 对于 sell_pct/buy_add，输出格式包含参数：sell_pct(30%)
            action_str = rule.action.value
            if rule.action in (RuleAction.SELL_PCT, RuleAction.BUY_ADD) and rule.action_detail:
                # 从 action_detail 提取比例，如 "降至30%仓位" → 30
                pct_match = re.search(r'(\d+)%', rule.action_detail)
                if pct_match:
                    action_str = f"{rule.action.value}({pct_match.group(1)}%)"
                elif rule.action == RuleAction.SELL_PCT and ('剩余仓位' in rule.action_detail or '全部' in rule.action_detail or '清仓' in rule.action_detail):
                    # 卖出剩余仓位/全部清仓的语义，应使用 stop_loss
                    action_str = RuleAction.STOP_LOSS.value
            line = (
                f"- [RULE{idx}] [{rule.rule_type}] "
                f"WHEN {trigger_expr} "
                f"THEN {action_str}{detail_suffix} "
                f"(@ {price_str})"
            )
            parts.append(line)
        parts.append("")  # trailing blank line

    return "\n".join(parts)
