import os

_TRADINGAGENTS_HOME = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")

# Single source of truth for env-var → config-key overrides. To expose
# a new config key for environment-based override, add a row here — no
# entry-point script changes required. Coercion is driven by the type
# of the existing default, so users can keep writing plain strings in
# their .env file.
_ENV_OVERRIDES = {
    "TRADINGAGENTS_LLM_PROVIDER":         "llm_provider",
    "TRADINGAGENTS_DEEP_THINK_LLM":       "deep_think_llm",
    "TRADINGAGENTS_QUICK_THINK_LLM":      "quick_think_llm",
    "TRADINGAGENTS_LLM_BACKEND_URL":      "backend_url",
    "TRADINGAGENTS_OUTPUT_LANGUAGE":      "output_language",
    "TRADINGAGENTS_MAX_DEBATE_ROUNDS":    "max_debate_rounds",
    "TRADINGAGENTS_MAX_RISK_ROUNDS":      "max_risk_discuss_rounds",
    "TRADINGAGENTS_CHECKPOINT_ENABLED":   "checkpoint_enabled",
    "TRADINGAGENTS_BENCHMARK_TICKER":     "benchmark_ticker",
    # Master methodology overrides — set TRADINGAGENTS_MASTER_<ROLE> to a
    # master_id (e.g. "buffett", "graham", "soros") to inject that master's
    # methodology into the corresponding agent's prompt.  "default" = no
    # injection (use original prompt).
    "TRADINGAGENTS_MASTER_BULL":          "master_bull",
    "TRADINGAGENTS_MASTER_BEAR":          "master_bear",
    "TRADINGAGENTS_MASTER_AGGRESSIVE":    "master_aggressive",
    "TRADINGAGENTS_MASTER_CONSERVATIVE":  "master_conservative",
    "TRADINGAGENTS_MASTER_NEUTRAL":       "master_neutral",
    "TRADINGAGENTS_MASTER_TRADER":        "master_trader",
    "TRADINGAGENTS_MASTER_PM":            "master_pm",
    "TRADINGAGENTS_MASTER_RM":            "master_rm",
    "TRADINGAGENTS_MASTER_FUNDAMENTALS":  "master_fundamentals",
    "TRADINGAGENTS_MASTER_MARKET":        "master_market",
    "TRADINGAGENTS_MASTER_NEWS":          "master_news",
    "TRADINGAGENTS_MASTER_SENTIMENT":     "master_sentiment",
}


def _coerce(value: str, reference):
    """Coerce env-var string to the type of the existing default value."""
    if isinstance(reference, bool):
        return value.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(config: dict) -> dict:
    """Apply TRADINGAGENTS_* env vars to the config dict in-place."""
    for env_var, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        config[key] = _coerce(raw, config.get(key))
    return config


DEFAULT_CONFIG = _apply_env_overrides({
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", os.path.join(_TRADINGAGENTS_HOME, "logs")),
    "data_cache_dir": os.getenv("TRADINGAGENTS_CACHE_DIR", os.path.join(_TRADINGAGENTS_HOME, "cache")),
    "memory_log_path": os.getenv("TRADINGAGENTS_MEMORY_LOG_PATH", os.path.join(_TRADINGAGENTS_HOME, "memory", "trading_memory.md")),
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings
    "llm_provider": "openai",
    "deep_think_llm": "gpt-5.4",
    "quick_think_llm": "gpt-5.4-mini",
    # When None, each provider's client falls back to its own default endpoint
    # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
    # The CLI overrides this per provider when the user picks one. Keeping a
    # provider-specific URL here would leak (e.g. OpenAI's /v1 was previously
    # being forwarded to Gemini, producing malformed request URLs).
    "backend_url": None,
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "English",
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    "analyst_concurrency_limit": 1,
    # News / data fetching parameters
    # Increase for longer lookback strategies or to broaden macro coverage;
    # decrease to reduce token usage in agent prompts.
    "news_article_limit": 20,             # max articles per ticker (ticker-news)
    "global_news_article_limit": 10,      # max articles for global/macro news
    "global_news_lookback_days": 7,       # macro news lookback window
    # Search queries used by get_global_news for macro headlines. Extend or
    # replace to broaden geographic / sector coverage.
    "global_news_queries": [
        "Federal Reserve interest rates inflation",
        "S&P 500 earnings GDP economic outlook",
        "geopolitical risk trade war sanctions",
        "ECB Bank of England BOJ central bank policy",
        "oil commodities supply chain energy",
    ],
    # Data vendor configuration
    # Category-level configuration (default for all tools in category)
    "data_vendors": {
        "core_stock_apis": "akshare",        # Options: akshare, alpha_vantage, yfinance
        "technical_indicators": "akshare",   # Options: akshare, alpha_vantage, yfinance
        "fundamental_data": "akshare",      # Options: akshare, alpha_vantage, yfinance
        "news_data": "akshare,yfinance",    # Primary: akshare, Fallback: yfinance
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
    # Benchmark for alpha calculation in the reflection layer.
    # ``benchmark_ticker`` (when set) overrides the suffix map for all
    # tickers; leave it None to use ``benchmark_map`` for auto-detection
    # based on the ticker's exchange suffix. SPY remains the US default
    # so the reflection label keeps reading "Alpha vs SPY" for US tickers
    # while non-US tickers get their regional index automatically.
    "benchmark_ticker": None,
    "benchmark_map": {
        # Chinese A-share suffixes (6-digit codes auto-detected by _is_ashare)
        ".SH":  "000001",   # Shanghai Composite (上证指数)
        ".SZ":  "399001",   # Shenzhen Component (深证成指)
        ".SS":  "000001",   # Shanghai Composite alias
        # Fallback for A-share 6-digit tickers (used when no suffix matches)
        "_ASHARE_DEFAULT": "000001",
        # International markets
        ".NS":  "^NSEI",    # NSE India (Nifty 50)
        ".BO":  "^BSESN",   # BSE India (Sensex)
        ".T":   "^N225",    # Tokyo (Nikkei 225)
        ".HK":  "^HSI",     # Hong Kong (Hang Seng)
        ".L":   "^FTSE",    # London (FTSE 100)
        ".TO":  "^GSPTSE",  # Toronto (TSX Composite)
        ".AX":  "^AXJO",    # Australia (ASX 200)
        "":     "SPY",      # default for US-listed tickers (no suffix)
    },
    # ── Investment Master Methodology Configuration ──
    # Each key maps a role to a master_id.  "default" = no injection.
    # Available masters (see tradingagents/masters/yaml/*.yaml):
    #   buffett, graham, soros, marks, dalio, lynch, munger,
    #   taleb, bury, klarman, ptj
    # Example: {"bull_researcher": "buffett"} injects Buffett's value/moat
    # methodology into the Bull Researcher's prompt.
    "master_config": {
        "bull_researcher":      "default",   # Options: buffett, lynch, soros
        "bear_researcher":      "default",   # Options: graham, marks, taleb, bury, klarman
        "aggressive_debator":   "default",   # Options: soros, lynch, ptj
        "conservative_debator": "default",   # Options: graham, marks, klarman
        "neutral_debator":      "default",   # Options: dalio, munger, marks
        "trader":               "default",   # Options: ptj, livermore, raschke
        "portfolio_manager":    "default",   # Options: dalio, buffett, marks, ptj
        "research_manager":     "default",   # Options: munger, marks, dalio, bogle
        "fundamentals_analyst": "default",   # Options: buffett, graham, fisher, lynch
        "market_analyst":       "default",   # Options: livermore, ptj, raschke, dalio
        "news_analyst":         "default",   # Options: soros, ackman, wood, tepper
        "sentiment_analyst":    "default",   # Options: soros
    },
    # ── Custom Theory Injection (per role) ──
    # Same "角色定义 + {自定义理论}" contract as master_config, but the slot is
    # filled with the USER'S OWN free-text investment thesis instead of a preset
    # master.  A non-empty entry here OVERRIDES master_config for that role.
    # Example: {"bull_researcher": "我认为只看自由现金流和分红率，忽略市盈率……"}
    # Leave empty ("") to fall back to master_config / no injection.
    "custom_theory_config": {
        "bull_researcher":      "",
        "bear_researcher":      "",
        "aggressive_debator":   "",
        "conservative_debator": "",
        "neutral_debator":      "",
        "trader":               "",
        "portfolio_manager":    "",
        "research_manager":     "",
        "fundamentals_analyst": "",
        "market_analyst":       "",
        "news_analyst":         "",
        "sentiment_analyst":    "",
    },
})
