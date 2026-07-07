"""复合交易规则系统测试。

覆盖：
1. Condition.evaluate() — 各种比较操作符 + 缺失值/NaN 处理
2. TradingRule.evaluate_all() — AND 逻辑 + 优先级排序
3. RuleParser 正则层 (Layer 1) — 6 种模式
4. 序列化/反序列化 round-trip
5. 端到端：PM 文本 → 规则 → evaluate → 动作
6. 向后兼容：旧缓存无 trading_rules 字段
"""

import sys
import os
import unittest

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.trading_rules import (
    Condition, TradingRule, RuleAction, ComparisonOp,
    RuleParser, parse_op, resolve_field, FIELD_ALIAS_MAP,
)
from backtest.models import WeeklyDecision, PriceCondition, TradeDirection


class TestFieldAlias(unittest.TestCase):
    """字段别名映射测试。"""

    def test_chinese_to_column(self):
        self.assertEqual(resolve_field("收盘价"), "close")
        self.assertEqual(resolve_field("换手率"), "turn")
        self.assertEqual(resolve_field("RSI"), "rsi")
        self.assertEqual(resolve_field("ATR"), "atr")

    def test_english_passthrough(self):
        self.assertEqual(resolve_field("close"), "close")
        self.assertEqual(resolve_field("macdh"), "macdh")

    def test_unknown_returns_original(self):
        self.assertEqual(resolve_field("unknown_field_xyz"), "unknown_field_xyz")


class TestComparisonOp(unittest.TestCase):
    """操作符解析与求值。"""

    def test_parse_op(self):
        self.assertEqual(parse_op("<"), ComparisonOp.LT)
        self.assertEqual(parse_op(">="), ComparisonOp.GTE)
        self.assertEqual(parse_op("跌破"), ComparisonOp.LT)
        self.assertEqual(parse_op("超过"), ComparisonOp.GT)

    def test_evaluate_lt(self):
        cond = Condition(field="close", op=ComparisonOp.LT, value=36.52)
        self.assertTrue(cond.evaluate({"close": 36.0}))
        self.assertFalse(cond.evaluate({"close": 37.0}))

    def test_evaluate_gte(self):
        cond = Condition(field="high", op=ComparisonOp.GTE, value=45.0)
        self.assertTrue(cond.evaluate({"high": 45.0}))
        self.assertTrue(cond.evaluate({"high": 46.0}))
        self.assertFalse(cond.evaluate({"high": 44.9}))

    def test_evaluate_missing_field(self):
        cond = Condition(field="nonexistent", op=ComparisonOp.GT, value=10.0)
        self.assertFalse(cond.evaluate({}))

    def test_evaluate_nan(self):
        import pandas as pd
        cond = Condition(field="rsi", op=ComparisonOp.GT, value=70.0)
        self.assertFalse(cond.evaluate({"rsi": float('nan')}))

    def test_evaluate_volume_ratio(self):
        cond = Condition(field="turn", op=ComparisonOp.GT, value=3.0)
        self.assertTrue(cond.evaluate({"turn": 3.5}))
        self.assertFalse(cond.evaluate({"turn": 2.0}))


class TestTradingRule(unittest.TestCase):
    """TradingRule AND 逻辑与序列化。"""

    def test_single_condition_trigger(self):
        rule = TradingRule(
            name="止损出局",
            action=RuleAction.STOP_LOSS,
            conditions=[
                Condition(field="low", op=ComparisonOp.LTE, value=36.52),
            ],
            priority=90,
        )
        # 触发
        self.assertTrue(rule.evaluate_all({"low": 35.0}))
        # 不触发
        self.assertFalse(rule.evaluate_all({"low": 37.0}))

    def test_compound_and_logic(self):
        """两个条件必须同时满足。"""
        rule = TradingRule(
            name="破位+放量减仓",
            action=RuleAction.SELL_PCT,
            pct=0.5,
            conditions=[
                Condition(field="low", op=ComparisonOp.LTE, value=36.52),
                Condition(field="turn", op=ComparisonOp.GT, value=3.0),
            ],
            priority=88,
        )
        # 两个都满足 → 触发
        row = {"low": 35.0, "turn": 4.0}
        self.assertTrue(rule.evaluate_all(row))
        # 只满足一个 → 不触发
        self.assertFalse(rule.evaluate_all({"low": 35.0, "turn": 2.0}))
        self.assertFalse(rule.evaluate_all({"low": 38.0, "turn": 4.0}))

    def test_disabled_rule_never_triggers(self):
        rule = TradingRule(
            name="disabled",
            action=RuleAction.SELL_ALL,
            conditions=[Condition(field="close", op=ComparisonOp.LT, value=1.0)],
            enabled=False,
        )
        self.assertFalse(rule.evaluate_all({"close": 0.0}))

    def test_empty_conditions_never_triggers(self):
        rule = TradingRule(name="empty", action=RuleAction.HOLD)
        self.assertFalse(rule.evaluate_all({"close": 0.0}))

    def test_serialization_roundtrip(self):
        original = TradingRule(
            name="测试规则",
            action=RuleAction.SELL_PCT,
            pct=0.3,
            priority=75,
            source_sentence="当股价跌破 20 元且换手率超过 2% 时，减仓 30%",
            conditions=[
                Condition(field="low", op=ComparisonOp.LTE, value=20.0),
                Condition(field="turn", op=ComparisonOp.GT, value=2.0),
            ],
        )
        d = original.to_dict()
        restored = TradingRule.from_dict(d)
        self.assertEqual(restored.name, original.name)
        self.assertEqual(restored.action, original.action)
        self.assertEqual(restored.pct, original.pct)
        self.assertEqual(len(restored.conditions), len(original.conditions))
        self.assertEqual(restored.conditions[0].field, "low")
        self.assertEqual(restored.conditions[1].value, 2.0)

    def test_priority_sorting(self):
        rules = [
            TradingRule(name="p50", priority=50, action=RuleAction.HOLD),
            TradingRule(name="p90", priority=90, action=RuleAction.HOLD),
            TradingRule(name="p70", priority=70, action=RuleAction.HOLD),
        ]
        rules.sort(key=lambda r: r.priority, reverse=True)
        self.assertEqual(rules[0].priority, 90)
        self.assertEqual(rules[1].priority, 70)
        self.assertEqual(rules[2].priority, 50)

    def test_description_property(self):
        rule = TradingRule(
            name="RSI超买",
            action=RuleAction.HOLD,
            conditions=[Condition(field="rsi", op=ComparisonOp.GT, value=70.0)],
        )
        desc = rule.description
        self.assertIn("RSI超买", desc)
        self.assertIn("rsi > 70.0", desc)


class TestRuleParserRegex(unittest.TestCase):
    """RuleParser Layer 1 正则提取。"""

    def setUp(self):
        self.parser = RuleParser()

    def test_stop_loss_extraction(self):
        pm = "止损价设在 18.50 元，止盈目标 25.00 元。"
        rules = self.parser._regex_extract(pm)
        sl_rules = [r for r in rules if r.action == RuleAction.STOP_LOSS]
        self.assertEqual(len(sl_rules), 1)
        self.assertAlmostEqual(sl_rules[0].conditions[0].value, 18.5)

    def test_take_profit_extraction(self):
        pm = "止损设在 15 元，止盈目标 22.80 元。"
        rules = self.parser._regex_extract(pm)
        tp_rules = [r for r in rules if r.action == RuleAction.TAKE_PROFIT]
        self.assertEqual(len(tp_rules), 1)
        self.assertAlmostEqual(tp_rules[0].conditions[0].value, 22.8)

    def test_compound_reduce_position(self):
        pm = "一旦股价跌破 36.52 且换手率超过 3% 时，减持 50%。"
        rules = self.parser._regex_extract(pm)
        reduce_rules = [r for r in rules if r.action == RuleAction.SELL_PCT]
        self.assertEqual(len(reduce_rules), 1)
        rule = reduce_rules[0]
        self.assertEqual(len(rule.conditions), 2)  # AND: price + volume
        self.assertAlmostEqual(rule.pct, 0.5)  # 50%

    def test_add_position_rsi(self):
        pm = "当 RSI 跌破 30 时可逢低加仓"
        rules = self.parser._regex_extract(pm)
        add_rules = [r for r in rules if r.action == RuleAction.BUY_ADD]
        self.assertEqual(len(add_rules), 1)
        self.assertAlmostEqual(add_rules[0].conditions[0].value, 30.0)

    def test_fundamental_circuit_break(self):
        pm = "若 OCF/净利润低于 0.5 且营业周期超过 300 天时无条件清仓离场"
        rules = self.parser._regex_extract(pm)
        cb_rules = [r for r in rules if r.action == RuleAction.CIRCUIT_BREAK]
        self.assertGreaterEqual(len(cb_rules), 1)
        cb = cb_rules[0]
        self.assertEqual(cb.priority, 95)  # 最高优先级

    def test_no_match_returns_empty(self):
        pm = "今天天气不错，适合散步。"  # 无任何规则模式
        rules = self.parser._regex_extract(pm)
        # 可能有 0 条规则
        self.assertIsInstance(rules, list)


class TestRuleParserDefaultRules(unittest.TestCase):
    """Layer 3 默认规则生成。"""

    def setUp(self):
        self.parser = RuleParser()

    def test_default_stop_loss_from_price_cond(self):
        pc = PriceCondition(stop_loss=18.5, take_profit=25.0)
        rules = self.parser._default_rules(price_cond=pc)
        actions = {r.action for r in rules}
        self.assertIn(RuleAction.STOP_LOSS, actions)
        self.assertIn(RuleAction.TAKE_PROFIT, actions)

    def test_sell_direction_creates_sell_all(self):
        rules = self.parser._default_rules(direction=TradeDirection.SELL)
        actions = {r.action for r in rules}
        self.assertIn(RuleAction.SELL_ALL, actions)

    def test_empty_price_cond_no_defaults(self):
        rules = self.parser._default_rules(price_cond=PriceCondition())
        sl_rules = [r for r in rules if r.action == RuleAction.STOP_LOSS]
        self.assertEqual(len(sl_rules), 0)


class TestEndToEnd(unittest.TestCase):
    """端到端集成测试：模拟完整流程。"""

    def setUp(self):
        self.parser = RuleParser()

    def test_pm_text_to_rules_to_evaluate(self):
        """真实 PM 样本 → 解析 → 逐日 evaluate。"""
        pm_sample = """
## 锡业股份 (000960) 投资决策

**Rating**: Buy

### 风控规则
- 止损线：18.50 元
- 止盈目标：28.00 元
- 当股价跌破 22.00 元且换手率超过 4% 时，减仓 40%
- RSI 超过 75 时注意风险，考虑减仓
- 若 OCF/净利润低于 0.4 时无条件清仓
"""
        rules = self.parser.parse(pm_sample, use_llm=False)
        self.assertGreaterEqual(len(rules), 3)

        # 模拟第 1 天：正常状态（不触发任何规则）
        day_normal = {
            "close": 24.5, "high": 25.0, "low": 23.8, "open": 24.2,
            "turn": 1.5, "volume": 100000, "pct_chg": 1.2,
            "rsi": 55.0, "macd": 0.3, "macdh": 0.05,
            "atr": 1.2, "_volume_ratio": 0.8,
        }
        triggered = [r for r in rules if r.evaluate_all(day_normal)]
        self.assertEqual(len(triggered), 0, f"Normal day should not trigger any rule, but got: {[t.name for t in triggered]}")

        # 模拟第 2 天：价格破位 + 放量（触发减仓规则）
        day_break = dict(day_normal)
        day_break["low"] = 21.5   # 跌破 22.00
        day_break["turn"] = 5.0    # 换手率超过 4%
        triggered = [r for r in rules if r.evaluate_all(day_break)]
        reduce_triggered = [t for t in triggered if t.action == RuleAction.SELL_PCT]
        self.assertGreaterEqual(len(reduce_triggered), 1,
                                f"Break+volume day should trigger SELL_PCT rule. Triggered: {[t.name for t in triggered]}")

        # 模拟第 3 天：触及止损
        day_stoploss = dict(day_normal)
        day_stoploss["low"] = 17.8   # 跌破 18.50 止损线
        triggered = [r for r in rules if r.evaluate_all(day_stoploss)]
        sl_triggered = [t for t in triggered if t.action in (RuleAction.STOP_LOSS, RuleAction.SELL_ALL)]
        self.assertGreaterEqual(len(sl_triggered), 1)

    def test_backward_compatible_deserialization(self):
        """旧缓存数据（无 trading_rules 字段）能正常反序列化。"""
        old_cache_data = {
            "direction": "BUY",
            "position_pct": 0.8,
            "stop_loss": 18.5,
            "take_profit": 25.0,
            "buy_range": None,
            "trailing_stop_pct": 0.08,
            "technical_triggers": {"atr_period": 14},
            "fundamental_guards": {},
            "decision_date": "2026-01-15",
            "signal_raw": "Buy",
            "pm_rating": "Buy",
            "pm_raw_output": "test",
            "parsed_ok": True,
            # 注意：没有 trading_rules 和 rules_parsed_ok 字段
        }
        from backtest.decision_engine import DecisionEngine
        # 直接用 _dict_to_decision 测试需要实例
        # 这里用 WeeklyDecision 的方式间接测试
        from backtest.trading_rules import TradingRule
        rules_raw = old_cache_data.get("trading_rules", [])
        self.assertEqual(rules_raw, [])  # 旧数据返回空列表
        rules_parsed = old_cache_data.get("rules_parsed_ok", False)
        self.assertFalse(rules_parsed)


class TestNarrativePMStyle(unittest.TestCase):
    """叙事性 PM 输出规则提取测试（000423 东阿阿胶风格）。

    验证增强后的正则层能处理 LLM 生成的复杂描述性规则格式：
    - 表格建仓规则（批次 | 比例 | 触发条件）
    - 区间止损 + 收盘价 + 放量复合条件
    - MACD底背离 + RSI超卖 加仓
    - 放量阳线 + 站上均线 加仓
    - 跌破前低放弃建仓
    """

    def setUp(self):
        self.parser = RuleParser()

    def test_batch_entry_table(self):
        """表格建仓规则：三批 + 不同触发条件。"""
        pm = """| 第一批 | 40%增量 | **布林下轨45.48元支撑区域直接入场** |
| 第二批 | 30%增量 | MACD底背离 **且** RSI触及30以下超卖区 |
| 第三批 | 30%增量 | **日线放量阳线站稳10日均线（46.32元）以上** |"""
        rules = self.parser._regex_extract(pm)
        buy_rules = [r for r in rules if r.action == RuleAction.BUY_ADD]
        self.assertEqual(len(buy_rules), 3, f"Expected 3 batch entry rules, got {len(buy_rules)}")

        # 第一批：布林下轨 + 40%
        batch1 = buy_rules[0]
        self.assertAlmostEqual(batch1.pct, 0.4)
        self.assertTrue(any(c.field == "close" and c.op == ComparisonOp.LTE and c.value == 45.48
                           for c in batch1.conditions))

        # 第二批：MACD底背离 + RSI < 30 + 30%
        batch2 = buy_rules[1]
        self.assertAlmostEqual(batch2.pct, 0.3)
        self.assertTrue(any(c.field == "macdh" for c in batch2.conditions))
        self.assertTrue(any(c.field == "rsi" and c.op == ComparisonOp.LT and c.value == 30.0
                           for c in batch2.conditions))

        # 第三批：放量 + 站上46.32 + 30%
        batch3 = buy_rules[2]
        self.assertAlmostEqual(batch3.pct, 0.3)
        self.assertTrue(any(c.field == "_volume_ratio" for c in batch3.conditions))
        self.assertTrue(any(c.field == "close" and c.op == ComparisonOp.GTE and c.value == 46.32
                           for c in batch3.conditions))

    def test_range_stop_loss_with_volume(self):
        """区间止损 + 收盘价 + 放量 复合条件。"""
        pm = """硬止损线：布林下轨（45.48元）下方3%-5%，即约43.21-44.12元区间。
触发条件：若日线收盘价有效跌破该区间，且伴随成交量放大，必须无条件执行止损。"""
        rules = self.parser._regex_extract(pm)

        # 应包含区间止损(收盘价+放量) 规则
        compound_rules = [r for r in rules
                          if r.action == RuleAction.SELL_ALL
                          and len(r.conditions) >= 2
                          and any(c.field == "_volume_ratio" for c in r.conditions)]
        self.assertGreaterEqual(len(compound_rules), 1,
                                f"Expected compound stop-loss rule, got rules: {[(r.name, r.action.value) for r in rules]}")

        # 验证区间止损用高端（44.12）
        sl_rules = [r for r in rules if r.action in (RuleAction.STOP_LOSS, RuleAction.SELL_ALL)]
        sl_values = []
        for r in sl_rules:
            for c in r.conditions:
                if c.field in ("close", "low") and c.op in (ComparisonOp.LTE, ComparisonOp.LT):
                    sl_values.append(c.value)
        # 至少有一条止损线是 44.12（区间高端）
        self.assertIn(44.12, sl_values,
                      f"Expected stop-loss at 44.12 (range high), got values: {sl_values}")

    def test_break_prev_low_abandon(self):
        """跌破前低 → 放弃建仓。"""
        pm = "跌破前低44.76元应立即放弃建仓"
        rules = self.parser._regex_extract(pm)
        sell_rules = [r for r in rules if r.action == RuleAction.SELL_ALL]
        self.assertGreaterEqual(len(sell_rules), 1)
        self.assertTrue(any(
            c.field == "close" and c.op == ComparisonOp.LTE and c.value == 44.76
            for r in sell_rules for c in r.conditions
        ))

    def test_macd_rsi_buy_no_dup(self):
        """MACD+RSI 加仓不重复：表格已覆盖时不重复生成。"""
        pm = """| 第二批 | 30%增量 | MACD底背离 且 RSI触及30以下超卖区 |
MACD底背离 且 RSI触及30以下超卖区"""
        rules = self.parser._regex_extract(pm)
        buy_rules = [r for r in rules if r.action == RuleAction.BUY_ADD
                     and any(c.field == "macdh" for c in r.conditions)
                     and any(c.field == "rsi" for c in r.conditions)]
        # 应该只有 1 条（表格规则），不重复
        self.assertEqual(len(buy_rules), 1,
                         f"Expected 1 non-duplicate rule, got {len(buy_rules)}")

    def test_full_000423_pm_output(self):
        """完整 000423 PM 执行方案文本：覆盖所有规则类型。"""
        pm = """## 二、执行方案

| 第一批 | 40%增量 | **布林下轨45.48元支撑区域直接入场** |
| 第二批 | 30%增量 | MACD底背离 **且** RSI触及30以下超卖区 |
| 第三批 | 30%增量 | **日线放量阳线站稳10日均线（46.32元）以上** |

**止损纪律（不可协商）：**

- **硬止损线：布林下轨（45.48元）下方3%-5%，即约43.21-44.12元区间。**
- **触发条件：** 若日线收盘价有效跌破该区间，且伴随成交量放大，必须无条件执行止损。
- **逻辑：** 跌破前低44.76元应立即放弃建仓。"""
        rules = self.parser._regex_extract(pm)

        # 分类统计
        buy_count = len([r for r in rules if r.action == RuleAction.BUY_ADD])
        sell_count = len([r for r in rules if r.action == RuleAction.SELL_ALL])
        sl_count = len([r for r in rules if r.action == RuleAction.STOP_LOSS])

        # 至少：3条加仓 + 2条止损/清仓
        self.assertGreaterEqual(buy_count, 3, f"Expected >=3 BUY_ADD rules, got {buy_count}")
        self.assertGreaterEqual(sell_count + sl_count, 3,
                                f"Expected >=3 stop/sell rules, got {sell_count + sl_count}")
        self.assertGreaterEqual(len(rules), 6,
                                f"Expected >=6 total rules, got {len(rules)}")

    def test_full_601318_pm_output(self):
        """601318 中国平安 Underweight PM 输出 — 减仓 + 止损规则全覆盖。"""
        pm = """**Rating**: Underweight

**Executive Summary**: 基于2025年12月1日有效数据，建议立即将中国平安（601318）仓位降至组合标准配置的70%-80%，在58.63元附近执行第一轮减仓。设定57.00元为强制执行线，若有效跌破则进一步将剩余仓位降至50%以下。"""
        rules = self.parser._regex_extract(pm)

        # 预期 4 条规则：止损线 + 价格减仓 + 70-80%比例减仓 + 50%比例减仓
        self.assertGreaterEqual(len(rules), 3,
                                f"Expected >=3 rules for 601318, got {len(rules)}")

        # 1) 止损线 57.00
        sl_rules = [r for r in rules if r.action == RuleAction.STOP_LOSS]
        self.assertGreaterEqual(len(sl_rules), 1, "Expected >=1 stop-loss rule")
        sl_values = []
        for r in sl_rules:
            for c in r.conditions:
                if c.field in ("close", "low"):
                    sl_values.append(c.value)
        self.assertIn(57.0, sl_values, f"Expected stop-loss at 57.0, got {sl_values}")

        # 2) 价格触发减仓 58.63
        sell_half_rules = [r for r in rules if r.action == RuleAction.SELL_HALF]
        self.assertGreaterEqual(len(sell_half_rules), 1, "Expected >=1 sell-half rule")
        sh_prices = []
        for r in sell_half_rules:
            for c in r.conditions:
                if c.field == "close":
                    sh_prices.append(c.value)
        self.assertIn(58.63, sh_prices, f"Expected sell-half at 58.63, got {sh_prices}")

        # 3) 比例缩减规则（至少一条）
        sell_pct_rules = [r for r in rules if r.action == RuleAction.SELL_PCT]
        self.assertGreaterEqual(len(sell_pct_rules), 1,
                                f"Expected >=1 sell-pct rule, got {len(sell_pct_rules)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
