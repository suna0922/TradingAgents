# TradingAgents/graph/trading_graph.py

import logging
import os
import re
import threading
import time
from pathlib import Path
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

from langgraph.prebuilt import ToolNode

from tradingagents.llm_clients import create_llm_client

from tradingagents.agents import *
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.agents.utils.agent_states import (
    AgentState,
    InvestDebateState,
    RiskDebateState,
)
from tradingagents.dataflows.config import set_config

# Import the new abstract tool methods from agent_utils
from tradingagents.agents.utils.agent_utils import (
    get_stock_data,
    get_indicators,
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_l1_analysis,
    get_news,
    get_insider_transactions,
    get_global_news,
)

from .checkpointer import checkpoint_step, clear_checkpoint, get_checkpointer, thread_id
from .conditional_logic import ConditionalLogic
from .setup import GraphSetup
from .propagation import Propagator
from .reflection import Reflector
from .signal_processing import SignalProcessor


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=["market", "social", "news", "fundamentals"],
        debug=False,
        config: Dict[str, Any] = None,
        callbacks: Optional[List] = None,
    ):
        """Initialize the trading agents graph and components.

        Args:
            selected_analysts: List of analyst types to include
            debug: Whether to run in debug mode
            config: Configuration dictionary. If None, uses default config
            callbacks: Optional list of callback handlers (e.g., for tracking LLM/tool stats)
        """
        self.debug = debug
        self.config = config or DEFAULT_CONFIG
        self.callbacks = callbacks or []

        # Update the interface's config
        set_config(self.config)

        # Create necessary directories
        os.makedirs(self.config["data_cache_dir"], exist_ok=True)
        os.makedirs(self.config["results_dir"], exist_ok=True)

        # Initialize LLMs with provider-specific thinking configuration
        llm_kwargs = self._get_provider_kwargs()

        # Add callbacks to kwargs if provided (passed to LLM constructor)
        if self.callbacks:
            llm_kwargs["callbacks"] = self.callbacks

        deep_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["deep_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )
        quick_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )

        self.deep_thinking_llm = deep_client.get_llm()
        self.quick_thinking_llm = quick_client.get_llm()
        
        self.memory_log = TradingMemoryLog(self.config)

        # Create tool nodes
        self.tool_nodes = self._create_tool_nodes()

        # Initialize components
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.conditional_logic,
            analyst_concurrency_limit=self.config.get("analyst_concurrency_limit", 1),
        )

        self.propagator = Propagator(
            max_recur_limit=self.config.get("max_recur_limit", 100),
        )
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # State tracking
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}  # date to full state dict

        # Set up the graph: keep the workflow for recompilation with a checkpointer.
        self.workflow = self.graph_setup.setup_graph(selected_analysts)
        self.graph = self.workflow.compile()
        self._checkpointer_ctx = None

    def _get_provider_kwargs(self) -> Dict[str, Any]:
        """Get provider-specific kwargs for LLM client creation."""
        kwargs = {}
        provider = self.config.get("llm_provider", "").lower()

        if provider == "google":
            thinking_level = self.config.get("google_thinking_level")
            if thinking_level:
                kwargs["thinking_level"] = thinking_level

        elif provider == "openai":
            reasoning_effort = self.config.get("openai_reasoning_effort")
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort

        elif provider == "anthropic":
            effort = self.config.get("anthropic_effort")
            if effort:
                kwargs["effort"] = effort

        return kwargs

    def _create_tool_nodes(self) -> Dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                ]
            ),
            "social": ToolNode(
                [
                    # News tools for social media analysis
                    get_news,
                ]
            ),
            "news": ToolNode(
                [
                    # News and insider information
                    get_news,
                    get_global_news,
                    get_insider_transactions,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # Fundamental analysis tools
                    get_l1_analysis,      # 推荐：L1完整分析
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                ]
            ),
        }

    # ── A-share detection ──────────────────────────────────────────
    # 6-digit numeric codes are Chinese A-shares (e.g. 600519, 000001)
    _ASHARE_RE = re.compile(r"^\d{6}$")

    # ── Global request throttle for akshare ───────────────────────
    # Prevents rapid sequential calls from triggering 东方财富 anti-crawl.
    _ak_last_call_time: float = 0.0
    _ak_lock = threading.Lock()
    _AK_MIN_INTERVAL: float = 3.0  # seconds between akshare requests (conservative)

    @staticmethod
    def _ak_throttle() -> None:
        """Sleep if needed to enforce minimum interval between akshare calls."""
        with TradingAgentsGraph._ak_lock:
            now = time.monotonic()
            elapsed = now - TradingAgentsGraph._ak_last_call_time
            if elapsed < TradingAgentsGraph._AK_MIN_INTERVAL:
                wait = TradingAgentsGraph._AK_MIN_INTERVAL - elapsed
                logger.debug("akshare throttle: waiting %.1fs", wait)
                time.sleep(wait)
            TradingAgentsGraph._ak_last_call_time = time.monotonic()

    @staticmethod
    def _is_ashare(ticker: str) -> bool:
        """Detect A-share ticker (6-digit numeric code)."""
        return bool(TradingAgentsGraph._ASHARE_RE.match(ticker.strip()))

    def _resolve_benchmark(self, ticker: str) -> str:
        """Pick the benchmark ticker for alpha calculation against ``ticker``.

        ``config["benchmark_ticker"]`` overrides everything when set; otherwise
        the suffix map matches the ticker's exchange suffix (e.g. ``.T`` for
        Tokyo). For Chinese A-shares (6-digit codes), uses Shanghai Composite
        (000001) as default benchmark. US-listed tickers without a dotted suffix
        fall through to the empty-suffix entry (SPY by default).
        """
        explicit = self.config.get("benchmark_ticker")
        if explicit:
            return explicit
        # A-share: use Shanghai Composite by default
        if self._is_ashare(ticker):
            return self.config.get("benchmark_map", {}).get("_ASHARE_DEFAULT", "000001")
        benchmark_map = self.config.get("benchmark_map", {})
        ticker_upper = ticker.upper()
        for suffix, benchmark in benchmark_map.items():
            if suffix and ticker_upper.endswith(suffix.upper()):
                return benchmark
        return benchmark_map.get("", "SPY")

    def _fetch_price_data(self, symbol: str, start_date: str, end_date: str):
        """Fetch OHLCV price data, routing to baostock for A-shares and yfinance for others.

        Uses baostock as primary data source (free, stable, no anti-scraping).
        Falls back to akshare only if baostock returns empty.
        Returns a pandas DataFrame with at least a 'Close' column, or None on failure.
        """
        # A-share path via baostock primary + akshare fallback
        if self._is_ashare(symbol):
            from tradingagents.dataflows import akshare_data

            start_ak = start_date.replace("-", "")
            end_ak = end_date.replace("-", "")

            try:
                # Use baostock primary (same as CLI data path)
                df = akshare_data._fetch_ashare_ohlcv(symbol, start_ak, end_ak)
                if df is not None and not df.empty:
                    return df
                else:
                    logger.warning("baostock returned empty for %s", symbol)
                    return None
            except Exception as e:
                logger.error("baostock fetch failed for %s: %s", symbol, e)
                return None

        # Fallback: yfinance (for US stocks and index benchmarks)
        try:
            import yfinance as yf
            return yf.Ticker(symbol).history(start=start_date, end=end_date)
        except Exception as e:
            logger.warning("yfinance fetch failed for %s: %s", symbol, e)
            return None

    def _fetch_returns(
        self, ticker: str, trade_date: str, holding_days: int = 5,
        benchmark: str = "SPY",
    ) -> Tuple[Optional[float], Optional[float], Optional[int]]:
        """Fetch raw and alpha return for ticker over holding_days from trade_date.

        Routes to akshare for A-share codes and yfinance for others.
        Returns ``(raw_return, alpha_return, actual_holding_days)`` or
        ``(None, None, None)`` if price data is unavailable.
        """
        import pandas as pd
        try:
            start = datetime.strptime(trade_date, "%Y-%m-%d")
            end = start + timedelta(days=holding_days + 7)  # buffer for weekends/holidays
            end_str = end.strftime("%Y-%m-%d")

            stock = self._fetch_price_data(ticker, trade_date, end_str)
            bench = self._fetch_price_data(benchmark, trade_date, end_str)

            if stock is None or bench is None:
                return None, None, None
            if len(stock) < 2 or len(bench) < 2:
                return None, None, None

            actual_days = min(holding_days, len(stock) - 1, len(bench) - 1)
            raw = float(
                (stock["Close"].iloc[actual_days] - stock["Close"].iloc[0])
                / stock["Close"].iloc[0]
            )
            bench_ret = float(
                (bench["Close"].iloc[actual_days] - bench["Close"].iloc[0])
                / bench["Close"].iloc[0]
            )
            alpha = raw - bench_ret
            return raw, alpha, actual_days
        except Exception as e:
            logger.warning(
                "Could not resolve outcome for %s on %s vs %s (will retry next run): %s",
                ticker, trade_date, benchmark, e,
            )
            return None, None, None

    def _resolve_pending_entries(self, ticker: str, current_date: str = None) -> None:
        """Resolve pending log entries for ticker at the start of a new run.

        Fetches returns for each same-ticker pending entry, generates reflections,
        then writes all updates in a single atomic batch write to avoid redundant I/O.
        Skips entries whose price data is not yet available (too recent or delisted).

        1-H 修复：回测模式下只结算 entry_date + holding_days ≤ current_date 的条目，
        防止用未来行情回头结算历史决策、注入 past_context 造成前视污染。

        Trade-off: only same-ticker entries are resolved per run.  Entries for
        other tickers accumulate until that ticker is run again.

        Args:
            ticker: 股票代码
            current_date: 当前模拟日期 YYYY-MM-DD（回测模式时传入，实时模式为 None）
        """
        pending = [e for e in self.memory_log.get_pending_entries() if e["ticker"] == ticker]
        if not pending:
            return

        # 1-H 修复：回测模式下过滤掉 holding 期超前的条目
        from datetime import datetime as _dt, timedelta as _td
        if current_date:
            cur_dt = _dt.strptime(current_date, "%Y-%m-%d")
            hold_days = getattr(self, 'holding_days', 5)  # 默认 5 天持有
            resolvable = []
            for e in pending:
                entry_dt = _dt.strptime(e["date"], "%Y-%m-%d")
                # 该条目结算需要 entry_date + holding_days ≤ current_date
                if (entry_dt + _td(days=hold_days)) <= cur_dt:
                    resolvable.append(e)
                else:
                    logger.debug(
                        f"[Memory] Skipping resolution of {ticker} entry @ {e['date']}: "
                        f"holding period ({hold_days}d) extends beyond {current_date}"
                    )
            pending = resolvable
            if not pending:
                return

        benchmark = self._resolve_benchmark(ticker)
        updates = []
        for entry in pending:
            raw, alpha, days = self._fetch_returns(
                ticker, entry["date"], benchmark=benchmark,
            )
            if raw is None:
                continue  # price not available yet — try again next run
            reflection = self.reflector.reflect_on_final_decision(
                final_decision=entry.get("decision", ""),
                raw_return=raw,
                alpha_return=alpha,
                benchmark_name=benchmark,
            )
            updates.append({
                "ticker": ticker,
                "trade_date": entry["date"],
                "raw_return": raw,
                "alpha_return": alpha,
                "holding_days": days,
                "reflection": reflection,
            })

        if updates:
            self.memory_log.batch_update_with_outcomes(updates)

    def propagate(self, company_name, trade_date, asset_type: str = "stock",
                  position_state: str = ""):
        """Run the trading agents graph for a company on a specific date.

        ``asset_type`` selects between the stock pipeline (default) and the
        crypto pipeline (``"crypto"``) shipped in #567 — the CLI auto-detects
        from the ticker; programmatic callers pass it explicitly. When
        ``checkpoint_enabled`` is set in config, the graph is recompiled with
        a per-ticker SqliteSaver so a crashed run can resume from the last
        successful node on a subsequent invocation with the same ticker+date.
        
        1-A 修复: 新增 position_state 参数，让 PM 感知当前持仓状态。
        """
        self.ticker = company_name

        # Resolve any pending memory-log entries for this ticker before the pipeline runs.
        # 1-H 修复：传入当前日期限制结算范围，防止未来行情污染 past_context
        self._resolve_pending_entries(company_name, current_date=trade_date)

        # Recompile with a checkpointer if the user opted in.
        if self.config.get("checkpoint_enabled"):
            self._checkpointer_ctx = get_checkpointer(
                self.config["data_cache_dir"], company_name
            )
            saver = self._checkpointer_ctx.__enter__()
            self.graph = self.workflow.compile(checkpointer=saver)

            step = checkpoint_step(
                self.config["data_cache_dir"], company_name, str(trade_date)
            )
            if step is not None:
                logger.info(
                    "Resuming from step %d for %s on %s", step, company_name, trade_date
                )
            else:
                logger.info("Starting fresh for %s on %s", company_name, trade_date)

        try:
            return self._run_graph_stream(company_name, trade_date, asset_type=asset_type,
                                          position_state=position_state)
        finally:
            if self._checkpointer_ctx is not None:
                self._checkpointer_ctx.__exit__(None, None, None)
                self._checkpointer_ctx = None
                self.graph = self.workflow.compile()

    def propagate_stream(
        self, company_name, trade_date, on_chunk=None, asset_type: str = "stock"
    ):
        """Like propagate(), but streams intermediate state via on_chunk callback.

        on_chunk(state, node_name) is called after each graph node completes,
        where ``state`` is the cumulative AgentState dict and ``node_name``
        is the name of the node that just finished (or "" for initial state).
        Returns (final_state, signal) same as propagate().
        """
        self.ticker = company_name
        # Web 路径修复：补 current_date 防止 memory 结算无截止
        self._resolve_pending_entries(company_name, current_date=trade_date)

        if self.config.get("checkpoint_enabled"):
            self._checkpointer_ctx = get_checkpointer(
                self.config["data_cache_dir"], company_name
            )
            saver = self._checkpointer_ctx.__enter__()
            self.graph = self.workflow.compile(checkpointer=saver)

        try:
            return self._run_graph_stream(company_name, trade_date, on_chunk=on_chunk, asset_type=asset_type)
        finally:
            if self._checkpointer_ctx is not None:
                self._checkpointer_ctx.__exit__(None, None, None)
                self._checkpointer_ctx = None
                self.graph = self.workflow.compile()

    def _run_graph_stream(
        self, company_name, trade_date, on_chunk=None, asset_type: str = "stock",
        position_state: str = "",
    ):
        # 1-H 修复：传入当前日期限制记忆范围，防止未来行情结算的 reflection 注入
        past_context = self.memory_log.get_past_context(company_name, as_of_date=trade_date)
        try:
            from tradingagents.dataflows.akshare_data import get_stock_name
            resolved_name = get_stock_name(company_name)
        except Exception:
            resolved_name = company_name

        init_agent_state = self.propagator.create_initial_state(
            company_name, trade_date, asset_type=asset_type,
            past_context=past_context, stock_name=resolved_name,
            position_state=position_state,
        )
        args = self.propagator.get_graph_args()
        # Use "updates" mode so we can tell which node produced the delta
        args["stream_mode"] = "updates"

        final_state = dict(init_agent_state)
        if on_chunk:
            on_chunk(final_state, "")

        for chunk in self.graph.stream(init_agent_state, **args):
            for node_name, node_output in chunk.items():
                # node_output is a dict of state fields that changed
                if isinstance(node_output, dict):
                    final_state.update(node_output)
                if on_chunk:
                    on_chunk(final_state, node_name)

        self.curr_state = final_state
        self._log_state(trade_date, final_state)
        self.memory_log.store_decision(
            ticker=company_name, trade_date=trade_date,
            final_trade_decision=final_state["final_trade_decision"],
        )
        return final_state, self.process_signal(final_state["final_trade_decision"])

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file."""
        self.log_states_dict[str(trade_date)] = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
                "judge_decision": final_state["investment_debate_state"][
                    "judge_decision"
                ],
            },
            "trader_investment_decision": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "aggressive_history": final_state["risk_debate_state"]["aggressive_history"],
                "conservative_history": final_state["risk_debate_state"]["conservative_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
            "trading_rules_structured": final_state.get("trading_rules_structured", []),
        }

        # Save to file. Reject ticker values that would escape the
        # results directory when joined as a path component.
        safe_ticker = safe_ticker_component(self.ticker)
        directory = Path(self.config["results_dir"]) / safe_ticker / "TradingAgentsStrategy_logs"
        directory.mkdir(parents=True, exist_ok=True)

        log_path = directory / f"full_states_log_{trade_date}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(self.log_states_dict[str(trade_date)], f, indent=4)

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal)
