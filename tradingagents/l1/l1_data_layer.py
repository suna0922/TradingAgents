#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L1 数据提取层

职责：从各种原始数据格式（三大报表 / stock_financial_abstract / analysis_indicator）
      中提取标准化数值。纯数据转换，无业务逻辑。

被 L1FinancialAnalyzerEnhanced 继承使用（Mixin 方式）。
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional


class L1DataExtractor:
    """L1 数据提取层：从各种数据源标准化提取指标"""

    COLUMN_MAPPING = {
        # ===== 资本结构 =====
        '负债合计': ['TOTAL_LIABILITIES', 'TOTAL_LIAB', '负债合计'],
        '资产合计': ['TOTAL_ASSETS', 'TOTAL_ASSET', '资产合计', '资产总计'],
        '短期借款': ['SHORT_LOAN', 'ST_LOAN', 'SHORT_TERM_LOAN', '短期借款'],
        '长期借款': ['LONG_LOAN', 'LT_LOAN', 'LONG_TERM_LOAN', '长期借款'],
        '长期应付款': ['LONG_PAYABLE', '长期应付款'],
        '应付债券': ['BOND_PAYABLE', 'BONDS', '应付债券'],
        '流动负债合计': ['TOTAL_CURRENT_LIAB', 'CURRENT_LIAB', 'CURRENT_LIABILITY', '流动负债合计'],
        '非流动负债合计': ['TOTAL_NONCURRENT_LIAB', 'NONCURRENT_LIAB', '非流动负债合计'],
        '一年内到期的非流动负债': ['NONCURRENT_LIAB_1YEAR', '一年内到期的非流动负债'],
        '货币资金': ['MONETARYFUNDS', 'CASH', 'CASH_EQUIVALENT', '货币资金', '现金及现金等价物'],
        '其他货币资金': ['OTHER_MONETARY', 'OTHER_CASH', '其他货币资金'],
        '长期待摊费用': ['LONG_DEFER_EXPENSE', '长期待摊费用'],
        # 负债来源分析
        '应付账款': ['ACCOUNTS_PAY', 'ACCOUNTS_PAYABLE', '应付账款'],
        '应付票据': ['BILL_PAY', 'NOTES_PAYABLE', '应付票据', 'NOTE_PAYABLE'],
        '预收款项': ['ADVANCE_RECEIPTS', 'PREPAY_RECEIVABLE', '预收款项'],
        '合同负债': ['CONTRACT_LIAB', '合同负债'],
        '应付职工薪酬': ['EMPLOYEE_BENEFIT_PAY', 'STAFF_PAYABLE', 'STAFF_SALARY_PAYABLE', 'SALARY_PAYABLE', '应付职工薪酬'],
        '应交税费': ['TAX_PAYABLE', 'TAXES_PAYABLE', '应交税费'],
        '所有者权益合计': ['TOTAL_EQUITY', 'TOTAL_OWNERS_EQUITY', '所有者权益合计', 'TOTAL_PARENT_EQUITY'],
        '未分配利润': ['RETAINED_EARNINGS', 'RETAINED_PROFIT', '未分配利润', 'UNASSIGNED_PROFIT'],
        '股本': ['TOTAL_SHARE', 'SHARE_CAPITAL', '股本'],
        '资本公积': ['CAPITAL_RESERVE', '资本公积'],
        '盈余公积': ['SURPLUS_RESERVE', '盈余公积'],
        '其他综合收益': ['OTHER_COMPREHENSIVE_INCOME', 'OCI', '其他综合收益'],
        # ===== 资产项目 =====
        '固定资产': ['FIXED_ASSET', '固定资产'],
        '固定资产清理': ['FIXED_ASSET_DISPOSAL', '固定资产清理'],
        '在建工程': ['CIP', 'CONSTRUCTION_IN_PROCESS', 'CONSTRUCTION', '在建工程'],
        '工程物资': ['CONSTRUCTION_MATERIAL', '工程物资', 'PROJECT_MATERIAL'],
        '无形资产': ['INTANGIBLE_ASSET', 'INTANGIBLE', '无形资产'],
        '商誉': ['GOODWILL', '商誉'],
        '应收账款': ['ACCOUNTS_RECE', 'AR', 'ACCOUNTS_RECEIVABLE', '应收账款'],
        '应收票据': ['NOTE_RECE', 'NOTE_RECEIVABLE', '应收票据'],
        '存货': ['INVENTORY', '存货'],
        '投资性房地产': ['INVEST_REALESTATE', 'INVEST_REAL_ESTATE', '投资性房地产'],
        '交易性金融资产': ['TRADE_FINASSET', 'TRADING_FIN_ASSET', '交易性金融资产'],
        # 资产质量分析
        '其他应收款': ['OTHER_RECE', '其他应收款'],
        '其他权益工具投资': ['OTHER_EQUITY_INVEST', '其他权益工具投资'],
        '长期股权投资': ['LONG_EQUITY_INVEST', '长期股权投资'],
        '债权投资': ['CREDITOR_INVEST', '债权投资'],
        '其他债权投资': ['OTHER_DEBT_INVEST', '其他债权投资', 'OTHER_CREDITOR_INVEST'],
        '开发支出': ['DEVELOP_EXPENDITURE', '开发支出'],
        # ===== 利润表/现金流 =====
        '净利润': ['PARENT_NETPROFIT', 'NETPROFIT', 'NET_PROFIT', '净利润', '归属母公司净利润'],
        '归母净利润': ['PARENT_NETPROFIT', '归属母公司股东的净利润', '归母净利润'],
        '营业收入': ['OPERATE_INCOME', 'REVENUE', 'OPERATE_INCOME', '营业收入', '营业总收入', 'TOTAL_OPERATE_INCOME'],
        '经营活动现金流量净额': ['NETCASH_OPERATE', 'OCF', 'CASH_FLOW_OPERATE', '经营活动产生的现金流量净额'],
        '税前利润': ['TOTAL_PROFIT', 'PRETAX_PROFIT', '利润总额', '税前利润'],
        '利息费用': ['INTEREST_EXPENSE', '利息费用'],
        # 财务指标增强
        '净资产收益率': ['ROE', 'RETURN_EQUITY', '净资产收益率'],
        '净利率': ['NET_MARGIN', 'NET_PROFIT_RATIO', '净利率', '净利润率'],
        '毛利率': ['GROSS_MARGIN', '毛利率'],
        '财务费用': ['FINANCE_EXPENSE', 'FINANCIAL_EXPENSE', 'FINANCE_COST', '财务费用'],
        '研发费用': ['RESEARCH_EXPENSE', '研发费用', 'ME_RESEARCH_EXPENSE', 'RESEARCH_DEVELOP_EXPENSE', 'RD_EXPENSE'],
        '公允价值变动收益': ['FAIR_VALUE_CHANGE', '公允价值变动收益', 'FAIRVALUE_CHANGE_INCOME'],
        '信用减值损失': ['CREDIT_IMPAIRMENT', '信用减值损失', 'CREDIT_IMPAIRMENT_LOSS'],
        '坏账准备': ['BAD_DEBT', 'PROVISION_BAD_DEBT', '坏账准备'],
        '利息收入': ['INTEREST_INCOME', '利息收入', 'INTEREST_REV'],
        '营业成本': ['OPERATE_COST', 'COST_OF_GOODS_SOLD', '营业成本'],
        '营业总成本': ['TOTAL_OPERATE_COST', '营业总成本'],
        # ===== 资产负债表补充 =====
        # 流动资产
        '预付款项': ['PREPAYMENT', '预付款项'],
        '合同资产': ['CONTRACT_ASSET', '合同资产'],
        '其他流动资产': ['OTHER_CURRENT_ASSET', '其他流动资产'],
        '应收款项融资': ['FINANCE_RECE', '应收款项融资'],
        '应收股利': ['DIVIDEND_RECE', '应收股利'],
        '应收利息': ['INTEREST_RECE', '应收利息'],
        '应收补贴款': ['SUBSIDY_RECE', '应收补贴款'],
        '应收出口退税': ['EXPORT_REFUND_RECE', '应收出口退税'],
        '流动资产合计': ['TOTAL_CURRENT_ASSETS', '流动资产合计'],
        # 非流动资产
        '长期待摊费用': ['LONG_PREPAID_EXPENSE', '长期待摊费用'],
        '递延所得税资产': ['DEFER_TAX_ASSET', '递延所得税资产'],
        '其他非流动资产': ['OTHER_NONCURRENT_ASSET', '其他非流动资产'],
        '其他非流动金融资产': ['OTHER_NONCURRENT_FINASSET', '其他非流动金融资产'],
        '使用权资产': ['RIGHT_OF_USE_ASSET', '使用权资产'],
        '油气资产': ['OIL_GAS_ASSET', '油气资产'],
        '消耗性生物资产': ['CONSUMPTIVE_BIOLOGICAL_ASSET', '消耗性生物资产'],
        '生产性生物资产': ['PRODUCTIVE_BIOLOGY_ASSET', '生产性生物资产'],
        '非流动资产合计': ['TOTAL_NONCURRENT_ASSETS', '非流动资产合计'],
        '一年内到期的非流动资产': ['NONCURRENT_ASSET_1YEAR', '一年内到期的非流动资产'],
        '库存股': ['TREASURY_SHARES', '库存股'],
        # 流动负债
        '应付股利': ['DIVIDEND_PAYABLE', '应付股利'],
        '租赁负债': ['LEASE_LIAB', 'LEASE_LIABILITIES', '租赁负债'],
        '其他应付款': ['OTHER_PAYABLE', '其他应付款'],
        '递延收益': ['DEFER_INCOME', '递延收益'],
        '其他应付款合计': ['TOTAL_OTHER_PAYABLE', '其他应付款合计'],
        '预计负债': ['ESTIMATED_LIABILITIES', '预计负债'],
        '其他流动负债': ['OTHER_CURRENT_LIAB', '其他流动负债'],
        '应付利息': ['INTEREST_PAYABLE', '应付利息'],
        # 非流动负债
        '递延所得税负债': ['DEFER_TAX_LIAB', '递延所得税负债'],
        '长期应付职工薪酬': ['LONG_STAFFSALARY_PAYABLE', '长期应付职工薪酬'],
        '其他非流动负债': ['OTHER_NONCURRENT_LIAB', '其他非流动负债'],
        # 所有者权益
        '少数股东权益': ['MINORITY_EQUITY', '少数股东权益'],
        '其他权益工具': ['OTHER_EQUITY_INSTRUMENTS', '其他权益工具'],
        '负债和所有者权益总计': ['TOTAL_LIAB_EQUITY', '负债和所有者权益总计'],
        '其他应收款合计': ['TOTAL_OTHER_RECE', '其他应收款合计'],
        # ===== 利润表补充 =====
        '管理费用': ['MANAGE_EXPENSE', '管理费用'],
        '销售费用': ['SALE_EXPENSE', '销售费用'],
        '所得税费用': ['INCOME_TAX', '所得税费用'],
        '营业利润': ['OPERATE_PROFIT', '营业利润'],
        '营业税金及附加': ['OPERATE_TAX_ADD', '营业税金及附加'],
        '投资收益': ['INVEST_INCOME', '投资收益'],
        '对联营和合营企业投资收益': ['INVEST_JOINT_INCOME', '对联联营企业和合营企业的投资收益'],
        '资产减值损失': ['ASSET_IMPAIRMENT_LOSS', '资产减值损失'],
        # 借贷方向：贷方正值（转回），借方正值（计提）
        '信用减值损失_贷方': ['CREDIT_IMPAIRMENT_INCOME', '信用减值损失'],
        '资产减值损失_贷方': ['ASSET_IMPAIRMENT_INCOME', '资产减值损失'],
        '其他收益': ['OTHER_INCOME', '其他收益'],
        '营业外收入': ['NONBUSINESS_INCOME', '营业外收入'],
        '营业外支出': ['NONBUSINESS_EXPENSE', '营业外支出'],
        '其他业务收入': ['OTHER_BUSINESS_INCOME', '其他业务收入'],
        '其他业务成本': ['OTHER_BUSINESS_COST', '其他业务成本'],
        '资产处置收益': ['ASSET_DISPOSAL_INCOME', '资产处置收益'],
        '营业总收入': ['TOTAL_OPERATE_INCOME', '营业总收入'],
        '综合收益总额': ['TOTAL_COMPRE_INCOME', '综合收益总额'],
        '基本每股收益': ['BASIC_EPS', '基本每股收益'],
        '稀释每股收益': ['DILUTED_EPS', '稀释每股收益'],
        '扣非归母净利润': ['DEDUCT_PARENT_NETPROFIT', '扣除非经常性损益后的净利润'],
        '持续经营净利润': ['CONTINUED_NETPROFIT', '持续经营净利润'],
        '少数股东损益': ['MINORITY_INTEREST', '少数股东损益'],
        '利息费用（明细）': ['FE_INTEREST_EXPENSE', '利息费用'],
        '利息收入（明细）': ['FE_INTEREST_INCOME', '利息收入'],
        '其他权益工具公允价值变动': ['OTHERRIGHT_FAIRVALUE_CHANGE', '其他权益工具公允价值变动'],
        # ===== 现金流量表补充 =====
        '销售商品提供劳务收到的现金': ['SALES_SERVICES', '销售商品、提供劳务收到的现金'],
        '购买商品接受劳务支付的现金': ['BUY_SERVICES', '购买商品、接受劳务支付的现金'],
        '支付给职工的现金': ['PAY_STAFF_CASH', '支付给职工以及为职工支付的现金'],
        '支付的各项税费': ['PAY_ALL_TAX', '支付的各项税费'],
        '投资活动现金流量净额': ['NETCASH_INVEST', '投资活动产生的现金流量净额'],
        '筹资活动现金流量净额': ['NETCASH_FINANCE', '筹资活动产生的现金流量净额'],
        '分配股利偿付利息支付的现金': ['ASSIGN_DIVIDEND_PROFIT', 'ASSIGN_DIVIDEND_PORFIT', '分配股利、利润或偿付利息支付的现金'],
        '分配股利、利润或偿付利息支付的现金': ['ASSIGN_DIVIDEND_PROFIT', 'ASSIGN_DIVIDEND_PORFIT', '分配股利、利润或偿付利息支付的现金'],
        '购建固定资产等支付的现金': ['CONSTRUCT_LONG_ASSET', '购建固定资产、无形资产和其他长期资产支付的现金'],
        '投资支付的现金': ['INVEST_PAY_CASH', '投资支付的现金'],
        '收回投资收到的现金': ['WITHDRAW_INVEST', '收回投资收到的现金'],
        '取得投资收益收到的现金': ['RECEIVE_INVEST_INCOME', '取得投资收益收到的现金'],
        '偿还债务支付的现金': ['PAY_DEBT_CASH', '偿还债务支付的现金'],
        '取得借款收到的现金': ['RECEIVE_LOAN_CASH', '取得借款收到的现金'],
        '收到其他经营活动现金': ['RECEIVE_OTHER_OPERATE', '收到的其他与经营活动有关的现金'],
        '支付其他经营活动现金': ['PAY_OTHER_OPERATE', '支付的其他与经营活动有关的现金'],
        '支付其他筹资活动现金': ['PAY_OTHER_FINANCE', '支付的其他与筹资活动有关的现金'],
        '收到的税费返还': ['RECEIVE_TAX_REFUND', '收到的税费返还'],
        '经营活动现金流入小计': ['TOTAL_OPERATE_INFLOW', '经营活动现金流入小计'],
        '经营活动现金流出小计': ['TOTAL_OPERATE_OUTFLOW', '经营活动现金流出小计'],
        '投资活动现金流入小计': ['TOTAL_INVEST_INFLOW', '投资活动现金流入小计'],
        '投资活动现金流出小计': ['TOTAL_INVEST_OUTFLOW', '投资活动现金流出小计'],
        '筹资活动现金流入小计': ['TOTAL_FINANCE_INFLOW', '筹资活动现金流入小计'],
        '筹资活动现金流出小计': ['TOTAL_FINANCE_OUTFLOW', '筹资活动现金流出小计'],
        '吸收投资收到的现金': ['ACCEPT_INVEST_CASH', '吸收投资收到的现金'],
        '期初现金及现金等价物余额': ['BEGIN_CCE', '期初现金及现金等价物余额'],
        '期末现金及现金等价物余额': ['END_CCE', '期末现金及现金等价物余额'],
        '现金及现金等价物净增加额': ['CCE_ADD', '现金及现金等价物净增加额'],
    }

    def _find_col(self, df: pd.DataFrame, col_key: str) -> Optional[str]:
        """查找列名"""
        if df is None or df.empty:
            return None
        
        possible_names = self.COLUMN_MAPPING.get(col_key, [col_key])
        
        for col in df.columns:
            col_upper = str(col).upper().strip()
            
            # 跳过包含_EQUITY的列
            if '_EQUITY' in col_upper and col_key == '负债合计':
                continue
            
            for name in possible_names:
                name_upper = name.upper().strip()
                
                if col_upper == name_upper:
                    return col
                
                if not name_upper.isascii() and name_upper in col_upper:
                    return col
        
        return None
    
    def _get_latest_row(self, df: pd.DataFrame) -> Optional[Dict]:
        """取最新一行（可能是季报或年报）"""
        if df is None or df.empty:
            return None
        # 数据是升序排列（1993→2024），最后一行为最新
        return df.iloc[-1].to_dict()
    
    def _get_annual_row(self, df: pd.DataFrame) -> Optional[Dict]:
        """取最新年报数据
        注意：akshare返回的数据按时间升序排列（1993→2025），iloc[-1]是最新数据。
        年报是12-31的报告期，取最后一个12-31的行即可得到最新年报。
        """
        if df is None or df.empty:
            return None

        # 方法1: 通过REPORT_DATE_NAME包含"年报"筛选，取最后一个（最新）
        if 'REPORT_DATE_NAME' in df.columns:
            annual_mask = df['REPORT_DATE_NAME'].astype(str).str.contains('年报', na=False)
            if annual_mask.any():
                # iloc[-1]取最后一个匹配行（最新年报）
                return df[annual_mask].iloc[-1].to_dict()

        # 方法2: 通过REPORT_DATE筛选（12-31为年报），取最后一个（最新）
        if 'REPORT_DATE' in df.columns:
            annual_mask = df['REPORT_DATE'].astype(str).str.contains('12-31', na=False)
            if annual_mask.any():
                return df[annual_mask].iloc[-1].to_dict()

        # 方法3: 直接取最后一行（升序排列的最后是最新的）
        return df.iloc[-1].to_dict()
    
    def _get_latest_quarter_row(self, df: pd.DataFrame) -> Optional[Dict]:
        """取最新季报数据（非年报）"""
        if df is None or df.empty:
            return None
        
        # 通过REPORT_DATE_NAME不包含"年报"筛选
        if 'REPORT_DATE_NAME' in df.columns:
            quarter_mask = ~df['REPORT_DATE_NAME'].astype(str).str.contains('年报', na=False)
            if quarter_mask.any():
                return df[quarter_mask].iloc[-1].to_dict()
        
        # 备选：取倒数第二行（如果不是年报的话）
        if len(df) > 1:
            return df.iloc[-2].to_dict()
        return df.iloc[-1].to_dict()
    
    def _get_report_date(self, df: pd.DataFrame, row_dict: Dict) -> str:
        """从行数据中提取报告期信息"""
        if 'REPORT_DATE_NAME' in row_dict:
            return row_dict['REPORT_DATE_NAME']
        if 'REPORT_DATE' in row_dict:
            return str(row_dict['REPORT_DATE'])
        return '未知'
    
    def _extract_rows_by_period(self, bal: pd.DataFrame, prof: pd.DataFrame, 
                                  cash: pd.DataFrame, fin: pd.DataFrame, 
                                  period: str = 'annual') -> Dict[str, Dict]:
        """根据期间类型提取各表的行数据"""
        result = {'bal': None, 'prof': None, 'cash': None, 'fin': None}
        
        if period == 'annual':
            get_row = self._get_annual_row
        else:
            get_row = self._get_latest_row
        
        result['bal'] = get_row(bal)
        result['prof'] = get_row(prof)
        result['cash'] = get_row(cash)
        result['fin'] = get_row(fin)
        
        return result
    
    def _safe_float(self, val) -> Optional[float]:
        """安全转换为浮点数"""
        import math
        if val is None or (isinstance(val, str) and val.strip() == ''):
            return None
        try:
            f = float(val)
            # 处理nan和inf
            if math.isnan(f) or math.isinf(f):
                return None
            return f
        except (ValueError, TypeError):
            return None

    def _get_val(self, df_row: Dict, col: Optional[str]) -> Optional[float]:
        """安全获取字段值，区分「列不存在→None」和「值确实为0→0.0」。
        用于报告字段：列不存在时返回None，报告显示"未披露"；列存在时返回实际值（含0）。
        注意：用于计算分母的必填字段（如total_assets）请继续使用 _safe_float(... or 0)。
        """
        if col is None:
            return None
        return self._safe_float(df_row.get(col, 0))

    # ====================================================================
    # stock_financial_abstract 统一数据提取层
    # ====================================================================
    # stock_financial_abstract 格式:
    #   列0("选项"): 指标分类 (常用指标/每股指标/盈利能力/...)
    #   列1("指标"): 指标名称 (归母净利润/营业总收入/毛利率/...)
    #   列2+: 报告期数值，按 **降序** 排列 (20260331 → 19931231)
    # ====================================================================

    def _is_abstract_format(self, fin: pd.DataFrame) -> bool:
        """判断fin是否为stock_financial_abstract格式"""
        if fin is None or fin.empty:
            return False
        return '选项' in fin.columns and '指标' in fin.columns

    def _get_period_cols(self, fin: pd.DataFrame) -> List[str]:
        """获取abstract中所有报告期列（降序：最新在前）"""
        return [str(c) for c in fin.columns if c not in ('选项', '指标') and str(c).isdigit()]

    def _select_target_period(self, period_cols: List[str], period_type: str = 'annual') -> str:
        """
        从报告期列中选择目标期间。
        period_cols是降序排列（最新在前）。

        年报选择策略（A股年报次年4月底前发布）：
          - 优先使用数据中已有的最新年报（数据已发布即说明已审计）
          - 如果当前月份 < 4 且最新年报是当年（可能尚未审计），退而用上一年
        季报：直接取第一个（最新报告期，不一定非年报）
        """
        import datetime
        now = datetime.datetime.now()
        current_year = now.year
        current_month = now.month

        if period_type == 'annual':
            annual_cols = [c for c in period_cols if c.endswith('1231')]
            if not annual_cols:
                # 没有年报列，退而求其次取最新期
                return period_cols[0] if period_cols else None

            # 策略：直接使用数据中已有的最新年报
            # stock_financial_abstract只有在年报发布后才会包含该年数据
            # 所以 annual_cols[0] 就是最新可用年报
            latest_annual = annual_cols[0]
            latest_year = int(latest_annual[:4])

            # 安全检查：如果最新年报是当年且月份<4（年报尚未发布），退用上一年
            if latest_year == current_year and current_month < 4:
                if len(annual_cols) > 1:
                    return annual_cols[1]
                return latest_annual

            return latest_annual

        else:  # quarter - 取最新一期
            return period_cols[0] if period_cols else None

    def _get_indicator_value(self, fin: pd.DataFrame, indicator_name: str,
                             target_period: str, exact_match: bool = False) -> Optional[float]:
        """
        从abstract格式中提取指标值。

        Args:
            fin: stock_financial_abstract DataFrame
            indicator_name: 要查找的指标名（支持包含匹配）
            target_period: 报告期列名（如"20241231"）
            exact_match: True=精确匹配指标名，False=包含匹配
        """
        if fin is None or fin.empty or target_period is None:
            return None
        if target_period not in fin.columns:
            return None

        for idx, row in fin.iterrows():
            name = str(row.get('指标', '')).strip()
            if exact_match:
                if name == indicator_name:
                    return self._safe_float(row.get(target_period))
            else:
                if indicator_name in name:
                    return self._safe_float(row.get(target_period))

        return None

    def _get_all_indicator_values(self, fin: pd.DataFrame, target_period: str) -> Dict[str, float]:
        """获取指定报告期下所有指标的值"""
        result = {}
        if fin is None or fin.empty or target_period not in fin.columns:
            return result
        for idx, row in fin.iterrows():
            name = str(row.get('指标', '')).strip()
            val = self._safe_float(row.get(target_period))
            if val is not None:
                result[name] = val
        return result

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
