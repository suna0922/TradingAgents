"""类 SQL 交易规则表达式引擎。

核心设计：
- RuleExpression: 抽象基类，所有表达式节点继承它
- AtomicExpr: 原子条件  field op value
- AndExpr / OrExpr / NotExpr: 逻辑组合
- FuncExpr: 函数调用  MA(close,20) > close
- 每个节点实现 evaluate(row) -> bool 和 to_sql() -> str

与旧 Condition 的区别：
- 支持 OR / NOT，不只是 AND
- 支持函数（MA, RSI, MACD 等）
- 支持括号优先级
- SQL 字符串可直读、可调试、可持久化
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


# ── 操作符枚举 ──────────────────────────────────────────────────────

class Op(str, Enum):
    """比较操作符。"""

    LT = "<"
    LTE = "<="
    GT = ">"
    GTE = ">="
    EQ = "=="
    NEQ = "!="


# 文本→操作符映射（解析用）
_OP_MAP: Dict[str, Op] = {
    "<": Op.LT, "<=": Op.LTE,
    ">": Op.GT, ">=": Op.GTE,
    "==": Op.EQ, "=": Op.EQ,
    "!=": Op.NEQ, "≠": Op.NEQ,
    "低于": Op.LT, "低于或等于": Op.LTE,
    "高于": Op.GT, "高于或等于": Op.GTE,
    "跌破": Op.LT, "突破": Op.GT,
    "超过": Op.GT, "不低于": Op.GTE,
    "不高于": Op.LTE, "等于": Op.EQ,
    "不等于": Op.NEQ,
}

# 操作符→中文（输出用）
_OP_CN: Dict[Op, str] = {
    Op.LT: "低于", Op.LTE: "低于或等于",
    Op.GT: "高于", Op.GTE: "高于或等于",
    Op.EQ: "等于", Op.NEQ: "不等于",
}


def parse_op(text: str) -> Op:
    """从文本解析操作符，支持符号和中文。"""
    text = text.strip()
    # 精确匹配
    if text in _OP_MAP:
        return _OP_MAP[text]
    # 最长匹配（避免 < 匹配到 <=）
    for candidate in sorted(_OP_MAP.keys(), key=len, reverse=True):
        if candidate in text:
            return _OP_MAP[candidate]
    raise ValueError(f"Unknown operator: {text}")


# ── 字段别名映射（PM 自然语言 → DataFrame 列名）──────────────────────

FIELD_MAP: Dict[str, str] = {
    # 量价
    "close": "close", "收盘价": "close", "收盘": "close",
    "open": "open", "开盘价": "open", "开盘": "open",
    "high": "high", "最高价": "high", "最高": "high",
    "low": "low", "最低价": "low", "最低": "low",
    "volume": "volume", "成交量": "volume", "vol": "volume",
    "turn": "turn", "换手率": "turn", "换手": "turn",
    "pct_chg": "pct_chg", "涨跌幅": "pct_chg", "涨幅": "pct_chg", "跌幅": "pct_chg",
    "volume_ratio": "volume_ratio", "量比": "volume_ratio",
    "amplitude": "amplitude", "振幅": "amplitude",

    # 技术指标
    "rsi": "rsi", "RSI": "rsi",
    "macd": "macd", "MACD": "macd",
    "macdh": "macdh", "MACD柱": "macdh", "macd柱": "macdh",
    "ma5": "ma5", "MA5": "ma5", "5日均线": "ma5",
    "ma10": "ma10", "MA10": "ma10", "10日均线": "ma10",
    "ma20": "ma20", "MA20": "ma20", "20日均线": "ma20",
    "ma50": "ma50", "MA50": "ma50", "50日均线": "ma50",
    "ma60": "ma60", "MA60": "ma60", "60日均线": "ma60",
    "ma120": "ma120", "MA120": "ma120", "120日均线": "ma120",
    "ma200": "ma200", "MA200": "ma200", "200日均线": "ma200",
    "boll_upper": "boll_upper", "布林上轨": "boll_upper",
    "boll_lower": "boll_lower", "布林下轨": "boll_lower",
    "boll_mid": "boll_mid", "布林中轨": "boll_mid",
    "kdj_k": "kdj_k", "K": "kdj_k",
    "kdj_d": "kdj_d", "D": "kdj_d",
    "kdj_j": "kdj_j", "J": "kdj_j",
    "atr": "atr", "ATR": "atr",

    # 基本面（年报）
    "annual_roe": "annual_roe", "ROE": "annual_roe", "roe": "annual_roe",
    "annual_gross_margin": "annual_gross_margin", "毛利率": "annual_gross_margin",
    "annual_net_margin": "annual_net_margin", "净利率": "annual_net_margin",
    "annual_ocf_to_netprofit": "annual_ocf_to_netprofit",
    "OCF/净利润": "annual_ocf_to_netprofit", "ocf_profit": "annual_ocf_to_netprofit",
    "annual_debt_ratio": "annual_debt_ratio", "资产负债率": "annual_debt_ratio",
    "annual_cash_coverage": "annual_cash_coverage", "现金覆盖率": "annual_cash_coverage",
    "annual_revenue_growth": "annual_revenue_growth", "营收增长率": "annual_revenue_growth",
    "annual_profit_growth": "annual_profit_growth", "净利润增长率": "annual_profit_growth",

    # 基本面（季报）
    "quarter_roe": "quarter_roe",
    "quarter_gross_margin": "quarter_gross_margin",
    "quarter_net_margin": "quarter_net_margin",
    "quarter_ocf_to_netprofit": "quarter_ocf_to_netprofit",
    "quarter_debt_ratio": "quarter_debt_ratio",
    "quarter_revenue_growth": "quarter_revenue_growth",
    "quarter_profit_growth": "quarter_profit_growth",
}


def resolve_field(name: str) -> str:
    """将字段名映射到 DataFrame 列名。"""
    key = name.strip().lower()
    # 精确匹配（大小写不敏感）
    for alias, col in FIELD_MAP.items():
        if alias.lower() == key:
            return col
    # 未找到，返回原始值（可能是 df 中已有的列）
    return name.strip()


# ── 函数注册表 ──────────────────────────────────────────────────────

class FuncRegistry:
    """技术指标函数注册表。

    每个函数接收 (row: Dict, *args) -> float。
    row 是当日数据字典，可能包含历史序列（通过 _df 注入）。
    """

    _funcs: Dict[str, callable] = {}

    @classmethod
    def register(cls, name: str):
        def decorator(fn):
            cls._funcs[name.lower()] = fn
            return fn
        return decorator

    @classmethod
    def call(cls, name: str, row: Dict[str, Any], args: List[float]) -> Optional[float]:
        fn = cls._funcs.get(name.lower())
        if fn is None:
            logger.warning(f"[Func] Unknown function: {name}")
            return None
        try:
            return fn(row, *args)
        except Exception as e:
            logger.warning(f"[Func] {name}({args}) failed: {e}")
            return None

    @classmethod
    def list(cls) -> List[str]:
        return list(cls._funcs.keys())


# ── 内置函数实现 ────────────────────────────────────────────────────

@FuncRegistry.register("ma")
def _fn_ma(row: Dict, *args) -> Optional[float]:
    """N日移动平均线。需要 _df 注入历史数据。

    调用方式: MA(close, 20) 或 MA(20)
    """
    # 解析参数：可能是 (field, period) 或 (period,)
    if len(args) >= 2:
        period = float(args[-1])  # 最后一个参数是周期
    elif len(args) == 1:
        period = float(args[0])
    else:
        period = 20.0

    # 优先用预计算字段
    col = f"ma{int(period)}"
    val = row.get(col)
    if val is not None:
        return float(val)

    df = row.get("_df")
    if df is None:
        return None
    idx = row.get("_idx")
    if idx is None or idx < int(period):
        return None
    return df["close"].iloc[idx - int(period) + 1 : idx + 1].mean()


@FuncRegistry.register("rsi")
def _fn_rsi(row: Dict, period: float = 14.0) -> Optional[float]:
    """RSI 指标。优先用预计算字段。"""
    val = row.get("rsi")
    if val is not None:
        return float(val)
    df = row.get("_df")
    idx = row.get("_idx")
    if df is None or idx is None or idx < int(period):
        return None
    # 简化 RSI 计算
    closes = df["close"].iloc[idx - int(period) + 1 : idx + 1]
    deltas = closes.diff().dropna()
    gains = deltas[deltas > 0].sum()
    losses = -deltas[deltas < 0].sum()
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - (100.0 / (1 + rs))


@FuncRegistry.register("macd")
def _fn_macd(row: Dict) -> Optional[float]:
    """MACD 柱。优先用预计算字段。"""
    return row.get("macd")


@FuncRegistry.register("macdh")
def _fn_macdh(row: Dict) -> Optional[float]:
    """MACD 柱（同 macd）。"""
    return row.get("macdh") or row.get("macd")


@FuncRegistry.register("boll_upper")
def _fn_boll_upper(row: Dict, period: float = 20.0) -> Optional[float]:
    val = row.get("boll_upper")
    if val is not None:
        return float(val)
    df = row.get("_df")
    idx = row.get("_idx")
    if df is None or idx is None or idx < int(period):
        return None
    window = df["close"].iloc[idx - int(period) + 1 : idx + 1]
    return window.mean() + 2 * window.std()


@FuncRegistry.register("boll_lower")
def _fn_boll_lower(row: Dict, period: float = 20.0) -> Optional[float]:
    val = row.get("boll_lower")
    if val is not None:
        return float(val)
    df = row.get("_df")
    idx = row.get("_idx")
    if df is None or idx is None or idx < int(period):
        return None
    window = df["close"].iloc[idx - int(period) + 1 : idx + 1]
    return window.mean() - 2 * window.std()


@FuncRegistry.register("atr")
def _fn_atr(row: Dict, period: float = 14.0) -> Optional[float]:
    val = row.get("atr")
    if val is not None:
        return float(val)
    df = row.get("_df")
    idx = row.get("_idx")
    if df is None or idx is None or idx < 1:
        return None
    # 简化 ATR
    tr = max(
        row["high"] - row["low"],
        abs(row["high"] - df["close"].iloc[idx - 1]),
        abs(row["low"] - df["close"].iloc[idx - 1]),
    )
    return tr  # 简化版，实际应做平滑


@FuncRegistry.register("max")
def _fn_max(row: Dict, *args) -> float:
    """取最大值。"""
    return max(args)


@FuncRegistry.register("min")
def _fn_min(row: Dict, *args) -> float:
    """取最小值。"""
    return min(args)


@FuncRegistry.register("abs")
def _fn_abs(row: Dict, x: float) -> float:
    return abs(x)


# ── 表达式基类 ──────────────────────────────────────────────────────

class RuleExpression(ABC):
    """规则表达式抽象基类。

    所有表达式节点必须实现:
    - evaluate(row): 根据当日数据判断是否触发
    - to_sql(): 返回类 SQL 字符串（人类可读、可持久化）
    - fields(): 返回此表达式引用的所有字段名（用于数据依赖检查）
    """

    @abstractmethod
    def evaluate(self, row: Dict[str, Any]) -> bool:
        """评估表达式。"""
        ...

    @abstractmethod
    def to_sql(self) -> str:
        """输出类 SQL 字符串。"""
        ...

    @abstractmethod
    def fields(self) -> List[str]:
        """返回引用的字段列表。"""
        ...

    def __and__(self, other: RuleExpression) -> RuleExpression:
        return AndExpr(self, other)

    def __or__(self, other: RuleExpression) -> RuleExpression:
        return OrExpr(self, other)

    def __invert__(self) -> RuleExpression:
        return NotExpr(self)


# ── 原子表达式 ──────────────────────────────────────────────────────

@dataclass
class AtomicExpr(RuleExpression):
    """原子条件: field op value

    示例:
        AtomicExpr("close", Op.LT, 50.0)  →  close < 50
        AtomicExpr("rsi", Op.GT, 70.0)    →  rsi > 70
    """

    field: str       # 原始字段名（展示用）
    op: Op           # 操作符
    value: float     # 阈值
    resolved_field: str = ""  # 映射后的 DataFrame 列名（内部用）

    def __post_init__(self):
        if not self.resolved_field:
            self.resolved_field = resolve_field(self.field)

    def evaluate(self, row: Dict[str, Any]) -> bool:
        actual = row.get(self.resolved_field)
        if actual is None:
            logger.debug(f"[Expr] Missing field '{self.resolved_field}'")
            return False
        try:
            actual_f = float(actual)
        except (TypeError, ValueError):
            return False
        # 处理 NaN
        if isinstance(actual_f, float) and pd.isna(actual_f):
            return False

        if self.op == Op.LT:
            return actual_f < self.value
        elif self.op == Op.LTE:
            return actual_f <= self.value
        elif self.op == Op.GT:
            return actual_f > self.value
        elif self.op == Op.GTE:
            return actual_f >= self.value
        elif self.op == Op.EQ:
            return abs(actual_f - self.value) < 1e-5
        elif self.op == Op.NEQ:
            return abs(actual_f - self.value) >= 1e-5
        return False

    def to_sql(self) -> str:
        return f"{self.field} {self.op.value} {self.value}"

    def fields(self) -> List[str]:
        return [self.resolved_field]


# ── 函数表达式 ──────────────────────────────────────────────────────

@dataclass
class FuncExpr(RuleExpression):
    """函数调用表达式: FUNC(field, period) op value

    示例:
        FuncExpr("ma", ["close", 20], Op.GT, "close")
        → MA(close, 20) > close

        FuncExpr("rsi", [14], Op.LT, 30)
        → RSI(14) < 30
    """

    func_name: str           # 函数名（如 "ma", "rsi"）
    args: List[Any]          # 参数列表（字段名字符串或数字）
    op: Op                   # 比较操作符
    compare_value: float     # 比较阈值
    compare_field: str = ""  # 如果与字段比较（如 MA(close,20) > close）

    def evaluate(self, row: Dict[str, Any]) -> bool:
        # 解析参数（字段名→实际值）
        resolved_args = []
        for arg in self.args:
            if isinstance(arg, str):
                # 可能是字段名
                col = resolve_field(arg)
                val = row.get(col)
                if val is not None:
                    try:
                        resolved_args.append(float(val))
                    except (TypeError, ValueError):
                        resolved_args.append(arg)  # 保持原样传给函数
                else:
                    resolved_args.append(arg)
            else:
                resolved_args.append(float(arg))

        # 调用函数
        func_result = FuncRegistry.call(self.func_name, row, resolved_args)
        if func_result is None:
            return False

        # 确定比较值
        if self.compare_field:
            col = resolve_field(self.compare_field)
            cmp_val = row.get(col)
            if cmp_val is None:
                return False
            try:
                cmp_f = float(cmp_val)
            except (TypeError, ValueError):
                return False
        else:
            cmp_f = self.compare_value

        # 比较
        if self.op == Op.LT:
            return func_result < cmp_f
        elif self.op == Op.LTE:
            return func_result <= cmp_f
        elif self.op == Op.GT:
            return func_result > cmp_f
        elif self.op == Op.GTE:
            return func_result >= cmp_f
        elif self.op == Op.EQ:
            return abs(func_result - cmp_f) < 1e-9
        elif self.op == Op.NEQ:
            return abs(func_result - cmp_f) >= 1e-9
        return False

    def to_sql(self) -> str:
        args_str = ", ".join(str(a) for a in self.args)
        if self.compare_field:
            return f"{self.func_name.upper()}({args_str}) {self.op.value} {self.compare_field}"
        return f"{self.func_name.upper()}({args_str}) {self.op.value} {self.compare_value}"

    def fields(self) -> List[str]:
        fields = []
        for arg in self.args:
            if isinstance(arg, str) and not arg.replace(".", "").isdigit():
                fields.append(resolve_field(arg))
        if self.compare_field:
            fields.append(resolve_field(self.compare_field))
        return fields


# ── 逻辑组合表达式 ──────────────────────────────────────────────────

@dataclass
class AndExpr(RuleExpression):
    """AND 组合: A AND B AND C"""

    operands: List[RuleExpression]

    def __init__(self, *operands: RuleExpression):
        # 扁平化嵌套 AND
        flat: List[RuleExpression] = []
        for op in operands:
            if isinstance(op, AndExpr):
                flat.extend(op.operands)
            else:
                flat.append(op)
        self.operands = flat

    def evaluate(self, row: Dict[str, Any]) -> bool:
        return all(op.evaluate(row) for op in self.operands)

    def to_sql(self) -> str:
        inner = " AND ".join(f"({op.to_sql()})" if isinstance(op, (OrExpr, AndExpr)) else op.to_sql() for op in self.operands)
        return f"({inner})"

    def fields(self) -> List[str]:
        result = []
        for op in self.operands:
            result.extend(op.fields())
        return result


@dataclass
class OrExpr(RuleExpression):
    """OR 组合: A OR B OR C"""

    operands: List[RuleExpression]

    def __init__(self, *operands: RuleExpression):
        flat: List[RuleExpression] = []
        for op in operands:
            if isinstance(op, OrExpr):
                flat.extend(op.operands)
            else:
                flat.append(op)
        self.operands = flat

    def evaluate(self, row: Dict[str, Any]) -> bool:
        return any(op.evaluate(row) for op in self.operands)

    def to_sql(self) -> str:
        inner = " OR ".join(f"({op.to_sql()})" if isinstance(op, (OrExpr, AndExpr)) else op.to_sql() for op in self.operands)
        return f"({inner})"

    def fields(self) -> List[str]:
        result = []
        for op in self.operands:
            result.extend(op.fields())
        return result


@dataclass
class NotExpr(RuleExpression):
    """NOT 取反: NOT A"""

    operand: RuleExpression

    def evaluate(self, row: Dict[str, Any]) -> bool:
        return not self.operand.evaluate(row)

    def to_sql(self) -> str:
        return f"NOT ({self.operand.to_sql()})"

    def fields(self) -> List[str]:
        return self.operand.fields()


# ── SQL 解析器 ──────────────────────────────────────────────────────

class SQLParseError(Exception):
    """SQL 解析错误。"""
    pass


# 匹配函数调用: MA(close, 20) 或 RSI(14)
_RE_FUNC = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*([^)]+)\s*\)",
    re.IGNORECASE,
)

# 匹配原子条件: close < 50, rsi >= 70
_RE_ATOMIC = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_/]*)\s*(<|<=|>|>=|==|=|!=|≠)\s*([\d.]+)\s*$",
    re.IGNORECASE,
)

# 匹配操作符（含中文）— 长的必须放前面，否则 <= 会被 < 抢先匹配
_RE_OP = re.compile(r"(<=|>=|==|!=|≠|<|>|=|低于|高于|跌破|突破|超过|等于|不等于)")


def _tokenize(sql: str) -> List[str]:
    """将 SQL 字符串分词。"""
    # 保护括号内容
    tokens = []
    i = 0
    n = len(sql)
    while i < n:
        # 跳过空白
        while i < n and sql[i].isspace():
            i += 1
        if i >= n:
            break

        # 括号
        if sql[i] == "(":
            # 找到匹配的右括号
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                if sql[j] == "(":
                    depth += 1
                elif sql[j] == ")":
                    depth -= 1
                j += 1
            tokens.append(sql[i:j])
            i = j
            continue

        # AND / OR / NOT 关键字
        if sql[i:i + 3].upper() == "AND" and (i + 3 >= n or not sql[i + 3].isalnum()):
            tokens.append("AND")
            i += 3
            continue
        if sql[i:i + 2].upper() == "OR" and (i + 2 >= n or not sql[i + 2].isalnum()):
            tokens.append("OR")
            i += 2
            continue
        if sql[i:i + 3].upper() == "NOT" and (i + 3 >= n or not sql[i + 3].isalnum()):
            tokens.append("NOT")
            i += 3
            continue

        # 普通 token（到下一个空白或关键字）
        j = i
        while j < n:
            if sql[j].isspace():
                break
            # 检查关键字
            rest = sql[j:].upper()
            if rest.startswith("AND ") or rest.startswith("AND("):
                break
            if rest.startswith("OR ") or rest.startswith("OR("):
                break
            if rest.startswith("NOT ") or rest.startswith("NOT("):
                break
            j += 1
        tokens.append(sql[i:j])
        i = j

    return tokens


def _parse_expr(sql: str) -> RuleExpression:
    """解析类 SQL 表达式字符串为 RuleExpression 树。

    支持的语法:
        close < 50
        close < 50 AND volume > 10000
        close < 50 AND (rsi > 70 OR macd > 0)
        MA(close, 20) > close
        RSI(14) < 30
        NOT (close > 100)
    """
    sql = sql.strip()
    if not sql:
        raise SQLParseError("Empty expression")

    # 处理 NOT
    if sql.upper().startswith("NOT "):
        inner = sql[4:].strip()
        if inner.startswith("(") and inner.endswith(")"):
            inner = inner[1:-1]
        return NotExpr(_parse_expr(inner))

    # 处理括号包裹的整体
    if sql.startswith("(") and sql.endswith(")"):
        # 检查是否是最外层括号
        depth = 0
        for i, ch in enumerate(sql):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if depth == 0 and i < len(sql) - 1:
                break
        else:
            # 整个被括号包裹
            return _parse_expr(sql[1:-1])

    # 按 OR 分割（最低优先级）
    or_parts = _split_by_op(sql, "OR")
    if len(or_parts) > 1:
        return OrExpr(*[_parse_expr(part) for part in or_parts])

    # 按 AND 分割
    and_parts = _split_by_op(sql, "AND")
    if len(and_parts) > 1:
        return AndExpr(*[_parse_expr(part) for part in and_parts])

    # 原子表达式
    return _parse_atomic(sql)


def _split_by_op(sql: str, op: str) -> List[str]:
    """按操作符分割，考虑括号嵌套。"""
    parts = []
    current = []
    depth = 0
    i = 0
    n = len(sql)
    op_len = len(op)

    while i < n:
        ch = sql[i]
        if ch == "(":
            depth += 1
            current.append(ch)
            i += 1
        elif ch == ")":
            depth -= 1
            current.append(ch)
            i += 1
        elif depth == 0:
            # 检查是否匹配操作符
            substr = sql[i:i + op_len].upper()
            if substr == op:
                # 确认是完整单词
                before = i > 0 and sql[i - 1].isalnum()
                after = i + op_len < n and sql[i + op_len].isalnum()
                if not before and not after:
                    parts.append("".join(current).strip())
                    current = []
                    i += op_len
                    continue
            current.append(ch)
            i += 1
        else:
            current.append(ch)
            i += 1

    if current:
        parts.append("".join(current).strip())

    return [p for p in parts if p]


def _parse_atomic(sql: str) -> RuleExpression:
    """解析原子表达式（无 AND/OR/NOT）。

    支持格式:
        close < 50
        MA(close, 20) > close
        close < BOLL_LOWER(20)
        MA(close, 5) > MA(close, 20)
    """
    sql = sql.strip()

    # 找到操作符位置（先定位，再判断左右）
    op_match = _RE_OP.search(sql)
    if not op_match:
        raise SQLParseError(f"No operator found in: {sql}")

    op_str = op_match.group(1)
    op = parse_op(op_str)

    left_str = sql[:op_match.start()].strip()
    right_str = sql[op_match.end():].strip()

    # 左侧是函数？
    left_func = _RE_FUNC.fullmatch(left_str)
    if left_func:
        func_name = left_func.group(1)
        args = _parse_func_args(left_func.group(2))
        # 右侧可能是字段或数字
        right_func = _RE_FUNC.fullmatch(right_str)
        if right_func:
            # MA(close,5) > MA(close,20) — 右侧也是函数
            # 暂不支持函数比较函数，用 compare_field 存 SQL 字符串
            return FuncExpr(func_name, args, op, 0.0, compare_field=right_str)
        try:
            return FuncExpr(func_name, args, op, float(right_str))
        except ValueError:
            return FuncExpr(func_name, args, op, 0.0, compare_field=right_str)

    # 右侧是函数？（如 close < BOLL_LOWER(20)）
    right_func = _RE_FUNC.fullmatch(right_str)
    if right_func:
        # 左侧是字段，右侧是函数
        # 转换为: BOLL_LOWER(20) > close（交换左右并反转操作符）
        func_name = right_func.group(1)
        args = _parse_func_args(right_func.group(2))
        inv_op = _OP_INVERSE.get(op)
        if inv_op is None:
            inv_op = op
        try:
            left_val = float(left_str)
            return FuncExpr(func_name, args, inv_op, left_val)
        except ValueError:
            return FuncExpr(func_name, args, inv_op, 0.0, compare_field=left_str)

    # 普通原子条件: close < 50
    try:
        value = float(right_str)
    except ValueError:
        # 可能是字段比较: close < ma50
        return AtomicExpr(left_str, op, 0.0)  # 字段比较暂用 dummy value

    return AtomicExpr(left_str, op, value)


def _parse_func_args(args_str: str) -> List[Any]:
    """解析函数参数字符串。"""
    args = [a.strip() for a in args_str.split(",")]
    parsed = []
    for a in args:
        try:
            parsed.append(float(a))
        except ValueError:
            parsed.append(a)
    return parsed


# 操作符反转映射（用于交换左右操作数）
_OP_INVERSE = {
    Op.LT: Op.GT,
    Op.LTE: Op.GTE,
    Op.GT: Op.LT,
    Op.GTE: Op.LTE,
    Op.EQ: Op.EQ,
    Op.NEQ: Op.NEQ,
}


# ── 便捷函数 ────────────────────────────────────────────────────────

def parse_sql(sql: str) -> RuleExpression:
    """解析 SQL 字符串为 RuleExpression（入口函数）。"""
    return _parse_expr(sql)


def sql_from_conditions(conditions: List[Tuple[str, str, float]]) -> RuleExpression:
    """从 (field, op_str, value) 列表构建 AND 表达式。"""
    if not conditions:
        raise ValueError("Empty conditions")
    exprs = [AtomicExpr(f, parse_op(o), v) for f, o, v in conditions]
    if len(exprs) == 1:
        return exprs[0]
    return AndExpr(*exprs)
