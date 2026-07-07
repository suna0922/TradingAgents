#!/usr/bin/env python3
"""End-to-end verification test for the 3-layer numeric integrity defence.

Tests:
  Layer 1: NUMERIC_INTEGRITY_PLEDGE loads correctly
  Layer 2: extract_numeric_claims() pulls Chinese financial claims
          cross_check_caims() flags out-of-tolerance values
          build_guard_prompt() produces readable correction prompts
  Layer 3: get_fundamentals_structured() returns structured data
  Graph integration: numeric_validation nodes are importable and callable

Run with: .venv/bin/python verify_numeric_guard.py
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tradingagents.agents.utils.numeric_guard import (
    NUMERIC_INTEGRITY_PLEDGE,
    get_numeric_integrity_prompt,
    extract_numeric_claims,
    cross_check_claims,
    build_guard_prompt,
    parse_fundamentals_text,
)
from tradingagents.graph.numeric_validation import (
    inject_fundamentals_structured,
    _get_latest_debate_text,
)


def test_layer1_pledge():
    """Layer 1: Pledge should contain all 6 rules."""
    print("=" * 60)
    print("TEST Layer 1: NUMERIC_INTEGRITY_PLEDGE")
    print("=" * 60)

    pledge = get_numeric_integrity_prompt()
    assert len(pledge) > 100, "Pledge too short"
    assert "NEVER invent" in pledge, "Missing rule 1"
    assert "VERBATIM" in pledge, "Missing verbatim keyword"
    assert "数据中未提供" in pledge, "Missing Chinese fallback instruction"
    assert "Zero tolerance" in pledge, "Missing zero tolerance"

    # Check rule count (numbered list 1-6)
    lines = [l for l in pledge.split('\n') if l.strip().startswith(('1.', '2.', '3.', '4.', '5.', '6.'))]
    assert len(lines) == 6, f"Expected 6 rules, got {len(lines)}"

    print(f"  ✅ Pledge length: {len(pledge)} chars")
    print(f"  ✅ All 6 rules present")
    return True


def test_layer2_extraction():
    """Layer 2a: Extract numeric claims from Chinese financial text."""
    print("=" * 60)
    print("TEST Layer 2a: extract_numeric_claims()")
    print("=" * 60)

    # Simulated Bull Researcher output with real 000960 data
    text = """
    锡业股份(000960)2026年一季报表现亮眼：
    - 营收155.52亿，同比增长59.86%
    - 归母净利润8.68亿，同比增长73.71%
    - 毛利率11.37%，净利率5.62%
    - ROE达到9.77%
    - 资产负债率47.35%
    """

    claims = extract_numeric_claims(text)
    print(f"  Extracted {len(claims)} claims:")

    # Should find at least: revenue, revenue growth, net profit, profit growth, gross margin, net margin, roe, debt_ratio
    metrics_found = {c['metric'] for c in claims}
    print(f"  Metrics found: {metrics_found}")

    assert len(claims) >= 6, f"Expected >=6 claims, got {len(claims)}"

    # Check specific claim
    revenue_claims = [c for c in claims if '营收' in c['metric']]
    assert len(revenue_claims) >= 1, "Should extract revenue claim"
    assert revenue_claims[0]['value'] == 155.52, f"Revenue value wrong: {revenue_claims[0]}"

    growth_claims = [c for c in claims if '同比' in c.get('raw_text', '') or '增长' in c.get('raw_text', '')]
    assert len(growth_claims) >= 1, "Should extract growth rate claim"

    print(f"  ✅ Extraction works correctly")
    for c in claims:
        print(f"     {c['metric']}: {c['value_str']} {c.get('unit', '')}")

    return True


def test_layer2_cross_check_ok():
    """Layer 2b: Cross-check should PASS when values match source data."""
    print("=" * 60)
    print("TEST Layer 2b: cross_check_claims() — NO violations expected")
    print("=" * 60)

    text = "营收155.52亿，同比增长59.86%，归母净利润8.68亿"
    claims = extract_numeric_claims(text)

    # Source data matching the text exactly (real 000960 Q1 data)
    source = {
        'ticker': '000960',
        'source': '同花顺',
        'total_revenue': 155.52,
        'revenue_growth_yoy': 59.86,
        'net_profit_parent': 8.68,
        'net_profit_growth_yoy': 73.71,
        'gross_margin': 11.37,
        'net_margin': 5.62,
        'roe': 9.77,
        'debt_ratio': 47.35,
        'eps': 0.25,
    }

    violations = cross_check_claims(claims, source)
    assert len(violations) == 0, f"Expected 0 violations, got {len(violations)}: {violations}"
    print(f"  ✅ All {len(claims)} claims passed validation")
    return True


def test_layer2_cross_check_violation():
    """Layer 2c: Cross-check should CATCH fabricated/rounded numbers."""
    print("=" * 60)
    print("TEST Layer 2c: cross_check_claims() — VIOLATIONS expected")
    print("=" * 60)

    # LLM output with typical hallucination patterns:
    # - Rounding 59.86% → 60%
    # - Inflating 155.52 → 160亿
    # - Fabricating a growth rate not in source
    text = """
    锡业股份季报数据显示：
    - 营收160亿（四舍五入后）
    - 同比增长约60%
    - 归母净利润9亿（向上取整）
    - 毛利率12%（估算）
    """
    claims = extract_numeric_claims(text)

    source = {
        'total_revenue': 155.52,
        'revenue_growth_yoy': 59.86,
        'net_profit_parent': 8.68,
        'gross_margin': 11.37,
        'net_margin': 5.62,
        'roe': 9.77,
        'debt_ratio': 47.35,
    }

    violations = cross_check_claims(claims, source)
    print(f"  Detected {len(violations)} violations:")
    for v in violations:
        print(f"    [{v['severity']}] {v['claim']} "
              f"(expected={v['expected']}, stated={v['actual']}, dev={v['deviation']})")

    assert len(violations) >= 2, f"Expected >=2 violations, got {len(violations)}"
    high_severity = [v for v in violations if v['severity'] == 'HIGH']
    medium_or_high = [v for v in violations if v['severity'] in ('HIGH', 'MEDIUM')]
    assert len(medium_or_high) >= 1, f"Expected >=1 MEDIUM+ severity violation, got severities: {[v['severity'] for v in violations]}"

    print(f"  ✅ Violation detection working correctly ({len(violations)} caught)")
    return True


def test_layer2_guard_prompt():
    """Layer 2d: Guard prompt should be human-readable and actionable."""
    print("=" * 60)
    print("TEST Layer 2d: build_guard_prompt()")
    print("=" * 60)

    violations = [
        {
            'claim': '营收160亿',
            'metric': '营收',
            'expected': 155.52,
            'actual': '160',
            'deviation': 2.89,
            'unit': '',
            'severity': 'HIGH',
            'source_key': 'total_revenue',
        },
        {
            'claim': '同比增长约60%',
            'metric': '同比增长',
            'expected': 59.86,
            'actual': '60',
            'deviation': 0.14,
            'unit': '%',
            'severity': 'LOW',
            'source_key': 'revenue_growth_yoy',
        },
    ]

    prompt = build_guard_prompt(violations)
    assert len(prompt) > 50, "Guard prompt too short"
    assert 'Numeric Integrity Check' in prompt, "Missing header"
    assert '160亿' in prompt, "Missing violation detail"
    assert 'correct' in prompt.lower() or '修正' in prompt or 'match' in prompt.lower(), \
        "Missing correction instruction"

    print(f"  ✅ Guard prompt generated ({len(prompt)} chars)")
    print(f"  --- Prompt preview ---")
    print(prompt[:300] + "..." if len(prompt) > 300 else prompt)
    return True


def test_layer3_structured_data():
    """Layer 3: parse_fundamentals_text should produce structured dict."""
    print("=" * 60)
    print("TEST Layer 3: parse_fundamentals_text()")
    print("=" * 60)

    text = """# Company Fundamentals for 000960 (via 同花顺)
Total Revenue: 155.52亿
Revenue Growth YoY: 59.86%
Net Income: 8.68亿
Net Income Growth YoY: 73.71%
EPS: 0.25
ROE: 9.77%
Gross Margin: 11.37%
Net Margin: 5.62%
Debt-to-Asset Ratio: 47.35%
"""

    parsed = parse_fundamentals_text(text)
    assert parsed['ticker'] == '000960', f"Ticker wrong: {parsed['ticker']}"
    assert parsed['source'] == '同花顺', f"Source wrong: {parsed['source']}"
    assert parsed['total_revenue'] == 155.52, f"Revenue wrong: {parsed['total_revenue']}"
    assert parsed['revenue_growth_yoy'] == 59.86, f"Growth wrong: {parsed['revenue_growth_yoy']}"
    assert parsed['debt_ratio'] == 47.35, f"Debt ratio wrong: {parsed['debt_ratio']}"

    non_none_keys = [k for k, v in parsed.items()
                     if k != '_raw_t' and v is not None and k != '_raw_text']
    print(f"  ✅ Parsed {len(non_none_keys)} structured fields:")
    for k in ['total_revenue', 'revenue_growth_yoy', 'net_income', 'eps', 'roe', 'gross_margin']:
        v = parsed.get(k)
        if v is not None:
            print(f"     {k} = {v}")
    return True


def test_graph_nodes_importable():
    """Graph integration: numeric_validation nodes should be importable."""
    print("=" * 60)
    print("TEST Graph Integration: node imports & call signature")
    print("=" * 60)

    from tradingagents.graph.numeric_validation import (
        inject_fundamentals_structured,
        numeric_check_bull,
        numeric_check_bear,
        numeric_check_risk,
    )

    # Test that they are callable
    assert callable(inject_fundamentals_structured), "inject not callable"
    assert callable(numeric_check_bull), "bull check not callable"
    assert callable(numeric_check_bear), "bear check not callable"
    assert callable(numeric_check_risk), "risk check not callable"

    # Test inject with empty state (should not crash)
    dummy_state = {'fundamentals_report': 'test'}
    result = inject_fundamentals_structured(dummy_state)
    assert 'fundamentals_structured' in result, "Missing key in result"

    # Test bull check with empty state (no structured data → should return {})
    debate_state = {
        'investment_debate_state': {
            'bull_history': 'test revenue 155.52亿',
            'current_response': '',
            'history': '',
            'bear_history': '',
            'count': 0,
        },
        'fundamentals_structured': {},
        'numeric_violations': [],
    }
    result = numeric_check_bull(debate_state)
    # Empty source → no check → empty dict
    assert isinstance(result, dict), f"Result should be dict, got {type(result)}"

    print(f"  ✅ All 4 graph nodes importable and callable")
    return True


def test_debate_text_extraction():
    """Test _get_latest_debate_text helper."""
    print("=" * 60)
    print("TEST Helper: _get_latest_debate_text()")
    print("=" * 60)

    # Test with current_response populated
    state = {
        'investment_debate_state': {
            'current_response': '最新看多观点：营收155.52亿，增长59.86%',
            'bull_history': '旧的历史内容...',
            'history': '通用历史...',
            'bear_history': '',
            'count': 2,
        }
    }
    text = _get_latest_debate_text(state, side='bull')
    assert '155.52' in text, f"Should find current_response content, got: {text}"
    print(f"  ✅ current_response extraction works")

    # Test fallback to history
    state2 = {
        'investment_debate_state': {
            'current_response': '',
            'bull_history': 'Bull says: 营收155.52亿, 净利润8.68亿',
            'history': '',
            'bear_history': '',
            'count': 1,
        }
    }
    text2 = _get_latest_debate_text(state2, side='bull')
    assert '155.52' in text2, f"Should fall back to bull_history, got: {text2}"
    print(f"  ✅ bull_history fallback works")
    return True


def run_all_tests():
    """Run all tests and report results."""
    tests = [
        ("Layer 1 — Pledge", test_layer1_pledge),
        ("Layer 2a — Extraction", test_layer2_extraction),
        ("Layer 2b — Cross-check OK", test_layer2_cross_check_ok),
        ("Layer 2c — Cross-check VIOLATION", test_layer2_cross_check_violation),
        ("Layer 2d — Guard prompt", test_layer2_guard_prompt),
        ("Layer 3 — Structured parse", test_layer3_structured_data),
        ("Graph — Node imports", test_graph_nodes_importable),
        ("Helper — Debate text", test_debate_text_extraction),
    ]

    results = []
    for name, fn in tests:
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:
            results.append((name, "FAIL", str(e)))

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, s, _ in results if s == "PASS")
    failed = sum(1 for _, s, _ in results if s == "FAIL")

    for name, status, err in results:
        icon = "✅" if status == "PASS" else "❌"
        print(f"  {icon} {name}")
        if err:
            print(f"     Error: {err}")

    print(f"\nTotal: {passed}/{len(results)} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    ok = run_all_tests()
    sys.exit(0 if ok else 1)
