#!/bin/bash
# 阿里云ECS自动保活套件 - 一键安装脚本 v2
# 架构：Nginx (公网 8080) → Gunicorn (Unix Socket, gthread) → Flask
# Telegram 监听器作为独立 systemd 服务运行
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ==================== 常量 ====================
INSTALL_DIR="/opt/ecs-auto"
LOCK_FILE="/var/run/ecs-auto.lock"
SOCKET_PATH="/run/ecs-webhook.sock"
NGINX_CONF="/etc/nginx/conf.d/ecs-webhook.conf"
GH_BASE="https://raw.githubusercontent.com/SuPeRK0123/Ali-CDT-Manage/refs/heads/main"
PY_FILES=("ecs_webhook.py" "cdt_auto_stop.py" "cdt_daily_report.py" "tg_bot.py")
SERVICE_FILES=(
    "/etc/systemd/system/ecs-webhook.service"
    "/etc/systemd/system/ecs-tgbot.service"
    "/etc/systemd/system/cdt-stop.service"
    "/etc/systemd/system/cdt-stop.timer"
    "/etc/systemd/system/cdt-report.service"
    "/etc/systemd/system/cdt-report.timer"
)

# ==================== 工具函数 ====================
print_banner() {
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  阿里云抢占式实例自动保活套件 安装  ${NC}"
    echo -e "${GREEN}========================================${NC}"
}

print_help() {
    cat <<EOF
用法: sudo ./install.sh [选项]

选项:
  -h, --help        显示帮助
  -u, --uninstall   卸载已安装的服务和配置
EOF
}

# 端口是否可用（被 nginx 占用视为可用，因为我们将复用 nginx）
check_port_available() {
    local port=$1
    local output
    output=$(ss -tlnp 2>/dev/null | grep ":$port " || true)
    [[ -z "$output" ]] && return 0
    echo "$output" | grep -q "nginx" && return 0
    return 1
}

retry_curl() {
    local url="$1"
    local output="$2"
    local retries=3
    local count=0
    while [ $count -lt $retries ]; do
        if curl -fsSL -o "$output" "$url"; then
            return 0
        fi
        count=$((count + 1))
        echo -e "${YELLOW}下载失败 ($count/$retries)，等待 2 秒后重试...${NC}"
        sleep 2
    done
    echo -e "${RED}下载 $url 失败，请检查网络后重新运行安装脚本。${NC}"
    return 1
}

# ==================== 卸载 ====================
do_uninstall() {
    echo -e "${YELLOW}>>> 卸载 Ali-CDT-Manage...${NC}"

    systemctl stop ecs-webhook ecs-tgbot cdt-stop.timer cdt-report.timer 2>/dev/null || true
    systemctl disable ecs-webhook ecs-tgbot cdt-stop.timer cdt-report.timer 2>/dev/null || true

    for f in "${SERVICE_FILES[@]}"; do
        rm -f "$f"
    done
    systemctl daemon-reload

    if [[ -f "$NGINX_CONF" ]]; then
        rm -f "$NGINX_CONF"
        if command -v nginx &>/dev/null && nginx -t &>/dev/null; then
            systemctl reload nginx 2>/dev/null || true
        fi
    fi

    echo -e "${YELLOW}是否删除配置和数据目录 $INSTALL_DIR？(y/N)${NC}"
    read -r DEL_DATA || true
    if [[ "$DEL_DATA" =~ ^[Yy]$ ]]; then
        rm -rf "$INSTALL_DIR"
        rm -f "$SOCKET_PATH" /var/run/cdt_history.json
        echo -e "${GREEN}数据已删除${NC}"
    fi

    echo -e "${GREEN}卸载完成${NC}"
}

# ==================== 参数解析 ====================
case "${1:-}" in
    -h|--help)
        print_banner
        print_help
        exit 0
        ;;
    -u|--uninstall)
        if [[ $EUID -ne 0 ]]; then
            echo -e "${RED}请使用 root 用户运行此脚本${NC}"
            exit 1
        fi
        do_uninstall
        exit 0
        ;;
esac

print_banner

# ==================== 前置检查 ====================
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}请使用 root 用户运行此脚本（或使用 sudo）${NC}"
    exit 1
fi

if command -v apt &> /dev/null; then
    PKG_MGR="apt"
elif command -v yum &> /dev/null; then
    PKG_MGR="yum"
else
    echo -e "${RED}不支持的操作系统，请手动安装 python3、pip 和 nginx${NC}"
    exit 1
fi

# ==================== 覆盖安装处理 ====================
if [[ -f "$INSTALL_DIR/config.json" ]]; then
    echo -e "${YELLOW}检测到已安装的配置，是否覆盖？(y/N)${NC}"
    read -r OVERWRITE || true
    if [[ ! "$OVERWRITE" =~ ^[Yy]$ ]]; then
        echo "退出安装"
        exit 0
    fi
    echo -e "${YELLOW}>>> 停止旧服务，避免端口/Socket 冲突...${NC}"
    systemctl stop ecs-webhook ecs-tgbot 2>/dev/null || true
    systemctl disable ecs-webhook ecs-tgbot 2>/dev/null || true
    rm -f "$SOCKET_PATH"
fi

# ==================== 安装系统依赖 ====================
echo -e "${YELLOW}>>> 安装系统依赖 (python3, pip, nginx, curl)...${NC}"
if [[ "$PKG_MGR" == "apt" ]]; then
    apt update -qq
    apt install -y python3 python3-pip nginx curl
else
    yum install -y python3 python3-pip nginx curl
fi

# Python 版本检查（>= 3.8）
PY_OK=$(python3 -c 'import sys; print(int(sys.version_info >= (3, 8)))')
if [[ "$PY_OK" != "1" ]]; then
    PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    echo -e "${RED}检测到 Python $PY_VER，需要 3.8 或更高版本。${NC}"
    exit 1
fi

# ==================== 安装 Python 依赖 ====================
echo -e "${YELLOW}>>> 安装 Python 依赖...${NC}"
pip3 install -q --break-system-packages \
    flask gunicorn requests \
    aliyun-python-sdk-core aliyun-python-sdk-ecs

# ==================== 收集配置 ====================
echo -e "${YELLOW}>>> 请输入配置信息：${NC}"
read -p "阿里云 AccessKey ID: " ACCESS_KEY_ID
read -sp "阿里云 AccessKey Secret: " ACCESS_KEY_SECRET
echo
read -p "地域 (如 cn-hongkong): " REGION_ID
read -p "ECS 实例 ID: " ECS_INSTANCE_ID
read -p "Telegram Bot Token: " TG_BOT_TOKEN
read -p "Telegram Chat ID (用户ID或群组ID): " TG_CHAT_ID
read -p "CDT免费额度上限(GB, 默认200): " CDT_LIMIT
CDT_LIMIT=${CDT_LIMIT:-200}

DEFAULT_SAFE=$((CDT_LIMIT - 5))
read -p "CDT安全启动阈值(GB, 建议低于上限5-10GB, 默认${DEFAULT_SAFE}): " CDT_SAFE
CDT_SAFE=${CDT_SAFE:-$DEFAULT_SAFE}

read -p "Webhook监听端口(默认8080, 由Nginx对外监听): " WEBHOOK_PORT
WEBHOOK_PORT=${WEBHOOK_PORT:-8080}

# ==================== 端口冲突检查 ====================
echo -e "${YELLOW}>>> 检查端口 ${WEBHOOK_PORT}...${NC}"
if ! check_port_available "$WEBHOOK_PORT"; then
    echo -e "${RED}端口 ${WEBHOOK_PORT} 已被其他程序占用，请选择其他端口或停止该程序。${NC}"
    echo -e "${YELLOW}当前占用情况：${NC}"
    ss -tlnp | grep ":$WEBHOOK_PORT " || true
    exit 1
fi

# ==================== 创建目录和配置 ====================
mkdir -p "$INSTALL_DIR"

cat > "$INSTALL_DIR/config.json" <<EOF
{
    "access_key_id": "$ACCESS_KEY_ID",
    "access_key_secret": "$ACCESS_KEY_SECRET",
    "region_id": "$REGION_ID",
    "ecs_instance_id": "$ECS_INSTANCE_ID",
    "tg_bot_token": "$TG_BOT_TOKEN",
    "tg_chat_id": "$TG_CHAT_ID",
    "cdt_limit_gb": $CDT_LIMIT,
    "cdt_safe_gb": $CDT_SAFE,
    "webhook_port": $WEBHOOK_PORT,
    "lock_file": "$LOCK_FILE",
    "alert_interval_minutes": 60,
    "alert_threshold_gb": 10
}
EOF
chmod 600 "$INSTALL_DIR/config.json"

# ==================== 部署脚本文件 ====================
echo -e "${YELLOW}>>> 部署脚本文件...${NC}"
ALL_LOCAL=1
for f in "${PY_FILES[@]}"; do
    [[ -f "$f" ]] || ALL_LOCAL=0
done

if [[ $ALL_LOCAL -eq 1 ]]; then
    cp "${PY_FILES[@]}" "$INSTALL_DIR/"
else
    echo -e "${YELLOW}本地缺少部分脚本，从 GitHub 下载...${NC}"
    for f in "${PY_FILES[@]}"; do
        if [[ -f "$f" ]]; then
            cp "$f" "$INSTALL_DIR/$f"
        else
            retry_curl "$GH_BASE/$f" "$INSTALL_DIR/$f" || exit 1
        fi
    done
fi
chmod +x "$INSTALL_DIR"/*.py

# ==================== 创建 systemd 服务 ====================
echo -e "${YELLOW}>>> 创建 systemd 服务...${NC}"

# Webhook 服务：Gunicorn (gthread) + Unix Socket
cat > /etc/systemd/system/ecs-webhook.service <<EOF
[Unit]
Description=ECS Auto-Start Webhook (Gunicorn + Unix Socket)
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 -m gunicorn \\
    --workers 1 \\
    --threads 4 \\
    --worker-class gthread \\
    --timeout 120 \\
    --bind unix:$SOCKET_PATH \\
    --umask 0000 \\
    --access-logfile - \\
    --error-logfile - \\
    ecs_webhook:app
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Telegram 监听服务：独立进程
cat > /etc/systemd/system/ecs-tgbot.service <<EOF
[Unit]
Description=ECS Telegram Bot Listener
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/tg_bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# CDT 超额关机
cat > /etc/systemd/system/cdt-stop.service <<EOF
[Unit]
Description=CDT Auto Stop Check

[Service]
Type=oneshot
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/cdt_auto_stop.py
StandardOutput=journal
StandardError=journal
EOF

cat > /etc/systemd/system/cdt-stop.timer <<EOF
[Unit]
Description=CDT Stop Timer (random 20-40min)

[Timer]
OnBootSec=5min
OnUnitActiveSec=20min
RandomizedDelaySec=20min

[Install]
WantedBy=timers.target
EOF

# CDT 每日报告
cat > /etc/systemd/system/cdt-report.service <<EOF
[Unit]
Description=CDT Daily Report

[Service]
Type=oneshot
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/cdt_daily_report.py
StandardOutput=journal
StandardError=journal
EOF

cat > /etc/systemd/system/cdt-report.timer <<EOF
[Unit]
Description=CDT Daily Report Timer

[Timer]
OnCalendar=*-*-* 09:00:00
OnCalendar=*-*-* 20:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

# ==================== 写入 Nginx 配置 ====================
echo -e "${YELLOW}>>> 写入 Nginx 配置...${NC}"
cat > "$NGINX_CONF" <<EOF
# Ali-CDT-Manage Webhook 反向代理
# 只放行 /webhook/ecs 的 POST 请求，其余路径直接 444 断连
server {
    listen ${WEBHOOK_PORT};
    server_name _;

    location = /webhook/ecs {
        limit_except POST { deny all; }

        proxy_pass http://unix:$SOCKET_PATH;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;

        proxy_connect_timeout 5s;
        proxy_read_timeout 10s;
    }

    location / {
        return 444;
    }
}
EOF

# Nginx 配置语法校验
if ! nginx -t &>/dev/null; then
    echo -e "${RED}Nginx 配置校验失败，请检查：${NC}"
    nginx -t
    exit 1
fi

# ==================== 启动服务 ====================
echo -e "${YELLOW}>>> 启动服务...${NC}"
systemctl daemon-reload

# 启动 Nginx
if systemctl is-active --quiet nginx; then
    systemctl reload nginx
else
    systemctl enable --now nginx
fi

# 启动业务服务
systemctl enable ecs-webhook ecs-tgbot cdt-stop.timer cdt-report.timer
systemctl restart ecs-webhook ecs-tgbot
systemctl start cdt-stop.timer cdt-report.timer

# ==================== 完成提示 ====================
PUBLIC_IP=$(curl -s ifconfig.me || curl -s icanhazip.com || echo "未知")

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}安装完成！${NC}"
echo -e ""
echo -e "架构：Nginx (公网:${WEBHOOK_PORT}) → Gunicorn (Unix Socket) → Flask"
echo -e "      Telegram 监听器：独立服务 ecs-tgbot"
echo -e ""
echo -e "请前往阿里云云监控配置事件订阅的 Webhook 地址为："
echo -e "  ${GREEN}http://${PUBLIC_IP}:${WEBHOOK_PORT}/webhook/ecs${NC}"
echo -e ""
echo -e "查看日志："
echo -e "  journalctl -u ecs-webhook -f   # Webhook 服务"
echo -e "  journalctl -u ecs-tgbot -f     # Telegram 监听"
echo -e ""
echo -e "手动控制 API（本地）："
echo -e "  开机：curl -X POST http://localhost:${WEBHOOK_PORT}/api/start"
echo -e "  关机：curl -X POST http://localhost:${WEBHOOK_PORT}/api/stop"
echo -e "  状态：curl http://localhost:${WEBHOOK_PORT}/api/status"
echo -e ""
echo -e "Telegram Bot 已启用交互式控制，发送 /help 查看指令列表"
echo -e ""
echo -e "卸载：sudo ./install.sh --uninstall"
echo -e "${GREEN}========================================${NC}"
