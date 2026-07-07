from langchain_core.tools import tool
from typing import Annotated
from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_fundamentals(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
) -> str:
    """
    Retrieve comprehensive fundamental data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing comprehensive fundamental data
    """
    return route_to_vendor("get_fundamentals", ticker, curr_date)


@tool
def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve balance sheet data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing balance sheet data
    """
    return route_to_vendor("get_balance_sheet", ticker, freq, curr_date)


@tool
def get_cashflow(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve cash flow statement data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing cash flow statement data
    """
    return route_to_vendor("get_cashflow", ticker, freq, curr_date)


@tool
def get_income_statement(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve income statement data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing income statement data
    """
    return route_to_vendor("get_income_statement", ticker, freq, curr_date)


def _truncate_l1_report(full_report: str, max_chars: int = 8000) -> str:
    """智能截断 L1 报告以适配 LLM context window。

    保留策略（按优先级）：
    1. 标题 + 综合评分 + 关键指标摘要表
    2. 每个维度（盈利/资本结构/资产质量/风险/成长/分红）的**核心指标表**
    3. 五年趋势表（最紧凑的数据）
    4. 截断：详细文字分析、大段解读、逐项明细

    目标: 将 ~24KB 的原始报告压缩到 ~8KB 以内，
         确保 deepseek-v4-pro (128K ctx) 能舒适容纳 system prompt + tool result。
         fundamentals analyst 已切换至 deep_thinking_llm (v4-pro)，不再使用 v4-flash。
    """
    if len(full_report) <= max_chars:
        return full_report

    lines = full_report.split("\n")
    sections = []          # (header_line_idx, content_lines)
    current_section = []
    in_table = False

    for line in lines:
        # 检测表格行（含 | 分隔符）
        is_table_row = "|" in line and line.strip().startswith("|")
        # 检测章节标题
        is_header = (
            line.startswith("#") or
            (line.startswith("**") and "：" in line) or
            (line.startswith("---"))
        )

        if is_header and current_section:
            sections.append(current_section)
            current_section = []

        current_section.append(line)

    if current_section:
        sections.append(current_section)

    # ── 按优先级打分并选择章节 ──
    scored_sections = []
    for idx, sec_lines in enumerate(sections):
        text = "\n".join(sec_lines)
        score = 0
        # 高优先级：摘要/评分/关键指标
        if any(kw in text for kw in ["综合评分", "关键指标摘要", "## 一、年报"]):
            score += 100
        # 中优先级：五年趋势表
        if "五年趋势" in text:
            score += 80
        if "季报" in text or"季度" in text:
            score += 70
        # 中优先级：核心指标表（紧凑）
        if any(kw in text for kw in ["ROE", "毛利率", "净利率", "资产负债率",
                                       "现金流", "股息"]):
            score += 60
        # 低优先级：详细解读、明细项
        if any(kw in text for kw in ["详情", "明细", "变动", "拆解",
                                       "驱动因素", "策略分类"]):
            score += 20

        table_count = sum(1 for l in sec_lines if "|" in l and l.strip().startswith("|"))
        score += min(table_count * 2, 20)  # 表格行加分但有上限

        scored_sections.append((score, idx, sec_lines))

    # 按分数降序排列，高优先级先选
    scored_sections.sort(key=lambda x: -x[0])

    result_lines = []
    total_chars = 0
    used_indices = set()

    for _, orig_idx, sec_lines in scored_sections:
        sec_text = "\n".join(sec_lines) + "\n"
        if total_chars + len(sec_text) > max_chars:
            # 尝试截断该 section 内的尾部低价值内容
            remaining = max_chars - total_chars
            partial = []
            partial_chars = 0
            for l in sec_lines:
                if partial_chars + len(l) + 1 > remaining:
                    break
                partial.append(l)
                partial_chars += len(l) + 1
            if partial:
                result_lines.extend(partial)
                result_lines.append("... *(内容已截断以控制长度)*")
            break
        result_lines.extend(sec_lines)
        total_chars += len(sec_text)
        used_indices.add(orig_idx)

    return "\n".join(result_lines)


@tool
def get_l1_analysis(
    ticker: Annotated[str, "A-share ticker symbol, 6 digits, e.g. 600519"],
    output_dir: Annotated[str, "optional directory path to save raw L1 report for hallucination auditing"] = None,
    curr_date: Annotated[str, "current analysis date, yyyy-mm-dd. CRITICAL: pass this to prevent future data leakage (look-ahead bias)"] = None,
) -> str:
    """
    Run complete L1 fundamental analysis (6 dimensions, 64 indicators, 5-year annual + 4-quarter reports).
    Returns a structured report covering: capital structure, asset quality, profitability,
    risk signals, 5-year trend analysis, and comprehensive scoring (A/B/C/D rating).
    This is the recommended entry point for fundamental analysis, replacing separate calls to
    get_fundamentals / get_balance_sheet / get_cashflow / get_income_statement.
    The raw machine-generated L1 report is automatically saved to {results_dir}/{ticker}/0_l1_raw/
    for audit of downstream LLM-generated reports (hallucination detection).
    NOTE: The returned report is automatically truncated to fit LLM context windows (~8KB max).
          The full un-truncated version is always saved to disk for auditing.
    Args:
        ticker (str): A-share ticker symbol (6-digit string, e.g. '600519')
        output_dir (str, optional): override directory path to save raw L1 report for audit
        curr_date (str, optional): current analysis date yyyy-mm-dd. REQUIRED for backtesting —
            filters out all report periods AFTER this date to prevent look-ahead bias.
            If not provided, all available data (including future periods) will be included.
    Returns:
        str: L1 analysis report (truncated for LLM consumption; full version saved to disk)
    """
    from tradingagents.l1 import run_l1_analysis
    from tradingagents.default_config import DEFAULT_CONFIG

    # 若未显式指定 output_dir，自动保存到 results_dir/{ticker}/ 下
    if output_dir is None:
        from pathlib import Path as _P
        output_dir = str(_P(DEFAULT_CONFIG["results_dir"]) / ticker)

    full_report = run_l1_analysis(ticker, name=ticker, output_dir=output_dir, analysis_date=curr_date)
    # 截断返回值以避免超过 DeepSeek flash 的 context window (64K tokens)
    # 原始完整版已保存到磁盘供审计
    return _truncate_l1_report(full_report, max_chars=8000)