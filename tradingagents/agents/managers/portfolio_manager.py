"""Portfolio Manager: synthesises the risk-analyst debate into the final decision.

Uses LangChain's ``with_structured_output`` so the LLM produces a typed
``PortfolioDecision`` directly, in a single call.  The result is rendered
back to markdown for storage in ``final_trade_decision`` so memory log,
CLI display, and saved reports continue to consume the same shape they do
today.  When a provider does not expose structured output, the agent falls
back gracefully to free-text generation.
"""

from __future__ import annotations

import logging

from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
    get_master_methodology,
)
from tradingagents.agents.utils.numeric_guard import get_numeric_integrity_prompt
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext_with_raw,
)


def create_portfolio_manager(llm):
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")

    def portfolio_manager_node(state) -> dict:
        instrument_context = build_instrument_context(state["company_of_interest"], stock_name=state.get("stock_name", ""))
        master_methodology = get_master_methodology("portfolio_manager")

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        research_plan = state["investment_plan"]
        trader_plan = state["trader_investment_plan"]

        past_context = state.get("past_context", "")

        # 截断 past_context 防止 prompt 膨胀（memory log 可积累到 1MB+）
        _MAX_PAST_CONTEXT = 5000
        if len(past_context) > _MAX_PAST_CONTEXT:
            logging.getLogger(__name__).warning(
                "[PM] past_context truncated: %d → %d chars (memory log too large, consider cleaning)",
                len(past_context), _MAX_PAST_CONTEXT,
            )
            past_context = past_context[:_MAX_PAST_CONTEXT] + "\n...(truncated)"

        lessons_line = (
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        # ── 诊断: 记录 prompt 各部分大小 ──
        _pm_diag = logging.getLogger("deepseek_diag")
        _pm_diag.warning(
            "[PM-PROMPT-SIZE] instrument=%d | master=%d | research_plan=%d | trader_plan=%d | history=%d | lessons=%d | numeric=%d",
            len(instrument_context),
            len(master_methodology),
            len(research_plan),
            len(trader_plan),
            len(history),
            len(lessons_line),
            len(get_numeric_integrity_prompt()),
        )

        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.

{instrument_context}
{master_methodology}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
{lessons_line}
**Risk Analysts Debate History:**
{history}

---

**🔑 DATA CITATION RULES (MANDATORY):**
When referencing numeric metrics in your final decision, you MUST preserve or add (年报20xx)/(季报20xxQx) period labels. Never strip period context from analyst arguments.

**⛔ CROSS-PERIOD-TYPE PROHIBITION (CRITICAL):**
NEVER compare an annual (年报, 12-month cumulative) value with a quarterly (季报, 3-month single-quarter) value in your synthesis. If an analyst argument contains such invalid comparisons, reject that specific claim rather than propagating it.

**📋 TRADING RULES (MANDATORY — 结构化输出):**
You MUST enumerate every actionable trading rule as a separate structured item in the `trading_rules` list.  Each rule must answer:
1. **WHEN does it trigger?** → `trigger_sql` (SQL-like expression)
   - For **technical indicators** (MA/RSI/BOLL/KDJ etc.), ALWAYS use function syntax: `close < MA(close,200)`, `RSI(14) < 30`, `close < BOLL_LOWER(20)`, `kdj_k < 20`
   - For **absolute price targets** (stop-loss, take-profit), you MAY use fixed prices: `close < 45.48`, `high > 60.0`
   - For **fundamental metrics**, you MUST use the exact field name with 年报/季报 prefix:
     * 年报指标: `annual_roe`, `annual_gross_margin`, `annual_net_margin`, `annual_ocf_to_netprofit`, `annual_debt_ratio`, `annual_dividend_payout`, `annual_current_ratio`, `annual_interest_coverage`, `annual_cash_coverage`, `annual_revenue_growth`, `annual_profit_growth`
     * 季报指标: `quarter_roe`, `quarter_gross_margin`, `quarter_net_margin`, `quarter_ocf_to_netprofit`, `quarter_debt_ratio`, `quarter_dividend_payout`, `quarter_current_ratio`, `quarter_interest_coverage`, `quarter_cash_coverage`, `quarter_revenue_growth`, `quarter_profit_growth`
     * Examples: `annual_dividend_payout > 50`, `quarter_debt_ratio > 70`, `annual_roe < 15 AND quarter_net_margin < 10`
   - NEVER hard-code a fixed price for dynamic technical conditions like "跌破200日SMA" — use `MA(close,200)` instead
   - Use field names like close/open/high/low/volume, operators (< <= > >= == !=), AND/OR/NOT
2. **WHAT action to take?** → `action` + `action_detail` (e.g. stop_loss + "无条件清仓")
3. **AT what price?** → `price_threshold` (numeric price in 元) + `technical_reference` (e.g. "布林下轨", "200日SMA")

IMPORTANT: Use `trigger_sql` for machine-parseable expressions, NOT `trigger_condition`. The `trigger_sql` field is what the execution engine evaluates daily. Functions available: MA(field,period), RSI(period), MACD(), BOLL_UPPER(period), BOLL_LOWER(period), ATR(period).

Minimum required rules (if applicable):
- **Entry / build-position rules**: If your decision includes BUY or adding positions, you MUST create entry_zone rules for each planned entry point. Examples:
  * `close < MA(close,50)` → action: `buy_add(20%)` (first entry at 50 SMA)
  * `close < BOLL_LOWER(20)` → action: `buy_add(30%)` (second entry at Bollinger lower band)
  * `RSI(14) < 30 AND close < MA(close,20)` → action: `buy_add(15%)` (oversold bounce entry)
  * `close > MA(close,50) AND MACD() > 0 AND volume > MA(volume,20)*1.5` → action: `buy_add(20%)` (trend-following entry - participate in confirmed uptrends, not just dips)
- **Stop-loss / liquidation line**: The absolute floor price — if breached, exit entirely
- **Reduce-position thresholds**: Intermediate levels where you cut part of the position
- **Observation anchors**: Key technical levels to watch (support/resistance/MA crossovers)
- **Take-profit target**: If a price target exists, register it as a take_profit rule

**🏷️ POSITION SIZING (MANDATORY):** Don't wait for perfect entries. When close > MA(close,200) + fundamentals solid, start small (5-10%). Scale in with subsequent buy_add rules. Also include trend-following entry: `close > MA(close,50) AND MACD() > 0 AND volume > MA(volume,20)*1.5`.

CRITICAL: Entry rules are NOT optional. If your decision says "first batch at 50 SMA, second batch at Bollinger lower band", you MUST create corresponding `entry_zone` or `buy_add` rules with `trigger_sql` conditions. Do NOT bury entry plans inside free-form prose — put each one in `trading_rules` so downstream systems can parse and execute them reliably.

**🏷️ FA PREFIX RULE (MANDATORY):**
When a trigger_condition references ANY fundamental metric (ROE, OCF/净利润, 毛利率, 资产负债率, 有息负债率, etc.), you MUST prefix it with either **年报** or **季报** to indicate which data period to use. Never write bare metric names.
- ✅ Correct: 「季报OCF/净利润 < 1.5」「年报ROE < 15%」「年报有息负债率 > 30%」
- ❌ Wrong: 「OCF/净利润 < 1.5」「ROE < 15%」
This ensures downstream execution engines can match the condition against the correct financial period data.

Be decisive and ground every conclusion in specific evidence from the analysts.

{get_numeric_integrity_prompt()}{get_language_instruction()}"""

        final_trade_decision_md, portfolio_decision_raw = invoke_structured_or_freetext_with_raw(
            structured_llm,
            llm,
            prompt,
            render_pm_decision,
            "Portfolio Manager",
            timeout_s=600,
        )

        # ★ 将结构化 trading_rules 注入 state，供下游直接使用（绕过 markdown 解析）
        # portfolio_decision_raw 是 PortfolioDecision Pydantic 对象（如果结构化调用成功）
        trading_rules_structured = []
        if portfolio_decision_raw is not None and hasattr(portfolio_decision_raw, 'trading_rules'):
            try:
                trading_rules_structured = [
                    {
                        "rule_type": r.rule_type,
                        "action": r.action.value,
                        "trigger_sql": r.trigger_sql,
                        "trigger_condition": r.trigger_condition,
                        "price_threshold": r.price_threshold,
                        "technical_reference": r.technical_reference,
                        "action_detail": r.action_detail,
                        "priority": r.priority,
                    }
                    for r in portfolio_decision_raw.trading_rules
                ]
            except Exception as e:
                logging.getLogger(__name__).warning(f"[PM] Failed to extract structured trading_rules: {e}")

        # ★ pct 补充: 从 markdown 的 THEN <action>(N%) 中解析百分比
        # 无论 structured 输出是否成功，都从 prose 中提取 pct 作为兜底
        if trading_rules_structured:
            import re
            pct_from_md = {}
            for m in re.finditer(
                r'(?:THEN|→)\s+(\w+)\s*\((\d+)\s*%?\)',
                final_trade_decision_md,
            ):
                pct_key = (m.group(1), len(pct_from_md))
                pct_from_md[m.group(1)] = float(m.group(2)) / 100.0

            for rule_dict in trading_rules_structured:
                action = rule_dict.get("action", "")
                if action in ("sell_pct", "buy_add") and rule_dict.get("pct", 0) == 0:
                    if action in pct_from_md:
                        rule_dict["pct"] = pct_from_md[action]
                # 从 action_detail 中提取百分比
                ad = rule_dict.get("action_detail", "")
                if rule_dict.get("pct", 0) == 0 and ad:
                    pct_match = re.search(r'(\d+)%', ad)
                    if pct_match:
                        rule_dict["pct"] = float(pct_match.group(1)) / 100.0

        if not trading_rules_structured:
            logging.getLogger(__name__).warning(
                "[PM] No structured trading_rules extracted from PortfolioDecision "
                "(structured call may have fallen back to free-text). "
                "Downstream will rely on markdown parsing if available."
            )
            # ★ 两阶段 Fallback: 主调用无规则时，用精简 prompt + function_calling 单独生成
            try:
                summary = final_trade_decision_md[:3000]
                rules_prompt = f"""Investment Decision for 000423 东阿阿胶:

{summary}

Based EXCLUSIVELY on the decision above, output structured trading rules.
Each rule MUST include: rule_type, action, trigger_sql, trigger_condition, priority, action_detail, price_threshold, technical_reference.

For trigger_sql: close/open/high/low/volume, MA(field,period), RSI(period), MACD(), BOLL_UPPER(period), BOLL_LOWER(period).
Fundamental: annual_roe, annual_gross_margin, quarter_ocf_to_netprofit, annual_debt_ratio.

Actions: stop_loss(sell all), sell_pct, sell_all, buy_add, alert_only, rating_reeval, take_profit.
Rule types: stop_loss, reduce_position, take_profit, entry_zone, observation_anchor, rating_reeval.

CRITICAL: For sell_pct and buy_add, action_detail MUST contain a percentage like '30%'. Do NOT leave it empty."""

                from tradingagents.agents.schemas import TradingRuleItem
                from pydantic import BaseModel, Field
                from typing import List
                import re as _re

                class RulesWrapper(BaseModel):
                    trading_rules: List[TradingRuleItem] = Field(default_factory=list)

                import logging as _log
                rules_llm = None
                try:
                    rules_llm = llm.with_structured_output(RulesWrapper, method="function_calling")
                    _log.getLogger(__name__).info("[PM-Fallback] Rules LLM created")
                except Exception as e2:
                    _log.getLogger(__name__).warning(f"[PM-Fallback] Failed to create rules LLM: {e2}")

                if rules_llm:
                    import time
                    from threading import Thread
                    result = [None]
                    def _invoke():
                        result[0] = rules_llm.invoke(rules_prompt)
                    t = Thread(target=_invoke)
                    t.start()
                    t.join(timeout=120)
                    if result[0] and hasattr(result[0], 'trading_rules') and result[0].trading_rules:
                        trading_rules_structured = []
                        for r in result[0].trading_rules:
                            rule_dict = {
                                "rule_type": r.rule_type,
                                "action": r.action.value,
                                "trigger_sql": r.trigger_sql,
                                "trigger_condition": r.trigger_condition,
                                "price_threshold": r.price_threshold,
                                "technical_reference": r.technical_reference,
                                "action_detail": r.action_detail,
                                "priority": r.priority,
                            }
                            # ★ 从 action_detail 提取 pct
                            pct = 0.0
                            ad = r.action_detail or ""
                            pm = _re.search(r'(\d+)%', ad)
                            if pm:
                                pct = float(pm.group(1)) / 100.0
                            elif r.action.value in ("sell_pct", "buy_add"):
                                pct = 0.3  # 默认 30%
                            rule_dict["pct"] = pct
                            trading_rules_structured.append(rule_dict)
                        _log.getLogger(__name__).info(
                            f"[PM-Fallback] Generated {len(trading_rules_structured)} rules"
                        )
                    else:
                        _log.getLogger(__name__).warning("[PM-Fallback] Rules LLM returned empty")
            except Exception as e:
                logging.getLogger(__name__).warning(f"[PM-Fallback] Failed: {e}")

        new_risk_debate_state = {
            "judge_decision": final_trade_decision_md,
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision_md,
            "trading_rules_structured": trading_rules_structured,
        }

    return portfolio_manager_node
