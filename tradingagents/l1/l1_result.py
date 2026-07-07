#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L1 财报过滤分析器（完整版）
包含所有分析维度

重构说明：
- 阈值配置已迁移到 l1_config.THRESHOLDS
- 数据融合逻辑已迁移到 l1_data_layer.py
- 建议逐步迁移到新架构
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict


@dataclass
class L1AnalysisResult:
    """L1财报过滤结果（完整版）- 增强版"""
    code: str
    name: str

    # 数据期间标识
    data_period: str = None  # 'annual' 或 'quarter'
    report_date: str = None

    # ===== 第一部分：资本结构 =====
    debt_ratio: float = None
    interest_debt: float = None
    cash_coverage: float = None
    short_term_debt_ratio: float = None
    long_term_debt_ratio: float = None
    interest_bearing_debt: float = None
    debt_structure_detail: Dict[str, Any] = None
    capital_structure_change: Dict[str, Any] = None
    peer_comparison: Dict[str, Any] = None
    # 新增：负债剖析增强
    operating_debt_ratio: float = None          # 经营性负债/总负债 (%)
    financing_debt_ratio: float = None          # 融资性负债/总负债 (%)
    debt_source_detail: Dict = None             # 负债来源拆分（银行/债券/其他）
    short_long_ratio: float = None              # 短期有息/长期有息比
    finance_cost_ratio: float = None            # 财务费用/有息负债（综合利率%）
    interest_coverage: float = None             # EBIT/财务费用（利息保障倍数）
    equity_change_detail: Dict = None           # 所有者权益变动明细

    # ===== 第二部分：资产质量 =====
    production_asset_ratio: float = None
    production_asset_roe: float = None
    receivables_ratio: float = None
    goodwill_ratio: float = None
    inventory_ratio: float = None
    r_d_capitalize_ratio: float = None
    non_core_asset_ratio: float = None
    production_asset_trend: List[float] = None
    production_asset_trend_years: List[str] = None  # 对应年份标签，如 ['2021','2022','2023','2024','2025']
    receivables_detail: Dict[str, Any] = None
    asset_quality_detail: Dict[str, Any] = None
    # 新增：资产质量增强
    payables_ratio: float = None                # 应付账款占比 = 应付账款/总资产(%)
    receivables_to_payables: float = None       # 应收/应付 = 应收账款/应付账款
    other_rece_change: float = None             # 其他应收款同比变化率 (%)
    non_core_asset_detail: Dict = None          # 非主业资产明细（扩展计算）
    real_debt_ratio_ex_goodwill: float = None   # 剔除商誉后真实负债率 (%)
    inventory_revenue_diverge: str = None       # 存货vs营收增速背离判断
    r_d_analysis: Dict = None                   # 研发支出分析详情

    # ===== 第三部分：盈利与现金流 =====
    roe: float = None
    net_margin: float = None
    gross_margin: float = None
    ocf_to_netprofit: float = None
    dividend_payout: float = None
    fin_assets_ratio: float = None
    dividend_policy: Dict[str, Any] = None
    fin_assets_detail: Dict[str, Any] = None
    # 新增：盈利真实性增强
    fair_value_impact: Dict = None              # 浮盈浮亏对利润/净资产影响
    dividend_ocf_ratio: float = None            # 分红/经营现金流比
    net_profit_margin_parent: float = None      # 归母净利率 = 归母净利润/营业收入(%)

    # ===== 第四部分：风险信号 =====
    cash_excess_signal: bool = None
    high_cash_high_debt: bool = None
    other_monetary: float = None
    depreciation_policy: str = None
    operating_cycle_days: float = None
    related_party_transactions: Dict[str, Any] = None
    cash_abnormal: Dict[str, Any] = None
    fixed_asset_policy: Dict[str, Any] = None
    # 新增：风险信号增强
    inefficient_cash: bool = None               # 低效现金判断
    cash_ratio: float = None                    # 货币资金占比 = 货币资金/总资产(%)
    cash: float = None                          # 货币资金金额（元）
    cash_assets: float = None                   # 现金类资产总额（元）= 货币资金+交易性金融资产

    # ===== 五年趋势（年报专用） =====
    # 一、盈利与现金流
    roe_trend: List[float] = None
    roe_trend_years: List[str] = None
    net_profit_margin_parent_trend: List[float] = None
    net_profit_margin_parent_trend_years: List[str] = None
    ocf_to_netprofit_trend: List[float] = None
    ocf_to_netprofit_trend_years: List[str] = None
    # 二、资本结构
    debt_ratio_trend: List[float] = None
    debt_ratio_trend_years: List[str] = None
    equity_trend: List[float] = None                  # 所有者权益合计（金额）
    equity_trend_years: List[str] = None
    interest_debt_trend: List[float] = None           # 有息负债率
    interest_debt_trend_years: List[str] = None
    cash_coverage_trend: List[float] = None
    cash_coverage_trend_years: List[str] = None
    cash_ratio_trend: List[float] = None              # 货币资金占比
    cash_ratio_trend_years: List[str] = None
    # 三、资产质量
    r_d_expense_trend: List[float] = None             # 研发费用（金额）
    r_d_expense_trend_years: List[str] = None
    gross_margin_trend: List[float] = None            # 毛利率(%)
    gross_margin_trend_years: List[str] = None
    inventory_ratio_trend: List[float] = None         # 存货占比(%)
    inventory_ratio_trend_years: List[str] = None
    receivables_ratio_trend: List[float] = None       # 应收占比(%)
    receivables_ratio_trend_years: List[str] = None
    goodwill_ratio_trend: List[float] = None          # 商誉占比(%)
    goodwill_ratio_trend_years: List[str] = None
    non_core_asset_ratio_trend: List[float] = None   # 非主业资产占比(%)
    non_core_asset_ratio_trend_years: List[str] = None
    other_receivable_ratio_trend: List[float] = None  # 其他应收款占比(%)
    other_receivable_ratio_trend_years: List[str] = None
    production_asset_roe_trend: List[float] = None     # 生产资产ROE(%)
    production_asset_roe_trend_years: List[str] = None
    short_long_ratio_trend: List[float] = None         # 短期有息/长期有息比
    short_long_ratio_trend_years: List[str] = None
    payables_ratio_trend: List[float] = None            # 应付账款占比(%)
    payables_ratio_trend_years: List[str] = None
    # 四、分红
    dividend_payout_trend: List[float] = None         # 股息发放率(%)
    dividend_payout_trend_years: List[str] = None
    # 五、新增缺失趋势指标（补齐47指标全覆盖）
    dividend_ocf_ratio_trend: List[float] = None      # 分红/OCF比(%)
    dividend_ocf_ratio_trend_years: List[str] = None
    finance_cost_ratio_trend: List[float] = None       # 融资成本率(%)
    finance_cost_ratio_trend_years: List[str] = None
    interest_coverage_trend: List[float] = None        # 利息保障倍数(倍)
    interest_coverage_trend_years: List[str] = None
    receivables_to_payables_trend: List[float] = None  # 应收/应付比(倍)
    receivables_to_payables_trend_years: List[str] = None
    # 六、成长性基础指标（营收/净利润绝对值趋势）
    revenue_trend: List[float] = None                  # 营业收入(亿元)
    revenue_trend_years: List[str] = None
    net_profit_trend: List[float] = None               # 归母净利润(亿元)
    net_profit_trend_years: List[str] = None
    # 七、analysis_indicator现成指标趋势（从宽表直接提取）
    revenue_growth_trend: List[float] = None           # 营业收入增长率(%)
    revenue_growth_trend_years: List[str] = None
    profit_growth_trend: List[float] = None            # 净利润增长率(%)
    profit_growth_trend_years: List[str] = None
    asset_turnover_trend: List[float] = None           # 总资产周转率(次)
    asset_turnover_trend_years: List[str] = None
    current_ratio_trend: List[float] = None            # 流动比率
    current_ratio_trend_years: List[str] = None
    quick_ratio_trend: List[float] = None              # 速动比率
    quick_ratio_trend_years: List[str] = None
    ocf_trend: List[float] = None                      # 经营现金流净额(亿元)
    ocf_trend_years: List[str] = None
    capex_trend: List[float] = None                    # 资本支出(亿元)
    capex_trend_years: List[str] = None
    fcf_trend: List[float] = None                      # 自由现金流=OCF-CAPEX(亿元)
    fcf_trend_years: List[str] = None

    # ===== 综合评分与结论 =====
    overall_score: float = None
    pass_l1: bool = None
    rating: str = None                          # A/B/C/D 评级
    score_detail: Dict = None                   # 6维度分项得分
    red_flags: List[str] = None
    strengths: List[str] = None
    summary: str = None
    detailed_report: str = None

    # ===== 年报附注人工核查清单 =====
    all_manual_checks: List[Dict[str, str]] = None


@dataclass
class L1DualPeriodResult:
    """双维度L1分析结果"""
    code: str
    name: str
    
    # 年报数据
    annual: L1AnalysisResult = None
    
    # 最新季报数据
    quarter: L1AnalysisResult = None
    
    # 对比分析
    comparison: Dict[str, Any] = None
    
    # 综合报告
    dual_report: str = None

