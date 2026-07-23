from tradingagents.agents.utils.agent_utils import get_language_instruction, build_instrument_context, get_master_methodology
from tradingagents.agents.utils.numeric_guard import get_numeric_integrity_prompt


def create_bear_researcher(llm):
    def bear_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bear_history = investment_debate_state.get("bear_history", "")

        current_response = investment_debate_state.get("current_response", "")
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        asset_type = state.get("asset_type", "stock")
        target_label = "stock" if asset_type == "stock" else "asset"
        fundamentals_label = (
            "Company fundamentals report"
            if asset_type == "stock"
            else "Asset fundamentals report (may be unavailable for crypto)"
        )
        instrument_context = build_instrument_context(
            state["company_of_interest"], stock_name=state.get("stock_name", ""), curr_date=state.get("trade_date")
        )
        master_methodology = get_master_methodology("bear_researcher")

        prompt = f"""{instrument_context}
{master_methodology}

You are a Bear Analyst making the case against investing in the {target_label}. Your goal is to present a well-reasoned argument emphasizing risks, challenges, and negative indicators. Leverage the provided research and data to highlight potential downsides and counter bullish arguments effectively.

Key points to focus on:

- Risks and Challenges: Highlight factors like market saturation, financial instability, or macroeconomic threats that could hinder the stock's performance.
- Competitive Weaknesses: Emphasize vulnerabilities such as weaker market positioning, declining innovation, or threats from competitors.
- Negative Indicators: Use evidence from financial data, market trends, or recent adverse news to support your position.
- Bull Counterpoints: Critically analyze the bull argument with specific data and sound reasoning, exposing weaknesses or over-optimistic assumptions.
- Engagement: Present your argument in a conversational style, directly engaging with the bull analyst's points and debating effectively rather than simply listing facts.

Resources available:

Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
{fundamentals_label}: {fundamentals_report}
Conversation history of the debate: {history}
Last bull argument: {current_response}
Use this information to deliver a compelling bear argument, refute the bull's claims, and engage in a dynamic debate that demonstrates the risks and weaknesses of investing in the {target_label}.

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
- ❌ NEVER write: "OCF/净利润从0.66倍(年报2025)跌至-0.40倍(季报2026Q1)"
- ❌ NEVER write: "营业周期从91天(年报2025)拉长至292天(季报2026Q1)"
- ✅ INSTEAD: Discuss annual data in annual context (YoY trends across 年报), and quarterly data in quarterly context (QoQ trends across 季报). Keep them in separate sentences or paragraphs.""" + get_numeric_integrity_prompt() + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Bear Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bear_history": bear_history + "\n" + argument,
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bear_node
