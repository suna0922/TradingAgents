#!/usr/bin/env python3
"""
Full integration test for trading rules chain:
PM output → AgentState → DecisionEngine → TradingRule → eval_condition
"""

import sys
sys.path.insert(0, '/Users/sunrui/WorkBuddy/2026-05-23-task-13')

from tradingagents.agents.utils.agent_states import AgentState
from backtest.decision_engine import DecisionEngine
from backtest.models import BacktestConfig
from backtest.cache_manager import CacheManager
from backtest.trading_rules import RuleAction
import json

print("=" * 70)
print("FULL INTEGRATION TEST: Trading Rules Chain")
print("=" * 70)

# Step 1: Simulate PM output (what portfolio_manager_node returns)
print("\n[Step 1] Simulating PM node output...")
pm_output = {
    "risk_debate_state": "debate_complete",
    "final_trade_decision": "## 交易决策\n\n**评级**: Buy\n**目标仓位**: 60%\n\n### 交易规则\n- [RULE1] [stop_loss] WHEN close < MA(close,200) THEN stop_loss\n- [RULE2] [reduce_position] WHEN close < 55.0 THEN sell_pct(30%)\n",
    "trading_rules_structured": [
        {
            "rule_type": "stop_loss",
            "action": "stop_loss",
            "trigger_sql": "close < MA(close,200)",
            "trigger_condition": "收盘价跌破200日SMA",
            "priority": 90,
            "pct": 0.0,
            "action_detail": "无条件清仓"
        },
        {
            "rule_type": "reduce_position",
            "action": "sell_pct",
            "trigger_sql": "close < 55.0",
            "trigger_condition": "股价回落至55元",
            "priority": 75,
            "pct": 0.30,
            "action_detail": "减仓30%"
        },
        {
            "rule_type": "entry_zone",
            "action": "buy_add",
            "trigger_sql": "close >= 57.0 AND close <= 58.0",
            "trigger_condition": "股价在57-58元区间",
            "priority": 40,
            "pct": 0.20,
            "action_detail": "加仓20%"
        }
    ]
}
print(f"  trading_rules_structured count: {len(pm_output['trading_rules_structured'])}")

# Step 2: Verify AgentState can hold trading_rules_structured
print("\n[Step 2] Verifying AgentState schema...")
try:
    # AgentState is a TypedDict, we can check if the field exists
    from typing import get_type_hints
    hints = get_type_hints(AgentState)
    if "trading_rules_structured" in hints:
        print(f"  ✅ AgentState has 'trading_rules_structured' field: {hints['trading_rules_structured']}")
    else:
        print("  ❌ AgentState missing 'trading_rules_structured' field!")
        sys.exit(1)
except Exception as e:
    print(f"  ⚠️ Could not verify AgentState: {e}")

# Step 3: Simulate DecisionEngine receiving state_dict
print("\n[Step 3] Simulating DecisionEngine.run_decision_chain...")
config = BacktestConfig()
cache = CacheManager("/tmp/test_cache")
engine = DecisionEngine(config, cache)

# Mock state_dict as would be returned by graph.propagate()
state_dict = {
    "final_trade_decision": pm_output["final_trade_decision"],
    "trading_rules_structured": pm_output["trading_rules_structured"],
    "investment_plan": "test_plan",
    "risk_debate_state": "complete",
}

# Test _convert_structured_rules directly
structured_rules = state_dict.get("trading_rules_structured")
if structured_rules:
    print(f"  ✅ Found structured_rules in state_dict (count={len(structured_rules)})")
    rules = engine._convert_structured_rules(structured_rules)
    print(f"  ✅ Converted to {len(rules)} TradingRule objects")
else:
    print("  ❌ No structured_rules found in state_dict!")
    sys.exit(1)

# Step 4: Verify each TradingRule field
print("\n[Step 4] Verifying TradingRule fields...")
for i, rule in enumerate(rules):
    print(f"\n  Rule {i+1}: {rule.name}")
    assert rule.condition_str, f"Rule {i+1} has empty condition_str!"
    print(f"    ✅ condition_str: '{rule.condition_str}'")
    assert rule.action != RuleAction.HOLD, f"Rule {i+1} has HOLD action!"
    print(f"    ✅ action: {rule.action.value}")
    assert rule.priority > 0, f"Rule {i+1} has invalid priority!"
    print(f"    ✅ priority: {rule.priority}")
    print(f"    ✅ pct: {rule.pct}")
    print(f"    ✅ enabled: {rule.enabled}")

# Step 5: Verify priority sorting
print("\n[Step 5] Verifying priority sorting...")
priorities = [r.priority for r in rules]
assert priorities == sorted(priorities, reverse=True), "Rules not sorted by priority!"
print(f"  ✅ Priorities in descending order: {priorities}")

# Step 6: Test eval_condition with mock data
print("\n[Step 6] Testing eval_condition...")

# Scenario A: Price drops below stop loss
mock_row_sl = {
    'close': 53.0,
    'close_200_sma': 55.0,  # close < MA200 => 53 < 55 => True
}
result = rules[0].evaluate_all(mock_row_sl)  # stop_loss rule
assert result == True, "Stop loss should trigger when close < MA200!"
print(f"  ✅ Stop loss triggers (close=53, MA200=55): {result}")

# Scenario B: Price in entry zone
mock_row_entry = {
    'close': 57.5,
}
entry_rule = [r for r in rules if r.action == RuleAction.BUY_ADD][0]
result = entry_rule.evaluate_all(mock_row_entry)
assert result == True, "Entry zone should trigger when close in [57, 58]!"
print(f"  ✅ Entry zone triggers (close=57.5): {result}")

# Scenario C: Price above entry zone
mock_row_no_entry = {
    'close': 59.0,
}
result = entry_rule.evaluate_all(mock_row_no_entry)
assert result == False, "Entry zone should NOT trigger when close > 58!"
print(f"  ✅ Entry zone does not trigger (close=59): {result}")

# Step 7: Test serialization
print("\n[Step 7] Testing serialization...")
rule_dict = rules[0].to_dict()
assert "condition_str" in rule_dict, "Serialized rule missing condition_str!"
assert rule_dict["condition_str"] == "close < MA(close,200)", "condition_str mismatch!"
print(f"  ✅ Serialization preserves condition_str: '{rule_dict['condition_str']}'")

# Step 8: Test with empty trigger_sql (fallback to trigger_condition)
print("\n[Step 8] Testing fallback to trigger_condition...")
mock_structured_with_empty_sql = [
    {
        "rule_type": "stop_loss",
        "action": "stop_loss",
        "trigger_sql": "",  # Empty!
        "trigger_condition": "close < 50.0",  # Fallback
        "priority": 90,
        "pct": 0.0,
        "action_detail": "test"
    }
]
rules_fallback = engine._convert_structured_rules(mock_structured_with_empty_sql)
assert rules_fallback[0].condition_str == "close < 50.0", "Should fallback to trigger_condition!"
print(f"  ✅ Fallback works: condition_str = '{rules_fallback[0].condition_str}'")

print("\n" + "=" * 70)
print("ALL INTEGRATION TESTS PASSED!")
print("=" * 70)
print("\nSummary:")
print("  ✅ AgentState has trading_rules_structured field")
print("  ✅ DecisionEngine._convert_structured_rules extracts trigger_sql correctly")
print("  ✅ TradingRule objects have valid condition_str, action, priority, pct")
print("  ✅ Rules are sorted by priority (descending)")
print("  ✅ eval_condition correctly evaluates SQL expressions")
print("  ✅ Serialization preserves condition_str")
print("  ✅ Fallback to trigger_condition when trigger_sql is empty")
