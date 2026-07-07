from tradingagents.agents.utils.agent_utils import get_language_instruction, build_instrument_context, get_master_methodology
from tradingagents.agents.utils.numeric_guard import get_numeric_integrity_prompt


def create_neutral_debator(llm):
    def neutral_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        neutral_history = risk_debate_state.get("neutral_history", "")

        current_aggressive_response = risk_debate_state.get("current_aggressive_response", "")
        current_conservative_response = risk_debate_state.get("current_conservative_response", "")

        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        trader_decision = state["trader_investment_plan"]
        instrument_context = build_instrument_context(
            state["company_of_interest"], stock_name=state.get("stock_name", "")
        )
        master_methodology = get_master_methodology("neutral_debator")

        prompt = f"""{instrument_context}
{master_methodology}

As the Neutral Risk Analyst, your role is to provide a balanced perspective, weighing both the potential benefits and risks of the trader's decision or plan. You prioritize a well-rounded approach, evaluating the upsides and downsides while factoring in broader market trends, potential economic shifts, and diversification strategies.Here is the trader's decision:

{trader_decision}

Your task is to challenge both the Aggressive and Conservative Analysts, pointing out where each perspective may be overly optimistic or overly cautious. Use insights from the following data sources to support a moderate, sustainable strategy to adjust the trader's decision:

Market Research Report: {market_research_report}
Social Media Sentiment Report: {sentiment_report}
Latest World Affairs Report: {news_report}
Company Fundamentals Report: {fundamentals_report}
Here is the current conversation history: {history} Here is the last response from the aggressive analyst: {current_aggressive_response} Here is the last response from the conservative analyst: {current_conservative_response}. If there are no responses from the other viewpoints yet, present your own argument based on the available data.

Engage actively by analyzing both sides critically, addressing weaknesses in the aggressive and conservative arguments to advocate for a more balanced approach. Challenge each of their points to illustrate why a moderate risk strategy might offer the best of both worlds, providing growth potential while safeguarding against extreme volatility. Focus on debating rather than simply presenting data, aiming to show that a balanced view can lead to the most reliable outcomes. Output conversationally as if you are speaking without any special formatting.

**🔑 DATA CITATION RULES (MANDATORY):**
When referencing ANY numeric metric, financial ratio, percentage, or monetary value from the Company Fundamentals Report, you MUST explicitly state which reporting period the data comes from. Use one of these formats:
- "(年报20xx)" for annual report data — e.g., "ROE 9.77%(年报2025)", "存货占比26.91%(年报2025)"
- "(季报20xxQx)" for quarterly data — e.g., "毛利率10.04%(季报2026Q1)"
- NEVER cite a naked number without its period label — e.g., writing "融资成本率1.87%" WITHOUT "(年报2025)" is STRICTLY FORBIDDEN
- If you reference a trend spanning multiple periods, label each period clearly
- This rule applies to ALL financial metrics: margins, ratios, debt levels, cash flow multiples, growth rates, etc.
Violating this rule undermines the credibility of your analysis and confuses readers about whether a value is annual or quarterly.

**⛔ CROSS-PERIOD-TYPE PROHIBITION (CRITICAL):**
You MUST NOT compare an annual (年报, 12-month cumulative) value directly with a quarterly (季报, 3-month single-quarter) value. They have different magnitudes and CANNOT be numerically compared.
- ❌ NEVER write: "指标X从A(年报2025)变化到B(季报2026Q1)"
- ✅ INSTEAD: Keep annual (年报) and quarterly (季报) discussions completely separate. Compare within same type only.""" + get_numeric_integrity_prompt() + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Neutral Analyst: {response.content}"

        new_risk_debate_state = {
            "history": history + "\n" + argument,
            "aggressive_history": risk_debate_state.get("aggressive_history", ""),
            "conservative_history": risk_debate_state.get("conservative_history", ""),
            "neutral_history": neutral_history + "\n" + argument,
            "latest_speaker": "Neutral",
            "current_aggressive_response": risk_debate_state.get(
                "current_aggressive_response", ""
            ),
            "current_conservative_response": risk_debate_state.get("current_conservative_response", ""),
            "current_neutral_response": argument,
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return neutral_node
