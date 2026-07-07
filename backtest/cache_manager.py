"""回测系统缓存管理层。

四级缓存策略：
1. OHLCV 数据缓存（ohlcv/）: 按 symbol 缓存日线数据
2. FA 报告缓存（fa/）: 按 (symbol, report_period) 缓存，季度粒度
3. 决策缓存（decisions/）: 按 (symbol, date) 缓存 WeeklyDecision
4. 快照缓存（snapshots/）: 支持断点续跑

所有缓存以 JSON 文件形式存储在 output_dir/cache/ 下。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)


class CacheManager:
    """回测系统的缓存管理器。"""

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.data_dir = self.base_dir / "cache" / "ohlcv"
        self.fa_dir = self.base_dir / "cache" / "fa"
        self.decisions_dir = self.base_dir / "cache" / "decisions"
        self.snapshots_dir = self.base_dir / "cache" / "snapshots"

        for d in [self.data_dir, self.fa_dir, self.decisions_dir,
                   self.snapshots_dir]:
            d.mkdir(parents=True, exist_ok=True)

    # ── OHLCV 数据缓存 ───────────────────────────────────────────

    def get_ohlcv(self, symbol: str) -> Optional[Any]:
        """获取缓存的 OHLCV DataFrame（CSV 格式）。"""
        import pandas as pd
        cache_file = self.data_dir / f"{symbol}.csv"
        if cache_file.exists():
            try:
                df = pd.read_csv(cache_file, parse_dates=["date"])
                logger.debug(f"[Cache] OHLCV HIT {symbol}")
                return df
            except Exception as e:
                logger.warning(f"[Cache] OHLCV cache corrupt: {e}")
        return None

    def save_ohlcv(self, symbol: str, df) -> None:
        """保存 OHLCV 数据到 CSV 缓存。"""
        import pandas as pd
        cache_file = self.data_dir / f"{symbol}.csv"
        try:
            df.to_csv(cache_file, index=False)
            logger.info(f"[Cache] OHLCV SAVED {symbol} ({len(df)} rows)")
        except IOError as e:
            logger.error(f"[Cache] Failed to save OHLCV: {e}")

    # ── FA 报告缓存 ─────────────────────────────────────────────

    def get_fa_report(self, symbol: str, report_period: str) -> Optional[Dict]:
        """获取缓存的 FA 报告。

        Args:
            symbol: 股票代码，如 "000960"
            report_period: 报告期，如 "2024Q1", "2024Q2"

        Returns:
            缓存的 dict 或 None（未命中）
        """
        cache_file = self.fa_dir / f"{symbol}_{report_period}.json"
        if cache_file.exists():
            try:
                with open(cache_file, encoding="utf-8") as f:
                    data = json.load(f)
                    logger.debug(f"[Cache] FA HIT {symbol} {report_period}")
                    return data
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"[Cache] FA cache corrupt: {cache_file}, {e}")
        return None

    def save_fa_report(self, symbol: str, report_period: str,
                       fa_result: Dict) -> None:
        """保存 FA 报告到缓存。"""
        cache_file = self.fa_dir / f"{symbol}_{report_period}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(fa_result, f, ensure_ascii=False, indent=2)
            logger.info(f"[Cache] FA SAVED {symbol} {report_period}")
        except IOError as e:
            logger.error(f"[Cache] Failed to save FA: {e}")

    # ── 季度报告期计算 ──────────────────────────────────────────

    @staticmethod
    def get_latest_fa_period(date_str: str) -> str:
        """根据当前日期，计算最近已发布的季度报告期。

        A股财报发布规则（法定截止日 + 实际可用时间）：
        - Q1 (1-3月): 4月30日前发布 → 5月1日起可用
        - H1/Q2 (4-6月): 8月31日前 → 9月1日起可用
        - Q3 (7-9月): 10月31日前 → 11月1日起可用
        - Q4/年报 (10-12月): 次年4月30日前 → 次年5月1日起可用

        时间线：
            1月 ~ 4月30日   → 上年Q4(年报)
            5月1日 ~ 8月31日 → 当年Q1
            9月1日 ~ 10月31日 → 当年H1(Q2)
            11月1日 ~ 12月31日 → 当年Q3

        Returns:
            如 "2025Q1", "2024Q4"
        """
        # 处理带时间部分的日期字符串（如 "2025-01-02 00:00:00"）
        date_only = date_str.split()[0] if ' ' in date_str else date_str
        
        try:
            dt = datetime.strptime(date_only, "%Y-%m-%d")
        except ValueError:
            for fmt in ("%Y%m%d", "%Y/%m/%d"):
                try:
                    dt = datetime.strptime(date_only, fmt)
                    break
                except ValueError:
                    continue
            else:
                return "Unknown"

        y = dt.year
        m = dt.month

        # 四个时间窗口，按 A 股财报实际发布节奏
        if m >= 5 and m <= 8:
            # 5月1日 ~ 8月31日：当年一季报已发布
            return f"{y}Q1"
        elif m >= 9 and m <= 10:
            # 9月1日 ~ 10月31日：当年半年报(H1/Q2)已发布
            return f"{y}H1"
        elif m >= 11:
            # 11月1日 ~ 12月31日：当年三季报(Q3)已发布
            return f"{y}Q3"
        else:
            # 1月 ~ 4月30日：上年年报(Q4)已发布
            return f"{y - 1}Q4"

    def is_quarter_start(self, symbol: str, date_str: str) -> bool:
        """判断当前日期是否进入新的季度报告期。

        通过比较最新可用报告期与上一次缓存的 FA 报告期来判断。
        如果不同则说明进入了新季度，需要重新跑 FA。

        Args:
            symbol: 股票代码
            date_str: 当前日期 YYYY-MM-DD

        Returns:
            True 如果需要跑新季度的 FA
        """
        current_period = self.get_latest_fa_period(date_str)
        last_fa_period = self._get_last_fa_period(symbol)

        if last_fa_period is None:
            # 从未跑过 FA → 需要跑第一次
            logger.debug(f"[FA] No previous FA for {symbol}, need first run")
            return True

        if current_period != last_fa_period:
            logger.debug(f"[FA] Quarter boundary: {last_fa_period} -> {current_period}"
                         f" for {symbol}")
            return True

        return False

    def _get_last_fa_period(self, symbol: str) -> Optional[str]:
        """获取该股票最近一次缓存的 FA 报告期。"""
        files = sorted(self.fa_dir.glob(f"{symbol}_Q*.json"))
        if files:
            # 从文件名解析报告期，如 "000960_2024Q1.json" → "2024Q1"
            stem = files[-1].stem
            parts = stem.rsplit("_", 1)
            if len(parts) == 2:
                return parts[1]
        return None

    # ── 决策缓存 ─────────────────────────────────────────────────

    def get_decision(self, symbol: str, date: str) -> Optional[Dict]:
        """获取缓存的决策结果。"""
        cache_file = self.decisions_dir / f"{symbol}_{date}.json"
        if cache_file.exists():
            try:
                with open(cache_file, encoding="utf-8") as f:
                    data = json.load(f)
                    logger.debug(f"[Cache] Decision HIT {symbol} on {date}")
                    return data
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"[Cache] Decision cache corrupt: {e}")
        return None

    def save_decision(self, symbol: str, date: str, decision: Dict) -> None:
        """保存决策到缓存。"""
        cache_file = self.decisions_dir / f"{symbol}_{date}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(decision, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"[Cache] Failed to save decision: {e}")

    def get_last_decision_date(self, symbol: str) -> Optional[str]:
        """获取上一次决策的日期字符串。"""
        files = sorted(self.decisions_dir.glob(f"{symbol}_*.json"))
        if files:
            stem = files[-1].stem
            prefix = f"{symbol}_"
            if stem.startswith(prefix):
                return stem[len(prefix):]
        return None

    def list_decision_dates(self, symbol: str) -> List[str]:
        """列出所有已缓存的决策日期（有序）。"""
        files = sorted(self.decisions_dir.glob(f"{symbol}_*.json"))
        prefix = f"{symbol}_"
        dates = []
        for f in files:
            stem = f.stem
            if stem.startswith(prefix):
                dates.append(stem[len(prefix):])
        return dates

    # ── 快照缓存（断点续跑）──────────────────────────────────────

    def save_snapshot(self, symbol: str, date: str, state: Dict) -> None:
        """保存每日组合状态快照。"""
        snapshot_file = self.snapshots_dir / f"{symbol}_{date}.json"
        try:
            with open(snapshot_file, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"[Cache] Failed to save snapshot: {e}")

    def load_latest_snapshot(self, symbol: str) -> Optional[Dict]:
        """加载最新的状态快照（用于断点续跑）。"""
        files = sorted(self.snapshots_dir.glob(f"{symbol}_*.json"))
        if files:
            try:
                with open(files[-1], encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"[Cache] Snapshot corrupt: {e}")
        return None

    # ── 统计信息 ────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        """返回缓存统计信息（用于调试和监控）。"""
        fa_count = len(list(self.fa_dir.glob("*.json"))) if self.fa_dir.exists() else 0
        decision_count = len(list(self.decisions_dir.glob("*.json"))) if self.decisions_dir.exists() else 0
        snapshot_count = len(list(self.snapshots_dir.glob("*.json"))) if self.snapshots_dir.exists() else 0
        ohlcv_count = len(list(self.data_dir.glob("*"))) if self.data_dir.exists() else 0

        return {
            "fa_reports": fa_count,
            "decisions": decision_count,
            "snapshots": snapshot_count,
            "ohlcv_files": ohlcv_count,
            "cache_base": str(self.base_dir),
        }
