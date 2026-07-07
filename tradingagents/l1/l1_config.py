#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L1分析器配置文件
所有硬编码值集中管理
"""

# ===== 1. 数据获取配置 =====
class DataConfig:
    """数据获取配置"""

    # 年报取数范围
    ANNUAL_YEARS = 5  # 年报取最近5年（用户要求）

    # 季报取数范围
    QUARTER_COUNT = 4  # 季报取最近4季度

    # ===== 日期格式常量 =====
    ANNUAL_DATE_SUFFIX = '12-31'  # 年报日期后缀（用于DataFrame筛选）
    ANNUAL_DATE_FULL = '1231'  # abstract中年报日期后缀
    REPORT_DATE_FORMAT = '%Y-%m-%d'  # 日期格式

    # ===== 报告期后缀 =====
    # 年报：12-31
    # 一季报：03-31
    # 半年报：06-30
    # 三季报：09-30
    PERIOD_SUFFIXES = {
        'annual': '12-31',
        'q1': '03-31',
        'mid': '06-30',
        'q3': '09-30',
    }

    # ===== abstract日期列后缀 =====
    ABSTRACT_PERIOD_SUFFIXES = {
        'annual': '1231',
        'q1': '0331',
        'mid': '0630',
        'q3': '0930',
    }

    # ===== 日期列名 =====
    DATE_COL_NAMES = ['日期', 'REPORT_DATE', 'REPORT_DATE_NAME', 'date']


# ===== 2. 阈值配置 =====
THRESHOLDS = {
    # ===== 资本结构阈值 =====
    'debt_ratio_max': 90,  # 资产负债率上限(%)
    'interest_debt_max': 70,  # 有息负债率上限(%)
    'cash_coverage_min': 0.5,  # 现金覆盖率下限(倍)
    'short_long_ratio_max': 2.0,  # 短长期有息负债比上限

    # ===== 资产质量阈值 =====
    'receivables_ratio_max': 30,  # 应收账款占比上限(%)
    'goodwill_ratio_max': 30,  # 商誉占比上限(%)
    'inventory_ratio_max': 40,  # 存货占比上限(%)
    'production_asset_ratio_max': 70,  # 生产资产占比上限(%)
    'cash_excess_ratio': 30,  # 货币资金异常阈值(%)
    'other_monetary_max': 5,  # 其他货币资金占比上限(%)
    'non_core_asset_ratio_max': 20,  # 非主业资产占比上限(%)

    # ===== 盈利能力阈值 =====
    'roe_min': 8,  # ROE下限(%)
    'net_margin_min': 3,  # 净利率下限(%)
    'gross_margin_min': 20,  # 毛利率下限(%)

    # ===== 成长性阈值 =====
    'revenue_growth_min': -20,  # 营收增长下限(%)
    'revenue_growth_max': 100,  # 营收增长上限(%)
    'profit_growth_min': -30,  # 净利润增长下限(%)
    'total_asset_growth_max': 50,  # 总资产增长上限(%)

    # ===== 现金流阈值 =====
    'ocf_np_ratio_min': 50,  # 经营现金流/净利润下限(%)
    'dividend_ocf_ratio_max': 80,  # 分红/经营现金流上限(%)

    # ===== 运营效率阈值 =====
    'asset_turnover_min': 0.3,  # 总资产周转率下限(次)
    'inventory_turnover_min': 1.0,  # 存货周转率下限(次)
    'receivable_turnover_min': 3.0,  # 应收账款周转率下限(次)

    # ===== 分红阈值 =====
    'dividend_payout_min': 10,  # 股息发放率下限(%)
    'dividend_payout_max': 100,  # 股息发放率上限(%)

    # ===== 评分阈值 =====
    'score_excellent': 80,  # 优秀分数线
    'score_good': 60,  # 良好分数线
    'score_pass': 40,  # 及格分数线
    'debt_ratio_alarm': 70,  # 负债率警告线(%)
    'roe_excellent': 15,  # ROE优秀线(%)
    'roe_outstanding': 20,  # ROE卓越线(%)

    # ===== 一票否决阈值 =====
    'debt_ratio_veto': 95,  # 负债率否决线(%)
    'score_min_pass': 40,  # 通过最低分数

    # ===== 评级分数线 =====
    'rating_a_score': 80,  # A级分数线
    'rating_b_score': 60,  # B级分数线
    'rating_c_score': 40,  # C级分数线
    # D级：<40分
}


# ===== 3. 评分权重 =====
SCORE_WEIGHTS = {
    'profitability': 30,  # 盈利能力(30分)
    'solvency': 20,  # 偿债能力(20分)
    'growth': 20,  # 成长性(20分)
    'efficiency': 15,  # 运营效率(15分)
    'cash': 10,  # 现金流(10分)
    'dividend': 5,  # 分红(5分)
}


# ===== 4. 股票池配置（可扩展）=====
STOCK_POOLS = {
    'baijiu': {
        'name': '白酒',
        'codes': [
            '000596',  # 古井贡酒
            '000858',  # 五粮液
            '600809',  # 山西汾酒
            '000568',  # 泸州老窖
            '002304',  # 洋河股份
            '603369',  # 今世缘
        ],
        'keywords': [
            '贵州茅台', '五粮液', '泸州老窖', '洋河股份',
            '山西汾酒', '古井贡酒', '今世缘', '口子窖',
            '水井坊', '舍得酒业', '酒鬼酒', '迎驾贡酒',
            '金徽酒', '伊力特', '老白干酒',
        ],
    },
    'bank': {
        'name': '银行',
        'codes': [
            '600000',  # 浦发银行
            '601398',  # 工商银行
            '601939',  # 建设银行
            '601288',  # 农业银行
            '601988',  # 中国银行
            '600036',  # 招商银行
        ],
        'keywords': [
            '浦发银行', '工商银行', '建设银行', '农业银行',
            '中国银行', '招商银行', '交通银行', '兴业银行',
            '民生银行', '光大银行', '平安银行', '华夏银行',
        ],
    },
    'default': {
        'name': '默认',
        'codes': [],
        'keywords': [],
    },
}


# ===== 5. 分析器indicator字段名映射 =====

# stock_financial_analysis_indicator (86列) 列名映射
ANALYSIS_INDICATOR_COLS = {
    # 增长率类
    'revenue_growth': '主营业务收入增长率(%)',
    'profit_growth': '净利润增长率(%)',
    'total_asset_growth': '总资产增长率(%)',
    'equity_growth': '股东权益增长率(%)',

    # 周转率类
    'asset_turnover': '总资产周转率(次)',
    'inventory_turnover': '存货周转率(次)',
    'receivable_turnover': '应收账款周转率(次)',
    'current_asset_turnover': '流动资产周转率(次)',
    'fixed_asset_turnover': '固定资产周转率(次)',

    # 流动性类
    'current_ratio': '流动比率',
    'quick_ratio': '速动比率',
    'cash_ratio': '现金比率',
    'operating_cycle': '营业周期(天)',

    # 盈利能力类
    'roe': '净资产收益率(%)',
    'roa': '总资产报酬率(ROA)(%)',
    'net_margin': '销售净利率(%)',
    'gross_margin': '销售毛利率(%)',
    'expense_ratio': '期间费用率(%)',

    # 分红类
    'dividend_payout': '股息发放率(%)',
    'dps': '每股股利(元)',
    'eps': '每股收益(元)',

    # 偿债能力类
    'interest_coverage': '利息支付倍数(倍)',
    'debt_ratio': '资产负债率(%)',

    # 现金流类
    'ocf_np_ratio': '经营现金净流量与净利润的比率(%)',
    'cash_reinvestment_ratio': '现金再投资比率(%)',

    # 每股指标类
    'bps': '每股净资产(元)',
    'ocfps': '每股经营现金净流量(元)',
}


# ===== 6. abstract指标名映射 =====

ABSTRACT_COLS = {
    # 盈利能力类
    'roe': '净资产收益率(ROE)',
    'roa': '总资产报酬率(ROA)',
    'net_margin': '销售净利率',
    'gross_margin': '毛利率',

    # 每股指标类
    'basic_eps': '基本每股收益',
    'diluted_eps': '稀释每股收益',
    'bps': '每股净资产',
    'ocfps': '每股经营现金流',

    # 增长率类
    'revenue_growth': '营业总收入增长率',
    'profit_growth': '归属母公司净利润增长率',
    'total_asset_growth': '总资产增长率',

    # 周转率类
    'asset_turnover': '总资产周转率',
    'inventory_turnover': '存货周转率',
    'receivable_turnover': '应收账款周转率',
    'current_asset_turnover': '流动资产周转率',

    # 流动性类
    'current_ratio': '流动比率',
    'quick_ratio': '速动比率',
}


# ===== 7. abstract选项分类 =====

ABSTRACT_SECTIONS = {
    'common': '常用指标',  # 常用指标
    'per_share': '每股指标',  # 每股指标
    'operation': '经营能力',  # 经营能力
    'profit': '盈利能力',  # 盈利能力
    'structure': '资本结构',  # 资本结构
    'growth': '成长能力',  # 成长能力
    'cash_flow': '现金流量',  # 现金流量
    'liquidity': '偿债能力',  # 偿债能力
    'industry': '行业指标',  # 行业指标
}


# ===== 8. COLUMN_MAPPING 核心字段 =====

# 核心资产负载字段（最常用的，约30个）
CORE_COLUMN_MAPPING = {
    # 资产负债
    '资产合计': ['TOTAL_ASSETS', 'TOTAL_ASSET', '资产合计', '资产总计'],
    '负债合计': ['TOTAL_LIABILITIES', 'TOTAL_LIAB', '负债合计'],
    '所有者权益合计': ['TOTAL_EQUITY', 'TOTAL_OWNERS_EQUITY', '所有者权益合计', 'TOTAL_PARENT_EQUITY'],
    '货币资金': ['MONETARYFUNDS', 'CASH', 'CASH_EQUIVALENT', '货币资金', '现金及现金等价物'],

    # 有息负债
    '短期借款': ['SHORT_LOAN', 'ST_LOAN', 'SHORT_TERM_LOAN', '短期借款'],
    '长期借款': ['LONG_LOAN', 'LT_LOAN', 'LONG_TERM_LOAN', '长期借款'],
    '应付债券': ['BOND_PAYABLE', 'BONDS', '应付债券'],

    # 关键资产
    '固定资产': ['FIXED_ASSET', '固定资产'],
    '应收账款': ['ACCOUNTS_RECE', 'AR', 'ACCOUNTS_RECEIVABLE', '应收账款'],
    '存货': ['INVENTORY', '存货'],
    '商誉': ['GOODWILL', '商誉'],

    # 利润
    '净利润': ['PARENT_NETPROFIT', 'NETPROFIT', 'NET_PROFIT', '净利润', '归属母公司净利润'],
    '营业收入': ['OPERATE_INCOME', 'REVENUE', 'OPERATE_INCOME', '营业收入'],
    '营业成本': ['OPERATE_COST', 'COST_OF_GOODS_SOLD', '营业成本', 'TOTAL_OPERATE_COST'],
    '财务费用': ['FINANCIAL_EXPENSE', 'FINANCE_COST', '财务费用'],

    # 现金流
    '经营活动现金流量净额': ['NETCASH_OPERATE', 'OCF', 'CASH_FLOW_OPERATE', '经营活动产生的现金流量净额'],
    '税前利润': ['TOTAL_PROFIT', 'PRETAX_PROFIT', '利润总额', '税前利润'],
}


# ===== 9. 行业对比配置 =====

INDUSTRY_COMPARISON_CONFIG = {
    'baijiu': {
        'name': '白酒行业',
        'benchmarks': {
            'gross_margin': 70,  # 毛利率基准(%)
            'roe': 15,  # ROE基准(%)
            'revenue_growth': 10,  # 营收增长率基准(%)
        },
        'score_rules': {
            'gross_margin_above_80': 10,
            'roe_above_20': 15,
            'revenue_growth_above_15': 10,
        },
    },
}


# ===== 10. 报告生成配置 =====

REPORT_CONFIG = {
    'summary_template': 'l1_summary',  # 摘要模板
    'detailed_template': 'l1_detailed',  # 详细报告模板
    'include_peer_comparison': True,  # 包含同行对比
    'include_trend': True,  # 包含趋势分析
    'max_red_flags': 10,  # 最多显示的红旗数
    'max_strengths': 10,  # 最多显示的优势数
}


# ===== 辅助函数 =====

def get_threshold(key: str, default=None):
    """安全获取阈值"""
    return THRESHOLDS.get(key, default)


def get_stock_pool(pool_name: str) -> dict:
    """获取股票池配置"""
    return STOCK_POOLS.get(pool_name, STOCK_POOLS['default'])


def get_indicator_col(key: str, source: str = 'analysis') -> str:
    """获取indicator字段名"""
    if source == 'analysis':
        return ANALYSIS_INDICATOR_COLS.get(key, key)
    elif source == 'abstract':
        return ABSTRACT_COLS.get(key, key)
    return key
