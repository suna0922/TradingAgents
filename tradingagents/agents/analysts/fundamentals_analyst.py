from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_l1_analysis,
    get_insider_transactions,
    get_language_instruction,
    get_master_methodology,
)
from tradingagents.dataflows.config import get_config


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"], stock_name=state.get("stock_name", ""), curr_date=state.get("trade_date"))
        master_methodology = get_master_methodology("fundamentals_analyst")

        tools = [
            get_l1_analysis,       # 推荐：L1完整分析（6维64指标）
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
        ]

        system_message = (
            "You are a researcher tasked with analyzing fundamental information over the past week about a company. "
            "Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. "
            "Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + " **Recommended tool**: Use `get_l1_analysis(ticker)` FIRST — it runs a complete L1 fundamental analysis (6 dimensions, 64 indicators, 5-year annual + 4-quarter reports) and returns a structured report. "
            "If you need additional detail on specific statements, you may also use `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, or `get_income_statement` as fallback."
            + "\n\n**🔑 CRITICAL REPORT STRUCTURE RULES (MUST FOLLOW):**\n"
            + "1. **Separate annual and quarterly analysis completely** — Annual data is cumulative (12 months), quarterly data is single-quarter (3 months). They have different magnitudes and MUST NOT be directly compared numerically.\n"
            + "2. **Annual section**: Show 5-year trend (2021→2022→2023→2024→2025) with year-over-year comparisons only.\n"
            + "3. **Quarterly section**: Show 4-quarter trend with quarter-over-quarter or year-over-year comparisons among quarters ONLY.\n"
            + "4. **FORBIDDEN**: Never put annual and quarterly values in the same comparison table (e.g., never compare ROE 9.77% annual vs 4.24% Q1).\n"
            + "5. **FORBIDDEN**: Never label cross-period-type changes as '恶化/改善/deterioration/improvement' (e.g., OCF dropping from 2.36x annual to -0.40x Q1 is NOT deterioration — it's comparing different things).\n"
            + "6. **FORBIDDEN**: Never create a mixed table like '2024年报 | 2025年报 | 2026Q1' for metrics like ROE, margins, OCF ratios.\n"
            + "7. **Allowed cross-reference**: You MAY note directional alignment (e.g., 'both annual trend and quarterly momentum show improving profitability') but NEVER calculate numerical differences between annual and quarterly values.\n"
            + "8. **READ APPENDICES FIRST** — The L1 report contains Appendix A (5-year trend summary for ALL indicators) and/or Appendix B (4-quarter trend summary). You MUST read these appendices BEFORE writing your analysis to ensure you have the complete picture of every indicator's historical trajectory. Never omit any indicator category.\n"
            + "9. **ACTIVE RISK IDENTIFICATION REQUIRED** — In EVERY module (profitability, capital structure, asset quality, cash flow), proactively identify risk signals:\n"
            + "   - Peer comparison anomalies (if peer data available)\n"
            + "   - Multi-year or multi-quarter trend anomalies (e.g., 3+ consecutive years of decline)\n"
            + "   - Absolute value anomalies (e.g., debt ratio > 70%, gross margin < 10%, cash ratio > 30%)\n"
            + "   - Proportion extremes (e.g., receivables/payables imbalance, goodwill > 20% of assets)\n"
            + "   Make your own analytical judgment — do NOT just list numbers.\n"
            + master_methodology
            + get_language_instruction(),
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)

        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
