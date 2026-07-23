from langchain_core.messages import HumanMessage, RemoveMessage

# Import tools from separate utility files
from tradingagents.agents.utils.core_stock_tools import (
    get_stock_data
)
from tradingagents.agents.utils.technical_indicators_tools import (
    get_indicators
)
from tradingagents.agents.utils.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_l1_analysis,
)
from tradingagents.agents.utils.news_data_tools import (
    get_news,
    get_insider_transactions,
    get_global_news
)


def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Applied to every agent whose output reaches the saved report —
    analysts, researchers, debaters, research manager, trader, and
    portfolio manager — so a non-English run produces a fully localized
    report rather than a mix of languages.
    """
    from tradingagents.dataflows.config import get_config
    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return ""
    return f" Write your entire response in {lang}."


def build_instrument_context(
    ticker: str,
    asset_type: str = "stock",
    stock_name: str = "",
    curr_date: str = None,
) -> str:
    """Describe the exact instrument so agents preserve exchange-qualified tickers.

    For A-shares, also appends a price-based valuation snapshot
    (PE静态/PB/PEG/总市值/PS/股息率) so that EVERY role — not just the
    fundamentals analyst — sees the same valuation numbers as the web
    data panel (single source: akshare_data.get_valuation_metrics).
    Pass ``curr_date`` (trade date) to keep the snapshot look-ahead safe
    in backtests; defaults to today for real-time analysis.
    """
    instrument_label = "asset" if asset_type == "crypto" else "instrument"
    extra_hint = (
        " Treat it as a crypto asset rather than a company, and do not assume company fundamentals are available."
        if asset_type == "crypto"
        else ""
    )
    name_hint = f" Its full name is **{stock_name}**." if stock_name else ""
    context = (
        f"The {instrument_label} to analyze is `{ticker}`.{name_hint}"
        " Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`, `-USD`)."
        + extra_hint
    )
    # ── 估值快照注入（A股，模块级缓存，失败安全返回空串）──
    if asset_type == "stock":
        try:
            from tradingagents.dataflows.akshare_data import get_valuation_snapshot
            context += get_valuation_snapshot(ticker, curr_date)
        except Exception:
            pass
        # ── 全量数据注入：财务24项 + OHLCV概览 + L1（所有角色共用 DataSession 缓存）──
        try:
            context += build_full_data_context(ticker, curr_date)
        except Exception:
            pass
    return context


def build_full_data_context(ticker: str, curr_date: str = None) -> str:
    """注入：财务24项 + OHLCV技术面概览 + L1深度报告 → 所有角色prompt。

    与 build_instrument_context 互补（后者含标的标识+估值快照）。
    三层数据均来自 DataSession 全局缓存，零额外 API 调用。
    """
    parts = []

    # ── 层1: 财务24项明细 ──
    try:
        from tradingagents.dataflows.akshare_data import get_fundamentals_structured
        # 按 ticker 键控读取，避免并发会话下拿到其他标的的数据
        s = get_fundamentals_structured(ticker)
        if s and len(s) > 3:  # 至少 ticker+source+1个指标
            lines = ["\n【同花顺财务摘要（最新报告期，与面板同源）】"]
            # 按中文字段名排序输出（跳过内部字段）
            skip = {'ticker', 'source'}
            metrics = sorted(
                [(k, v) for k, v in s.items() if k not in skip and v is not None],
                key=lambda x: x[0]
            )
            for sk, val in metrics:
                if isinstance(val, (int, float)):
                    lines.append(f"- {sk}: {val}")
            parts.append("\n".join(lines))
    except Exception:
        pass

    # ── 层2: OHLCV 技术面概览 ──
    try:
        from tradingagents.dataflows.akshare_data import _load_ohlcv_akshare
        ohlcv = _load_ohlcv_akshare(ticker, curr_date)
        if ohlcv is not None and not ohlcv.empty and len(ohlcv) >= 20:
            close = ohlcv["Close"]
            price = float(close.iloc[-1])
            prev = float(close.iloc[-2]) if len(close) > 1 else price
            chg = round((price - prev) / prev * 100, 2) if prev else 0
            high_60 = float(close.tail(60).max())
            low_60 = float(close.tail(60).min())
            ma20 = round(float(close.tail(20).mean()), 2)
            vol_recent = ohlcv["Volume"].tail(5).mean()
            vol_month = ohlcv["Volume"].tail(20).mean() if len(ohlcv) >= 20 else vol_recent
            vol_ratio = round(vol_recent / vol_month, 2) if vol_month else 1
            parts.append(f"\n【OHLCV技术面概览】\n- 最新价: {price} ({'+' if chg>=0 else ''}{chg}%)\n- 60日最高: {high_60}\n- 60日最低: {low_60}\n- 近5日均量/20日均量: {vol_ratio}x\n- 20日均线: {ma20}")
    except Exception:
        pass

    return "".join(parts)


def get_master_methodology(role: str, master_id: str = None) -> str:
    """Return formatted methodology prompt snippet for the given role.

    Reads from config (master_config dict) or explicit override.
    Returns "" if role has no master assigned ("default").

    Args:
        role: Agent role key (e.g. "bull_researcher", "aggressive_debator")
        master_id: Explicit master override; None reads from config.
    """
    from tradingagents.masters.loader import get_master_methodology as _get
    return _get(role, master_id)

def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        messages = state["messages"]

        # Remove all messages
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        # Add a minimal placeholder message
        placeholder = HumanMessage(content="Continue")

        return {"messages": removal_operations + [placeholder]}

    return delete_messages


        
