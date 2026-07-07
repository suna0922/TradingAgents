#!/usr/bin/env python3
"""
backtest_hybrid.py — 混合回测引擎

完全对齐 cli/main.py 执行流程，集成 TradingAgentsGraph (L1 LLM 分析)
+ ExecutionEngine (L2 规则执行) 的季度-日级混合回测。

回测调度:
  ┌─ 每季度第一个交易日 ─→ 运行 fundamentals + market 分析（对齐 CLI）
  │                         生成 structured_rules → 更新决策
  │
  ├─ 每 15 天 stale ────→ 运行 market 分析（对齐 CLI）
  │  或 价格变动 >10%      更新 structured_rules
  │
  └─ 每日 ──────────────→ ExecutionEngine 按规则执行交易

Usage:
  .venv/bin/python backtest_hybrid.py \
      --symbol 000423 \
      --start 2025-01-02 \
      --end 2025-06-30 \
      --price-change-threshold 0.10 \
      --stale-days 15

  仅测试L1分析（不执行每日回测）:
  .venv/bin/python backtest_hybrid.py \
      --symbol 000423 \
      --date 2025-01-15 \
      --l1-only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# ── Path setup ────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROJECT_ROOT)

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.dataflows.config import set_config as set_global_config

from backtest.models import (
    BacktestConfig, PortfolioState, DailyState, WeeklyDecision,
    TradeRecord, PriceCondition, TechnicalTriggers, FundamentalGuards,
    TradeDirection,
)
from backtest.trading_rules import TradingRule, RuleAction
from backtest.execution_engine import ExecutionEngine
from backtest.data_layer import DataLayer

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backtest_hybrid")


# ══════════════════════════════════════════════════════════════════
# 辅助: 结构化规则转换（复用 DecisionEngine._convert_structured_rules 逻辑）
# ══════════════════════════════════════════════════════════════════

def convert_structured_rules(structured_rules: List[Dict]) -> List[TradingRule]:
    """将 PM 的 PortfolioDecision.trading_rules 转换为 TradingRule 对象。

    直接从 trading_rules_structured 字段获取，绕过 markdown 解析。
    trigger_sql 被正确传递而不是被丢弃。
    """
    rules = []
    for r_data in structured_rules:
        try:
            # 优先 trigger_sql（SQL 形式），fallback trigger_condition
            condition = r_data.get("trigger_sql", "") or r_data.get("trigger_condition", "")

            # 解析 action
            action_str = r_data.get("action", "hold")
            try:
                action = RuleAction(action_str.lower())
            except ValueError:
                action_map = {
                    "stop_loss": RuleAction.STOP_LOSS,
                    "take_profit": RuleAction.TAKE_PROFIT,
                    "reduce_position": RuleAction.SELL_PCT,
                    "downgrade": RuleAction.SELL_PCT,
                    "sell_all": RuleAction.SELL_ALL,
                    "sell_pct": RuleAction.SELL_PCT,
                    "buy_add": RuleAction.BUY_ADD,
                    "add_position": RuleAction.BUY_ADD,
                    "alert_only": RuleAction.ALERT_ONLY,
                    "rating_reeval": RuleAction.RATING_REEVAL,
                    "no_left_buy": RuleAction.NO_LEFT_BUY,
                    "circuit_break": RuleAction.CIRCUIT_BREAK,
                    "hold": RuleAction.HOLD,
                }
                action = action_map.get(action_str.lower(), RuleAction.HOLD)

            # 解析 priority
            priority = r_data.get("priority", 50)
            priority_map = {
                "stop_loss": 90, "take_profit": 85,
                "reduce_position": 75, "downgrade": 80,
                "observation_anchor": 60, "entry_zone": 40,
                "alert_only": 60, "rating_reeval": 70,
            }
            rule_type = r_data.get("rule_type", "").lower()
            if priority == 50 and rule_type in priority_map:
                priority = priority_map[rule_type]

            # 解析 pct
            pct = r_data.get("pct", 0.0)
            if pct == 0.0:
                action_detail = r_data.get("action_detail", "")
                if action_detail:
                    import re
                    m = re.search(r"(\d+)%", action_detail)
                    if m:
                        pct = float(m.group(1)) / 100.0

            rule = TradingRule(
                name=f"[{rule_type}] {condition[:40]}",
                action=action,
                condition_str=condition,
                priority=priority,
                pct=pct,
                source_sentence=r_data.get("action_detail", ""),
            )
            rules.append(rule)
            logger.info(f"  ✓ Rule: {rule.name} → action={action.value}, priority={priority}")
        except Exception as e:
            logger.warning(f"  ✗ Failed to convert rule {r_data}: {e}")

    # 按优先级降序排序
    rules.sort(key=lambda r: r.priority, reverse=True)
    return rules


def build_weekly_decision_from_rules(
    trading_rules: List[TradingRule],
    signal_raw: str = "hold",
    pm_raw_output: str = "",
    decision_date: str = "",
) -> WeeklyDecision:
    """从 structured_rules 构建 WeeklyDecision 供 ExecutionEngine 消费。"""
    # Map PM signal to TradeDirection (case-insensitive)
    signal_lower = signal_raw.lower()
    if any(kw in signal_lower for kw in ("buy", "overweight")):
        direction = TradeDirection.BUY
    elif any(kw in signal_lower for kw in ("sell", "underweight")):
        direction = TradeDirection.SELL
    else:
        direction = TradeDirection.HOLD
    return WeeklyDecision(
        direction=direction,
        position_pct=-1,
        price_cond=PriceCondition(),
        technical_triggers=TechnicalTriggers(),
        fundamental_guards=FundamentalGuards(),
        decision_date=decision_date,
        signal_raw=signal_raw,
        pm_rating="",
        pm_raw_output=pm_raw_output,
        parsed_ok=True,
        trading_rules=trading_rules,
        rules_parsed_ok=len(trading_rules) > 0,
    )


def _is_quarter_start(date_str: str, last_quarter_period: Optional[str]) -> Tuple[bool, str]:
    """判断是否为季度第一个月并且与上次季度不同。

    Returns: (is_start, current_period)
    """
    dt = pd.Timestamp(date_str)
    # 季度报告期: Q1=1-3月, Q2=4-6月, Q3=7-9月, Q4=10-12月
    quarter = (dt.month - 1) // 3 + 1
    current_period = f"{dt.year}Q{quarter}"

    if last_quarter_period is None:
        return True, current_period

    if current_period != last_quarter_period:
        return True, current_period

    return False, current_period


# ══════════════════════════════════════════════════════════════════
# L1 分析: 对齐 cli/main.py 的完整执行流程
# ══════════════════════════════════════════════════════════════════

@dataclass
class L1AnalysisResult:
    """一次 L1 分析的结果。"""
    date: str
    analysts: List[str]                    # 使用的 analysts 列表
    signal: str                            # BUY/SELL/HOLD
    trading_rules: List[TradingRule]       # 结构化规则
    pm_raw_output: str                     # PM 原始输出
    fundamentals_report: str               # 基本面报告
    market_report: str                     # 技术面报告
    fa_metrics: Dict[str, Any]             # 展平的基本面指标

    def to_dict(self) -> Dict:
        return {
            "date": self.date,
            "analysts": self.analysts,
            "signal": self.signal,
            "trading_rules": [{"name": r.name, "action": r.action.value, "condition": r.condition_str, "priority": r.priority, "pct": r.pct} for r in self.trading_rules],
            "pm_raw_output": self.pm_raw_output[:500],
            "fa_metrics": self.fa_metrics,
        }


class L1Analyzer:
    """L1 分析器 — 完全对齐 cli/main.py 的 TradingAgentsGraph 执行流程。

    支持两种模式:
      - full: fundamentals + market（季度分析）
      - quick: market only（stale / price-change 刷新）
    """

    def __init__(self, config: Dict[str, Any], output_dir: Path):
        self.config = config
        self.output_dir = output_dir
        # L1 分析缓存: {date_str: L1AnalysisResult}
        self._cache: Dict[str, L1AnalysisResult] = {}

    def run_full_analysis(self, symbol: str, date_str: str) -> L1AnalysisResult:
        """运行完整分析（fundamentals + market）—— 每季度一次。

        对齐 cli/main.py:
          - TradingAgentsGraph(selected_analysts=["fundamentals", "market"], config=..., debug=False)
          - graph.propagate(symbol, date_str)
          - 提取 trading_rules_structured → 转换

        缓存: 按 {symbol}_{quarter} 缓存季度全分析结果，避免重复跑 LLM。
        """
        dt = pd.Timestamp(date_str)
        quarter = (dt.month - 1) // 3 + 1
        cache_key = f"q_{symbol}_{dt.year}Q{quarter}"

        # 检查缓存
        cached = self._load_cache(cache_key)
        if cached is not None:
            logger.info(f"[L1-CACHE-HIT] {symbol} {dt.year}Q{quarter} (from cache)")
            return cached

        logger.info(f"\n{'='*60}")
        logger.info(f"[L1-FULL] {symbol} @ {date_str} | analysts=[fundamentals, market]")
        logger.info(f"{'='*60}")

        result = self._run_analysis(symbol, date_str, ["fundamentals", "market"], deep_model=True)
        self._save_cache(cache_key, result)
        return result

    def run_quick_analysis(self, symbol: str, date_str: str) -> L1AnalysisResult:
        """运行快速分析（market only）—— stale 或 price-change 触发。

        对齐 cli/main.py:
          - TradingAgentsGraph(selected_analysts=["market"], config=..., debug=False)
          - graph.propagate(symbol, date_str)
        """
        logger.info(f"[L1-QUICK] {symbol} @ {date_str} | analysts=[market]")
        return self._run_analysis(symbol, date_str, ["market"], deep_model=False)

    def _run_analysis(
        self,
        symbol: str,
        date_str: str,
        analysts: List[str],
        deep_model: bool,
    ) -> L1AnalysisResult:
        """核心分析执行 — 完全对齐 TradingAgentsGraph.propagate() 流程。"""
        # 构建 config（对齐 CLI: 用 deep/quick 区分模型）
        analysis_config = self.config.copy()
        if not deep_model:
            # quick 模式: 市场分析用 flash 模型
            analysis_config["deep_think_llm"] = analysis_config["quick_think_llm"]

        # 同步全局 config（数据层需要）
        set_global_config(analysis_config)

        # 创建 Graph（对齐 CLI 的 TradingAgentsGraph 初始化）
        graph = TradingAgentsGraph(
            selected_analysts=analysts,
            debug=False,
            config=analysis_config,
        )

        # 执行 propagate() — 完全等同于 CLI 的 graph.propagate()
        try:
            state, signal = graph.propagate(symbol, date_str)
        except Exception as e:
            logger.error(f"[L1] propagate failed for {symbol} @ {date_str}: {e}")
            raise

        # 提取结构化规则
        structured_rules = state.get("trading_rules_structured", [])
        trading_rules = convert_structured_rules(structured_rules) if structured_rules else []

        logger.info(f"[L1] Signal: {signal} | Rules: {len(trading_rules)}")
        for rule in trading_rules:
            logger.info(f"  - {rule.name} ({rule.action.value}, priority={rule.priority})")

        # 提取基本面指标（季度分析时使用）
        fa_metrics = state.get("fundamentals_structured", {})
        # 确保 fa_metrics 是展平字典
        if isinstance(fa_metrics, dict):
            fa_flat = _flatten_fa_metrics(fa_metrics)
        else:
            fa_flat = {}

        result = L1AnalysisResult(
            date=date_str,
            analysts=analysts,
            signal=signal,
            trading_rules=trading_rules,
            pm_raw_output=state.get("final_trade_decision", ""),
            fundamentals_report=state.get("fundamentals_report", ""),
            market_report=state.get("market_report", ""),
            fa_metrics=fa_flat,
        )

        # 缓存并保存
        self._cache[date_str] = result
        self._save_analysis_result(symbol, result)

        return result

    def get_cached(self, date_str: str) -> Optional[L1AnalysisResult]:
        """获取缓存的 L1 分析结果。"""
        return self._cache.get(date_str)

    def _save_analysis_result(self, symbol: str, result: L1AnalysisResult):
        """保存 L1 分析结果到文件。"""
        safe_date = result.date.replace("-", "")
        out_dir = self.output_dir / symbol / "l1_analysis"
        out_dir.mkdir(parents=True, exist_ok=True)

        analysis_type = "full" if "fundamentals" in result.analysts else "quick"
        filename = f"{safe_date}_{analysis_type}.json"
        filepath = out_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"[L1] Saved to {filepath}")

    def _cache_dir(self) -> Path:
        """L1 分析缓存目录。"""
        d = self.output_dir / "l1_cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_cache(self, cache_key: str, result: L1AnalysisResult) -> None:
        """保存 L1 分析结果到缓存文件（用于跨回测复用）。"""
        cache_file = self._cache_dir() / f"{cache_key}.json"
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"[L1-CACHE-SAVE] {cache_key} → {cache_file}")

    def _load_cache(self, cache_key: str) -> Optional[L1AnalysisResult]:
        """从缓存文件加载 L1 分析结果。"""
        cache_file = self._cache_dir() / f"{cache_key}.json"
        if not cache_file.exists():
            return None
        try:
            with open(cache_file) as f:
                data = json.load(f)
            rules = convert_structured_rules(data.get("trading_rules", [])) if data.get("trading_rules_json") else [
                TradingRule(name=r["name"], action=RuleAction(r["action"]), condition_str=r["condition"],
                           priority=r["priority"], pct=r["pct"])
                for r in data.get("trading_rules", [])
            ]
            # 修复: 重新序列化规则列表
            if not rules and data.get("trading_rules"):
                rules = [
                    TradingRule(name=r["name"], action=RuleAction(r["action"]), condition_str=r["condition"],
                               priority=r["priority"], pct=r["pct"])
                    for r in data.get("trading_rules", [])
                ]
            return L1AnalysisResult(
                date=data["date"], analysts=data.get("analysts", []), signal=data.get("signal", "?"),
                trading_rules=rules, pm_raw_output=data.get("pm_raw_output", ""),
                fundamentals_report=data.get("fundamentals_report", ""),
                market_report=data.get("market_report", ""),
                fa_metrics=data.get("fa_metrics", {}),
            )
        except Exception as e:
            logger.warning(f"[L1-CACHE] Failed to load {cache_key}: {e}")
            return None


def _flatten_fa_metrics(metrics: Dict, prefix: str = "") -> Dict[str, float]:
    """递归展平嵌套的基本面指标字典。"""
    flat = {}
    for k, v in metrics.items():
        full_key = f"{prefix}_{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(_flatten_fa_metrics(v, prefix=full_key))
        elif isinstance(v, (int, float)):
            flat[full_key] = float(v)
    return flat


# ══════════════════════════════════════════════════════════════════
# 混合回测引擎主循环
# ══════════════════════════════════════════════════════════════════

@dataclass
class HybridBacktestResult:
    """混合回测完整结果。"""
    config: Dict[str, Any]
    summary: Dict[str, Any]
    daily_states: List[Dict]
    trade_history: List[Dict]
    l1_analyses: List[Dict]          # 所有 L1 分析点
    decisions: List[Dict]             # 所有决策点（含规则触发）

    def to_dict(self) -> Dict:
        return {
            "config": self.config,
            "summary": self.summary,
            "daily_states": self.daily_states,
            "trade_history": self.trade_history,
            "l1_analyses": self.l1_analyses,
            "decisions": self.decisions,
        }


class HybridBacktestEngine:
    """混合回测引擎 — 季度 LLM 分析 + 每日规则执行。"""

    def __init__(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        initial_cash: float = 1_000_000.0,
        price_change_threshold: float = 0.10,
        stale_days: int = 15,
        output_dir: str = "backtest_results/hybrid",
    ):
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.price_change_threshold = price_change_threshold
        self.stale_days = stale_days
        self.output_dir = Path(output_dir)

        # ── 配置（对齐 CLI） ──
        self.config = DEFAULT_CONFIG.copy()
        self.config["llm_provider"] = "deepseek"
        self.config["deep_think_llm"] = "deepseek-reasoner"
        self.config["quick_think_llm"] = "deepseek-chat"
        self.config["backend_url"] = os.environ.get(
            "DEEPSEEK_BACKEND_URL", "https://api.deepseek.com"
        )
        self.config["max_debate_rounds"] = 1
        self.config["max_risk_discuss_rounds"] = 1
        self.config["results_dir"] = str(self.output_dir / "graph_results")
        self.config["data_cache_dir"] = str(self.output_dir / "graph_cache")

        # 同步全局 config
        set_global_config(self.config)

        # ── BacktestConfig（供 ExecutionEngine 使用） ──
        self.bt_config = BacktestConfig(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            llm_provider=self.config["llm_provider"],
            deep_think_llm=self.config["deep_think_llm"],
            quick_think_llm=self.config["quick_think_llm"],
            price_change_threshold=price_change_threshold,
            decision_stale_days=stale_days,
            output_dir=str(self.output_dir),
        )

        # ── 组件初始化 ──
        self.data_layer = DataLayer(symbol, start_date, end_date)
        self.l1_analyzer = L1Analyzer(self.config, self.output_dir)
        self.execution_engine = ExecutionEngine(self.bt_config, self.data_layer)

        # ── 回测状态 ──
        self.portfolio = PortfolioState(cash=initial_cash)
        self.active_decision: Optional[WeeklyDecision] = None
        self.last_decision_price: float = 0.0
        self.last_decision_idx: int = -stale_days - 10
        self.last_quarter_period: Optional[str] = None
        self._last_report_pub_date: Optional[str] = None  # 最新季报发布日
        self._report_dates: Dict[str, str] = {}  # 预加载的报告日期 {pubDate: statDate}
        self.fa_metrics: Dict[str, Any] = {}
        self.force_decision_next_day: bool = False

        # ── 记录 ──
        self.l1_analyses_log: List[Dict] = []
        self.daily_states_log: List[Dict] = []
        self.decisions_log: List[Dict] = []

    def run(self) -> HybridBacktestResult:
        """执行混合回测。"""
        logger.info(f"\n{'#'*60}")
        logger.info(f"# Hybrid Backtest: {self.symbol} | {self.start_date} → {self.end_date}")
        logger.info(f"# Initial Cash: ¥{self.bt_config.initial_cash:,.0f}")
        logger.info(f"# Price Change Threshold: {self.price_change_threshold*100}%")
        logger.info(f"# Stale Days: {self.stale_days}")
        logger.info(f"{'#'*60}\n")

        # Phase 1: 加载数据
        logger.info("[Phase 1] Loading data...")
        ohlcv = self.data_layer.fetch_ohlcv()
        df_indicators = self.data_layer.compute_indicators()
        # 预加载报告日期（避免每日 baostock 查询超时）
        self._report_dates = self.data_layer.preload_report_dates()
        logger.info(f"  Preloaded {len(self._report_dates)} report dates for {self.symbol}")
        # 将 date 设为普通列
        df = df_indicators.reset_index()
        df = df.rename(columns={"index": "date"}) if "date" not in df.columns else df

        if df.empty:
            logger.error("No data loaded. Aborting.")
            return HybridBacktestResult(
                config={}, summary={"error": "no_data"},
                daily_states=[], trade_history=[], l1_analyses=[], decisions=[],
            )

        logger.info(f"  Loaded {len(df)} trading days")

        # Phase 2: 主循环
        logger.info("[Phase 2] Running hybrid loop...")
        total_days = len(df)

        for idx, row in df.iterrows():
            # 处理行索引（iterrows 给出 int 索引）
            if isinstance(idx, int):
                actual_idx = idx
            else:
                actual_idx = df.index.get_loc(idx)

            date_str = str(row.get("date", row.name))
            if isinstance(row.get("date"), pd.Timestamp):
                date_str = row["date"].strftime("%Y-%m-%d")

            close = float(row["close"])

            # ── 季度检测 → L1 完整分析 ──
            is_q_start, current_period = _is_quarter_start(date_str, self.last_quarter_period)
            if is_q_start:
                logger.info(f"\n{'─'*40}")
                logger.info(f"[SCHEDULE] Quarter start: {current_period} @ {date_str}")
                logger.info(f"{'─'*40}")
                try:
                    result = self.l1_analyzer.run_full_analysis(self.symbol, date_str)
                    self.last_quarter_period = current_period

                    # 更新基本面指标
                    self.fa_metrics = result.fa_metrics

                    # 构建 WeeklyDecision → 更新 active_decision
                    new_decision = build_weekly_decision_from_rules(
                        result.trading_rules,
                        signal_raw=result.signal,
                        pm_raw_output=result.pm_raw_output,
                        decision_date=date_str,
                    )
                    self.active_decision = new_decision
                    self.portfolio.active_decision = new_decision
                    self.portfolio.last_decision_executed_date = ""
                    self.last_decision_price = close
                    self.last_decision_idx = actual_idx
                    self.force_decision_next_day = False

                    self.l1_analyses_log.append(result.to_dict())
                    self.decisions_log.append({
                        "date": date_str,
                        "type": "quarterly_full",
                        "trigger": f"quarter_start={current_period}",
                        "signal": result.signal,
                        "rules_count": len(result.trading_rules),
                        "rules": [r.name for r in result.trading_rules],
                    })
                    logger.info(f"[L1-QUARTERLY] New decision: {result.signal}, "
                                f"{len(result.trading_rules)} rules")
                except Exception as e:
                    logger.error(f"[L1-QUARTERLY] Failed: {e}")

            # ── L1 触发检测（价格变动 / stale / alert） ──
            should_trigger = self._should_trigger_l1(actual_idx, close, date_str)

            if should_trigger and not is_q_start:
                # 在 reset 之前捕获触发原因
                trigger_reason = self._trigger_reason(actual_idx, close)
                logger.info(f"[SCHEDULE] L1 refresh triggered @ {date_str} (reason={trigger_reason})")
                try:
                    result = self.l1_analyzer.run_quick_analysis(self.symbol, date_str)

                    # 构建 WeeklyDecision
                    new_decision = build_weekly_decision_from_rules(
                        result.trading_rules,
                        signal_raw=result.signal,
                        pm_raw_output=result.pm_raw_output,
                        decision_date=date_str,
                    )
                    self.active_decision = new_decision
                    self.portfolio.active_decision = new_decision
                    self.portfolio.last_decision_executed_date = ""
                    self.last_decision_price = close
                    self.last_decision_idx = actual_idx
                    self.force_decision_next_day = False

                    self.l1_analyses_log.append(result.to_dict())
                    self.decisions_log.append({
                        "date": date_str,
                        "type": "refresh",
                        "trigger": trigger_reason,
                        "signal": result.signal,
                        "rules_count": len(result.trading_rules),
                        "rules": [r.name for r in result.trading_rules],
                    })
                    logger.info(f"[L1-REFRESH] New decision: {result.signal}, "
                                f"{len(result.trading_rules)} rules")
                except Exception as e:
                    logger.error(f"[L1-REFRESH] Failed: {e}")

            # ── L2: 每日规则执行 ──
            try:
                daily_state = self._run_daily_execution(row, df, actual_idx, date_str)
                # ★ 注入技术指标值（供 Excel 展示和回查）
                daily_state["_ma20"] = round(float(row.get("close_20_sma", 0)), 2)
                daily_state["_ma50"] = round(float(row.get("close_50_sma", 0)), 2)
                daily_state["_ma200"] = round(float(row.get("close_200_sma", 0)), 2)
                daily_state["_macd"] = round(float(row.get("macd", 0)), 4)
                daily_state["_rsi14"] = round(float(row.get("rsi_14", row.get("rsi", 0))), 2)
                daily_state["_boll_upper"] = round(float(row.get("boll_ub", 0)), 2)
                daily_state["_boll_lower"] = round(float(row.get("boll_lb", 0)), 2)
                daily_state["_vol_ma20"] = round(float(row.get("volume", 0) / (row.get("volume_ma20", 1) or 1)), 2)
                self.daily_states_log.append(daily_state)

                # 只有 rating_reeval 触发时才强制次日复评
                # alert_only 仅预警，不触发 L1 重评估
                triggered_rules = daily_state.get("triggered_rules", [])
                has_reeval = any("rating_reeval" in r for r in triggered_rules)
                has_alert = any("alert_only" in r or "observation_anchor" in r for r in triggered_rules)

                if has_reeval:
                    self.force_decision_next_day = True
                    logger.info(f"[REVAL] rating_reeval triggered @ {date_str}, forcing re-eval next day")
                elif has_alert:
                    logger.info(f"[ALERT] alert_only triggered @ {date_str} (observation only, no force re-eval)")
            except Exception as e:
                logger.error(f"[L2-EXEC] Daily execution failed @ {date_str}: {e}")
                self.daily_states_log.append({
                    "date": date_str, "close": close, "cash": self.portfolio.cash,
                    "shares": self.portfolio.shares, "position_value": 0,
                    "total_value": self.portfolio.cash,
                    "position_pct": 0, "action": "ERROR",
                    "action_price": 0, "action_shares": 0,
                    "triggered_rules": [], "alert_triggered": False,
                })

            # 进度
            if (actual_idx + 1) % 30 == 0 or actual_idx == total_days - 1:
                total_value = (self.portfolio.cash +
                               self.portfolio.shares * close)
                pnl = total_value - self.bt_config.initial_cash
                pnl_pct = pnl / self.bt_config.initial_cash * 100
                logger.info(f"[PROGRESS] {date_str} | Day {actual_idx+1}/{total_days} | "
                            f"Total: ¥{total_value:,.0f} | PnL: {pnl_pct:+.2f}% | "
                            f"Pos: {self.portfolio.shares} shares")

        # Phase 3: 计算汇总
        logger.info("\n[Phase 3] Computing summary...")
        summary = self._compute_summary(df)
        self.daily_states_log = summary.pop("_daily_states_raw", self.daily_states_log)

        # 记录交易历史
        trade_history = []
        for t in self.portfolio.trade_history:
            trade_history.append({
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "shares": t.shares,
                "direction": t.direction,
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
                "exit_reason": t.exit_reason,
            })

        return HybridBacktestResult(
            config={
                "symbol": self.symbol,
                "start_date": self.start_date,
                "end_date": self.end_date,
                "initial_cash": self.bt_config.initial_cash,
                "price_change_threshold": self.price_change_threshold,
                "stale_days": self.stale_days,
            },
            summary=summary,
            daily_states=self.daily_states_log,
            trade_history=trade_history,
            l1_analyses=self.l1_analyses_log,
            decisions=self.decisions_log,
        )

    def _should_trigger_l1(self, idx: int, close: float, date_str: str) -> bool:
        """判断是否需要触发 L1 分析刷新（价格变动 / stale / 首日 / alert / 新季报）。"""
        # 首日
        if idx == 0:
            return True

        # Alert 触发次日强制
        if self.force_decision_next_day:
            return True

        # 新季报发布 → 立即重评
        if self._new_quarterly_report_available(date_str):
            return True

        # 价格变动超过阈值
        if self.last_decision_price > 0:
            price_change = abs(close - self.last_decision_price) / self.last_decision_price
            if price_change >= self.price_change_threshold:
                return True

        # 决策过期
        if idx - self.last_decision_idx >= self.stale_days:
            return True

        return False

    def _new_quarterly_report_available(self, date_str: str) -> bool:
        """检测是否有新季报发布。

        使用预加载的报告日期 dict，O(1) 查找。比较所有 pubDate <= date_str 的
        最大值是否与上次不同。
        """
        if not self._report_dates:
            return False
        try:
            # 找到 <= date_str 的最新 pubDate
            latest = None
            for pub in sorted(self._report_dates.keys(), reverse=True):
                if pub <= date_str:
                    latest = pub
                    break
            if latest and latest != self._last_report_pub_date:
                self._last_report_pub_date = latest
                logger.info(f"[SCHEDULE] New report published: {latest}")
                return True
        except Exception as e:
            logger.debug(f"[SCHEDULE] Report check failed: {e}")
        return False

    def _trigger_reason(self, idx: int, close: float) -> str:
        """返回 L1 触发原因。"""
        if idx == 0:
            return "first_day"
        if self.force_decision_next_day:
            return "alert_triggered"
        # 新季报优先于价格变动判断
        if self._last_report_pub_date and idx - self.last_decision_idx < self.stale_days:
            return f"new_report={self._last_report_pub_date}"
        if self.last_decision_price > 0:
            pc = abs(close - self.last_decision_price) / self.last_decision_price
            if pc >= self.price_change_threshold:
                return f"price_change={pc*100:.1f}%"
        if idx - self.last_decision_idx >= self.stale_days:
            return f"stale={idx - self.last_decision_idx}d"
        return "unknown"

    def _run_daily_execution(
        self, row: pd.Series, df: pd.DataFrame, idx: int, date_str: str
    ) -> Dict:
        """每日 L2 规则执行 — 通过 ExecutionEngine.execute()。"""
        # 更新 portfolio 日期
        self.portfolio.current_date = date_str

        # 调用 ExecutionEngine
        daily_state = self.execution_engine.execute(
            portfolio=self.portfolio,
            decision=self.active_decision,
            row=row,
            idx=idx,
            df=df,
            fa_metrics=self.fa_metrics,
        )

        return {
            "date": date_str,
            "close": float(row["close"]),
            "cash": self.portfolio.cash,
            "shares": self.portfolio.shares,
            "position_value": self.portfolio.shares * float(row["close"]),
            "total_value": self.portfolio.cash + self.portfolio.shares * float(row["close"]),
            "position_pct": (self.portfolio.shares * float(row["close"]) /
                             (self.portfolio.cash + self.portfolio.shares * float(row["close"]))
                             if (self.portfolio.cash + self.portfolio.shares * float(row["close"])) > 0 else 0),
            "action": daily_state.action,
            "action_price": daily_state.action_price,
            "action_shares": daily_state.action_shares,
            "triggered_rules": daily_state.triggered_rules,
            "alert_triggered": daily_state.alert_triggered,
        }

    def _compute_summary(self, df: pd.DataFrame) -> Dict:
        """计算回测汇总统计。"""
        states = self.daily_states_log
        if not states:
            return {"error": "no_daily_states"}

        # 起始/结束总资产
        start_value = self.bt_config.initial_cash
        end_value = states[-1]["total_value"]
        total_return = (end_value - start_value) / start_value
        total_return_pct = total_return * 100

        # 年化收益
        start_dt = pd.Timestamp(self.start_date)
        end_dt = pd.Timestamp(self.end_date)
        years = (end_dt - start_dt).days / 365.25
        annual_return = ((1 + total_return) ** (1 / max(years, 0.01)) - 1) * 100

        # 最大回撤
        peak = start_value
        max_drawdown = 0.0
        for s in states:
            tv = s["total_value"]
            if tv > peak:
                peak = tv
            dd = (peak - tv) / peak if peak > 0 else 0
            if dd > max_drawdown:
                max_drawdown = dd
        max_drawdown_pct = max_drawdown * 100

        # 交易统计
        trades = [t for t in self.portfolio.trade_history if t.direction == "SELL"]
        win_trades = [t for t in trades if t.pnl > 0]
        total_pnl = sum(t.pnl for t in trades)
        win_rate = len(win_trades) / len(trades) * 100 if trades else 0

        # 基准收益（买入持有）
        first_close = float(df.iloc[0]["close"])
        last_close = float(df.iloc[-1]["close"])
        benchmark_return = (last_close - first_close) / first_close * 100

        # 夏普比率（简化版）
        daily_returns = []
        for i in range(1, len(states)):
            r = (states[i]["total_value"] - states[i-1]["total_value"]) / states[i-1]["total_value"]
            daily_returns.append(r)
        if daily_returns:
            mean_ret = sum(daily_returns) / len(daily_returns)
            std_ret = (sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)) ** 0.5
            sharpe = (mean_ret / std_ret * (252 ** 0.5)) if std_ret > 0 else 0
        else:
            sharpe = 0

        # L1 分析统计
        l1_count = len(self.l1_analyses_log)
        l1_full_count = sum(1 for a in self.l1_analyses_log if "fundamentals" in a.get("analysts", []))
        l1_quick_count = l1_count - l1_full_count

        summary = {
            "symbol": self.symbol,
            "period": f"{self.start_date} → {self.end_date}",
            "initial_cash": start_value,
            "final_value": end_value,
            "total_return_pct": round(total_return_pct, 2),
            "annual_return_pct": round(annual_return, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "sharpe_ratio": round(sharpe, 2),
            "benchmark_return_pct": round(benchmark_return, 2),
            "total_trades": len(trades),
            "win_trades": len(win_trades),
            "win_rate_pct": round(win_rate, 2),
            "total_pnl": round(total_pnl, 2),
            "l1_analyses_total": l1_count,
            "l1_analyses_full": l1_full_count,
            "l1_analyses_quick": l1_quick_count,
            "trading_days": len(states),
        }

        # 保留原始 daily states 供输出
        summary["_daily_states_raw"] = states

        return summary


# ══════════════════════════════════════════════════════════════════
# L1-Only 模式: 仅运行一次分析（不执行每日回测）
# ══════════════════════════════════════════════════════════════════

def run_l1_only(symbol: str, date_str: str, output_dir: str = "backtest_results/hybrid"):
    """仅运行 L1 分析，输出结构化规则供人工检查。"""
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "deepseek"
    config["deep_think_llm"] = "deepseek-reasoner"
    config["quick_think_llm"] = "deepseek-chat"
    config["backend_url"] = os.environ.get("DEEPSEEK_BACKEND_URL", "https://api.deepseek.com")
    config["results_dir"] = str(Path(output_dir) / "graph_results")
    config["data_cache_dir"] = str(Path(output_dir) / "graph_cache")

    set_global_config(config)

    analyzer = L1Analyzer(config, Path(output_dir))
    result = analyzer.run_full_analysis(symbol, date_str)

    print(f"\n{'='*60}")
    print(f"L1 Analysis Result: {symbol} @ {date_str}")
    print(f"{'='*60}")
    print(f"Signal: {result.signal}")
    print(f"Rules: {len(result.trading_rules)}")
    for i, rule in enumerate(result.trading_rules, 1):
        print(f"\n  Rule {i}: {rule.name}")
        print(f"    action:      {rule.action.value}")
        print(f"    condition:   {rule.condition_str}")
        print(f"    priority:    {rule.priority}")
        print(f"    pct:         {rule.pct}")
        print(f"    source:      {rule.source_sentence[:100]}")

    # Save to file
    out_dir = Path(output_dir) / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"l1_result_{date_str.replace('-', '')}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)
    print(f"\nSaved to: {out_file}\n")

    return result


# ══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Hybrid Backtest Engine — Quarterly LLM Analysis + Daily Rule Execution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 完整回测
  .venv/bin/python backtest_hybrid.py --symbol 000423 --start 2025-01-02 --end 2025-06-30

  # 仅 L1 分析（不执行每日回测）
  .venv/bin/python backtest_hybrid.py --symbol 000423 --date 2025-01-15 --l1-only
        """,
    )

    # 回测模式参数
    parser.add_argument("--symbol", required=True, help="股票代码 (e.g. 000423)")
    parser.add_argument("--start", help="回测开始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="回测结束日期 YYYY-MM-DD")
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0,
                        help="初始资金 (default: 1,000,000)")
    parser.add_argument("--price-change-threshold", type=float, default=0.10,
                        help="价格变动阈值触发 L1 刷新 (default: 0.10 = 10%%)")
    parser.add_argument("--stale-days", type=int, default=15,
                        help="决策过期天数触发 L1 刷新 (default: 15)")
    parser.add_argument("--output-dir", default="backtest_results/hybrid",
                        help="输出目录 (default: backtest_results/hybrid)")

    # L1-only 模式
    parser.add_argument("--date", help="仅跑 L1 分析的日期 YYYY-MM-DD")
    parser.add_argument("--l1-only", action="store_true",
                        help="仅运行 L1 分析，不执行每日回测")

    args = parser.parse_args()

    # ── L1-Only 模式 ──
    if args.l1_only:
        if not args.date:
            parser.error("--l1-only 模式必须指定 --date")
        run_l1_only(args.symbol, args.date, args.output_dir)
        return

    # ── 完整回测模式 ──
    if not args.start or not args.end:
        parser.error("回测模式必须指定 --start 和 --end")

    engine = HybridBacktestEngine(
        symbol=args.symbol,
        start_date=args.start,
        end_date=args.end,
        initial_cash=args.initial_cash,
        price_change_threshold=args.price_change_threshold,
        stale_days=args.stale_days,
        output_dir=args.output_dir,
    )

    result = engine.run()

    # ── 输出 ──
    out_dir = Path(args.output_dir) / args.symbol
    out_dir.mkdir(parents=True, exist_ok=True)

    # 保存完整结果
    result_file = out_dir / f"result_{args.start.replace('-','')}_{args.end.replace('-','')}.json"
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2, ensure_ascii=False, default=str)

    # 保存汇总
    summary_file = out_dir / "summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(result.summary, f, indent=2, ensure_ascii=False)

    # 打印汇总
    print(f"\n{'='*60}")
    print(f"  Hybrid Backtest Complete: {args.symbol}")
    print(f"{'='*60}")
    s = result.summary
    print(f"  Period:         {s.get('period', 'N/A')}")
    print(f"  Initial Cash:   ¥{s['initial_cash']:,.0f}")
    print(f"  Final Value:    ¥{s['final_value']:,.0f}")
    print(f"  Total Return:   {s['total_return_pct']:+.2f}%")
    print(f"  Annual Return:  {s['annual_return_pct']:+.2f}%")
    print(f"  Max Drawdown:   {s['max_drawdown_pct']:.2f}%")
    print(f"  Sharpe Ratio:   {s['sharpe_ratio']:.2f}")
    print(f"  Benchmark:      {s['benchmark_return_pct']:+.2f}%")
    print(f"  Trades:         {s['total_trades']} ({s['win_trades']} wins, {s['win_rate_pct']:.1f}%)")
    print(f"  Total PnL:      ¥{s['total_pnl']:+,.2f}")
    print(f"  L1 Analyses:    {s['l1_analyses_total']} (full={s['l1_analyses_full']}, quick={s['l1_analyses_quick']})")
    print(f"  Trading Days:   {s['trading_days']}")
    print(f"\n  Results saved to: {out_dir}")
    print(f"  Full result:      {result_file}")
    print(f"  Summary:          {summary_file}")
    print(f"{'='*60}\n")

    return result


if __name__ == "__main__":
    main()
