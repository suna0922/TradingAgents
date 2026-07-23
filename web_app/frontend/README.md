# 前端说明（单一权威源）

本前端**只有一条构建/服务链路**，请勿再引入第二套。

## 权威源与产物

```
src-vanilla/app.jsx   ← 唯一手改源码（vanilla React，用全局 React，无 import）
        │  esbuild 打包
        ▼
vendor/app.js         ← 唯一被服务的产物（index.html 通过 <script> 引用）
index.html            ← 页面骨架 + 运行时 CDN（react / react-dom / tailwind 皆走 vendor/*.js）
```

后端 `web_app/backend/main.py`：`/` 返回 `index.html`，`/vendor` 挂载 `vendor/` 静态目录。
**没有 vite dev server、没有 dist/，`src/` 已废弃。**

## 改前端的正确姿势

1. 编辑 `src-vanilla/app.jsx`
2. 重新打包：
   ```bash
   cd web_app/frontend
   npm run build      # = esbuild ... --minify
   # 或开发时热重建：npm run watch
   ```
3. **bump 缓存号**：修改 `index.html` 里 `vendor/app.js?v=YYYYMMDDHHMM`，否则浏览器读旧缓存
4. 重启/刷新后端页面

## `_deprecated_vite/`（已归档，勿用）

原本有一套 Vite + TypeScript + `src/*.tsx` 工程，但**从未被构建或服务**（main.py 不服务 dist/，无人引用 src/），是"双轨"隐患的来源。现已整体移入 `_deprecated_vite/` 冷冻：
`src/ vite.config.ts tsconfig*.json postcss.config.js tailwind.config.js public/`

如果将来要正式迁移到 Vite 工程，需要：把 vanilla 独有功能（自定义座位、SSE 数据面板、TheoryPanel 等）补齐进 `src/`，`vite build` 产 `dist/`，并改 `main.py` 改为服务 `dist/index.html` + `dist/assets`。届时再删除本 vanilla 链路。
