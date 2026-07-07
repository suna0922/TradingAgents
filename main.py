from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
import sys
from datetime import datetime

# DEFAULT_CONFIG already applies TRADINGAGENTS_* env-var overrides
# (llm_provider, deep_think_llm, quick_think_llm, backend_url, etc.),
# so users can switch models or endpoints purely via .env without
# editing this script. Override individual keys here only when you
# want a hard-coded value that should ignore the environment.
config = DEFAULT_CONFIG.copy()

# Initialize with custom config
ta = TradingAgentsGraph(debug=True, config=config)

# forward propagate — accepts command-line args or uses defaults
# Usage: python main.py [TICKER] [YYYY-MM-DD]
#   e.g. python main.py 600519 2025-12-01
#   e.g. python main.py 000858
#   e.g. python main.py              → defaults below
ticker = sys.argv[1] if len(sys.argv) > 1 else "600519"
analysis_date = sys.argv[2] if len(sys.argv) > 2 else "2025-12-01"

# ---- date validation (prevent future dates / look-ahead bias) ----
try:
    parsed = datetime.strptime(analysis_date, "%Y-%m-%d")
    if parsed.date() > datetime.now().date():
        print(f"Error: analysis date {analysis_date} is in the future. "
              f"Please use a date on or before {datetime.now().strftime('%Y-%m-%d')}.")
        sys.exit(1)
except ValueError:
    print(f"Error: invalid date format '{analysis_date}'. Please use YYYY-MM-DD.")
    sys.exit(1)

_, decision = ta.propagate(ticker, analysis_date)
print(decision)

# Memorize mistakes and reflect
# ta.reflect_and_remember(1000) # parameter is the position returns
