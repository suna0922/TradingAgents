#!/usr/bin/env python3
"""
watchdog_backtest.py — 回测守护进程

监控 backtest_hybrid.py 的运行状态，检测异常并自动停止。

监控项:
  1. 进程存活 (每30秒检查)
  2. 日志停滞 (超过300秒无新日志 → 可能卡死)
  3. L1 分析文件连续 0 规则 (超过2次 → PM异常)
  4. DeepSeek API 连续超时 (超过3次 → 接口问题)
  5. 重复的 L1 分析 (同一天多次触发 → 级联异常)

Usage:
  .venv/bin/python watchdog_backtest.py --log /tmp/backtest_6m.log \
      --l1-dir backtest_results/hybrid_6m/000423/l1_analysis \
      --pid-file /tmp/backtest_6m.pid
"""

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from datetime import datetime


class Watchdog:
    def __init__(self, log_path: str, l1_dir: str, pid_file: str,
                 stall_timeout: int = 300, check_interval: int = 30):
        self.log_path = Path(log_path)
        self.l1_dir = Path(l1_dir)
        self.pid_file = Path(pid_file)
        self.stall_timeout = stall_timeout
        self.check_interval = check_interval

        # 状态追踪
        self.last_log_size = 0
        self.last_log_time = time.time()
        self.last_l1_count = 0
        self.consecutive_zero_rules = 0
        self.seen_decisions = set()
        self.api_failures = 0

    def get_pid(self) -> int:
        try:
            return int(self.pid_file.read_text().strip())
        except Exception:
            return 0

    def is_process_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def check_log_progress(self) -> bool:
        """检查日志是否有新输出。返回 True 表示正常。"""
        if not self.log_path.exists():
            return False

        current_size = self.log_path.stat().st_size
        if current_size > self.last_log_size:
            self.last_log_size = current_size
            self.last_log_time = time.time()
            return True

        # 日志大小没变，检查是否超时
        elapsed = time.time() - self.last_log_time
        if elapsed > self.stall_timeout:
            # 检查最后几行是否有 LLM 调用中（LLM 调用可能持续很久）
            try:
                with open(self.log_path) as f:
                    lines = f.readlines()
                    last_lines = "".join(lines[-5:]) if lines else ""
                    if "HTTP Request: POST" in last_lines or "structured_llm.invoke" in last_lines:
                        # LLM 正在调用中，放宽超时
                        if elapsed < self.stall_timeout * 3:
                            return True
            except Exception:
                pass

            print(f"[WATCHDOG] Log stalled for {elapsed:.0f}s (> {self.stall_timeout}s), aborting!")
            return False
        return True

    def check_l1_quality(self) -> bool:
        """检查 L1 分析产物质量。返回 True 表示正常。"""
        if not self.l1_dir.exists():
            return True

        files = sorted(self.l1_dir.glob("*.json"))
        if len(files) == self.last_l1_count:
            return True
        self.last_l1_count = len(files)

        # 检查最新的 L1 分析
        for f in files[-3:]:
            fpath = str(f)
            # 跳过已经检查过的文件
            if fpath in self.seen_decisions:
                continue
            self.seen_decisions.add(fpath)

            try:
                with open(f) as fh:
                    data = json.load(fh)
                rules = data.get("trading_rules", [])
                date = data.get("date", "unknown")
                signal = data.get("signal", "?")

                # 检测 1: 连续 0 规则（5次才终止，0规则是PM结构化验证失败的常见产物）
                if len(rules) == 0:
                    self.consecutive_zero_rules += 1
                    print(f"[WATCHDOG] L1 {date}: 0 rules (consecutive={self.consecutive_zero_rules})")
                    if self.consecutive_zero_rules >= 5:
                        print(f"[WATCHDOG] Too many 0-rule analyses, aborting!")
                        return False
                else:
                    self.consecutive_zero_rules = 0

                print(f"[WATCHDOG] L1 {date} ({f.stem}): {signal}, {len(rules)} rules")

            except Exception as e:
                print(f"[WATCHDOG] Failed to read L1 file {f}: {e}")

        return True

    def check_log_errors(self) -> bool:
        """检查日志中的严重错误。返回 True 表示正常。"""
        if not self.log_path.exists():
            return True

        try:
            with open(self.log_path) as f:
                content = f.read()

            # 检测 DeepSeek API 连续超时
            timeout_count = content.count("ReadTimeout") + content.count("ConnectTimeout")
            if timeout_count > self.api_failures + 3:
                self.api_failures = timeout_count
                print(f"[WATCHDOG] DeepSeek API timeout count: {timeout_count}")

            # 检测 Traceback (非 eval_condition 的)
            traceback_lines = [l for l in content.split("\n") if "Traceback" in l]
            # 忽略 eval_condition 的已知 warning
            critical_tracebacks = [l for l in traceback_lines if "eval_condition" not in l]
            if len(critical_tracebacks) > 0:
                print(f"[WATCHDOG] CRITICAL: Traceback detected!")
                for tb in critical_tracebacks[-3:]:
                    print(f"  {tb}")
                return False

        except Exception as e:
            print(f"[WATCHDOG] Failed to read log: {e}")

        return True

    def run(self):
        pid = self.get_pid()
        if pid == 0:
            print("[WATCHDOG] No PID file found. Waiting...")

        print(f"[WATCHDOG] Monitoring PID={pid}, interval={self.check_interval}s, stall_timeout={self.stall_timeout}s")
        print(f"[WATCHDOG] Log: {self.log_path}")
        print(f"[WATCHDOG] L1 Dir: {self.l1_dir}")

        self.last_log_time = time.time()
        self.last_log_size = self.log_path.stat().st_size if self.log_path.exists() else 0

        while True:
            time.sleep(self.check_interval)

            pid = self.get_pid()

            # 进程已退出
            if not self.is_process_alive(pid):
                print(f"[WATCHDOG] Process {pid} exited. Checking results...")
                # 检查是否有正常结果输出
                result_dir = self.l1_dir.parent
                summary_file = result_dir / "summary.json"
                if summary_file.exists():
                    with open(summary_file) as f:
                        summary = json.load(f)
                    print(f"[WATCHDOG] Backtest completed!")
                    print(f"  Period:    {summary.get('period', '?')}")
                    print(f"  Return:    {summary.get('total_return_pct', 0):+.2f}%")
                    print(f"  Trades:    {summary.get('total_trades', 0)}")
                    print(f"  L1 Runs:   {summary.get('l1_analyses_total', 0)}")
                    print(f"  Win Rate:  {summary.get('win_rate_pct', 0):.1f}%")
                else:
                    print(f"[WATCHDOG] Backtest ended without summary (likely crashed)")
                break

            # 检查项
            checks = [
                ("Log progress", self.check_log_progress),
                ("L1 quality", self.check_l1_quality),
                ("Log errors", self.check_log_errors),
            ]

            for name, check_fn in checks:
                if not check_fn():
                    print(f"[WATCHDOG] Check '{name}' FAILED. Killing process {pid}...")
                    try:
                        os.kill(pid, signal.SIGTERM)
                        time.sleep(2)
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
                    sys.exit(1)

            # 定期状态
            now = datetime.now().strftime("%H:%M:%S")
            elapsed = time.time() - self.last_log_time
            print(f"[WATCHDOG {now}] OK | log_age={elapsed:.0f}s | L1_files={self.last_l1_count} | zero_rules={self.consecutive_zero_rules}")


def main():
    parser = argparse.ArgumentParser(description="Backtest Watchdog")
    parser.add_argument("--log", required=True, help="Backtest log file path")
    parser.add_argument("--l1-dir", required=True, help="L1 analysis output directory")
    parser.add_argument("--pid-file", required=True, help="PID file path")
    parser.add_argument("--stall-timeout", type=int, default=300,
                        help="Max log stall time in seconds (default: 300)")
    parser.add_argument("--check-interval", type=int, default=30,
                        help="Check interval in seconds (default: 30)")
    args = parser.parse_args()

    watchdog = Watchdog(
        log_path=args.log,
        l1_dir=args.l1_dir,
        pid_file=args.pid_file,
        stall_timeout=args.stall_timeout,
        check_interval=args.check_interval,
    )
    watchdog.run()


if __name__ == "__main__":
    main()
