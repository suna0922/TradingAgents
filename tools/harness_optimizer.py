#!/usr/bin/env python3
"""
HarnessOptimizer — 基于 P&L 的 Harness 参数调优引擎（支持多股票交叉验证）。

5 层 Harness 架构：
  L1 Base Model      — LLM (DeepSeek)
  L2 System Prompt   — Agent prompt 变体
  L3 Tool Orchestration — 数据源 / 辩论轮数
  L4 Validator       — 容忍度 / 数值检查
  L5 Loop Controller — 参数搜索、回测循环、评分排序  ← 本模块实现

防止过拟合策略：
  - 多股票交叉验证：同一个参数在 N 只股票上回测，取 ensemble 评分
  - 股票分组：--train-symbols 调优 / --test-symbols 验证泛化
  - Ensemble: mean（默认）/ median（鲁棒）/ min（保守）

用法：
  # 单股票（向后兼容）
  .venv/bin/python tools/harness_optimizer.py \\
      --symbol 000423 --start 2024-01-01 --end 2025-12-31

  # 多股票训练 + 留出验证
  .venv/bin/python tools/harness_optimizer.py \\
      --train-symbols 000423,600519,000858,601318,000333 \\
      --test-symbols 600036,000651 \\
      --start 2024-01-01 --end 2025-12-31 \\
      --max-trials 30 --workers 4
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

# ── Path setup ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("harness_optimizer")


# ══════════════════════════════════════════════════════════════════
# 参数空间
# ══════════════════════════════════════════════════════════════════

PARAM_SPACE_DEFAULT: Dict[str, List[Any]] = {
    # ── L5 策略参数（最直接影响 P&L） ──
    "default_stop_loss_pct": [0.05, 0.08, 0.10, 0.12, 0.15],
    "default_take_profit_pct": [0.15, 0.20, 0.25, 0.30, 0.35],

    # ── L3 辩论轮数 ──
    "max_debate_rounds": [1, 2],
    "max_risk_discuss_rounds": [1, 2],

    # ── L3 触发频率 ──
    "price_change_threshold": [0.05, 0.08, 0.10, 0.15],
    "stale_days": [10, 15, 20],

    # ── L1 模型温度 ──
    "pm_temperature": [0.0, 0.1, 0.3],

    # ── L4 数值容忍度 ──
    "numeric_tolerance_pct": [0.01, 0.02, 0.05],

    # ── L2 Prompt 变体（大师选择） ──
    # 离散值: 各 Agent 角色对应的大师 ID 列表
    # 由 build_master_param_space() 在初始化时动态填充
}

# 默认的大师参数选项（子集，避免参数爆炸）
# 完整列表见 tools/prompt_variants.py
MASTER_PARAM_DEFAULTS: Dict[str, List[str]] = {
    "master_bull":   ["default", "buffett", "lynch", "fisher", "duan_yongping", "simons"],
    "master_bear":   ["default", "klarman", "marks", "taleb", "soros", "graham"],
    "master_pm":     ["default", "buffett", "graham", "munger", "dalio", "marks", "lynch"],
    "master_trader": ["default", "livermore", "druckenmiller", "ptj", "raschke"],
}

# 推荐的 A 股多股票池（覆盖不同行业/市值）
RECOMMENDED_SYMBOLS: List[str] = [
    "600519",  # 贵州茅台 — 大市值消费
    "000858",  # 五粮液   — 消费
    "000333",  # 美的集团 — 家电
    "601318",  # 中国平安 — 金融
    "600036",  # 招商银行 — 银行
    "000651",  # 格力电器 — 家电
    "000423",  # 东阿阿胶 — 中药
    "600276",  # 恒瑞医药 — 医药
]


# ══════════════════════════════════════════════════════════════════
# 试验结果
# ══════════════════════════════════════════════════════════════════

@dataclass
class StockResult:
    """单只股票的回测结果。"""
    symbol: str
    status: str = "ok"
    error_msg: str = ""
    score: float = -999.0
    total_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    num_trades: int = 0
    profit_factor: float = 0.0
    elapsed_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TrialResult:
    """一次试验的完整结果（多股票聚合）。"""
    trial_id: int
    params: Dict[str, Any]
    status: str = "ok"
    error_msg: str = ""
    # 聚合评分
    score: float = -999.0        # ensemble score
    score_method: str = "mean"    # 聚合方式
    score_min: float = -999.0     # 最差股票得分（用于鲁棒性检查）
    score_std: float = 0.0        # 各股票得分标准差（越小越稳定）
    # 聚合指标（对各股票取 mean）
    total_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    num_trades: int = 0
    profit_factor: float = 0.0
    # 明细
    per_stock: List[StockResult] = field(default_factory=list)
    elapsed_s: float = 0.0
    output_dir: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["per_stock"] = [s.to_dict() for s in self.per_stock]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TrialResult":
        stocks = [StockResult(**s) for s in d.pop("per_stock", [])]
        return cls(per_stock=stocks, **d)


# ══════════════════════════════════════════════════════════════════
# 单股票回测（在子进程内调用）
# ══════════════════════════════════════════════════════════════════

def _run_one_backtest(
    symbol: str,
    params: Dict[str, Any],
    start_date: str,
    end_date: str,
    initial_cash: float,
    output_dir: str,
) -> StockResult:
    """对单只股票执行一次回测，返回 StockResult。"""
    t0 = time.time()

    try:
        import backtest.decision_engine as de

        orig_stop = de.DecisionEngine.DEFAULT_STOP_LOSS_PCT
        orig_take = de.DecisionEngine.DEFAULT_TAKE_PROFIT_PCT
        de.DecisionEngine.DEFAULT_STOP_LOSS_PCT = params.get("default_stop_loss_pct", 0.08)
        de.DecisionEngine.DEFAULT_TAKE_PROFIT_PCT = params.get("default_take_profit_pct", 0.20)

        from backtest_hybrid import HybridBacktestEngine

        engine = HybridBacktestEngine(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            price_change_threshold=params.get("price_change_threshold", 0.10),
            stale_days=params.get("stale_days", 15),
            output_dir=output_dir,
        )
        engine.config["max_debate_rounds"] = params.get("max_debate_rounds", 1)
        engine.config["max_risk_discuss_rounds"] = params.get("max_risk_discuss_rounds", 1)
        if hasattr(engine, "l1_analyzer"):
            engine.l1_analyzer.config["pm_temperature"] = params.get("pm_temperature", 0.0)
        if params.get("numeric_tolerance_pct") is not None:
            os.environ["TRADINGAGENTS_NUMERIC_TOLERANCE_PCT"] = str(params["numeric_tolerance_pct"])

        # ── 注入大师配置（prompt variants） ──
        # 必须在 engine.run() 之前设置全局 config，因为 agent 创建时
        # 会调用 get_master_methodology(role) 读取 config
        from tradingagents.dataflows.config import set_config
        master_keys = ["master_bull", "master_bear", "master_pm", "master_trader",
                       "master_aggressive", "master_conservative", "master_neutral"]
        master_changed = False
        for key in master_keys:
            if key in params and params[key] != "default":
                engine.config[key] = params[key]
                master_changed = True
        if master_changed:
            set_config(engine.config)

        result = engine.run()
        summary = result.summary if hasattr(result, "summary") else {}

        de.DecisionEngine.DEFAULT_STOP_LOSS_PCT = orig_stop
        de.DecisionEngine.DEFAULT_TAKE_PROFIT_PCT = orig_take

        score_data = _compute_single_score(summary)
        elapsed = time.time() - t0

        return StockResult(
            symbol=symbol,
            elapsed_s=elapsed,
            **score_data,
        )

    except Exception as e:
        elapsed = time.time() - t0
        return StockResult(
            symbol=symbol,
            status="error",
            error_msg=f"{type(e).__name__}: {str(e)[:200]}",
            elapsed_s=elapsed,
        )


def _compute_single_score(summary: Dict[str, Any]) -> Dict[str, Any]:
    """从单股票回测结果计算评分。"""
    total_return = float(summary.get("total_return_pct", 0.0))
    sharpe = float(summary.get("sharpe_ratio", 0.0))
    max_dd = float(summary.get("max_drawdown_pct", 0.0))
    win_rate = float(summary.get("win_rate", 0.0))
    num_trades = int(summary.get("num_trades", 0))
    total_profit = float(summary.get("total_profit", 0.0))
    total_loss = float(summary.get("total_loss", 0.0))
    profit_factor = total_profit / abs(total_loss) if total_loss != 0 else 0.0

    score = (
        total_return * 0.35
        + sharpe * 0.30
        + max(max_dd, -1.0) * 0.15
        + win_rate * 0.10
        + min(profit_factor, 5.0) * 0.05
        - min(num_trades / 50, 1.0) * 0.05
    )

    return {
        "status": "ok",
        "score": score,
        "total_return_pct": total_return,
        "sharpe_ratio": sharpe,
        "max_drawdown_pct": max_dd,
        "win_rate": win_rate,
        "num_trades": num_trades,
        "profit_factor": profit_factor,
    }


# ══════════════════════════════════════════════════════════════════
# 多股票试验 worker（子进程入口）
# ══════════════════════════════════════════════════════════════════

def _run_multi_stock_trial_worker(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """在子进程中运行一组参数在多只股票上的回测。

    一个 trial = 一组参数 × N 只股票 → 聚合评分。
    """
    symbols: List[str] = kwargs["symbols"]
    start_date = kwargs["start_date"]
    end_date = kwargs["end_date"]
    trial_id = kwargs["trial_id"]
    params = kwargs["params"]
    output_root = kwargs["output_root"]
    initial_cash = kwargs.get("initial_cash", 100000.0)
    ensemble_method = kwargs.get("ensemble_method", "mean")

    trial_dir = Path(output_root) / f"trial_{trial_id:04d}"
    trial_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    per_stock: List[StockResult] = []

    for symbol in symbols:
        stock_dir = trial_dir / symbol
        stock_dir.mkdir(parents=True, exist_ok=True)

        sr = _run_one_backtest(
            symbol=symbol,
            params=params,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            output_dir=str(stock_dir),
        )
        per_stock.append(sr)

    # ── 聚合评分 ──
    ok_results = [s for s in per_stock if s.status == "ok"]
    error_count = len([s for s in per_stock if s.status != "ok"])

    if not ok_results:
        return TrialResult(
            trial_id=trial_id,
            params=params,
            status="error",
            error_msg=f"All {len(per_stock)} stocks failed",
            per_stock=per_stock,
            elapsed_s=time.time() - t0,
            output_dir=str(trial_dir),
        ).to_dict()

    scores = [s.score for s in ok_results]
    returns = [s.total_return_pct for s in ok_results]
    sharpes = [s.sharpe_ratio for s in ok_results]
    dds = [s.max_drawdown_pct for s in ok_results]
    wr = [s.win_rate for s in ok_results]
    trades = [s.num_trades for s in ok_results]
    pfs = [s.profit_factor for s in ok_results]

    # 聚合方法
    if ensemble_method == "median":
        ensemble_score = statistics.median(scores)
    elif ensemble_method == "min":
        ensemble_score = min(scores)  # 最保守
    elif ensemble_method == "mean_penalized":
        # mean − 0.5×std − 0.3×(mean−min) 惩罚不稳定
        ensemble_score = statistics.mean(scores) - 0.5 * (statistics.pstdev(scores) if len(scores) > 1 else 0)
    else:  # "mean" (default)
        ensemble_score = statistics.mean(scores)

    min_score = min(scores)
    score_std = statistics.pstdev(scores) if len(scores) > 1 else 0.0

    elapsed = time.time() - t0

    result = TrialResult(
        trial_id=trial_id,
        params=params,
        score=ensemble_score,
        score_method=ensemble_method,
        score_min=min_score,
        score_std=score_std,
        total_return_pct=statistics.mean(returns),
        sharpe_ratio=statistics.mean(sharpes),
        max_drawdown_pct=statistics.mean(dds),
        win_rate=statistics.mean(wr),
        num_trades=int(statistics.mean(trades)),
        profit_factor=statistics.mean(pfs),
        per_stock=per_stock,
        elapsed_s=elapsed,
        output_dir=str(trial_dir),
    )

    if error_count > 0:
        result.status = f"partial_ok_{error_count}_failed"

    # 保存
    result_file = trial_dir / "trial_result.json"
    result_file.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))

    return result.to_dict()


# ══════════════════════════════════════════════════════════════════
# HarnessOptimizer — 主控制器
# ══════════════════════════════════════════════════════════════════

class HarnessOptimizer:
    """基于 P&L 的 Harness 参数调优引擎（多股票交叉验证）。

    用法::

        # 单股票
        opt = HarnessOptimizer(
            symbols=["000423"],
            start_date="2024-01-01",
            end_date="2025-12-31",
            max_trials=50,
            n_workers=4,
        )

        # 多股票训练
        opt = HarnessOptimizer(
            symbols=["000423", "600519", "000858", "601318", "000333"],
            start_date="2024-01-01",
            end_date="2025-12-31",
            max_trials=30,
            n_workers=4,
        )
        best = opt.run()
        print(f"Best ensemble score: {best.score:.3f}")
    """

    def __init__(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
        max_trials: int = 50,
        n_workers: int = 4,
        initial_cash: float = 100_000.0,
        output_root: str = "backtest_results/optimize",
        param_space: Optional[Dict[str, List[Any]]] = None,
        ensemble_method: str = "mean",
        test_symbols: Optional[List[str]] = None,
        holdout_start: Optional[str] = None,
        holdout_end: Optional[str] = None,
        master_mode: str = "random",  # "random" | "combo" | "none"
    ):
        self.symbols = symbols
        self.start_date = start_date
        self.end_date = end_date
        self.max_trials = max_trials
        self.n_workers = n_workers
        self.initial_cash = initial_cash
        self.ensemble_method = ensemble_method
        self.master_mode = master_mode
        self.param_space = param_space or PARAM_SPACE_DEFAULT
        self.test_symbols = test_symbols or []
        self.holdout_start = holdout_start
        self.holdout_end = holdout_end

        # ── 大师选择注入参数空间 ──
        if self.master_mode != "none":
            self._param_space_with_masters = dict(self.param_space)
            self._param_space_with_masters.update(MASTER_PARAM_DEFAULTS)
        else:
            self._param_space_with_masters = self.param_space

        # 输出目录用第一个股票命名（多股票则用 "multi"）
        tag = symbols[0] if len(symbols) == 1 else "multi"
        self.output_root = Path(output_root) / tag

        self.results: List[TrialResult] = []
        self.best: Optional[TrialResult] = None

    # ── 参数采样 ──────────────────────────────────────────

    def _sample_params(self) -> Iterator[Dict[str, Any]]:
        """随机采样参数组合。支持 master_mode='combo' 使用预定义组合。"""
        import random
        random.seed(42)

        grid = self._param_space_with_masters

        if self.master_mode == "combo":
            from tools.prompt_variants import RECOMMENDED_COMBOS

            for trial_id in range(self.max_trials):
                params = {}
                for key, values in grid.items():
                    params[key] = random.choice(values)
                # 为每个 trial 固定一组大师组合
                combo = RECOMMENDED_COMBOS[trial_id % len(RECOMMENDED_COMBOS)]
                params.update(combo["masters"])
                params["_trial_id"] = trial_id
                params["_master_combo_name"] = combo["name"]
                yield params
        else:
            for trial_id in range(self.max_trials):
                params = {}
                for key, values in grid.items():
                    params[key] = random.choice(values)
                params["_trial_id"] = trial_id
                yield params

    # ── 主循环 ────────────────────────────────────────────

    def run(self) -> Optional[TrialResult]:
        """执行多股票参数优化循环。"""
        n_sym = len(self.symbols)

        logger.info("=" * 60)
        logger.info(f"HarnessOptimizer: {n_sym} stocks | {self.start_date} → {self.end_date}")
        logger.info(f"Train symbols: {', '.join(self.symbols)}")
        if self.test_symbols:
            logger.info(f"Test symbols:  {', '.join(self.test_symbols)}")
        logger.info(f"Max trials: {self.max_trials} | Workers: {self.n_workers}")
        logger.info(f"Ensemble: {self.ensemble_method} | Output: {self.output_root}")
        logger.info("=" * 60)

        self.output_root.mkdir(parents=True, exist_ok=True)

        param_list = list(self._sample_params())
        logger.info(f"[Phase 1] Random Search — {len(param_list)} trials × {n_sym} stocks = {len(param_list) * n_sym} backtests")

        with ProcessPoolExecutor(max_workers=self.n_workers) as executor:
            futures = {}
            for params in param_list:
                trial_id = params["_trial_id"]
                worker_kwargs = {
                    "symbols": self.symbols,
                    "start_date": self.start_date,
                    "end_date": self.end_date,
                    "trial_id": trial_id,
                    "params": params,
                    "output_root": str(self.output_root),
                    "initial_cash": self.initial_cash,
                    "ensemble_method": self.ensemble_method,
                }
                futures[executor.submit(_run_multi_stock_trial_worker, worker_kwargs)] = trial_id

            completed = 0
            for future in as_completed(futures):
                trial_id = futures[future]
                completed += 1

                try:
                    result_dict = future.result()
                    result = TrialResult.from_dict(result_dict)
                except Exception as e:
                    logger.error(f"Trial {trial_id} crashed: {e}")
                    result = TrialResult(
                        trial_id=trial_id,
                        params=param_list[trial_id],
                        status="error",
                        error_msg=str(e),
                    )

                self.results.append(result)

                if result.status.startswith("ok"):
                    n_ok = len([s for s in result.per_stock if s.status == "ok"])
                    master_info = ""
                    if self.master_mode != "none":
                        master_info = f" | {result.params.get('_master_combo_name', '')}"
                    logger.info(
                        f"  [{completed}/{self.max_trials}] T{trial_id:04d}: "
                        f"score={result.score:.3f} (σ={result.score_std:.3f}) | "
                        f"return={result.total_return_pct:+.1f}% | "
                        f"sharpe={result.sharpe_ratio:.2f} | "
                        f"dd={result.max_drawdown_pct:+.1f}% | "
                        f"{n_ok}/{len(result.per_stock)} stocks ok | "
                        f"{result.elapsed_s:.0f}s{master_info}"
                    )
                else:
                    logger.warning(
                        f"  [{completed}/{self.max_trials}] T{trial_id:04d}: "
                        f"FAILED — {result.error_msg[:80]}"
                    )

        # ── 排序 ──
        ok_results = [r for r in self.results if r.status.startswith("ok")]
        ok_results.sort(key=lambda r: r.score, reverse=True)

        if ok_results:
            self.best = ok_results[0]
            self._log_best()
        else:
            logger.error("No successful trials!")

        self._save_report()

        # ── 测试集验证 ──
        if self.best and self.test_symbols:
            self._validate_on_test_symbols()

        return self.best

    def _log_best(self):
        assert self.best is not None
        p = {k: v for k, v in self.best.params.items() if not k.startswith("_")}
        logger.info("\n" + "=" * 60)
        logger.info("🏆 BEST RESULT (ensemble)")
        logger.info(f"  Trial ID:    {self.best.trial_id:04d}")
        logger.info(f"  Score:       {self.best.score:.3f} (σ={self.best.score_std:.3f}, min={self.best.score_min:.3f})")
        logger.info(f"  Return:      {self.best.total_return_pct:+.1f}%")
        logger.info(f"  Sharpe:      {self.best.sharpe_ratio:.2f}")
        logger.info(f"  Max DD:      {self.best.max_drawdown_pct:+.1f}%")
        logger.info(f"  Params:      {json.dumps(p, ensure_ascii=False)}")
        if self.master_mode != "none":
            combo_name = self.best.params.get("_master_combo_name", "")
            masters = {k: v for k, v in self.best.params.items()
                       if k.startswith("master_") and v != "default"}
            if masters:
                logger.info(f"  Masters:     {combo_name} | {json.dumps(masters, ensure_ascii=False)}")
        logger.info("  Per-stock scores:")
        for sr in sorted(self.best.per_stock, key=lambda s: s.score, reverse=True):
            icon = "✅" if sr.status == "ok" else "❌"
            logger.info(f"    {icon} {sr.symbol}: score={sr.score:.3f}, return={sr.total_return_pct:+.1f}%, sharpe={sr.sharpe_ratio:.2f}")
        logger.info("=" * 60)

    def _save_report(self):
        """保存优化报告（JSON + Markdown）。"""
        report_path = self.output_root / "optimization_report.json"

        report = {
            "meta": {
                "train_symbols": self.symbols,
                "test_symbols": self.test_symbols,
                "start_date": self.start_date,
                "end_date": self.end_date,
                "max_trials": self.max_trials,
                "n_workers": self.n_workers,
                "initial_cash": self.initial_cash,
                "ensemble_method": self.ensemble_method,
                "generated_at": datetime.now().isoformat(),
            },
            "param_space": self.param_space,
            "best": self.best.to_dict() if self.best else None,
            "all_trials": [r.to_dict() for r in sorted(self.results, key=lambda r: r.score, reverse=True)],
            "statistics": {
                "total_trials": len(self.results),
                "ok_trials": len([r for r in self.results if r.status.startswith("ok")]),
                "error_trials": len([r for r in self.results if not r.status.startswith("ok")]),
                "mean_ensemble_score": (
                    sum(r.score for r in self.results if r.status.startswith("ok"))
                    / max(len([r for r in self.results if r.status.startswith("ok")]), 1)
                ),
                "top5_scores": [
                    r.score for r in sorted(
                        [r for r in self.results if r.status.startswith("ok")],
                        key=lambda r: r.score, reverse=True,
                    )[:5]
                ],
                "per_stock_mean_scores": self._per_stock_summary(),
            },
        }

        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        logger.info(f"\n📄 Report saved: {report_path}")

        # Markdown 摘要
        self._save_markdown_summary(report)

    def _per_stock_summary(self) -> Dict[str, float]:
        """计算各股票在 Top 10 试验中的平均得分。"""
        top10 = sorted(
            [r for r in self.results if r.status.startswith("ok")],
            key=lambda r: r.score, reverse=True,
        )[:10]
        symbol_scores: Dict[str, List[float]] = {}
        for trial in top10:
            for sr in trial.per_stock:
                if sr.status == "ok":
                    symbol_scores.setdefault(sr.symbol, []).append(sr.score)
        return {
            sym: statistics.mean(scores) if scores else 0.0
            for sym, scores in symbol_scores.items()
        }

    def _save_markdown_summary(self, report: Dict):
        md_path = self.output_root / "optimization_summary.md"
        n_stocks = len(self.symbols)

        lines = [
            f"# Harness 优化结果 — {n_stocks} 只股票",
            f"",
            f"**训练股票**: {', '.join(self.symbols)}",
            f"**测试股票**: {', '.join(self.test_symbols) if self.test_symbols else '(无)'}",
            f"**回测区间**: {self.start_date} → {self.end_date}",
            f"**试验次数**: {self.max_trials} 组参数 × {n_stocks} 只股票 = {self.max_trials * n_stocks} 次回测",
            f"**聚合方式**: {self.ensemble_method}",
            f"**成功/失败**: {report['statistics']['ok_trials']} / {report['statistics']['error_trials']}",
            f"",
        ]

        if self.best:
            p = {k: v for k, v in self.best.params.items() if not k.startswith("_")}
            lines += [
                f"## 🏆 最优参数",
                f"",
                f"| 指标 | 值 |",
                f"|------|-----|",
                f"| 综合评分 (ensemble) | {self.best.score:.3f} |",
                f"| 评分标准差 | {self.best.score_std:.3f} |",
                f"| 最差股票得分 | {self.best.score_min:.3f} |",
                f"| 平均收益率 | {self.best.total_return_pct:+.1f}% |",
                f"| 平均夏普 | {self.best.sharpe_ratio:.2f} |",
                f"| 平均最大回撤 | {self.best.max_drawdown_pct:+.1f}% |",
                f"",
                f"### 参数配置",
                f"```json",
                f"{json.dumps(p, indent=2, ensure_ascii=False)}",
                f"```",
                f"",
                f"### 各股票表现",
                f"",
                f"| 股票 | Score | Return | Sharpe | MaxDD | WinRate | Trades |",
                f"|------|-------|--------|--------|-------|---------|--------|",
            ]
            for sr in sorted(self.best.per_stock, key=lambda s: s.score, reverse=True):
                icon = "✅" if sr.status == "ok" else "❌"
                lines.append(
                    f"| {icon} {sr.symbol} | {sr.score:.3f} | {sr.total_return_pct:+.1f}% | "
                    f"{sr.sharpe_ratio:.2f} | {sr.max_drawdown_pct:+.1f}% | "
                    f"{sr.win_rate:.1%} | {sr.num_trades} |"
                )
            lines.append("")

            # Top 5
            top5 = [r for r in sorted(self.results, key=lambda r: r.score, reverse=True)[:5] if r.status.startswith("ok")]
            lines += [
                f"## Top 5 试验",
                f"",
                f"| # | Score | σ | Min | Return | Sharpe | MaxDD | Key Param |",
                f"|---|-------|---|-----|--------|--------|-------|-----------|",
            ]
            for i, r in enumerate(top5):
                key_param = f"s={r.params.get('default_stop_loss_pct',0):.0%} tp={r.params.get('default_take_profit_pct',0):.0%}"
                lines.append(
                    f"| {i+1} | {r.score:.3f} | {r.score_std:.3f} | {r.score_min:.3f} | "
                    f"{r.total_return_pct:+.1f}% | {r.sharpe_ratio:.2f} | "
                    f"{r.max_drawdown_pct:+.1f}% | {key_param} |"
                )
            lines.append("")

            # 各股票平均分
            per_stock = report["statistics"].get("per_stock_mean_scores", {})
            if per_stock:
                lines += [
                    f"### 各股票 Top10 平均得分",
                    f"",
                    f"| 股票 | 平均 Score |",
                    f"|------|-----------|",
                ]
                for sym, score in sorted(per_stock.items(), key=lambda x: x[1], reverse=True):
                    lines.append(f"| {sym} | {score:.3f} |")
                lines.append("")

        md_path.write_text("\n".join(lines))
        logger.info(f"📄 Summary saved: {md_path}")

    # ── 测试集验证 ──────────────────────────────────────

    def _validate_on_test_symbols(self):
        """用测试集股票验证最优参数的泛化能力。"""
        logger.info("\n" + "=" * 60)
        logger.info(f"[Validation] Testing on held-out symbols: {', '.join(self.test_symbols)}")
        logger.info("=" * 60)

        val_dir = self.output_root / "validation_test_symbols"
        val_dir.mkdir(parents=True, exist_ok=True)

        clean_params = {k: v for k, v in self.best.params.items() if not k.startswith("_")}

        worker_kwargs = {
            "symbols": self.test_symbols,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "trial_id": 9999,
            "params": clean_params,
            "output_root": str(val_dir),
            "initial_cash": self.initial_cash,
            "ensemble_method": self.ensemble_method,
        }

        result_dict = _run_multi_stock_trial_worker(worker_kwargs)
        result = TrialResult.from_dict(result_dict)

        if result.status.startswith("ok"):
            train_score = self.best.score
            test_score = result.score
            degradation = train_score - test_score

            logger.info(f"  Train ensemble score: {train_score:.3f}")
            logger.info(f"  Test ensemble score:  {test_score:.3f}")
            logger.info(f"  Degradation:          {degradation:+.3f}")

            if degradation > 0.5:
                logger.warning("⚠️  SIGNIFICANT OVERFITTING — test score << train score")
                logger.warning("   Consider: more diverse training stocks, fewer parameters, or longer training period.")

            logger.info("  Per-test-stock scores:")
            for sr in sorted(result.per_stock, key=lambda s: s.score, reverse=True):
                icon = "✅" if sr.status == "ok" else "❌"
                logger.info(f"    {icon} {sr.symbol}: score={sr.score:.3f}, return={sr.total_return_pct:+.1f}%")
        else:
            logger.error(f"Test validation failed: {result.error_msg}")


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="HarnessOptimizer — 基于 P&L 的参数调优引擎（多股票交叉验证）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 单股票调优
  .venv/bin/python tools/harness_optimizer.py \\
      --symbol 000423 --start 2024-01-01 --end 2025-12-31

  # 多股票交叉验证调优
  .venv/bin/python tools/harness_optimizer.py \\
      --train-symbols 000423,600519,000858,601318,000333 \\
      --start 2024-01-01 --end 2025-12-31 \\
      --max-trials 30 --workers 4

  # 多股票训练 + 测试集验证泛化
  .venv/bin/python tools/harness_optimizer.py \\
      --train-symbols 000423,600519,000858,601318,000333 \\
      --test-symbols 600036,000651 \\
      --start 2024-01-01 --end 2025-12-31 \\
      --max-trials 30 --workers 4 --ensemble median

  # 使用推荐股票池
  .venv/bin/python tools/harness_optimizer.py \\
      --train-symbols recommended \\
      --start 2024-01-01 --end 2025-12-31 \\
      --max-trials 20 --workers 4
        """,
    )

    # 股票选择
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbol", help="单股票代码（向后兼容）")
    group.add_argument("--train-symbols", help="训练股票列表，逗号分隔，或 'recommended' 使用推荐池")

    parser.add_argument("--test-symbols", help="测试股票列表（逗号分隔），用于验证泛化")
    parser.add_argument("--start", required=True, help="回测起始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="回测结束日期 (YYYY-MM-DD)")
    parser.add_argument("--max-trials", type=int, default=50, help="最大试验次数 (默认: 50)")
    parser.add_argument("--workers", type=int, default=4, help="并行 worker 数 (默认: 4)")
    parser.add_argument("--initial-cash", type=float, default=100_000.0, help="初始资金 (默认: 100000)")
    parser.add_argument("--output", default="backtest_results/optimize", help="输出目录")
    parser.add_argument("--ensemble", choices=["mean", "median", "min", "mean_penalized"],
                        default="mean", help="多股票聚合方式 (默认: mean)")
    parser.add_argument("--master-mode", choices=["random", "combo", "none"],
                        default="random",
                        help="大师选择模式: random=随机分配, combo=预定义组合, none=不注入大师 (默认: random)")
    parser.add_argument("--params", help="JSON 文件路径: 自定义参数空间")

    args = parser.parse_args()

    # 解析股票列表
    if args.symbol:
        train_symbols = [args.symbol]
    elif args.train_symbols == "recommended":
        # 前 5 只训练，后 3 只测试
        train_symbols = RECOMMENDED_SYMBOLS[:5]
        if not args.test_symbols:
            args.test_symbols = ",".join(RECOMMENDED_SYMBOLS[5:8])
            logger.info(f"Using recommended split: train={train_symbols}, test={RECOMMENDED_SYMBOLS[5:8]}")
    else:
        train_symbols = [s.strip() for s in args.train_symbols.split(",") if s.strip()]

    test_symbols = []
    if args.test_symbols:
        test_symbols = [s.strip() for s in args.test_symbols.split(",") if s.strip()]

    # 参数空间
    param_space = PARAM_SPACE_DEFAULT
    if args.params:
        with open(args.params) as f:
            param_space = json.load(f)
        logger.info(f"Loaded custom param space: {list(param_space.keys())}")

    # 创建优化器
    opt = HarnessOptimizer(
        symbols=train_symbols,
        start_date=args.start,
        end_date=args.end,
        max_trials=args.max_trials,
        n_workers=args.workers,
        initial_cash=args.initial_cash,
        output_root=args.output,
        param_space=param_space,
        ensemble_method=args.ensemble,
        test_symbols=test_symbols,
        master_mode=args.master_mode,
    )

    # 运行
    best = opt.run()

    if best is None:
        logger.error("Optimization failed — no successful trials.")
        sys.exit(1)


if __name__ == "__main__":
    main()
