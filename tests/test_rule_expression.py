"""Tests for rule_expression.py — SQL-like rule expression engine."""

import pytest
import pandas as pd
import numpy as np

from backtest.rule_expression import (
    AtomicExpr, AndExpr, OrExpr, NotExpr, FuncExpr,
    Op, parse_op, parse_sql, sql_from_conditions,
    resolve_field, SQLParseError, FuncRegistry,
)


# ── AtomicExpr ──────────────────────────────────────────────────────

class TestAtomicExpr:
    def test_close_lt(self):
        expr = AtomicExpr("close", Op.LT, 50.0)
        assert expr.evaluate({"close": 45.0}) is True
        assert expr.evaluate({"close": 55.0}) is False
        assert expr.evaluate({"close": 50.0}) is False  # strict <

    def test_close_lte(self):
        expr = AtomicExpr("close", Op.LTE, 50.0)
        assert expr.evaluate({"close": 50.0}) is True

    def test_close_gt(self):
        expr = AtomicExpr("close", Op.GT, 100.0)
        assert expr.evaluate({"close": 120.0}) is True
        assert expr.evaluate({"close": 80.0}) is False

    def test_close_gte(self):
        expr = AtomicExpr("close", Op.GTE, 100.0)
        assert expr.evaluate({"close": 100.0}) is True

    def test_eq(self):
        expr = AtomicExpr("close", Op.EQ, 50.0)
        assert expr.evaluate({"close": 50.0}) is True
        assert expr.evaluate({"close": 50.000001}) is True  # within epsilon
        assert expr.evaluate({"close": 51.0}) is False

    def test_neq(self):
        expr = AtomicExpr("close", Op.NEQ, 50.0)
        assert expr.evaluate({"close": 51.0}) is True
        assert expr.evaluate({"close": 50.0}) is False

    def test_missing_field(self):
        expr = AtomicExpr("rsi", Op.GT, 70.0)
        assert expr.evaluate({"close": 100.0}) is False

    def test_nan_value(self):
        expr = AtomicExpr("rsi", Op.GT, 70.0)
        assert expr.evaluate({"rsi": np.nan}) is False

    def test_chinese_field(self):
        expr = AtomicExpr("收盘价", Op.LT, 50.0)
        assert expr.evaluate({"close": 45.0}) is True
        assert expr.fields() == ["close"]

    def test_to_sql(self):
        expr = AtomicExpr("close", Op.LT, 50.0)
        assert expr.to_sql() == "close < 50.0"


# ── Logic Operators ─────────────────────────────────────────────────

class TestAndExpr:
    def test_and_both_true(self):
        expr = AndExpr(
            AtomicExpr("close", Op.LT, 50.0),
            AtomicExpr("volume", Op.GT, 10000.0),
        )
        assert expr.evaluate({"close": 45.0, "volume": 20000.0}) is True

    def test_and_one_false(self):
        expr = AndExpr(
            AtomicExpr("close", Op.LT, 50.0),
            AtomicExpr("volume", Op.GT, 10000.0),
        )
        assert expr.evaluate({"close": 45.0, "volume": 5000.0}) is False

    def test_and_flatten_nested(self):
        inner = AndExpr(
            AtomicExpr("close", Op.LT, 50.0),
            AtomicExpr("volume", Op.GT, 10000.0),
        )
        outer = AndExpr(
            inner,
            AtomicExpr("rsi", Op.GT, 70.0),
        )
        # Should be flattened to 3 operands
        assert len(outer.operands) == 3

    def test_and_to_sql(self):
        expr = AndExpr(
            AtomicExpr("close", Op.LT, 50.0),
            AtomicExpr("volume", Op.GT, 10000.0),
        )
        sql = expr.to_sql()
        assert "close < 50.0" in sql
        assert "volume > 10000.0" in sql
        assert "AND" in sql


class TestOrExpr:
    def test_or_one_true(self):
        expr = OrExpr(
            AtomicExpr("close", Op.LT, 50.0),
            AtomicExpr("rsi", Op.GT, 70.0),
        )
        assert expr.evaluate({"close": 45.0, "rsi": 30.0}) is True
        assert expr.evaluate({"close": 55.0, "rsi": 80.0}) is True

    def test_or_both_false(self):
        expr = OrExpr(
            AtomicExpr("close", Op.LT, 50.0),
            AtomicExpr("rsi", Op.GT, 70.0),
        )
        assert expr.evaluate({"close": 55.0, "rsi": 30.0}) is False

    def test_or_to_sql(self):
        expr = OrExpr(
            AtomicExpr("close", Op.LT, 50.0),
            AtomicExpr("rsi", Op.GT, 70.0),
        )
        sql = expr.to_sql()
        assert "OR" in sql


class TestNotExpr:
    def test_not(self):
        expr = NotExpr(AtomicExpr("close", Op.GT, 100.0))
        assert expr.evaluate({"close": 80.0}) is True
        assert expr.evaluate({"close": 120.0}) is False

    def test_not_to_sql(self):
        expr = NotExpr(AtomicExpr("close", Op.GT, 100.0))
        assert expr.to_sql() == "NOT (close > 100.0)"


# ── Operator Overloads ──────────────────────────────────────────────

class TestOperatorOverloads:
    def test_and_overload(self):
        a = AtomicExpr("close", Op.LT, 50.0)
        b = AtomicExpr("volume", Op.GT, 10000.0)
        expr = a & b
        assert isinstance(expr, AndExpr)
        assert expr.evaluate({"close": 45.0, "volume": 20000.0}) is True

    def test_or_overload(self):
        a = AtomicExpr("close", Op.LT, 50.0)
        b = AtomicExpr("rsi", Op.GT, 70.0)
        expr = a | b
        assert isinstance(expr, OrExpr)

    def test_not_overload(self):
        a = AtomicExpr("close", Op.GT, 100.0)
        expr = ~a
        assert isinstance(expr, NotExpr)


# ── FuncExpr ────────────────────────────────────────────────────────

class TestFuncExpr:
    def test_ma_with_precomputed(self):
        """MA 使用预计算的 ma20 字段。"""
        expr = FuncExpr("ma", ["close", 20], Op.GT, 0.0, compare_field="close")
        # MA(20) = 105, close = 100 → MA > close
        assert expr.evaluate({"close": 100.0, "ma20": 105.0}) is True
        assert expr.evaluate({"close": 100.0, "ma20": 95.0}) is False

    def test_rsi_with_precomputed(self):
        expr = FuncExpr("rsi", [14], Op.LT, 30.0)
        assert expr.evaluate({"rsi": 25.0}) is True
        assert expr.evaluate({"rsi": 35.0}) is False

    def test_boll_lower(self):
        expr = FuncExpr("boll_lower", [20], Op.GT, 0.0, compare_field="close")
        assert expr.evaluate({"close": 100.0, "boll_lower": 95.0}) is False  # 95 > 100? No
        assert expr.evaluate({"close": 100.0, "boll_lower": 105.0}) is True  # 105 > 100? Yes

    def test_func_to_sql(self):
        expr = FuncExpr("ma", ["close", 20], Op.GT, 0.0, compare_field="close")
        assert expr.to_sql() == "MA(close, 20) > close"


# ── SQL Parser ──────────────────────────────────────────────────────

class TestParseSQL:
    def test_simple_close_lt(self):
        expr = parse_sql("close < 50")
        assert isinstance(expr, AtomicExpr)
        assert expr.evaluate({"close": 45.0}) is True

    def test_simple_rsi_gt(self):
        expr = parse_sql("rsi > 70")
        assert expr.evaluate({"rsi": 75.0}) is True

    def test_and_two_conditions(self):
        expr = parse_sql("close < 50 AND volume > 10000")
        assert isinstance(expr, AndExpr)
        assert expr.evaluate({"close": 45.0, "volume": 20000.0}) is True
        assert expr.evaluate({"close": 45.0, "volume": 5000.0}) is False

    def test_or_two_conditions(self):
        expr = parse_sql("close < 50 OR rsi > 70")
        assert isinstance(expr, OrExpr)
        assert expr.evaluate({"close": 55.0, "rsi": 75.0}) is True

    def test_and_or_mixed(self):
        expr = parse_sql("close < 50 AND (rsi > 70 OR macd > 0)")
        assert isinstance(expr, AndExpr)
        assert expr.evaluate({"close": 45.0, "rsi": 75.0, "macd": -1.0}) is True
        assert expr.evaluate({"close": 45.0, "rsi": 30.0, "macd": -1.0}) is False

    def test_not(self):
        expr = parse_sql("NOT (close > 100)")
        assert isinstance(expr, NotExpr)
        assert expr.evaluate({"close": 80.0}) is True

    def test_func_ma(self):
        expr = parse_sql("MA(close, 20) > close")
        assert isinstance(expr, FuncExpr)
        assert expr.evaluate({"close": 100.0, "ma20": 105.0}) is True

    def test_func_rsi(self):
        expr = parse_sql("RSI(14) < 30")
        assert isinstance(expr, FuncExpr)
        assert expr.evaluate({"rsi": 25.0}) is True

    def test_func_boll_lower(self):
        expr = parse_sql("BOLL_LOWER(20) < close")
        assert isinstance(expr, FuncExpr)

    def test_chinese_operators(self):
        expr = parse_sql("close 低于 50")
        assert isinstance(expr, AtomicExpr)
        assert expr.evaluate({"close": 45.0}) is True

    def test_empty_raises(self):
        with pytest.raises(SQLParseError):
            parse_sql("")

    def test_no_operator_raises(self):
        with pytest.raises(SQLParseError):
            parse_sql("close 50")


# ── sql_from_conditions ─────────────────────────────────────────────

class TestSQLFromConditions:
    def test_single(self):
        expr = sql_from_conditions([("close", "<", 50.0)])
        assert isinstance(expr, AtomicExpr)
        assert expr.evaluate({"close": 45.0}) is True

    def test_multiple_and(self):
        expr = sql_from_conditions([
            ("close", "<", 50.0),
            ("volume", ">", 10000.0),
        ])
        assert isinstance(expr, AndExpr)


# ── resolve_field ───────────────────────────────────────────────────

class TestResolveField:
    def test_close(self):
        assert resolve_field("close") == "close"
        assert resolve_field("收盘价") == "close"

    def test_rsi(self):
        assert resolve_field("RSI") == "rsi"

    def test_unknown(self):
        assert resolve_field("unknown_field") == "unknown_field"


# ── Integration: Complex Real-World Rules ───────────────────────────

class TestIntegration:
    def test_stop_loss_rule(self):
        """止损规则: close < 45.48"""
        expr = parse_sql("close < 45.48")
        assert expr.evaluate({"close": 44.0}) is True
        assert expr.evaluate({"close": 46.0}) is False

    def test_compound_reduce(self):
        """复合减仓: close < 50 AND volume > 20000"""
        expr = parse_sql("close < 50 AND volume > 20000")
        assert expr.evaluate({"close": 45.0, "volume": 25000.0}) is True
        assert expr.evaluate({"close": 55.0, "volume": 25000.0}) is False

    def test_entry_rsi_macd(self):
        """入场: RSI(14) < 30 AND MACD() > 0"""
        expr = parse_sql("RSI(14) < 30 AND macd > 0")
        assert expr.evaluate({"rsi": 25.0, "macd": 1.5}) is True
        assert expr.evaluate({"rsi": 35.0, "macd": 1.5}) is False

    def test_bollinger_entry(self):
        """布林下轨入场: close < BOLL_LOWER(20) AND volume_ratio > 1.5"""
        expr = parse_sql("close < BOLL_LOWER(20) AND volume_ratio > 1.5")
        assert expr.evaluate({"close": 90.0, "boll_lower": 95.0, "volume_ratio": 2.0}) is True
        assert expr.evaluate({"close": 100.0, "boll_lower": 95.0, "volume_ratio": 2.0}) is False

    def test_fundamental_circuit_break(self):
        """基本面熔断: annual_ocf_to_netprofit < 0.5 OR annual_debt_ratio > 70"""
        expr = parse_sql("annual_ocf_to_netprofit < 0.5 OR annual_debt_ratio > 70")
        assert expr.evaluate({"annual_ocf_to_netprofit": 0.3, "annual_debt_ratio": 50.0}) is True
        assert expr.evaluate({"annual_ocf_to_netprofit": 0.8, "annual_debt_ratio": 50.0}) is False

    def test_ma_crossover(self):
        """均线金叉: MA(close, 5) > close（简化版，右侧字段比较暂不支持）"""
        expr = parse_sql("MA(close, 5) > close")
        assert expr.evaluate({"close": 100.0, "ma5": 105.0}) is True
        assert expr.evaluate({"close": 100.0, "ma5": 95.0}) is False

    def test_trailing_stop(self):
        """移动止损: close < ma50（用预计算均线）"""
        # 注意：字段比较（如 close < ma50）当前解析为 AtomicExpr 但 value=0
        # 需要显式用 FuncExpr 或改用 close < MA(close,50)
        expr = parse_sql("close < MA(close, 50)")
        assert expr.evaluate({"close": 90.0, "ma50": 100.0}) is True
        assert expr.evaluate({"close": 110.0, "ma50": 100.0}) is False


# ── FuncRegistry ────────────────────────────────────────────────────

class TestFuncRegistry:
    def test_list_functions(self):
        funcs = FuncRegistry.list()
        assert "ma" in funcs
        assert "rsi" in funcs
        assert "macd" in funcs
        assert "boll_upper" in funcs
        assert "boll_lower" in funcs

    def test_unknown_function(self):
        result = FuncRegistry.call("unknown", {}, [])
        assert result is None
