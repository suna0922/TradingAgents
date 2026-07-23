# Web App 预测系统审计

**日期**: 2026-07-22
**审计范围**: `web_app/`（frontend + backend）
**对比基准**: 用户需求——"每天收盘后分析 → 输出规则 → 次日盯盘执行"

---

## 评分

| 需求 | 得分 | 关键发现 |
|------|------|---------|
| 大师/自定义理论配置 | ✅ 10/10 | 拖拽 + 自定义，完全满足 |
| 分析触发 | ⚠️ 6/10 | 支持手动触发，但无日期选择、无定时 |
| 规则输出展示 | ❌ 0/10 | **全是散文，不是规则** |
| 结果持久化 | ❌ 0/10 | 内存存储，刷新即没 |
| 持仓感知 | ❌ 0/10 | 完全不知道用户仓位 |

---

## 逐项分析

### 1. 大师理论配置 — 完全够用 ✅

- 拖拽 28 位大师到每个座位
- 每个角色独立的理论注入
- `custom_theory` 文本框，优先级高于大师

**结论**: 不需要改。

---

### 2. 规则输出 — 全项目最大的缺口 ❌

**现状**: 分析产出的 `final_trade_decision_md` 是 LLM 生成的散文：

```
"我建议在当前价位开始逐步建仓，第一笔在 200 日均线附近轻仓试探..."
```

预测系统需要的是**结构化规则**：

```
Rule 1: close < MA(close,200) AND volume > MA(volume,20) → BUY_ADD(20%) priority=70
Rule 2: low < 48.50 → STOP_LOSS priority=90
```

**好消息**: `backtest/hybrid.py` 的 `L1Analyzer._run_analysis()` 已经完成了结构化提取（`state["trading_rules_structured"]`），只是 Web 前端没展示。

### 需要做的事

| 序号 | 内容 | 工作量 |
|------|------|--------|
| a | 后端新增 `GET /api/analyze/session/{id}/rules`，返回 `trading_rules_structured` 列表 | ~10 行 |
| b | 前端增加 `RulesPanel` 组件，表格展示：条件列 / 动作列 / 优先级 | ~60 行 JSX |
| c | 规则可复制/导出为文本，方便盯盘对照 | ~20 行 |

---

### 3. 结果持久化 — 需要修 ❌

**现状**: `_sessions: dict` 存内存，服务器重启全丢。

**最轻量方案**: 每次分析完成后写 JSON 文件

```
sessions/
  2026-07-22_000423/
    result.json          ← trading_rules_structured + signal + fa_metrics
    fundamentals.md      ← 基本面报告原文
    market.md            ← 技术面报告原文
    final_decision.md    ← PM 最终决策
```

| 序号 | 内容 | 工作量 |
|------|------|--------|
| a | `AgentService` 加 `_save_session()` / `_load_session()` | ~30 行 |
| b | `GET /api/analyze/sessions` 列出历史分析 | ~15 行 |
| c | 前端加历史列表页 | ~40 行 JSX |

---

### 4. 定时调度 — 低优先级 ⚠️

用 crontab 一行搞定，不需要内建到 Web App：

```bash
# crontab: 每天 15:05 跑
5 15 * * 1-5 curl -X POST http://localhost:8000/api/analyze/session \
  -H "Content-Type: application/json" \
  -d '{"ticker":"000423","seats":[...]}'
```

建议新增一个 `POST /api/analyze/session/quick` 端点，接收 JSON 配置并返回 session_id（非 SSE 流），方便脚本调用。

---

### 5. 持仓感知 — 设计阶段，现在别做

预测系统阶段不需要。用户自己知道仓位，对照规则执行即可。等未来有券商 API 对接时再做。

---

## 修复优先级

| 优先级 | 内容 | 原因 |
|--------|------|------|
| **P0** | 规则输出展示 | 预测系统的核心产出——没有规则展示等于没有预测系统 |
| **P1** | 结果持久化 | 查历史对比、复盘验证 |
| **P2** | Quick API 端点 | 配合 crontab 自动化 |
| **P3** | 持仓感知 | 等实盘阶段 |

---

## 总结

Web App 目前是一个**分析展示工具**（给 LLM 分析结果排个好看的版面），不是**规则输出工具**（给用户可执行的交易指令）。

改完 P0+P1（规则展示 + 持久化）后，它就满足预测系统的基本需求：今天收盘跑分析 → 看到规则列表 → 明天盯盘对照执行。
