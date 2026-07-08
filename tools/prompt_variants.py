"""
prompt_variants.py — Agent "大师"变体目录。

利用现有 tradingagents/masters/yaml/ 中的 28 位投资大师定义，
为每个 Agent 角色提供可选的大师列表（角色→大师映射）。
"""

from typing import Dict, List

# 从 YAML registry 动态加载（缓存在 loader 中）
try:
    from tradingagents.masters.loader import get_all_masters_for_role

    # ── 各角色可用大师（动态发现） ──
    MASTERS_BY_ROLE: Dict[str, List[str]] = {}
    for _role in ["bull_researcher", "bear_researcher", "portfolio_manager",
                  "trader", "aggressive_debator", "conservative_debator",
                  "neutral_debator", "research_manager", "fundamentals_analyst"]:
        _masters = get_all_masters_for_role(_role)
        # 去重 + 加上 "default"
        MASTERS_BY_ROLE[_role] = sorted(set(_masters)) + ["default"]

except ImportError:
    MASTERS_BY_ROLE = {}

# ── 推荐组合（手选，基于大师风格互补） ──

# 各组大师风格标签
MASTER_STYLES = {
    "buffett":       "value/moat",
    "graham":        "deep_value",
    "munger":        "quality_growth",
    "lynch":         "growth_GARP",
    "fisher":        "scuttlebutt_growth",
    "dalio":         "macro_cycles",
    "drukenmiller":  "macro_momentum",
    "soros":         "reflexivity",
    "klarman":       "margin_of_safety",
    "marks":         "cycle_mastery",
    "livermore":     "tape_reading",
    "simons":        "quant_systematic",
    "ptj":           "macro_tactical",
    "taleb":         "tail_risk",
    "bogle":         "passive_index",
    "swensen":       "endowment_model",
    "schloss":       "statistical_cheap",
    "tepper":        "distressed",
    "ackman":        "activist",
    "bury":          "deep_contrarian",
    "oshaughnessy":  "factor_trends",
    "wood":          "innovation",
    "raschke":       "technical_swing",
    "aqr":           "factor_systematic",
    "duan_yongping": "consumer_moat",
    "zhang_lei":     "emerging_growth",
    "liang_wenfeng": "ai_quant",
    "qiu_guolu":     "value_growth",
}

# ── 推荐组合（不同风格碰撞） ──
# 目标：Bull/Bear/PM 分别用不同流派的大师，让辩论更有张力

RECOMMENDED_COMBOS = [
    {
        "name": "价值派 × 做空派",
        "desc": "Buffett/Munger 看多 vs Klarman/Taleb 看空，PM用 Graham 平衡",
        "masters": {
            "master_bull":   "buffett",
            "master_bear":   "klarman",
            "master_pm":     "graham",
            "master_trader": "livermore",
        },
    },
    {
        "name": "成长派 × 周期派",
        "desc": "Lynch/Fisher 看多 vs Marks/Dalio 看空，PM用 Munger",
        "masters": {
            "master_bull":   "lynch",
            "master_bear":   "marks",
            "master_pm":     "munger",
            "master_trader": "druckenmiller",
        },
    },
    {
        "name": "量化派 × 宏观派",
        "desc": "Simons/AQR 看多 vs Soros/Dalio 看空，PM用 Dalio 宏观视角",
        "masters": {
            "master_bull":   "simons",
            "master_bear":   "soros",
            "master_pm":     "dalio",
            "master_trader": "ptj",
        },
    },
    {
        "name": "实战派 × 风控派",
        "desc": "Livermore/PTJ 看多 vs Taleb/Schloss 看空，PM用 Lynch",
        "masters": {
            "master_bull":   "livermore",
            "master_bear":   "taleb",
            "master_pm":     "lynch",
            "master_trader": "raschke",
        },
    },
    {
        "name": "A股本地派",
        "desc": "段永平/张磊 看多 vs 邱国鹭 看空，PM用 Buffett",
        "masters": {
            "master_bull":   "duan_yongping",
            "master_bear":   "qiu_guolu",
            "master_pm":     "buffett",
            "master_trader": "liang_wenfeng",
        },
    },
    {
        "name": "保守均衡型",
        "desc": "Bogle 看多 vs Swensen 看空，PM用 Marks",
        "masters": {
            "master_bull":   "bogle",
            "master_bear":   "swensen",
            "master_pm":     "marks",
            "master_trader": "simons",
        },
    },
    {
        "name": "无大师（纯系统Prompt）",
        "desc": "所有角色使用 default（不注入任何大师方法论）",
        "masters": {
            "master_bull":   "default",
            "master_bear":   "default",
            "master_pm":     "default",
            "master_trader": "default",
        },
    },
]
