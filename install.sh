#!/bin/bash
# 阿里云ECS自动保活套件 - 一键安装脚本
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  阿里云抢占式实例自动保活套件 安装  ${NC}"
echo -e "${GREEN}========================================${NC}"

if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}请使用 root 用户运行此脚本（或使用 sudo）${NC}"
   exit 1
fi

INSTALL_DIR="/opt/ecs-auto"
LOCK_FILE="/var/run/ecs-auto.lock"

# 检测操作系统
if command -v apt &> /dev/null; then
    PKG_MGR="apt"
elif command -v yum &> /dev/null; then
    PKG_MGR="yum"
else
    echo -e "${RED}不支持的操作系统，请手动安装 python3 和 pip${NC}"
    exit 1
fi

# 1. 检查是否已安装，提供覆盖选项
if [[ -f "$INSTALL_DIR/config.json" ]]; then
    echo -e "${YELLOW}检测到已安装的配置，是否覆盖？(y/N)${NC}"
    read -r OVERWRITE
    if [[ ! "$OVERWRITE" =~ ^[Yy]$ ]]; then
        echo "退出安装"
        exit 0
    fi
fi

# 2. 安装系统依赖
echo -e "${YELLOW}>>> 安装系统依赖...${NC}"
if [[ "$PKG_MGR" == "apt" ]]; then
    apt update -qq && apt install -y python3 python3-pip curl
else
    yum install -y python3 python3-pip curl
fi

# 3. 安装 Python 依赖（移除 bssopenapi）
echo -e "${YELLOW}>>> 安装 Python 依赖...${NC}"
pip3 install -q --break-system-packages flask gunicorn aliyun-python-sdk-core aliyun-python-sdk-ecs requests

# 4. 收集配置信息
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

# 【新增】让用户输入安全启动阈值
DEFAULT_SAFE=$((CDT_LIMIT - 5))
read -p "CDT安全启动阈值(GB, 建议低于上限5-10GB, 默认${DEFAULT_SAFE}): " CDT_SAFE
CDT_SAFE=${CDT_SAFE:-$DEFAULT_SAFE}

read -p "余额预警阈值(CNY, 默认10): " BALANCE_WARN
BALANCE_WARN=${BALANCE_WARN:-10}
read -p "Webhook监听端口(默认8080): " WEBHOOK_PORT
WEBHOOK_PORT=${WEBHOOK_PORT:-8080}

# 5. 创建目录和配置文件
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
    "balance_warn": $BALANCE_WARN,
    "webhook_port": $WEBHOOK_PORT,
    "lock_file": "$LOCK_FILE",
    "alert_interval_minutes": 60,
    "alert_threshold_gb": 10
}
EOF

# 6. 部署脚本文件
echo -e "${YELLOW}>>> 部署脚本文件...${NC}"
if [[ -f "ecs_webhook.py" ]]; then
    cp ecs_webhook.py cdt_auto_stop.py cdt_daily_report.py "$INSTALL_DIR/"
else
    echo -e "${YELLOW}本地未找到脚本，从 GitHub 下载...${NC}"

    retry_curl() {
        local url="$1"
        local output="$2"
        local retries=3
        local count=0
        while [ $count -lt $retries ]; do
            if curl -s -o "$output" "$url"; then
                return 0
            fi
            count=$((count + 1))
            echo -e "${YELLOW}下载失败 ($count/$retries)，等待 2 秒后重试...${NC}"
            sleep 2
        done
        echo -e "${RED}下载 $url 失败，请检查网络后重新运行安装脚本。${NC}"
        return 1
    }

    retry_curl "https://raw.githubusercontent.com/SuPeRK0123/Ali-CDT-Manage/refs/heads/main/ecs_webhook.py" "$INSTALL_DIR/ecs_webhook.py" || exit 1
    retry_curl "https://raw.githubusercontent.com/SuPeRK0123/Ali-CDT-Manage/refs/heads/main/cdt_auto_stop.py" "$INSTALL_DIR/cdt_auto_stop.py" || exit 1
    retry_curl "https://raw.githubusercontent.com/SuPeRK0123/Ali-CDT-Manage/refs/heads/main/cdt_daily_report.py" "$INSTALL_DIR/cdt_daily_report.py" || exit 1
fi
chmod +x "$INSTALL_DIR"/*.py

# 7. 创建 systemd 服务
echo -e "${YELLOW}>>> 创建 systemd 服务...${NC}"

cat > /etc/systemd/system/ecs-webhook.service <<EOF
[Unit]
Description=ECS Auto-Start Webhook
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 -m gunicorn --bind 0.0.0.0:${WEBHOOK_PORT} ecs_webhook:app
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

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

# 8. 启动服务
echo -e "${YELLOW}>>> 启动服务...${NC}"
systemctl daemon-reload
systemctl enable ecs-webhook cdt-stop.timer cdt-report.timer
systemctl restart ecs-webhook
systemctl start cdt-stop.timer cdt-report.timer

# 9. 获取公网 IP
PUBLIC_IP=$(curl -s ifconfig.me || curl -s icanhazip.com || echo "未知")

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}安装完成！${NC}"
echo -e "Webhook 服务已启动，监听端口 ${WEBHOOK_PORT}"
echo -e "请前往阿里云 EventBridge 配置 HTTP 目标为："
echo -e "  http://${PUBLIC_IP}:${WEBHOOK_PORT}/webhook/ecs"
echo -e ""
echo -e "查看日志：journalctl -u ecs-webhook -f"
echo -e "手动控制 API："
echo -e "  开机：curl -X POST http://localhost:${WEBHOOK_PORT}/api/start"
echo -e "  关机：curl -X POST http://localhost:${WEBHOOK_PORT}/api/stop"
echo -e "  状态：curl http://localhost:${WEBHOOK_PORT}/api/status"
echo -e ""
echo -e "Telegram Bot 已启用交互式控制，发送 /help 查看指令列表"
echo -e "${GREEN}========================================${NC}"
