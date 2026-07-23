"""Load investment masters from YAML files."""

from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[3]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import yaml

from web_app.backend.models import Master, MasterStyle, AgentRole

# Emoji mapping for masters
_EMOJI_MAP = {
    "Warren Buffett": "👴",
    "Benjamin Graham": "📚",
    "Peter Lynch": "🔍",
    "George Soros": "🌐",
    "Ray Dalio": "🔄",
    "Howard Marks": "📉",
    "Nassim Nicholas Taleb": "🦢",
    "Jim Simons": "🔢",
    "Philip Fisher": "🌱",
    "Michael Burry": "🔮",
    "Charlie Munger": "🧠",
    "John Bogle": "📊",
    "Jesse Livermore": "🐂",
    "Paul Tudor Jones": "📈",
    "Bill Ackman": "🦈",
    "David Tepper": "🐯",
    "Stanley Druckenmiller": "🎯",
    "Cathie Wood": "🚀",
    "Joel Greenblatt": "📖",
    "James O'Shaughnessy": "📐",
    "Seth Klarman": "🏰",
    "Linda Raschke": "📡",
    "Walter Schloss": "🕰️",
    "David Swensen": "🏛️",
    "张磊 (Zhang Lei)": "🇨🇳",
    "段永平 (Duan Yongping)": "🍎",
    "邱国鹭 (Qiu Guolu)": "📝",
    "梁文锋 (Liang Wenfeng)": "🔬",
    "AQR Capital": "🧮",
    "Robert Shiller": "📉",
    "Li Lu": "🌏",
}

# Title mapping
_TITLE_MAP = {
    "Warren Buffett": "价值投资之父",
    "Benjamin Graham": "证券分析之父",
    "Peter Lynch": "成长股猎手",
    "George Soros": "宏观对冲大师",
    "Ray Dalio": "全天候策略创始人",
    "Howard Marks": "周期与风险大师",
    "Nassim Nicholas Taleb": "黑天鹅作者",
    "Jim Simons": "量化之王",
    "Philip Fisher": "成长股投资之父",
    "Michael Burry": "大空头原型",
    "Charlie Munger": "伯克希尔副董事长",
    "John Bogle": "指数基金之父",
    "Jesse Livermore": "投机之王",
    "Paul Tudor Jones": "宏观交易传奇",
    "Bill Ackman": "激进投资者",
    "David Tepper": "困境资产之王",
    "Stanley Druckenmiller": "索罗斯接班人",
    "Cathie Wood": "颠覆性创新女王",
    "James O'Shaughnessy": "因子投资先驱",
    "Seth Klarman": "安全边际守护者",
    "Linda Raschke": "短线交易女王",
    "Walter Schloss": "烟蒂股大师",
    "David Swensen": "耶鲁模式创始人",
    "张磊 (Zhang Lei)": "高瓴资本创始人",
    "段永平 (Duan Yongping)": "步步高/OPPO创始人",
    "邱国鹭 (Qiu Guolu)": "高毅资产董事长",
    "梁文锋 (Liang Wenfeng)": "量化投资先锋",
    "AQR Capital": "系统化投资先驱",
    "Robert Shiller": "非理性繁荣作者",
    "Li Lu": "喜马拉雅资本创始人",
}

# Industry keyword extraction from a_share_adaptation
_INDUSTRY_KEYWORDS = {
    "消费": "消费",
    "医药": "医药",
    "科技": "科技",
    "金融": "金融",
    "新能源": "新能源",
    "制造业": "制造业",
    "白酒": "白酒",
    "芯片": "芯片/半导体",
    "互联网": "互联网",
    "地产": "地产",
    "银行": "银行",
    "保险": "保险",
    "汽车": "汽车",
    "家电": "家电",
    "零售": "零售",
    "能源": "能源",
    "化工": "化工",
    "TMT": "TMT",
    "生物医药": "生物医药",
    "高端制造": "高端制造",
    "数字经济": "数字经济",
    "人工智能": "AI",
    "电动汽车": "电动车",
    "光伏": "光伏",
    "国防": "军工",
}


def _style_to_enum(style_str: str) -> MasterStyle:
    """Convert YAML style string to MasterStyle enum."""
    s = style_str.lower()
    if any(w in s for w in ("value", "safety", "moat", "深度价值")):
        return MasterStyle.VALUE
    if any(w in s for w in ("growth", "disruptive", "innovation")):
        return MasterStyle.GROWTH
    if any(w in s for w in ("momentum", "trend", "speculation")):
        return MasterStyle.MOMENTUM
    if any(w in s for w in ("quant", "stat_arb", "factor", "systematic")):
        return MasterStyle.QUANT
    if any(w in s for w in ("macro", "reflexivity", "global", "currency")):
        return MasterStyle.MACRO
    if any(w in s for w in ("contrarian", "distressed", "cyclical")):
        return MasterStyle.CONTRARIAN
    if any(w in s for w in ("synthesis", "long_term")):
        return MasterStyle.VALUE
    return MasterStyle.VALUE


def _extract_industries(data: dict) -> list[str]:
    """Extract industry tags from YAML data."""
    industries = set()
    
    # Check a_share_adaptation
    adaptation = data.get("a_share_adaptation", [])
    if isinstance(adaptation, list):
        text = " ".join(adaptation)
    elif isinstance(adaptation, str):
        text = adaptation
    else:
        text = ""
    
    for keyword, tag in _INDUSTRY_KEYWORDS.items():
        if keyword in text:
            industries.add(tag)
    
    # Also check core principles and analysis framework
    for field in ["core_principles", "analysis_framework", "key_indicators"]:
        content = data.get(field, [])
        if isinstance(content, list):
            ft = " ".join(content)
        else:
            ft = str(content)
        for keyword, tag in _INDUSTRY_KEYWORDS.items():
            if keyword in ft and tag not in industries:
                industries.add(tag)
    
    # Special industry assignments based on master style
    name = data.get("name", "")
    style = data.get("style", "")
    
    if "Wood" in name or "disruptive" in style:
        industries.update(["科技", "AI", "电动车"])
    if "Zhang Lei" in name or "张磊" in name:
        industries.update(["消费", "科技", "医药"])
    if "Simons" in name:
        industries.update(["量化全市场"])
    if "Dalio" in name:
        industries.update(["宏观全市场"])
    if "Burry" in name:
        industries.update(["地产", "金融"])
    if "Lynch" in name:
        industries.update(["消费", "零售", "制造业"])
    
    return sorted(industries)[:5]  # Max 5 tags


def _build_methodology(data: dict) -> str:
    """Build a comprehensive methodology description from YAML data."""
    parts = []
    
    # 1. Core principles (first 3)
    principles = data.get("core_principles", [])
    if principles:
        parts.append("核心理念：")
        for p in principles[:3]:
            parts.append(f"• {p}")
    
    # 2. Key indicators (first 3)
    indicators = data.get("key_indicators", [])
    if indicators:
        parts.append("关注指标：")
        for ind in indicators[:3]:
            parts.append(f"• {ind}")
    
    # 3. A-share adaptation (abbreviated)
    adaptation = data.get("a_share_adaptation", [])
    if adaptation:
        parts.append("A股适用：")
        for a in adaptation[:2]:
            parts.append(f"• {a}")
    
    return "\n".join(parts)


def load_all_masters() -> list[Master]:
    """Load all investment masters from YAML files."""
    yaml_dir = _project_root / "tradingagents" / "masters" / "yaml"
    masters = []
    
    if not yaml_dir.exists():
        return masters
    
    for yaml_file in sorted(yaml_dir.glob("*.yaml")):
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            
            if not data or not isinstance(data, dict):
                continue
            
            name = data.get("name", yaml_file.stem)
            master_id = yaml_file.stem
            
            # Build methodology
            methodology = _build_methodology(data)
            
            # Style
            style = _style_to_enum(data.get("style", "value"))
            
            # Industries
            industries = _extract_industries(data)
            
            # Compatible roles
            role_map = {
                "bull_researcher": AgentRole.BULL,
                "bear_researcher": AgentRole.BEAR,
                "fundamentals_analyst": AgentRole.FUNDAMENTALS,
                "market_analyst": AgentRole.MARKET,
                "aggressive_debator": AgentRole.RISK_AGGRESSIVE,
                "conservative_debator": AgentRole.RISK_CONSERVATIVE,
                "neutral_debator": AgentRole.RISK_NEUTRAL,
                "portfolio_manager": AgentRole.MANAGER,
                "research_manager": AgentRole.MANAGER,
                "trader": AgentRole.MARKET,
                "news_analyst": AgentRole.MARKET,
                "sentiment_analyst": AgentRole.MARKET,
            }
            
            compatible_roles_raw = data.get("compatible_roles", [])
            best_for = []
            for r in compatible_roles_raw:
                mapped = role_map.get(r)
                if mapped and mapped not in best_for:
                    best_for.append(mapped)
            
            master = Master(
                id=master_id,
                name=name,
                title=_TITLE_MAP.get(name, ""),
                avatar_url=_EMOJI_MAP.get(name, "🧑‍💼"),
                style=style,
                methodology=methodology,
                best_for=best_for,
                # Add custom fields via dict (they'll be serialized as extra)
            )
            
            # Attach industries as extra metadata (setattr for Pydantic)
            master.__dict__["industries"] = industries
            
            masters.append(master)
            
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to load master YAML %s: %s", yaml_file.name, e
            )
    
    return masters
