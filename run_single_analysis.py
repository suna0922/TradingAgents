#!/usr/bin/env python3
"""
Run a single analysis and save all intermediate artifacts for verification.
Usage: .venv/bin/python run_single_analysis.py --symbol 688795 --date 2025-01-02
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, '/Users/sunrui/WorkBuddy/2026-05-23-task-13')

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG


def main():
    parser = argparse.ArgumentParser(description="Run single analysis and save artifacts")
    parser.add_argument("--symbol", required=True, help="Stock symbol (e.g. 688795)")
    parser.add_argument("--date", required=True, help="Analysis date (YYYY-MM-DD)")
    parser.add_argument("--output-dir", default="backtest_results/single_analysis", help="Output directory")
    args = parser.parse_args()

    symbol = args.symbol
    date_str = args.date
    output_dir = Path(args.output_dir) / symbol / date_str
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Running analysis for {symbol} on {date_str}...")
    print(f"Output directory: {output_dir}")

    # Use same config as CLI (deepseek provider)
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "deepseek"
    config["deep_think_llm"] = "deepseek-reasoner"
    config["quick_think_llm"] = "deepseek-chat"
    # Load backend_url from env if available
    config["backend_url"] = os.environ.get("DEEPSEEK_BACKEND_URL", "https://api.deepseek.com")

    # Sync to global config so dataflows (akshare, etc.) use same paths
    from tradingagents.dataflows.config import set_config
    set_config(config)

    print(f"LLM provider: {config['llm_provider']}")
    print(f"Deep think LLM: {config['deep_think_llm']}")
    print(f"Quick think LLM: {config['quick_think_llm']}")
    print(f"Data cache dir: {config['data_cache_dir']}")

    # Validate date against stock listing date
    from tradingagents.dataflows.stockstats_utils import load_ohlcv
    import pandas as pd
    
    test_df = load_ohlcv(symbol, date_str)
    if test_df.empty:
        print(f"\n⚠️  Warning: No data for {symbol} on {date_str}")
        print(f"   This may be before the stock's listing date.")
        
        # Try to find listing date from cache
        import glob
        cache_files = glob.glob(os.path.join(config["data_cache_dir"], f"{symbol}-*.csv"))
        if cache_files:
            df = pd.read_csv(cache_files[0], on_bad_lines="skip", encoding="utf-8")
            if not df.empty:
                listing_date = df["Date"].min()
                print(f"   {symbol} listing date: {listing_date}")
                print(f"   Please use a date after {listing_date}")
        
        print("\n❌ Analysis aborted due to missing data.")
        return

    graph = TradingAgentsGraph(config=config)
    state, signal = graph.propagate(symbol, date_str)

    print(f"\n=== Signal: {signal} ===")

    # Save full state (serializable parts)
    save_state = {
        "signal": signal,
        "final_trade_decision": state.get("final_trade_decision", ""),
        "trading_rules_structured": state.get("trading_rules_structured", []),
        "investment_plan": state.get("investment_plan", ""),
        "risk_debate_state": state.get("risk_debate_state", ""),
    }

    state_file = output_dir / "state.json"
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(save_state, f, indent=2, ensure_ascii=False)
    print(f"\nState saved to: {state_file}")

    # Save trading_rules_structured separately for easy inspection
    tr_file = output_dir / "trading_rules_structured.json"
    with open(tr_file, 'w', encoding='utf-8') as f:
        json.dump(state.get("trading_rules_structured", []), f, indent=2, ensure_ascii=False)
    print(f"Trading rules saved to: {tr_file}")

    # Print summary
    tr = state.get("trading_rules_structured", [])
    print(f"\n=== Trading Rules Summary ({len(tr)} rules) ===")
    for i, rule in enumerate(tr, 1):
        print(f"\n  Rule {i}:")
        print(f"    rule_type: {rule.get('rule_type', 'N/A')}")
        print(f"    action: {rule.get('action', 'N/A')}")
        print(f"    trigger_sql: {rule.get('trigger_sql', 'N/A')}")
        print(f"    trigger_condition: {rule.get('trigger_condition', 'N/A')}")
        print(f"    priority: {rule.get('priority', 'N/A')}")
        print(f"    pct: {rule.get('pct', 'N/A')}")
        print(f"    action_detail: {rule.get('action_detail', 'N/A')}")

    print(f"\n=== Done ===")
    print(f"All artifacts saved to: {output_dir}")


if __name__ == "__main__":
    main()
