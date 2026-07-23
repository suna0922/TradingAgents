# 选股圆桌会议 Web 应用 — 全链路审计报告

> **审计日期**：2026-07-22  
> **审计范围**：`web_app/` 全量代码（后端 Python + 前端 JSX）  
> **代码规模**：后端 ~2,800 行 / 前端 ~1,100 行 / 模板 61 行  
> **审计结论**：46 项问题（P0 × 17，P1 × 18，P2 × 11）

---

## 一、项目架构速览

```
web_app/
├── backend/                          # FastAPI 后端 (~2,800 行)
│   ├── main.py                       # FastAPI 入口，端口 8000
│   ├── models.py                     # Pydantic 数据模型
│   ├── routes/
│   │   ├── analyze.py                # 分析 API + SSE 流式端点
│   │   └── stock.py                  # 股票数据 REST API
│   └── services/
│       ├── graph_adapter.py          # ★ 核心：SSE → TradingAgentsGraph 桥接
│       ├── agent_service.py          # RoundtableSession + LLM 编排（备选路径）
│       ├── data_session.py           # ★ 核心：三层共享数据缓存（L1/L2/L3）
│       ├── stock_service.py          # 股票数据查询 + 面板组装
│       ├── master_loader.py          # YAML 大师加载器
│       ├── valuation.py              # 估值指标计算（已被 stock_service 替代）
│       └── data_fetcher.py           # 子进程数据取数（已废弃）
└── frontend/                         # 前端
    ├── index.html                    # 页面壳 + 内联 CSS (61 行)
    ├── src-vanilla/
    │   └── app.jsx                   # ★ 单文件巨石组件 (1,054 行)
    ├── vendor/                       # 本地 CDN 替代（React 18 + Tailwind + Babel）
    │   ├── react.production.min.js
    │   ├── react-dom.production.min.js
    │   ├── tailwind.js               # 407KB Tailwind Play CDN（生产环境不应使用）
    │   ├── babel.min.js              # 1.9MB（未被引用，可删除）
    │   └── app.js                    # esbuild 构建产物
    ├── _deprecated_vite/             # Vite+React+TS 废弃工程（可删除）
    └── package.json
```

### 数据流链路

```
用户输入 ticker
  │
  ├─ 1. POST /api/analyze/session       → 创建会话，fire-and-forget 预热面板数据
  ├─ 2. GET  /api/stock/{ticker}/...    → REST 面板数据（技术面/基本面）
  ├─ 3. GET  /api/analyze/session/{id}/stream → SSE 流式分析
  │      └── graph_adapter.py → TradingAgentsGraph.propagate_stream()
  │            ├── 基本面分析 → 技术面分析 → 多空辩论 → 风险辩论 → 交易策略 → 投资决策
  │            └── 每步通过 SSE 推送 chat 消息
  └─ 4. GET  /api/analyze/session/{id}/report/{type} → 查看各阶段报告
```

---

## 二、P0 — 致命问题（必须立即修复）

### B-P0-1 ⚠️ graph_adapter.py:239 — 单行 OHLCV 导致 IndexError 崩溃

**位置**：`web_app/backend/services/graph_adapter.py` 第 238-241 行

```python
# 当前代码（第 238-241 行）
prev_close = float(ohlcv["Close"].iloc[-2])     # ← 先求值，后判断长度！
change_pct = round((latest_price / prev_close - 1) * 100, 2) if len(ohlcv) > 1 else 0
```

**问题**：`iloc[-2]` 在 Python 表达式中立即求值，`len(ohlcv) > 1` 的判断在 `iloc[-2]` 之后才执行。当 OHLCV 只有 1 行时，直接抛出 `IndexError`，导致整个 SSE 流崩溃。

**修复**：
```python
if len(ohlcv) >= 2:
    prev_close = float(ohlcv["Close"].iloc[-2])
    change_pct = round((latest_price / prev_close - 1) * 100, 2)
else:
    change_pct = 0
```

---

### B-P0-2 ⚠️ agent_service.py:170 — LLM 错误被静默吞噬，假分析流入下游

**位置**：`web_app/backend/services/agent_service.py` 第 170 行附近

```python
# 当前代码
except Exception as e:
    return f"[LLM调用失败: {e}]"   # ← 把错误变成字符串当作分析结果返回
```

**问题**：LLM 调用失败后，错误字符串被当作正常的分析内容返回。下游的信号提取器（`_extract_signal`）会基于这个错误字符串做交易决策。用户也会在前端看到 "LLM调用失败" 被当作分析报告展示，没有任何红色错误提示。

**修复**：异常向上 propagate，由调用层（analyze.py 的 SSE 流）统一处理，通过 SSE 发送 `type: "error"` 事件通知前端。

---

### B-P0-3 ⚠️ agent_service.py:472-473 + graph_adapter.py:36-38 — 关键字匹配将否定语境误判为买入信号

**位置**：两处独立实现的信号提取函数

```python
# agent_service.py _extract_signal (行 472-473)
if "买入" in text:
    return "buy"

# graph_adapter.py _extract_signal_from_state (行 36-38)
if "买入" in pm_decision:
    signal = "buy"
```

**问题**：子串匹配，无法区分肯定/否定语境。例如：
- `"当前不建议买入，建议观望"` → 匹配 `"买入"` → 返回 `buy`（错了！应为 hold/sell）
- `"暂不买入"` / `"避免追高买入"` → 同样被误判

**修复方案**：添加否定词前后缀过滤：
```python
NEGATION_PATTERNS = ["不建议买入", "暂不买入", "避免.*买入", "不宜买入", "不要买入", "不建议追买"]
# 先检查否定模式，再检查肯定模式
def _extract_signal(text):
    for pat in NEGATION_PATTERNS:
        if re.search(pat, text):
            return "hold"
    if "强烈卖出" in text: return "sell"
    if "减持" in text: return "sell"
    if "强烈买入" in text: return "buy"
    if "买入" in text: return "buy"
    if "卖出" in text: return "sell"
    return "hold"
```

**影响范围**：`agent_service.py` 和 `graph_adapter.py` 两处均需修复。

---

### B-P0-4 ⚠️ stock_service.py:249-254 — 财务值移除单位词但不缩放，数值严重错误

**位置**：`web_app/backend/services/stock_service.py` 第 249-254 行

```python
# 当前代码
s = str(v).replace("亿", "").replace("万", "").replace("%", "")...
vals.append(float(s))
```

**问题**：移除单位词后直接 `float()`，不做数值缩放。例如：
- `"5.2亿"` → `float("5.2")` → **5.2**（实际应为 520,000,000 或 5.2 亿）
- `"3890万"` → `float("3890")` → **3890**（实际应为 38,900,000 或 3890 万）

这两个值在同一图表中展示时，单位完全不可比。

**修复**：根据原始单位缩放：
```python
def _parse_financial_value(v):
    """解析财务数值，统一转为原始值（元/万元等，按约定）。"""
    s = str(v).replace(",", "").replace("%", "").strip()
    multiplier = 1
    if "亿" in s:
        s = s.replace("亿", "")
        multiplier = 100_000_000  # 或统一用"亿"作为基础单位
    elif "万" in s:
        s = s.replace("万", "")
        multiplier = 10_000
    try:
        return float(s) * multiplier
    except ValueError:
        return None
```

---

### B-P0-5 ⚠️ data_session.py + graph_adapter.py — 7 个技术指标函数完全重复

**位置**：
- `web_app/backend/services/data_session.py`
- `web_app/backend/services/graph_adapter.py`

**重复的函数**：`_calc_sma`、`_calc_rsi`、`_calc_atr`、`_calc_macd`、`_calc_kdj`、`_calc_boll`、`_calc_volume_ratio`

**问题**：两处独立维护相同的技术指标计算逻辑。任何一边修 bug 另一边很可能遗漏，导致：
- 前端面板显示的技术指标值（通过 data_session.py → stock_service.py → REST API）
- LLM 分析使用的技术指标值（通过 graph_adapter.py → prompt 注入）

**这两个数值不一致**，用户看到的面板数据和 AI 分析依据的数据不同，这是最隐蔽的 bug。

**修复**：提取到独立模块 `web_app/backend/services/indicators.py`，两处 import 同一份实现。

---

### B-P0-6 ⚠️ stock_service.py:148-153 — `or 0` falsy 陷阱

**位置**：`web_app/backend/services/stock_service.py` 第 148 行

```python
tech.pe_static = float(
    ds.valuation.get("市盈率(静态)", [0])[0]
    if isinstance(ds.valuation.get("市盈率(静态)"), (list,tuple))
    else ds.valuation.get("市盈率(静态)", 0) or 0  # ← BUG
)
```

**问题**：Python 中 `0.0` 是 falsy。当 PE = 0.0（亏损公司）时，`float(0.0) or 0` 返回 `0`。虽然 `0.0` 和 `0` 值相同，但这里暴露了编码习惯问题。

正确的写法应为：
```python
val = ds.valuation.get("市盈率(静态)", 0)
tech.pe_static = float(val) if val is not None else 0
```

**影响范围**：`pe_static`、`pb`、`peg`、`market_cap`、`ps`、`dividend_yield` 共 6 个字段均存在此问题。

---

### B-P0-7 ⚠️ graph_adapter.py:287 — 需验证 master_config key 名是否正确注入引擎

**位置**：`web_app/backend/services/graph_adapter.py` 第 280-290 行

```python
config = DEFAULT_CONFIG.copy()
config["master_config"] = master_config
config["custom_theory_config"] = custom_theory_config
graph = TradingAgentsGraph(config=config, ...)
```

**问题**：`TradingAgentsGraph` 内部读取 `master_config` 和 `custom_theory_config` 是否使用相同的 key？如果 key 名不匹配（例如引擎内部读 `master_theories` 而非 `master_config`），大师理论注入将静默失效——所有座位分配的大师理论都不会生效，但不会报错。

**验证方法**：搜索 `tradingagents/graph/trading_graph.py` 中这些配置 key 的实际读取方式。

---

### B-P0-8 ⚠️ analyze.py:103 — fire-and-forget 任务无错误感知

**位置**：`web_app/backend/routes/analyze.py` 第 103 行

```python
asyncio.create_task(_prefetch_and_warm(session))
```

**问题**：`asyncio.create_task` 创建的协程如果内部抛异常，除了记 log 之外没有任何机制通知调用方或前端。如果 OHLCV 取数失败但用户没有立即触发分析，面板会一直显示"等待数据推送…"，用户不知道是取数失败还是仍在加载。

**修复**：任务完成后设置 DataSession 的 `prefetch_status` 字段（success/error），面板 API 返回状态信息。

---

### F-P0-1 ⚠️ app.jsx:211-214 — SSE 与 REST 数据源竞争

**位置**：`web_app/frontend/src-vanilla/app.jsx` 第 183-216 行

**问题**：
- 第 183-190 行：REST `/api/stock/{ticker}/technicals` 结果通过 `setTechnicals(d)` 设置
- 第 213-214 行：SSE `data_technicals` / `data_fundamentals` 事件也通过 `setTechnicals` 设置
- 两个异步来源到达顺序不确定。第 214 行的 `prev && prev.sections?.length` 防护不够可靠

**修复**：统一数据来源。SSE 推送 `data_technicals` 时标记 `source: "sse"`，REST 结果来的时候如果已有 SSE 数据则不覆盖。

---

### F-P0-2 ⚠️ app.jsx:157-224 — 快速双击"开始分析"的竞态条件

**位置**：`web_app/frontend/src-vanilla/app.jsx` 第 157-224 行

**问题**：`isAnalyzing` 在第 160 行设为 `true`，但在 `try/catch` 内。如果创建会话阶段失败：
1. `isAnalyzing` 被重置为 `false`
2. 之前的 SSE 连接可能还开着（`esRef.current` 可能没正确关闭）
3. 旧的 SSE 消息继续流入，与新会话数据混淆

**修复**：
```javascript
const sessionIdRef = useRef(null);  // 追踪当前活跃会话

const handleAnalyze = useCallback(async () => {
    const sid = Date.now().toString();
    sessionIdRef.current = sid;
    // ...
    es.addEventListener('message', (event) => {
        if (sessionIdRef.current !== sid) return;  // 忽略过期会话的消息
    });
}, [ticker]);
```

---

### F-P0-3 ⚠️ app.jsx:183-190 — 缺少 AbortController，快速切换股票时数据混乱

**位置**：`web_app/frontend/src-vanilla/app.jsx` 第 183-190 行

**问题**：REST 请求是 fire-and-forget（`.then().catch()`），没有 `AbortController`。如果用户在面板数据返回前切换股票：
1. 旧股票的请求可能在新请求之后完成
2. `setTechnicals(d)` 会把旧数据覆盖到新股票上

**修复**：在 `handleAnalyze` 开头创建 `AbortController`，传给所有 fetch：
```javascript
const controller = new AbortController();
fetch(url, { signal: controller.signal })
```

---

### F-P0-4 ⚠️ app.jsx:222 — SSE 错误处理阻止浏览器原生重连

**位置**：`web_app/frontend/src-vanilla/app.jsx` 第 222 行

```javascript
es.addEventListener('error', () => {
    setStatusMsg('⚠️ 连接中断，请刷新后重试');
    setIsAnalyzing(false);
    es.close();   // ← 阻止浏览器原生自动重连
});
```

**问题**：EventSource 原生支持自动重连，但 `es.close()` 彻底关闭了连接。用户在分析中断后必须手动刷新页面，无法恢复。且不区分"网络短暂波动"和"服务端崩溃"——前者应重试，后者才应提示刷新。

**修复**：实现指数退避重连：
```javascript
let retryCount = 0;
const MAX_RETRIES = 3;
es.addEventListener('error', () => {
    if (es.readyState === EventSource.CLOSED && retryCount < MAX_RETRIES) {
        retryCount++;
        setTimeout(() => createEventSource(), 1000 * Math.pow(2, retryCount));
    } else {
        setStatusMsg('⚠️ 连接中断，请刷新后重试');
        setIsAnalyzing(false);
        es.close();
    }
});
```

---

### F-P0-5 ⚠️ index.html:9 — 生产环境加载 407KB Tailwind CDN 脚本

**位置**：`web_app/frontend/index.html` 第 9 行

```html
<script src="/vendor/tailwind.js"></script>
```

**问题**：这是 Tailwind Play CDN 脚本（407KB），会在浏览器中动态解析所有 class 名并实时生成 CSS。生产环境应使用 esbuild 构建时预先提取用到的 class 生成静态 CSS。当前这 407KB 是所有前端资源中最大的单一文件，严重拖累首屏加载。

**修复**：
1. 从 `app.jsx` 中提取所有使用的 Tailwind class
2. 用 Tailwind CLI 或 PostCSS 构建静态 CSS 文件
3. 替换 `<script src="/vendor/tailwind.js">` 为 `<link rel="stylesheet" href="/vendor/tailwind.min.css">`

---

### F-P0-6 ⚠️ app.jsx — 单文件巨石组件 1,054 行

**位置**：`web_app/frontend/src-vanilla/app.jsx`

**问题**：12 个组件共存在一个文件中，包含：
- `App`（主组件，~250 行）
- `DataPanels`、`TechnicalsView`、`MiniLineChart`、`MetricCard`、`FundamentalsView`
- `RoundTable`、`MessageCard`、`PhasePanel`
- `ReportViewer`、`TheoryModal`、`RulesPanel`

所有状态管理、事件处理、API 调用、DOM 渲染逻辑全部耦合。任何修改都需要在这 1,054 行中定位。

**修复**：拆分为独立文件：
```
src-vanilla/
├── app.jsx              # 主入口，组装组件
├── components/
│   ├── StockInput.jsx
│   ├── DataPanels.jsx
│   ├── TechnicalsView.jsx
│   ├── FundamentalsView.jsx
│   ├── RoundTable.jsx
│   ├── PhasePanel.jsx
│   ├── MessageCard.jsx
│   ├── ReportViewer.jsx
│   ├── TheoryModal.jsx
│   └── RulesPanel.jsx
├── hooks/
│   └── useSSE.js       # 提取 SSE 连接逻辑
└── constants.js         # 大师数据、角色样式等常量
```

---

### F-P0-7 ⚠️ 无 React.memo/useMemo/useCallback — 每条 SSE 消息触发全量重渲染

**位置**：`web_app/frontend/src-vanilla/app.jsx` 全局

**问题**：
- `handleAnalyze` 中每条 SSE `chat` 消息执行 `setMessages(prev => [...prev, d.message])`（第 212 行），创建新的数组引用 → 触发 `App` 重渲染 → 所有子组件重渲染
- `PhasePanel`（第 680-755 行）重新渲染所有已渲染的消息卡片
- 在活跃的 SSE 流期间（可能持续 30-60 秒），每秒可能有多次重渲染

**修复**：
- `PhasePanel` 用 `React.memo` 包裹
- `MessageCard` 用 `React.memo` + id 作为比较 key
- 消息列表用 `useMemo` 缓存

---

### F-P0-8 ⚠️ 完全不支持移动端

**位置**：`web_app/frontend/src-vanilla/app.jsx` 全局

**固定宽度硬编码**：
- 头部输入区：`style={{width: '440px'}}`（第 282 行）
- 左侧数据面板列：`w-[520px] flex-shrink-0`（第 324 行）
- SVG 圆桌：`W: 510, H: 440`（第 557 行）
- 大师面板：`w-[340px]`（第 970 行）

CSS 中（`index.html` 第 20-48 行）没有任何 `@media` 查询。HTML5 拖拽 API 在移动端完全不可用。

**修复**：
1. 添加响应式断点（`md:` / `lg:` 前缀）
2. 移动端布局：圆桌→座位列表，面板→折叠式 accordion
3. 移动端用 touch 事件替代拖拽

---

### F-P0-9 ⚠️ 无 label 元素和 ARIA 属性

**位置**：`web_app/frontend/src-vanilla/app.jsx` 全局

**缺失项**：
- 所有 `<input>` 仅用 `placeholder` 替代 `<label>`（第 284、339、923-926 行）
- 没有任何 `aria-label`、`role`、`aria-live`、`aria-expanded` 属性
- 表格（第 1028-1049 行）缺少 `<th>` 对应的 `<caption>` 和 scope 属性
- `<button>` 缺少 `aria-label`（纯图标按钮）
- SSE 状态消息没有任何 `aria-live` 区域

**修复**：为每个交互元素添加 label 和 aria 属性。

---

## 三、P1 — 重要问题（应在近期修复）

### 后端

| # | 位置 | 问题 | 修复方案 |
|---|------|------|----------|
| **B-P1-1** | models.py:171 | "debator" 拼写错误（应为 "debater"），贯穿 7 个 agent prompt 和 3 个节点定义 | 全局搜索替换 `debator` → `debater` |
| **B-P1-2** | master_loader.py:274 | `master.__dict__["industries"] = industries` 绕过 Pydantic 校验，`industries` 字段不在 `Master` 模型中 | 在 `Master` Pydantic 模型中添加 `industries: list[str] = []` 可选字段 |
| **B-P1-3** | agent_service.py 全文件 | `RoundtableSession.run()` 方法（LLM 编排）未被 web 路径实际使用；web 走 `graph_adapter.py`。`RoundtableSession` 仅用作 session 数据容器 | 清理未使用的 `run()` 逻辑，保留数据容器功能 |
| **B-P1-4** | data_session.py:25-26 | 全局 `_sessions: dict[str, DataSession] = {}` 无 TTL 过期和 LRU 淘汰。每个 ticker+date 组合占 ~200KB，10 个并发用户 1 小时 = ~72MB 不可释放 | 添加 `cachetools.TTLCache` 或手动 LRU + TTL |
| **B-P1-5** | analyze.py + main.py | 无速率限制。任何人可无限次触发 LLM 分析，每次消耗 DeepSeek API 额度 | 使用 `slowapi`（FastAPI 限流库）添加每分钟 5 次的全局限制 |
| **B-P1-6** | main.py | 无认证。所有 API 端点公开可访问 | 添加 API Key 中间件（X-API-Key header） |
| **B-P1-7** | models.py vs app.jsx | Master 数据在前端 `MASTERS_DEFAULT`（app.jsx 第 6-17 行）和后端 `get_default_masters()` 中双重维护 | 前端改为从 `/api/analyze/masters` 获取，单一数据源 |
| **B-P1-8** | master_loader.py:8 | `_project_root = Path(__file__).resolve().parents[3]` 与 agent_service.py 的 `parents[2]` 不一致。因为 master_loader.py 在 `web_app/backend/services/`（+1 层嵌套），这种差异是代码异味 | 统一用 `web_app_root = Path(__file__).resolve().parents[...]` 常量 |
| **B-P1-9** | agent_service.py:480 | PM 未给出明确信号时默认返回 `HOLD`。这可能掩盖 PM 未能生成有效 signal 的问题 | 添加明确的 `signal=` 字段追踪，区分"明确 hold"和"无信号" |
| **B-P1-10** | analyze.py:84 | `import asyncio` 在第 5 行和第 84 行重复导入 | 删除第 84 行的重复 import |

### 前端

| # | 位置 | 问题 | 修复方案 |
|---|------|------|----------|
| **F-P1-1** | app.jsx:44-58 | `ROLE_STYLE`、`ROLE_BORDERS`、`ROLE_ICONS` 三处分别维护同一角色的颜色/图标信息，修改角色样式需改三处 | 合并为一个 `ROLE_CONFIG` 对象，包含所有字段 |
| **F-P1-2** | app.jsx:560-566, 685-691 | `phaseMeta` 在 `RoundTable` 和 `PhasePanel` 中完全重复定义 | 提取为共享常量 `PHASE_META` |
| **F-P1-3** | app.jsx:651-664, 879-891 | `getConversationSnippet()` 和 `parseMD()` 是两个独立实现的 Markdown 解析器，行为不一致 | 统一为一个 `parseMarkdown()` 函数 |
| **F-P1-4** | app.jsx:1024 | `navigator.clipboard.writeText()` 无 fallback。HTTP 或旧浏览器下复制功能完全失效 | 添加 `document.execCommand('copy')` fallback |
| **F-P1-5** | app.jsx:516-523 | 基本面历史数据切换年报/季报时每次都重新请求 API，无前端缓存 | 用 `useRef` 缓存已获取的数据 |
| **F-P1-6** | app.jsx:151-155 | Masters 列表每次页面加载都重新 fetch（静态数据） | 添加 `sessionStorage` 缓存，或改为构建时嵌入 |
| **F-P1-7** | index.html | 缺少 SEO 元标签：`<meta name="description">`、OG 标签、canonical URL | 添加完整的 meta 标签集 |
| **F-P1-8** | index.html:56 | `window.addEventListener('error', () => r.innerHTML = ...)` 用 `innerHTML` 替换整个 `#root`，销毁 React 内部引用 | 用 React ErrorBoundary 替代 |

---

## 四、P2 — 建议改进

### 后端

| # | 描述 | 建议 |
|---|------|------|
| B-P2-1 | `data_fetcher.py` 已废弃但仍保留 | 删除 |
| B-P2-2 | `_deprecated_vite/` 完整 React+TS 工程仍存在 | 删除或移到 archive/ |
| B-P2-3 | `valuation.py` 功能已被 DataSession/stock_service 内嵌 | 删除或合并 |
| B-P2-4 | agent_service.py 中 LLM prompts 硬编码（~100 行） | 迁移到 YAML/JSON 配置文件 |
| B-P2-5 | graph_adapter.py 中 `q.get(timeout=0.3)` 忙等待 | 用 `threading.Condition` 实现事件通知 |

### 前端

| # | 描述 | 建议 |
|---|------|------|
| F-P2-1 | `STOCK_SUGGESTIONS` 硬编码 6 只股票（app.jsx:68-71） | 从 API 动态加载 |
| F-P2-2 | `vendor/babel.min.js` (1.9MB) 存在于文件系统但未被 index.html 引用 | 删除 |
| F-P2-3 | 大师姓名 `max-w-[38px]` 截断过窄，中文姓名如"本杰明·格雷厄姆"完全不可读 | 改为 `max-w-[80px]` 或 tooltip |
| F-P2-4 | 聊天消息无限增长无虚拟滚动 | 使用 `react-window` 或限制显示最近 200 条 |
| F-P2-5 | `-webkit-mask-composite: xor`（index.html:36）仅 WebKit 支持 | 用 SVG mask 或 Canvas 替代 |
| F-P2-6 | 无 React ErrorBoundary 组件 | 添加顶层 ErrorBoundary |

---

## 五、性能评估

| 指标 | 当前值 | 目标值 | 问题 |
|------|--------|--------|------|
| 首屏加载 JS 总量 | ~550KB | <200KB | 407KB Tailwind CDN + babel 残留 |
| SSE 消息渲染 | 每条约 20ms 全量重渲染 | <5ms 增量渲染 | 无 memoization |
| 首次 API 延迟 | 5-15s（akshare 冷启动） | 3-5s | 名称表预加载已实现，OHLCV 仍需优化 |
| 内存占用（单会话） | ~200KB | <100KB | OHLCV 完整 DataFrame 保留在内存 |
| 移动端可用性 | 0%（完全不支持） | 60%+ | 无响应式 |

---

## 六、安全评估

| 风险 | 严重度 | 现状 |
|------|--------|------|
| 无认证 | 🔴 高 | 所有 API 公开可访问，任何人都可触发 LLM 分析消耗 API 额度 |
| 无速率限制 | 🔴 高 | 同上，可能被 DDoS 导致 API 费用暴涨 |
| CORS 配置错误 | 🟡 中 | `allow_origins=["*"]` + `allow_credentials=True` 组合浏览器会拒绝 |
| XSS | 🟢 低 | React createElement 会转义文本，未使用 dangerouslySetInnerHTML。仅 error handler 用 innerHTML（P1-8） |
| 输入校验 | 🟢 低 | Pydantic 模型提供基础校验，ticker 格式有限制 |

---

## 七、修复优先级与估算

| 批次 | 范围 | 工时估算 | 内容 |
|------|------|----------|------|
| **第一批** | 后端 P0 × 6 | 2-3h | IndexError、LLM 错误吞噬、信号误判、财务值单位、指标重复、falsy 陷阱 |
| **第二批** | 后端 P0 × 2 + 前端 P0 × 5 | 3-4h | config 验证、fire-and-forget 感知、数据源竞争、竞态、AbortController、SSE 重连、Tailwind CDN |
| **第三批** | 前端 P0 × 2 + 跨端 P1 × 10 | 6-8h | 组件拆分、memoization、移动端适配、可访问性、速率限制、认证 |
| **第四批** | P1 剩余 + P2 全部 | 3-4h | 拼写修正、数据去重、删除废弃代码、缓存优化、fallback |

**总计**：14-19 工时

---

## 八、修复顺序建议

```
第一批（数据正确性）
  └─ 保证系统不崩溃 + 数据不错
  └─ 产出可交付的修复

第二批（前端可用性）
  └─ 保证用户正常使用
  └─ 减少 407KB 首屏加载

第三批（架构 + 质量）
  └─ 可维护性 + 移动端
  └─ 安全底座

第四批（清理 + 优化）
  └─ 代码精简 + 性能打磨
```

---

*报告结束*
