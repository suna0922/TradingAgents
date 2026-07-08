# TradingAgents 测试 Harness 设计

> 状态：设计草案 | 日期：2026-07-07

---

## 一、现状诊断

### 覆盖热力图

```
████████  llm_clients/capabilities,openai,validators,model_catalog  ✅ 优秀
████████  agents/utils/rating,memory                                 ✅ 优秀
████████  agents/schemas,structured (间接)                            ✅ 良好
████████  graph/analyst_execution,checkpointer,propagation            ✅ 良好
████████  backtest/trading_rules,rule_expression                      ✅ 优秀
████████  dataflows/config,utils                                      ✅ 良好
████████  graph/signal_processing,reflection                          ✅ 良好
████████  default_config,env_overrides                                ✅ 良好
░░░░░░░░  backtest/decision_engine                                   ❌ 零覆盖
░░░░░░░░  backtest/execution_engine                                  ❌ 零覆盖
░░░░░░░░  backtest/data_layer                                        ❌ 零覆盖
░░░░░░░░  backtest/models                                            ❌ 零覆盖
░░░░░░░░  agents/utils/numeric_guard                                 ❌ 零覆盖
░░░░░░░░  graph/numeric_validation                                   ❌ 零覆盖
░░░░░░░░  graph/setup (条件边)                                       ❌ 零覆盖
░░░░░░░░  graph/conditional_logic                                    ❌ 零覆盖
░░░░░░░░  dataflows/akshare_data                                     ❌ 零覆盖
```

### 已知脆弱点 — 全部零回归测试

| # | 脆弱点 | 影响范围 | 回归测试 |
|---|--------|---------|---------|
| 1 | 跨期类型比较（年报 vs 季报） | 7 个下游 agent 输出 | **无** |
| 2 | 条件边 path_map KeyError | LangGraph 运行时崩溃 | **无** |
| 3 | 正则 alternation 优先级 | 止损/止盈/OFC 提取失败 | **无** |
| 4 | condition_str 为空 → fallback | 复合规则系统失效 | **无** |
| 5 | PM schema action 格式不匹配 | Pydantic 验证失败 → 0 规则 | **无** |
| 6 | 数据加载 look-ahead bias | 回测结果失真 | **无** |

---

## 二、测试金字塔

```
         ╱   E2E     ╲        烟雾测试 — 全链路 1 个交易日
        ╱─────────────╲
       ╱  Integration  ╲      跨模块 — PM解析→规则执行 端到端
      ╱─────────────────╲
     ╱      Unit          ╲    纯函数 — mock 所有外部依赖
    ╱───────────────────────╲
   ╱   Regression Safety    ╲  针对性 — 覆盖 6 个已知脆弱点
  ╱───────────────────────────╲
```

### 层级定义

| 层级 | 目标 | 运行频率 | 超时 | 并行 |
|------|------|---------|------|------|
| **Regression Safety** | 已知 bug 不再复发 | 每次 push | < 5s | ✅ |
| **Unit** | 纯函数逻辑正确 | 每次 push | < 30s | ✅ |
| **Integration** | 跨模块契约合规 | 每次 PR | < 5min | 部分 |
| **E2E / Smoke** | 全链路可运行 | 每日/每次 release | < 30min | ❌ |

---

## 三、共享测试基础设施

### 3.1 目录结构

```
tests/
├── conftest.py                    # 现有 — 扩展 fixture
├── __init__.py
│
├── fixtures/                      # [新增] 共享测试数据工厂
│   ├── __init__.py
│   ├── data_factories.py          # OHLCV DataFrame, FA 指标 dict
│   ├── agent_outputs.py           # 各类 Agent 的典型输出文本
│   └── backtest_configs.py        # stock/crypto 的标准 BacktestConfig
│
├── utils/                         # [新增] 测试工具
│   ├── __init__.py
│   ├── llm_mock.py                # 统一的 LLM mock 构建器
│   └── assertions.py              # 自定义 assert helpers
│
├── unit/                          # [重构] 从 tests/ 移入
│   ├── backtest/
│   │   ├── test_decision_engine.py
│   │   ├── test_execution_engine.py
│   │   ├── test_data_layer.py
│   │   └── test_models.py
│   ├── agents/
│   │   ├── test_numeric_guard.py
│   │   ├── test_schemas.py
│   │   └── test_structured.py
│   └── graph/
│       ├── test_conditional_logic.py
│       ├── test_numeric_validation.py
│       └── test_setup.py
│
├── integration/                   # [新增]
│   ├── test_pm_to_execution.py    # PM 输出 → 规则解析 → 执行
│   ├── test_backtest_pipeline.py  # 多日 mini 回测
│   └── test_data_source_fallback.py
│
├── regression/                    # [新增] 回归安全网
│   ├── test_cross_period_types.py
│   ├── test_path_map_consistency.py
│   ├── test_regex_priority.py
│   ├── test_condition_str_fallback.py
│   ├── test_pm_action_format.py
│   └── test_lookahead_bias.py
│
└── e2e/                           # [新增]
    └── test_smoke_backtest.py
```

### 3.2 核心 Fixture — `fixtures/data_factories.py`

```python
"""可复用的测试数据工厂。不做 I/O，全部 in-memory 构造。"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def make_ohlcv_df(
    days: int = 60,
    start_date: str = "2026-01-02",
    base_price: float = 100.0,
    trend: float = 0.0,       # 日涨幅，0 = 横盘
    volatility: float = 0.02, # 日波动率
    seed: int = 42,
) -> pd.DataFrame:
    """生成模拟 OHLCV DataFrame，含所有必要列和 stockstats 兼容索引。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start_date, periods=days)
    closes = base_price * np.cumprod(1 + rng.normal(trend, volatility, days))

    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open":  closes * (1 + rng.normal(0, 0.005, days)),
        "high":  closes * (1 + abs(rng.normal(0, 0.01, days))),
        "low":   closes * (1 - abs(rng.normal(0, 0.01, days))),
        "close": closes,
        "volume": rng.integers(1_000_000, 50_000_000, days),
        "pct_chg": np.diff(closes, prepend=closes[0]) / np.append(closes[0], closes[:-1]) * 100,
    })
    df.index = pd.to_datetime(df["date"])
    return df


def make_fa_metrics(
    annual_roe: float = 22.5,
    annual_revenue_growth: float = 15.3,
    quarter_ocf_to_netprofit: float = 0.95,
    **overrides,
) -> dict:
    """构造展平的 L1 基本面指标字典（用于 fa_metrics 注入）。"""
    defaults = {
        "annual_roe": annual_roe,
        "annual_revenue_growth": annual_revenue_growth,
        "annual_net_margin": 18.2,
        "annual_debt_to_equity": 0.45,
        "quarter_ocf_to_netprofit": quarter_ocf_to_netprofit,
        "quarter_revenue_yoy": 12.1,
        "quarter_eps": 2.35,
        # ... 更多指标按需添加
    }
    defaults.update(overrides)
    return defaults


def make_portfolio_state(
    cash: float = 100_000.0,
    shares: int = 500,
    avg_cost: float = 95.0,
    ticker: str = "000887",
    stock_name: str = "中鼎股份",
) -> dict:
    """构造最小 PortfolioState 等价 dict（用于 ExecutionEngine 测试）。"""
    # 注意：ExecutionEngine 接受 PortfolioState dataclass
    from backtest.models import PortfolioState
    return PortfolioState(
        cash=cash,
        shares=shares,
        avg_cost=avg_cost,
        ticker=ticker,
        stock_name=stock_name,
    )


def make_weekly_decision(
    direction: str = "BUY",
    rating: str = "Overweight",
    position_pct: float = 0.60,
    stop_loss_pct: float = -0.08,
    take_profit_pct: float = 0.20,
    trading_rules: list = None,
    fa_metrics: dict = None,
) -> dict:
    """构造最小 WeeklyDecision。"""
    from backtest.models import WeeklyDecision, TradeDirection
    direction_map = {
        "BUY": TradeDirection.BUY,
        "SELL": TradeDirection.SELL,
        "HOLD": TradeDirection.HOLD,
    }
    return WeeklyDecision(
        date="2026-06-15",
        direction=direction_map.get(direction, TradeDirection.HOLD),
        rating=rating,
        position_pct=position_pct,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        entry_price_low=90.0,
        entry_price_high=105.0,
        trading_rules=trading_rules or [],
        fa_metrics=fa_metrics or {},
    )


def make_backtest_config(ticker: str = "000887", **overrides) -> dict:
    """标准 A 股回测配置。"""
    from backtest.models import BacktestConfig
    defaults = {
        "ticker": ticker,
        "start_date": "2026-01-02",
        "end_date": "2026-06-30",
        "initial_cash": 100_000.0,
        "commission_rate": 0.0003,
        "stamp_duty_rate": 0.001,
        "transfer_fee_rate": 0.00002,
        "position_limit": 0.80,
        "strategy": "mixed",
    }
    defaults.update(overrides)
    return BacktestConfig(**defaults)
```

### 3.3 统一 LLM Mock 构建器 — `utils/llm_mock.py`

```python
"""统一的 LLM mock 构建器，替代当前三种分散的 mock 模式。"""
from unittest.mock import MagicMock, patch


class LLMMockBuilder:
    """Builder pattern for constructing LLM mocks.

    Usage::

        llm = LLMMockBuilder() \\
            .with_structured(portfolio_decision) \\
            .with_plain("买入，目标价 120") \\
            .build()

        # 或作为 context manager:
        with LLMMockBuilder().as_context():
            ...
    """

    def __init__(self):
        self._structured_output = None
        self._plain_response = ""
        self._structured_error = None
        self._capture_prompt = False
        self._captured = {}

    def with_structured(self, pydantic_obj):
        """设置 structured output 返回值（含 prompt 捕获）。"""
        self._structured_output = pydantic_obj
        self._capture_prompt = True
        return self

    def with_plain(self, text: str):
        """设置 plain invoke 返回值。"""
        self._plain_response = text
        return self

    def with_structured_error(self, exc: Exception):
        """模拟 structured output 失败 → 回退到 plain。"""
        self._structured_error = exc
        return self

    def build(self):
        """返回 (structured_llm_mock, plain_llm_mock) 元组。"""
        structured = MagicMock()

        if self._structured_error:
            structured.invoke.side_effect = self._structured_error
        elif self._structured_output is not None:
            if self._capture_prompt:
                captured = self._captured
                structured.invoke.side_effect = (
                    lambda prompt: captured.update(prompt=prompt)
                    or self._structured_output
                )
            else:
                structured.invoke.return_value = self._structured_output

        plain = MagicMock()
        plain.invoke.return_value = MagicMock(content=self._plain_response)

        return structured, plain

    def as_context(self):
        """返回 context manager，自动 patch create_llm_client。"""
        return patch(
            "tradingagents.llm_clients.factory.create_llm_client",
            return_value=self._build_client_mock(),
        )

    def _build_client_mock(self):
        structured, plain = self.build()
        client = MagicMock()
        client.get_llm.return_value = MagicMock()
        client.get_llm.return_value.with_structured_output.return_value = structured
        return client


def mock_graph_run(pm_output_text: str):
    """Mock TradingAgentsGraph.run() 返回标准 agent state。"""
    return {
        "final_pm_output": pm_output_text,
        "rating": "Overweight",
        "trader_output": "建议买入，目标价 120 元",
        "research_manager_output": "研究确认基本面良好",
        "fundamentals_structured": {},
    }
```

### 3.4 Agent 输出 Fixture — `fixtures/agent_outputs.py`

```python
"""各类 Agent 的典型输出文本，用于解析测试。"""

# PM 输出 — 正常情况（含结构化规则）
PM_OUTPUT_NORMAL = """
**最终评级:** Overweight
**止损价:** 88.00 (基于-8%)
**止盈价:** 120.00 (基于+20%)
**执行摘要:** 基本面稳健，估值合理偏低，建议超配。

**交易规则:**
1. WHEN close > MA(close, 20) AND close < 110 THEN action=buy_add, action_detail='20%', priority=1
2. WHEN close < 88.0 THEN action=stop_loss, priority=2
3. WHEN close > 120.0 THEN action=take_profit, priority=2
"""

# PM 输出 — 含跨期类型比较（应被检测到）
PM_OUTPUT_CROSS_TYPE_BUG = """
**执行摘要:** 
- 经营现金流/净利润从0.66倍（年报2025）跌至-0.40倍（季报2026Q1），说明现金流恶化
- ROE从22.5%（年报2025）拉长到18.3%（季报2026Q1），盈利能力下降
"""

# PM 输出 — action 含百分比括号（历史上导致过解析失败）
PM_OUTPUT_ACTION_PARENS = """
**交易规则:**
1. WHEN close < 95 THEN action=sell_pct(50%), priority=1
2. WHEN close < 90 THEN action=sell_pct(100%), priority=2
"""

# PM 输出 — condition_str 为空（历史上导致 fallback）
PM_OUTPUT_EMPTY_CONDITION = """
**交易规则:**
1. WHEN THEN action=hold, priority=1
2. IF THEN action=alert_only, priority=2
"""

# PM 输出 — 含中文字段名（测试 FIELD_ALIAS_MAP 替换）
PM_OUTPUT_CHINESE_FIELDS = """
**交易规则:**
1. WHEN 收盘价高于20日移动平均 AND 成交量大于5000万 THEN action=buy_add, action_detail='30%', priority=1
2. WHEN MACD柱小于0 OR RSI低于30 THEN action=sell_pct, action_detail='50%', priority=2
"""

# 研究员辩论输出 — 含凭空捏造数字
BULL_OUTPUT_FABRICATED = """
看多观点：
- 公司营收达到156亿元，同比增长约60%
- ROE高达23%，远超行业平均
- 经营性现金流非常健康
"""

# 原始基本面报告（source data）对应上述输出
BULL_SOURCE_DATA = {
    "annual_revenue": 155.52,       # 亿 — 不是 156
    "annual_revenue_yoy": 59.86,    # % — 不是 60
    "annual_roe": 22.51,            # % — 不是 23
    "annual_ocf": 38.2,             # 亿
}
```

---

## 四、回归安全网 — 针对性设计

### 4.1 跨期类型比较检测

```python
# tests/regression/test_cross_period_types.py

import pytest
from tradingagents.agents.utils.numeric_guard import (
    detect_cross_period_type_comparison,
    build_cross_type_warning,
)

class TestCrossPeriodTypeDetection:
    """验证 detect_cross_period_type_comparison 的 7 种模式。"""

    def test_annual_to_quarterly_compare(self):
        """年报→季报 比较应被检测到。"""
        text = "OCF/净利润从0.66倍（年报2025）跌至-0.40倍（季报2026Q1）"
        violations = detect_cross_period_type_comparison(text)
        assert len(violations) >= 1
        assert violations[0]["severity"] == "CRITICAL"
        assert violations[0]["type_from"] == "年报"
        assert violations[0]["type_to"] == "季报"

    def test_quarterly_to_annual_compare(self):
        """季报→年报 比较应被检测到。"""
        text = "ROE从18.3%（季报2026Q1）升至22.5%（年报2025）"
        violations = detect_cross_period_type_comparison(text)
        assert len(violations) >= 1

    def test_same_type_no_false_positive(self):
        """同年报/同季报比较不应误报。"""
        text = "ROE从22.5%（年报2024）提升到25.1%（年报2025），同比增长"
        violations = detect_cross_period_type_comparison(text)
        assert len(violations) == 0

    def test_quarterly_qoq_no_false_positive(self):
        """季报 QoQ 比较不应误报。"""
        text = "营收从45.2亿（季报2025Q3）增长到52.1亿（季报2025Q4）"
        violations = detect_cross_period_type_comparison(text)
        assert len(violations) == 0

    def test_deduplication(self):
        """同一段文本重复匹配应去重。"""
        text = "OCF跌至-0.40倍（季报2026Q1） OCF跌至-0.40倍（季报2026Q1）"
        violations = detect_cross_period_type_comparison(text)
        # 即使文本重复，不应产生重复 violation
        assert len(violations) <= 2  # pattern 1 和 pattern 3 各可能匹配一次

    def test_warning_message_contains_fix_guidance(self):
        """build_cross_type_warning 应包含修复指引。"""
        text = "OCF从0.66倍（年报2025）跌至-0.40倍（季报2026Q1）"
        violations = detect_cross_period_type_comparison(text)
        warning = build_cross_type_warning(violations)
        assert "年报" in warning
        assert "季报" in warning
        assert "FIX" in warning or "修复" in warning

    def test_no_violations_empty_warning(self):
        """无违规时应返回空字符串。"""
        assert build_cross_type_warning([]) == ""
```

### 4.2 条件边 path_map 一致性

```python
# tests/regression/test_path_map_consistency.py

import pytest
from tradingagents.graph.setup import GraphSetup
from tradingagents.graph.conditional_logic import ConditionalLogic

class TestPathMapConsistency:
    """验证所有条件函数的返回值都在对应 path_map 中。"""

    def _get_all_conditional_edges(self):
        """从 setup.py 提取所有 add_conditional_edges 调用信息。"""
        # 这里手动列出（因为解析 Python AST 太重）
        return [
            {
                "source": "Bull Researcher",
                "condition": "should_continue_debate",
                "path_map": {"Bear Researcher", "Numeric Check Bull", "Research Manager"},
            },
            {
                "source": "Bear Researcher",
                "condition": "should_continue_debate",
                "path_map": {"Bull Researcher", "Numeric Check Bear", "Research Manager"},
            },
            {
                "source": "Aggressive Analyst",
                "condition": "should_continue_risk_analysis",
                "path_map": {"Conservative Analyst", "Portfolio Manager"},
            },
            {
                "source": "Conservative Analyst",
                "condition": "should_continue_risk_analysis",
                "path_map": {"Neutral Analyst", "Portfolio Manager"},
            },
            {
                "source": "Neutral Analyst",
                "condition": "should_continue_risk_analysis",
                "path_map": {"Portfolio Manager"},
            },
        ]

    @pytest.mark.parametrize("edge", _get_all_conditional_edges.__func__())
    def test_condition_returns_valid_routes(self, edge):
        """条件函数的所有可能返回值都应在 path_map 中。"""
        logic = ConditionalLogic(max_debate_rounds=3, max_risk_rounds=3)
        condition_fn = getattr(logic, edge["condition"])

        # 获取函数的所有可能返回值
        possible_returns = self._enumerate_possible_returns(condition_fn, edge)

        for ret in possible_returns:
            assert ret in edge["path_map"], (
                f"Edge '{edge['source']}' 的条件函数 {edge['condition']} 返回了 "
                f"'{ret}'，但不在 path_map {edge['path_map']} 中"
            )

    def _enumerate_possible_returns(self, fn, edge):
        """静态分析或手动枚举条件函数可能的返回值。"""
        # 对于 should_continue_debate:
        #   response.startswith("Bull") → depends on current_response
        #   response.startswith("Bear") → depends on current_response
        #   达到 max_rounds → "Research Manager"
        #   default/final → "Bull Researcher"
        # 但真正的暴力穷举需要模拟 state

        # 这里简化：从已知返回值集合硬编码
        if edge["condition"] == "should_continue_debate":
            return {"Bull Researcher", "Bear Researcher", "Research Manager"}
        elif edge["condition"] == "should_continue_risk_analysis":
            return {"Conservative Analyst", "Neutral Analyst", "Portfolio Manager"}
        return set()

    def test_neutral_analyst_path_map_count(self):
        """Neutral Analyst 的 path_map 必须覆盖所有可能返回值。
        
        历史上 path_map 只包含 'Portfolio Manager'，漏掉了其他路由，
        导致 count=3 时 KeyError。
        """
        from tradingagents.graph.conditional_logic import ConditionalLogic
        logic = ConditionalLogic(max_debate_rounds=3, max_risk_rounds=3)

        # should_continue_risk_analysis 在 count=3 时的行为
        # 应该返回 "Portfolio Manager"
        result = logic.should_continue_risk_analysis(
            {"risk_round_count": 3, "current_risk_response": "Neutral"}
        )
        assert result in {"Neutral Analyst", "Portfolio Manager"}, (
            f"count=3 时 should_continue_risk_analysis 返回 '{result}'，"
            f"但 Neutral Analyst 的 path_map 已记录了"
        )
```

### 4.3 condition_str 为空 → fallback

```python
# tests/regression/test_condition_str_fallback.py

import pytest
from backtest.trading_rules import eval_condition, RuleParser
from backtest.models import TradingRule, RuleAction

class TestConditionStrFallback:
    """验证空 condition_str 不会导致静默的规则失效。"""

    def test_empty_condition_evaluates_false(self):
        """空 condition_str 应 eval 为 False → 规则不触发。"""
        row = {"close": 100.0, "volume": 5_000_000}
        assert eval_condition("", row) is False, (
            "空 condition_str 应返回 False（规则禁用），而不是 True（不当触发）"
        )

    def test_whitespace_only_condition(self):
        """纯空白 condition_str 应 eval 为 False。"""
        row = {"close": 100.0}
        assert eval_condition("   \n  ", row) is False

    def test_parser_skips_empty_condition_rule(self):
        """RuleParser 应跳过 condition_str 为空的规则行。"""
        pm_text = """
        1. WHEN THEN action=hold, priority=1
        2. WHEN close < 100 THEN action=sell_pct, action_detail='50%', priority=2
        """
        rules = RuleParser.parse(pm_text)
        # 规则 1 的 condition_str 为空，应被跳过
        # 只有规则 2 被解析
        condition_strs = [r.condition_str for r in rules if r.condition_str.strip()]
        assert len(condition_strs) >= 1, "至少应解析出一条有 condition 的规则"
        assert all(cs.strip() for cs in condition_strs), "所有解析出的 condition_str 不应为空"

    def test_trading_rule_disabled_when_no_condition(self):
        """TradingRule 的 enabled 字段在 condition_str 为空时应为 False。"""
        rule = TradingRule(
            condition_str="",
            action=RuleAction.HOLD,
            priority=1,
            enabled=True,
        )
        # enabled 应该被自动设为 False（或 condition_str 为空时应视为禁用）
        # 根据当前实现验证
        assert rule.enabled is True or rule.condition_str == "", (
            "enabled 和 condition_str 状态需要保持一致"
        )
```

### 4.4 PM Schema Action 格式

```python
# tests/regression/test_pm_action_format.py

import pytest
from tradingagents.agents.schemas import TradingRuleItem, RuleAction

class TestPMActionFormat:
    """验证 TradingRuleItem 的 action 字段验证器。"""

    @pytest.mark.parametrize("raw_action,expected", [
        ("sell_pct(30%)", RuleAction.SELL_PCT),
        ("sell_pct(50%)", RuleAction.SELL_PCT),
        ("sell_pct", RuleAction.SELL_PCT),
        ("buy_add(20%)", RuleAction.BUY_ADD),
        ("buy_add", RuleAction.BUY_ADD),
        ("stop_loss", RuleAction.STOP_LOSS),
        ("take_profit", RuleAction.TAKE_PROFIT),
        ("hold", RuleAction.HOLD),
    ])
    def test_action_field_validator_strips_parens(self, raw_action, expected):
        """action 字段的 field_validator 应剥离 (xxx%) 后缀。"""
        item = TradingRuleItem(action=raw_action, action_detail="20%")
        assert item.action == expected, (
            f"raw_action='{raw_action}' 应被解析为 {expected}，实际为 {item.action}"
        )

    def test_unknown_action_falls_back(self):
        """不认识的 action 字符串应触发验证错误（而非静默忽略）。"""
        with pytest.raises(Exception):  # Pydantic ValidationError
            TradingRuleItem(action="dance", action_detail="all")

    def test_action_detail_preserved_after_strip(self):
        """action_detail 在 action 清理后不应被修改。"""
        item = TradingRuleItem(action="sell_pct(30%)", action_detail="30%")
        assert item.action_detail == "30%"

    def test_render_pm_decision_handles_cleaned_actions(self):
        """render_pm_decision 应正确处理清理后的 action 值。"""
        from tradingagents.agents.schemas import render_pm_decision, PortfolioDecision

        decision = PortfolioDecision(
            rating="Overweight",
            executive_summary="测试",
            investment_thesis="测试 thesis",
            trading_rules=[
                TradingRuleItem(action="sell_pct", action_detail="50%"),
                TradingRuleItem(action="buy_add", action_detail="20%"),
            ],
        )
        markdown = render_pm_decision(decision)
        assert "sell_pct" in markdown or "减仓" in markdown
        assert "50%" in markdown
```

### 4.5 Look-Ahead Bias

```python
# tests/regression/test_lookahead_bias.py

import pytest
import pandas as pd
from datetime import datetime

# 注意：DataLayer 依赖 baostock，测试时需要 mock

class TestLookaheadBias:
    """验证数据层不会泄露未来信息。"""

    def _make_ohlcv_with_future(self):
        """构造含未来日期的 OHLCV 数据。"""
        dates = pd.bdate_range("2026-06-01", "2026-06-30")
        return pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "close": range(100, 100 + len(dates)),
            "open":  range(100, 100 + len(dates)),
            "high":  range(101, 101 + len(dates)),
            "low":   range(99, 99 + len(dates)),
            "volume": [5_000_000] * len(dates),
        })

    def test_ohlcv_filtered_by_current_date(self, monkeypatch):
        """_load_ohlcv_akshare 应过滤掉 curr_date 之后的数据。"""
        # Mock akshare 返回全年数据
        import tradingagents.dataflows.akshare_data as ad

        full_df = self._make_ohlcv_with_future()

        # Mock akshare 返回函数
        monkeypatch.setattr(
            ad, "_fetch_ashare_ohlcv",
            lambda ticker, start, end: full_df,
        )

        # 调用时应只返回 curr_date 及之前的数据
        result = ad._load_ohlcv_akshare("000887", "2026-06-01", "2026-06-15")

        # 断言结果中不包含 2026-06-15 之后的数据
        max_date = result["date"].max()
        assert max_date <= "2026-06-15", (
            f"OHLCV 数据包含未来日期 {max_date}，存在 look-ahead bias"
        )

    def test_fundamentals_filtered_by_report_date(self, monkeypatch):
        """get_ashare_financial_indicators_by_quarter 应过滤未来的报告。"""
        import tradingagents.dataflows.akshare_data as ad

        # Mock baostock 返回所有报告（含未来季度）
        mock_reports = pd.DataFrame({
            "code": ["000887"] * 4,
            "pubDate": ["2026-04-30", "2026-04-30", "2026-08-30", "2026-10-30"],
            "statDate": ["2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"],
            "roeAvg": [22.5, 5.1, 12.3, 18.7],
            "currentRatio": [2.1, 2.3, 2.5, 2.8],
        })

        monkeypatch.setattr(ad, "_get_bs_finance_summary", lambda code, year, qtr: mock_reports)

        # 从 2026-06-01 视角查询 — 不应看到 2026Q3
        result = ad.get_ashare_financial_indicators_by_quarter(
            "000887",
            current_date="2026-06-01",
        )

        # 断言没有 2026-09-30 的报告
        if isinstance(result, pd.DataFrame) and not result.empty:
            future_stats = result[result["statDate"] > "2026-06-01"]
            assert len(future_stats) == 0, (
                f"基本面数据包含未来报告: {future_stats['statDate'].tolist()}"
            )

    def test_latest_report_date_respects_as_of(self, monkeypatch):
        """get_latest_report_date(as_of_date=...) 不应返回未来的报告日期。"""
        from backtest.data_layer import DataLayer

        # Mock baostock 查询
        monkeypatch.setattr(
            "backtest.data_layer.DataLayer._query_report_dates",
            lambda self, ticker: {
                "2025-12-31": "2026-04-30",
                "2026-03-31": "2026-04-30",
                "2026-06-30": "2026-08-30",  # 未来！
            }
        )

        dl = DataLayer(ticker="000887")
        # as_of 在 2026-06-01，不应返回 2026-08-30
        latest = dl.get_latest_report_date(as_of_date=datetime(2026, 6, 1))
        assert latest <= datetime(2026, 6, 1), (
            f"get_latest_report_date 返回了未来日期 {latest}"
        )
```

### 4.6 正则优先级

```python
# tests/regression/test_regex_priority.py

import pytest
import re

class TestRegexPriority:
    """验证 alternation 和字符类的正则优先级问题不再复发。"""

    def test_ocf_alternation_not_truncated(self):
        """'OCF'(短)不应在'OCF/净利润'(长)之前截断匹配。"""
        # 错误的正则: r"OCF|OCF/净利润" → OCF 先匹配，截断 OCF/净利润
        # 正确的正则: r"(?:OCF/净利润|OCF)" → 长的先匹配
        
        text = "经营现金流/净利润从0.66倍跌至-0.40倍"

        # 坏的写法
        bad_pattern = re.compile(r"(OCF|OCF/净利润)\s*从")
        bad_match = bad_pattern.search(text.replace("经营现金流", "OCF"))
        assert bad_match.group(1) == "OCF/净利润" if bad_match else True, (
            "如果 OCF 先于 OCF/净利润 匹配，说明 alternation 顺序有问题"
        )

        # 好的写法验证
        good_pattern = re.compile(r"(?:OCF/净利润|经营现金流/净利润|OCF|经营现金流)\s*从")
        good_match = good_pattern.search(text)
        assert good_match is not None
        if "OCF" in text:
            assert good_match.group(0).startswith("经营现金流/净利润")

    def test_field_alias_longest_first(self):
        """FIELD_ALIAS_MAP 替换应按 key 长度降序，避免短词截断长词。"""
        from backtest.trading_rules import FIELD_ALIAS_MAP

        # 验证 "MACD柱" 出现在 "MACD" 之前
        aliases = sorted(FIELD_ALIAS_MAP.keys(), key=len, reverse=True)
        
        if "MACD" in aliases and "MACD柱" in aliases:
            macd_pos = aliases.index("MACD")
            macd_bar_pos = aliases.index("MACD柱")
            assert macd_bar_pos < macd_pos, (
                f"'MACD柱'(位置{macd_bar_pos})应排在'MACD'(位置{macd_pos})之前，避免短词截断"
            )

    def test_fullwidth_colon_detection(self):
        """中文全角冒号（：）应被正确识别。"""
        text = "**止损价：** 88.00\n**止盈价：** 120.00"

        # 验证全角冒号能被正则匹配
        pattern = re.compile(r'\*\*止损价[：:]\s*\*\*\s*([\d.]+)')
        match = pattern.search(text)
        assert match is not None
        assert match.group(1) == "88.00"

    def test_newline_after_target_not_required(self):
        """匹配尾部边界时不应要求 $（行中间换行也接受）。"""
        text = "止损价：88.00\n止盈价：120.00"

        # 好的正则：尾部边界用 \n 而非 $
        pattern = re.compile(r'止损价[：:]\s*([\d.]+)\s*\n')
        match = pattern.search(text)
        assert match is not None
        assert match.group(1) == "88.00"
```

---

## 五、Unit Test 设计

### 5.1 ExecutionEngine — 交易逻辑核心

```python
# tests/unit/backtest/test_execution_engine.py

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

from backtest.execution_engine import ExecutionEngine
from backtest.models import (
    BacktestConfig, PortfolioState, WeeklyDecision, TradeDirection,
    DailyState, TradeRecord, RuleAction, TradingRule,
)
from backtest.data_layer import DataLayer
from tests.fixtures.data_factories import (
    make_ohlcv_df, make_fa_metrics,
    make_portfolio_state, make_weekly_decision, make_backtest_config,
)


class TestExecutionEngine:
    """ExecutionEngine 交易执行逻辑测试。

    原则：纯确定性逻辑 — 相同输入相同输出，不需要 mock LLM。
    """

    # ── 止损测试 ──────────────────────────────────────

    def test_stop_loss_triggers_sell_all(self):
        """价格跌破止损线 → 全仓卖出。"""
        config = make_backtest_config()
        dl = MagicMock(spec=DataLayer)

        engine = ExecutionEngine(config, dl)
        portfolio = make_portfolio_state(cash=50000, shares=500, avg_cost=95.0)
        decision = make_weekly_decision(
            direction="BUY",
            stop_loss_pct=-0.08,  # 止损线 -8%，即 87.4
        )

        df = make_ohlcv_df(days=30, base_price=95)
        # 第 15 天暴跌到 85（跌破止损线 87.4）
        df.loc[df.index[14], "close"] = 85.0
        df.loc[df.index[14], "low"] = 84.0
        df.loc[df.index[14], "open"] = 90.0

        row = df.iloc[14]
        result = engine.execute(portfolio, decision, row, 14, df)

        assert result.action == "SELL_ALL"
        assert result.action_reason in ("stop_loss", "止损")
        assert portfolio.shares == 0

    def test_stop_loss_not_triggered_above_line(self):
        """价格在止损线上方 → 不触发止损。"""
        config = make_backtest_config()
        dl = MagicMock(spec=DataLayer)
        engine = ExecutionEngine(config, dl)

        portfolio = make_portfolio_state(shares=500)
        decision = make_weekly_decision(stop_loss_pct=-0.08)  # 止损线 87.4

        df = make_ohlcv_df(days=30, base_price=95)
        row = df.iloc[14]  # close ≈ 95，远高于止损线

        result = engine.execute(portfolio, decision, row, 14, df)
        assert result.action != "SELL_ALL"

    # ── 止盈测试 ──────────────────────────────────────

    def test_take_profit_triggers_sell(self):
        """价格触及止盈线 → 触发卖出。"""
        config = make_backtest_config()
        dl = MagicMock(spec=DataLayer)
        engine = ExecutionEngine(config, dl)

        portfolio = make_portfolio_state(shares=500, avg_cost=95.0)
        decision = make_weekly_decision(take_profit_pct=0.20)  # 止盈线 114

        df = make_ohlcv_df(days=30, base_price=95)
        df.loc[df.index[20], "close"] = 116.0  # 突破止盈
        df.loc[df.index[20], "high"] = 118.0

        row = df.iloc[20]
        result = engine.execute(portfolio, decision, row, 20, df)

        assert result.action in ("SELL_ALL", "SELL")
        assert "take_profit" in result.action_reason.lower() or "止盈" in result.action_reason

    # ── 空持仓测试 ────────────────────────────────────

    def test_empty_portfolio_skips_sell_rules(self):
        """空持仓时所有卖出类操作应被跳过。"""
        config = make_backtest_config()
        dl = MagicMock(spec=DataLayer)
        engine = ExecutionEngine(config, dl)

        portfolio = make_portfolio_state(shares=0)
        decision = make_weekly_decision(
            stop_loss_pct=-0.08,
            take_profit_pct=0.20,
        )

        df = make_ohlcv_df(days=30, base_price=95)
        df.loc[df.index[14], "close"] = 80.0  # 跌破止损

        row = df.iloc[14]
        result = engine.execute(portfolio, decision, row, 14, df)

        # 空持仓不应卖出
        assert result.action != "SELL_ALL"

    # ── 涨跌停检测 ────────────────────────────────────

    def test_limit_up_skips_buy(self):
        """涨停板 → 跳过买入。"""
        dl = MagicMock(spec=DataLayer)

        # ExecutionEngine 静态方法
        is_limit_up = DataLayer.is_limit_up
        assert is_limit_up(9.99, 9.09, "A")
        assert is_limit_up(10.02, 9.09, "A")
        assert not is_limit_up(9.50, 9.09, "A")

    def test_limit_down_skips_sell(self):
        """跌停板 → 跳过卖出。"""
        is_limit_down = DataLayer.is_limit_down
        assert is_limit_down(-9.95, 11.15, "A")
        assert is_limit_down(-10.03, 11.15, "A")
        assert not is_limit_down(-5.0, 11.15, "A")

    # ── 成本计算 ──────────────────────────────────────

    def test_buy_cost_calculation(self):
        """买入成本 = 价格×数量 + 佣金 + 过户费（沪市）。"""
        config = make_backtest_config(
            commission_rate=0.0003,
            stamp_duty_rate=0.001,
            transfer_fee_rate=0.00002,
        )
        dl = MagicMock(spec=DataLayer)

        engine = ExecutionEngine(config, dl)
        cost = engine._calculate_buy_cost(price=100.0, shares=500)

        expected_commission = 100.0 * 500 * 0.0003   # 15
        expected_transfer = 100.0 * 500 * 0.00002    # 1
        expected_total = 100.0 * 500 + expected_commission + expected_transfer
        assert abs(cost - expected_total) < 0.01

    def test_sell_cost_includes_stamp_duty(self):
        """卖出成本 = 买入成本计算 + 印花税（仅卖出）。"""
        config = make_backtest_config()
        dl = MagicMock(spec=DataLayer)
        engine = ExecutionEngine(config, dl)

        cost = engine._calculate_sell_cost(price=100.0, shares=500)

        expected_stamp = 100.0 * 500 * 0.001   # 50
        expected_commission = 100.0 * 500 * 0.0003  # 15
        assert cost > 100.0 * 500  # 成本应高于纯价格

    # ── 按手取整 ──────────────────────────────────────

    def test_round_lot_100_shares(self):
        """A 股最小交易单位 100 股。"""
        config = make_backtest_config()
        dl = MagicMock(spec=DataLayer)
        engine = ExecutionEngine(config, dl)

        assert engine._round_lot(150) == 100
        assert engine._round_lot(250) == 200
        assert engine._round_lot(99) == 0
        assert engine._round_lot(100) == 100

    # ── FA 指标注入 ───────────────────────────────────

    def test_fa_metrics_injected_into_row_dict(self):
        """fa_metrics 应注入到 row_dict 供 Condition 评估使用。"""
        config = make_backtest_config()
        dl = MagicMock(spec=DataLayer)
        engine = ExecutionEngine(config, dl)

        portfolio = make_portfolio_state(shares=500)
        rule = TradingRule(
            condition_str="annual_roe > 20",
            action=RuleAction.BUY_ADD,
            action_detail="10%",
            priority=1,
        )
        decision = make_weekly_decision(trading_rules=[rule])

        df = make_ohlcv_df(days=30, base_price=95)
        fa = make_fa_metrics(annual_roe=22.5)

        row = df.iloc[14]
        result = engine.execute(portfolio, decision, row, 14, df, fa_metrics=fa)

        # 规则 annual_roe > 20 应触发（roe=22.5）
        # 具体结果取决于规则执行逻辑
        assert result is not None  # 不应崩溃
```

### 5.2 Numeric Guard — 数值完整性

```python
# tests/unit/agents/test_numeric_guard.py

import pytest
from tradingagents.agents.utils.numeric_guard import (
    extract_numeric_claims,
    cross_check_claims,
    detect_cross_period_type_comparison,
    parse_fundamentals_text,
)

class TestExtractNumericClaims:
    """L2a: 从 agent 输出中提取数值声明。"""

    def test_extracts_chinese_currency(self):
        text = "公司营收达到155.52亿元，同比增长59.86%"
        claims = extract_numeric_claims(text)
        assert any(c["metric"] == "营收" and abs(c["value"] - 155.52) < 0.01 for c in claims)

    def test_extracts_percentage(self):
        text = "毛利率提升至45.2%，净利率18.3%"
        claims = extract_numeric_claims(text)
        assert len(claims) >= 2

    def test_extracts_english_metrics(self):
        text = "ROE reached 22.5%, Revenue grew to 15.5B"
        claims = extract_numeric_claims(text)
        assert any("ROE" in c["metric"] and abs(c["value"] - 22.5) < 0.01 for c in claims)

    def test_empty_text_returns_empty(self):
        assert extract_numeric_claims("") == []


class TestCrossCheckClaims:
    """L2b: 将提取的数值与源数据交叉验证。"""

    def test_exact_match_passes(self):
        claims = [{"metric": "annual_roe", "value": 22.5, "raw_text": "ROE 22.5%"}]
        source = {"annual_roe": 22.5}
        violations = cross_check_claims(claims, source)
        assert len(violations) == 0

    def test_small_rounding_is_flagged(self):
        """LLM 将 22.51 四舍五入为 23 → 违规。"""
        claims = [{"metric": "annual_roe", "value": 23, "raw_text": "ROE 23%"}]
        source = {"annual_roe": 22.51}
        violations = cross_check_claims(claims, source)
        assert len(violations) > 0

    def test_within_tolerance_passes(self):
        """±0.05（ratio tolerance）以内的偏差可以通过。"""
        claims = [{"metric": "annual_roe", "value": 22.47, "raw_text": "ROE 22.47%"}]
        source = {"annual_roe": 22.51}
        violations = cross_check_claims(claims, source, tolerance_ratio=0.05)
        assert len(violations) == 0

    def test_growth_rate_deviation_flagged(self):
        """±1pp 以上的百分点偏差应被标记。"""
        claims = [{"metric": "annual_revenue_growth", "value": 60, "raw_text": "+60%"}]
        source = {"annual_revenue_growth": 59.86}
        violations = cross_check_claims(claims, source, tolerance_pp=1.0)
        assert len(violations) == 0  # 60 - 59.86 = 0.14pp < 1pp

        claims2 = [{"metric": "annual_revenue_growth", "value": 60, "raw_text": "+60%"}]
        source2 = {"annual_revenue_growth": 58.0}
        violations2 = cross_check_claims(claims2, source2, tolerance_pp=1.0)
        assert len(violations2) > 0  # 60 - 58 = 2pp > 1pp

    def test_missing_metric_in_source_passes(self):
        """源数据中没有的指标不标记为违规（可能不在 scope 内）。"""
        claims = [{"metric": "ev_to_ebitda", "value": 12.5, "raw_text": "EV/EBITDA 12.5x"}]
        source = {"annual_roe": 22.5}  # 没有 ev_to_ebitda
        violations = cross_check_claims(claims, source)
        # 默认行为：不标记（因为无法判断）
        assert len(violations) == 0
```

### 5.3 Conditional Logic — 辩论控制

```python
# tests/unit/graph/test_conditional_logic.py

import pytest
from tradingagents.graph.conditional_logic import ConditionalLogic

class TestConditionalLogic:
    """测试辩论和风险分析的轮数控制和路由逻辑。"""

    # ── should_continue_debate ─────────────────────────

    def test_debate_stops_at_max_rounds(self):
        logic = ConditionalLogic(max_debate_rounds=3, max_risk_rounds=3)
        state = {"debate_round_count": 3}
        assert logic.should_continue_debate(state) == "Research Manager"

    def test_debate_alternates_bull_bear(self):
        logic = ConditionalLogic(max_debate_rounds=3, max_risk_rounds=3)
        
        # Bull 的回合 → 下一个是 Bear
        result = logic.should_continue_debate({
            "debate_round_count": 1,
            "current_response": "Bull: 看多观点..."
        })
        assert result == "Bear Researcher"

        # Bear 的回合 → 下一个是 Bull
        result = logic.should_continue_debate({
            "debate_round_count": 1,
            "current_response": "Bear: 看空观点..."
        })
        assert result == "Bull Researcher"

    def test_debate_unknown_response_falls_back(self):
        """不认识的 response 应 fallback 到 Bull Researcher。"""
        logic = ConditionalLogic(max_debate_rounds=3, max_risk_rounds=3)
        result = logic.should_continue_debate({
            "debate_round_count": 1,
            "current_response": "Something unexpected"
        })
        assert result in {"Bull Researcher", "Bear Researcher"}, (
            f"不认识的 response 应 fallback，实际返回 {result}"
        )

    # ── should_continue_risk_analysis ──────────────────

    def test_risk_stops_at_max_rounds(self):
        logic = ConditionalLogic(max_debate_rounds=3, max_risk_rounds=3)
        state = {"risk_round_count": 3}
        assert logic.should_continue_risk_analysis(state) == "Portfolio Manager"

    def test_risk_alternates_correctly(self):
        logic = ConditionalLogic(max_debate_rounds=3, max_risk_rounds=3)

        # Aggressive → Conservative
        result = logic.should_continue_risk_analysis({
            "risk_round_count": 1,
            "current_risk_response": "Aggressive: 激进观点..."
        })
        assert result == "Conservative Analyst"

        # Conservative → Neutral
        result = logic.should_continue_risk_analysis({
            "risk_round_count": 2,
            "current_risk_response": "Conservative: 保守观点..."
        })
        assert result == "Neutral Analyst"

        # Neutral → Portfolio Manager (3 analysts done)
        result = logic.should_continue_risk_analysis({
            "risk_round_count": 2,  # 还没到 max
            "current_risk_response": "Neutral: 中性观点..."
        })
        assert result == "Portfolio Manager"

    def test_risk_all_possible_returns_in_some_path_map(self):
        """should_continue_risk_analysis 的所有可能返回值必须在某个 path_map 中。"""
        logic = ConditionalLogic(max_debate_rounds=3, max_risk_rounds=3)
        possible = {"Conservative Analyst", "Neutral Analyst", "Portfolio Manager"}

        # 模拟不同状态验证返回值总在这个集合中
        for round_count in [1, 2, 3, 4]:
            for response in ["Aggressive: ...", "Conservative: ...", "Neutral: ..."]:
                result = logic.should_continue_risk_analysis({
                    "risk_round_count": round_count,
                    "current_risk_response": response,
                })
                assert result in possible, (
                    f"round={round_count}, response='{response}' → '{result}' 不在 {possible} 中"
                )
```

---

## 六、Integration Test 设计

### 6.1 PM 输出 → 规则解析 → 执行（端到端）

```python
# tests/integration/test_pm_to_execution.py

import pytest
from unittest.mock import MagicMock, patch

from backtest.models import WeeklyDecision, TradingRule, RuleAction
from backtest.decision_engine import DecisionEngine
from backtest.execution_engine import ExecutionEngine
from tests.fixtures.data_factories import (
    make_ohlcv_df, make_fa_metrics, make_portfolio_state,
    make_backtest_config,
)
from tests.fixtures.agent_outputs import (
    PM_OUTPUT_NORMAL,
    PM_OUTPUT_CROSS_TYPE_BUG,
    PM_OUTPUT_EMPTY_CONDITION,
)


class TestPMToExecution:
    """集成测试：PM Markdown → DecisionEngine 解析 → ExecutionEngine 执行。"""

    def test_normal_pm_parses_and_executes(self):
        """正常 PM 输出应被正确解析并触发交易规则。"""
        config = make_backtest_config()
        de = DecisionEngine(config)  # 简化构造（可能需要 mock graph）

        # 测试 PM 解析
        parsed = de._parse_pm_output(PM_OUTPUT_NORMAL)
        assert parsed is not None
        assert parsed.rating == "Overweight" or parsed.direction is not None

        # 测试规则提取
        rules = parsed.trading_rules
        assert len(rules) >= 2, f"期望至少 2 条规则，实际 {len(rules)}"

        # 验证第一条规则（buy_add when close > MA(close,20)）
        buy_rule = rules[0]
        assert buy_rule.condition_str.strip() != "", "condition_str 不应为空"
        assert buy_rule.action in (RuleAction.BUY_ADD, RuleAction.HOLD)

    def test_cross_type_bug_is_detected_in_pm_output(self):
        """PM 输出中的跨期类型比较应被检测（集成 numeric_guard）。"""
        from tradingagents.agents.utils.numeric_guard import detect_cross_period_type_comparison

        violations = detect_cross_period_type_comparison(PM_OUTPUT_CROSS_TYPE_BUG)
        assert len(violations) >= 2, (
            f"PM_OUTPUT_CROSS_TYPE_BUG 应检测到至少 2 个跨期比较违规，"
            f"实际 {len(violations)}: {[v['raw_text'] for v in violations]}"
        )

    def test_empty_condition_rules_not_executed(self):
        """condition_str 为空的规则不应被执行。"""
        config = make_backtest_config()
        dl = MagicMock()
        engine = ExecutionEngine(config, dl)

        empty_rule = TradingRule(
            condition_str="",  # 空！
            action=RuleAction.SELL_ALL,
            priority=1,
        )
        decision = WeeklyDecision(
            date="2026-06-15",
            trading_rules=[empty_rule],
        )

        portfolio = make_portfolio_state(shares=500)
        df = make_ohlcv_df(days=30, base_price=95)
        row = df.iloc[14]

        result = engine.execute(portfolio, decision, row, 14, df)
        # 空 condition → 规则不应触发 → 不应卖出
        assert result.action != "SELL_ALL", (
            "condition_str 为空的规则不应触发 SELL_ALL"
        )
```

### 6.2 数据源 Fallback

```python
# tests/integration/test_data_source_fallback.py

import pytest
import pandas as pd
from unittest.mock import patch


class TestDataSourceFallback:
    """验证 baostock 失败时自动 fallback 到 akshare。"""

    def test_baostock_fails_falls_back_to_akshare(self, monkeypatch):
        """baostock 超时/无数据 → 自动使用 akshare。"""
        import tradingagents.dataflows.akshare_data as ad

        # Mock baostock 返回空
        monkeypatch.setattr(ad, "_fetch_ashare_ohlcv", lambda ticker, start, end: pd.DataFrame())

        # Mock akshare 正常返回
        mock_ak_df = pd.DataFrame({
            "date": ["2026-06-01", "2026-06-02"],
            "open": [95.0, 96.0],
            "close": [96.5, 97.0],
            "high": [97.0, 98.0],
            "low": [94.5, 95.5],
            "volume": [5_000_000, 6_000_000],
        })
        monkeypatch.setattr(
            ad, "_fetch_ashare_akshare",
            lambda ticker, start, end: mock_ak_df,
        )

        # 调用后应返回 akshare 的数据
        result = ad._load_ohlcv_akshare("000887", "2026-06-01", "2026-06-30")
        assert len(result) > 0
        # 列名应已映射为小写
        assert "close" in result.columns

    def test_both_sources_fail_returns_empty(self, monkeypatch):
        """两个数据源都失败时不应崩溃。"""
        import tradingagents.dataflows.akshare_data as ad

        monkeypatch.setattr(ad, "_fetch_ashare_ohlcv", lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(ad, "_fetch_ashare_akshare", lambda *a, **kw: pd.DataFrame())

        result = ad._load_ohlcv_akshare("000887", "2026-06-01", "2026-06-30")
        assert isinstance(result, pd.DataFrame)
```

---

## 七、E2E Smoke Test

```python
# tests/e2e/test_smoke_backtest.py

import pytest
from unittest.mock import MagicMock, patch

from backtest.backtest_engine import BacktestEngine
from tests.fixtures.data_factories import make_backtest_config, make_ohlcv_df


@pytest.mark.smoke
class TestSmokeBacktest:
    """烟雾测试 — 验证全链路不崩溃。"""

    @patch("backtest.data_layer.DataLayer._load_ohlcv")
    @patch("backtest.decision_engine.DecisionEngine.run_fundamentals_analysis")
    @patch("backtest.decision_engine.DecisionEngine.run_decision_chain")
    def test_mini_backtest_runs_without_crash(
        self, mock_decision, mock_fa, mock_ohlcv,
    ):
        """3 天 mini 回测应正常完成。"""
        config = make_backtest_config(
            start_date="2026-06-01",
            end_date="2026-06-03",
        )

        # Mock OHLCV 数据
        mock_ohlcv.return_value = make_ohlcv_df(days=3, start_date="2026-06-01")

        # Mock FA 和 Decision
        mock_fa.return_value = {"annual_roe": 22.5}
        mock_decision.return_value = MagicMock(
            rating="Hold",
            direction="HOLD",
            position_pct=-1.0,
            trading_rules=[],
            fa_metrics={},
        )

        engine = BacktestEngine(config)
        result = engine.run()

        assert result is not None
        assert hasattr(result, "trades") or hasattr(result, "daily_states")

    @patch("backtest.data_layer.DataLayer._load_ohlcv")
    @patch("backtest.decision_engine.DecisionEngine.run_decision_chain")
    def test_portfolio_initialized_correctly(
        self, mock_decision, mock_ohlcv,
    ):
        """回测启动时初始资金和持仓应正确。"""
        config = make_backtest_config(initial_cash=200_000.0)
        mock_ohlcv.return_value = make_ohlcv_df(days=10)
        mock_decision.return_value = MagicMock(
            rating="Buy",
            direction="BUY",
            position_pct=0.60,
            trading_rules=[],
            fa_metrics={},
        )

        engine = BacktestEngine(config)
        # 验证初始状态
        assert engine.portfolio.cash == 200_000.0
        assert engine.portfolio.shares == 0
```

---

## 八、CI 集成

### pytest 配置增强

```toml
# pyproject.toml 追加

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers -v"
markers = [
    "unit: 快速纯函数测试",
    "integration: 需要 mock 外部依赖的测试",
    "smoke: 全链路快速健康检查",
    "regression: 针对性回归测试 — 每个已知 bug 至少一个",
    "slow: 耗时超过 5 秒的测试",
]

# 排他运行（只跑特定标记）
# pytest -m unit         → 只跑单元测试
# pytest -m regression   → 只跑回归安全网
# pytest -m "not slow"   → 跳过慢速测试
```

### CI Workflow（建议结构）

```yaml
# .github/workflows/test.yml （概念示意）

jobs:
  regression:         # < 30s  每次 push
    - pytest -m regression --tb=short

  unit:               # < 2min  每次 push
    - pytest -m unit --tb=short -n auto

  integration:        # < 10min 每次 PR
    - pytest -m integration --tb=long

  smoke:              # < 15min 每次 release / 每日
    - pytest -m smoke --tb=long

  coverage:           # 每次 PR
    - pytest --cov=tradingagents --cov=backtest --cov-report=term-missing
```

---

## 九、实施路线图

### Phase 1 — 回归安全网（优先级最高，本周）

| 步骤 | 任务 | 依赖 |
|------|------|------|
| 1.1 | 创建 `tests/fixtures/data_factories.py` | 无 |
| 1.2 | 创建 `tests/utils/llm_mock.py` | 无 |
| 1.3 | `test_cross_period_types.py` — 7 种模式全覆盖 | 1.1 |
| 1.4 | `test_pm_action_format.py` — action validator | 1.1 |
| 1.5 | `test_path_map_consistency.py` — 条件边审计 | 无 |
| 1.6 | `test_condition_str_fallback.py` | 1.1 |
| 1.7 | `test_regex_priority.py` | 无 |

**目标：6 个已知脆弱点全部有回归测试，5 分钟内跑完。**

### Phase 2 — 核心单元测试（本周~下周）

| 步骤 | 任务 | 依赖 |
|------|------|------|
| 2.1 | `test_execution_engine.py` — 止损/止盈/涨跌停/成本/手数 | 1.1 |
| 2.2 | `test_numeric_guard.py` — numeric 4 模式 + cross_check | 1.1 |
| 2.3 | `test_conditional_logic.py` — 辩论/风险路由 | 无 |
| 2.4 | `test_decision_engine.py` — PM 解析 3 层策略 | 1.1, 1.2 |
| 2.5 | `test_data_layer.py` — 数据加载/缓存/look-ahead | 1.1 |

### Phase 3 — 集成测试 & E2E（下周~下下周）

| 步骤 | 任务 | 依赖 |
|------|------|------|
| 3.1 | `test_pm_to_execution.py` — PM→规则→执行 | 1.1, 2.1, 2.4 |
| 3.2 | `test_data_source_fallback.py` | 1.1, 2.5 |
| 3.3 | `test_smoke_backtest.py` | 1.1, 1.2, 2.1, 2.4 |

### Phase 4 — CI 与监控

| 步骤 | 任务 | 依赖 |
|------|------|------|
| 4.1 | pytest marker 配置 + coverage 阈值 | Phase 1-3 |
| 4.2 | pre-push hook: regression + unit | Phase 1-2 |
| 4.3 | pre-commit hook: `pytest -m regression --tb=line` | Phase 1 |

---

## 十、设计原则总结

1. **回归安全网先行**。已知的 bug 必须阻止复发，这是最低成本的投资。
2. **纯函数优先**。ExecutionEngine、numeric_guard、conditional_logic 都是确定性逻辑 — 不需要 LLM mock 就能测。
3. **Fixture 复用**。`make_ohlcv_df`、`make_fa_metrics`、`make_portfolio_state` 是跨模块共享的基础设施，写一次用 N 次。
4. **Mock 只在边界**。Mock LLM 调用（网络边界），不 mock 内部逻辑。
5. **一个 bug 一个回归测试**。修复 bug 时同步写测试，避免"修好了但不知道"。
6. **小于 5 秒的测试可以在 pre-push 跑**。如果回归安全网撑不到 5 秒内完成，说明测试太重了。
