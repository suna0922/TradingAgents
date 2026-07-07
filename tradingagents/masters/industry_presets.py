"""Industry-specific master methodology presets for A-share stocks.

Usage:
    from tradingagents.masters.industry_presets import industry_preset, list_industries

    # Get preset for tech stocks
    preset = industry_preset("tech_innovation")
    # → {"bull_researcher": "fisher", "bear_researcher": "marks", ...}

    # Apply to system
    from tradingagents.dataflows.config import set_config
    set_config({"master_config": preset})

Design principles:
    - Industry characteristics → master style match
    - Role positioning → master strength match
    - A-share specifics → local masters where needed
"""

from __future__ import annotations

from typing import Optional


# ── Industry preset definitions ──
# Each preset maps agent roles to master_ids from tradingagents/masters/yaml/

_INDUSTRY_PRESETS: dict[str, dict[str, str]] = {
    "tech_innovation": {
        # 科技创新（半导体/AI/软件/通信/消费电子）
        # 高成长高波动，技术迭代快，国产替代主线，题材炒作严重
        "bull_researcher":      "fisher",        # 闲聊法调研技术壁垒
        "bear_researcher":      "marks",         # 钟摆顶部识别科技泡沫
        "aggressive_debator":   "wood",          # 颠覆性创新不惧高估值
        "conservative_debator": "klarman",       # 极致安全边际防御
        "neutral_debator":      "aqr",           # 多因子量化平衡
        "portfolio_manager":    "lynch",         # GARP 合理价格成长
        "fundamentals_analyst": "fisher",        # 成长定性分析
        "market_analyst":       "raschke",       # 技术面趋势
    },
    "new_energy": {
        # 新能源（光伏/锂电/风电/储能/新能源车）
        # 成长+周期双属性，政策驱动强，产能周期是核心
        "bull_researcher":      "zhang_lei",     # 长坡厚雪产业链布局
        "bear_researcher":      "bury",          # 极深逆向识别产能过剩
        "aggressive_debator":   "soros",         # 反身性捕捉政策催化
        "conservative_debator": "marks",         # 周期钟摆防御
        "neutral_debator":      "munger",        # 逆向思维避免极端
        "portfolio_manager":    "ptj",           # 宏观+技术节奏
        "fundamentals_analyst": "lynch",         # GARP 成长合理估值
        "market_analyst":       "livermore",     # 趋势跟随
    },
    "consumer": {
        # 消费白马（白酒/食品饮料/家电/零售/美妆）
        # 品牌护城河，现金流稳定，白酒是A股独有消费垄断
        "bull_researcher":      "buffett",       # 护城河+消费垄断
        "bear_researcher":      "graham",        # 安全边际警示高估值
        "aggressive_debator":   "lynch",         # GARP+消费十倍股
        "conservative_debator": "duan_yongping", # 本分+能力圈
        "neutral_debator":      "munger",        # 逆向思维平衡
        "portfolio_manager":    "buffett",       # 长期持有+集中配置
        "fundamentals_analyst": "buffett",       # 护城河分析
        "market_analyst":       "lynch",         # 消费股分类
    },
    "pharma": {
        # 医药医疗（创新药/器械/CXO/中药/医疗服务）
        # 研发驱动，集采政策敏感，黑天鹅频发（临床失败/集采丢标）
        "bull_researcher":      "fisher",        # 闲聊法调研研发管线
        "bear_researcher":      "taleb",         # 黑天鹅尾部风险防御
        "aggressive_debator":   "wood",          # 颠覆性医疗创新
        "conservative_debator": "klarman",       # 极致安全边际+逆向
        "neutral_debator":      "aqr",           # 多因子平衡
        "portfolio_manager":    "qiu_guolu",     # A股本土医药投资框架
        "fundamentals_analyst": "fisher",        # 管线深度调研
        "market_analyst":       "marks",         # 周期+风险意识
    },
    "finance": {
        # 金融（银行/保险/券商）
        # 低估值高杠杆，资产质量为王，利率周期敏感
        "bull_researcher":      "buffett",       # 银行护城河+低成本存款
        "bear_researcher":      "bury",          # 信用风险逆向识别
        "aggressive_debator":   "soros",         # 反身性+金融杠杆周期
        "conservative_debator": "graham",        # 防御+低PB
        "neutral_debator":      "swensen",       # 机构配置思维
        "portfolio_manager":    "dalio",         # 风险平价+宏观
        "fundamentals_analyst": "graham",        # 低估值分析
        "market_analyst":       "dalio",         # 宏观周期
    },
    "cyclical": {
        # 周期资源（有色/煤炭/化工/钢铁/石油石化）
        # 大宗商品定价，周期PE陷阱（高PE买低PE卖），库存周期
        "bull_researcher":      "ptj",           # 宏观+技术节奏
        "bear_researcher":      "marks",         # 周期钟摆顶部
        "aggressive_debator":   "druckenmiller", # 不对称集中下注拐点
        "conservative_debator": "klarman",       # 安全边际+商品底
        "neutral_debator":      "aqr",           # 多因子+周期因子
        "portfolio_manager":    "ptj",           # 宏观+技术
        "fundamentals_analyst": "dalio",         # 宏观供需分析
        "market_analyst":       "livermore",     # 趋势跟随
    },
    "manufacturing": {
        # 高端制造（机械/军工/汽车/通用设备）
        # 产业升级+国产替代，订单驱动，技术壁垒分化
        "bull_researcher":      "fisher",        # 成长定性+技术调研
        "bear_researcher":      "graham",        # 安全边际+资产底
        "aggressive_debator":   "druckenmiller", # 不对称集中下注拐点
        "conservative_debator": "schloss",       # 极简统计低估
        "neutral_debator":      "munger",        # 逆向思维
        "portfolio_manager":    "zhang_lei",     # 产业链布局+长期主义
        "fundamentals_analyst": "lynch",         # GARP
        "market_analyst":       "ptj",           # 宏观+技术
    },
    "real_estate": {
        # 地产基建（房地产/建筑/建材）
        # 政策博弈，高杠杆+信用风险，NAV估值，国企vs民企分化
        "bull_researcher":      "graham",        # 安全边际+低PB
        "bear_researcher":      "bury",          # 信用风险逆向
        "aggressive_debator":   "soros",         # 反身性+政策催化
        "conservative_debator": "klarman",       # 安全边际+困境反转
        "neutral_debator":      "marks",         # 周期判断
        "portfolio_manager":    "marks",         # 周期+信用
        "fundamentals_analyst": "graham",        # 资产底分析
        "market_analyst":       "marks",         # 周期+风险
    },
    "utility": {
        # 公用环保（电力/水务/环保/交运）
        # 稳定现金流，股息率策略，利率敏感，防御性
        "bull_researcher":      "buffett",       # 稳定现金流+护城河
        "bear_researcher":      "graham",        # 安全边际+防御
        "aggressive_debator":   "lynch",         # GARP+稳定增长
        "conservative_debator": "schloss",       # 极简低估值+高分红
        "neutral_debator":      "bogle",         # 被动+低成本
        "portfolio_manager":    "swensen",       # 机构配置
        "fundamentals_analyst": "buffett",       # 现金流分析
        "market_analyst":       "dalio",         # 利率周期
    },
    "agriculture": {
        # 农业养殖（养殖/种植/饲料）
        # 强周期性（猪周期3-4年），周期PE反向，疫病黑天鹅
        "bull_researcher":      "ptj",           # 宏观+周期节奏
        "bear_researcher":      "bury",          # 极深逆向识别周期顶点
        "aggressive_debator":   "soros",         # 反身性+周期
        "conservative_debator": "marks",         # 周期钟摆防御
        "neutral_debator":      "aqr",           # 多因子+周期因子
        "portfolio_manager":    "ptj",           # 周期择时
        "fundamentals_analyst": "dalio",         # 宏观供需分析
        "market_analyst":       "livermore",     # 趋势跟随
    },
}

# ── Industry metadata (for display) ──

_INDUSTRY_INFO: dict[str, dict] = {
    "tech_innovation": {
        "name": "科技创新",
        "sw_sectors": "电子/计算机/通信",
        "key_drivers": "技术迭代、国产替代、政策催化",
        "valuation": "高PE/PS，成长溢价",
        "style": "成长+逆向",
    },
    "new_energy": {
        "name": "新能源",
        "sw_sectors": "电力设备",
        "key_drivers": "政策驱动、产能周期、出海",
        "valuation": "周期+成长双属性",
        "style": "成长+周期",
    },
    "consumer": {
        "name": "消费白马",
        "sw_sectors": "食品饮料/家电/商贸/美容",
        "key_drivers": "品牌壁垒、渠道、消费力",
        "valuation": "PE/DCF，确定性溢价",
        "style": "价值投资",
    },
    "pharma": {
        "name": "医药医疗",
        "sw_sectors": "医药生物",
        "key_drivers": "研发管线、集采政策、出海",
        "valuation": "管线DCF+PE，高风险高回报",
        "style": "成长+黑天鹅防御",
    },
    "finance": {
        "name": "金融",
        "sw_sectors": "银行/非银金融",
        "key_drivers": "利率周期、信用周期、政策",
        "valuation": "低PB/PE，资产质量为王",
        "style": "价值+防御",
    },
    "cyclical": {
        "name": "周期资源",
        "sw_sectors": "有色/煤炭/化工/钢铁/石化",
        "key_drivers": "大宗商品价格、供需周期、库存",
        "valuation": "周期PE陷阱（低PE买高点）",
        "style": "宏观+周期",
    },
    "manufacturing": {
        "name": "高端制造",
        "sw_sectors": "机械/军工/汽车",
        "key_drivers": "产业升级、国产替代、订单",
        "valuation": "PE+PEG，订单可见度",
        "style": "成长+宏观",
    },
    "real_estate": {
        "name": "地产基建",
        "sw_sectors": "房地产/建筑/建材",
        "key_drivers": "政策、信用、销售数据",
        "valuation": "低PB/NAV，政策博弈",
        "style": "价值+逆向",
    },
    "utility": {
        "name": "公用环保",
        "sw_sectors": "公用事业/环保/交运",
        "key_drivers": "利率、稳定现金流、特许经营",
        "valuation": "DDM/DCF，股息率",
        "style": "防御+配置",
    },
    "agriculture": {
        "name": "农业养殖",
        "sw_sectors": "农林牧渔",
        "key_drivers": "猪周期/禽周期、粮食价格",
        "valuation": "周期PE反向（高PE买低点）",
        "style": "周期+逆向",
    },
}


def industry_preset(industry: str) -> dict[str, str]:
    """Return the master_config dict for the given industry.

    Args:
        industry: Industry key (e.g. "tech_innovation", "consumer", "pharma")

    Returns:
        Dict mapping role → master_id, ready for set_config({"master_config": ...})

    Raises:
        ValueError: If industry key is not found.
    """
    if industry not in _INDUSTRY_PRESETS:
        available = ", ".join(sorted(_INDUSTRY_PRESETS.keys()))
        raise ValueError(
            f"Unknown industry: '{industry}'. Available: {available}"
        )
    return dict(_INDUSTRY_PRESETS[industry])


def list_industries() -> list[str]:
    """Return all available industry preset keys."""
    return sorted(_INDUSTRY_PRESETS.keys())


def get_industry_info(industry: str) -> dict:
    """Return metadata about an industry preset."""
    if industry not in _INDUSTRY_INFO:
        raise ValueError(
            f"Unknown industry: '{industry}'. "
            f"Available: {', '.join(sorted(_INDUSTRY_INFO.keys()))}"
        )
    return dict(_INDUSTRY_INFO[industry])


def list_industries_with_info() -> list[dict]:
    """Return all industries with their metadata, sorted by key."""
    result = []
    for key in sorted(_INDUSTRY_INFO.keys()):
        info = dict(_INDUSTRY_INFO[key])
        info["key"] = key
        info["preset"] = dict(_INDUSTRY_PRESETS[key])
        result.append(info)
    return result


def apply_industry_preset(industry: str) -> dict[str, str]:
    """Apply an industry preset to the global config and return the preset.

    This is a convenience function that calls set_config internally.

    Args:
        industry: Industry key

    Returns:
        The applied master_config dict
    """
    preset = industry_preset(industry)
    from tradingagents.dataflows.config import set_config
    set_config({"master_config": preset})
    return preset


def validate_industry_preset(industry: str) -> dict[str, str]:
    """Validate that all masters in an industry preset exist and are role-compatible.

    Returns:
        Dict of {role: error_message} for any issues. Empty dict = all valid.
    """
    preset = industry_preset(industry)
    from tradingagents.masters.loader import validate_master_config
    return validate_master_config(preset)
