#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L1 财报过滤分析器（整理版）

三层架构：
- l1_result.py      : 数据模型（L1AnalysisResult / L1DualPeriodResult）
- l1_data_layer.py  : 数据提取层（L1DataExtractor — COLUMN_MAPPING + 数据提取方法）
- 本文件            : 分析逻辑层（L1FinancialAnalyzerEnhanced — 指标计算、评分、报告）
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict

# 导入数据模型（兼容包内导入和直接运行）
try:
    from .l1_result import L1AnalysisResult, L1DualPeriodResult
    from .l1_data_layer import L1DataExtractor
except ImportError:
    from l1_result import L1AnalysisResult, L1DualPeriodResult
    from l1_data_layer import L1DataExtractor

# 从配置文件导入阈值和数据配置（重构目标：逐步消除 self.thresholds）
try:
    from .l1_config import THRESHOLDS, DataConfig
    _USE_CONFIG_THRESHOLDS = True
except ImportError:
    _USE_CONFIG_THRESHOLDS = False


class L1FinancialAnalyzerEnhanced(L1DataExtractor):
    """L1 财报过滤分析器（整理版）"""
    def __init__(self, debug: bool = False):
        self.debug = debug
        # 阈值配置（优先使用l1_config中的值，保持向后兼容）
        if _USE_CONFIG_THRESHOLDS:
            self.thresholds = THRESHOLDS.copy()
        else:
            # 向后兼容的默认值
            self.thresholds = {
                'debt_ratio_max': 90,
                'roe_min': 8,
                'net_margin_min': 3,
                'cash_coverage_min': 0.5,
                'receivables_ratio_max': 30,
                'goodwill_ratio_max': 30,
            }

    def analyze(self, code: str, name: str = '', profile: Dict[str, Any] = None) -> L1AnalysisResult:
        """执行完整L1分析（默认年报数据）"""
        r = L1AnalysisResult(code=code, name=name)
        r.red_flags = []
        r.strengths = []

        if profile is None:
            profile = {}

        # 提取数据
        fin = profile.get('fin_indicator', pd.DataFrame())
        bal = profile.get('balance_sheet', pd.DataFrame())
        prof = profile.get('profit_sheet', pd.DataFrame())
        cash = profile.get('cashflow', pd.DataFrame())
        analysis_df = profile.get('analysis_indicator', pd.DataFrame())

        # 判断数据格式：三大报表优先，abstract作为补充
        has_standard_data = (not bal.empty and not prof.empty and not cash.empty)
        is_abstract = self._is_abstract_format(fin)

        # abstract年报期（提前获取，供多处使用）
        target_period = None
        if is_abstract:
            period_cols = self._get_period_cols(fin)
            target_period = self._select_target_period(period_cols, 'annual')

        if has_standard_data:
            # 标准报表格式（优先使用三大报表获取更完整数据）
            annual_row = self._get_annual_row(bal)
            r.report_date = self._get_report_date(bal, annual_row) if annual_row else '年报'
            r.data_period = 'annual'
            self._analyze_capital_structure(r, bal, prof, fin, annual_row)
            self._analyze_structure_change(r, bal)
            self._analyze_asset_quality(r, bal, prof, fin, annual_row)
            self._analyze_profitability(r, fin, prof, cash, bal, target_period=target_period)
            self._analyze_risk_signals(r, bal, prof, cash, annual_row)
            self._analyze_industry_comparison(r, bal, prof, fin)
            # 五年趋势计算
            self._compute_5year_trends(r, bal, prof, cash, fin)
            self._analyze_trend_anomalies(r)

        # abstract补充（无论是否有标准报表都可执行）
        if is_abstract and target_period:
            if has_standard_data:
                # 有三大报表时，abstract作为补充
                self._supplement_from_abstract(r, fin, target_period)
            else:
                # 没有三大报表时，abstract作为主要数据源
                self._analyze_all_from_abstract(r, fin, target_period)

        # analysis_indicator补充（优先级最高，覆盖前面的值）
        if not analysis_df.empty:
            self._supplement_from_analysis_indicator(r, analysis_df, target_period=target_period)

        # 人工核查清单
        self._collect_manual_checks(r)

        # 评分与判定
        self._calc_score(r)
        r.pass_l1 = self._judge_pass(r)
        r.summary = self._generate_summary(r)
        r.detailed_report = self._generate_detailed_report(r)

        return r

    def analyze_dual(self, code: str, name: str = '', profile: Dict[str, Any] = None) -> L1DualPeriodResult:
        """执行双维度L1分析（年报 + 最新季报）"""
        result = L1DualPeriodResult(code=code, name=name)
        result.red_flags = []
        result.strengths = []

        if profile is None:
            profile = {}

        # 提取数据
        fin = profile.get('fin_indicator', pd.DataFrame())
        bal = profile.get('balance_sheet', pd.DataFrame())
        prof = profile.get('profit_sheet', pd.DataFrame())
        cash = profile.get('cashflow', pd.DataFrame())
        analysis_df = profile.get('analysis_indicator', pd.DataFrame())

        # 判断数据格式：三大报表优先，abstract作为补充
        has_standard_data = (not bal.empty and not prof.empty and not cash.empty)
        is_abstract = self._is_abstract_format(fin)

        if has_standard_data:
            # ===== 标准报表格式（优先使用三大报表）=====
            # ===== 年报分析 =====
            r_annual = L1AnalysisResult(code=code, name=name, data_period='annual')
            r_annual.red_flags = []
            r_annual.strengths = []

            annual_rows = self._extract_rows_by_period(bal, prof, cash, fin, 'annual')
            r_annual.report_date = self._get_report_date(bal, annual_rows['bal']) if annual_rows['bal'] else '年报'

            if annual_rows['bal'] is not None:
                self._analyze_capital_structure(r_annual, bal, prof, fin, annual_rows['bal'])
                self._analyze_structure_change(r_annual, bal)
                self._analyze_asset_quality(r_annual, bal, prof, fin, annual_rows['bal'])
                # 获取abstract年报期
                annual_period = None
                if is_abstract:
                    period_cols = self._get_period_cols(fin)
                    annual_period = self._select_target_period(period_cols, 'annual')
                self._analyze_profitability(r_annual, fin, prof, cash, bal,
                                            annual_rows['fin'], annual_rows['prof'],
                                            annual_rows['cash'], annual_rows['bal'],
                                            target_period=annual_period)
                self._analyze_risk_signals(r_annual, bal, prof, cash, annual_rows['bal'])
                self._analyze_industry_comparison(r_annual, bal, prof, fin)
                # 五年趋势计算（年报专用）
                self._compute_5year_trends(r_annual, bal, prof, cash, fin, analysis_df=analysis_df)
                # 趋势异常检测（需趋势数据计算完成后执行）
                self._analyze_trend_anomalies(r_annual)
                # abstract补充指标
                if is_abstract:
                    period_cols = self._get_period_cols(fin)
                    annual_period = self._select_target_period(period_cols, 'annual')
                    if annual_period:
                        self._supplement_from_abstract(r_annual, fin, annual_period)
                # analysis_indicator补充（优先级最高）
                if not analysis_df.empty:
                    self._supplement_from_analysis_indicator(r_annual, analysis_df, target_period=annual_period)
                self._collect_manual_checks(r_annual)
                self._calc_score(r_annual)
                r_annual.pass_l1 = self._judge_pass(r_annual)
                r_annual.summary = self._generate_summary(r_annual)
                r_annual.detailed_report = self._generate_detailed_report(r_annual)

            result.annual = r_annual

            # ===== 最新季报分析 =====
            r_quarter = L1AnalysisResult(code=code, name=name, data_period='quarter')
            r_quarter.red_flags = []
            r_quarter.strengths = []

            quarter_rows = self._extract_rows_by_period(bal, prof, cash, fin, 'quarter')
            r_quarter.report_date = self._get_report_date(bal, quarter_rows['bal']) if quarter_rows['bal'] else '季报'

            if quarter_rows['bal'] is not None:
                self._analyze_capital_structure(r_quarter, bal, prof, fin, quarter_rows['bal'])
                # 注意：权益变动仅适用于年报，季报不调用
                # self._analyze_structure_change(r_quarter, bal)
                self._analyze_asset_quality(r_quarter, bal, prof, fin, quarter_rows['bal'])
                # 获取abstract季报期
                quarter_period = None
                if is_abstract:
                    period_cols = self._get_period_cols(fin)
                    quarter_period = self._select_target_period(period_cols, 'quarter')
                self._analyze_profitability(r_quarter, fin, prof, cash, bal,
                                            quarter_rows['fin'], quarter_rows['prof'],
                                            quarter_rows['cash'], quarter_rows['bal'],
                                            target_period=quarter_period)
                self._analyze_risk_signals(r_quarter, bal, prof, cash, quarter_rows['bal'])
                self._analyze_industry_comparison(r_quarter, bal, prof, fin)
                # 近4季度趋势计算（季报专用）
                self._compute_4quarter_trends(r_quarter, bal, prof, cash, fin, analysis_df=analysis_df)
                # 趋势异常检测（需趋势数据计算完成后执行）
                self._analyze_trend_anomalies(r_quarter)
                # analysis_indicator补充
                if not analysis_df.empty:
                    self._supplement_from_analysis_indicator(r_quarter, analysis_df, target_period=quarter_period)
                self._collect_manual_checks(r_quarter)
                self._calc_score(r_quarter)
                r_quarter.pass_l1 = self._judge_pass(r_quarter)
                r_quarter.summary = self._generate_summary(r_quarter)
                r_quarter.detailed_report = self._generate_detailed_report(r_quarter)

            result.quarter = r_quarter

        else:
            # ===== Abstract格式（三大报表不可用时的后备）=====
            period_cols = self._get_period_cols(fin)
            annual_period = self._select_target_period(period_cols, 'annual')
            quarter_period = self._select_target_period(period_cols, 'quarter')

            if self.debug:
                print(f"  [DEBUG] Abstract模式(后备): annual={annual_period}, quarter={quarter_period}")

            r_annual = L1AnalysisResult(code=code, name=name, data_period='annual')
            r_annual.red_flags = []
            r_annual.strengths = []
            r_annual.report_date = annual_period or '年报'
            self._analyze_all_from_abstract(r_annual, fin, annual_period)
            # analysis_indicator补充
            if not analysis_df.empty:
                self._supplement_from_analysis_indicator(r_annual, analysis_df, target_period=annual_period)
            self._collect_manual_checks(r_annual)
            self._calc_score(r_annual)
            r_annual.pass_l1 = self._judge_pass(r_annual)
            r_annual.summary = self._generate_summary(r_annual)
            r_annual.detailed_report = self._generate_detailed_report(r_annual)
            result.annual = r_annual

            if quarter_period and quarter_period != annual_period:
                r_quarter = L1AnalysisResult(code=code, name=name, data_period='quarter')
                r_quarter.red_flags = []
                r_quarter.strengths = []
                r_quarter.report_date = quarter_period or '季报'
                self._analyze_all_from_abstract(r_quarter, fin, quarter_period)
                # analysis_indicator补充
                if not analysis_df.empty:
                    self._supplement_from_analysis_indicator(r_quarter, analysis_df, target_period=quarter_period)
                self._collect_manual_checks(r_quarter)
                self._calc_score(r_quarter)
                r_quarter.pass_l1 = self._judge_pass(r_quarter)
                r_quarter.summary = self._generate_summary(r_quarter)
                r_quarter.detailed_report = self._generate_detailed_report(r_quarter)
            else:
                r_quarter = r_annual
            result.quarter = r_quarter

        # ===== 对比分析 =====
        result.comparison = self._generate_comparison(result.annual, result.quarter)

        # ===== 综合报告 =====
        result.dual_report = self._generate_dual_report(result)

        return result

    def _generate_comparison(self, annual: L1AnalysisResult, quarter: L1AnalysisResult) ->Dict[str, Any]:
        """生成年报与季报的分离展示（不做跨期类型数值对比）

        年报数据是年度累计值（12个月合计），季报数据是单季度值（3个月），
        两者量级不同，直接做数值对比（如 ROE、OCF/净利润等）没有意义。
        正确做法是：年报看5年趋势演变，季报看4季度动量变化，各自独立分析。
        """
        comparison = {}

        # === 关键指标快照（分别展示，不计算差值）===
        key_metrics = [
            ('debt_ratio', '资产负债率'),
            ('interest_debt', '有息负债率'),
            ('cash_coverage', '现金覆盖率'),
            ('roe', 'ROE'),
            ('net_margin', '净利润率'),
            ('gross_margin', '毛利率'),
            ('overall_score', '综合评分'),
        ]

        annual_snapshot = {}
        quarter_snapshot = {}
        for attr, label in key_metrics:
            ann_val = getattr(annual, attr, None)
            qtr_val = getattr(quarter, attr, None)
            if ann_val is not None:
                annual_snapshot[attr] = {
                    'label': label,
                    'value': round(ann_val, 2) if isinstance(ann_val, float) else ann_val,
                    'report_date': annual.report_date or '最新年报',
                }
            if qtr_val is not None:
                quarter_snapshot[attr] = {
                    'label': label,
                    'value': round(qtr_val, 2) if isinstance(qtr_val, float) else qtr_val,
                    'report_date': quarter.report_date or '最新季报',
                }

        comparison['annual_snapshot'] = annual_snapshot
        comparison['quarter_snapshot'] = quarter_snapshot

        # === 各自内部趋势摘要（不跨期比较）===
        # 年报侧：基于5年趋势的方向性判断
        annual_trend_notes = []
        if hasattr(annual, 'red_flags') and annual.red_flags:
            annual_trend_notes.append(f"年报风险信号: {'; '.join(annual.red_flags[:5])}")
        if hasattr(annual, 'strengths') and annual.strengths:
            annual_trend_notes.append(f"年报优势: {'; '.join(annual.strengths[:5])}")

        # 季报侧：基于近4季度趋势的方向性判断
        quarter_trend_notes = []
        if hasattr(quarter, 'red_flags') and quarter.red_flags:
            quarter_trend_notes.append(f"季报风险信号: {'; '.join(quarter.red_flags[:5])}")
        if hasattr(quarter, 'strengths') and quarter.strengths:
            quarter_trend_notes.append(f"季报优势: {'; '.join(quarter.strengths[:5])}")

        comparison['annual_trend_notes'] = annual_trend_notes
        comparison['quarter_trend_notes'] = quarter_trend_notes

        # === 综合判定（各自独立评估）===
        verdict_parts = []
        if annual.pass_l1:
            verdict_parts.append(f"✅ 年报通过L1筛选（{annual.report_date or '最新年报'}，评分{annual.overall_score or 'N/A'}）")
        else:
            verdict_parts.append(f"❌ 年报未通过L1筛选（{annual.report_date or '最新年报'}，评分{annual.overall_score or 'N/A'}）")

        if quarter.pass_l1:
            verdict_parts.append(f"✅ 季报通过L1筛选（{quarter.report_date or '最新季报'}，评分{quarter.overall_score or 'N/A'}）")
        else:
            verdict_parts.append(f"❌ 季报未通过L1筛选（{quarter.report_date or '最新季报'}，评分{quarter.overall_score or 'N/A'}）")

        comparison['verdict'] = ' | '.join(verdict_parts)
        comparison['important_warning'] = (
            '[重要] 年报为年度累计数据（12个月），季报为单季度数据（3个月），'
            '两者量级不同，禁止直接做数值对比。正确分析方法：'
            '①年报数据应在5年时间序列内比较趋势；②季报数据应在4个季度间比较动量；'
            '③若需关联分析，仅做方向一致性判断（如同为上升/下降趋势），不做数值差计算。'
        )

        return comparison

    def _generate_dual_report(self, result: L1DualPeriodResult) -> str:
        """生成双维度分析报告 - 包含详细分析内容"""
        report = []
        report.append(f"# {result.code} {result.name} - L1财报双维度分析报告\n")
        report.append(f"**生成时间**: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        report.append("---\n\n")
        
        def fmt_val(attr, val):
            if val is None:
                return None
            # 特殊标记：利息收入型（财务费用为负）
            if val == -9999:
                if attr in ['finance_cost_ratio', 'interest_coverage']:
                    return '利息收入型'
                return 'N/A'
            if attr in ['cash_coverage', 'ocf_to_netprofit', 'short_long_ratio', 'interest_coverage']:
                if isinstance(val, float) and val == float('inf'):
                    return '纯短期'
                return f"{val:.2f}倍"
            if attr == 'operating_cycle_days':
                return f"{val:.1f}天"
            if attr in ['total_assets', 'interest_bearing_debt']:
                return f"{val/1e8:.2f}亿"
            if attr in ['revenue', 'profit', 'operating_cash_flow']:
                return f"{val/1e8:.2f}亿"
            if isinstance(val, bool):
                return '是' if val else '否'
            if isinstance(val, (int, float)):
                return f"{val:.2f}%"
            return str(val)
        
        # 全部关键指标（可计算简单字段）
        all_metrics = [
            # 盈利
            ('roe', 'ROE'),
            ('gross_margin', '毛利率'),
            ('net_margin', '净利润率'),
            ('net_profit_margin_parent', '归母净利率'),
            ('ocf_to_netprofit', '经营现金流/归母净利润'),
            ('dividend_payout', '股息发放率'),
            ('fin_assets_ratio', '金融资产投资占比'),
            ('dividend_ocf_ratio', '分红/经营现金流比'),
            # 资本结构
            ('debt_ratio', '资产负债率'),
            ('interest_debt', '有息负债率'),
            ('cash_coverage', '现金覆盖率'),
            ('short_term_debt_ratio', '短期负债/总负债'),
            ('long_term_debt_ratio', '长期负债/总负债'),
            ('operating_debt_ratio', '经营性负债/总负债'),
            ('financing_debt_ratio', '融资性负债/总负债'),
            ('interest_bearing_debt', '有息负债总额'),
            ('short_long_ratio', '短期有息/长期有息比'),
            ('finance_cost_ratio', '融资成本率'),
            ('interest_coverage', '利息保障倍数'),
            # 资产质量
            ('production_asset_ratio', '生产资产占比'),
            ('production_asset_roe', '生产资产ROE'),
            ('receivables_ratio', '应收账款占比'),
            ('payables_ratio', '应付账款占比'),
            ('receivables_to_payables', '应收/应付'),
            ('goodwill_ratio', '商誉占比'),
            ('inventory_ratio', '存货占比'),
            ('r_d_capitalize_ratio', '研发资本化率'),
            ('non_core_asset_ratio', '非主业资产占比'),
            ('other_rece_change', '其他应收款变化'),
            ('real_debt_ratio_ex_goodwill', '剔除商誉后真实负债率'),
            ('inventory_revenue_diverge', '存货vs营收背离'),
            # 现金流与风险
            ('cash_ratio', '货币资金占比'),
            ('cash_excess_signal', '货币资金占比超30%'),
            ('high_cash_high_debt', '高现金高负债'),
            ('inefficient_cash', '低效现金'),
            ('other_monetary', '其他货币资金占比'),
            ('operating_cycle_days', '现金周转天数'),
        ]
        
        def _append_detail(r: L1AnalysisResult, period_name: str):
            report.append(f"## {period_name}\n")
            if r.report_date:
                report.append(f"**数据期间**: {r.report_date}\n\n")
            report.append(f"### 综合评分: {r.overall_score or 'N/A'}/100 | L1通过: {'✅ 是' if r.pass_l1 else '❌ 否'}\n\n")
            
            # 关键指标表格（3列）
            report.append("### 关键指标摘要\n\n")
            report.append("| 指标 | 数值 | 指标 | 数值 | 指标 | 数值 |\n")
            report.append("|------|------|------|------|------|------|\n")
            
            vals = []
            for attr, label in all_metrics:
                val = getattr(r, attr, None)
                if val is not None:
                    vals.append((label, fmt_val(attr, val)))
            
            for i in range(0, len(vals), 3):
                row = vals[i:i+3]
                cells = []
                for label, val in row:
                    cells.extend([label, val])
                while len(cells) < 6:
                    cells.extend(['', ''])
                report.append(f"| {cells[0]} | {cells[1]} | {cells[2]} | {cells[3]} | {cells[4]} | {cells[5]} |\n")
            
            # 嵌入详细报告（去掉标题和附录）
            if r.detailed_report:
                detailed = r.detailed_report
                # 提取 ## 一级标题之后的内容
                lines = detailed.split('\n')
                content_started = False
                content_lines = []
                for line in lines:
                    if line.startswith('## ') or line.startswith('---\n##'):
                        content_started = True
                    if content_started and not line.startswith('# 附录：需年报附注核查的事项'):
                        content_lines.append(line)
                if content_lines:
                    report.append("\n### 详细分析\n")
                    report.append('\n'.join(content_lines))
                    report.append("\n")
        
        # 年报分析
        _append_detail(result.annual, "一、年报分析")
        
        # 季报分析
        _append_detail(result.quarter, "二、最新季报分析")
        
        # 风险信号汇总
        all_flags = set()
        if result.annual.red_flags:
            all_flags.update(result.annual.red_flags)
        if result.quarter.red_flags:
            all_flags.update(result.quarter.red_flags)
        
        if all_flags:
            report.append("\n## 三、风险信号汇总\n")
            for flag in all_flags:
                report.append(f"- ⚠️ {flag}\n")
        
        # 优势汇总
        all_strengths = set()
        if result.annual.strengths:
            all_strengths.update(result.annual.strengths)
        if result.quarter.strengths:
            all_strengths.update(result.quarter.strengths)
        
        if all_strengths:
            report.append("\n## 四、核心优势\n")
            for s in all_strengths:
                report.append(f"- ✅ {s}\n")
        
        report.append("\n---\n")
        report.append("## ⚠️ 数据性质说明（报告使用必读）\n\n")
        report.append("**年报数据** = 年度累计值（12个月合计），用于分析 **5年长期趋势演变**。\n\n")
        report.append("**季报数据** = 单季度值（3个月），用于分析 **4个季度短期动量变化**。\n\n")
        report.append("**禁止操作**：\n")
        report.append("- ❌ 禁止将年报数值与季报数值直接做数值对比（如 ROE 9.77% vs 4.24%）\n")
        report.append("- ❌ 禁止将年度 OCF/净利润 与单季度值放同一表格判定\"恶化/改善\"\n")
        report.append("- ❌ 禁止用季报单期数据否定年报趋势结论（或反之）\n\n")
        report.append("**正确做法**：\n")
        report.append("- ✅ 年报：在 2021→2022→2023→2024→2025 时间序列内比较趋势方向\n")
        report.append("- ✅ 季报：在近4个季度间比较动量变化（环比/同比）\n")
        report.append("- ✅ 关联分析仅做方向一致性判断（如同为改善/同为恶化趋势），不做数值差计算\n\n")
        report.append("---\n")
        return ''.join(report)

    def _analyze_capital_structure(self, r, bal, prof, fin, row_data: Dict = None):
        """第一部分：资本结构分析 - 增强版"""
        if row_data is not None:
            latest = row_data
        else:
            latest = self._get_latest_row(bal)
        if latest is None:
            return
        
        # 资产负债率
        col_total_liab = self._find_col(bal, '负债合计')
        col_total_assets = self._find_col(bal, '资产合计')
        
        if col_total_assets and col_total_liab:
            total_assets = self._safe_float(latest.get(col_total_assets, 0)) or 0
            total_liab = self._safe_float(latest.get(col_total_liab, 0)) or 0
            if total_assets > 0:
                r.debt_ratio = total_liab / total_assets * 100
        
        # ===== T1.1 增强：有息负债（加"一年内到期非流动负债"）=====
        col_st_debt = self._find_col(bal, '短期借款')
        col_lt_debt = self._find_col(bal, '长期借款')
        col_bonds = self._find_col(bal, '应付债券')
        # 新增：一年内到期的非流动负债
        col_due_noncurrent = self._find_col(bal, '一年内到期的非流动负债')
        
        st_debt = self._safe_float(latest.get(col_st_debt, 0)) or 0 if col_st_debt else 0
        lt_debt = self._safe_float(latest.get(col_lt_debt, 0)) or 0 if col_lt_debt else 0
        bonds = self._safe_float(latest.get(col_bonds, 0)) or 0 if col_bonds else 0
        due_noncurrent = self._safe_float(latest.get(col_due_noncurrent, 0)) or 0 if col_due_noncurrent else 0
        
        # 有息负债 = 短期借款 + 长期借款 + 应付债券 + 一年内到期非流动负债
        interest_debt = st_debt + lt_debt + bonds + due_noncurrent
        r.interest_bearing_debt = interest_debt
        
        # ===== T1.2：有息负债率 = 有息负债/总资产 =====
        if total_assets > 0:
            r.interest_debt = interest_debt / total_assets * 100
        
        # ===== T1.3 增强：现金覆盖率 = (货币资金+交易性金融资产)/有息负债 =====
        col_cash = self._find_col(bal, '货币资金')
        col_trading_fin = self._find_col(bal, '交易性金融资产')
        cash = self._safe_float(latest.get(col_cash, 0)) or 0 if col_cash else 0
        trading_fin = self._safe_float(latest.get(col_trading_fin, 0)) or 0 if col_trading_fin else 0
        # 现金类资产 = 货币资金 + 交易性金融资产
        cash_assets = cash + trading_fin
        r.cash = cash
        r.cash_assets = cash_assets
        if interest_debt > 0:
            r.cash_coverage = cash_assets / interest_debt

        # 货币资金占比
        if total_assets > 0:
            r.cash_ratio = cash / total_assets * 100
        
        # ===== T1.4：负债结构拆解（短/长期负债比例）=====
        col_current_liab = self._find_col(bal, '流动负债合计')
        col_noncurrent_liab = self._find_col(bal, '非流动负债合计')
        
        current_liab = self._safe_float(latest.get(col_current_liab, 0)) or 0 if col_current_liab else 0
        noncurrent_liab = self._safe_float(latest.get(col_noncurrent_liab, 0)) or 0 if col_noncurrent_liab else 0
        
        if total_liab > 0:
            r.short_term_debt_ratio = current_liab / total_liab * 100 if total_liab > 0 else None
            r.long_term_debt_ratio = noncurrent_liab / total_liab * 100 if total_liab > 0 else None
        
        # 负债结构详情（仅金额类指标，比率类单独展示）
        # 注意：仅保留负债类项目，资产类项目（货币资金等）在资本结构主表格中展示
        r.debt_structure_detail = {
            '短期借款': st_debt,
            '长期借款': lt_debt,
            '应付债券': bonds,
            '一年内到期非流动负债': due_noncurrent,
            '流动负债合计': current_liab,
            '非流动负债合计': noncurrent_liab,
            '有息负债总额': interest_debt,
        }

        # ===== N3: 经营性负债率 vs 融资性负债率 =====
        col_ap = self._find_col(bal, '应付账款')
        col_np = self._find_col(bal, '应付票据')
        col_adv = self._find_col(bal, '预收款项')
        col_cl = self._find_col(bal, '合同负债')
        # 修复：补充经营性负债科目（应付职工薪酬、应交税费）
        col_salary_payable = self._find_col(bal, '应付职工薪酬')
        col_tax_payable = self._find_col(bal, '应交税费')
        operating_debt = 0
        for col in [col_ap, col_np, col_adv, col_cl, col_salary_payable, col_tax_payable]:
            if col:
                operating_debt += self._safe_float(latest.get(col, 0)) or 0
        if total_liab > 0:
            r.operating_debt_ratio = round(operating_debt / total_liab * 100, 2)
            r.financing_debt_ratio = round(interest_debt / total_liab * 100, 2)

        # ===== N4: 负债来源明细 =====
        r.debt_source_detail = {
            '银行借款(短期+长期)': st_debt + lt_debt,
            '债券': bonds,
            '其他有息(一年内到期)': due_noncurrent,
        }

        # ===== N5: 短期有息/长期有息比 =====
        long_interest = lt_debt + bonds
        short_interest = st_debt + due_noncurrent
        if long_interest > 0:
            r.short_long_ratio = round(short_interest / long_interest, 2)
        elif short_interest > 0:
            # 长期有息负债为0但有短期有息负债 → 全部为短期负债，标记特殊值
            r.short_long_ratio = float('inf')  # 表示纯短期负债结构

        # ===== N6: 融资成本率 & 利息保障倍数 =====
        # 修复：必须从利润表(prof)获取财务费用和税前利润，不能从资产负债表(bal)查找
        prof_latest = self._get_latest_row(prof) if prof is not None and not prof.empty else None
        col_fin_expense = self._find_col(prof, '财务费用') if prof is not None and not prof.empty else None
        fin_expense = self._safe_float(prof_latest.get(col_fin_expense, 0)) if (col_fin_expense and prof_latest) else 0
        if interest_debt > 0 and fin_expense > 0:
            r.finance_cost_ratio = round(fin_expense / interest_debt * 100, 2)
        elif fin_expense < 0:
            r.finance_cost_ratio = -9999  # 特殊标记：利息收入型（财务费用为负）
            r.strengths.append('财务费用为负，利息收入超过利息支出')
        col_pretax = self._find_col(prof, '税前利润') if prof is not None and not prof.empty else None
        pretax_profit = self._safe_float(prof_latest.get(col_pretax, 0)) if (col_pretax and prof_latest) else 0
        # 利息保障倍数仅在有息负债且财务费用>0时计算
        if fin_expense > 0:
            r.interest_coverage = round((pretax_profit + fin_expense) / fin_expense, 2)
        elif fin_expense < 0:
            r.interest_coverage = -9999  # 特殊标记：利息收入型

        # 风险信号：现金奶牛检测
        if r.cash_coverage and r.cash_coverage > 2 and interest_debt > 0:
            r.strengths.append(f'现金奶牛企业：现金覆盖率{r.cash_coverage:.1f}倍')
        elif r.cash_coverage and r.cash_coverage < 0.5:
            r.red_flags.append(f'现金覆盖率{r.cash_coverage:.1f}倍偏低，偿债压力较大')

    def _analyze_structure_change(self, r, bal):
        """分析权益结构年度变动（仅使用年报数据）"""
        if bal is None or bal.empty or len(bal) < 2:
            return

        # 修复：优先筛选年报（12-31）行，避免季度/年度混用
        date_col = self._find_col(bal, 'REPORT_DATE')
        if date_col:
            # 日期可能是datetime对象或字符串，统一转为字符串后匹配
            date_strs = bal[date_col].astype(str).str.replace(' 00:00:00', '')
            bal_filtered = bal[date_strs.str.endswith('12-31')]
            if len(bal_filtered) >= 2:
                latest2 = bal_filtered.iloc[-2:]
            else:
                latest2 = bal.iloc[-2:]  # fallback
        else:
            latest2 = bal.iloc[-2:]
        if len(latest2) < 2:
            return

        prev_row = latest2.iloc[0].to_dict()
        curr_row = latest2.iloc[1].to_dict()

        col_total_liab = self._find_col(bal, '负债合计')
        col_total_equity = self._find_col(bal, '所有者权益合计')
        col_retained = self._find_col(bal, '未分配利润')
        col_share_cap = self._find_col(bal, '股本')
        col_cap_reserve = self._find_col(bal, '资本公积')
        col_surplus = self._find_col(bal, '盈余公积')

        prev_liab = self._safe_float(prev_row.get(col_total_liab)) if col_total_liab else None
        curr_liab = self._safe_float(curr_row.get(col_total_liab)) if col_total_liab else None
        prev_equity = self._safe_float(prev_row.get(col_total_equity)) if col_total_equity else None
        curr_equity = self._safe_float(curr_row.get(col_total_equity)) if col_total_equity else None

        prev_retained = self._safe_float(prev_row.get(col_retained)) if col_retained else None
        curr_retained = self._safe_float(curr_row.get(col_retained)) if col_retained else None
        prev_share = self._safe_float(prev_row.get(col_share_cap)) if col_share_cap else None
        curr_share = self._safe_float(curr_row.get(col_share_cap)) if col_share_cap else None
        prev_cap_r = self._safe_float(prev_row.get(col_cap_reserve)) if col_cap_reserve else None
        curr_cap_r = self._safe_float(curr_row.get(col_cap_reserve)) if col_cap_reserve else None
        prev_surplus = self._safe_float(prev_row.get(col_surplus)) if col_surplus else None
        curr_surplus = self._safe_float(curr_row.get(col_surplus)) if col_surplus else None

        liab_change = None
        if prev_liab and prev_liab != 0 and curr_liab is not None:
            liab_change = round((curr_liab - prev_liab) / abs(prev_liab) * 100, 2)

        equity_change = None
        if prev_equity and prev_equity != 0 and curr_equity is not None:
            equity_change = round((curr_equity - prev_equity) / abs(prev_equity) * 100, 2)

        def _calc_chg(prev, curr):
            if prev is not None and curr is not None and prev != 0:
                return round((curr - prev) / abs(prev) * 100, 2)
            return None

        retained_chg = _calc_chg(prev_retained, curr_retained)
        share_chg = _calc_chg(prev_share, curr_share)
        cap_reserve_chg = _calc_chg(prev_cap_r, curr_cap_r)
        surplus_chg = _calc_chg(prev_surplus, curr_surplus)

        # Determine main driver
        driver = '其他'
        if retained_chg is not None and abs(retained_chg) > abs(share_chg or 0) and abs(retained_chg) > abs(cap_reserve_chg or 0):
            driver = '利润积累'
        elif share_chg is not None and abs(share_chg) > 5:
            driver = '融资'
        elif cap_reserve_chg is not None and abs(cap_reserve_chg) > 10:
            driver = '融资'

        r.equity_change_detail = {
            '负债同比变化率': liab_change,
            '权益同比变化率': equity_change,
            '未分配利润变化率': retained_chg,
            '股本变化率': share_chg,
            '资本公积变化率': cap_reserve_chg,
            '盈余公积变化率': surplus_chg,
            '主要驱动因素': driver,
        }

    def _get_annual_rows_with_year(self, df: pd.DataFrame) -> List[tuple]:
        """返回年报行列表，每项为(year_str, row_dict)，按年份升序排列"""
        if df is None or df.empty:
            return []
        date_col = self._find_col(df, 'REPORT_DATE')
        if not date_col:
            return []
        date_strs = df[date_col].astype(str).str.replace(' 00:00:00', '')
        annual_mask = date_strs.str[:10].str.endswith('12-31')
        annual_df = df[annual_mask].copy()
        if annual_df.empty:
            return []
        # 按日期排序（升序）
        annual_df = annual_df.sort_values(by=date_col)
        result = []
        for _, row in annual_df.iterrows():
            year = str(row[date_col])[:4]
            result.append((year, row.to_dict()))
        return result

    def _compute_5year_trends(self, r, bal, prof, cash, fin, analysis_df=None):
        """计算年报各模块指标的五年变化趋势"""
        # ★ 关键修复：优先使用 analysis_indicator (86列宽表) 作为 fin 数据源
        # analyze_dual() 中 fin 参数来自 profile['fin_indicator']（通常为空），
        # 而 analysis_df 来自 profile['analysis_indicator']（有数据的宽表）
        fin_wide = analysis_df if (analysis_df is not None and not analysis_df.empty) else fin

        bal_rows = self._get_annual_rows_with_year(bal)
        prof_rows = self._get_annual_rows_with_year(prof)
        cash_rows = self._get_annual_rows_with_year(cash)

        if not bal_rows:
            return

        # 预查找列名（避免循环内重复查找）
        col_total_assets = self._find_col(bal, '资产合计')
        col_total_liab = self._find_col(bal, '负债合计')
        col_equity = self._find_col(bal, '所有者权益合计')
        col_cash = self._find_col(bal, '货币资金')
        col_trading_fin = self._find_col(bal, '交易性金融资产')
        col_st_debt = self._find_col(bal, '短期借款')
        col_lt_debt = self._find_col(bal, '长期借款')
        col_bonds = self._find_col(bal, '应付债券')
        col_due_nc = self._find_col(bal, '一年内到期的非流动负债')
        col_netp_prof = self._find_col(prof, '归母净利润') if prof is not None and not prof.empty else None
        col_rev_prof = self._find_col(prof, '营业收入') if prof is not None and not prof.empty else None
        col_rd_prof = self._find_col(prof, '研发费用') if prof is not None and not prof.empty else None
        col_ocf_cash = self._find_col(cash, '经营活动现金流量净额') if cash is not None and not cash.empty else None
        col_div_cash = self._find_col(cash, '分配股利、利润或偿付利息支付的现金') if cash is not None and not cash.empty else None
        col_fin_exp = self._find_col(cash, 'FINANCE_EXPENSE') if cash is not None and not cash.empty else None
        prod_cols = {}
        for key in ['固定资产', '在建工程', '工程物资', '无形资产']:
            prod_cols[key] = self._find_col(bal, key)
        # 新增：补充更多趋势所需列名
        col_op_cost = self._find_col(prof, '营业成本') if prof is not None and not prof.empty else None
        col_rev_prof2 = self._find_col(prof, '营业收入') if prof is not None and not prof.empty else None
        col_inventory = self._find_col(bal, '存货')
        col_ar_bal = self._find_col(bal, '应收账款')
        col_nr_bal = self._find_col(bal, '应收票据')
        col_goodwill = self._find_col(bal, '商誉')
        col_invest_real = self._find_col(bal, '投资性房地产')
        col_other_rece_bal = self._find_col(bal, '其他应收款')
        col_ap_bal = self._find_col(bal, '应付账款')
        col_np_bal = self._find_col(bal, '应付票据')
        col_pretax_trend = self._find_col(prof, '税前利润') if prof is not None and not prof.empty else None
        col_fin_expense_prof = self._find_col(prof, '财务费用') if prof is not None and not prof.empty else None  # 利润表财务费用

        # analysis_indicator (fin) 宽表的列预查找
        fin_date_col = None
        fin_rev_growth_col, fin_profit_growth_col = None, None
        fin_asset_turnover_col, fin_current_ratio_col, fin_quick_ratio_col = None, None, None
        fin_roe_col = None  # ★ 新增：ROE从fin宽表提取
        if fin_wide is not None and not fin_wide.empty:
            fin_date_col = self._find_col(fin_wide, '日期')
            fin_rev_growth_col = self._find_col(fin_wide, '主营业务收入增长率(%)')
            fin_profit_growth_col = self._find_col(fin_wide, '净利润增长率(%)')
            fin_asset_turnover_col = self._find_col(fin_wide, '总资产周转率(次)')
            fin_current_ratio_col = self._find_col(fin_wide, '流动比率')
            fin_quick_ratio_col = self._find_col(fin_wide, '速动比率')
            fin_roe_col = self._find_col(fin_wide, '净资产收益率(%)')  # ★ 新增
        # 现金流绝对值列（从cash表获取OCF和CAPEX）
        col_capex_cash = self._find_col(cash, '购建固定资产等支付的现金') if cash is not None and not cash.empty else None

        # abstract ROE提取准备
        is_abstract = self._is_abstract_format(fin)
        abstract_period_cols = self._get_period_cols(fin) if is_abstract else []

        years, roe_vals, npm_vals, ocf_np_vals = [], [], [], []
        debt_vals, equity_vals, int_debt_vals, cc_vals, cash_r_vals = [], [], [], [], []
        prod_vals, rd_vals = [], []
        dividend_vals = []
        # 新增趋势收集
        gm_vals, inv_vals, ar_vals, gw_vals, noncore_vals, other_rece_vals = [], [], [], [], [], []
        prod_roe_vals, slr_vals, payables_vals = [], [], []
        # 新增4个缺失指标趋势收集
        dividend_ocf_vals, finance_cost_vals, interest_cov_vals, receivables_payables_vals = [], [], [], []
        # 成长性基础指标趋势收集（营收/净利润绝对值，亿元）
        revenue_vals, net_profit_vals = [], []
        # analysis_indicator现成指标趋势收集（从fin宽表直接提取）
        revenue_growth_vals, profit_growth_vals = [], []   # 增长率(%)
        asset_turnover_vals, current_ratio_vals, quick_ratio_vals = [], [], []  # 周转率/流动性
        ocf_abs_vals, capex_vals, fcf_vals = [], [], []    # 现金流绝对值(亿元)

        _annual_years = DataConfig.ANNUAL_YEARS if _USE_CONFIG_THRESHOLDS else 5
        for year, bal_row in bal_rows[-_annual_years:]:  # 最近N年（配置驱动）
            # 找对应年份的prof和cash行
            prof_row = None
            for py, pr in prof_rows:
                if py == year:
                    prof_row = pr
                    break
            cash_row = None
            for cy, cr in cash_rows:
                if cy == year:
                    cash_row = cr
                    break

            # 从abstract获取ROE（优先），fallback到fin宽表（在fin_row_for_year获取后）
            roe_val = None
            if is_abstract:
                target_period = f"{year}1231"
                if target_period in abstract_period_cols:
                    for _, fin_row in fin.iterrows():
                        indicator_name = str(fin_row.get('指标', '')).strip()
                        category = str(fin_row.get('选项', '')).strip()
                        if indicator_name == '净资产收益率(ROE)':
                            roe_val = self._safe_float(fin_row.get(target_period))
                            break
                        elif indicator_name == '净资产收益率' and category == '常用指标':
                            roe_val = self._safe_float(fin_row.get(target_period))
                            break
            # ★ roe_val 可能在后面被 fin_wide fallback 覆盖（见下方 fin_row_for_year 之后）

            # 从现金流量表计算股息发放率
            dividend_payout = None
            if cash_row and col_div_cash and col_netp_prof and prof_row:
                div_cash = self._safe_float(cash_row.get(col_div_cash))
                fin_exp = self._safe_float(cash_row.get(col_fin_exp)) if col_fin_exp else 0
                netp = self._safe_float(prof_row.get(col_netp_prof))
                if div_cash is not None and netp and netp != 0:
                    # 分配股利利润或偿付利息支付的现金 - 财务费用(利息支出近似)
                    # 当财务费用为负时(利息收入),不减
                    interest_exp = max(0, fin_exp or 0)
                    dividend_approx = max(0, div_cash - interest_exp)
                    dividend_payout = dividend_approx / netp * 100

            total_assets = self._safe_float(bal_row.get(col_total_assets)) if col_total_assets else None
            total_liab = self._safe_float(bal_row.get(col_total_liab)) if col_total_liab else None
            equity = self._safe_float(bal_row.get(col_equity)) if col_equity else None

            cash_val = self._safe_float(bal_row.get(col_cash)) or 0 if col_cash else 0
            trading_fin_val = self._safe_float(bal_row.get(col_trading_fin)) or 0 if col_trading_fin else 0
            st_debt = self._safe_float(bal_row.get(col_st_debt)) or 0 if col_st_debt else 0
            lt_debt = self._safe_float(bal_row.get(col_lt_debt)) or 0 if col_lt_debt else 0
            bonds = self._safe_float(bal_row.get(col_bonds)) or 0 if col_bonds else 0
            due_nc = self._safe_float(bal_row.get(col_due_nc)) or 0 if col_due_nc else 0
            interest_debt = st_debt + lt_debt + bonds + due_nc

            # 资产负债率
            debt_ratio = total_liab / total_assets * 100 if (total_assets and total_assets > 0 and total_liab is not None) else None
            # 有息负债率
            interest_debt_ratio = interest_debt / total_assets * 100 if (total_assets and total_assets > 0) else None
            # 现金覆盖率
            cash_coverage = (cash_val + trading_fin_val) / interest_debt if interest_debt > 0 else None
            # 货币资金占比
            cash_ratio = cash_val / total_assets * 100 if (total_assets and total_assets > 0) else None
            # 生产资产占比
            prod_asset = 0
            for key, col in prod_cols.items():
                if col:
                    prod_asset += self._safe_float(bal_row.get(col, 0)) or 0
            prod_ratio = prod_asset / total_assets * 100 if (total_assets and total_assets > 0) else None
            # 归母净利率
            npm = None
            if prof_row and col_netp_prof and col_rev_prof:
                netp = self._safe_float(prof_row.get(col_netp_prof))
                rev = self._safe_float(prof_row.get(col_rev_prof))
            if netp is not None and rev and rev != 0:
                npm = netp / rev * 100
            # 成长性基础指标（绝对值，亿元）
            revenue_y = round(rev / 1e8, 2) if prof_row and col_rev_prof and (rev := self._safe_float(prof_row.get(col_rev_prof))) is not None else None
            net_profit_y = round(netp / 1e8, 2) if prof_row and col_netp_prof and (netp := self._safe_float(prof_row.get(col_netp_prof))) is not None else None
            # OCF/净利润
            ocf_np = None
            if cash_row and prof_row and col_ocf_cash and col_netp_prof:
                ocf = self._safe_float(cash_row.get(col_ocf_cash))
                netp = self._safe_float(prof_row.get(col_netp_prof))
                if ocf is not None and netp and netp != 0:
                    ocf_np = ocf / netp
            # 研发费用
            rd = None
            if prof_row and col_rd_prof:
                rd = self._safe_float(prof_row.get(col_rd_prof))
            # 毛利率 = (营业收入 - 营业成本) / 营业收入
            gm = None
            if prof_row and col_rev_prof2 and col_op_cost:
                rev_p = self._safe_float(prof_row.get(col_rev_prof2))
                cost_p = self._safe_float(prof_row.get(col_op_cost))
                if rev_p and rev_p > 0 and cost_p is not None:
                    gm = (rev_p - cost_p) / rev_p * 100
            # 存货占比
            inv_ratio = None
            if col_inventory and total_assets and total_assets > 0:
                inv_val = self._safe_float(bal_row.get(col_inventory))
                if inv_val is not None:
                    inv_ratio = inv_val / total_assets * 100
            # 应收占比（应收账款+应收票据）
            ar_ratio = None
            if total_assets and total_assets > 0:
                ar_v = self._safe_float(bal_row.get(col_ar_bal)) if col_ar_bal else 0
                nr_v = self._safe_float(bal_row.get(col_nr_bal)) if col_nr_bal else 0
                total_ar_v = (ar_v or 0) + (nr_v or 0)
                if total_ar_v > 0 or (ar_v is not None):
                    ar_ratio = total_ar_v / total_assets * 100
            # 商誉占比
            gw_ratio = None
            if col_goodwill and total_assets and total_assets > 0:
                gw_val = self._safe_float(bal_row.get(col_goodwill))
                if gw_val is not None and gw_val > 0:
                    gw_ratio = gw_val / total_assets * 100
            # 非主业资产占比（投资性房地产+交易性金融资产）
            noncore_ratio = None
            if total_assets and total_assets > 0:
                ir_v = self._safe_float(bal_row.get(col_invest_real)) if col_invest_real else 0
                tf_v = self._safe_float(bal_row.get(col_trading_fin)) if col_trading_fin else 0
                nc_total = (ir_v or 0) + (tf_v or 0)
                if nc_total > 0:
                    noncore_ratio = nc_total / total_assets * 100
            # 其他应收款占比
            other_rece_ratio = None
            if col_other_rece_bal and total_assets and total_assets > 0:
                orc_v = self._safe_float(bal_row.get(col_other_rece_bal))
                if orc_v is not None and orc_v > 0:
                    other_rece_ratio = orc_v / total_assets * 100
            # 生产资产ROE = 归母净利润 / 生产资产
            prod_roe_val = None
            if prof_row and col_netp_prof and prod_asset > 0:
                netp = self._safe_float(prof_row.get(col_netp_prof))
                if netp and netp > 0:
                    prod_roe_val = netp / prod_asset * 100
            # 应付账款占比 = (应付账款 + 应付票据) / 总资产
            payables_ratio = None
            if total_assets and total_assets > 0:
                ap_v = self._safe_float(bal_row.get(col_ap_bal)) if col_ap_bal else 0
                np_v = self._safe_float(bal_row.get(col_np_bal)) if col_np_bal else 0
                ap_total = (ap_v or 0) + (np_v or 0)
                if ap_total > 0:
                    payables_ratio = ap_total / total_assets * 100
            # 短期有息/长期有息比
            long_int_t = (lt_debt or 0) + (bonds or 0)
            short_int_t = (st_debt or 0) + (due_nc or 0)
            if long_int_t > 0:
                slr_val = round(short_int_t / long_int_t, 2)
            elif short_int_t > 0:
                slr_val = float('inf')
            else:
                slr_val = None

            # ===== 新增4个缺失指标的趋势计算 =====
            # 1) 分红/OCF比 = 分红近似值 / 经营现金流净额
            dividend_ocf_val = None
            if cash_row and col_div_cash and col_ocf_cash:
                div_c = self._safe_float(cash_row.get(col_div_cash))
                ocf_c = self._safe_float(cash_row.get(col_ocf_cash))
                if div_c is not None and ocf_c and ocf_c != 0 and prof_row and col_netp_prof:
                    netp_t = self._safe_float(prof_row.get(col_netp_prof))
                    fin_exp_t = self._safe_float(cash_row.get(col_fin_exp)) if col_fin_exp else 0
                    interest_exp_t = max(0, fin_exp_t or 0)
                    div_approx_t = max(0, div_c - interest_exp_t)
                    if netp_t and netp_t > 0:
                        dividend_ocf_val = round(div_approx_t / ocf_c * 100, 2)

            # 2) 融资成本率 = 财务费用(利润表) / 有息负债 × 100
            finance_cost_val = None
            if interest_debt > 0 and prof_row and col_fin_expense_prof:
                fe_t = self._safe_float(prof_row.get(col_fin_expense_prof, 0)) or 0
                if fe_t > 0:
                    finance_cost_val = round(fe_t / interest_debt * 100, 2)
                elif fe_t < 0:
                    finance_cost_val = -9999  # 利息收入型标记

            # 3) 利息保障倍数 = (税前利润 + 财务费用) / 财务费用
            interest_cov_val = None
            if prof_row and col_fin_expense_prof:
                fe_ic = self._safe_float(prof_row.get(col_fin_expense_prof, 0)) or 0
                if fe_ic > 0 and col_pretax_trend:
                    pt_ic = self._safe_float(prof_row.get(col_pretax_trend, 0)) or 0
                    interest_cov_val = round((pt_ic + fe_ic) / fe_ic, 2)
                elif fe_ic < 0:
                    interest_cov_val = -9999  # 利息收入型标记

            # 4) 应收/应付比 = (应收账款+应收票据) / (应付账款+应付票据)
            receivables_payables_val = None
            if total_assets and total_assets > 0:
                ap_v_t = self._safe_float(bal_row.get(col_ap_bal)) if col_ap_bal else 0
                np_v_t = self._safe_float(bal_row.get(col_np_bal)) if col_np_bal else 0
                ap_total_t = (ap_v_t or 0) + (np_v_t or 0)
                ar_v_t = self._safe_float(bal_row.get(col_ar_bal)) if col_ar_bal else 0
                nr_v_t = self._safe_float(bal_row.get(col_nr_bal)) if col_nr_bal else 0
                ar_total_t = (ar_v_t or 0) + (nr_v_t or 0)
                if ap_total_t > 0 and ar_total_t and ar_total_t > 0:
                    receivables_payables_val = round(ar_total_t / ap_total_t, 2)

            # ===== 从 analysis_indicator (fin) 宽表提取现成指标 =====
            fin_row_for_year = None
            if fin_wide is not None and not fin_wide.empty and fin_date_col:
                for _, fr in fin_wide.iterrows():
                    fd = str(fr[fin_date_col])[:10]
                    # 精确匹配年份+12-31，避免匹配到其他年份的年报
                    if fd == f"{year}-12-31":
                        fin_row_for_year = fr
                        break
            # ★ ROE fallback: 当abstract路径未获取到时，从fin宽表提取
            if roe_val is None and fin_row_for_year is not None and fin_roe_col:
                roe_val = self._safe_float(fin_row_for_year.get(fin_roe_col))
            # 增长率/周转率/流动性（直接从fin宽表取）
            rev_growth_y = None
            if fin_row_for_year is not None and fin_rev_growth_col:
                rev_growth_y = self._safe_float(fin_row_for_year.get(fin_rev_growth_col))
            profit_growth_y = None
            if fin_row_for_year is not None and fin_profit_growth_col:
                profit_growth_y = self._safe_float(fin_row_for_year.get(fin_profit_growth_col))
            asset_turnover_y = None
            if fin_row_for_year is not None and fin_asset_turnover_col:
                asset_turnover_y = self._safe_float(fin_row_for_year.get(fin_asset_turnover_col))
            current_ratio_y = None
            if fin_row_for_year is not None and fin_current_ratio_col:
                current_ratio_y = self._safe_float(fin_row_for_year.get(fin_current_ratio_col))
            quick_ratio_y = None
            if fin_row_for_year is not None and fin_quick_ratio_col:
                quick_ratio_y = self._safe_float(fin_row_for_year.get(fin_quick_ratio_col))
            # 现金流绝对值（亿元）— 从cash行取
            ocf_abs_y = None
            capex_abs_y = None
            fcf_abs_y = None
            if cash_row and col_ocf_cash:
                ocf_raw = self._safe_float(cash_row.get(col_ocf_cash))
                if ocf_raw is not None:
                    ocf_abs_y = round(ocf_raw / 1e8, 2)
            if cash_row and col_capex_cash:
                capex_raw = self._safe_float(cash_row.get(col_capex_cash))
                if capex_raw is not None:
                    capex_abs_y = round(capex_raw / 1e8, 2)
            if ocf_abs_y is not None and capex_abs_y is not None:
                fcf_abs_y = round(ocf_abs_y - capex_abs_y, 2)

            years.append(year)
            roe_vals.append(roe_val)
            npm_vals.append(npm)
            ocf_np_vals.append(ocf_np)
            debt_vals.append(debt_ratio)
            equity_vals.append(equity)
            int_debt_vals.append(interest_debt_ratio)
            cc_vals.append(cash_coverage)
            cash_r_vals.append(cash_ratio)
            prod_vals.append(prod_ratio)
            rd_vals.append(rd)
            dividend_vals.append(dividend_payout)
            gm_vals.append(gm)
            inv_vals.append(inv_ratio)
            ar_vals.append(ar_ratio)
            gw_vals.append(gw_ratio)
            noncore_vals.append(noncore_ratio)
            other_rece_vals.append(other_rece_ratio)
            prod_roe_vals.append(prod_roe_val)
            slr_vals.append(slr_val)
            payables_vals.append(payables_ratio)
            # 新增4个缺失指标
            dividend_ocf_vals.append(dividend_ocf_val)
            finance_cost_vals.append(finance_cost_val)
            interest_cov_vals.append(interest_cov_val)
            receivables_payables_vals.append(receivables_payables_val)
            # 成长性基础指标
            revenue_vals.append(revenue_y)
            net_profit_vals.append(net_profit_y)
            # analysis_indicator现成指标
            revenue_growth_vals.append(rev_growth_y)
            profit_growth_vals.append(profit_growth_y)
            asset_turnover_vals.append(asset_turnover_y)
            current_ratio_vals.append(current_ratio_y)
            quick_ratio_vals.append(quick_ratio_y)
            ocf_abs_vals.append(ocf_abs_y)
            capex_vals.append(capex_abs_y)
            fcf_vals.append(fcf_abs_y)

        if years:
            r.roe_trend_years = years
            r.roe_trend = roe_vals
            r.net_profit_margin_parent_trend_years = years
            r.net_profit_margin_parent_trend = npm_vals
            r.ocf_to_netprofit_trend_years = years
            r.ocf_to_netprofit_trend = ocf_np_vals
            r.debt_ratio_trend_years = years
            r.debt_ratio_trend = debt_vals
            r.equity_trend_years = years
            r.equity_trend = equity_vals
            r.interest_debt_trend_years = years
            r.interest_debt_trend = int_debt_vals
            r.cash_coverage_trend_years = years
            r.cash_coverage_trend = cc_vals
            r.cash_ratio_trend_years = years
            r.cash_ratio_trend = cash_r_vals
            r.production_asset_trend = prod_vals
            r.production_asset_trend_years = years
            r.r_d_expense_trend_years = years
            r.r_d_expense_trend = rd_vals
            r.dividend_payout_trend_years = years
            r.dividend_payout_trend = dividend_vals
            # 新增趋势
            r.gross_margin_trend_years = years
            r.gross_margin_trend = gm_vals
            r.inventory_ratio_trend_years = years
            r.inventory_ratio_trend = inv_vals
            r.receivables_ratio_trend_years = years
            r.receivables_ratio_trend = ar_vals
            r.goodwill_ratio_trend_years = years
            r.goodwill_ratio_trend = gw_vals
            r.non_core_asset_ratio_trend_years = years
            r.non_core_asset_ratio_trend = noncore_vals
            r.other_receivable_ratio_trend_years = years
            r.other_receivable_ratio_trend = other_rece_vals
            r.production_asset_roe_trend_years = years
            r.production_asset_roe_trend = prod_roe_vals
            r.short_long_ratio_trend_years = years
            r.short_long_ratio_trend = slr_vals
            r.payables_ratio_trend_years = years
            r.payables_ratio_trend = payables_vals
            # 新增4个缺失指标趋势赋值
            r.dividend_ocf_ratio_trend_years = years
            r.dividend_ocf_ratio_trend = dividend_ocf_vals
            r.finance_cost_ratio_trend_years = years
            r.finance_cost_ratio_trend = finance_cost_vals
            r.interest_coverage_trend_years = years
            r.interest_coverage_trend = interest_cov_vals
            r.receivables_to_payables_trend_years = years
            r.receivables_to_payables_trend = receivables_payables_vals
            # 成长性基础指标（年报）
            r.revenue_trend_years = years
            r.revenue_trend = revenue_vals
            r.net_profit_trend_years = years
            r.net_profit_trend = net_profit_vals
            # analysis_indicator现成指标（年报）
            r.revenue_growth_trend_years = years
            r.revenue_growth_trend = revenue_growth_vals
            r.profit_growth_trend_years = years
            r.profit_growth_trend = profit_growth_vals
            r.asset_turnover_trend_years = years
            r.asset_turnover_trend = asset_turnover_vals
            r.current_ratio_trend_years = years
            r.current_ratio_trend = current_ratio_vals
            r.quick_ratio_trend_years = years
            r.quick_ratio_trend = quick_ratio_vals
            r.ocf_trend_years = years
            r.ocf_trend = ocf_abs_vals
            r.capex_trend_years = years
            r.capex_trend = capex_vals
            r.fcf_trend_years = years
            r.fcf_trend = fcf_vals

    def _get_quarter_rows_with_period(self, df: pd.DataFrame, max_n: int = 4) -> List[tuple]:
        """返回季报行列表，每项为(period_label, row_dict)，按日期升序排列"""
        if df is None or df.empty:
            return []
        date_col = self._find_col(df, 'REPORT_DATE')
        if not date_col:
            return []
        date_strs = df[date_col].astype(str).str.replace(' 00:00:00', '')
        # 排除12-31结尾的年报行（06-30中报、09-30三季报、03-31一季报保留）
        quarter_mask = ~date_strs.str[:10].str.endswith('12-31')
        quarter_df = df[quarter_mask].copy()
        if quarter_df.empty:
            return []
        quarter_df = quarter_df.sort_values(by=date_col)
        result = []
        for _, row in quarter_df.iterrows():
            ds = str(row[date_col])[:10]  # YYYY-MM-DD
            year = ds[:4]
            month = ds[5:7]
            q_map = {'03': 'Q1', '06': 'Q2', '09': 'Q3'}
            q_label = q_map.get(month, month)
            result.append((f"{year}{q_label}", row.to_dict()))
        return result[-max_n:]  # 最近max_n个季度

    def _compute_4quarter_trends(self, r, bal, prof, cash, fin, analysis_df=None):
        """计算季报各模块指标的近4季度变化趋势

        ⚠️ A股季报数据口径说明：
        - 资产负债表科目：时点值，无需处理 ✅
        - 利润表科目（营收、净利润、研发费用等）：Q1=单季, Q2/Q3=年初累计 ❌需差分
        - 现金流量表科目（经营现金流等）：同上 ❌需差分

        累计→单季度转换公式：
          Q1单季 = Q1原始值（已为单季度）
          Q2单季 = Q2原始值 - Q1原始值（上半年累计 - Q1单季）
          Q3单季 = Q3原始值 - Q2原始值（前三季累计 - 上半年累计）
        """
        # ★ 关键修复：优先使用 analysis_indicator (86列宽表) 作为 fin 数据源
        fin_wide = analysis_df if (analysis_df is not None and not analysis_df.empty) else fin

        bal_rows = self._get_quarter_rows_with_period(bal, max_n=4)
        prof_rows = self._get_quarter_rows_with_period(prof, max_n=4) if prof is not None and not prof.empty else []
        cash_rows = self._get_quarter_rows_with_period(cash, max_n=4) if cash is not None and not cash.empty else []

        if not bal_rows:
            return

        # 预查找列名
        col_total_assets = self._find_col(bal, '资产合计')
        col_total_liab = self._find_col(bal, '负债合计')
        col_equity = self._find_col(bal, '所有者权益合计')
        col_cash_bal = self._find_col(bal, '货币资金')
        col_trading_fin = self._find_col(bal, '交易性金融资产')
        col_st_debt = self._find_col(bal, '短期借款')
        col_lt_debt = self._find_col(bal, '长期借款')
        col_bonds = self._find_col(bal, '应付债券')
        col_due_nc = self._find_col(bal, '一年内到期的非流动负债')
        col_netp_prof = self._find_col(prof, '归母净利润') if prof is not None and not prof.empty else None
        col_rev_prof = self._find_col(prof, '营业收入') if prof is not None and not prof.empty else None
        col_rd_prof = self._find_col(prof, '研发费用') if prof is not None and not prof.empty else None
        col_ocf_cash = self._find_col(cash, '经营活动现金流量净额') if cash is not None and not cash.empty else None
        col_div_cash_q = self._find_col(cash, '分配股利、利润或偿付利息支付的现金') if cash is not None and not cash.empty else None
        col_fin_exp_q = self._find_col(cash, 'FINANCE_EXPENSE') if cash is not None and not cash.empty else None
        col_fin_expense_prof_q = self._find_col(prof, '财务费用') if prof is not None and not prof.empty else None
        col_pretax_q = self._find_col(prof, '税前利润') if prof is not None and not prof.empty else None
        # 补充BS列（4个新指标所需）
        col_ar_bal_q = self._find_col(bal, '应收账款')
        col_nr_bal_q = self._find_col(bal, '应收票据')
        col_ap_bal_q = self._find_col(bal, '应付账款')
        col_np_bal_q = self._find_col(bal, '应付票据')
        # P1修复：利润表/现金流量表指标需做累计→单季度差分，资产负债表时点值不需要
        prod_cols = {}
        for key in ['固定资产', '在建工程', '工程物资', '无形资产']:
            prod_cols[key] = self._find_col(bal, key)

        is_abstract = self._is_abstract_format(fin)
        abstract_period_cols = self._get_period_cols(fin) if is_abstract else []

        # analysis_indicator (fin) 宽表列预查找（季报用）
        fin_q_date_col = None
        fin_q_rev_growth_col, fin_q_profit_growth_col = None, None
        fin_q_asset_turnover_col, fin_q_current_ratio_col, fin_q_quick_ratio_col = None, None, None
        fin_q_roe_col = None  # ★ 新增：季报ROE从fin宽表提取
        if fin_wide is not None and not fin_wide.empty:
            fin_q_date_col = self._find_col(fin_wide, '日期')
            fin_q_rev_growth_col = self._find_col(fin_wide, '主营业务收入增长率(%)')
            fin_q_profit_growth_col = self._find_col(fin_wide, '净利润增长率(%)')
            fin_q_asset_turnover_col = self._find_col(fin_wide, '总资产周转率(次)')
            fin_q_current_ratio_col = self._find_col(fin_wide, '流动比率')
            fin_q_quick_ratio_col = self._find_col(fin_wide, '速动比率')
            fin_q_roe_col = self._find_col(fin_wide, '净资产收益率(%)')  # ★ 新增
        col_capex_cash_q = self._find_col(cash, '购建固定资产等支付的现金') if cash is not None and not cash.empty else None

        # ===== 第一轮收集：按期间收集所有原始值 =====
        raw_periods = []  # 每个元素是一个dict，包含该期的所有原始指标

        for idx, (period_label, bal_row) in enumerate(bal_rows):
            # 找对应期间的prof和cash行
            prof_row = None
            for pl, pr in prof_rows:
                if pl == period_label:
                    prof_row = pr
                    break
            cash_row = None
            for cl, cr in cash_rows:
                if cl == period_label:
                    cash_row = cr
                    break

            # 从abstract获取ROE
            roe_val = None
            if is_abstract:
                year = period_label[:4]
                q = period_label[-2:]
                q_map = {'Q1': '0331', 'Q2': '0630', 'Q3': '0930'}
                suffix = q_map.get(q, '')
                target_period = f"{year}{suffix}"
                if target_period in abstract_period_cols:
                    for _, fin_row in fin.iterrows():
                        indicator_name = str(fin_row.get('指标', '')).strip()
                        category = str(fin_row.get('选项', '')).strip()
                        if indicator_name == '净资产收益率(ROE)':
                            roe_val = self._safe_float(fin_row.get(target_period))
                            break
                        elif indicator_name == '净资产收益率' and category == '常用指标':
                            roe_val = self._safe_float(fin_row.get(target_period))
                            break

            # --- 资产负债表时点值（无需差分）---
            total_assets = self._safe_float(bal_row.get(col_total_assets)) if col_total_assets else None
            total_liab = self._safe_float(bal_row.get(col_total_liab)) if col_total_liab else None
            equity = self._safe_float(bal_row.get(col_equity)) if col_equity else None

            cash_val = self._safe_float(bal_row.get(col_cash_bal)) or 0 if col_cash_bal else 0
            trading_fin_val = self._safe_float(bal_row.get(col_trading_fin)) or 0 if col_trading_fin else 0
            st_debt = self._safe_float(bal_row.get(col_st_debt)) or 0 if col_st_debt else 0
            lt_debt = self._safe_float(bal_row.get(col_lt_debt)) or 0 if col_lt_debt else 0
            bonds = self._safe_float(bal_row.get(col_bonds)) or 0 if col_bonds else 0
            due_nc = self._safe_float(bal_row.get(col_due_nc)) or 0 if col_due_nc else 0
            interest_debt = st_debt + lt_debt + bonds + due_nc

            # --- 利润表/现金流量表 原始值（可能为累计值，标记为_raw）---
            netp_raw = None
            rev_raw = None
            rd_raw = None
            ocf_raw = None

            if prof_row:
                if col_netp_prof:
                    netp_raw = self._safe_float(prof_row.get(col_netp_prof))
                if col_rev_prof:
                    rev_raw = self._safe_float(prof_row.get(col_rev_prof))
                if col_rd_prof:
                    rd_raw = self._safe_float(prof_row.get(col_rd_prof))
            if cash_row and col_ocf_cash:
                ocf_raw = self._safe_float(cash_row.get(col_ocf_cash))
            # 新增4个指标所需的原始值
            div_raw = None
            fin_exp_raw = None  # 现金流量表FINANCE_EXPENSE（用于分红计算）
            fin_expense_p_raw = None  # 利润表财务费用
            pretax_raw = None
            # BS时点值（应收/应付）
            ar_total_raw = None
            ap_total_raw = None

            if cash_row:
                if col_div_cash_q:
                    div_raw = self._safe_float(cash_row.get(col_div_cash_q))
                if col_fin_exp_q:
                    fin_exp_raw = self._safe_float(cash_row.get(col_fin_exp_q))
            if prof_row:
                if col_fin_expense_prof_q:
                    fin_expense_p_raw = self._safe_float(prof_row.get(col_fin_expense_prof_q))
                if col_pretax_q:
                    pretax_raw = self._safe_float(prof_row.get(col_pretax_q))
            # 应收/应付（BS时点值，无需差分）
            if total_assets and total_assets > 0:
                ar_v_t = self._safe_float(bal_row.get(col_ar_bal_q)) if col_ar_bal_q else 0
                nr_v_t = self._safe_float(bal_row.get(col_nr_bal_q)) if col_nr_bal_q else 0
                ar_total_raw = (ar_v_t or 0) + (nr_v_t or 0)
                ap_v_t2 = self._safe_float(bal_row.get(col_ap_bal_q)) if col_ap_bal_q else 0
                np_v_t2 = self._safe_float(bal_row.get(col_np_bal_q)) if col_np_bal_q else 0
                ap_total_raw = (ap_v_t2 or 0) + (np_v_t2 or 0)

            raw_periods.append({
                'idx': idx,
                'period': period_label,
                'year': period_label[:4],
                'q': period_label[-2:],
                # BS时点值
                'total_assets': total_assets,
                'total_liab': total_liab,
                'equity': equity,
                'cash_val': cash_val,
                'trading_fin_val': trading_fin_val,
                'interest_debt': interest_debt,
                # P&L/CF 原始累计值
                'netp_raw': netp_raw,
                'rev_raw': rev_raw,
                'rd_raw': rd_raw,
                'ocf_raw': ocf_raw,
                # ROE (from abstract)
                'roe_val': roe_val,
                # 新增4个指标原始值
                'div_raw': div_raw,
                'fin_exp_raw': fin_exp_raw,
                'fin_expense_p_raw': fin_expense_p_raw,
                'pretax_raw': pretax_raw,
                'ar_total_raw': ar_total_raw,
                'ap_total_raw': ap_total_raw,
                # ★ 保存原始行引用（用于OCF/CAPEX/FCF绝对值提取）
                'cash_row': cash_row,
                'prof_row': prof_row,
                'bal_row': bal_row,
            })

        # ===== 第二轮：P1修复 — 累计值差分还原为单季度值 =====
        # A股规则：Q1=单季度(已正确), Q2=1-6月累计, Q3=1-9月累计
        # 差分仅在同年内有效；跨年时Q1已是新年度起点
        def _diff(curr, prev):
            """安全的差分：任一为None则返回None"""
            if curr is not None and prev is not None:
                return curr - prev
            return None

        for i, d in enumerate(raw_periods):
            q = d['q']
            if q == 'Q1':
                # Q1已经是单季度值
                d['netp_sq'] = d['netp_raw']
                d['rev_sq'] = d['rev_raw']
                d['rd_sq'] = d['rd_raw']
                d['ocf_sq'] = d['ocf_raw']
                d['div_sq'] = d['div_raw']
                d['fin_expense_p_sq'] = d['fin_expense_p_raw']
                d['pretax_sq'] = d['pretax_raw']
            elif i > 0 and raw_periods[i - 1]['year'] == d['year']:
                # 同年非Q1季度：差分得到单季度值
                prev = raw_periods[i - 1]
                d['netp_sq'] = _diff(d['netp_raw'], prev['netp_raw'])
                d['rev_sq'] = _diff(d['rev_raw'], prev['rev_raw'])
                d['rd_sq'] = _diff(d['rd_raw'], prev['rd_raw'])
                d['ocf_sq'] = _diff(d['ocf_raw'], prev['ocf_raw'])
                d['div_sq'] = _diff(d['div_raw'], prev.get('div_raw'))
                d['fin_expense_p_sq'] = _diff(d['fin_expense_p_raw'], prev.get('fin_expense_p_raw'))
                d['pretax_sq'] = _diff(d['pretax_raw'], prev.get('pretax_raw'))
            else:
                # 无法差分（跨年或首个元素非Q1）：使用原始值并保留标记
                d['netp_sq'] = d['netp_raw']
                d['rev_sq'] = d['rev_raw']
                d['rd_sq'] = d['rd_raw']
                d['ocf_sq'] = d['ocf_raw']
                d['div_sq'] = d.get('div_raw')
                d['fin_expense_p_sq'] = d.get('fin_expense_p_raw')
                d['pretax_sq'] = d.get('pretax_raw')

        # ===== 第三轮：用单季度值计算衍生指标 =====
        periods, roe_vals, npm_vals, ocf_np_vals = [], [], [], []
        debt_vals, equity_vals, int_debt_vals, cc_vals, cash_r_vals = [], [], [], [], []
        prod_vals, rd_sq_vals = [], []
        # 新增4个缺失指标季报趋势收集
        dividend_ocf_qvals, finance_cost_qvals, interest_cov_qvals, receivables_payables_qvals = [], [], [], []
        # 成长性基础指标季报趋势收集（单季度值，亿元）
        revenue_qvals, net_profit_qvals = [], []
        # analysis_indicator现成指标季报趋势收集
        revenue_growth_qvals, profit_growth_qvals = [], []   # 增长率(%)
        asset_turnover_qvals, current_ratio_qvals, quick_ratio_qvals = [], [], []  # 周转率/流动性
        ocf_abs_qvals, capex_qvals, fcf_qvals = [], [], []    # 现金流绝对值(亿元)

        for d in raw_periods:
            periods.append(d['period'])
            roe_vals.append(d['roe_val'])

            # BS指标（时点值，直接用）
            total_assets = d['total_assets']
            total_liab = d['total_liab']
            equity_val = d['equity']
            cash_val = d['cash_val']
            trading_fin_val = d['trading_fin_val']
            interest_debt = d['interest_debt']

            debt_ratio = total_liab / total_assets * 100 if (total_assets and total_assets > 0 and total_liab is not None) else None
            interest_debt_ratio = interest_debt / total_assets * 100 if (total_assets and total_assets > 0) else None
            cash_coverage = (cash_val + trading_fin_val) / interest_debt if interest_debt > 0 else None
            cash_ratio = cash_val / total_assets * 100 if (total_assets and total_assets > 0) else None

            debt_vals.append(debt_ratio)
            equity_vals.append(equity_val)
            int_debt_vals.append(interest_debt_ratio)
            cc_vals.append(cash_coverage)
            cash_r_vals.append(cash_ratio)

            # 生产资产占比（BS时点值）
            bal_row_idx = d['idx']
            _, bal_row_ref = bal_rows[bal_row_idx]
            prod_asset = 0
            for key, col in prod_cols.items():
                if col:
                    prod_asset += self._safe_float(bal_row_ref.get(col, 0)) or 0
            prod_ratio = prod_asset / total_assets * 100 if (total_assets and total_assets > 0) else None
            prod_vals.append(prod_ratio)

            # P1修复：用**单季度值**计算净利率（而非累计值）
            netp_sq = d.get('netp_sq')
            rev_sq = d.get('rev_sq')
            npm = None
            if netp_sq is not None and rev_sq and rev_sq != 0:
                npm = netp_sq / rev_sq * 100
            npm_vals.append(npm)

            # P1修复：用**单季度值**计算OCF/净利润（而非累计值）
            ocf_sq = d.get('ocf_sq')
            ocf_np = None
            if ocf_sq is not None and netp_sq and netp_sq != 0:
                ocf_np = ocf_sq / netp_sq
            ocf_np_vals.append(ocf_np)

            # P1修复：研发费用使用**单季度绝对值**
            rd_sq_vals.append(d.get('rd_sq'))

            # 成长性基础指标（单季度值，亿元）
            rev_sq = d.get('rev_sq')
            netp_sq = d.get('netp_sq')
            revenue_qvals.append(round(rev_sq / 1e8, 2) if rev_sq is not None else None)
            net_profit_qvals.append(round(netp_sq / 1e8, 2) if netp_sq is not None else None)

            # ===== 新增4个缺失指标的季报趋势计算 =====
            # 1) 分红/OCF比（使用单季度差分值）
            dividend_ocf_qv = None
            div_sq = d.get('div_sq')
            ocf_sq = d.get('ocf_sq')
            if div_sq is not None and ocf_sq and ocf_sq != 0:
                fin_exp_q_raw = d.get('fin_exp_raw')  # CF表的财务费用不需要差分(近似)
                interest_exp_q = max(0, fin_exp_q_raw or 0)
                div_approx_q = max(0, div_sq - interest_exp_q)
                dividend_ocf_qv = round(div_approx_q / ocf_sq * 100, 2)

            # 2) 融资成本率 = 财务费用(利润表单季度) / 有息负债 × 100
            finance_cost_qv = None
            interest_debt = d['interest_debt']
            fe_p_sq = d.get('fin_expense_p_sq')
            if interest_debt and interest_debt > 0 and fe_p_sq is not None and fe_p_sq > 0:
                finance_cost_qv = round(fe_p_sq / interest_debt * 100, 2)
            elif fe_p_sq is not None and fe_p_sq < 0:
                finance_cost_qv = -9999  # 利息收入型

            # 3) 利息保障倍数 = (税前利润+财务费用) / 财务费用（均用单季度）
            interest_cov_qv = None
            if fe_p_sq is not None and fe_p_sq > 0:
                pt_sq = d.get('pretax_sq')
                if pt_sq is not None:
                    interest_cov_qv = round((pt_sq + fe_p_sq) / fe_p_sq, 2)
            elif fe_p_sq is not None and fe_p_sq < 0:
                interest_cov_qv = -9999

            # 4) 应收/应付比（BS时点值，直接用raw即可）
            receivables_payables_qv = None
            ar_total = d.get('ar_total_raw')
            ap_total = d.get('ap_total_raw')
            if ar_total and ar_total > 0 and ap_total and ap_total > 0:
                receivables_payables_qv = round(ar_total / ap_total, 2)

            dividend_ocf_qvals.append(dividend_ocf_qv)
            finance_cost_qvals.append(finance_cost_qv)
            interest_cov_qvals.append(interest_cov_qv)
            receivables_payables_qvals.append(receivables_payables_qv)

            # ===== 从 analysis_indicator (fin) 宽表提取现成指标（季报） =====
            fin_row_for_quarter = None
            if fin_wide is not None and not fin_wide.empty and fin_q_date_col:
                q_period = d['period']  # e.g. "2025Q1"
                q_year = q_period[:4]
                q_label = q_period[-2:]  # Q1/Q2/Q3/Q4
                q_date_map = {'Q1': '03-31', 'Q2': '06-30', 'Q3': '09-30', 'Q4': '12-31'}
                q_date_suffix = q_date_map.get(q_label, '')
                if q_date_suffix:
                    for _, fr in fin_wide.iterrows():
                        fd = str(fr[fin_q_date_col])[:10]
                        if fd == f"{q_year}-{q_date_suffix}":  # ★ 精确匹配，防止跨期错位
                            fin_row_for_quarter = fr
                            break

            # ★ ROE fallback: 季报也从fin宽表提取
            if roe_val is None and fin_row_for_quarter is not None and fin_q_roe_col:
                roe_val = self._safe_float(fin_row_for_quarter.get(fin_q_roe_col))

            # 增长率/周转率/流动性（直接从fin宽表取）
            rev_growth_qy = (self._safe_float(fin_row_for_quarter.get(fin_q_rev_growth_col))
                             if fin_row_for_quarter is not None and fin_q_rev_growth_col else None)
            profit_growth_qy = (self._safe_float(fin_row_for_quarter.get(fin_q_profit_growth_col))
                                if fin_row_for_quarter is not None and fin_q_profit_growth_col else None)
            asset_turnover_qy = (self._safe_float(fin_row_for_quarter.get(fin_q_asset_turnover_col))
                                 if fin_row_for_quarter is not None and fin_q_asset_turnover_col else None)
            current_ratio_qy = (self._safe_float(fin_row_for_quarter.get(fin_q_current_ratio_col))
                                if fin_row_for_quarter is not None and fin_q_current_ratio_col else None)
            quick_ratio_qy = (self._safe_float(fin_row_for_quarter.get(fin_q_quick_ratio_col))
                              if fin_row_for_quarter is not None and fin_q_quick_ratio_col else None)

            # 现金流绝对值（亿元）— 从每个周期对应的cash_row取（而非外层残留变量）
            cash_row_for_q = d.get('cash_row')
            ocf_abs_qy = round(self._safe_float(cash_row_for_q.get(col_ocf_cash)) / 1e8, 2) if cash_row_for_q and col_ocf_cash else None
            capex_abs_qy = round(self._safe_float(cash_row_for_q.get(col_capex_cash_q)) / 1e8, 2) if cash_row_for_q and col_capex_cash_q else None
            fcf_abs_qy = round(ocf_abs_qy - capex_abs_qy, 2) if (ocf_abs_qy is not None and capex_abs_qy is not None) else None

            # 收集到列表
            revenue_growth_qvals.append(rev_growth_qy)
            profit_growth_qvals.append(profit_growth_qy)
            asset_turnover_qvals.append(asset_turnover_qy)
            current_ratio_qvals.append(current_ratio_qy)
            quick_ratio_qvals.append(quick_ratio_qy)
            ocf_abs_qvals.append(ocf_abs_qy)
            capex_qvals.append(capex_abs_qy)
            fcf_qvals.append(fcf_abs_qy)

        if periods:
            r.roe_trend_years = periods
            r.roe_trend = roe_vals
            r.net_profit_margin_parent_trend_years = periods
            r.net_profit_margin_parent_trend = npm_vals
            r.ocf_to_netprofit_trend_years = periods
            r.ocf_to_netprofit_trend = ocf_np_vals
            r.debt_ratio_trend_years = periods
            r.debt_ratio_trend = debt_vals
            r.equity_trend_years = periods
            r.equity_trend = equity_vals
            r.interest_debt_trend_years = periods
            r.interest_debt_trend = int_debt_vals
            r.cash_coverage_trend_years = periods
            r.cash_coverage_trend = cc_vals
            r.cash_ratio_trend_years = periods
            r.cash_ratio_trend = cash_r_vals
            r.production_asset_trend = prod_vals
            r.production_asset_trend_years = periods
            r.r_d_expense_trend_years = periods
            r.r_d_expense_trend = rd_sq_vals
            # 新增4个缺失指标季报趋势赋值
            r.dividend_ocf_ratio_trend_years = periods
            r.dividend_ocf_ratio_trend = dividend_ocf_qvals
            r.finance_cost_ratio_trend_years = periods
            r.finance_cost_ratio_trend = finance_cost_qvals
            r.interest_coverage_trend_years = periods
            r.interest_coverage_trend = interest_cov_qvals
            r.receivables_to_payables_trend_years = periods
            r.receivables_to_payables_trend = receivables_payables_qvals
            # 成长性基础指标（季报）
            r.revenue_trend_years = periods
            r.revenue_trend = revenue_qvals
            r.net_profit_trend_years = periods
            r.net_profit_trend = net_profit_qvals
            # analysis_indicator现成指标（季报）
            r.revenue_growth_trend_years = periods; r.revenue_growth_trend = revenue_growth_qvals
            r.profit_growth_trend_years = periods; r.profit_growth_trend = profit_growth_qvals
            r.asset_turnover_trend_years = periods; r.asset_turnover_trend = asset_turnover_qvals
            r.current_ratio_trend_years = periods; r.current_ratio_trend = current_ratio_qvals
            r.quick_ratio_trend_years = periods; r.quick_ratio_trend = quick_ratio_qvals
            r.ocf_trend_years = periods; r.ocf_trend = ocf_abs_qvals
            r.capex_trend_years = periods; r.capex_trend = capex_qvals
            r.fcf_trend_years = periods; r.fcf_trend = fcf_qvals

    def _analyze_asset_quality(self, r, bal, prof, fin, row_data: Dict = None):
        """第二部分：资产质量分析 - 增强版"""
        if row_data is not None:
            latest = row_data
        else:
            latest = self._get_latest_row(bal)
        if latest is None:
            return
        
        col_total_assets = self._find_col(bal, '资产合计')
        total_assets = self._safe_float(latest.get(col_total_assets, 1)) or 1 if col_total_assets else 1
        
        # ===== T2.1-T2.4：生产资产 = 固定资产+在建工程+工程物资+无形资产 =====
        prod_asset = 0
        for key in ['固定资产', '在建工程', '工程物资', '无形资产']:
            col = self._find_col(bal, key)
            if col:
                prod_asset += self._safe_float(latest.get(col, 0)) or 0
        
        r.production_asset_ratio = prod_asset / total_assets * 100 if total_assets > 0 else None
        
        # ===== T2.3：生产资产ROE = 归母净利润/生产资产 =====
        prof_row_latest = self._get_annual_row(prof) if prof is not None and not prof.empty else None
        col_netp_latest = self._find_col(prof, '归母净利润') if prof is not None and not prof.empty else None
        if col_netp_latest and prof_row_latest:
            net_profit_parent = self._safe_float(prof_row_latest.get(col_netp_latest, 0)) or 0
            if net_profit_parent and prod_asset > 0:
                r.production_asset_roe = net_profit_parent / prod_asset * 100
        
        # ===== T2.5：资产结构趋势（3-5年，仅年报）=====
        if not bal.empty and len(bal) >= 3:
            r.production_asset_trend = []
            r.production_asset_trend_years = []
            # 过滤仅年报（12-31结尾），取最近5年
            date_col = self._find_col(bal, 'REPORT_DATE')
            if date_col:
                bal_annual = bal[bal[date_col].astype(str).str[:10].str.endswith('12-31')]
            else:
                bal_annual = bal
            for i in range(min(5, len(bal_annual))):
                idx = len(bal_annual) - 1 - i
                if idx >= 0:
                    row = bal_annual.iloc[idx]
                    # 提取年份
                    if date_col and date_col in row.index:
                        year_str = str(row[date_col])[:4]
                    else:
                        year_str = f'T-{i}'
                    pa = 0
                    for key in ['固定资产', '在建工程', '工程物资', '无形资产']:
                        col = self._find_col(bal, key)
                        if col and col in row.index:
                            pa += self._safe_float(row.get(col, 0)) or 0
                    ta = self._safe_float(row.get(col_total_assets, 1)) or 1 if col_total_assets and col_total_assets in row.index else 1
                    r.production_asset_trend.append(pa / ta * 100 if ta > 0 else None)
                    r.production_asset_trend_years.append(year_str)
        
        # ===== T2.6-T2.7：应收深度分析（区分应收账款和应收票据）=====
        col_ar = self._find_col(bal, '应收账款')
        col_nr = self._find_col(bal, '应收票据')
        ar_val = self._get_val(latest, col_ar)
        nr_val = self._get_val(latest, col_nr)
        # 计算时用0兜底（分母不允许None），但报告字段保留None
        ar_val_calc = ar_val if ar_val is not None else 0
        nr_val_calc = nr_val if nr_val is not None else 0

        total_ar = ar_val_calc + nr_val_calc
        r.receivables_ratio = total_ar / total_assets * 100 if total_assets > 0 else None

        r.receivables_detail = {
            '应收账款': ar_val,       # None表示列未找到，报告显示"未披露"
            '应收票据': nr_val,       # None表示列未找到或值为nan
            '应收总额': total_ar,
            '应收账款占比': ar_val_calc / total_assets * 100 if total_assets > 0 else None,
            # 应收票据占比：列未找到(nan)时显示N/A，不计算为0.00%
            '应收票据占比': nr_val / total_assets * 100 if (nr_val is not None and total_assets > 0) else None,
            # 票据占比：列未找到时不计算
            '票据占比': nr_val / total_ar * 100 if (nr_val is not None and total_ar > 0) else None,
        }
        
        # 风险判断
        if ar_val is not None and ar_val / total_assets * 100 > 20:
            r.red_flags.append('应收账款占比偏高，需关注回款能力')

        # ===== T2.8：应付账款占比 & 应收/应付 =====
        # 应付账款占比 = (应付账款 + 应付票据) / 总资产
        col_ap = self._find_col(bal, '应付账款')
        col_np = self._find_col(bal, '应付票据')
        ap_val = self._get_val(latest, col_ap)
        np_val = self._get_val(latest, col_np)
        ap_val_calc = (ap_val or 0) + (np_val or 0)
        r.payables_ratio = ap_val_calc / total_assets * 100 if total_assets > 0 else None

        # 应收/应付 = (应收账款+应收票据) / (应付账款+应付票据)
        if ap_val_calc > 0 and ar_val is not None and ar_val > 0:
            r.receivables_to_payables = ar_val / ap_val_calc
        else:
            r.receivables_to_payables = None

        # ===== T2.10：非主业资产 = (投资性房地产+交易性金融资产)/总资产 =====
        col_invest_real = self._find_col(bal, '投资性房地产')
        invest_real = self._get_val(latest, col_invest_real)  # None=列未找到
        invest_real_calc = invest_real if invest_real is not None else 0
        col_trading_fin2 = self._find_col(bal, '交易性金融资产')
        trading_fin2 = self._get_val(latest, col_trading_fin2)
        trading_fin2_calc = trading_fin2 if trading_fin2 is not None else 0
        r.non_core_asset_ratio = (invest_real_calc + trading_fin2_calc) / total_assets * 100 if total_assets > 0 else None
        
        # ===== T2.12：长期待摊费用检查 =====
        col_long_defer = self._find_col(bal, '长期待摊费用')
        long_defer = self._get_val(latest, col_long_defer)  # None=列未找到
        long_defer_calc = long_defer if long_defer is not None else 0
        long_defer_ratio = long_defer_calc / total_assets * 100 if total_assets > 0 else None
        
        if long_defer_ratio and long_defer_ratio > 2:
            r.red_flags.append(f'长期待摊费用占{long_defer_ratio:.2f}%，需关注是否存在费用资本化')
        
        # ===== T2.13：存货分析 =====
        col_inventory = self._find_col(bal, '存货')
        inventory = self._get_val(latest, col_inventory)  # None=列未找到
        inventory_calc = inventory if inventory is not None else 0
        r.inventory_ratio = inventory_calc / total_assets * 100 if total_assets > 0 else None
        
        if r.inventory_ratio and r.inventory_ratio > 30:
            r.red_flags.append(f'存货占比{r.inventory_ratio:.1f}%，需关注存货积压风险')
        elif r.inventory_ratio and r.inventory_ratio < 5:
            r.strengths.append('存货占比极低，经营效率高')
        
        # ===== T2.14：在建工程检查 =====
        col_construct = self._find_col(bal, '在建工程')
        construct = self._get_val(latest, col_construct)  # None=列未找到
        construct_calc = construct if construct is not None else 0
        construct_ratio = construct_calc / total_assets * 100 if total_assets > 0 else None

        # 在建工程/固定资产比（判断在建工程是否异常）
        col_fixed = self._find_col(bal, '固定资产')
        fixed = self._safe_float(latest.get(col_fixed, 0)) or 0 if col_fixed else 0
        if fixed > 0:
            construct_to_fixed = construct_calc / fixed * 100
            if construct_to_fixed > 50:
                r.red_flags.append(f'在建工程/固定资产={construct_to_fixed:.1f}%，可能存在延迟转固')
            elif construct_to_fixed > 20:
                r.strengths.append('在建工程规模适中，产能扩张有序')
        
        # 资产质量详情
        r.asset_quality_detail = {
            '生产资产': prod_asset,
            '生产资产占比': r.production_asset_ratio,
            '生产资产ROE': r.production_asset_roe,
            '应收账款': ar_val,
            '应收票据': nr_val,
            '存货': inventory,
            '存货占比': r.inventory_ratio,
            '在建工程': construct,
            '在建工程占比': construct_ratio,
            '长期待摊费用': long_defer,
            '长期待摊费用占比': long_defer_ratio,
            '非主业资产(投资性房地产)': invest_real,
            '非主业资产占比': r.non_core_asset_ratio,
        }
        
        # 商誉
        col_goodwill = self._find_col(bal, '商誉')
        if col_goodwill:
            goodwill = self._safe_float(latest.get(col_goodwill, 0)) or 0
            r.goodwill_ratio = goodwill / total_assets * 100 if total_assets > 0 else None

        # ===== N12: 其他应收款同比变化 =====
        # 修复：必须筛选年报行进行同比，避免季报/年报混用导致失真
        col_other_rece = self._find_col(bal, '其他应收款')
        if col_other_rece and not bal.empty and len(bal) >= 2:
            date_col_bal = self._find_col(bal, 'REPORT_DATE')
            if date_col_bal:
                bal_annual = bal[bal[date_col_bal].astype(str).str[:10].str.endswith('12-31')]
            else:
                bal_annual = bal
            if len(bal_annual) >= 2:
                curr_other_rece = self._safe_float(bal_annual.iloc[-1].get(col_other_rece, 0)) or 0
                prev_other_rece = self._safe_float(bal_annual.iloc[-2].get(col_other_rece, 0)) or 0
                if prev_other_rece != 0:
                    r.other_rece_change = round((curr_other_rece - prev_other_rece) / abs(prev_other_rece) * 100, 2)
                    if r.other_rece_change > 50:
                        r.red_flags.append(f'其他应收款同比变化{r.other_rece_change:.1f}%，增速异常')

        # ===== N13: 非主业资产明细（扩展）=====
        non_core_items = {}
        for key in ['投资性房地产', '交易性金融资产', '其他权益工具投资', '长期股权投资', '债权投资', '其他债权投资']:
            col = self._find_col(bal, key)
            if col:
                val = self._safe_float(latest.get(col, 0)) or 0
                if val > 0:
                    non_core_items[key] = val
        r.non_core_asset_detail = non_core_items

        # ===== N14: 剔除商誉后真实负债率 =====
        goodwill_val = self._safe_float(latest.get(col_goodwill, 0)) or 0 if col_goodwill else 0
        total_liab_col = self._find_col(bal, '负债合计')
        total_liab_val = self._safe_float(latest.get(total_liab_col, 0)) or 0 if total_liab_col else 0
        assets_ex_goodwill = total_assets - (goodwill_val or 0)
        if assets_ex_goodwill > 0:
            r.real_debt_ratio_ex_goodwill = round(total_liab_val / assets_ex_goodwill * 100, 2)

        # ===== N16: 存货vs营收增速背离 =====
        # 直接使用abstract中已有的增长率指标进行对比
        if self._is_abstract_format(fin):
            period_cols_fin = self._get_period_cols(fin)
            if period_cols_fin:
                annual_cols_fin = [c for c in period_cols_fin if c.endswith('1231')]
                if len(annual_cols_fin) >= 2:
                    # 取最新两个年报的增长率指标（abstract格式已有）
                    rev_g_curr = self._get_indicator_value(fin, '营业总收入增长率', annual_cols_fin[0])
                    rev_g_prev = self._get_indicator_value(fin, '营业总收入增长率', annual_cols_fin[1])
                    inv_g_curr = self._get_indicator_value(fin, '存货增长率', annual_cols_fin[0])
                    inv_g_prev = self._get_indicator_value(fin, '存货增长率', annual_cols_fin[1])
                    
                    if inv_g_curr is not None and inv_g_prev is not None:
                        divergence = abs(inv_g_curr - rev_g_curr)
                        if divergence > 20:
                            r.inventory_revenue_diverge = f'存货增速{inv_g_curr:.1f}% vs 营收增速{rev_g_curr:.1f}%，背离{divergence:.1f}pp'
                            r.red_flags.append(f'存货与营收增速背离{divergence:.1f}pp，需关注')
        elif not bal.empty and len(bal) >= 2 and prof is not None and not prof.empty:
            col_rev_prof = self._find_col(prof, '营业收入')
            if col_rev_prof and col_inventory:
                # 从三大报表取最新两个年报行来计算
                date_col_bal = self._find_col(bal, 'REPORT_DATE')
                date_col_prof = self._find_col(prof, 'REPORT_DATE')
                if date_col_bal and date_col_prof:
                    bal_annual = bal[bal[date_col_bal].astype(str).str.endswith('12-31')]
                    prof_annual = prof[prof[date_col_prof].astype(str).str.endswith('12-31')]
                    if len(bal_annual) >= 2 and len(prof_annual) >= 2:
                        curr_inv = self._safe_float(bal_annual.iloc[-1].get(col_inventory, 0)) or 0
                        prev_inv = self._safe_float(bal_annual.iloc[-2].get(col_inventory, 0)) or 0
                        curr_rev = self._safe_float(prof_annual.iloc[-1].get(col_rev_prof, 0)) or 0
                        prev_rev = self._safe_float(prof_annual.iloc[-2].get(col_rev_prof, 0)) or 0
                        if prev_inv != 0 and curr_rev and prev_rev != 0:
                            inv_growth = (curr_inv - prev_inv) / abs(prev_inv) * 100
                            rev_growth = (curr_rev - prev_rev) / abs(prev_rev) * 100
                            divergence = abs(inv_growth - rev_growth)
                            if divergence > 20:
                                r.inventory_revenue_diverge = f'存货增速{inv_growth:.1f}% vs 营收增速{rev_growth:.1f}%，背离{divergence:.1f}pp'
                                r.red_flags.append(f'存货与营收增速背离{divergence:.1f}pp，需关注')

        # ===== N19: 研发支出分析 =====
        # 修复：研发费用从利润表(prof)获取，开发支出从资产负债表(bal)获取
        if prof is not None and not prof.empty:
            col_rd_exp = self._find_col(prof, '研发费用')
            col_dev_exp = self._find_col(bal, '开发支出')
            # 研发费用必须从利润表的行获取
            prof_row = self._get_latest_row(prof)
            rd_expense = self._get_val(prof_row, col_rd_exp) if prof_row else None
            dev_expenditure = self._get_val(latest, col_dev_exp)
            # 计算用实际值（含0），None=未找到
            rd_calc = rd_expense if rd_expense is not None else 0
            dev_calc = dev_expenditure if dev_expenditure is not None else 0
            total_rd = rd_calc + dev_calc
            cap_rate = None
            if total_rd > 0:
                cap_rate = round(dev_calc / total_rd * 100, 2)
                r.r_d_capitalize_ratio = cap_rate
            category = 'N/A'
            if cap_rate is not None:
                if cap_rate < 30:
                    category = '保守'
                elif cap_rate <= 60:
                    category = '适中'
                else:
                    category = '激进'
            r.r_d_analysis = {
                '研发费用': rd_expense,
                '开发支出(资本化)': dev_expenditure,
                '研发总投入': total_rd,
                '资本化率': cap_rate,
                '策略分类': category,
            }

    def _calc_dividend_from_statements(self, r, prof, cash):
        """从三大报表计算分红数据（当abstract无分红指标时的fallback）"""
        # 获取年报行
        prof_annual = self._get_annual_row(prof)
        cash_annual = self._get_annual_row(cash)
        if not prof_annual or not cash_annual:
            return

        col_netp = self._find_col(prof, '归母净利润')
        col_div_cash = self._find_col(cash, '分配股利偿付利息支付的现金')
        col_fin_exp_cash = self._find_col(cash, 'FINANCE_EXPENSE')

        if not col_netp or not col_div_cash:
            return

        netp = self._safe_float(prof_annual.get(col_netp))
        div_cash = self._safe_float(cash_annual.get(col_div_cash))
        fin_exp = self._safe_float(cash_annual.get(col_fin_exp_cash)) if col_fin_exp_cash else 0

        if not netp or netp == 0 or div_cash is None:
            return

        # 分红近似 = 分配股利利润支付现金 - 财务费用(利息部分)
        interest_exp = max(0, fin_exp or 0)
        dividend_approx = max(0, div_cash - interest_exp)

        if dividend_approx > 0:
            r.dividend_payout = round(dividend_approx / netp * 100, 2)
            r.dividend_policy = {
                '分红金额(近似)': dividend_approx,
                '归母净利润': netp,
                '分红支付率(近似)': r.dividend_payout,
                '数据来源': '现金流量表-分配股利偿付利息支付的现金',
            }

        # 计算分红/经营现金流比
        col_ocf = self._find_col(cash, '经营活动现金流量净额')
        ocf_val = self._safe_float(cash_annual.get(col_ocf)) if col_ocf else None
        if dividend_approx and ocf_val and ocf_val != 0:
            r.dividend_ocf_ratio = round(dividend_approx / ocf_val * 100, 2)

    def _analyze_profitability(self, r, fin, prof, cash, bal,
                                fin_row: Dict = None, prof_row: Dict = None,
                                cash_row: Dict = None, bal_row: Dict = None,
                                target_period: str = None):
        """第三部分：盈利与现金流分析 - 增强版"""

        # 处理stock_financial_abstract格式（行是指标，列是报告期）
        if self._is_abstract_format(fin):
            # stock_financial_abstract格式
            self._analyze_profitability_from_abstract(r, fin, prof, cash, bal, target_period)
            # 补充：如果abstract没匹配到分红数据，从三大报表计算
            if r.dividend_payout is None and prof is not None and not prof.empty and cash is not None and not cash.empty:
                self._calc_dividend_from_statements(r, prof, cash)
            return

        # 原有逻辑（标准格式）
        if fin_row is not None:
            latest_fin = fin_row
        else:
            latest_fin = self._get_latest_row(fin)

        if prof_row is not None:
            latest_prof = prof_row
        else:
            latest_prof = self._get_latest_row(prof)

        if cash_row is not None:
            latest_cash = cash_row
        else:
            latest_cash = self._get_latest_row(cash)

        if bal_row is not None:
            latest_bal = bal_row
        else:
            latest_bal = self._get_latest_row(bal)

        # ROE、净利率、毛利率
        if latest_fin:
            col_roe = self._find_col(fin, '净资产收益率')
            col_nm = self._find_col(fin, '净利率')
            col_gm = self._find_col(fin, '毛利率')

            if col_roe:
                r.roe = self._safe_float(latest_fin.get(col_roe))
            if col_nm:
                r.net_margin = self._safe_float(latest_fin.get(col_nm))
            if col_gm:
                r.gross_margin = self._safe_float(latest_fin.get(col_gm))

        # 经营现金流/归母净利润（统一口径：归母净利润）
        if latest_cash and latest_prof:
            col_ocf = self._find_col(cash, '经营活动现金流量净额')
            col_netp = self._find_col(prof, '归母净利润')

            if col_ocf and col_netp:
                ocf = self._safe_float(latest_cash.get(col_ocf))
                netp = self._safe_float(latest_prof.get(col_netp))
                if netp and netp != 0 and ocf is not None:
                    r.ocf_to_netprofit = ocf / netp

        # 归母净利率 = 归母净利润 / 营业收入
        if latest_prof:
            col_netp = self._find_col(prof, '归母净利润')
            col_rev = self._find_col(prof, '营业收入')
            if col_netp and col_rev:
                netp = self._safe_float(latest_prof.get(col_netp))
                rev = self._safe_float(latest_prof.get(col_rev))
                if rev and rev != 0 and netp is not None:
                    r.net_profit_margin_parent = netp / rev * 100

        # 交易性金融资产分析
        if latest_bal:
            col_trading_fin = self._find_col(bal, '交易性金融资产')
            trading_fin = self._safe_float(latest_bal.get(col_trading_fin, 0)) or 0 if col_trading_fin else 0
            col_total_assets = self._find_col(bal, '资产合计')
            total_assets = self._safe_float(latest_bal.get(col_total_assets, 1)) or 1 if col_total_assets else 1

            r.fin_assets_ratio = trading_fin / total_assets * 100 if total_assets > 0 else None

            r.fin_assets_detail = {
                '交易性金融资产': trading_fin,
                '占总资产比例': r.fin_assets_ratio,
                '评价': '偏高' if r.fin_assets_ratio and r.fin_assets_ratio > 10 else '正常',
            }

            if r.fin_assets_ratio and r.fin_assets_ratio > 15:
                r.red_flags.append(f'交易性金融资产占{r.fin_assets_ratio:.1f}%，可能偏离主业')

        # ===== N23: 公允价值变动收益影响（统一口径：归母净利润）=====
        if latest_prof:
            col_fv = self._find_col(prof, '公允价值变动收益')
            fv_gain = self._safe_float(latest_prof.get(col_fv, 0)) if col_fv else 0
            col_netp = self._find_col(prof, '归母净利润')
            net_profit = self._safe_float(latest_prof.get(col_netp, 0)) if col_netp else 0
            fv_to_np = round(fv_gain / net_profit * 100, 2) if (fv_gain is not None and net_profit and net_profit != 0) else None
            col_total_equity = self._find_col(bal, '所有者权益合计')
            total_equity = self._safe_float(latest_bal.get(col_total_equity, 1)) if col_total_equity else 1
            fv_to_equity = round(fv_gain / total_equity * 100, 2) if (fv_gain is not None and total_equity and total_equity != 0) else None
            r.fair_value_impact = {
                '公允价值变动收益': fv_gain,
                '占归母净利润比例': fv_to_np,
                '占净资产比例': fv_to_equity,
            }

        # ===== N26: 分红/经营现金流比 =====
        if r.dividend_policy and latest_cash:
            col_ocf_cash = self._find_col(cash, '经营活动现金流量净额')
            ocf_val = self._safe_float(latest_cash.get(col_ocf_cash, 0)) if col_ocf_cash else None
            # 修复：兼容abstract路径('分红金额')和supplement路径('每股分红金额')两种键名
            total_div = r.dividend_policy.get('分红总额') or r.dividend_policy.get('分红金额')
            # 如果没有总分红金额，尝试用每股分红 * 总股本估算
            if not total_div:
                dps = r.dividend_policy.get('每股分红金额')
                if dps and latest_bal:
                    col_total_shares = self._find_col(bal, '总股本')
                    total_shares = self._safe_float(latest_bal.get(col_total_shares, 0)) if col_total_shares else None
                    if total_shares and total_shares > 0:
                        total_div = dps * total_shares
            if total_div and ocf_val and ocf_val != 0:
                r.dividend_ocf_ratio = round(total_div / ocf_val * 100, 2)

    def _analyze_profitability_from_abstract(self, r, fin, prof, cash, bal,
                                              target_period: str = None):
        """从stock_financial_abstract格式提取盈利数据"""
        if '指标' not in fin.columns:
            return

        # 自动选择报告期（如果未指定）
        if target_period is None:
            period_cols = self._get_period_cols(fin)
            target_period = self._select_target_period(period_cols, 'annual')

        if target_period is None:
            return

        if self.debug:
            print(f"  [DEBUG] _analyze_profitability_from_abstract: target_period={target_period}")

        # ===== 严格匹配关键指标 =====
        # ROE: 必须是"净资产收益率(ROE)"（在"常用指标"分类下），排除摊薄/扣除/平均变体
        for idx, row in fin.iterrows():
            indicator_name = str(row.get('指标', '')).strip()
            category = str(row.get('选项', '')).strip()
            value = self._safe_float(row.get(target_period))

            if value is None:
                continue

            # ROE匹配：严格匹配"净资产收益率(ROE)"，排除所有变体
            if r.roe is None:
                if indicator_name == '净资产收益率(ROE)':
                    r.roe = value
                elif indicator_name == '净资产收益率' and category == '常用指标':
                    r.roe = value

            # 毛利率匹配：严格匹配"毛利率"
            if r.gross_margin is None and indicator_name == '毛利率' and category == '常用指标':
                r.gross_margin = value

            # 净利率匹配：严格匹配"销售净利率"
            if r.net_margin is None and indicator_name == '销售净利率' and category == '常用指标':
                r.net_margin = value

        # 经营现金流/归母净利润（统一口径）
        # P0修复：增加None保护，防止覆盖路径A（三大报表）已计算的正确值
        if r.ocf_to_netprofit is None:
            ocf_val = self._get_indicator_value(fin, '经营现金流量净额', target_period)
            net_profit_val = self._get_indicator_value(fin, '归母净利润', target_period)
            if ocf_val and net_profit_val and net_profit_val != 0:
                r.ocf_to_netprofit = ocf_val / net_profit_val

        # 归母净利率 = 归母净利润 / 营业总收入
        if r.net_profit_margin_parent is None:
            revenue_val = self._get_indicator_value(fin, '营业总收入', target_period)
            if net_profit_val and revenue_val and revenue_val != 0:
                r.net_profit_margin_parent = net_profit_val / revenue_val * 100

    # Abstract指标名映射表（标准化名称 -> 可能的实际名称列表）
    # 注意：abstract中的指标主要是财务比率，不是资产负债表科目
    ABSTRACT_INDICATOR_MAP = {
        '货币资金': ['货币资金', '现金及现金等价物'],
        '固定资产': ['固定资产', '固定资产合计'],
        '在建工程': ['在建工程'],
        '无形资产': ['无形资产', '无形资产合计'],
        '资产总计': ['资产总计', '总资产', '资产合计'],
        '应收账款': ['应收账款', '应收票据及应收账款'],
        '存货': ['存货'],
        '商誉': ['商誉'],
        '投资性房地产': ['投资性房地产'],
        '交易性金融资产': ['交易性金融资产'],
        '股东权益合计(净资产)': ['股东权益合计(净资产)', '股东权益合计', '净资产', '所有者权益合计'],
        '分红': ['分红', '现金分红', '派息'],
        '有息负债率': ['有息负债率'],
        '净利润增长率': ['净利润增长率', '归属母公司净利润增长率', '归母净利润增长率'],
        '总资产增长率': ['总资产增长率'],
        '总资产周转率(次)': ['总资产周转率', '总资产周转率(次)'],
        '存货周转率(次)': ['存货周转率', '存货周转率(次)'],
        '应收账款周转率(次)': ['应收账款周转率', '应收账款周转率(次)'],
        '营业总收入增长率': ['营业总收入增长率'],
        '营业收入增长率': ['营业收入增长率', '营业总收入增长率'],
    }

    def _get_abstract_value(self, all_vals: Dict, standard_name: str) -> Optional[float]:
        """根据标准化名称从abstract中获取值（支持多名称映射）"""
        # 先直接查找
        if standard_name in all_vals:
            return all_vals[standard_name]
        # 再查找映射表
        if standard_name in self.ABSTRACT_INDICATOR_MAP:
            for alt_name in self.ABSTRACT_INDICATOR_MAP[standard_name]:
                if alt_name in all_vals:
                    return all_vals[alt_name]
        return None

    def _analyze_all_from_abstract(self, r: L1AnalysisResult, fin: pd.DataFrame,
                                     target_period: str):
        """
        从stock_financial_abstract统一提取所有维度数据。
        当三大报表API不可用时，完全依赖abstract提取。
        """
        if fin is None or fin.empty or target_period is None:
            return

        if self.debug:
            print(f"  [DEBUG] _analyze_all_from_abstract: period={target_period}")

        # 获取所有指标值
        all_vals = self._get_all_indicator_values(fin, target_period)

        if not all_vals:
            return

        # ===== 1. 资本结构 =====
        # 资产负债率
        if '资产负债率' in all_vals:
            r.debt_ratio = all_vals['资产负债率']
        
        # 有息负债率（如果abstract中有）
        if '有息负债率' in all_vals:
            r.interest_debt = all_vals['有息负债率']
        
        # 现金覆盖率（货币资金/有息负债）
        monetary_funds = self._get_abstract_value(all_vals, '货币资金')
        if monetary_funds is not None and r.interest_debt is not None and r.interest_debt > 0:
            # 估算有息负债 = 总资产 * 有息负债率
            total_assets_est = self._get_abstract_value(all_vals, '资产总计')
            if total_assets_est:
                interest_debt_abs = total_assets_est * r.interest_debt / 100
                if interest_debt_abs > 0:
                    r.cash_coverage = monetary_funds / interest_debt_abs

        # 货币资金占比
        if monetary_funds is not None:
            total_assets_est = self._get_abstract_value(all_vals, '资产总计')
            if total_assets_est and total_assets_est > 0:
                r.cash_ratio = monetary_funds / total_assets_est * 100
        
        # ===== 2. 资产质量 =====
        # 生产资产占比 = 固定资产+在建工程+工程物资+无形资产
        fixed_assets = self._get_abstract_value(all_vals, '固定资产')
        construction = self._get_abstract_value(all_vals, '在建工程')
        engineering = self._get_abstract_value(all_vals, '工程物资')
        intangible = self._get_abstract_value(all_vals, '无形资产')
        total_assets_est = self._get_abstract_value(all_vals, '资产总计')
        if total_assets_est and total_assets_est > 0:
            production_assets = (fixed_assets or 0) + (construction or 0) + (engineering or 0) + (intangible or 0)
            r.production_asset_ratio = production_assets / total_assets_est * 100
            
            # 应收账款占比
            receivables = self._get_abstract_value(all_vals, '应收账款')
            if receivables is not None:
                r.receivables_ratio = receivables / total_assets_est * 100

            # 应付账款占比 = (应付账款 + 应付票据) / 总资产
            payables = (self._get_abstract_value(all_vals, '应付账款') or 0) + (self._get_abstract_value(all_vals, '应付票据') or 0)
            if payables > 0:
                r.payables_ratio = payables / total_assets_est * 100

            # 应收/应付
            if receivables is not None and receivables > 0 and payables > 0:
                r.receivables_to_payables = receivables / payables

            # 存货占比
            inventory = self._get_abstract_value(all_vals, '存货')
            if inventory is not None:
                r.inventory_ratio = inventory / total_assets_est * 100
            
            # 商誉占比
            goodwill = self._get_abstract_value(all_vals, '商誉')
            if goodwill is not None:
                r.goodwill_ratio = goodwill / total_assets_est * 100
            
            # 非主业资产占比
            invest_property = self._get_abstract_value(all_vals, '投资性房地产') or 0
            trading_fin = self._get_abstract_value(all_vals, '交易性金融资产') or 0
            r.non_core_asset_ratio = (invest_property + trading_fin) / total_assets_est * 100

        # ===== 3. 盈利与现金流 =====
        # ROE（严格匹配"净资产收益率(ROE)"）
        r.roe = all_vals.get('净资产收益率(ROE)')

        # 毛利率（严格匹配）
        r.gross_margin = all_vals.get('毛利率')

        # 销售净利率
        r.net_margin = all_vals.get('销售净利率')

        # 先提取净利润（后续多处使用）
        net_profit = all_vals.get('归母净利润')

        # 分红相关
        dividend = self._get_abstract_value(all_vals, '分红')
        if dividend is not None and net_profit and net_profit != 0:
            r.dividend_payout = dividend / net_profit * 100
            r.dividend_policy = {
                '分红金额': dividend,
                '归母净利润': net_profit,
                '分红支付率': r.dividend_payout,
            }

        # ROA（后续填入asset_quality_detail）
        roa = all_vals.get('总资产报酬率(ROA)')

        # ===== 新增：从abstract提取更多指标 =====
        # 基础指标
        current_ratio = all_vals.get('流动比率')
        quick_ratio = all_vals.get('速动比率')
        revenue_growth = all_vals.get('营业总收入增长率')
        profit_growth = all_vals.get('归属母公司净利润增长率')
        asset_turnover = all_vals.get('总资产周转率')
        inventory_turnover = all_vals.get('存货周转率')
        receivable_turnover = all_vals.get('应收账款周转率')
        total_asset_growth = all_vals.get('总资产增长率')

        # 营业周期 = 存货周转天数 + 应收账款周转天数
        if inventory_turnover and inventory_turnover > 0 and receivable_turnover and receivable_turnover > 0:
            r.operating_cycle_days = 365 / inventory_turnover + 365 / receivable_turnover

        # 经营现金流/归母净利润（使用前面已提取的net_profit）
        # P0修复：增加None保护，防止覆盖其他路径已计算的正确值
        if r.ocf_to_netprofit is None:
            ocf = all_vals.get('经营现金流量净额')
            if ocf and net_profit and net_profit != 0:
                r.ocf_to_netprofit = ocf / net_profit

        # ===== 4. 风险信号 =====
        # 每股数据
        eps = all_vals.get('基本每股收益')
        bvps = all_vals.get('每股净资产')

        # ===== 5. 生成详情 =====
        r.debt_structure_detail = {
            '数据来源': 'stock_financial_abstract',
            '报告期': target_period,
            '资产负债率': r.debt_ratio,
            '流动比率': current_ratio,
            '速动比率': quick_ratio,
        }

        # 存储扩展指标供评分和报告使用
        r.asset_quality_detail = {
            '数据来源': 'stock_financial_abstract',
            '报告期': target_period,
            '商誉': all_vals.get('商誉'),
            '归母净利润': net_profit,
            '营业总收入': all_vals.get('营业总收入'),
            '营业成本': all_vals.get('营业成本'),
            '经营现金流量净额': ocf,
            '基本每股收益': eps,
            '每股净资产': bvps,
            # 新增指标
            '总资产报酬率(ROA)': roa,
            '流动比率': current_ratio,
            '速动比率': quick_ratio,
            '营业收入增长率': revenue_growth,
            '净利润增长率': profit_growth,
            '总资产增长率': total_asset_growth,
            '总资产周转率': asset_turnover,
            '存货周转率': inventory_turnover,
            '应收账款周转率': receivable_turnover,
        }

        r.fin_assets_detail = {
            '数据来源': 'stock_financial_abstract',
        }

        # 注意：strengths和red_flags由 _calc_score() 统一添加，此处不重复添加
        # _calc_score() 会基于相同阈值生成标记

    def _supplement_from_abstract(self, r: L1AnalysisResult, fin: pd.DataFrame,
                                    target_period: str):
        """从abstract补充提取三大报表中无法直接获取的指标（增长率等）"""
        if fin is None or fin.empty or target_period is None:
            return
        all_vals = self._get_all_indicator_values(fin, target_period)
        if not all_vals:
            return

        # 补充到asset_quality_detail
        if r.asset_quality_detail is None:
            r.asset_quality_detail = {}

        # 增长率指标（abstract独有）— 修正实际akshare指标名
        abstract_name_map = {
            '营业总收入增长率': 'revenue_growth',
            '归属母公司净利润增长率': 'profit_growth',
            '总资产增长率': 'total_asset_growth',
        }
        for actual_name, eng_key in abstract_name_map.items():
            if actual_name in all_vals:
                r.asset_quality_detail[actual_name] = all_vals[actual_name]
                r.asset_quality_detail[eng_key] = all_vals[actual_name]

        # 周转率指标 — 修正实际akshare指标名（无"(次)"后缀）
        turnover_name_map = {
            '总资产周转率': 'asset_turnover',
            '存货周转率': 'inventory_turnover',
            '应收账款周转率': 'receivable_turnover',
            '流动资产周转率': 'current_asset_turnover',
        }
        for actual_name, eng_key in turnover_name_map.items():
            if actual_name in all_vals:
                r.asset_quality_detail[actual_name] = all_vals[actual_name]
                r.asset_quality_detail[eng_key] = all_vals[actual_name]

        # 流动比率/速动比率
        for key in ['流动比率', '速动比率']:
            if key in all_vals:
                r.asset_quality_detail[key] = all_vals[key]

        # ROA
        if '总资产报酬率(ROA)' in all_vals:
            r.asset_quality_detail['总资产报酬率(ROA)'] = all_vals['总资产报酬率(ROA)']

    def _supplement_from_analysis_indicator(self, r, analysis_df, target_period: str = None):
        """
        从stock_financial_analysis_indicator(86列)补充提取关键指标。
        这是数据最完整的来源，优先级最高。
        
        Args:
            target_period: 目标报告期(如'20260331')，为None时取最新年报
        """
        if analysis_df is None or analysis_df.empty:
            return

        date_col = None
        for c in analysis_df.columns:
            if '日期' in str(c):
                date_col = c
                break
        
        target_row = None
        if date_col is not None:
            date_strs = analysis_df[date_col].astype(str).str.replace(' 00:00:00', '')
            
            if target_period:
                # 根据目标期匹配对应行
                # 处理 target_period 格式：20260331 -> 2026-03-31
                tp_formatted = target_period
                if len(target_period) == 8:
                    tp_formatted = f"{target_period[:4]}-{target_period[4:6]}-{target_period[6:]}"
                mask = date_strs.str.contains(tp_formatted)
                matched = analysis_df[mask]
                if not matched.empty:
                    target_row = matched.iloc[-1]
            
            if target_row is None:
                if target_period:
                    # 传入了明确的目标期但匹配失败，不补充（避免用其他期间数据污染）
                    return
                # 默认取最新年报行(12月31日)
                mask = date_strs.str.contains('12-31')
                annual_rows = analysis_df[mask]
                if not annual_rows.empty:
                    target_row = annual_rows.iloc[-1]
        
        if target_row is None:
            if target_period:
                return
            target_row = analysis_df.iloc[-1]

        def _get(col_name):
            """安全获取值"""
            if col_name not in target_row.index:
                return None
            return self._safe_float(target_row.get(col_name))

        if r.asset_quality_detail is None:
            r.asset_quality_detail = {}

        # ===== 增长率（覆盖abstract和自算结果，以此为准）=====
        rev_g = _get('主营业务收入增长率(%)')
        if rev_g is not None:
            r.asset_quality_detail['revenue_growth'] = rev_g
            r.asset_quality_detail['营业收入增长率'] = rev_g

        np_g = _get('净利润增长率(%)')
        if np_g is not None:
            r.asset_quality_detail['profit_growth'] = np_g
            r.asset_quality_detail['净利润增长率'] = np_g

        ta_g = _get('总资产增长率(%)')
        if ta_g is not None:
            r.asset_quality_detail['总资产增长率'] = ta_g

        # ===== 周转率（覆盖）=====
        at = _get('总资产周转率(次)')
        if at is not None:
            r.asset_quality_detail['asset_turnover'] = at
            r.asset_quality_detail['总资产周转率'] = at

        it = _get('存货周转率(次)')
        if it is not None:
            r.asset_quality_detail['inventory_turnover'] = it
            r.asset_quality_detail['存货周转率'] = it

        art = _get('应收账款周转率(次)')
        if art is not None:
            r.asset_quality_detail['receivable_turnover'] = art
            r.asset_quality_detail['应收账款周转率'] = art

        cat = _get('流动资产周转率(次)')
        if cat is not None:
            r.asset_quality_detail['current_asset_turnover'] = cat

        # ===== 流动性 =====
        cr = _get('流动比率')
        if cr is not None:
            r.asset_quality_detail['流动比率'] = cr

        qr = _get('速动比率')
        if qr is not None:
            r.asset_quality_detail['速动比率'] = qr

        # ===== 股息发放率（分红维度核心指标）=====
        dp = _get('股息发放率(%)')
        # 修复：analysis_indicator 中偶有极低错误值（如0.0002），不覆盖已有正确计算值
        if dp is not None and dp >= 0.01:
            r.dividend_payout = dp

        # ===== 分红方案详情 =====
        dps = _get('每股股利(元)')
        eps = _get('每股收益(元)')
        if dps is not None or eps is not None:
            dividend_info = {}
            if dps is not None:
                dividend_info['每股分红金额'] = dps
            if eps is not None:
                dividend_info['每股收益'] = eps
            if dps is not None and eps is not None and eps > 0:
                dividend_info['分红支付率'] = round(dps / eps * 100, 2)
            if dp is not None:
                dividend_info['股息发放率'] = dp
            if dividend_info:
                r.dividend_policy = dividend_info

        # ===== 利息保障倍数 =====
        # 仅在当前值为None时覆盖（避免覆盖cap结构中已正确设为None的情况）
        ic = _get('利息支付倍数')
        if ic is not None and r.interest_coverage is None:
            if ic > 0:  # 只接受正值
                r.interest_coverage = ic

        # ===== 补充ROE/净利率/毛利率（如果缺失）=====
        roe = _get('净资产收益率(%)')
        if roe is not None and r.roe is None:
            r.roe = roe

        nm = _get('销售净利率(%)')
        if nm is not None and r.net_margin is None:
            r.net_margin = nm

        gm = _get('销售毛利率(%)')
        if gm is not None and r.gross_margin is None:
            r.gross_margin = gm

        # ===== 经营现金净流量与净利润的比率 =====
        ocf_np = _get('经营现金净流量与净利润的比率(%)')
        if ocf_np is not None:
            r.asset_quality_detail['ocf_to_np_ratio'] = ocf_np

        # ===== 营业周期计算 =====
        # 营业周期 = 存货周转天数 + 应收账款周转天数
        if r.operating_cycle_days is None:
            it = (r.asset_quality_detail or {}).get('inventory_turnover')
            art = (r.asset_quality_detail or {}).get('receivable_turnover')
            if it and it > 0 and art and art > 0:
                r.operating_cycle_days = 365 / it + 365 / art

    def _analyze_risk_signals(self, r, bal, prof, cash, row_data: Dict = None):
        """第四部分：风险信号排查 - 增强版"""
        if row_data is not None:
            latest = row_data
        else:
            latest = self._get_latest_row(bal)
        if latest is None:
            return
        
        col_total_assets = self._find_col(bal, '资产合计')
        total_assets = self._safe_float(latest.get(col_total_assets, 1)) or 1 if col_total_assets else 1
        
        # 货币资金异常（已实现）
        col_cash = self._find_col(bal, '货币资金')
        
        if col_cash:
            cash_val = self._safe_float(latest.get(col_cash, 0)) or 0
            r.cash_excess_signal = cash_val > total_assets * 0.3 if total_assets > 0 else None
            
            if r.cash_excess_signal:
                r.red_flags.append('货币资金占比超过30%，可能资金运用效率低')
        
        # ===== T4.3：其他货币资金检查 =====
        col_other_cash = self._find_col(bal, '其他货币资金')
        if col_other_cash:
            other_cash = self._safe_float(latest.get(col_other_cash, 0)) or 0
            r.other_monetary = other_cash / total_assets * 100 if total_assets > 0 else None
            
            if r.other_monetary and r.other_monetary > 5:
                r.red_flags.append(f'其他货币资金占{r.other_monetary:.1f}%，需关注资金性质')
        
        # ===== T4.2增强：高货币资金+高负债检测 =====
        if r.cash_excess_signal and r.interest_debt and r.interest_debt > 10:
            r.high_cash_high_debt = True
            r.cash_abnormal = {
                '货币资金占比': r.cash_excess_signal,
                '有息负债率': r.interest_debt,
                '判断': '高货币资金+高负债，可能存在资金占用或财务造假风险',
            }
            r.red_flags.append('高货币资金+高负债异常，需警惕')
        else:
            r.high_cash_high_debt = False
        
        # 货币异常详情
        if not r.cash_abnormal:
            r.cash_abnormal = {
                '货币资金占比': r.cash_excess_signal,
                '有息负债率': r.interest_debt,
                '判断': '正常',
            }

        # ===== N30: 低效现金检测 =====
        col_st_loan = self._find_col(bal, '短期借款')
        col_due_nc = self._find_col(bal, '一年内到期的非流动负债')
        short_interest_debt = 0
        if col_st_loan:
            short_interest_debt += self._safe_float(latest.get(col_st_loan, 0)) or 0
        if col_due_nc:
            short_interest_debt += self._safe_float(latest.get(col_due_nc, 0)) or 0

        col_interest_income = self._find_col(prof, '利息收入') if prof is not None and not prof.empty else None
        prof_row = self._get_latest_row(prof) if prof is not None and not prof.empty else None
        interest_income = self._get_val(prof_row, col_interest_income) if prof_row else None

        cash_val = self._safe_float(latest.get(col_cash, 0)) or 0 if col_cash else 0
        # 短期有息负债=0时不计算此比率，标记为N/A
        cash_to_short = cash_val / short_interest_debt if short_interest_debt > 0 else None
        # 利息收入为None（列未找到）时不计算比率
        interest_income_ratio = interest_income / cash_val * 100 if (cash_val > 0 and interest_income is not None) else None

        r.inefficient_cash = False
        r.interest_income_available = interest_income is not None  # 标记利息收入是否可用
        if cash_to_short is not None and interest_income_ratio is not None:
            if cash_to_short > 3 and interest_income_ratio < 1.5:
                r.inefficient_cash = True
                r.red_flags.append(f'低效现金：货币资金/短期有息负债={cash_to_short:.1f}倍，利息收入/货币资金={interest_income_ratio:.2f}%')
            elif cash_to_short > 5:
                r.inefficient_cash = True
                r.red_flags.append(f'弱信号：货币资金/短期有息负债={cash_to_short:.1f}倍，现金冗余')

    def _analyze_industry_comparison(self, r, bal, prof, fin):
        """第五部分：综合对比与行业定位 - 增强版"""
        # ===== T5.1-T5.3：行业对比增强 =====
        # 计算本公司关键指标
        company_metrics = {
            'debt_ratio': r.debt_ratio,
            'interest_debt': r.interest_debt,
            'roe': r.roe,
            'net_margin': r.net_margin,
            'gross_margin': r.gross_margin,
            'production_asset_ratio': r.production_asset_ratio,
            'receivables_ratio': r.receivables_ratio,
            'cash_coverage': r.cash_coverage,
        }
        
        # ===== T5.2：竞争对手预定义列表 =====
        # 根据不同行业预定义竞争对手
        peers_config = {
            '白酒': ['贵州茅台', '五粮液', '洋河股份', '山西汾酒', '泸州老窖'],
            '银行': ['招商银行', '工商银行', '建设银行'],
            '房地产': ['万科A', '保利发展', '中国海外发展'],
            '新能源': ['宁德时代', '比亚迪', '隆基绿能'],
            '医药': ['恒瑞医药', '药明康德', '迈瑞医疗'],
            'default': ['中煤能源', '陕西煤业', '兖矿能源'],
        }

        # 自动匹配行业：根据股票名称或代码中的关键词
        selected_peers = peers_config['default']
        industry_key = 'default'
        name_lower = (r.name or '').lower()
        code = (r.code or '')
        # 白酒行业关键词
        baijiu_keywords = ['白酒', '酒业', '茅台', '五粮液', '古井', '汾酒', '泸州', '洋河',
                          '酒', '贡酒', '老窖', '舍得', '水井坊', '迎驾', '口子窖']
        # 白酒行业常见股票代码段（000596/000858/600809等）
        baijiu_codes = ['000596', '000858', '600809', '000568', '002304', '603369']
        if any(kw in name_lower for kw in baijiu_keywords) or code in baijiu_codes:
            selected_peers = peers_config['白酒']
            industry_key = '白酒'
        elif any(kw in name_lower for kw in ['银行']):
            selected_peers = peers_config['银行']
            industry_key = '银行'
        
        # ===== T5.3：同业对比指标 =====
        comparison_metrics = ['资产负债率', '有息负债率', 'ROE', '净利润率', '毛利率', '生产资产占比']
        
        r.peer_comparison = {
            '行业': industry_key,
            '竞争对手': selected_peers,
            '对比指标': comparison_metrics,
            '本公司数据': company_metrics,
            '对比状态': '需扩展数据源获取同业数据',
            '建议': '可使用akshare获取同业财务数据做横向对比',
        }
        
        # ===== T5.4-T5.5：核心优势和风险点（基于已有分析）=====
        # 在评分时已更新strengths和red_flags

        # ===== 新增：关键指标行业偏差检测 =====
        # 定义各行业关键指标的合理区间（基于行业经验数据）
        industry_benchmarks = {
            '白酒': {
                'debt_ratio': (20, 50),
                'interest_debt': (0, 20),
                'roe': (10, 35),
                'gross_margin': (55, 90),
                'net_margin': (15, 45),
                'receivables_ratio': (0, 8),
                'cash_coverage': (0.5, 10),
                'production_asset_ratio': (15, 55),
                'inventory_ratio': (5, 45),
                'cash_ratio': (10, 55),
            },
            '银行': {
                'debt_ratio': (90, 96),
                'interest_debt': (80, 95),
                'roe': (8, 18),
                'gross_margin': (None, None),
                'net_margin': (25, 45),
                'receivables_ratio': (0, 5),
                'cash_coverage': (1, 5),
                'production_asset_ratio': (0, 10),
                'inventory_ratio': (0, 2),
                'cash_ratio': (5, 20),
            },
            'default': {
                'debt_ratio': (30, 70),
                'interest_debt': (5, 40),
                'roe': (5, 20),
                'gross_margin': (20, 50),
                'net_margin': (5, 20),
                'receivables_ratio': (5, 30),
                'cash_coverage': (0.5, 3),
                'production_asset_ratio': (20, 60),
                'inventory_ratio': (10, 40),
                'cash_ratio': (5, 30),
            },
        }

        benchmarks = industry_benchmarks.get(industry_key, industry_benchmarks['default'])
        metric_names = {
            'debt_ratio': '资产负债率',
            'interest_debt': '有息负债率',
            'roe': 'ROE',
            'gross_margin': '毛利率',
            'net_margin': '净利率',
            'receivables_ratio': '应收账款占比',
            'cash_coverage': '现金覆盖率',
            'production_asset_ratio': '生产资产占比',
            'inventory_ratio': '存货占比',
            'cash_ratio': '货币资金占比',
        }

        r.peer_comparison['偏差检测'] = []
        for metric, (low, high) in benchmarks.items():
            val = company_metrics.get(metric)
            if val is None:
                continue
            name = metric_names.get(metric, metric)
            if low is not None and val < low:
                deviation = low - val
                msg = f'{name}{val:.1f}%低于行业正常区间({low}%-{high}%)，偏低{deviation:.1f}个百分点'
                r.peer_comparison['偏差检测'].append({'指标': name, '状态': '偏低', '数值': val, '区间': (low, high), '说明': msg})
                # 严重偏低时加入red_flags
                if deviation > 10:
                    r.red_flags.append(f'【行业偏离】{name}显著低于同业水平({val:.1f}% vs 正常{low}%-{high}%)')
                else:
                    r.strengths.append(f'【行业偏离】{name}低于同业平均，属保守型特征')
            elif high is not None and val > high:
                deviation = val - high
                msg = f'{name}{val:.1f}%高于行业正常区间({low}%-{high}%)，偏高{deviation:.1f}个百分点'
                r.peer_comparison['偏差检测'].append({'指标': name, '状态': '偏高', '数值': val, '区间': (low, high), '说明': msg})
                # 严重偏高时加入red_flags
                if deviation > 10:
                    r.red_flags.append(f'【行业偏离】{name}显著高于同业水平({val:.1f}% vs 正常{low}%-{high}%)')
                else:
                    r.strengths.append(f'【行业偏离】{name}高于同业平均，需关注合理性')

    def _analyze_trend_anomalies(self, r: L1AnalysisResult):
        """第六部分：趋势异常检测 - 检测各模块指标的剧烈变化（全历史扫描）"""
        # 定义趋势异常检测规则：(指标字段, 年份字段, 绝对阈值, 相对阈值%, 指标名称)
        # 绝对阈值用于百分比指标(pp变化)，相对阈值用于波动大的倍数/比率指标和金额指标
        trend_rules = [
            # === 盈利能力 ===
            ('roe_trend', 'roe_trend_years', 5.0, None, 'ROE'),
            ('net_profit_margin_parent_trend', 'net_profit_margin_parent_trend_years', 5.0, None, '归母净利率'),
            ('ocf_to_netprofit_trend', 'ocf_to_netprofit_trend_years', 0.5, 50.0, 'OCF/净利润'),
            # === 资本结构 ===
            ('debt_ratio_trend', 'debt_ratio_trend_years', 8.0, None, '资产负债率'),
            ('interest_debt_trend', 'interest_debt_trend_years', 5.0, None, '有息负债率'),
            ('cash_coverage_trend', 'cash_coverage_trend_years', 20.0, 80.0, '现金覆盖率'),
            ('cash_ratio_trend', 'cash_ratio_trend_years', 10.0, 50.0, '货币资金占比'),
            # === 资产质量 ===
            ('production_asset_trend', 'production_asset_trend_years', 10.0, None, '生产资产占比'),
            # === 金额类指标（用相对阈值） ===
            ('equity_trend', 'equity_trend_years', None, 20.0, '所有者权益'),
            ('r_d_expense_trend', 'r_d_expense_trend_years', None, 30.0, '研发费用'),
        ]

        r.trend_anomalies = []
        r.red_flags = r.red_flags or []
        r.strengths = r.strengths or []

        for trend_attr, years_attr, abs_threshold, rel_threshold, name in trend_rules:
            values = getattr(r, trend_attr, None)
            years = getattr(r, years_attr, None) if years_attr else None

            if not values or len(values) < 2:
                continue

            # 确保年份和数值长度一致
            if years and len(years) != len(values):
                years = None

            # ===== 1. 全历史相邻年份扫描（修复：不再只检测最近一年）=====
            for i in range(1, len(values)):
                curr_val = values[i]
                prev_val = values[i-1]
                if curr_val is None or prev_val is None or prev_val == 0:
                    continue

                change = curr_val - prev_val
                change_abs = abs(change)
                # 相对变化率：适用于现金覆盖率等波动大的指标
                change_rel = change_abs / abs(prev_val) * 100 if prev_val != 0 else 0
                direction = '上升' if change > 0 else '下降'

                # 判断标准：绝对变化超过阈值，或相对变化超过相对阈值
                is_anomaly = False
                if abs_threshold is not None and change_abs > abs_threshold:
                    is_anomaly = True
                if rel_threshold is not None and change_rel > rel_threshold:
                    is_anomaly = True

                if is_anomaly:
                    year_label = f"({years[i-1]}→{years[i]})" if years else ''
                    # 根据指标类型选择展示单位
                    if name == '现金覆盖率':
                        msg = f'{name}{year_label}{direction}{change_abs:.1f}倍(从{prev_val:.1f}到{curr_val:.1f})，变化剧烈'
                    elif name == 'OCF/净利润':
                        msg = f'{name}{year_label}{direction}{change_abs:.2f}倍(从{prev_val:.2f}到{curr_val:.2f})，变化剧烈'
                    elif name == '所有者权益':
                        msg = f'{name}{year_label}{direction}{change_abs/1e8:.1f}亿元(相对变化{change_rel:.0f}%)，变化剧烈'
                    elif name == '研发费用':
                        msg = f'{name}{year_label}{direction}{change_abs/1e8:.2f}亿元(相对变化{change_rel:.0f}%)，变化剧烈'
                    elif rel_threshold and change_rel > rel_threshold:
                        msg = f'{name}{year_label}{direction}{change_abs:.1f}个百分点(相对变化{change_rel:.0f}%)，变化剧烈'
                    else:
                        msg = f'{name}{year_label}{direction}{change_abs:.1f}个百分点，变化剧烈'

                    r.trend_anomalies.append({
                        '指标': name,
                        '类型': '年度剧变',
                        '变化': f'{direction}{change_abs:.1f}',
                        '说明': msg,
                    })

                    # 风险信号分级：只将关键指标的恶化方向加入red_flags
                    is_deterioration = (
                        (name in ['ROE', '归母净利率', '现金覆盖率', 'OCF/净利润', '货币资金占比'] and direction == '下降') or
                        (name in ['资产负债率', '有息负债率', '生产资产占比'] and direction == '上升') or
                        (name == '所有者权益' and direction == '下降')
                    )
                    if is_deterioration:
                        r.red_flags.append(f'【趋势异常】{msg}')

            # ===== 2. 检测连续下降趋势（连续2年下降）=====
            if len(values) >= 3:
                max_consecutive_declines = 0
                current_declines = 0
                decline_end_idx = 0
                for i in range(1, len(values)):
                    if values[i] is not None and values[i-1] is not None and values[i] < values[i-1]:
                        current_declines += 1
                        if current_declines > max_consecutive_declines:
                            max_consecutive_declines = current_declines
                            decline_end_idx = i
                    else:
                        current_declines = 0

                if max_consecutive_declines >= 2:
                    start_idx = decline_end_idx - max_consecutive_declines
                    start_year = years[start_idx] if years and start_idx >= 0 else '?'
                    end_year = years[decline_end_idx] if years and decline_end_idx < len(years) else '?'
                    year_label = f"({start_year}→{end_year})"
                    msg = f'{name}{year_label}连续{max_consecutive_declines+1}年下降，需关注持续性'
                    r.trend_anomalies.append({
                        '指标': name,
                        '类型': '连续下降',
                        '变化': f'连续{max_consecutive_declines+1}年下降',
                        '说明': msg,
                    })
                    if name in ['ROE', '归母净利率', '现金覆盖率', 'OCF/净利润', '货币资金占比', '所有者权益']:
                        r.red_flags.append(f'【趋势恶化】{msg}')

            # ===== 3. 检测极端异常值（新增）=====
            extreme_thresholds = {
                '现金覆盖率': {'high': 500, 'low': 0.5},
                '货币资金占比': {'high': 50, 'low': 2},
                '有息负债率': {'high': 60, 'low': None},
                'OCF/净利润': {'high': None, 'low': 0.3},
            }
            if name in extreme_thresholds:
                thresholds = extreme_thresholds[name]
                for i, val in enumerate(values):
                    if val is None:
                        continue
                    high_thr = thresholds.get('high')
                    low_thr = thresholds.get('low')
                    if high_thr is not None and val > high_thr:
                        year_label = f"({years[i]})" if years else ''
                        msg = f'{name}{year_label}达{val:.1f}，远高于正常水平(>{high_thr})'
                        r.trend_anomalies.append({
                            '指标': name,
                            '类型': '极端偏高',
                            '变化': f'{val:.1f}',
                            '说明': msg,
                        })
                        r.red_flags.append(f'【极端值】{msg}')
                    if low_thr is not None and val < low_thr:
                        year_label = f"({years[i]})" if years else ''
                        msg = f'{name}{year_label}仅{val:.1f}，远低于正常水平(<{low_thr})'
                        r.trend_anomalies.append({
                            '指标': name,
                            '类型': '极端偏低',
                            '变化': f'{val:.1f}',
                            '说明': msg,
                        })
                        r.red_flags.append(f'【极端值】{msg}')

            # ===== 4. 检测从正常区间突变为异常区间（全历史扫描）=====
            if len(values) >= 2 and name in ['ROE', '资产负债率', '归母净利率']:
                abnormal_thresholds = {
                    'ROE': (5, None),
                    '资产负债率': (None, 70),
                    '归母净利率': (None, 10),
                }
                low_thr, high_thr = abnormal_thresholds.get(name, (None, None))
                for i in range(1, len(values)):
                    prev_val = values[i-1]
                    curr_val = values[i]
                    if prev_val is None or curr_val is None:
                        continue
                    was_normal = (low_thr is None or prev_val >= low_thr) and (high_thr is None or prev_val <= high_thr)
                    is_abnormal = (low_thr is not None and curr_val < low_thr) or (high_thr is not None and curr_val > high_thr)
                    if was_normal and is_abnormal:
                        year_label = f"({years[i-1]}→{years[i]})" if years else ''
                        msg = f'{name}{year_label}从正常区间突变为异常({curr_val:.1f}%)'
                        r.trend_anomalies.append({
                            '指标': name,
                            '类型': '区间突变',
                            '变化': '正常→异常',
                            '说明': msg,
                        })
                        r.red_flags.append(f'【指标突变】{msg}')
                        break  # 只报告第一次突变

    def _collect_manual_checks(self, r: L1AnalysisResult):
        """汇总所有需人工核查年报附注的项目"""
        checks = [
            {'section': '资本结构', 'indicator': '负债用途', 'target': '借款用途披露（经营/投资/偿债）'},
            {'section': '资产质量', 'indicator': '应收票据构成', 'target': '银行承兑vs商业承兑占比'},
            {'section': '资产质量', 'indicator': '应收账龄分布', 'target': '1年以上账龄占比'},
            {'section': '资产质量', 'indicator': '坏账计提标准', 'target': '单独测试减值是否为0'},
            {'section': '资产质量', 'indicator': '应收关联方', 'target': '是否集中在少数关联方'},
            {'section': '资产质量', 'indicator': '存货计价方法', 'target': '是否发生会计政策变更'},
            {'section': '资产质量', 'indicator': '在建工程进度', 'target': '工程进度是否异常延迟'},
            {'section': '盈利真实性', 'indicator': '金融资产分类', 'target': '交易性金融资产持有目的'},
            {'section': '盈利真实性', 'indicator': '金融资产重分类', 'target': 'FVTPL↔FVTOCI重分类'},
            {'section': '盈利真实性', 'indicator': '持股公允价值', 'target': '期末公允价值vs成本差额'},
            {'section': '盈利真实性', 'indicator': '债权投资减值', 'target': '减值计提或转回情况'},
            {'section': '风险信号', 'indicator': '关联交易定价', 'target': '非同一控制合并作价公允性'},
            {'section': '风险信号', 'indicator': '其他货币资金', 'target': '金额及合理性说明'},
            {'section': '风险信号', 'indicator': '固定资产折旧', 'target': '折旧方法和年限'},
            {'section': '风险信号', 'indicator': '折旧政策变更', 'target': '报告期内是否变更'},
        ]
        r.all_manual_checks = checks

    def _calc_score(self, r: L1AnalysisResult):
        """6维度加权评分体系"""
        # Helper: score by threshold ranges
        def score_by_range(value, ranges):
            """ranges: list of (threshold, score) sorted desc"""
            if value is None:
                return 0
            for threshold, score in ranges:
                if value >= threshold:
                    return score
            return 0

        # 1. 盈利能力 (30分)
        roe_score = score_by_range(r.roe, [(15, 10), (10, 7), (5, 4), (0, 2)])
        gm_score = score_by_range(r.gross_margin, [(60, 10), (40, 7), (30, 4), (0, 2)])
        nm_score = score_by_range(r.net_margin, [(20, 10), (15, 7), (10, 4), (0, 2)])
        profitability = roe_score + gm_score + nm_score

        # 2. 偿债能力 (20分)
        dr_score = score_by_range(100 - r.debt_ratio if r.debt_ratio else 0, [(70, 10), (50, 7), (30, 4), (0, 2)])
        cc_score = score_by_range(r.cash_coverage, [(2, 10), (1, 7), (0.5, 4), (0, 2)])
        solvency = dr_score + cc_score

        # 3. 成长性 (20分) - get from asset_quality_detail if available
        rev_growth = (r.asset_quality_detail or {}).get('revenue_growth')
        prof_growth = (r.asset_quality_detail or {}).get('profit_growth')
        rg_score = score_by_range(rev_growth, [(20, 10), (10, 7), (0, 4), (-10, 2)])
        pg_score = score_by_range(prof_growth, [(20, 10), (10, 7), (0, 4), (-10, 2)])
        growth = rg_score + pg_score

        # 4. 运营效率 (15分)
        at = (r.asset_quality_detail or {}).get('asset_turnover')
        it = (r.asset_quality_detail or {}).get('inventory_turnover')
        at_score = score_by_range(at, [(1, 7.5), (0.5, 5), (0.3, 3), (0, 1)])
        it_score = score_by_range(it, [(5, 7.5), (3, 5), (1, 3), (0, 1)])
        efficiency = at_score + it_score

        # 5. 现金流 (10分)
        ocf_score = score_by_range(r.ocf_to_netprofit, [(1.2, 10), (1.0, 7), (0.8, 4), (0.6, 2)])
        cash_flow = ocf_score

        # 6. 分红 (5分)
        dp_score = score_by_range(r.dividend_payout, [(50, 5), (30, 3.5), (0, 2), (-1, 0)])
        dividends = dp_score

        total = profitability + solvency + growth + efficiency + cash_flow + dividends
        r.overall_score = max(0, min(100, round(total, 1)))

        # Rating
        if r.overall_score >= 80:
            r.rating = 'A级(优秀)'
        elif r.overall_score >= 60:
            r.rating = 'B级(良好)'
        elif r.overall_score >= 40:
            r.rating = 'C级(一般)'
        else:
            r.rating = 'D级(较差)'

        r.score_detail = {
            '盈利能力(30)': profitability,
            '偿债能力(20)': solvency,
            '成长性(20)': growth,
            '运营效率(15)': efficiency,
            '现金流(10)': cash_flow,
            '分红(5)': dividends,
        }

        # Red flags and strengths
        r.red_flags = r.red_flags or []
        r.strengths = r.strengths or []

        if r.debt_ratio and r.debt_ratio > 70:
            r.red_flags.append(f'资产负债率{r.debt_ratio:.1f}%偏高')
        elif r.debt_ratio and r.debt_ratio < 30:
            r.strengths.append('资产负债率低，财务稳健')

        if r.roe:
            if r.roe < 5:
                r.red_flags.append(f'ROE {r.roe:.1f}%偏低')
            elif r.roe > 15:
                r.strengths.append(f'ROE {r.roe:.1f}%优秀')

        if r.gross_margin and r.gross_margin > 60:
            r.strengths.append(f'毛利率{r.gross_margin:.1f}%优秀')

        if r.net_margin:
            if r.net_margin < 3:
                r.red_flags.append(f'净利率{r.net_margin:.1f}%偏低')
            elif r.net_margin > 20:
                r.strengths.append(f'净利率{r.net_margin:.1f}%优秀')

        if r.ocf_to_netprofit is not None:
            if r.ocf_to_netprofit < 0.6:
                r.red_flags.append(f'OCF/归母净利润{r.ocf_to_netprofit:.2f}倍，盈利质量存疑')
            elif r.ocf_to_netprofit > 1.2:
                r.strengths.append(f'OCF/归母净利润{r.ocf_to_netprofit:.2f}倍，盈利含金量高')

        if r.cash_coverage and r.cash_coverage > 2:
            r.strengths.append(f'现金覆盖率{r.cash_coverage:.1f}倍，偿债能力极强')
        elif r.cash_coverage and r.cash_coverage < 0.5:
            r.red_flags.append(f'现金覆盖率{r.cash_coverage:.1f}倍偏低，偿债压力大')

    def _judge_pass(self, r: L1AnalysisResult) -> bool:
        """判定是否通过L1（基于评级：C级及以上即score>=40通过）"""
        if r.debt_ratio and r.debt_ratio > 95:
            return False
        return r.overall_score >= 40 if r.overall_score is not None else False

    def _generate_summary(self, r: L1AnalysisResult) -> str:
        """生成摘要"""
        lines = []
        lines.append(f"代码: {r.code} | {r.name}")
        lines.append(f"综合评分: {r.overall_score}/100 | L1通过: {'✅' if r.pass_l1 else '❌'}")
        
        if r.debt_ratio:
            lines.append(f"资产负债率: {r.debt_ratio:.1f}%")
        if r.roe:
            lines.append(f"ROE: {r.roe:.2f}%")
        if r.net_margin:
            lines.append(f"净利润率: {r.net_margin:.2f}%")
        
        if r.red_flags:
            lines.append(f"⚠️ 风险信号: {'; '.join(r.red_flags[:3])}")
        if r.strengths:
            lines.append(f"✨ 优势: {'; '.join(r.strengths[:2])}")
        
        return '\n'.join(lines)

    def _append_trend_table(self, report, title, years, data_list):
        """生成趋势表格
        years: 年份列表
        data_list: [(values_list, label, formatter_func), ...]
        """
        if not years or not any(d[0] for d in data_list):
            return
        report.append(f"\n**{title}**:\n\n")
        # 表头
        headers = "| 年份 |"
        for _, label, _ in data_list:
            headers += f" {label} |"
        report.append(headers + "\n")
        # 分隔线
        sep = "|------|"
        for _ in data_list:
            sep += "------|"
        report.append(sep + "\n")
        # 数据行
        for i, year in enumerate(years):
            row = f"| {year} |"
            for values, _, formatter in data_list:
                if values is None or not isinstance(values, (list, tuple)):
                    val = None
                else:
                    val = values[i] if i < len(values) else None
                row += f" {formatter(val)} |"
            report.append(row + "\n")
        report.append("\n")

    def _generate_detailed_report(self, r: L1AnalysisResult) -> str:
        """生成详细分析报告 - 核心33指标"""
        def fv(val, unit='亿'):
            if val is None:
                return 'N/A'
            if unit == '亿':
                return f'{val / 1e8:.2f}{unit}'
            return f'{val:.2f}'

        def pct(val):
            return f'{val:.2f}%' if val is not None else 'N/A'

        def fmt_times(val):
            return f'{val:.2f}倍' if val is not None else 'N/A'

        report = []

        # 判断报告期间类型，用于趋势标题
        trend_label = '季报' if r.data_period == 'quarter' else '五年'

        # ===== 标题 =====
        report.append(f"# L1财报分析报告: {r.code} {r.name}\n")
        report.append(f"**数据期间**: {r.report_date or 'N/A'} | "
                      f"**综合评分**: {r.overall_score or 'N/A'}/100 | "
                      f"**L1通过**: {'是' if r.pass_l1 else '否'}\n\n")

        if r.score_detail:
            report.append("**分项得分**:\n")
            for dim, score in r.score_detail.items():
                report.append(f"- {dim}: {score:.1f}\n")
            report.append("\n")

        # ===== 一、盈利与现金流 =====
        report.append("---\n## 一、盈利与现金流\n\n")

        report.append(f"| 指标 | 数值 |\n")
        report.append(f"|------|------|\n")
        report.append(f"| ROE(净资产收益率) | {pct(r.roe)} |\n")
        report.append(f"| 毛利率 | {pct(r.gross_margin)} |\n")
        report.append(f"| 净利率 | {pct(r.net_margin)} |\n")
        report.append(f"| 归母净利率 | {pct(r.net_profit_margin_parent)} |\n")
        report.append(f"| 经营现金流/归母净利润 | {r.ocf_to_netprofit:.2f}倍 |\n" if r.ocf_to_netprofit is not None else "| 经营现金流/归母净利润 | N/A |\n")
        report.append(f"| 股息发放率 | {pct(r.dividend_payout)} |\n")
        report.append(f"| 金融资产投资占比 | {pct(r.fin_assets_ratio)} |\n")
        report.append("\n")

        # 盈利与现金流趋势（扩展为全量指标）
        if r.roe_trend_years:
            self._append_trend_table(report, f"盈利与现金流{trend_label}趋势", r.roe_trend_years, [
                (r.roe_trend, "ROE", pct),
                (r.gross_margin_trend, "毛利率", pct),
                (r.net_profit_margin_parent_trend, "归母净利率", pct),
                (r.ocf_to_netprofit_trend, "OCF/净利润", fmt_times),
                (r.dividend_payout_trend, "股息发放率", pct),
                (r.dividend_ocf_ratio_trend, "分红/OCF比", pct),
            ])

        # ===== 二、资本结构（13字段）=====
        report.append("---\n## 二、资本结构\n\n")

        report.append(f"| 指标 | 数值 |\n")
        report.append(f"|------|------|\n")
        report.append(f"| 资产负债率 | {pct(r.debt_ratio)} |\n")
        report.append(f"| 有息负债率 | {pct(r.interest_debt)} |\n")
        report.append(f"| 现金覆盖率 | {r.cash_coverage:.2f}倍 |\n" if r.cash_coverage is not None else "| 现金覆盖率 | N/A |\n")
        report.append(f"| 短期负债/总负债 | {pct(r.short_term_debt_ratio)} |\n")
        report.append(f"| 长期负债/总负债 | {pct(r.long_term_debt_ratio)} |\n")
        report.append(f"| 经营性负债/总负债 | {pct(r.operating_debt_ratio)} |\n")
        report.append(f"| 融资性负债/总负债 | {pct(r.financing_debt_ratio)} |\n")
        report.append(f"| 有息负债总额 | {fv(r.interest_bearing_debt)} |\n")
        slr_str = f"{r.short_long_ratio:.2f}" if (r.short_long_ratio is not None and r.short_long_ratio != float('inf')) else ("纯短期(无长期有息)" if r.short_long_ratio == float('inf') else "N/A")
        report.append(f"| 短期有息/长期有息比 | {slr_str} |\n")
        report.append(f"| 货币资金 | {fv(r.cash) if hasattr(r, 'cash') and r.cash is not None else 'N/A'} |\n")
        report.append(f"| 现金类资产总额 | {fv(r.cash_assets) if hasattr(r, 'cash_assets') and r.cash_assets is not None else 'N/A'} |\n")
        report.append(f"| 货币资金占比 | {pct(r.cash_ratio)} |\n")
        if r.finance_cost_ratio == -9999:
            report.append("| 融资成本率(财务费用/有息负债) | 利息收入型(财务费用为负) |\n")
        else:
            report.append(f"| 融资成本率(财务费用/有息负债) | {pct(r.finance_cost_ratio)} |\n")
        if r.interest_coverage == -9999:
            report.append("| 利息保障倍数(EBIT/财务费用) | 利息收入型(无利息支出) |\n")
        elif r.interest_coverage is not None:
            report.append(f"| 利息保障倍数(EBIT/财务费用) | {r.interest_coverage:.2f}倍 |\n")
        else:
            report.append("| 利息保障倍数 | N/A |\n")

        # 负债结构详情
        if r.debt_structure_detail:
            report.append("\n**负债结构详情**:\n\n")
            report.append(f"| 项目 | 金额 |\n")
            report.append(f"|------|------|\n")
            for k, v in r.debt_structure_detail.items():
                if isinstance(v, (int, float)) and v != 0:
                    report.append(f"| {k} | {fv(v)} |\n")
            report.append("\n")

        # 权益变动
        if r.equity_change_detail:
            report.append("\n**权益变动**:\n\n")
            ec = r.equity_change_detail
            report.append(f"| 项目 | 同比变化 |\n")
            report.append(f"|------|----------|\n")
            report.append(f"| 负债同比变化 | {pct(ec.get('负债同比变化率'))} |\n")
            report.append(f"| 权益同比变化 | {pct(ec.get('权益同比变化率'))} |\n")
            report.append(f"| 未分配利润变化 | {pct(ec.get('未分配利润变化率'))} |\n")
            report.append(f"| 主要驱动因素 | {ec.get('主要驱动因素', 'N/A')} |\n")
            report.append("\n")

        # 资本结构趋势（扩展为全量指标）
        if r.debt_ratio_trend_years:
            # 构建资本结构趋势行（处理特殊值）
            def _fmt_slr(v):
                if v is None: return "N/A"
                if isinstance(v, float) and v == float('inf'): return "纯短期"
                return f"{v:.2f}"

            def _fmt_fcr(v):
                if v is None: return "N/A"
                if v == -9999: return "利息收入型"
                return pct(v)

            def _fmt_ic(v):
                if v is None: return "N/A"
                if v == -9999: return "利息收入型"
                return fmt_times(v)

            self._append_trend_table(report, f"资本结构{trend_label}趋势", r.debt_ratio_trend_years, [
                (r.debt_ratio_trend, "资产负债率", pct),
                (r.interest_debt_trend, "有息负债率", pct),
                (r.cash_coverage_trend, "现金覆盖率", fmt_times),
                (r.cash_ratio_trend, "货币资金占比", pct),
                (r.short_long_ratio_trend, "短长有息比", _fmt_slr),
                (r.finance_cost_ratio_trend, "融资成本率", _fmt_fcr),
                (r.interest_coverage_trend, "利息保障倍数", _fmt_ic),
                (r.equity_trend, "权益(亿元)", lambda v: f"{v/1e8:.2f}亿" if v is not None else "N/A"),
            ])

        # ===== 三、资产质量（14字段）=====
        report.append("---\n## 三、资产质量\n\n")

        report.append(f"| 指标 | 数值 | 说明 |\n")
        report.append(f"|------|------|------|\n")

        pa_type = ''
        if r.production_asset_ratio is not None:
            pa_type = '重资产' if r.production_asset_ratio > 40 else '轻资产'
        report.append(f"| 生产资产占比 | {pct(r.production_asset_ratio)} | {pa_type} |\n")

        report.append(f"| 生产资产ROE | {pct(r.production_asset_roe)} | 归母净利润/生产资产 |\n")
        report.append(f"| 应收账款占比 | {pct(r.receivables_ratio)} | (应收+票据)/总资产 |\n")
        report.append(f"| 应付账款占比 | {pct(r.payables_ratio)} | (应付+票据)/总资产 |\n")
        rp_status = ''
        if r.receivables_to_payables is not None:
            rp_status = '应收>应付' if r.receivables_to_payables > 1 else '应付>应收'
            report.append(f"| 应收/应付 | {r.receivables_to_payables:.2f} | {rp_status} |\n")
        else:
            report.append(f"| 应收/应付 | N/A | {rp_status} |\n")
        report.append(f"| 存货占比 | {pct(r.inventory_ratio)} | 存货/总资产 |\n")
        report.append(f"| 商誉占比 | {pct(r.goodwill_ratio)} | 商誉/总资产 |\n")
        report.append(f"| 非主业资产占比 | {pct(r.non_core_asset_ratio)} | (投资性房产+交易性金融资产)/总资产 |\n")
        report.append(f"| 研发资本化率 | {pct(r.r_d_capitalize_ratio)} | 开发支出/(研发费用+开发支出) |\n")
        report.append(f"| 其他应收款变化 | {pct(r.other_rece_change)} | 同比 |\n")
        report.append(f"| 剔除商誉后真实负债率 | {pct(r.real_debt_ratio_ex_goodwill)} | |\n")
        report.append(f"| 存货vs营收背离 | {r.inventory_revenue_diverge or 'N/A'} | |\n")
        if r.operating_cycle_days is not None:
            report.append(f"| 营业周期(存货周转天数+应收周转天数) | {r.operating_cycle_days:.0f}天 | 365/存货周转率+365/应收周转率 |\n")
        else:
            report.append("| 营业周期 | N/A | 365/存货周转率+365/应收周转率 |\n")
        report.append("\n")

        # 应收详情
        if r.receivables_detail:
            report.append("\n**应收款项详情**:\n\n")
            rd = r.receivables_detail
            report.append(f"| 项目 | 金额 | 占比 |\n")
            report.append(f"|------|------|------|\n")
            ar = rd.get('应收账款')
            nr = rd.get('应收票据')
            report.append(f"| 应收账款 | {fv(ar)} | {pct(rd.get('应收账款占比'))} |\n")
            report.append(f"| 应收票据 | {fv(nr)} | {pct(rd.get('应收票据占比'))} |\n")
            report.append(f"| 票据占比 | - | {pct(rd.get('票据占比'))} |\n")
            report.append("\n")

        # 资产质量详情
        if r.asset_quality_detail:
            report.append("\n**资产质量补充指标**:\n\n")
            aq = r.asset_quality_detail
            report.append(f"| 指标 | 数值 |\n")
            report.append(f"|------|------|\n")
            for k in ['总资产周转率', '存货周转率', '应收账款周转率', '流动比率', '速动比率',
                      '营收增长率', '利润增长率', '总资产增长率']:
                v = aq.get(k)
                if v is not None:
                    v_str = f'{v:.4f}' if isinstance(v, float) else str(v)
                    report.append(f"| {k} | {v_str} |\n")
            report.append("\n")

        # 生产资产趋势
        if r.production_asset_trend:
            report.append(f"\n**生产资产占比趋势({trend_label})**:\n\n")
            report.append(f"| 年份 | 占比 |\n")
            report.append(f"|------|------|\n")
            years = r.production_asset_trend_years or [f'T-{i}' for i in range(len(r.production_asset_trend))]
            for year, v in zip(years, r.production_asset_trend):
                report.append(f"| {year} | {pct(v)} |\n")
            report.append("\n")

        # 非主业资产明细
        if r.non_core_asset_detail:
            report.append("\n**非主业资产明细**:\n\n")
            report.append(f"| 项目 | 金额 |\n")
            report.append(f"|------|------|\n")
            for k, v in r.non_core_asset_detail.items():
                report.append(f"| {k} | {fv(v)} |\n")
            report.append("\n")

        # 研发分析
        if r.r_d_analysis:
            report.append("\n**研发支出分析**:\n\n")
            rd = r.r_d_analysis
            report.append(f"| 项目 | 数值 |\n")
            report.append(f"|------|------|\n")
            report.append(f"| 研发费用 | {fv(rd.get('研发费用'))} |\n")
            report.append(f"| 开发支出(资本化) | {fv(rd.get('开发支出(资本化)'))} |\n")
            report.append(f"| 资本化率 | {pct(rd.get('资本化率'))} |\n")
            report.append(f"| 策略分类 | {rd.get('策略分类', 'N/A')} |\n")
            report.append("\n")

        # 研发费用趋势
        if r.r_d_expense_trend_years:
            self._append_trend_table(report, f"研发费用{trend_label}趋势", r.r_d_expense_trend_years, [
                (r.r_d_expense_trend, "研发费用(亿元)", lambda v: f"{v/1e8:.2f}亿" if v is not None else "N/A"),
            ])

        # 资产质量全量趋势表（补齐原有10个未渲染的趋势数组）
        if r.inventory_ratio_trend_years or r.receivables_ratio_trend_years:
            _aq_trend_years = r.inventory_ratio_trend_years or r.receivables_ratio_trend_years or []
            self._append_trend_table(report, f"资产质量{trend_label}趋势", _aq_trend_years, [
                (r.inventory_ratio_trend, "存货占比", pct),
                (r.receivables_ratio_trend, "应收账款占比", pct),
                (r.goodwill_ratio_trend, "商誉占比", pct),
                (r.non_core_asset_ratio_trend, "非主业资产占比", pct),
                (r.other_receivable_ratio_trend, "其他应收款占比", pct),
                (r.payables_ratio_trend, "应付账款占比", pct),
                (r.production_asset_roe_trend, "生产资产ROE", pct),
                (r.receivables_to_payables_trend, "应收/应付比", lambda v: f"{v:.2f}" if v is not None else "N/A"),
            ])

        # ===== 四、风险信号 =====
        report.append("---\n## 四、风险信号\n\n")
        if r.red_flags:
            for flag in r.red_flags:
                report.append(f"- ⚠️ {flag}\n")
        else:
            report.append("- 未发现显著风险信号\n")
        report.append("\n")

        # ===== 五、核心优势 =====
        report.append("---\n## 五、核心优势\n\n")
        if r.strengths:
            for s in r.strengths:
                report.append(f"- ✅ {s}\n")
        else:
            report.append("- 未识别显著优势\n")
        report.append("\n")

        # ===== 附录：需核查事项 =====
        report.append("---\n## 附录：需年报附注核查的事项\n\n")
        report.append("以下信息无法从财务报表主表自动获取，需查阅年报附注：\n\n")
        report.append("1. 应收票据构成（银行承兑 vs 商业承兑比例）\n")
        report.append("2. 应收账款账龄分布（1年以上占比）\n")
        report.append("3. 坏账计提标准（是否严格）\n")
        report.append("4. 存货计价方法（先进先出/加权平均等）及是否变更\n")
        report.append("5. 在建工程具体进度和预计转固时间\n")
        report.append("6. 研发支出资本化比例（费用化 vs 资本化）\n")
        report.append("7. 固定资产折旧方法和年限\n")
        report.append("8. 关联交易详情和定价公允性\n")
        report.append("9. 交易性金融资产具体构成和持有目的\n")
        report.append("\n")
        report.append("---\n## 附录：年报附注人工核查清单\n")
        if r.all_manual_checks:
            report.append("| 序号 | 板块 | 指标 | 核查目标 |\n")
            report.append("|------|------|------|----------|\n")
            for i, chk in enumerate(r.all_manual_checks, 1):
                report.append(f"| {i} | {chk['section']} | {chk['indicator']} | {chk['target']} |\n")
        report.append("\n")

        # ===== 附录A：全量指标5年趋势总表（年报） =====
        if r.data_period != 'quarter' and r.roe_trend_years:
            report.append("---\n## 附录A：全量指标5年趋势总表（年报）\n\n")
            report.append("以下为所有具备5年历史数据的指标汇总，**加粗**表示该期间存在异常偏离。\n\n")

            # A1. 盈利与现金流
            report.append("### A1. 盈利与现金流\n\n")
            self._append_trend_table(report, "盈利与现金流5年趋势", r.roe_trend_years, [
                (r.roe_trend, "ROE", pct),
                (r.gross_margin_trend, "毛利率", pct),
                (r.net_profit_margin_parent_trend, "归母净利率", pct),
                (r.ocf_to_netprofit_trend, "OCF/净利润", fmt_times),
                (r.dividend_payout_trend, "股息发放率", pct),
                (r.dividend_ocf_ratio_trend, "分红/OCF比", pct),
            ])

            # A2. 资本结构
            report.append("\n### A2. 资本结构\n\n")
            self._append_trend_table(report, "资本结构5年趋势", r.debt_ratio_trend_years, [
                (r.debt_ratio_trend, "资产负债率", pct),
                (r.interest_debt_trend, "有息负债率", pct),
                (r.cash_coverage_trend, "现金覆盖率", fmt_times),
                (r.cash_ratio_trend, "货币资金占比", pct),
                (r.short_long_ratio_trend, "短长有息比", lambda v: _fmt_slr(v) if 'slr' in dir() else (f"{v:.2f}" if v is not None and v != float('inf') else ("纯短期" if v == float('inf') else "N/A"))),
                (r.finance_cost_ratio_trend, "融资成本率", lambda v: _fmt_fcr(v) if '_fmt_fcr' in dir() else (pct(v) if v is not None and v != -9999 else ("利息收入型" if v == -9999 else "N/A"))),
                (r.interest_coverage_trend, "利息保障倍数", lambda v: _fmt_ic(v) if '_fmt_ic' in dir() else (fmt_times(v) if v is not None and v != -9999 else ("利息收入型" if v == -9999 else "N/A"))),
            ])

            # A3. 资产质量
            report.append("\n### A3. 资产质量\n\n")
            _aq_years = r.inventory_ratio_trend_years or r.roe_trend_years or []
            self._append_trend_table(report, "资产质量5年趋势", _aq_years, [
                (r.production_asset_trend, "生产资产占比", pct),
                (r.receivables_ratio_trend, "应收账款占比", pct),
                (r.payables_ratio_trend, "应付账款占比", pct),
                (r.inventory_ratio_trend, "存货占比", pct),
                (r.goodwill_ratio_trend, "商誉占比", pct),
                (r.non_core_asset_ratio_trend, "非主业资产占比", pct),
                (r.other_receivable_ratio_trend, "其他应收款占比", pct),
                (r.production_asset_roe_trend, "生产资产ROE", pct),
                (r.r_d_expense_trend, "研发费用(亿元)", lambda v: f"{v/1e8:.2f}亿" if v is not None else "N/A"),
                (r.receivables_to_payables_trend, "应收/应付比", lambda v: f"{v:.2f}" if v is not None else "N/A"),
            ])
            report.append("\n")

        # ===== 附录B：全量指标4季度趋势总表（季报） =====
        if r.data_period == 'quarter' and r.roe_trend_years:
            report.append("---\n## 附录B：全量指标4季度趋势总表（季报）\n\n")
            report.append("以下为最近4个季度的指标变化，注意利润表/现金流量表指标已做累计→单季度差分处理。\n\n")

            # B1. 盈利与现金流
            report.append("### B1. 盈利与现金流（单季度值）\n\n")
            self._append_trend_table(report, "盈利与现金流4季度趋势", r.roe_trend_years, [
                (r.roe_trend, "ROE", pct),
                (r.net_profit_margin_parent_trend, "归母净利率(单季)", pct),
                (r.ocf_to_netprofit_trend, "OCF/净利润(单季)", fmt_times),
                (r.dividend_ocf_ratio_trend, "分红/OCF比", pct),
            ])

            # B2. 资本结构
            report.append("\n### B2. 资本结构（时点值）\n\n")
            self._append_trend_table(report, "资本结构4季度趋势", r.debt_ratio_trend_years, [
                (r.debt_ratio_trend, "资产负债率", pct),
                (r.interest_debt_trend, "有息负债率", pct),
                (r.cash_coverage_trend, "现金覆盖率", fmt_times),
                (r.cash_ratio_trend, "货币资金占比", pct),
                (r.finance_cost_ratio_trend, "融资成本率(单季)", lambda v: "利息收入型" if v == -9999 else (pct(v) if v is not None else "N/A")),
                (r.interest_coverage_trend, "利息保障倍数(单季)", lambda v: "利息收入型" if v == -9999 else (fmt_times(v) if v is not None else "N/A")),
            ])

            # B3. 资产质量
            report.append("\n### B3. 资产质量（时点值）\n\n")
            _aq_q_years = r.roe_trend_years
            self._append_trend_table(report, "资产质量4季度趋势", _aq_q_years, [
                (r.production_asset_trend, "生产资产占比", pct),
                (r.inventory_ratio_trend, "存货占比", pct),
                (r.receivables_ratio_trend, "应收账款占比", pct),
                (r.goodwill_ratio_trend, "商誉占比", pct),
                (r.payables_ratio_trend, "应付账款占比", pct),
                (r.receivables_to_payables_trend, "应收/应付比", lambda v: f"{v:.2f}" if v is not None else "N/A"),
            ])
            report.append("\n")

        report.append("---\n")
        report.append(f"*报告生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}*\n")

        return ''.join(report)

    # ------------------------------------------------------------------
    # 横向对比报告
    # ------------------------------------------------------------------
    def generate_peer_comparison_report(
        self,
        results: List[L1AnalysisResult],
        title: str = None
    ) -> str:
        """
        生成同行业多股票关键指标横向对比报告。

        用法示例：
            analyzer = L1FinancialAnalyzerEnhanced()
            r1 = analyzer.analyze('000596', '古井贡酒')
            r2 = analyzer.analyze('600809', '山西汾酒')
            report = analyzer.generate_peer_comparison_report([r1, r2], title='白酒行业对比')
        """
        if not results:
            return "# 错误：未提供任何分析结果\n"

        report: List[str] = []

        # ---- 标题 ----
        report_title = title or f"{results[0].name if results[0].name else results[0].code}等同行业关键指标横向对比报告"
        report.append(f"# {report_title}\n")
        report.append(f"**生成时间**: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')} | **对比股票数**: {len(results)}\n\n")

        # ---- 格式化辅助函数 ----
        def _fmt(attr: str, val) -> str:
            if val is None:
                return 'N/A'
            # 利息收入型特殊标记
            if val == -9999:
                if attr in ['finance_cost_ratio', 'interest_coverage']:
                    return '利息收入型'
                return 'N/A'
            # 倍数字段
            if attr in ['cash_coverage', 'ocf_to_netprofit', 'short_long_ratio', 'interest_coverage']:
                if isinstance(val, float) and val == float('inf'):
                    return '纯短期'
                return f"{val:.2f}倍"
            # 天数字段
            if attr == 'operating_cycle_days':
                return f"{val:.1f}天"
            # 金额字段（元→亿）
            if attr in ['interest_bearing_debt']:
                return f"{val / 1e8:.2f}亿"
            # 布尔字段
            if isinstance(val, bool):
                return '是' if val else '否'
            # 百分比字段（默认）
            if isinstance(val, (int, float)):
                return f"{val:.2f}%"
            return str(val)

        # ---- 指标定义：分类 + 方向 ----
        # direction: 'higher'=越高越好, 'lower'=越低越好, 'neutral'=中性, 'bool'=布尔
        metric_groups = [
            ("一、盈利能力", [
                ('roe', 'ROE', 'higher'),
                ('gross_margin', '毛利率', 'higher'),
                ('net_margin', '净利率', 'higher'),
                ('net_profit_margin_parent', '归母净利率', 'higher'),
                ('ocf_to_netprofit', '经营现金流/归母净利润', 'neutral'),
                ('dividend_payout', '股息发放率', 'higher'),
                ('dividend_ocf_ratio', '分红/经营现金流比', 'higher'),
                ('fin_assets_ratio', '金融资产投资占比', 'lower'),
            ]),
            ("二、资本结构", [
                ('debt_ratio', '资产负债率', 'lower'),
                ('interest_debt', '有息负债率', 'lower'),
                ('cash_coverage', '现金覆盖率', 'higher'),
                ('short_term_debt_ratio', '短期负债/总负债', 'neutral'),
                ('long_term_debt_ratio', '长期负债/总负债', 'neutral'),
                ('operating_debt_ratio', '经营性负债/总负债', 'neutral'),
                ('financing_debt_ratio', '融资性负债/总负债', 'lower'),
                ('interest_bearing_debt', '有息负债总额', 'lower'),
                ('short_long_ratio', '短期有息/长期有息比', 'neutral'),
                ('finance_cost_ratio', '融资成本率', 'lower'),
                ('interest_coverage', '利息保障倍数', 'higher'),
            ]),
            ("三、资产质量", [
                ('production_asset_ratio', '生产资产占比', 'neutral'),
                ('production_asset_roe', '生产资产ROE', 'higher'),
                ('receivables_ratio', '应收账款占比', 'lower'),
                ('payables_ratio', '应付账款占比', 'neutral'),
                ('receivables_to_payables', '应收/应付', 'neutral'),
                ('goodwill_ratio', '商誉占比', 'lower'),
                ('inventory_ratio', '存货占比', 'lower'),
                ('r_d_capitalize_ratio', '研发资本化率', 'lower'),
                ('non_core_asset_ratio', '非主业资产占比', 'lower'),
                ('other_rece_change', '其他应收款变化', 'lower'),
                ('real_debt_ratio_ex_goodwill', '剔除商誉后真实负债率', 'lower'),
                ('inventory_revenue_diverge', '存货vs营收背离', 'bool'),
            ]),
            ("四、现金流与风险", [
                ('cash_ratio', '货币资金占比', 'neutral'),
                ('operating_cycle_days', '现金周转天数', 'lower'),
                ('cash_excess_signal', '货币资金占比超30%', 'bool'),
                ('high_cash_high_debt', '高现金高负债', 'bool'),
                ('inefficient_cash', '低效现金', 'bool'),
                ('other_monetary', '其他货币资金占比', 'lower'),
            ]),
        ]

        # ---- 股票概览表 ----
        report.append("## 股票概览\n\n")
        report.append("| 项目 | " + " | ".join([f"{r.code} {r.name}" for r in results]) + " |\n")
        report.append("|------|" + "|".join(["------"] * len(results)) + "|\n")
        report.append("| 数据期间 | " + " | ".join([r.report_date or 'N/A' for r in results]) + " |\n")
        report.append("| 综合评分 | " + " | ".join([f"{r.overall_score:.1f}" if r.overall_score is not None else 'N/A' for r in results]) + " |\n")
        report.append("| L1通过 | " + " | ".join(['✅ 是' if r.pass_l1 else '❌ 否' for r in results]) + " |\n")
        report.append("\n")

        # ---- 各分类指标对比表 ----
        for group_title, metrics in metric_groups:
            report.append(f"## {group_title}\n\n")
            # 表头
            header = "| 指标 | " + " | ".join([f"{r.code}" for r in results]) + " |"
            report.append(header + "\n")
            report.append("|------|" + "|".join(["------"] * len(results)) + "|\n")

            for attr, label, direction in metrics:
                # 提取所有股票的值
                vals = [getattr(r, attr, None) for r in results]
                # 数值型指标才做高亮
                numeric_vals = []
                for v in vals:
                    if isinstance(v, (int, float)) and v != -9999:
                        numeric_vals.append(v)
                    else:
                        numeric_vals.append(None)

                # 计算最优/最劣
                valid_pairs = [(i, v) for i, v in enumerate(numeric_vals) if v is not None]
                best_idx, worst_idx = None, None
                if valid_pairs and direction in ('higher', 'lower'):
                    if direction == 'higher':
                        best_idx = max(valid_pairs, key=lambda x: x[1])[0]
                        worst_idx = min(valid_pairs, key=lambda x: x[1])[0]
                    else:  # lower
                        best_idx = min(valid_pairs, key=lambda x: x[1])[0]
                        worst_idx = max(valid_pairs, key=lambda x: x[1])[0]

                # 布尔指标："否"更好
                if direction == 'bool':
                    bool_pairs = [(i, v) for i, v in enumerate(vals) if isinstance(v, bool)]
                    if bool_pairs:
                        best_idx = [i for i, v in bool_pairs if v is False]
                        worst_idx = [i for i, v in bool_pairs if v is True]
                        best_idx = best_idx[0] if best_idx else None
                        worst_idx = worst_idx[0] if worst_idx else None

                # 组装该行
                cells = [label]
                for i, (r_obj, val) in enumerate(zip(results, vals)):
                    cell = _fmt(attr, val)
                    # 高亮
                    if direction == 'bool':
                        if val is True:
                            cell = f"⚠️ {cell}"
                        elif val is False:
                            cell = f"✅ {cell}"
                    elif best_idx is not None and worst_idx is not None:
                        if i == best_idx:
                            cell = f"✅ {cell}"
                        elif i == worst_idx:
                            cell = f"⚠️ {cell}"
                    cells.append(cell)

                report.append("| " + " | ".join(cells) + " |\n")

            report.append("\n")

        # ---- 风险信号汇总 ----
        report.append("## 五、风险信号汇总\n\n")
        has_any_flag = False
        for r in results:
            if r.red_flags:
                has_any_flag = True
                report.append(f"**{r.code} {r.name}**:\n")
                for flag in r.red_flags:
                    report.append(f"- ⚠️ {flag}\n")
                report.append("\n")
        if not has_any_flag:
            report.append("所有对比股票均未发现显著风险信号。\n\n")

        # ---- 核心优势汇总 ----
        report.append("## 六、核心优势汇总\n\n")
        has_any_strength = False
        for r in results:
            if r.strengths:
                has_any_strength = True
                report.append(f"**{r.code} {r.name}**:\n")
                for s in r.strengths:
                    report.append(f"- ✅ {s}\n")
                report.append("\n")
        if not has_any_strength:
            report.append("所有对比股票均未识别显著优势。\n\n")

        # ---- 图例 ----
        report.append("---\n")
        report.append("**图例**: ✅ = 该指标表现最优 | ⚠️ = 该指标表现最劣\n\n")
        report.append(f"*报告生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}*\n")

        return ''.join(report)


if __name__ == '__main__':
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))

    usage = "用法: python analyzer_l1_enhanced_complete.py <股票代码> [股票名称]\n示例: python analyzer_l1_enhanced_complete.py 600809 山西汾酒"

    try:
        from data_loader_fixed import get_stock_profile_safe

        code = sys.argv[1] if len(sys.argv) > 1 else None
        name = sys.argv[2] if len(sys.argv) > 2 else ''

        if not code:
            print(usage)
            sys.exit(1)

        print(f"L1财报双维度分析: {code} {name}")
        profile = get_stock_profile_safe(code)

        analyzer = L1FinancialAnalyzerEnhanced(debug=True)
        dual_result = analyzer.analyze_dual(code, name, profile)

        print("\n" + "="*70)
        print(f"{code} {name} L1分析结果:")
        print("="*70)
        print(dual_result.dual_report)

        # 保存报告（文件名包含股票代码和名称）
        safe_name = name.replace(' ', '') if name else ''
        output_file = f"{code}_{safe_name}_L1分析报告.md" if safe_name else f"{code}_L1分析报告.md"
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(dual_result.dual_report)
        print(f"\n报告已保存到: {output_file}")
            
    except ImportError as e:
        print(f"错误: 无法导入模块 - {e}")
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
