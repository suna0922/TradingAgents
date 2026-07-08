#!/usr/bin/env python3
"""
HarnessOptimizer — 基于 P&L 的 Harness 参数调优引擎。

5 层 Harness 架构：
  L1 Base Model      — LLM (DeepSeek)
  L2 System Prompt   — Agent prompt 变体
  L3 Tool Orchestration — 数据源 / 辩论轮数
  L4 Validator       — 容忍度 / 数值检查
  L5 Loop Controller — 参数搜索、回测循环、评分排序  ← 本模块实现

用法：
  .venv/bin/python tools/harness_optimizer.py \
      --symbol 000423 \
      --start 2024-01-01 \
      --end 2025-12-31 \
      --max-trials 50 \
      --workers 4
"""

from __future__ import annotations

import argparse
import json
import logging
import os
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
}


# ══════════════════════════════════════════════════════════════════
# 试验结果
# ══════════════════════════════════════════════════════════════════

@dataclass
class TrialResult:
    """一次回测试验的完整结果。"""
    trial_id: int
    params: Dict[str, Any]
    status: str = "ok"       # "ok" | "error" | "timeout"
    error_msg: str = ""
    score: float = -999.0
    total_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    num_trades: int = 0
    total_profit: float = 0.0
    total_loss: float = 0.0
    profit_factor: float = 0.0
    daily_volatility: float = 0.0
    final_value: float = 0.0
    elapsed_s: float = 0.0
    output_dir: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════
# 回测运行器（子进程入口）
# ══════════════════════════════════════════════════════════════════

def _run_single_trial_worker(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """在子进程中运行单次回测。

    作为 ProcessPoolExecutor 的 worker 函数，
    接收序列化的参数字典，返回序列化的 TrialResult。
    """
    symbol = kwargs["symbol"]
    start_date = kwargs["start_date"]
    end_date = kwargs["end_date"]
    trial_id = kwargs["trial_id"]
    params = kwargs["params"]
    output_root = kwargs["output_root"]
    initial_cash = kwargs.get("initial_cash", 100000.0)

    trial_dir = Path(output_root) / f"trial_{trial_id:04d}"
    trial_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()

    try:
        # ── 注入参数 ──
        # 1. 通过 monkeypatch DecisionEngine 的默认风控参数
        import backtest.decision_engine as de
        orig_stop = de.DecisionEngine.DEFAULT_STOP_LOSS_PCT
        orig_take = de.DecisionEngine.DEFAULT_TAKE_PROFIT_PCT
        de.DecisionEngine.DEFAULT_STOP_LOSS_PCT = params.get("default_stop_loss_pct", 0.08)
        de.DecisionEngine.DEFAULT_TAKE_PROFIT_PCT = params.get("default_take_profit_pct", 0.20)

        # 2. 通过 config 注入辩论轮数和触发参数
        from backtest_hybrid import HybridBacktestEngine

        engine = HybridBacktestEngine(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            price_change_threshold=params.get("price_change_threshold", 0.10),
            stale_days=params.get("stale_days", 15),
            output_dir=str(trial_dir),
        )

        # 注入辩论轮数
        engine.config["max_debate_rounds"] = params.get("max_debate_rounds", 1)
        engine.config["max_risk_discuss_rounds"] = params.get("max_risk_discuss_rounds", 1)

        # 3. 注入模型温度（通过 L1Analyzer）
        if hasattr(engine, "l1_analyzer"):
            engine.l1_analyzer.config["pm_temperature"] = params.get("pm_temperature", 0.0)

        # 4. 注入数值容忍度
        if params.get("numeric_tolerance_pct") is not None:
            os.environ["TRADINGAGENTS_NUMERIC_TOLERANCE_PCT"] = str(params["numeric_tolerance_pct"])

        # ── 运行回测 ──
        result = engine.run()

        # ── 恢复默认值 ──
        de.DecisionEngine.DEFAULT_STOP_LOSS_PCT = orig_stop
        de.DecisionEngine.DEFAULT_TAKE_PROFIT_PCT = orig_take

        # ── 评分 ──
        summary = result.summary if hasattr(result, "summary") else {}
        score_data = _compute_score(summary)

        elapsed = time.time() - t0

        # ── 保存结果 ──
        trial_result = TrialResult(
            trial_id=trial_id,
            params=params,
            elapsed_s=elapsed,
            output_dir=str(trial_dir),
            **score_data,
        )

        result_file = trial_dir / "trial_result.json"
        result_file.write_text(json.dumps(trial_result.to_dict(), indent=2, ensure_ascii=False))

        return trial_result.to_dict()

    except Exception as e:
        elapsed = time.time() - t0
        logger.exception(f"Trial {trial_id} failed")

        return TrialResult(
            trial_id=trial_id,
            params=params,
            status="error",
            error_msg=f"{type(e).__name__}: {str(e)[:200]}",
            elapsed_s=elapsed,
            output_dir=str(trial_dir),
        ).to_dict()


def _compute_score(summary: Dict[str, Any]) -> Dict[str, Any]:
    """从回测结果计算多维度评分。

    综合评分 = 收益率×0.35 + 夏普×0.30 + 回撤×0.15 + 胜率×0.10 + 盈亏比×0.05 + 交易频率惩罚
    """
    total_return = float(summary.get("total_return_pct", 0.0))
    sharpe = float(summary.get("sharpe_ratio", 0.0))
    max_dd = float(summary.get("max_drawdown_pct", 0.0))  # 负值或 0
    win_rate = float(summary.get("win_rate", 0.0))
    num_trades = int(summary.get("num_trades", 0))
    total_profit = float(summary.get("total_profit", 0.0))
    total_loss = float(summary.get("total_loss", 0.0))
    profit_factor = total_profit / abs(total_loss) if total_loss != 0 else 0.0
    daily_vol = float(summary.get("daily_volatility", 0.0))
    final_value = float(summary.get("final_value", 0.0))

    # 综合评分公式
    # max_dd 是负值（如 -15.3 表示回撤 15.3%），加 1 转为正值（如 -15.3 + 1 = -14.3）
    # 这里简化：max_dd 越大（越接近 0）越好
    score = (
        total_return * 0.35
        + sharpe * 0.30
        + max(max_dd, -1.0) * 0.15  # cap at -100%
        + win_rate * 0.10
        + min(profit_factor, 5.0) * 0.05  # cap at 5x
        - min(num_trades / 50, 1.0) * 0.05  # 惩罚过度交易
    )

    return {
        "status": "ok",
        "score": score,
        "total_return_pct": total_return,
        "sharpe_ratio": sharpe,
        "max_drawdown_pct": max_dd,
        "win_rate": win_rate,
        "num_trades": num_trades,
        "total_profit": total_profit,
        "total_loss": total_loss,
        "profit_factor": profit_factor,
        "daily_volatility": daily_vol,
        "final_value": final_value,
    }


# ══════════════════════════════════════════════════════════════════
# HarnessOptimizer — 主控制器
# ══════════════════════════════════════════════════════════════════

class HarnessOptimizer:
    """基于 P&L 的 Harness 参数调优引擎。

    用法::

        opt = HarnessOptimizer(
            symbol="000423",
            start_date="2024-01-01",
            end_date="2025-12-31",
            max_trials=50,
            n_workers=4,
        )
        best = opt.run()
        print(f"Best: score={best.score:.3f}, return={best.total_return_pct:.1%}")
        print(f"Params: {best.params}")
    """

    def __init__(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        max_trials: int = 50,
        n_workers: int = 4,
        initial_cash: float = 100_000.0,
        output_root: str = "backtest_results/optimize",
        param_space: Optional[Dict[str, List[Any]]] = None,
        holdout_start: Optional[str] = None,
        holdout_end: Optional[str] = None,
    ):
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.max_trials = max_trials
        self.n_workers = n_workers
        self.initial_cash = initial_cash
        self.output_root = Path(output_root) / symbol
        self.param_space = param_space or PARAM_SPACE_DEFAULT
        self.holdout_start = holdout_start
        self.holdout_end = holdout_end

        self.results: List[TrialResult] = []
        self.best: Optional[TrialResult] = None

    # ── 参数采样 ──────────────────────────────────────────

    def _sample_params(self) -> Iterator[Dict[str, Any]]:
        """随机采样参数组合。"""
        import random
        random.seed(42)  # 可复现

        grid = self.param_space

        for trial_id in range(self.max_trials):
            params = {}
            for key, values in grid.items():
                params[key] = random.choice(values)
            params["_trial_id"] = trial_id
            yield params

    # ── 主循环 ────────────────────────────────────────────

    def run(self) -> TrialResult:
        """执行参数优化循环。"""
        logger.info("=" * 60)
        logger.info(f"HarnessOptimizer: {self.symbol} | {self.start_date} → {self.end_date}")
        logger.info(f"Max trials: {self.max_trials} | Workers: {self.n_workers}")
        logger.info(f"Output: {self.output_root}")
        logger.info("=" * 60)

        self.output_root.mkdir(parents=True, exist_ok=True)

        # Phase 1: Random Search
        logger.info("[Phase 1] Random Search — sampling parameters...")

        param_list = list(self._sample_params())

        with ProcessPoolExecutor(max_workers=self.n_workers) as executor:
            futures = {}
            for params in param_list:
                trial_id = params["_trial_id"]
                worker_kwargs = {
                    "symbol": self.symbol,
                    "start_date": self.start_date,
                    "end_date": self.end_date,
                    "trial_id": trial_id,
                    "params": params,
                    "output_root": str(self.output_root),
                    "initial_cash": self.initial_cash,
                }
                futures[executor.submit(_run_single_trial_worker, worker_kwargs)] = trial_id

            completed = 0
            for future in as_completed(futures):
                trial_id = futures[future]
                completed += 1

                try:
                    result_dict = future.result()
                    result = TrialResult(**result_dict)
                except Exception as e:
                    logger.error(f"Trial {trial_id} crashed: {e}")
                    result = TrialResult(
                        trial_id=trial_id,
                        params=param_list[trial_id],
                        status="error",
                        error_msg=str(e),
                    )

                self.results.append(result)

                if result.status == "ok":
                    logger.info(
                        f"  [{completed}/{self.max_trials}] Trial {trial_id:04d}: "
                        f"score={result.score:.3f}, "
                        f"return={result.total_return_pct:+.1f}%, "
                        f"sharpe={result.sharpe_ratio:.2f}, "
                        f"dd={result.max_drawdown_pct:+.1f}%, "
                        f"trades={result.num_trades}, "
                        f"{result.elapsed_s:.0f}s"
                    )
                else:
                    logger.warning(
                        f"  [{completed}/{self.max_trials}] Trial {trial_id:04d}: "
                        f"FAILED — {result.error_msg[:80]}"
                    )

        # ── 排序 ──
        ok_results = [r for r in self.results if r.status == "ok"]
        ok_results.sort(key=lambda r: r.score, reverse=True)

        if ok_results:
            self.best = ok_results[0]
            logger.info("\n" + "=" * 60)
            logger.info("🏆 BEST RESULT")
            logger.info(f"  Trial ID:   {self.best.trial_id:04d}")
            logger.info(f"  Score:      {self.best.score:.3f}")
            logger.info(f"  Return:     {self.best.total_return_pct:+.1f}%")
            logger.info(f"  Sharpe:     {self.best.sharpe_ratio:.2f}")
            logger.info(f"  Max DD:     {self.best.max_drawdown_pct:+.1f}%")
            logger.info(f"  Win Rate:   {self.best.win_rate:.1%}")
            logger.info(f"  Trades:     {self.best.num_trades}")
            logger.info(f"  Params:     {json.dumps({k: v for k, v in self.best.params.items() if not k.startswith('_')}, ensure_ascii=False)}")
            logger.info("=" * 60)
        else:
            logger.error("No successful trials!")

        # ── 保存报告 ──
        self._save_report()

        return self.best

    def _save_report(self):
        """保存优化报告。"""
        report_path = self.output_root / "optimization_report.json"

        report = {
            "meta": {
                "symbol": self.symbol,
                "start_date": self.start_date,
                "end_date": self.end_date,
                "max_trials": self.max_trials,
                "n_workers": self.n_workers,
                "initial_cash": self.initial_cash,
                "generated_at": datetime.now().isoformat(),
            },
            "param_space": self.param_space,
            "best": self.best.to_dict() if self.best else None,
            "all_trials": [r.to_dict() for r in sorted(self.results, key=lambda r: r.score, reverse=True)],
            "statistics": {
                "total_trials": len(self.results),
                "ok_trials": len([r for r in self.results if r.status == "ok"]),
                "error_trials": len([r for r in self.results if r.status == "error"]),
                "mean_score": sum(r.score for r in self.results if r.status == "ok") / max(len([r for r in self.results if r.status == "ok"]), 1),
                "top5_scores": [r.score for r in sorted([r for r in self.results if r.status == "ok"], key=lambda r: r.score, reverse=True)[:5]],
            },
        }

        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        logger.info(f"\n📄 Report saved: {report_path}")

        # 同时保存易读的 markdown 摘要
        md_path = self.output_root / "optimization_summary.md"
        lines = [
            f"# Harness 优化结果 — {self.symbol}",
            f"",
            f"**回测区间**: {self.start_date} → {self.end_date}",
            f"**试验次数**: {self.max_trials}",
            f"**成功/失败**: {report['statistics']['ok_trials']} / {report['statistics']['error_trials']}",
            f"**初始资金**: ¥{self.initial_cash:,.0f}",
            f"",
        ]

        if self.best:
            p = {k: v for k, v in self.best.params.items() if not k.startswith("_")}
            lines += [
                f"## 🏆 最优参数",
                f"",
                f"| 指标 | 值 |",
                f"|------|-----|",
                f"| 综合评分 | {self.best.score:.3f} |",
                f"| 总收益率 | {self.best.total_return_pct:+.1f}% |",
                f"| 夏普比率 | {self.best.sharpe_ratio:.2f} |",
                f"| 最大回撤 | {self.best.max_drawdown_pct:+.1f}% |",
                f"| 胜率 | {self.best.win_rate:.1%} |",
                f"| 交易次数 | {self.best.num_trades} |",
                f"| 盈亏比 | {self.best.profit_factor:.2f} |",
                f"| 最终资金 | ¥{self.best.final_value:,.0f} |",
                f"",
                f"### 参数配置",
                f"",
                f"```json",
                f"{json.dumps(p, indent=2, ensure_ascii=False)}",
                f"```",
                f"",
            ]

            # Top 5
            top5 = [r for r in sorted(self.results, key=lambda r: r.score, reverse=True)[:5] if r.status == "ok"]
            lines += [
                f"## Top 5 试验",
                f"",
                f"| # | Score | Return | Sharpe | MaxDD | WinRate | Trades | Key Param |",
                f"|---|-------|--------|--------|-------|---------|--------|-----------|",
            ]
            for i, r in enumerate(top5):
                key_param = f"stop={r.params.get('default_stop_loss_pct',0):.0%} tp={r.params.get('default_take_profit_pct',0):.0%}"
                lines.append(
                    f"| {i+1} | {r.score:.3f} | {r.total_return_pct:+.1f}% | "
                    f"{r.sharpe_ratio:.2f} | {r.max_drawdown_pct:+.1f}% | "
                    f"{r.win_rate:.1%} | {r.num_trades} | {key_param} |"
                )

        md_path.write_text("\n".join(lines))
        logger.info(f"📄 Summary saved: {md_path}")

    # ── Walk-Forward Validation ────────────────────────────

    def validate(self, best_params: Dict[str, Any]) -> Optional[TrialResult]:
        """用留出的时间段验证最优参数，防止过拟合。"""
        if not self.holdout_start:
            logger.warning("No holdout period configured. Skipping validation.")
            return None

        logger.info("\n" + "=" * 60)
        logger.info(f"[Validation] Holdout: {self.holdout_start} → {self.holdout_end}")
        logger.info("=" * 60)

        val_dir = self.output_root / "validation"
        val_dir.mkdir(parents=True, exist_ok=True)

        worker_kwargs = {
            "symbol": self.symbol,
            "start_date": self.holdout_start,
            "end_date": self.holdout_end,
            "trial_id": 9999,
            "params": best_params,
            "output_root": str(val_dir),
            "initial_cash": self.initial_cash,
        }

        result_dict = _run_single_trial_worker(worker_kwargs)
        result = TrialResult(**result_dict)

        if result.status == "ok":
            in_sample_return = self.best.total_return_pct if self.best else 0.0
            degradation = abs(result.total_return_pct - in_sample_return)

            logger.info(f"  In-sample return:   {in_sample_return:+.1f}%")
            logger.info(f"  Out-of-sample return: {result.total_return_pct:+.1f}%")
            logger.info(f"  Degradation:         {degradation:.1f}pp")

            if result.total_return_pct < in_sample_return * 0.3:
                logger.warning("⚠️  SIGNIFICANT OVERFITTING DETECTED!")
                logger.warning("   Out-of-sample return is < 30% of in-sample.")
                logger.warning("   Consider: reduce parameter count, extend training period, or simplify strategy.")

        return result


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="HarnessOptimizer — 基于 P&L 的参数调优引擎",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 100次试验，8核并行
  .venv/bin/python tools/harness_optimizer.py \\
      --symbol 000423 --start 2024-01-01 --end 2025-12-31 \\
      --max-trials 100 --workers 8

  # 含 walk-forward validation
  .venv/bin/python tools/harness_optimizer.py \\
      --symbol 000423 --start 2024-01-01 --end 2025-06-30 \\
      --max-trials 30 --workers 4 \\
      --holdout-start 2025-07-01 --holdout-end 2025-12-31
        """,
    )

    parser.add_argument("--symbol", required=True, help="股票代码 (如 000423)")
    parser.add_argument("--start", required=True, help="回测起始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="回测结束日期 (YYYY-MM-DD)")
    parser.add_argument("--max-trials", type=int, default=50, help="最大试验次数 (默认: 50)")
    parser.add_argument("--workers", type=int, default=4, help="并行 worker 数 (默认: 4)")
    parser.add_argument("--initial-cash", type=float, default=100_000.0, help="初始资金 (默认: 100000)")
    parser.add_argument("--output", default="backtest_results/optimize", help="输出目录")
    parser.add_argument("--holdout-start", help="验证期起始日期 (YYYY-MM-DD)")
    parser.add_argument("--holdout-end", help="验证期结束日期 (YYYY-MM-DD)")
    parser.add_argument("--params", help="JSON 文件路径: 自定义参数空间")

    args = parser.parse_args()

    # 自定义参数空间
    param_space = PARAM_SPACE_DEFAULT
    if args.params:
        with open(args.params) as f:
            param_space = json.load(f)
        logger.info(f"Loaded custom param space from {args.params}")

    # 创建优化器
    opt = HarnessOptimizer(
        symbol=args.symbol,
        start_date=args.start,
        end_date=args.end,
        max_trials=args.max_trials,
        n_workers=args.workers,
        initial_cash=args.initial_cash,
        output_root=args.output,
        param_space=param_space,
        holdout_start=args.holdout_start,
        holdout_end=args.holdout_end,
    )

    # 运行
    best = opt.run()

    # Walk-forward validation
    if best and opt.holdout_start:
        clean_params = {k: v for k, v in best.params.items() if not k.startswith("_")}
        opt.validate(clean_params)

    # 退出码
    if best is None:
        logger.error("Optimization failed — no successful trials.")
        sys.exit(1)


if __name__ == "__main__":
    main()
