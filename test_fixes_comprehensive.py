"""Comprehensive test for trading rules fixes.

Tests:
1. Parser extracts pct parameters correctly
2. Action enum consistency between schemas and trading_rules
3. RATING_REEVAL triggers alert_triggered
4. Execution engine handles all action types
"""

import sys
sys.path.insert(0, '/Users/sunrui/WorkBuddy/2026-05-23-task-13')

from backtest.trading_rules import RuleAction, TradingRule, RuleParser
from tradingagents.agents.schemas import RuleAction as SchemaRuleAction


def test_action_enum_consistency():
    """Verify schemas.RuleAction == trading_rules.RuleAction"""
    print("=" * 60)
    print("TEST 1: Action Enum Consistency")
    print("=" * 60)

    # Get all values from both enums
    schema_actions = {a.value for a in SchemaRuleAction}
    trading_actions = {a.value for a in RuleAction}

    if schema_actions == trading_actions:
        print("✅ PASS: Both enums have identical values")
        print(f"   Actions: {sorted(schema_actions)}")
    else:
        print("❌ FAIL: Enum mismatch!")
        print(f"   Only in schemas: {schema_actions - trading_actions}")
        print(f"   Only in trading_rules: {trading_actions - schema_actions}")
        return False
    return True


def test_parser_pct_extraction():
    """Test parser extracts pct from action(pct%) format"""
    print("\n" + "=" * 60)
    print("TEST 2: Parser PCT Parameter Extraction")
    print("=" * 60)

    parser = RuleParser()

    # Test cases with different formats
    test_cases = [
        # (input_text, expected_action, expected_pct)
        ("- [RULE1] [reduce_position] WHEN close < 50 THEN sell_pct(30%) (@ 50元)",
         RuleAction.SELL_PCT, 0.30),
        ("- [RULE2] [entry_zone] WHEN rsi < 30 THEN buy_add(20%) (@ N/A)",
         RuleAction.BUY_ADD, 0.20),
        ("- [RULE3] [stop_loss] WHEN low < 45 THEN stop_loss (@ 45元)",
         RuleAction.STOP_LOSS, 0.0),
        ("- [RULE4] [observation_anchor] WHEN close > 60 THEN alert_only (@ 60元)",
         RuleAction.ALERT_ONLY, 0.0),
        ("- [RULE5] [rating_reeval] WHEN close < 55 THEN rating_reeval (@ 55元)",
         RuleAction.RATING_REEVAL, 0.0),
        # Test with bullet prefix
        ("• [RULE6] [reduce_position] WHEN volume > 10000 THEN sell_pct(50%) — 减仓 (@ N/A)",
         RuleAction.SELL_PCT, 0.50),
    ]

    all_passed = True
    for text, expected_action, expected_pct in test_cases:
        rules = parser.parse(text)
        if not rules:
            print(f"❌ FAIL: No rules parsed from: {text[:50]}...")
            all_passed = False
            continue

        rule = rules[0]
        if rule.action != expected_action:
            print(f"❌ FAIL: Expected action {expected_action.value}, got {rule.action.value}")
            print(f"   Text: {text[:60]}...")
            all_passed = False
        elif abs(rule.pct - expected_pct) > 0.001:
            print(f"❌ FAIL: Expected pct {expected_pct}, got {rule.pct}")
            print(f"   Text: {text[:60]}...")
            all_passed = False
        else:
            print(f"✅ PASS: {rule.action.value}(pct={rule.pct:.0%})")

    return all_passed


def test_priority_mapping():
    """Test priority values match PM prompt recommendations"""
    print("\n" + "=" * 60)
    print("TEST 3: Priority Mapping Consistency")
    print("=" * 60)

    parser = RuleParser()

    test_cases = [
        ("- [RULE1] [stop_loss] WHEN close < 45 THEN stop_loss (@ 45元)", 90),
        ("- [RULE2] [take_profit] WHEN high > 60 THEN take_profit (@ 60元)", 85),
        ("- [RULE3] [reduce_position] WHEN close < 50 THEN sell_pct(30%) (@ 50元)", 75),
        ("- [RULE4] [downgrade] WHEN close < 52 THEN sell_pct(20%) (@ 52元)", 80),
        ("- [RULE5] [observation_anchor] WHEN close > 55 THEN alert_only (@ 55元)", 60),
        ("- [RULE6] [entry_zone] WHEN rsi < 30 THEN buy_add(20%) (@ N/A)", 40),
        ("- [RULE7] [rating_reeval] WHEN close < 48 THEN rating_reeval (@ 48元)", 70),
    ]

    all_passed = True
    for text, expected_priority in test_cases:
        rules = parser.parse(text)
        if not rules:
            print(f"❌ FAIL: No rules parsed")
            all_passed = False
            continue

        rule = rules[0]
        if rule.priority != expected_priority:
            print(f"❌ FAIL: Expected priority {expected_priority}, got {rule.priority}")
            print(f"   Rule type: {text[:40]}...")
            all_passed = False
        else:
            print(f"✅ PASS: priority={rule.priority} for {text[:40]}...")

    return all_passed


def test_action_mappings():
    """Test all action string mappings"""
    print("\n" + "=" * 60)
    print("TEST 4: Action String Mappings")
    print("=" * 60)

    parser = RuleParser()

    # Test all supported action strings
    action_tests = [
        ("stop_loss", RuleAction.STOP_LOSS),
        ("take_profit", RuleAction.TAKE_PROFIT),
        ("reduce_position", RuleAction.SELL_PCT),
        ("downgrade", RuleAction.SELL_PCT),
        ("sell_all", RuleAction.SELL_ALL),
        ("sell_half", RuleAction.SELL_PCT),
        ("sell_pct", RuleAction.SELL_PCT),
        ("buy_add", RuleAction.BUY_ADD),
        ("add_position", RuleAction.BUY_ADD),
        ("alert_only", RuleAction.ALERT_ONLY),
        ("rating_adjustment", RuleAction.RATING_REEVAL),
        ("rating_reeval", RuleAction.RATING_REEVAL),
        ("no_left_buy", RuleAction.NO_LEFT_BUY),
        ("circuit_break", RuleAction.CIRCUIT_BREAK),
        ("hold", RuleAction.HOLD),
    ]

    all_passed = True
    for action_str, expected in action_tests:
        text = f"- [RULE1] [test] WHEN close < 50 THEN {action_str} (@ 50元)"
        rules = parser.parse(text)
        if not rules:
            print(f"❌ FAIL: Could not parse action '{action_str}'")
            all_passed = False
            continue

        actual = rules[0].action
        if actual != expected:
            print(f"❌ FAIL: '{action_str}' → {actual.value} (expected {expected.value})")
            all_passed = False
        else:
            print(f"✅ PASS: '{action_str}' → {actual.value}")

    return all_passed


def test_render_pm_decision():
    """Test PM decision rendering includes pct in action"""
    print("\n" + "=" * 60)
    print("TEST 5: PM Decision Rendering")
    print("=" * 60)

    from tradingagents.agents.schemas import PortfolioDecision, TradingRuleItem, PortfolioRating

    decision = PortfolioDecision(
        rating=PortfolioRating.BUY,
        executive_summary="Test",
        investment_thesis="Test",
        trading_rules=[
            TradingRuleItem(
                rule_type="reduce_position",
                action=SchemaRuleAction.SELL_PCT,
                trigger_sql="close < 50",
                action_detail="降至30%仓位",
                priority=75,
            ),
            TradingRuleItem(
                rule_type="entry_zone",
                action=SchemaRuleAction.BUY_ADD,
                trigger_sql="rsi < 30",
                action_detail="加仓至20%",
                priority=40,
            ),
        ]
    )

    from tradingagents.agents.schemas import render_pm_decision
    output = render_pm_decision(decision)
    print("Rendered output:")
    for line in output.split('\n'):
        if 'RULE' in line or 'Trading' in line:
            print(f"  {line}")

    # Check that pct is included in output
    if "sell_pct(30%)" in output:
        print("✅ PASS: sell_pct includes 30% parameter")
    else:
        print("❌ FAIL: sell_pct missing 30% parameter")
        return False

    if "buy_add(20%)" in output:
        print("✅ PASS: buy_add includes 20% parameter")
    else:
        print("❌ FAIL: buy_add missing 20% parameter")
        return False

    return True


def test_execution_engine_rating_reeval():
    """Test ExecutionEngine handles RATING_REEVAL correctly"""
    print("\n" + "=" * 60)
    print("TEST 6: ExecutionEngine RATING_REEVAL Handling")
    print("=" * 60)

    from backtest.execution_engine import ExecutionEngine
    from backtest.models import (
        BacktestConfig, PortfolioState, WeeklyDecision, TradeDirection,
        PriceCondition, TechnicalTriggers, FundamentalGuards,
    )
    from backtest.data_layer import DataLayer
    import pandas as pd

    config = BacktestConfig(symbol="000423", start_date="2025-01-01", end_date="2025-03-01")
    dl = DataLayer(config, config.start_date, config.end_date)
    engine = ExecutionEngine(config, dl)

    # Create a mock decision with RATING_REEVAL rule
    rule = TradingRule(
        name="[rating_reeval] test",
        action=RuleAction.RATING_REEVAL,
        condition_str="close < 100",  # Will trigger
        priority=70,
    )

    decision = WeeklyDecision(
        direction=TradeDirection.HOLD,
        position_pct=-1,  # -1 means don't change position (not 0 which means liquidate)
        price_cond=PriceCondition(),
        technical_triggers=TechnicalTriggers(),
        fundamental_guards=FundamentalGuards(),
        decision_date="2025-01-15",
        signal_raw="Hold",
        pm_rating="Hold",
        pm_raw_output="",
        trading_rules=[rule],
    )

    # Create mock row data
    row = pd.Series({
        "date": "2025-01-15",
        "close": 50.0,  # Will trigger close < 100
        "high": 51.0,
        "low": 49.0,
        "open": 50.5,
        "volume": 10000,
        "pct_chg": 0.0,
    })

    portfolio = PortfolioState(cash=1000000, shares=1000)

    # Execute
    df = pd.DataFrame([row])
    daily_state = engine.execute(
        portfolio, decision, row, 0, df, fa_metrics={}
    )

    if daily_state.alert_triggered:
        print("✅ PASS: RATING_REEVAL sets alert_triggered=True")
    else:
        print("❌ FAIL: RATING_REEVAL did not set alert_triggered")
        return False

    if daily_state.action == "HOLD":
        print("✅ PASS: RATING_REEVAL results in HOLD action")
    else:
        print(f"❌ FAIL: RATING_REEVAL resulted in {daily_state.action} instead of HOLD")
        return False

    return True


def test_pct_symbol_removal():
    """Test eval_condition removes % symbol correctly"""
    print("\n" + "=" * 60)
    print("TEST 7: Percentage Symbol Removal in eval_condition")
    print("=" * 60)

    from backtest.trading_rules import eval_condition

    test_cases = [
        # (condition, row_data, expected_result)
        ("annual_dividend_payout > 50%", {"annual_dividend_payout": 60.0}, True),
        ("annual_dividend_payout > 50%", {"annual_dividend_payout": 40.0}, False),
        ("annual_debt_ratio < 70%", {"annual_debt_ratio": 65.0}, True),
        ("annual_debt_ratio < 70%", {"annual_debt_ratio": 75.0}, False),
        ("quarter_roe > 15% AND annual_gross_margin > 20%",
         {"quarter_roe": 18.0, "annual_gross_margin": 25.0}, True),
        ("annual_ocf_to_netprofit > 0.5",
         {"annual_ocf_to_netprofit": 0.6}, True),
    ]

    all_passed = True
    for condition, row, expected in test_cases:
        try:
            result = eval_condition(condition, row)
            if result == expected:
                print(f"✅ PASS: '{condition}' → {result}")
            else:
                print(f"❌ FAIL: '{condition}' → {result} (expected {expected})")
                all_passed = False
        except Exception as e:
            print(f"❌ FAIL: '{condition}' → ERROR: {e}")
            all_passed = False

    return all_passed


def test_ma_function_quoting():
    """Test MA(close,200) is correctly quoted to MA('close',200)"""
    print("\n" + "=" * 60)
    print("TEST 8: MA Function Argument Quoting")
    print("=" * 60)

    from backtest.trading_rules import eval_condition

    # Mock row with pre-computed MA values
    row = {
        "close": 50.0,
        "close_200_sma": 55.0,  # 200-day MA is above current price
        "close_50_sma": 52.0,
    }

    test_cases = [
        # (condition, expected_result)
        ("close < MA(close,200)", True),   # 50 < 55 → True
        ("close > MA(close,200)", False),  # 50 > 55 → False
        ("close < MA(close,50)", True),    # 50 < 52 → True
    ]

    all_passed = True
    for condition, expected in test_cases:
        try:
            result = eval_condition(condition, row)
            if result == expected:
                print(f"✅ PASS: '{condition}' → {result}")
            else:
                print(f"❌ FAIL: '{condition}' → {result} (expected {expected})")
                all_passed = False
        except Exception as e:
            print(f"❌ FAIL: '{condition}' → ERROR: {e}")
            all_passed = False

    return all_passed


def test_fundamental_metrics_sql():
    """Test fundamental metrics work in SQL expressions with annual/quarter prefixes"""
    print("\n" + "=" * 60)
    print("TEST 9: Fundamental Metrics SQL Expressions")
    print("=" * 60)

    from backtest.trading_rules import eval_condition

    # Mock row with fundamental metrics
    row = {
        "close": 50.0,
        "annual_roe": 22.5,
        "annual_gross_margin": 65.0,
        "annual_net_margin": 25.0,
        "annual_ocf_to_netprofit": 0.85,
        "annual_debt_ratio": 18.0,
        "annual_dividend_payout": 94.04,
        "annual_current_ratio": 3.5,
        "annual_interest_coverage": 15.0,
        "annual_cash_coverage": 45.0,
        "annual_revenue_growth": 12.0,
        "annual_profit_growth": 8.0,
        "quarter_roe": 5.5,
        "quarter_gross_margin": 62.0,
        "quarter_net_margin": 22.0,
        "quarter_ocf_to_netprofit": 0.75,
        "quarter_debt_ratio": 20.0,
        "quarter_dividend_payout": 0.0,  # 季报通常无分红
        "quarter_current_ratio": 3.2,
        "quarter_interest_coverage": 14.0,
    }

    test_cases = [
        # 年报指标
        ("annual_roe > 20", True),
        ("annual_roe < 15", False),
        ("annual_gross_margin > 60", True),
        ("annual_net_margin > 30", False),
        ("annual_ocf_to_netprofit > 0.8", True),
        ("annual_debt_ratio < 20", True),
        ("annual_dividend_payout > 90", True),
        ("annual_current_ratio > 3", True),
        ("annual_interest_coverage > 10", True),
        ("annual_cash_coverage > 40", True),
        ("annual_revenue_growth > 10", True),
        ("annual_profit_growth > 10", False),
        # 季报指标
        ("quarter_roe > 5", True),
        ("quarter_gross_margin > 60", True),
        ("quarter_net_margin < 20", False),
        ("quarter_ocf_to_netprofit > 0.7", True),
        ("quarter_debt_ratio < 25", True),
        # 组合条件
        ("annual_roe > 20 AND annual_debt_ratio < 25", True),
        ("annual_roe > 20 AND annual_debt_ratio < 15", False),
        ("quarter_roe > 5 OR annual_roe > 30", True),
        ("annual_dividend_payout > 90%", True),  # 带%符号
        ("annual_debt_ratio < 20%", True),  # 带%符号
        # 混合技术指标和基本面
        ("close < 60 AND annual_roe > 20", True),
        ("close > 100 AND annual_roe > 20", False),
    ]

    all_passed = True
    for condition, expected in test_cases:
        try:
            result = eval_condition(condition, row)
            if result == expected:
                print(f"✅ PASS: '{condition}' → {result}")
            else:
                print(f"❌ FAIL: '{condition}' → {result} (expected {expected})")
                all_passed = False
        except Exception as e:
            print(f"❌ FAIL: '{condition}' → ERROR: {e}")
            all_passed = False

    return all_passed


def test_chinese_alias_ordering():
    """Test Chinese alias replacement with prefix ordering"""
    print("\n" + "=" * 60)
    print("TEST 10: Chinese Alias Prefix Ordering")
    print("=" * 60)

    from backtest.trading_rules import eval_condition

    # Mock row with both annual and quarter data
    row = {
        "annual_roe": 22.5,
        "quarter_roe": 5.5,
        "annual_debt_ratio": 18.0,
        "quarter_debt_ratio": 20.0,
    }

    test_cases = [
        # 带年报前缀的应该正确映射到 annual_*
        ("年报ROE > 20", True),
        ("年报ROE < 15", False),
        ("年报2025ROE > 20", True),  # 带年份
        ("年报2025Q1ROE > 20", True),  # 带年份和季度
        # 带季报前缀的应该正确映射到 quarter_*
        ("季报ROE > 5", True),
        ("季报ROE < 3", False),
        ("季报2025Q1ROE > 5", True),
        # 不带前缀的应该映射到 annual_*（默认）
        ("ROE > 20", True),
        ("ROE < 15", False),
        # 组合条件
        ("年报ROE > 20 AND 季报ROE > 5", True),
        ("年报ROE > 20 AND 季报ROE < 3", False),
        # 负债率测试
        ("年报负债率 < 20", True),
        ("季报负债率 < 25", True),
        ("负债率 < 20", True),  # 默认年报
    ]

    all_passed = True
    for condition, expected in test_cases:
        try:
            result = eval_condition(condition, row)
            if result == expected:
                print(f"✅ PASS: '{condition}' → {result}")
            else:
                print(f"❌ FAIL: '{condition}' → {result} (expected {expected})")
                all_passed = False
        except Exception as e:
            print(f"❌ FAIL: '{condition}' → ERROR: {e}")
            all_passed = False

    return all_passed


def test_extract_pct_from_text():
    """Test _extract_pct_from_text helper"""
    print("\n" + "=" * 60)
    print("TEST 11: Extract PCT from Rule Text")
    print("=" * 60)

    from backtest.execution_engine import ExecutionEngine

    test_cases = [
        ("减仓30%", 0.30),
        ("降至30%仓位", 0.30),
        ("加仓20%", 0.20),
        ("建立40%底仓", 0.40),
        ("卖出50%", 0.50),
        ("减持25%持仓", 0.25),
        ("增至60%比例", 0.60),
        ("无条件清仓", None),  # 无百分比
        ("", None),  # 空字符串
    ]

    all_passed = True
    for text, expected in test_cases:
        result = ExecutionEngine._extract_pct_from_text(text)
        if result == expected:
            print(f"✅ PASS: '{text}' → {result}")
        else:
            print(f"❌ FAIL: '{text}' → {result} (expected {expected})")
            all_passed = False

    return all_passed


def main():
    print("\n" + "=" * 60)
    print("COMPREHENSIVE TRADING RULES FIX VALIDATION")
    print("=" * 60)

    results = []
    results.append(("Action Enum Consistency", test_action_enum_consistency()))
    results.append(("Parser PCT Extraction", test_parser_pct_extraction()))
    results.append(("Priority Mapping", test_priority_mapping()))
    results.append(("Action Mappings", test_action_mappings()))
    results.append(("PM Decision Rendering", test_render_pm_decision()))
    results.append(("ExecutionEngine RATING_REEVAL", test_execution_engine_rating_reeval()))
    results.append(("Percentage Symbol Removal", test_pct_symbol_removal()))
    results.append(("MA Function Quoting", test_ma_function_quoting()))
    results.append(("Fundamental Metrics SQL", test_fundamental_metrics_sql()))
    results.append(("Chinese Alias Ordering", test_chinese_alias_ordering()))
    results.append(("Extract PCT from Text", test_extract_pct_from_text()))

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_passed = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}: {name}")
        if not passed:
            all_passed = False

    if all_passed:
        print("\n🎉 ALL TESTS PASSED!")
    else:
        print("\n⚠️  SOME TESTS FAILED")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
