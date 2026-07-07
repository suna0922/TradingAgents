"""交易规则数据模型与解析器（简化版）。

核心设计：
- TradingRule: 一条完整交易规则（condition_str + action）
- eval_condition: 安全 eval 执行 PM 原文条件
- RuleParser: 从 PM markdown 文本中提取 TradingRule 列表

与 execution_engine.py 的集成点：
- ExecutionEngine.execute() 调用 rule.evaluate_all(row) 判断是否触发
- 触发时通过 _execute_rule_action() 执行对应的动作
"""

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any, Callable, Tuple

logger = logging.getLogger(__name__)


# ── 安全 eval 条件执行（直接执行PM原文，无需解析）─────────────────────

def eval_condition(condition: str, row: Dict[str, Any]) -> bool:
    """安全 eval PM 原文条件字符串。

    直接从 row 取字段值，支持 MA/RSI 等函数调用。
    示例: "close < 54.95 and volume > MA(volume,20)*1.2"
    """
    # 构建变量上下文（白名单，只暴露需要的字段和函数）
    context = {
        'close': row.get('close'),
        'open': row.get('open'),
        'high': row.get('high'),
        'low': row.get('low'),
        'volume': row.get('volume'),
        'turnover': row.get('turnover'),
        'pct_chg': row.get('pct_chg'),
        'rsi_14': row.get('rsi_14'),
        'rsi': row.get('rsi'),
        'macd': row.get('macd'),
        'macdh': row.get('macdh'),
        'macds': row.get('macds'),
        'boll_ub': row.get('boll_ub'),
        'boll_lb': row.get('boll_lb'),
        'boll': row.get('boll'),
        'close_5_sma': row.get('close_5_sma'),
        'close_10_sma': row.get('close_10_sma'),
        'close_20_sma': row.get('close_20_sma'),
        'close_50_sma': row.get('close_50_sma'),
        'close_60_sma': row.get('close_60_sma'),
        'close_120_sma': row.get('close_120_sma'),
        'close_200_sma': row.get('close_200_sma'),
        'ma5': row.get('close_5_sma'),
        'ma10': row.get('close_10_sma'),
        'ma20': row.get('close_20_sma'),
        'ma50': row.get('close_50_sma'),
        'ma60': row.get('close_60_sma'),
        'kdj_k': row.get('kdj_k'),
        'kdj_d': row.get('kdj_d'),
        'kdj_j': row.get('kdj_j'),
        'atr': row.get('atr'),
        # 基本面字段（从fa_metrics注入）
        'annual_roe': row.get('annual_roe'),
        'annual_gross_margin': row.get('annual_gross_margin'),
        'annual_net_margin': row.get('annual_net_margin'),
        'annual_ocf_to_netprofit': row.get('annual_ocf_to_netprofit'),
        'annual_debt_ratio': row.get('annual_debt_ratio'),
        'annual_cash_coverage': row.get('annual_cash_coverage'),
        'annual_revenue_growth': row.get('annual_revenue_growth'),
        'annual_profit_growth': row.get('annual_profit_growth'),
        'annual_dividend_payout': row.get('annual_dividend_payout'),
        'annual_current_ratio': row.get('annual_current_ratio'),
        'annual_interest_coverage': row.get('annual_interest_coverage'),
        'quarter_roe': row.get('quarter_roe'),
        'quarter_gross_margin': row.get('quarter_gross_margin'),
        'quarter_net_margin': row.get('quarter_net_margin'),
        'quarter_ocf_to_netprofit': row.get('quarter_ocf_to_netprofit'),
        'quarter_debt_ratio': row.get('quarter_debt_ratio'),
        'quarter_revenue_growth': row.get('quarter_revenue_growth'),
        'quarter_profit_growth': row.get('quarter_profit_growth'),
        'quarter_dividend_payout': row.get('quarter_dividend_payout'),
        'quarter_current_ratio': row.get('quarter_current_ratio'),
        'quarter_interest_coverage': row.get('quarter_interest_coverage'),
    }

    # 注册函数
    def MA(field, period: int = 20):
        """N日移动平均。优先用预计算字段，否则从row取。"""
        field_str = str(field).lower() if field else ""
        period_int = int(period) if period else 20
        if field_str in ('close',):
            col = f'close_{period_int}_sma'
            val = row.get(col)
            if val is not None:
                return float(val)
        val = row.get(field_str) if field_str else None
        if val is not None:
            return float(val)
        return None

    def RSI(period: int = 14):
        """RSI指标。优先用预计算字段。"""
        col = f'rsi_{int(period)}'
        val = row.get(col)
        if val is not None:
            return float(val)
        val = row.get('rsi')
        if val is not None:
            return float(val)
        return None

    def BOLL(period: int = 20):
        val = row.get('boll')
        if val is not None:
            return float(val)
        return None

    def BOLL_UPPER(period: int = 20):
        val = row.get('boll_ub')
        if val is not None:
            return float(val)
        return None

    def BOLL_LOWER(period: int = 20):
        val = row.get('boll_lb')
        if val is not None:
            return float(val)
        return None

    def ATR(period: int = 14):
        val = row.get('atr')
        if val is not None:
            return float(val)
        return None

    context['MA'] = MA
    context['RSI'] = RSI
    context['BOLL'] = BOLL
    context['BOLL_UPPER'] = BOLL_UPPER
    context['BOLL_LOWER'] = BOLL_LOWER
    context['ATR'] = ATR
    context['MAX'] = max
    context['MIN'] = min
    context['ABS'] = abs

    # 预处理：把PM输出的大写 AND/OR/NOT 转成小写（Python eval只认小写）
    condition_clean = condition
    condition_clean = re.sub(r'\bAND\b', 'and', condition_clean)
    condition_clean = re.sub(r'\bOR\b', 'or', condition_clean)
    condition_clean = re.sub(r'\bNOT\b', 'not', condition_clean)
    condition_clean = condition_clean.replace('≠', '!=')
    # 去除 % 符号：基本面指标（如股息发放率 94.04%）在数据库中以数值形式存储（94.04），不需要 %
    # 注意：必须在中文指标替换之前执行，避免影响其他内容
    condition_clean = condition_clean.replace('%', '')
    # 修复 MA(field,period) 中 field 被 eval 解析为变量值的问题
    # 将 MA(close,200) 替换为 MA('close',200)，使 field 参数保持为字符串
    def _quote_ma_field(match):
        args_str = match.group(1)
        parts = [p.strip() for p in args_str.split(',')]
        if len(parts) >= 1 and parts[0] in ('close', 'open', 'high', 'low', 'volume', 'turn'):
            parts[0] = repr(parts[0])
            return f"MA({', '.join(parts)})"
        return match.group(0)
    condition_clean = re.sub(r'MA\(([^)]+)\)', _quote_ma_field, condition_clean)
    # 中文指标名替换为英文
    # 注意：中文没有词边界(\b)，且PM可能写"年报2026股息发放率"(中间插年份)
    # 策略：1) 先处理带年报/季报前缀的（优先匹配更长的，避免短别名先替换导致长别名匹配失败）
    #       2) 去掉\b，直接子串匹配（中文无词边界概念）
    #       3) 允许"年报YYYY"或"季报YYYYQq"前缀
    # 关键：必须按别名长度降序处理，确保"年报ROE"先于"ROE"被替换

    # 分离带前缀和不带前缀的别名
    prefixed_items = []   # (cn_name, en_name) 带年报/季报前缀
    bare_items = []       # (cn_name, en_name) 不带前缀
    english_items = []    # (cn_name, en_name) 纯英文

    for cn_name, en_name in FIELD_ALIAS_MAP.items():
        if cn_name in ('close', 'high', 'low', 'open', 'volume', 'turnover',
                       'rsi_14', 'macd', 'macdh', 'boll_ub', 'boll_lb', 'boll',
                       'close_5_sma', 'close_10_sma', 'close_20_sma', 'close_50_sma'):
            continue
        if any('\u4e00' <= c <= '\u9fff' for c in cn_name):
            if cn_name.startswith(('年报', '季报')):
                prefixed_items.append((cn_name, en_name))
            else:
                bare_items.append((cn_name, en_name))
        else:
            english_items.append((cn_name, en_name))

    # 第1轮：处理带前缀的（按长度降序，确保长的先匹配）
    for cn_name, en_name in sorted(prefixed_items, key=lambda x: len(x[0]), reverse=True):
        base_name = cn_name[2:]  # 去掉"年报"或"季报"
        # 匹配：年报[YYYY[Qq]]base_name 或 季报[YYYY[Qq]]base_name
        # 注意：YYYY 和 Qq 都是可选的，支持 "年报ROE"、"年报2025ROE"、"年报2025Q1ROE"
        pat = re.escape(cn_name[0:2]) + r'(?:\d{4}(?:Q[1-4])?)?' + re.escape(base_name)
        condition_clean = re.sub(pat, en_name, condition_clean)

    # 第2轮：处理不带前缀的（按长度降序）
    for cn_name, en_name in sorted(bare_items, key=lambda x: len(x[0]), reverse=True):
        # 先匹配"年报[YYYY[Qq]]+该名"、"季报[YYYY[Qq]]+该名"格式
        # YYYY 和 Qq 都是可选的
        pat = r'(年报|季报)(?:\d{4}(?:Q[1-4])?)?' + re.escape(cn_name)
        condition_clean = re.sub(pat, en_name, condition_clean)
        # 再纯名称匹配（如"股息发放率 > 50%"）
        condition_clean = condition_clean.replace(cn_name, en_name)

    # 第3轮：处理英文指标（保留\b）
    for cn_name, en_name in sorted(english_items, key=lambda x: len(x[0]), reverse=True):
        condition_clean = re.sub(r'\b' + re.escape(cn_name) + r'\b', en_name, condition_clean)

    # ★ 修复: MACD() → macd（列值），RSI(n) → RSI(n)（保留函数调用）
    # 问题: PM 写 MACD() > 0，中文替换后变成 macd() > 0，macd 是 float 列不可调用
    condition_clean = re.sub(r'\bMACD\s*\(\s*\)', 'macd', condition_clean, flags=re.IGNORECASE)
    # RSI(14) → RSI(14) 保留原样（context 中有 RSI 函数），
    # 但中文替换可能把 RSI → rsi → rsi(14) 变成 float call
    # 所以先保护 RSI(...) 不被替换
    condition_clean = re.sub(r'\brsi\s*\(\s*(\d+)\s*\)', r'RSI(\1)', condition_clean, flags=re.IGNORECASE)
    # BOLL_UPPER(20) → boll_upper 函数在 context 中不直接存在，
    # 但 row 中有 boll_ub 列 → 直接映射: BOLL_UPPER(20) → boll_ub
    condition_clean = re.sub(r'\bBOLL_UPPER\s*\(\s*\d+\s*\)', 'boll_ub', condition_clean, flags=re.IGNORECASE)
    condition_clean = re.sub(r'\bBOLL_LOWER\s*\(\s*\d+\s*\)', 'boll_lb', condition_clean, flags=re.IGNORECASE)

    try:
        result = eval(condition_clean, {"__builtins__": {}}, context)
        return bool(result)
    except Exception as e:
        logger.warning(f"[eval_condition] Failed: {condition_clean} (original: {condition}) — {e}")
        raise


# ── 字段别名映射：PM 自然语言 → DataFrame / row 的实际列名 ────────

FIELD_ALIAS_MAP: Dict[str, str] = {
    # ── 量价 ──
    "收盘价": "close",
    "收盘": "close",
    "close": "close",
    "最高价": "high",
    "最高": "high",
    "high": "high",
    "最低价": "low",
    "最低": "low",
    "low": "low",
    "开盘价": "open",
    "开盘": "open",
    "open": "open",
    "成交量": "volume",
    "volume": "volume",
    "换手率": "turn",
    "换手": "turn",
    "turnover": "turn",
    "涨跌幅": "pct_chg",
    "涨幅": "pct_chg",
    "跌幅": "pct_chg",

    # ── 技术指标 ──
    "RSI": "rsi",
    "rsi": "rsi",
    "MACD柱": "macdh",
    "MACD": "macd",
    "macd": "macd",
    "macdh": "macdh",
    "布林上轨": "boll_ub",
    "布林下轨": "boll_lb",
    "BOLL上轨": "boll_ub",
    "BOLL下轨": "boll_lb",
    "KDJ_K值": "kdjk",
    "KDJ_D值": "kdjd",
    "KDJ_J值": "kdjj",
    "KDJ.K": "kdjk",
    "KDJ.D": "kdjd",
    "ATR": "atr",
    "atr": "atr",
    "5日均线": "close_5_sma",
    "MA5": "close_5_sma",
    "ma5": "close_5_sma",
    "20日均线": "close_20_sma",
    "MA20": "close_20_sma",
    "ma20": "close_20_sma",
    "50日均线": "close_50_sma",
    "MA50": "close_50_sma",
    "ma50": "close_50_sma",
    "60日均线": "close_60_sma",
    "MA60": "close_60_sma",
    "ma60": "close_60_sma",
    "120日均线": "close_120_sma",
    "MA120": "close_120_sma",
    "ma120": "close_120_sma",
    "200日均线": "close_200_sma",
    "MA200": "close_200_sma",
    "ma200": "close_200_sma",

    # ── 基本面（年报） ──
    "ROE": "annual_roe",
    "roe": "annual_roe",
    "净资产收益率": "annual_roe",
    "归母净利率": "annual_net_profit_margin_parent",
    "净利润率": "annual_net_margin",
    "净利率": "annual_net_margin",
    "毛利率": "annual_gross_margin",
    "OCF/净利润": "annual_ocf_to_netprofit",
    "经营性现金流/净利润": "annual_ocf_to_netprofit",
    "经营现金流/净利润": "annual_ocf_to_netprofit",
    "股息发放率": "annual_dividend_payout",
    "分红率": "annual_dividend_payout",
    "金融资产占比": "annual_fin_assets_ratio",
    "分红/OCF比": "annual_dividend_ocf_ratio",
    "分红/经营现金流": "annual_dividend_ocf_ratio",
    "资产负债率": "annual_debt_ratio",
    "负债率": "annual_debt_ratio",
    "有息负债率": "annual_interest_debt",
    "现金覆盖率": "annual_cash_coverage",
    "短期负债占比": "annual_short_term_debt_ratio",
    "长期负债占比": "annual_long_term_debt_ratio",
    "有息负债": "annual_interest_bearing_debt",
    "经营性负债占比": "annual_operating_debt_ratio",
    "融资性负债占比": "annual_financing_debt_ratio",
    "短长期负债比": "annual_short_long_ratio",
    "短长期有息比": "annual_short_long_ratio",
    "融资成本率": "annual_finance_cost_ratio",
    "利息保障倍数": "annual_interest_coverage",
    "生产资产占比": "annual_production_asset_ratio",
    "生产资产ROE": "annual_production_asset_roe",
    "应收占比": "annual_receivables_ratio",
    "应收账款占比": "annual_receivables_ratio",
    "商誉占比": "annual_goodwill_ratio",
    "存货占比": "annual_inventory_ratio",
    "研发资本化率": "annual_r_d_capitalize_ratio",
    "非主业资产占比": "annual_non_core_asset_ratio",
    "应付占比": "annual_payables_ratio",
    "应付账款占比": "annual_payables_ratio",
    "应收应付比": "annual_receivables_to_payables",
    "应收账款/应付账款": "annual_receivables_to_payables",
    "真实负债率": "annual_real_debt_ratio_ex_goodwill",
    "剔除商誉负债率": "annual_real_debt_ratio_ex_goodwill",
    "货币资金占比": "annual_cash_ratio",
    "现金占比": "annual_cash_ratio",
    "现金周转天数": "annual_operating_cycle_days",
    "其他货币资金占比": "annual_other_monetary",
    "货币资金": "annual_cash",
    "现金类资产": "annual_cash_assets",
    "流动比率": "annual_current_ratio",
    "综合评分": "annual_overall_score",
    "L1评分": "annual_overall_score",

    # ── 年报前缀 ──
    "年报ROE": "annual_roe",
    "年报净资产收益率": "annual_roe",
    "年报归母净利率": "annual_net_profit_margin_parent",
    "年报净利润率": "annual_net_margin",
    "年报净利率": "annual_net_margin",
    "年报毛利率": "annual_gross_margin",
    "年报OCF/净利润": "annual_ocf_to_netprofit",
    "年报经营性现金流/净利润": "annual_ocf_to_netprofit",
    "年报经营现金流/净利润": "annual_ocf_to_netprofit",
    "年报股息发放率": "annual_dividend_payout",
    "年报分红率": "annual_dividend_payout",
    "年报金融资产占比": "annual_fin_assets_ratio",
    "年报分红/OCF比": "annual_dividend_ocf_ratio",
    "年报分红/经营现金流": "annual_dividend_ocf_ratio",
    "年报资产负债率": "annual_debt_ratio",
    "年报负债率": "annual_debt_ratio",
    "年报有息负债率": "annual_interest_debt",
    "年报现金覆盖率": "annual_cash_coverage",
    "年报短期负债占比": "annual_short_term_debt_ratio",
    "年报长期负债占比": "annual_long_term_debt_ratio",
    "年报有息负债": "annual_interest_bearing_debt",
    "年报经营性负债占比": "annual_operating_debt_ratio",
    "年报融资性负债占比": "annual_financing_debt_ratio",
    "年报短长期负债比": "annual_short_long_ratio",
    "年报短长期有息比": "annual_short_long_ratio",
    "年报融资成本率": "annual_finance_cost_ratio",
    "年报利息保障倍数": "annual_interest_coverage",
    "年报生产资产占比": "annual_production_asset_ratio",
    "年报生产资产ROE": "annual_production_asset_roe",
    "年报应收占比": "annual_receivables_ratio",
    "年报应收账款占比": "annual_receivables_ratio",
    "年报商誉占比": "annual_goodwill_ratio",
    "年报存货占比": "annual_inventory_ratio",
    "年报研发资本化率": "annual_r_d_capitalize_ratio",
    "年报非主业资产占比": "annual_non_core_asset_ratio",
    "年报应付占比": "annual_payables_ratio",
    "年报应付账款占比": "annual_payables_ratio",
    "年报应收应付比": "annual_receivables_to_payables",
    "年报应收账款/应付账款": "annual_receivables_to_payables",
    "年报真实负债率": "annual_real_debt_ratio_ex_goodwill",
    "年报剔除商誉负债率": "annual_real_debt_ratio_ex_goodwill",
    "年报货币资金占比": "annual_cash_ratio",
    "年报现金占比": "annual_cash_ratio",
    "年报现金周转天数": "annual_operating_cycle_days",
    "年报其他货币资金占比": "annual_other_monetary",
    "年报货币资金": "annual_cash",
    "年报现金类资产": "annual_cash_assets",
    "年报流动比率": "annual_current_ratio",
    "年报综合评分": "annual_overall_score",
    "年报L1评分": "annual_overall_score",

    # ── 季报字段 ──
    "季报ROE": "quarter_roe",
    "季报毛利率": "quarter_gross_margin",
    "季报净利率": "quarter_net_margin",
    "季报归母净利率": "quarter_net_profit_margin_parent",
    "季报OCF/净利润": "quarter_ocf_to_netprofit",
    "季报资产负债率": "quarter_debt_ratio",
    "季报有息负债率": "quarter_interest_debt",
    "季报现金覆盖率": "quarter_cash_coverage",
    "季报应收占比": "quarter_receivables_ratio",
    "季报存货占比": "quarter_inventory_ratio",
    "季报股息发放率": "quarter_dividend_payout",
    "季报货币资金占比": "quarter_cash_ratio",
    "季报生产资产占比": "quarter_production_asset_ratio",
    "季报综合评分": "quarter_overall_score",
    "季报经营性现金流/净利润": "quarter_ocf_to_netprofit",
    "季报流动比率": "quarter_current_ratio",
    "季报速动比率": "quarter_quick_ratio",
    "季报应收账款占比": "quarter_receivables_ratio",
    "季报短期负债占比": "quarter_short_term_debt_ratio",
    "季报长期负债占比": "quarter_long_term_debt_ratio",
    "季报利息保障倍数": "quarter_interest_coverage",
    "季报融资成本率": "quarter_finance_cost_ratio",
    "季报现金类资产": "quarter_cash_assets",
    "季报净利润率": "quarter_net_margin",
    "季报净资产收益率": "quarter_roe",

    # ── 趋势字段 ──
    "ROE趋势": "annual_roe_trend",
    "毛利率趋势": "annual_gross_margin_trend",
    "净利率趋势": "annual_net_margin_trend",
    "归母净利率趋势": "annual_net_profit_margin_parent_trend",
    "OCF/净利润趋势": "annual_ocf_to_netprofit_trend",
    "资产负债率趋势": "annual_debt_ratio_trend",
    "有息负债率趋势": "annual_interest_debt_trend",
    "营收增长率": "annual_revenue_growth_trend",
    "营收增长率趋势": "annual_revenue_growth_trend",
    "净利润增长率": "annual_profit_growth_trend",
    "净利润增长率趋势": "annual_profit_growth_trend",
    "总资产周转率": "annual_asset_turnover_trend",
    "总资产周转率趋势": "annual_asset_turnover_trend",
    "流动比率趋势": "annual_current_ratio_trend",
    "速动比率": "annual_quick_ratio_trend",
    "速动比率趋势": "annual_quick_ratio_trend",
    "自由现金流趋势": "annual_fcf_trend",
    "营收趋势": "annual_revenue_trend",
    "净利润趋势": "annual_net_profit_trend",

    # ── 向后兼容 ──
    "fa_ocf_profit_ratio": "annual_ocf_to_netprofit",
    "fa_gross_margin": "annual_gross_margin",
    "fa_net_margin": "annual_net_margin",
    "fa_debt_ratio": "annual_debt_ratio",
    "fa_current_ratio": "annual_current_ratio",
}


def resolve_field(name: str) -> str:
    """将 PM 文本中的字段名映射到实际的 DataFrame 列名。"""
    key = name.strip()
    if key in FIELD_ALIAS_MAP:
        return FIELD_ALIAS_MAP[key]
    key_lower = key.lower()
    for alias, col_name in FIELD_ALIAS_MAP.items():
        if alias.lower() == key_lower:
            return col_name
    return key


# ── 规则动作枚举 ────────────────────────────────────────────────────

class RuleAction(str, Enum):
    """规则触发后的动作。

    与 tradingagents.agents.schemas.RuleAction 保持一致。
    PM 输出格式：action_name(pct%)，如 sell_pct(30%)、buy_add(20%)。
    """
    STOP_LOSS = "stop_loss"           # 完全止损/清仓
    TAKE_PROFIT = "take_profit"        # 止盈/获利了结
    SELL_ALL = "sell_all"              # 全部清仓（无条件）
    SELL_PCT = "sell_pct"              # 按比例减仓（如 sell_pct(30%)）
    BUY_ADD = "buy_add"                # 加仓（如 buy_add(20%)）
    ALERT_ONLY = "alert_only"          # 仅观察/预警，不执行操作
    RATING_REEVAL = "rating_reeval"    # 评级重新评估（触发 PM 复评）
    NO_LEFT_BUY = "no_left_buy"        # 禁止左侧加仓
    CIRCUIT_BREAK = "circuit_break"    # 基本面熔断清仓
    HOLD = "hold"                      # 无动作


# ── 交易规则数据类 ──────────────────────────────────────────────────

@dataclass
class TradingRule:
    """一条完整的交易规则 = 条件字符串 + 动作 + 元信息。

    条件用 condition_str 存储 PM 原文，evaluate_all() 直接 eval 执行。
    """
    name: str
    action: RuleAction
    condition_str: str = ""  # PM 原文条件，直接 eval
    priority: int = 50
    pct: float = 0.0
    source_sentence: str = ""
    enabled: bool = True

    def evaluate_all(self, row: Dict[str, Any]) -> bool:
        """判断条件是否满足。直接用 eval_condition 执行原文。"""
        if not self.enabled or not self.condition_str:
            return False
        try:
            return eval_condition(self.condition_str, row)
        except Exception as e:
            logger.warning(f"[TradingRule] eval failed: {e}")
            return False

    @property
    def description(self) -> str:
        """人类可读的规则描述。"""
        return f"[{self.name}] WHEN {self.condition_str} THEN {self.action.value}" + (
            f" (pct={self.pct:.0%})" if self.pct > 0 else ""
        )

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict（用于缓存）。"""
        return {
            "name": self.name,
            "action": self.action.value,
            "condition_str": self.condition_str,
            "priority": self.priority,
            "pct": self.pct,
            "source_sentence": self.source_sentence,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradingRule":
        """从 dict 反序列化。"""
        return cls(
            name=data.get("name", ""),
            action=RuleAction(data.get("action", "hold")),
            condition_str=data.get("condition_str", ""),
            priority=data.get("priority", 50),
            pct=data.get("pct", 0.0),
            source_sentence=data.get("source_sentence", ""),
            enabled=data.get("enabled", True),
        )


# ── 规则解析器 ──────────────────────────────────────────────────────

class RuleParser:
    """从 PM markdown 输出中提取 TradingRule 列表。

    只解析 WHEN ... THEN ... 格式的规则行，保存原文条件字符串。
    """

    # 匹配 WHEN <sql> THEN 格式
    # 前缀兼容: hyphen(-), bullet(• U+2022), triangular bullet(‣ U+2023), white bullet(◦ U+25E6)
    # 支持 action(pct%) 格式，如 sell_pct(30%)、buy_add(20%)
    _PAT_SQL_RULE_LINE = re.compile(
        r"[\-\u2022\u2023\u25E6]\s*\[RULE(\d+)\]\s*"
        r"\[([\w_]+)\]\s*"
        r"WHEN\s+(.+?)\s*"
        r"THEN\s+(\w+(?:\(\d+%\))?)"
        r"(?:\s*[-\u2014\u2022\u2023\u25E6]\s*(.+?))?"
        r"\s*\(@\s*"
        r"(?:([\d.N/A]+)\s*(?:yuan|\u5143)?)?"
        r"\s*\)",
        re.IGNORECASE,
    )

    # 兼容 IF ... THEN 格式
    _PAT_STRUCTURED_RULE_LINE = re.compile(
        r"[\-\u2022\u2023\u25E6]\s*\[RULE(\d+)\]\s*"
        r"\[([\w_]+)\]\s*"
        r"IF\s+(.+?)\s*"
        r"THEN\s+(\w+(?:\(\d+%\))?)"
        r"(?:\s*[-\u2014\u2022\u2023\u25E6]\s*(.+?))?"
        r"\s*\(@\s*"
        r"(?:([\d.]+)\s*(?:yuan|\u5143))?"
        r"(?:\s*\(([^)]+)\))?"
        r"\s*\)",
        re.IGNORECASE,
    )

    def parse(self, pm_text: str, **kwargs) -> List[TradingRule]:
        """解析入口。先尝试 WHEN/THEN 格式，再尝试 IF/THEN 格式。"""
        rules = self._sql_extract(pm_text)
        if not rules:
            rules = self._structured_extract(pm_text)
        rules.sort(key=lambda r: r.priority, reverse=True)
        logger.info(f"[RuleParser] Total: {len(rules)} rules")
        return rules

    def _sql_extract(self, pm_text: str) -> List[TradingRule]:
        """提取 WHEN <cond> THEN <action> 格式的规则。

        支持 action(pct%) 格式，如 sell_pct(30%)、buy_add(20%)。
        """
        rules: List[TradingRule] = []
        _STRING_TO_ACTION = {
            "stop_loss": RuleAction.STOP_LOSS,
            "take_profit": RuleAction.TAKE_PROFIT,
            "reduce_position": RuleAction.SELL_PCT,  # 统一映射到 SELL_PCT
            "downgrade": RuleAction.SELL_PCT,         # 统一映射到 SELL_PCT
            "alert_only": RuleAction.ALERT_ONLY,
            "add_position": RuleAction.BUY_ADD,       # 统一映射到 BUY_ADD
            "rating_adjustment": RuleAction.RATING_REEVAL,  # 映射为重新评级
            "rating_reeval": RuleAction.RATING_REEVAL,
            "sell_all": RuleAction.SELL_ALL,
            "sell_half": RuleAction.SELL_PCT,         # 统一映射到 SELL_PCT
            "sell_pct": RuleAction.SELL_PCT,
            "buy_add": RuleAction.BUY_ADD,
            "no_left_buy": RuleAction.NO_LEFT_BUY,
            "circuit_break": RuleAction.CIRCUIT_BREAK,
            "hold": RuleAction.HOLD,
        }
        _TYPE_TO_ACTION = {
            "stop_loss": RuleAction.STOP_LOSS,
            "take_profit": RuleAction.TAKE_PROFIT,
            "reduce_position": RuleAction.SELL_PCT,
            "observation_anchor": RuleAction.ALERT_ONLY,
            "entry_zone": RuleAction.BUY_ADD,
            "rating_reeval": RuleAction.RATING_REEVAL,
        }
        priority_map = {
            "stop_loss": 90, "take_profit": 85,
            "reduce_position": 75, "downgrade": 80,
            "observation_anchor": 60, "entry_zone": 40,
            "alert_only": 60, "rating_reeval": 70,
        }

        for m in self._PAT_SQL_RULE_LINE.finditer(pm_text):
            rule_type = m.group(2).lower()
            sql_str = m.group(3).strip()
            action_str = m.group(4).lower()
            price_str = m.group(6)

            # 解析 action(pct%) 格式
            action_name, pct = self._parse_action_with_pct(action_str)

            action = _STRING_TO_ACTION.get(action_name,
                    _TYPE_TO_ACTION.get(rule_type, RuleAction.HOLD))
            priority = priority_map.get(rule_type, 50)
            source_sentence = m.group(0).strip()

            # 解析 price_threshold
            price_threshold = None
            if price_str and price_str not in ("N/A", "n/a"):
                try:
                    price_threshold = float(price_str)
                except ValueError:
                    pass

            rules.append(TradingRule(
                name=f"[{rule_type}] {sql_str[:40]}",
                action=action,
                condition_str=sql_str,
                priority=priority,
                pct=pct,
                source_sentence=source_sentence,
            ))

        return rules

    def _structured_extract(self, pm_text: str) -> List[TradingRule]:
        """提取 IF <cond> THEN <action> 格式的规则（向后兼容）。"""
        rules: List[TradingRule] = []
        _TYPE_TO_ACTION = {
            "stop_loss": RuleAction.STOP_LOSS,
            "take_profit": RuleAction.TAKE_PROFIT,
            "reduce_position": RuleAction.SELL_PCT,
            "observation_anchor": RuleAction.ALERT_ONLY,
            "entry_zone": RuleAction.BUY_ADD,
            "rating_reeval": RuleAction.RATING_REEVAL,
        }
        _STRING_TO_ACTION = {
            "stop_loss": RuleAction.STOP_LOSS,
            "take_profit": RuleAction.TAKE_PROFIT,
            "reduce_position": RuleAction.SELL_PCT,
            "downgrade": RuleAction.SELL_PCT,
            "alert_only": RuleAction.ALERT_ONLY,
            "add_position": RuleAction.BUY_ADD,
            "rating_adjustment": RuleAction.RATING_REEVAL,
            "rating_reeval": RuleAction.RATING_REEVAL,
        }

        for m in self._PAT_STRUCTURED_RULE_LINE.finditer(pm_text):
            rule_type = m.group(2).lower()
            trigger_cond = m.group(3).strip()
            action_str = m.group(4).lower()
            price_str = m.group(6)

            # 解析 action(pct%) 格式
            action_name, pct = self._parse_action_with_pct(action_str)

            action = _STRING_TO_ACTION.get(action_name, _TYPE_TO_ACTION.get(rule_type, RuleAction.HOLD))

            priority_map = {
                "stop_loss": 90, "take_profit": 85,
                "reduce_position": 75, "downgrade": 80,
                "observation_anchor": 60, "entry_zone": 40,
                "alert_only": 60, "rating_reeval": 70,
            }
            priority = priority_map.get(rule_type, 50)
            source_sentence = m.group(0).strip()

            # 解析 price_threshold
            price_threshold = None
            if price_str and price_str not in ("N/A", "n/a"):
                try:
                    price_threshold = float(price_str)
                except ValueError:
                    pass

            rules.append(TradingRule(
                name=f"[{rule_type}] {trigger_cond[:30]}",
                action=action,
                condition_str=trigger_cond,
                priority=priority,
                pct=pct,
                source_sentence=source_sentence,
            ))

        return rules

    @staticmethod
    def _parse_action_with_pct(action_str: str) -> Tuple[str, float]:
        """解析 action(pct%) 格式。

        Args:
            action_str: 如 "sell_pct(30%)", "buy_add(20%)", "stop_loss"

        Returns:
            (action_name, pct) — pct 为 0.0~1.0 的小数
        """
        import re
        match = re.match(r'(\w+)\((\d+)%\)', action_str)
        if match:
            action_name = match.group(1).lower()
            pct = float(match.group(2)) / 100.0
            return action_name, pct
        return action_str.lower(), 0.0
