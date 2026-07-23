# TradingAgents/graph/numeric_validation.py

"""Post-hoc numeric integrity check nodes for the LangGraph pipeline.

These are pure-function graph nodes (state → state) that:
1. **inject_fundamentals_structured** — After the fundamentals analyst finishes,
   capture ``get_fundamentals_structured()`` into ``state["fundamentals_structured"]``
   so downstream nodes can reference structured source-of-truth data.

2. **numeric_check_node** — After any agent that produces numeric claims
   (bull/bear/debators), extract quantified claims via
   ``extract_numeric_claims()``, cross-check against
   ``state["fundamentals_structured"]``, and append violations to
   ``state["numeric_violations"]``.
"""

from __future__ import annotations

import logging
from typing import Dict, Any

from tradingagents.agents.utils.agent_states import AgentState

logger = logging.getLogger(__name__)


def inject_fundamentals_structured(state: AgentState) -> dict:
    """Populate state['fundamentals_structured'] from the data-layer cache.

    This node runs AFTER the fundamentals analyst and BEFORE Bull/Bear
    researchers, so they (and the numeric guard) have access to
    structured source-of-truth data.
    """
    try:
        from tradingagents.dataflows.akshare_data import get_fundamentals_structured
        # 按 ticker 键控读取，避免并发会话下拿到其他标的的数据
        ticker = state.get("company_of_interest", "")
        structured = get_fundamentals_structured(ticker or None)
    except Exception as e:
        logger.warning("Could not load fundamentals structured data: %s", e)
        structured = {}

    if not structured:
        logger.info("No structured fundamentals data available for numeric guard")

    return {"fundamentals_structured": structured}


def numeric_check_bull(state: AgentState) -> dict:
    """Run post-hoc numeric check on Bull Researcher's latest output.

    Extracts numeric claims from bull_history/current_response,
    cross-checks against fundamentals_structured, AND detects
    cross-period-type (年报 vs 季报) comparison errors.
    """
    from tradingagents.agents.utils.numeric_guard import (
        extract_numeric_claims,
        cross_check_claims,
        detect_cross_period_type_comparison,
    )

    text = _get_latest_debate_text(state, side="bull")
    source = state.get("fundamentals_structured") or {}
    existing = state.get("numeric_violations") or []

    if not text.strip():
        return {}

    all_violations = list(existing)

    # Layer 2a: Value-deviation checks
    if source:
        claims = extract_numeric_claims(text)
        value_vios = cross_check_claims(claims, source)
        if value_vios:
            tagged = [_tag_violation(v, node="Bull Researcher") for v in value_vios]
            logger.warning(
                "Numeric guard [Bull]: %d value deviation(s) detected",
                len(tagged),
            )
            all_violations.extend(tagged)

    # Layer 2b: Cross-period-type detection (works even without source data)
    cross_type_vios = detect_cross_period_type_comparison(text)
    if cross_type_vios:
        tagged = [_tag_violation(v, node="Bull Researcher") for v in cross_type_vios]
        logger.warning(
            "Numeric guard [Bull]: %d cross-period-type violation(s) detected (CRITICAL)",
            len(tagged),
        )
        all_violations.extend(tagged)

    if all_violations != existing:
        return {"numeric_violations": all_violations}

    logger.debug("Numeric guard [Bull]: no violations found")
    return {}


def numeric_check_bear(state: AgentState) -> dict:
    """Run post-hoc numeric check on Bear Researcher's latest output.

    Includes both value-deviation checks and cross-period-type detection.
    """
    from tradingagents.agents.utils.numeric_guard import (
        extract_numeric_claims,
        cross_check_claims,
        detect_cross_period_type_comparison,
    )

    text = _get_latest_debate_text(state, side="bear")
    source = state.get("fundamentals_structured") or {}
    existing = state.get("numeric_violations") or []

    if not text.strip():
        return {}

    all_violations = list(existing)

    # Layer 2a: Value-deviation checks
    if source:
        claims = extract_numeric_claims(text)
        value_vios = cross_check_claims(claims, source)
        if value_vios:
            tagged = [_tag_violation(v, node="Bear Researcher") for v in value_vios]
            logger.warning(
                "Numeric guard [Bear]: %d value deviation(s) detected",
                len(tagged),
            )
            all_violations.extend(tagged)

    # Layer 2b: Cross-period-type detection
    cross_type_vios = detect_cross_period_type_comparison(text)
    if cross_type_vios:
        tagged = [_tag_violation(v, node="Bear Researcher") for v in cross_type_vios]
        logger.warning(
            "Numeric guard [Bear]: %d cross-period-type violation(s) detected (CRITICAL)",
            len(tagged),
        )
        all_violations.extend(tagged)

    if all_violations != existing:
        return {"numeric_violations": all_violations}

    logger.debug("Numeric guard [Bear]: no violations found")
    return {}


def numeric_check_risk(state: AgentState) -> dict:
    """Run post-hoc numeric check on risk debator outputs.

    Checks aggressive/conservative/neutral responses for numeric violations
    AND cross-period-type (年报 vs 季报) comparison errors.
    """
    from tradingagents.agents.utils.numeric_guard import (
        extract_numeric_claims,
        cross_check_claims,
        detect_cross_period_type_comparison,
    )

    source = state.get("fundamentals_structured") or {}
    existing = state.get("numeric_violations") or []

    if not source:
        # Still run cross-type detection even without source data
        risk_state = state.get("risk_debate_state") or {}
        all_violations = list(existing)
        for side in ("aggressive", "conservative", "neutral"):
            key = f"current_{side}_response"
            text = risk_state.get(key, "")
            if text.strip():
                cross_type_vios = detect_cross_period_type_comparison(text)
                if cross_type_vios:
                    tagged = [_tag_violation(v, node=f"{side.title()} Analyst") for v in cross_type_vios]
                    logger.warning(
                        "Numeric guard [%s]: %d cross-period-type violation(s) detected (CRITICAL)",
                        side.title(), len(tagged),
                    )
                    all_violations.extend(tagged)
        if all_violations != existing:
            return {"numeric_violations": all_violations}
        return {}

    risk_state = state.get("risk_debate_state") or {}
    all_violations = list(existing)

    for side in ("aggressive", "conservative", "neutral"):
        key = f"current_{side}_response"
        text = risk_state.get(key, "")
        if not text.strip():
            continue

        # Layer 2a: Value-deviation checks
        claims = extract_numeric_claims(text)
        vios = cross_check_claims(claims, source)
        if vios:
            tagged = [_tag_violation(v, node=f"{side.title()} Analyst") for v in vios]
            logger.warning(
                "Numeric guard [%s]: %d value deviation(s) detected",
                side.title(), len(tagged),
            )
            all_violations.extend(tagged)

        # Layer 2b: Cross-period-type detection
        cross_type_vios = detect_cross_period_type_comparison(text)
        if cross_type_vios:
            tagged = [_tag_violation(v, node=f"{side.title()} Analyst") for v in cross_type_vios]
            logger.warning(
                "Numeric guard [%s]: %d cross-period-type violation(s) detected (CRITICAL)",
                side.title(), len(tagged),
            )
            all_violations.extend(tagged)

    if all_violations != existing:
        return {"numeric_violations": all_violations}
    return {}


# ── Internal helpers ──────────────────────────────────────────────


def _get_latest_debate_text(state: AgentState, side: str) -> str:
    """Extract the most recent response text from debate state.

    Prefers 'current_response' (latest turn), falls back to 'history'
    tail (accumulated conversation).
    """
    debate = state.get("investment_debate_state") or {}
    if not debate:
        return ""

    # current_response is the single latest AI message
    current = debate.get("current_response", "")
    if current and current.strip():
        return current

    # Fallback: use the side-specific history
    side_key = f"{side}_history"
    history = debate.get(side_key, "")
    if history and history.strip():
        # Return last 2000 chars to focus on recent content
        return history[-2000:]

    # Last resort: general history
    hist = debate.get("history", "")
    return hist[-2000:] if hist else ""


def _tag_violation(violation: Dict[str, Any], node: str) -> Dict[str, Any]:
    """Tag a violation with its source node name."""
    result = dict(violation)
    result["source_node"] = node
    return result
