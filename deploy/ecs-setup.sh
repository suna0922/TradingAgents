#!/usr/bin/env bash
# ECS 一键安装脚本 — 在 Ubuntu 22.04 LTS 上执行
# 前置：已将项目解压到 /opt/trading-roundtable
set -e

PROJECT_DIR="/opt/trading-roundtable"
cd "$PROJECT_DIR"

echo "================================"
echo "  选股圆桌 ECS 部署脚本"
echo "  OS: $(lsb_release -ds 2>/dev/null || cat /etc/os-release | grep PRETTY | cut -d= -f2 | tr -d '\"')"
echo "================================"

# ---------- 1. 系统更新 & 基础工具 ----------
echo "[1/8] 安装系统依赖..."
apt-get update -y
apt-get install -y --no-install-recommends \
    curl ca-certificates git \
    python3 python3-pip python3-venv \
    libffi-dev libssl-dev build-essential

# ---------- 2. 安装 uv (Python 包管理器) ----------
echo "[2/8] 安装 uv..."
if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi
uv --version

# ---------- 3. 创建 Python 3.13 虚拟环境 ----------
echo "[3/8] 创建 Python 3.13 虚拟环境..."
# uv 会自动安装 Python 3.13
uv python install 3.13 --quiet 2>/dev/null || true
uv venv --python python3.13 .venv

echo "[4/8] 安装项目依赖..."
uv sync --all-extras 2>/dev/null || uv pip install -e "." 2>/dev/null || {
    # 如果 uv sync 失败，手动安装核心依赖
    echo "  uv sync failed, installing manually..."
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install \
        fastapi uvicorn websockets python-multipart \
        akshare baostock stockstats pandas numpy \
        pyyaml httpx aiohttp
    # 重新安装项目本身
    .venv/bin/pip install -e .
}

# ---------- 4. 安装额外后端依赖 ----------
echo "[5/8] 安装后端扩展..."
.venv/bin/pip install --quiet pyarrow 2>/dev/null || true  # akshare 可能用到

# ---------- 5. 环境变量检查 ----------
echo "[6/8] 检查环境变量..."
if [ -f .env ]; then
    source .env
fi
if [ -z "$DEEPSEEK_API_KEY" ]; then
    echo ""
    echo "⚠️  警告：未检测到 DEEPSEEK_API_KEY"
    echo "    请编辑 /opt/trading-roundtable/.env 填入你的 API Key"
    echo "    然后执行: sudo systemctl restart trading-roundtable"
    echo ""
fi

# ---------- 6. 测试启动 ----------
echo "[7/8] 测试启动..."
.venv/bin/python -c "
import sys
sys.path.insert(0, '.')
from web_app.backend.main import app
print('✅ FastAPI app loads OK')
"

# 测试一次 baostock 连接（预热）
.venv/bin/python -c "
import sys; sys.path.insert(0, '.')
try:
    import baostock as bs
    lg = bs.login()
    if lg.error_code == '0':
        print('✅ baostock login OK')
        bs.logout()
    else:
        print('⚠️ baostock login returned:', lg.error_msg)
except Exception as e:
    print('⚠️ baostock not available:', e)
" 2>/dev/null || true

# ---------- 7. systemd 服务 ----------
echo "[8/8] 配置 systemd 服务..."
cat > /etc/systemd/system/trading-roundtable.service << 'EOF'
[Unit]
Description=选股圆桌 Web 服务
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/trading-roundtable
Environment=PATH=/opt/trading-roundtable/.venv/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=-/opt/trading-roundtable/.env
Environment=PYTHONPATH=/opt/trading-roundtable
Environment=UVICORN_WORKERS=1
ExecStart=/opt/trading-roundtable/.venv/bin/uvicorn web_app.backend.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable trading-roundtable
systemctl start trading-roundtable

sleep 3

# ---------- 8. 验证 ----------
echo ""
echo "================================"
echo "  部署验证"
echo "================================"
if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    echo "✅ 服务运行正常: http://127.0.0.1:8000/api/health"
else
    echo "❌ 服务启动失败，检查日志:"
    echo "   journalctl -u trading-roundtable -n 50"
fi

# 获取公网 IP
PUBLIC_IP=$(curl -sf http://metadata.aliyuncs.com/latest/meta-data/eipv4 2>/dev/null || \
            curl -sf http://metadata.aliyuncs.com/latest/meta-data/public-ipv4 2>/dev/null || \
            curl -sf https://api.ipify.org 2>/dev/null || \
            echo "<your-ecs-ip>")

echo ""
echo "🚀 部署完成！"
echo ""
echo "  本地访问:  http://127.0.0.1:8000"
echo "  公网访问: http://$PUBLIC_IP:8000"
echo ""
echo "  查看日志:  sudo journalctl -u trading-roundtable -f"
echo "  重启服务:  sudo systemctl restart trading-roundtable"
echo "  查看状态:  sudo systemctl status trading-roundtable"
echo ""
echo "  配置 API Key:"
echo "    sudo nano /opt/trading-roundtable/.env"
echo "    # 添加: DEEPSEEK_API_KEY=sk-your-key"
echo "    sudo systemctl restart trading-roundtable"
echo ""
