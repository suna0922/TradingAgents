"""Master methodology loader — reads YAML definitions and formats prompt snippets.

Design:
    - Each master is a YAML file under ``tradingagents/masters/yaml/``
    - ``default_config.py`` has a ``master_config`` dict mapping role → master_id
    - ``get_master_methodology(role)`` reads config, loads YAML, formats snippet
    - ``get_master_methodology(role, master_id)`` overrides config explicitly
    - The returned string is ready to concatenate into any agent prompt

Prompt injection convention:
    - f-string agents (Bull, Bear, Debators, PM):  insert after {instrument_context}
    - ChatPromptTemplate agents (Analysts):        append to system_message
    - Message-list agents (Trader):                append to system content
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_MASTERS_DIR = Path(__file__).parent / "yaml"

# ── Role → valid master-id mapping (populated from YAML files) ──
MASTER_REGISTRY: dict[str, list[str]] = {}

# ── Loaded YAML cache ──
_MASTER_CACHE: dict[str, dict] = {}


def _discover_masters() -> None:
    """Scan yaml/ directory and populate MASTER_REGISTRY."""
    if not _MASTERS_DIR.exists():
        logger.warning(f"[Masters] YAML directory not found: {_MASTERS_DIR}")
        return

    for yaml_file in sorted(_MASTERS_DIR.glob("*.yaml")):
        master_id = yaml_file.stem  # e.g. "buffett", "graham"
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"[Masters] Failed to load {yaml_file}: {e}")
            continue

        compatible_roles = data.get("compatible_roles", [])
        for role in compatible_roles:
            MASTER_REGISTRY.setdefault(role, []).append(master_id)

        # Cache the raw YAML data
        _MASTER_CACHE[master_id] = data

    logger.info(f"[Masters] Discovered {len(_MASTER_CACHE)} masters, "
                f"{len(MASTER_REGISTRY)} role mappings")


def _load_master_yaml(master_id: str) -> dict:
    """Load a single master's YAML definition (cached)."""
    if not _MASTER_CACHE:
        _discover_masters()

    if master_id in _MASTER_CACHE:
        return _MASTER_CACHE[master_id]

    # Try loading from file if not in cache
    yaml_file = _MASTERS_DIR / f"{master_id}.yaml"
    if yaml_file.exists():
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
            _MASTER_CACHE[master_id] = data
            return data
        except Exception as e:
            logger.error(f"[Masters] Failed to load {yaml_file}: {e}")

    raise ValueError(f"Unknown master: '{master_id}'. "
                     f"Available: {list_available_masters()}")


# ── Prompt formatting ──

_METHODLOGY_TEMPLATE = """\n
=== {name} ({style}) 方法论注入 ===

【核心原则】
{principles}

【关键指标/决策标准】
{indicators}

【分析框架】
{framework}

【风险控制/退出标准】
{risk_exit}

【A股适配要点】
{a_share}

=== 方法论注入结束 ===\n"""


# User-authored custom theory injected into a role's prompt.  Follows the same
# "角色定义 + {自定义理论}" contract as the master methodology so an agent's
# prompt body stays identical whether the slot is filled by a preset master or
# by the user's own investment thesis.
_CUSTOM_THEORY_TEMPLATE = """\n
=== 自定义理论注入{who} ===

{text}

=== 自定义理论注入结束 ===\n"""


# Nicer Chinese label for the custom-theory header, keyed by role.
_ROLE_LABEL: dict[str, str] = {
    "bull_researcher":      "（多头研究员）",
    "bear_researcher":      "（空头研究员）",
    "aggressive_debator":   "（激进风控）",
    "conservative_debator": "（保守风控）",
    "neutral_debator":      "（中立风控）",
    "trader":               "（交易员）",
    "portfolio_manager":    "（投资组合经理）",
    "research_manager":     "（研究主管）",
    "fundamentals_analyst": "（基本面分析师）",
    "market_analyst":       "（技术面分析师）",
    "news_analyst":         "（新闻分析师）",
    "sentiment_analyst":    "（情绪面分析师）",
}


def format_custom_theory(role: str, text: str) -> str:
    """Wrap user-authored theory text into the standard injection block.

    Public so the web layer / callers can preview exactly what will be
    injected for a given role.
    """
    text = (text or "").strip()
    if not text:
        return ""
    return _CUSTOM_THEORY_TEMPLATE.format(who=_ROLE_LABEL.get(role, ""), text=text)


def _format_methodology(data: dict) -> str:
    """Convert a master YAML dict into a formatted prompt snippet."""
    principles = data.get("core_principles", [])
    indicators = data.get("key_indicators", [])
    framework = data.get("analysis_framework", [])
    risk_exit = data.get("risk_exit_rules", [])
    a_share = data.get("a_share_adaptation", [])

    def _bullet(items: list) -> str:
        if not items:
            return "(未指定)"
        return "\n".join(f"  - {item}" for item in items)

    return _METHODLOGY_TEMPLATE.format(
        name=data.get("name", "Unknown"),
        style=data.get("style", ""),
        principles=_bullet(principles),
        indicators=_bullet(indicators),
        framework=_bullet(framework),
        risk_exit=_bullet(risk_exit),
        a_share=_bullet(a_share),
    )


# ── Public API ──

def get_master_methodology(
    role: str,
    master_id: Optional[str] = None,
) -> str:
    """Return the theory-injection snippet for the given role.

    Unified "角色定义 + {自定义理论}" resolution — the injected block can come
    from either a preset master methodology or a user-authored custom theory:

        1. explicit ``master_id`` argument            (highest priority)
        2. config ``custom_theory_config[role]``      (user's own theory text)
        3. env override key (TRADINGAGENTS_MASTER_*)  (master_id)
        4. config ``master_config[role]``             (master_id)
        5. "" — no injection, original prompt only    (lowest priority)

    Args:
        role: Agent role name (e.g. "bull_researcher", "aggressive_debator")
        master_id: Explicit master override. If None, reads from config.

    Returns:
        Formatted injection string, or "" if nothing is configured.
    """
    if not _MASTER_CACHE:
        _discover_masters()

    # Resolve: explicit override → custom theory → env override → config → empty
    if master_id is None:
        from tradingagents.dataflows.config import get_config
        config = get_config()

        # User-authored custom theory wins over any master selection.
        custom_text = (config.get("custom_theory_config") or {}).get(role, "")
        if custom_text and str(custom_text).strip():
            return format_custom_theory(role, str(custom_text))

        # Check top-level env override keys first (TRADINGAGENTS_MASTER_<SHORT>)
        _ENV_KEY_MAP = {
            "bull_researcher":      "master_bull",
            "bear_researcher":      "master_bear",
            "aggressive_debator":   "master_aggressive",
            "conservative_debator": "master_conservative",
            "neutral_debator":      "master_neutral",
            "trader":               "master_trader",
            "portfolio_manager":    "master_pm",
            "research_manager":     "master_rm",
            "fundamentals_analyst": "master_fundamentals",
            "market_analyst":       "master_market",
            "news_analyst":         "master_news",
            "sentiment_analyst":    "master_sentiment",
        }
        env_key = _ENV_KEY_MAP.get(role)
        if env_key and config.get(env_key):
            master_id = config.get(env_key)

        # Then check nested master_config dict
        if master_id is None:
            master_config = config.get("master_config", {})
            master_id = master_config.get(role, None)

    if master_id is None or master_id == "default":
        # No methodology injection — use original agent prompt
        return ""

    data = _load_master_yaml(master_id)

    # Validate role compatibility
    compatible = data.get("compatible_roles", [])
    if compatible and role not in compatible:
        logger.warning(
            f"[Masters] '{master_id}' is not designed for role '{role}'. "
            f"Compatible: {compatible}. Proceeding anyway."
        )

    return _format_methodology(data)


def get_all_masters_for_role(role: str) -> list[str]:
    """Return all master IDs compatible with the given role."""
    if not MASTER_REGISTRY:
        _discover_masters()
    return MASTER_REGISTRY.get(role, [])


def list_available_roles() -> list[str]:
    """Return all role names that have at least one compatible master."""
    if not MASTER_REGISTRY:
        _discover_masters()
    return sorted(MASTER_REGISTRY.keys())


def list_available_masters() -> list[str]:
    """Return all available master IDs."""
    if not _MASTER_CACHE:
        _discover_masters()
    return sorted(_MASTER_CACHE.keys())


def validate_master_config(master_config: dict) -> dict[str, str]:
    """Validate a master_config dict. Return {role: error_msg} for invalid entries."""
    errors = {}
    for role, master_id in master_config.items():
        if master_id == "default":
            continue
        try:
            data = _load_master_yaml(master_id)
            compatible = data.get("compatible_roles", [])
            if compatible and role not in compatible:
                errors[role] = (
                    f"'{master_id}' not designed for '{role}'. "
                    f"Compatible: {compatible}"
                )
        except ValueError as e:
            errors[role] = str(e)
    return errors
