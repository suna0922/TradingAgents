"""Research Manager: turns the bull/bear debate into a structured investment plan for the trader."""

from __future__ import annotations

from tradingagents.agents.schemas import ResearchPlan, render_research_plan
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
    get_master_methodology,
)
from tradingagents.agents.utils.numeric_guard import get_numeric_integrity_prompt
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


def create_research_manager(llm):
    structured_llm = bind_structured(llm, ResearchPlan, "Research Manager")

    def research_manager_node(state) -> dict:
        instrument_context = build_instrument_context(state["company_of_interest"], stock_name=state.get("stock_name", ""), curr_date=state.get("trade_date"))
        master_methodology = get_master_methodology("research_manager")
        history = state["investment_debate_state"].get("history", "")

        investment_debate_state = state["investment_debate_state"]

        prompt = f"""As the Research Manager and debate facilitator, your role is to critically evaluate this round of debate and deliver a clear, actionable investment plan for the trader.

{instrument_context}
{master_methodology}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction in the bull thesis; recommend taking or growing the position
- **Overweight**: Constructive view; recommend gradually increasing exposure
- **Hold**: Balanced view; recommend maintaining the current position
- **Underweight**: Cautious view; recommend trimming exposure
- **Sell**: Strong conviction in the bear thesis; recommend exiting or avoiding the position

Commit to a clear stance whenever the debate's strongest arguments warrant one; reserve Hold for situations where the evidence on both sides is genuinely balanced.

---

**Debate History:**
{history}

**🔑 DATA CITATION RULES (MANDATORY):**
When referencing ANY numeric metric, financial ratio, percentage, or monetary value from debate arguments (which derive from fundamentals data), you MUST preserve or add reporting period labels. Use formats like "(年报2025)" or "(季报2026Q1)". If the debate citations lack period labels, treat them as unverified and flag them as needing period confirmation. Never propagate naked numbers without period context in your final investment plan.

**⛔ CROSS-PERIOD-TYPE PROHIBITION (CRITICAL):**
When synthesizing bull/bear arguments into your investment plan, NEVER combine or compare an annual (年报) data point with a quarterly (季报) data point. If either analyst makes an invalid cross-type comparison, you MUST exclude or correct that claim in your synthesis rather than passing it through to the trader.

{get_numeric_integrity_prompt()}""" + get_language_instruction()

        investment_plan = invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_research_plan,
            "Research Manager",
        )

        new_investment_debate_state = {
            "judge_decision": investment_plan,
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": investment_plan,
            "count": investment_debate_state["count"],
        }

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": investment_plan,
        }

    return research_manager_node
