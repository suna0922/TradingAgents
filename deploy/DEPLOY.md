# 选股圆桌 — 阿里云 ECS 部署指南

> 适用配置：ECS e0c1ma.large (2vCPU + 2GB) 及以上，Ubuntu 22.04 LTS

---

## 一、买服务器（已完成请跳过）

| 配置项 | 推荐值 | 说明 |
|--------|--------|------|
| 实例 | `ecs.e0c1ma.xlarge` (2C4G) | 首选，4GB内存更从容 |
| 最低 | `ecs.e0c1ma.large` (2C2G) | 可运行，uvicorn 只开 1 worker |
| 系统盘 | 40GB SSD | 代码 + 数据 + 日志足够 |
| 操作系统 | Ubuntu 22.04 LTS (64位) | 社区支持好，Python 3 生态成熟 |
| 安全组 | 放行 80/443/8000/22 | 22(SSH), 80/443(HTTP), 8000(服务) |
| 公网 IP | 分配弹性公网 IP | 访问网页必需 |

---

## 二、本地打包代码

在项目根目录执行：

```bash
bash deploy/package.sh
```

产出 `deploy/trading-roundtable.tar.gz`，上传到 ECS：

```bash
scp deploy/trading-roundtable.tar.gz root@<ECS公网IP>:/root/
```

---

## 三、ECS 上执行安装（SSH 登录后）

```bash
ssh root@<ECS公网IP>
```

```bash
# 1. 解压
cd /root && tar xzf trading-roundtable.tar.gz
mv trading-roundtable /opt/
cd /opt/trading-roundtable

# 2. 执行安装脚本
bash deploy/ecs-setup.sh
```

脚本会自动完成：安装依赖、创建 Python venv、安装项目包、配置 systemd 服务、启动。

---

## 四、配置环境变量（关键）

```bash
sudo nano /opt/trading-roundtable/.env
```

写入：

```env
DEEPSEEK_API_KEY=sk-your-api-key-here
# 可选：其他 LLM 配置
# DEEPSEEK_BASE_URL=https://api.deepseek.com
```

保存后重启服务：

```bash
sudo systemctl restart trading-roundtable
sudo systemctl status trading-roundtable
```

---

## 五、验证服务

```bash
# 本地测试
curl http://127.0.0.1:8000/api/health
# 应返回 {"status":"ok"}

curl "http://127.0.0.1:8000/api/stock/600519/info"
# 应返回股票名称等基本信息
```

浏览器访问：`http://<ECS公网IP>:8000`

---

## 六、Nginx 反向代理（推荐，解决跨域 + 端口问题）

```bash
sudo apt install -y nginx
sudo cp deploy/nginx.conf /etc/nginx/sites-available/trading-roundtable
sudo ln -s /etc/nginx/sites-available/trading-roundtable /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

浏览器访问：`http://<ECS公网IP>`（默认80端口）

---

## 七、域名 + HTTPS（可选）

如果你有域名，可配置 certbot 自动 SSL：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

---

## 八、日常运维

```bash
# 查看状态
sudo systemctl status trading-roundtable

# 查看日志
sudo journalctl -u trading-roundtable -f

# 重启
sudo systemctl restart trading-roundtable

# 停止
sudo systemctl stop trading-roundtable
```

---

## 九、内存优化（2GB 实例必读）

如果只有 2GB 内存，建议：

```bash
sudo nano /etc/systemd/system/trading-roundtable.service
```

修改：
```ini
Environment="UVICORN_WORKERS=1"
```

重启：
```bash
sudo systemctl daemon-reload && sudo systemctl restart trading-roundtable
```

---

## 十、故障排查

| 问题 | 排查 |
|------|------|
| 端口不通 | `sudo ufw status` / 阿里云安全组检查 |
| 服务起不来 | `sudo journalctl -u trading-roundtable -n 50` |
| 缺少 API key | `cat /opt/trading-roundtable/.env` |
| 依赖安装失败 | `cd /opt/trading-roundtable && uv sync` 看报错 |
| 磁盘满 | `df -h` 检查，清理日志 `journalctl --vacuum-time=7d` |
