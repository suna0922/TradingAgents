#!/usr/bin/env bash
# 本地打包脚本 — 在 macOS 开发机上执行
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_DIR="$PROJECT_DIR/deploy"
TMP_DIR="/tmp/trading-roundtable-deploy-$$"

echo "=== Packaging project for ECS deployment ==="

# 创建临时目录
mkdir -p "$TMP_DIR"

# 复制核心文件
echo "Copying project files..."
cd "$PROJECT_DIR"

# 项目根文件
cp -r pyproject.toml uv.lock README.md LICENSE CHANGELOG.md \
   docker-compose.yml Dockerfile \
   tradingagents cli backtest \
   "$TMP_DIR/"
# web_app 需保持子目录结构（uvicorn 从项目根 import web_app.backend.main）
mkdir -p "$TMP_DIR/web_app"
cp -r web_app/backend "$TMP_DIR/web_app/"

# 前端产物（index.html + vendor 静态资源，缺 vendor 页面会白屏）
mkdir -p "$TMP_DIR/web_app/frontend"
cp web_app/frontend/index.html "$TMP_DIR/web_app/frontend/"
cp -r web_app/frontend/vendor "$TMP_DIR/web_app/frontend/"

# 复制部署脚本
cp -r deploy "$TMP_DIR/"

# 创建 .env 模板（用户需在ECS上填入自己的key）
cat > "$TMP_DIR/.env" << 'EOF'
# 配置你的 DeepSeek API Key
# DEEPSEEK_API_KEY=sk-your-api-key-here
# 可选：自定义 DeepSeek 地址
# DEEPSEEK_BASE_URL=https://api.deepseek.com
EOF

echo "Removing dev artifacts and large files..."
# 清理不需要的文件
find "$TMP_DIR" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "$TMP_DIR" -type d -name 'node_modules' -exec rm -rf {} + 2>/dev/null || true
find "$TMP_DIR" -type d -name '.mypy_cache' -exec rm -rf {} + 2>/dev/null || true
find "$TMP_DIR" -type f -name '*.pyc' -delete 2>/dev/null || true
find "$TMP_DIR" -type f -name '*.pyo' -delete 2>/dev/null || true
find "$TMP_DIR" -type f -name '.DS_Store' -delete 2>/dev/null || true
find "$TMP_DIR" -type f -name '*.tsbuildinfo' -delete 2>/dev/null || true

# 清理 reports / backtest_results（可选，保留目录结构）
rm -rf "$TMP_DIR/backtest_results"
mkdir -p "$TMP_DIR/backtest_results"
rm -rf "$TMP_DIR/reports"
mkdir -p "$TMP_DIR/reports"

echo "Creating tar.gz..."
cd /tmp
tar czf "$DEPLOY_DIR/trading-roundtable.tar.gz" \
    --exclude='.git' \
    --exclude='.venv' \
    trading-roundtable-deploy-$$/

# 重命名为标准目录名
mv "$TMP_DIR" "$TMP_DIR-rename"
mkdir -p "$TMP_DIR"
cp -r "$TMP_DIR-rename"/* "$TMP_DIR/"
rm -rf "$TMP_DIR-rename"

# 重新打包为正确的目录名
rm -f "$DEPLOY_DIR/trading-roundtable.tar.gz"
cd /tmp
mv trading-roundtable-deploy-$$ trading-roundtable
tar czf "$DEPLOY_DIR/trading-roundtable.tar.gz" \
    --exclude='.git' \
    --exclude='.venv' \
    trading-roundtable/
rm -rf trading-roundtable

PACKAGE_SIZE=$(ls -lh "$DEPLOY_DIR/trading-roundtable.tar.gz" | awk '{print $5}')
echo ""
echo "=== Package created: deploy/trading-roundtable.tar.gz ($PACKAGE_SIZE) ==="
echo ""
echo "Upload to ECS with:"
echo "  scp deploy/trading-roundtable.tar.gz root@<ECS_IP>:/root/"
echo ""
