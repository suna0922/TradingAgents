"""Investment Master Methodology Library.

Each master is defined as a YAML file in this directory.  The ``loader``
module reads the selected master for a given role, formats its methodology
as a prompt snippet, and returns it for injection into agent prompts.

Usage:
    from tradingagents.masters.loader import get_master_methodology

    methodology = get_master_methodology("bull_researcher", "buffett")
    # Returns a formatted string ready to inject into the Bull prompt.

Configuration lives in ``default_config.py`` under the ``master_config`` key.

Industry presets:
    from tradingagents.masters.industry_presets import industry_preset

    preset = industry_preset("tech_innovation")  # 10 A-share industries
    set_config({"master_config": preset})
"""

from .loader import (
    get_master_methodology,
    get_all_masters_for_role,
    list_available_roles,
    list_available_masters,
    validate_master_config,
    MASTER_REGISTRY,
)
from .industry_presets import (
    industry_preset,
    list_industries,
    get_industry_info,
    list_industries_with_info,
    apply_industry_preset,
    validate_industry_preset,
)
