"""
Numeric hard-constraint guard for LLM agent outputs.

Problem:  LLM agents in the trading pipeline can fabricate, round, or
           "polish" financial numbers with high confidence.  Even when
          upstream data sources are correct, downstream agents (bull,
          bear, debators) may silently alter figures.

Solution:  Three-layer defence:

  Layer 1 – Prompt-level rules (in each agent's system prompt)
          → Already partially deployed as DATA CITATION RULES.
          → This module adds NUMERIC INTEGRITY PLEDGE snippets that
            agents include verbatim in their prompts.

  Layer 2 – Post-hoc numeric extraction & cross-check
          → ``extract_numeric_claims(text)`` pulls every quantified
            claim (amount + metric + period + value).
          → ``cross_check_claims(claims, source_data)`` flags claims
            whose values deviate from source data beyond a tolerance.
          → ``build_guard_prompt(violations)`` returns a correction
            prompt to feed back to the LLM.

 Layer 3 – Structured data pass-through
          → ``get_fundamentals()`` now returns BOTH free-text AND a
            structured dict that later agents can reference for
            verification.  The structured dict is stored in graph state
            as ``fundamentals_structured``.

Tolerance thresholds
--------------------
* Absolute monetary amounts (revenue, profit): ±2 %
* Percentages (growth rates, margins): ±1 pp
* Ratios (ROE, debt ratios): ±0.05

These are deliberately tight — the goal is to catch LLM
rounding/polishing, not genuine data-source errors.
"""

from __future__ import annotations

import re
import json
import logging
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# Prompt snippets — inserted into every downstream agent's prompt
# ══════════════════════════════════════════════════════════════════

NUMERIC_INTEGRITY_PLEDGE = """
**⛔ NUMERIC INTEGRITY RULES (ABSOLUTE, NON-NEGOTIABLE):**
1. **NEVER invent, round, or estimate financial numbers.** Every figure you write MUST appear VERBATIM in the Company Fundamentals Report or L1 analysis provided above. If the report says "155.52亿", you write "155.52亿" — NOT "156亿", NOT "about 155亿", NOT "~155.5亿".
2. **If you don't see a number in the source data, you CANNOT state it.** Write "数据中未提供" (not available in data) instead of guessing. It is better to omit a figure than to fabricate one.
3. **Percentage points are sacred.** If source says "+59.86%", you write "+59.86%" — NOT "60%", NOT "nearly 60%", NOT "approximately 59.9%".
4. **Cross-period arithmetic is FORBIDDEN.** Do NOT calculate YoY/QoQ growth on your own. Only use growth percentages EXPLICITLY provided in the source data.
5. **🚨 ANNUAL vs QUARTERLY cross-type comparison is FORBIDDEN (CRITICAL).** Annual reports (年报) contain 12-month cumulative values; quarterly reports (季报) contain 3-month single-quarter values. They have DIFFERENT magnitudes and MUST NOT be directly compared numerically. Specifically:
   - ❌ FORBIDDEN: "OCF/净利润从0.66倍(年报2025)跌至-0.40倍(季报2026Q1)" — comparing annual value to quarterly value
   - ❌ FORBIDDEN: "营业周期从91天(年报2025)拉长至292天(季报2026Q1)" — same error
   - ❌ FORBIDDEN: Labeling cross-type changes as "恶化/改善/deterioration/improvement"
   - ✅ CORRECT: "季报OCF/净利润转负(-0.40倍, 2026Q1)，而近四季趋势为1.28→1.40→2.64→-0.40，Q1呈现断崖式转负"
   - ✅ CORRECT: Compare annual↔annual (YoY) or quarterly↔quarterly (QoQ/YoY) ONLY
6. **When in doubt, quote the exact string from source.** Use format: `Source: "[exact text from fundamentals/L1 report]"` if you need to reference an unusual number.
7. **Violation = credibility destruction.** Fabricated numbers or invalid cross-period comparisons undermine the ENTIRE trading recommendation chain. Zero tolerance."""


def get_numeric_integrity_prompt() -> str:
    """Return the numeric integrity pledge for injection into agent prompts."""
    return NUMERIC_INTEGRITY_PLEDGE


# ══════════════════════════════════════════════════════════════════
# Layer 2: Post-hoc numeric extraction & cross-check
# ══════════════════════════════════════════════════════════════════

# Patterns to extract numeric claims from LLM output
# Matches: 营收155.52亿, 同比增长59.86%, ROE 9.77%, 毛利率11.37%
_NUMERIC_PATTERNS = [
    # Chinese: 数字+单位(亿/万/元/%) + 指标名 context
    re.compile(
        r'([\u4e00-\u9fff]{2,10})'       # metric name prefix (Chinese)
        r'[\s:：]*'
        r'([\d,]+\.?\d*)'                  # number
        r'\s*(亿|万元|元|%|pp|倍)?'         # unit
        r'(?:\s*[\(（](季报|年报)\d{4}Q?[1-4]?[）\)])?'  # optional period tag
        , re.UNICODE
    ),
    # English-style: MetricName: 155.52亿 (+59.86%)
    re.compile(
        r'([A-Za-z][A-Za-z\s]{2,20})'      # English metric name
        r'[:\s]+'
        r'([\d,]+\.?\d*)'
        r'\s*(亿|万元|元|%|pp|倍|B|M)?'
        , re.UNICODE
    ),
    # Growth rate pattern: 同比/环比 + [约/近/约] XX%
    re.compile(
        r'(同比|环比|年增|季增)[增长下降]?\s*[（:\s]*'
        r'(?:[约近约]?\s*)?'          # optional: 约, 近
        r'([\d,]+\.?\d*)\s*[%％]'
        , re.UNICODE
    ),
    # Pattern with Chinese modifier prefix: 约/接近/将近 + XX%
    re.compile(
        r'(约|接近|将近|超过)\s*([\d,]+\.?\d*)\s*[%％]'
        , re.UNICODE
    ),
]


def extract_numeric_claims(text: str) -> List[Dict[str, Any]]:
    """Extract all quantified claims from agent output text.

    Returns a list of dicts with keys:
      - raw_text: the matched substring
      - metric: inferred metric name
      - value: float value extracted
      - unit: 亿/%/倍/None
      - period: 季报/年报XXXXQX or None
    """
    claims = []
    seen = set()  # deduplicate by position

    for pat in _NUMERIC_PATTERNS:
        for m in pat.finditer(text):
            key = (m.start(), m.end())
            if key in seen:
                continue
            seen.add(key)

            groups = m.groups()
            claim = {
                'raw_text': m.group(0),
                'start': m.start(),
                'end': m.end(),
                'metric': groups[0].strip() if len(groups) > 0 else 'unknown',
                'value_str': groups[1] if len(groups) > 1 else '',
                'unit': groups[2] if len(groups) > 2 else None,
                'period': groups[3] if len(groups) > 3 else None,
            }
            try:
                claim['value'] = float(claim['value_str'].replace(',', ''))
            except (ValueError, TypeError):
                continue

            if claim['value'] > 0:  # skip zero/negative extractions (likely noise)
                claims.append(claim)

    return claims


def cross_check_claims(
    claims: List[Dict],
    source_data: Dict[str, Any],
    tolerance_pct: float = 2.0,
    tolerance_pp: float = 1.0,
    tolerance_ratio: float = 0.05,
) -> List[Dict]:
    """Cross-check extracted claims against source-of-truth data.

    Args:
        claims: Output from extract_numeric_claims().
        source_data: Dict from get_fundamentals_structured() or similar.
            Expected keys like 'total_revenue', 'revenue_growth_yoy', etc.
        tolerance_pct: For monetary amounts, allowed deviation %.
        tolerance_pp: For percentage metrics, allowed deviation in points.
        tolerance_ratio: For ratio metrics (ROE, etc.), allowed abs deviation.

    Returns:
        List of violation dicts with keys: claim, expected, actual, severity.
    """
    violations = []

    # Normalize source data keys for matching
    source_lower = {k.lower().replace(' ', '_'): v for k, v in source_data.items()}

    for claim in claims:
        metric_key = _match_metric_to_source(claim['metric'], source_lower)
        if metric_key is None:
            continue  # can't check this claim against available source

        expected_val = source_lower[metric_key]
        if expected_val is None or expected_val == '':
            continue

        try:
            expected = float(str(expected_val).replace('%', '').replace('亿', '').replace(',', '').strip())
        except (ValueError, TypeError):
            continue

        actual = claim['value']
        unit = claim.get('unit', '')

        # Determine appropriate tolerance based on unit type
        if unit in ('%', '%', 'pp'):
            diff = abs(actual - expected)
            allowed = tolerance_pp
            severity = 'HIGH' if diff > allowed * 2 else ('MEDIUM' if diff > allowed else 'LOW')
        elif unit in ('倍',):
            diff = abs(actual - expected)
            allowed = tolerance_ratio
            severity = 'HIGH' if diff > allowed * 3 else ('MEDIUM' if diff > allowed else 'LOW')
        else:  # monetary: 亿/万/元
            if expected != 0:
                rel_diff = abs(actual - expected) / abs(expected) * 100
            else:
                rel_diff = 100 if actual != 0 else 0
            diff = rel_diff
            allowed = tolerance_pct
            severity = 'HIGH' if diff > allowed * 2 else ('MEDIUM' if diff > allowed else 'LOW')

        if diff > allowed:
            violations.append({
                'claim': claim['raw_text'],
                'metric': claim['metric'],
                'expected': expected_val,
                'actual': claim['value_str'],
                'deviation': round(diff, 2) if isinstance(diff, float) else diff,
                'unit': unit,
                'severity': severity,
                'source_key': metric_key,
            })

    return violations


def _match_metric_to_source(metric_name: str, source_lower: Dict[str, Any]) -> Optional[str]:
    """Fuzzy-match a claimed metric name to a source data key.

    Uses keyword mapping for common Chinese/English financial terms.
    """
    mapping = {
        # Revenue
        '营收': 'total_revenue', '营业总收入': 'total_revenue',
        'revenue': 'total_revenue', '收入': 'total_revenue',
        '营业总收入同比增长': 'revenue_growth_yoy', '营收同比增长': 'revenue_growth_yoy',
        'rev_growth': 'revenue_growth_yoy',
        # Profit
        '净利': 'net_income', '净利润': 'net_income',
        '归母净利润': 'net_profit_parent', '归母净利': 'net_profit_parent',
        'net_profit': 'net_profit_parent', 'net_income': 'net_income',
        '净利润同比增长': 'net_income_growth_yoy', '净利同比': 'net_income_growth_yoy',
        '归母净利润同比增长': 'net_profit_growth_yoy',
        # Margins
        '毛利率': 'gross_margin', 'gross_margin': 'gross_margin',
        '净利率': 'net_margin', 'net_margin': 'net_margin',
        'roe': 'roe', '净资产收益率': 'roe',
        # Debt
        '资产负债率': 'debt_ratio', 'debt_ratio': 'debt_ratio',
        # EPS
        'eps': 'eps', '每股收益': 'eps',
    }

    name_lower = metric_name.lower().replace(' ', '_').replace('：', ':').strip()

    # Direct match
    if name_lower in source_lower:
        return name_lower

    # Keyword match
    for keyword, source_key in mapping.items():
        if keyword in name_lower or name_lower in keyword:
            if source_key in source_lower and source_lower[source_key] is not None:
                return source_key

    # Partial contains match
    for src_key in source_lower:
        if src_key in name_lower or name_lower in src_key:
            return src_key

    return None


def build_guard_prompt(violations: List[Dict]) -> str:
    """Build a correction prompt from detected violations.

    This can be fed back to the LLM to request corrections, or simply
    attached as a warning appendix to the final report.
    """
    if not violations:
        return ""

    lines = ["\n\n--- ⚠️ Numeric Integrity Check ---"]
    lines.append(f"The following {len(violations)} claim(s) deviate from source data:")

    for i, v in enumerate(violations, 1):
        emoji = "🔴" if v['severity'] == 'HIGH' else ("🟡" if v['severity'] == 'MEDIUM' else "🟢")
        lines.append(
            f"\n{i}. [{emoji}] Claim: \"{v['claim']}\"\n"
            f"   Source data ({v['source_key']}): {v['expected']}\n"
            f"   Your stated value: {v['actual']} | Deviation: {v['deviation']}{v['unit']}"
        )

    lines.append(
        "\nYou must correct these values to match the source data EXACTLY, "
        "or remove the claim if it cannot be verified."
    )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# Layer 2b: Cross-period-type comparison detection
# Detects invalid annual-vs-quarterly comparisons in agent output
# ══════════════════════════════════════════════════════════════════

# Patterns that indicate cross-period-type comparison errors
# These match sentences where an annual (年报) value and a quarterly (季报) value
# are compared using comparative language (从...到..., 跌至, 升至, 拉长, 缩短, etc.)

_CROSS_TYPE_PATTERNS = [
    # Pattern 1: "指标名 from XX(年报...) to YY(季报...)" style comparison
    re.compile(
        r'([\u4e00-\u9fff]{2,15})'                          # metric name
        r'\s*从\s*'
        r'[^\(（]*'                                          # skip to first paren
        r'[（(](年报)\d{4}[）)]'                             # (年报20xx)
        r'[^\d]*'
        r'[（(](季报)\d{4}Q?[1-4]?[）)]'                     # (季报20xxQx)
        , re.UNICODE
    ),
    # Pattern 2: reverse direction: from 季报 to 年报
    re.compile(
        r'([\u4e00-\u9fff]{2,15})'
        r'\s*从\s*'
        r'[^\(（]*'
        r'[（(](季报)\d{4}Q?[1-4]?[）)]'
        r'[^\d]*'
        r'[（(](年报)\d{4}[）)]'
        , re.UNICODE
    ),
    # Pattern 3: "XX(年报...) vs/比 YY(季报...)" with comparative verbs
    re.compile(
        r'([\u4e00-\u9fff]{2,15})[^\n]{0,30}'               # metric + short context
        r'[（(](年报)\d{4}[）)][^\n]{0,20}'                  # annual tag + context
        r'(跌至|升至|拉长|缩短|恶化|改善|下降|上升|回落|反弹|转正|转负|扩大|收窄)'
        r'[^\n]{0,20}'
        r'[（(](季报)\d{4}Q?[1-4]?[）)]'                      # quarterly tag
        , re.UNICODE
    ),
    # Pattern 4: reverse: 季报 → 年报 with comparative verb
    re.compile(
        r'([\u4e00-\u9fff]{2,15})[^\n]{0,30}'
        r'[（(](季报)\d{4}Q?[1-4]?[）)][^\n]{0,20}'
        r'(跌至|升至|拉长|缩短|恶化|改善|下降|上升|回落|反弹|转正|转负|扩大|收窄)'
        r'[^\n]{0,20}'
        r'[（(](年报)\d{4}[）)]'
        , re.UNICODE
    ),
]


def detect_cross_period_type_comparison(text: str) -> List[Dict[str, Any]]:
    """Detect invalid annual-vs-quarterly cross-period-type comparisons.

    Annual reports contain 12-month cumulative values; quarterly reports
    contain 3-month single-quarter values. Comparing them directly is
    semantically meaningless and MUST be flagged.

    Args:
        text: Agent output text to scan.

    Returns:
        List of violation dicts with keys:
          - raw_text: the matched substring
          - metric: the metric name involved
          - type_from: '年报' or '季报' (source period type)
          - type_to: '年报' or '季报' (target period type)
          - severity: always 'CRITICAL' for cross-type comparison
          - rule: which pattern matched
    """
    violations = []
    seen = set()

    for rule_idx, pat in enumerate(_CROSS_TYPE_PATTERNS):
        for m in pat.finditer(text):
            pos_key = (m.start(), m.end())
            if pos_key in seen:
                continue
            seen.add(pos_key)

            groups = m.groups()
            violations.append({
                'raw_text': m.group(0).strip(),
                'metric': groups[0] if len(groups) > 0 else 'unknown',
                'type_from': groups[1] if len(groups) > 1 else 'unknown',
                'type_to': groups[2] if len(groups) > 2 else 'unknown',
                'severity': 'CRITICAL',
                'rule': f'cross_type_pattern_{rule_idx + 1}',
                'start': m.start(),
                'end': m.end(),
            })

    return violations


def build_cross_type_warning(violations: List[Dict]) -> str:
    """Build a warning message for detected cross-period-type violations.

    This is designed to be appended to the numeric guard report or
    used as a standalone correction prompt.
    """
    if not violations:
        return ""

    lines = [
        "\n\n--- 🚨 CROSS-PERIOD-TYPE COMPARISON DETECTED (CRITICAL) ---",
        f"The following {len(violations)} claim(s) compare ANNUAL data (12-month "
        "cumulative) with QUARTERLY data (3-month single quarter). "
        "This comparison is INVALID because the values have different magnitudes:",
    ]

    for i, v in enumerate(violations, 1):
        lines.append(
            f"\n{i}. 🔴 [{v['severity']}] \"{v['raw_text']}\"\n"
            f"   Metric: {v['metric']} | Compares {v['type_from']} ↔ {v['type_to']}\n"
            f"   FIX: Compare same-type periods only (annual↔annual YoY or quarterly↔quarterly QoQ/YoY)"
        )

    lines.append(
        "\nYou MUST rewrite these claims to use same-period-type comparisons only. "
        "For trends across time, show each period type in its own sequence."
    )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# Layer 3: Structured data helpers for get_fundamentals()
# ══════════════════════════════════════════════════════════════════

def parse_fundamentals_text(text: str) -> Dict[str, Any]:
    """Parse the free-text output of get_fundamentals() into a structured dict.

    This allows downstream agents to do programmatic cross-checks
    without relying solely on prompt-based rules.
    
    Input format example:
        # Company Fundamentals for 000960 (via 同花顺)
        Total Revenue: 155.52亿
        Revenue Growth YoY: 59.86%
        Net Income: 8.68亿
        ...
    """
    result = {
        'ticker': '',
        'source': '',
        'report_date': '',
        'total_revenue': None,
        'revenue_growth_yoy': None,
        'net_income': None,
        'net_income_growth_yoy': None,
        'net_profit_parent': None,
        'net_profit_growth_yoy': None,
        'eps': None,
        'roe': None,
        'gross_margin': None,
        'net_margin': None,
        'debt_ratio': None,
        # Raw text for fallback
        '_raw_text': text,
    }

    for line in text.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            # Parse header
            if 'Fundamentals for' in line:
                parts = line.split('(')
                result['ticker'] = parts[0].split('for')[-1].strip() if parts else ''
                if len(parts) > 1:
                    src_raw = parts[1].rstrip(')').strip()
                    # Strip "via " prefix if present: "via 同花顺" → "同花顺"
                    if src_raw.lower().startswith('via '):
                        src_raw = src_raw[4:].strip()
                    result['source'] = src_raw
            continue

        if ':' not in line:
            continue

        key, _, val = line.partition(':')
        key = key.strip().lower().replace(' ', '_')
        val = val.strip()

        # Map to canonical keys
        key_map = {
            'total_revenue': 'total_revenue',
            'revenue_growth_yoy': 'revenue_growth_yoy',
            'revenue_growth_yoy%': 'revenue_growth_yoy',
            'net_income': 'net_income',
            'net_income_growth_yoy': 'net_income_growth_yoy',
            'net_income_growth_yoy%': 'net_income_growth_yoy',
            'eps': 'eps',
            'roe': 'roe',
            'gross_margin': 'gross_margin',
            'net_margin': 'net_margin',
            'debt-to-asset_ratio': 'debt_ratio',
            'debt_ratio': 'debt_ratio',
        }

        mapped = key_map.get(key)
        if mapped and val and val != 'nan':
            # Strip units for clean numeric storage
            clean_val = val.replace('亿', '').replace('%', '').replace(',', '').strip()
            try:
                result[mapped] = float(clean_val)
            except ValueError:
                result[mapped] = val  # store as string if not numeric

    return result
