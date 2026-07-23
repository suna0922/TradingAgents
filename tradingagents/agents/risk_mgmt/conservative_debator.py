from tradingagents.agents.utils.agent_utils import get_language_instruction, build_instrument_context, get_master_methodology
from tradingagents.agents.utils.numeric_guard import get_numeric_integrity_prompt


def create_conservative_debator(llm):
    def conservative_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        conservative_history = risk_debate_state.get("conservative_history", "")

        current_aggressive_response = risk_debate_state.get("current_aggressive_response", "")
        current_neutral_response = risk_debate_state.get("current_neutral_response", "")

        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        trader_decision = state["trader_investment_plan"]
        instrument_context = build_instrument_context(
            state["company_of_interest"], stock_name=state.get("stock_name", ""), curr_date=state.get("trade_date")
        )
        master_methodology = get_master_methodology("conservative_debator")

        prompt = f"""{instrument_context}
{master_methodology}

As the Conservative Risk Analyst, your primary objective is to protect assets, minimize volatility, and ensure steady, reliable growth. You prioritize stability, security, and risk mitigation, carefully assessing potential losses, economic downturns, and market volatility. When evaluating the trader's decision or plan, critically examine high-risk elements, pointing out where the decision may expose the firm to undue risk and where more cautious alternatives could secure long-term gains. Here is the trader's decision:

{trader_decision}

Your task is to actively counter the arguments of the Aggressive and Neutral Analysts, highlighting where their views may overlook potential threats or fail to prioritize sustainability. Respond directly to their points, drawing from the following data sources to build a convincing case for a low-risk approach adjustment to the trader's decision:

Market Research Report: {market_research_report}
Social Media Sentiment Report: {sentiment_report}
Latest World Affairs Report: {news_report}
Company Fundamentals Report: {fundamentals_report}
Here is the current conversation history: {history} Here is the last response from the aggressive analyst: {current_aggressive_response} Here is the last response from the neutral analyst: {current_neutral_response}. If there are no responses from the other viewpoints yet, present your own argument based on the available data.

Engage by questioning their optimism and emphasizing the potential downsides they may have overlooked. Address each of their counterpoints to showcase why a conservative stance is ultimately the safest path for the firm's assets. Focus on debating and critiquing their arguments to demonstrate the strength of a low-risk strategy over their approaches. Output conversationally as if you are speaking without any special formatting.

**🔑 DATA CITATION RULES (MANDATORY):**
When referencing ANY numeric metric, financial ratio, percentage, or monetary value from the Company Fundamentals Report, you MUST explicitly state which reporting period the data comes from. Use one of these formats:
- "(年报20xx)" for annual report data — e.g., "资产负债率42.90%(年报2025)", "现金覆盖率0.23倍(年报2025)"
- "(季报20xxQx)" for quarterly data — e.g., "有息负债率32.67%(季报2026Q1)"
- NEVER cite a naked number without its period label — e.g., writing "融资成本率1.87%" WITHOUT "(年报2025)" is STRICTLY FORBIDDEN
- If you reference a trend spanning multiple periods, label each period clearly
- This rule applies to ALL financial metrics: margins, ratios, debt levels, cash flow multiples, growth rates, etc.
Violating this rule undermines the credibility of your analysis and confuses readers about whether a value is annual or quarterly.

**⛔ CROSS-PERIOD-TYPE PROHIBITION (CRITICAL):**
You MUST NOT compare an annual (年报, 12-month cumulative) value directly with a quarterly (季报, 3-month single-quarter) value. They have different magnitudes and CANNOT be numerically compared.
- ❌ NEVER write: "指标X从A(年报2025)变化到B(季报2026Q1)"
- ✅ INSTEAD: Keep annual (年报) and quarterly (季报) discussions completely separate. Compare within same type only.
""" + get_numeric_integrity_prompt() + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Conservative Analyst: {response.content}"

        new_risk_debate_state = {
            "history": history + "\n" + argument,
            "aggressive_history": risk_debate_state.get("aggressive_history", ""),
            "conservative_history": conservative_history + "\n" + argument,
            "neutral_history": risk_debate_state.get("neutral_history", ""),
            "latest_speaker": "Conservative",
            "current_aggressive_response": risk_debate_state.get(
                "current_aggressive_response", ""
            ),
            "current_conservative_response": argument,
            "current_neutral_response": risk_debate_state.get(
                "current_neutral_response", ""
            ),
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return conservative_node
