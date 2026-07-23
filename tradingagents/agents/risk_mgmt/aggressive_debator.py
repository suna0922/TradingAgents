from tradingagents.agents.utils.agent_utils import get_language_instruction, build_instrument_context, get_master_methodology
from tradingagents.agents.utils.numeric_guard import get_numeric_integrity_prompt


def create_aggressive_debator(llm):
    def aggressive_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        aggressive_history = risk_debate_state.get("aggressive_history", "")

        current_conservative_response = risk_debate_state.get("current_conservative_response", "")
        current_neutral_response = risk_debate_state.get("current_neutral_response", "")

        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        trader_decision = state["trader_investment_plan"]
        instrument_context = build_instrument_context(
            state["company_of_interest"], stock_name=state.get("stock_name", ""), curr_date=state.get("trade_date")
        )
        master_methodology = get_master_methodology("aggressive_debator")

        prompt = f"""{instrument_context}
{master_methodology}

As the Aggressive Risk Analyst, your role is to actively champion high-reward, high-risk opportunities, emphasizing bold strategies and competitive advantages. When evaluating the trader's decision or plan, focus intently on the potential upside, growth potential, and innovative benefits—even when these come with elevated risk. Use the provided market data and sentiment analysis to strengthen your arguments and challenge the opposing views. Specifically, respond directly to each point made by the conservative and neutral analysts, countering with data-driven rebuttals and persuasive reasoning. Highlight where their caution might miss critical opportunities or where their assumptions may be overly conservative. Here is the trader's decision:

{trader_decision}

Your task is to create a compelling case for the trader's decision by questioning and critiquing the conservative and neutral stances to demonstrate why your high-reward perspective offers the best path forward. Incorporate insights from the following sources into your arguments:

Market Research Report: {market_research_report}
Social Media Sentiment Report: {sentiment_report}
Latest World Affairs Report: {news_report}
Company Fundamentals Report: {fundamentals_report}
Here is the current conversation history: {history} Here are the last arguments from the conservative analyst: {current_conservative_response} Here are the last arguments from the neutral analyst: {current_neutral_response}. If there are no responses from the other viewpoints yet, present your own argument based on the available data.

**🔑 DATA CITATION RULES (MANDATORY):**
When referencing ANY numeric metric, financial ratio, percentage, or monetary value from the Company Fundamentals Report, you MUST explicitly state which reporting period the data comes from. Use one of these formats:
- "(年报20xx)" for annual report data — e.g., "融资成本率1.87%(年报2025)", "ROE 9.77%(年报2025)"
- "(季报20xxQx)" for quarterly data — e.g., "毛利率10.04%(季报2026Q1)"
- NEVER cite a naked number without its period label — e.g., writing "融资成本率1.87%" WITHOUT "(年报2025)" is STRICTLY FORBIDDEN
- If you reference a trend spanning multiple periods, label each period clearly
- This rule applies to ALL financial metrics: margins, ratios, debt levels, cash flow multiples, growth rates, etc.
Violating this rule undermines the credibility of your analysis and confuses readers about whether a value is annual or quarterly.

**⛔ CROSS-PERIOD-TYPE PROHIBITION (CRITICAL):**
You MUST NOT compare an annual (年报, 12-month cumulative) value directly with a quarterly (季报, 3-month single-quarter) value. They have different magnitudes and CANNOT be numerically compared.
- ❌ NEVER write: "指标X从A(年报2025)变化到B(季报2026Q1)"
- ✅ INSTEAD: Keep annual (年报) and quarterly (季报) discussions completely separate. Compare within same type only.

Engage actively by addressing any specific concerns raised, refuting the weaknesses in their logic, and asserting the benefits of risk-taking to outpace market norms. Maintain a focus on debating and persuading, not just presenting data. Challenge each counterpoint to underscore why a high-risk approach is optimal. Output conversationally as if you are speaking without any special formatting.
""" + get_numeric_integrity_prompt() + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Aggressive Analyst: {response.content}"

        new_risk_debate_state = {
            "history": history + "\n" + argument,
            "aggressive_history": aggressive_history + "\n" + argument,
            "conservative_history": risk_debate_state.get("conservative_history", ""),
            "neutral_history": risk_debate_state.get("neutral_history", ""),
            "latest_speaker": "Aggressive",
            "current_aggressive_response": argument,
            "current_conservative_response": risk_debate_state.get("current_conservative_response", ""),
            "current_neutral_response": risk_debate_state.get(
                "current_neutral_response", ""
            ),
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return aggressive_node
