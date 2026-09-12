# Ali-CDT-Manage

阿里云抢占式实例自动保活 + CDT 流量管理套件

## 功能

- **⚡ 自动开机**：实例被关机时，通过云监控事件订阅触发自动开机（带 CDT 前置检查）
- **🛑 超额关机**：每 20-40 分钟随机检查 CDT 流量，超限自动执行节省停机（StopCharging）
- **⚠️ 流量突增告警**：短时流量突增时主动推送 Telegram 告警
- **🤖 Telegram 交互**：发送 `/start`、`/stop`、`/status`、`/cdt` 远程操控
- **📊 每日报告**：定时推送 CDT 用量、公网 IP
- **🔒 防重复触发**：文件锁防止并发操作
- **🛡 Nginx 反代**：只放行 webhook 路径，其余请求直接断连，避免公网扫描
- **🧩 进程解耦**：Webhook 服务与 Telegram 监听独立运行，互不干扰

---

## 架构

```
公网/阿里云事件推送
       │
       ▼
┌───────────────────────┐
│  Nginx (0.0.0.0:8080) │
│   /webhook/ecs  → 转发 │──→ Gunicorn (Unix Socket, gthread) ──→ Flask 应用
│   其他路径 → 444 断连  │
└───────────────────────┘

独立服务：
  ecs-tgbot.service → tg_bot.py → Telegram 长轮询
```

**各组件职责**：

| 组件 | 作用 |
|------|------|
| **Nginx** | 安全屏障 + 路径白名单，阻断扫描器；对后端做连接缓冲 |
| **Gunicorn (gthread)** | 管理 Flask 应用进程；多线程 worker 抵御慢连接攻击 |
| **Flask** | 业务逻辑（事件解析、ECS API 调用、路由） |
| **ecs-tgbot 独立进程** | Telegram 长轮询，不受 Webhook 服务重启影响 |

---

## 快速开始

### 1. 一键安装

```bash
wget https://raw.githubusercontent.com/SuPeRK0123/Ali-CDT-Manage/refs/heads/main/install.sh
chmod +x install.sh
sudo ./install.sh
```

按提示输入：
- AccessKey ID / Secret
- 地域（如 `cn-hongkong`）
- ECS 实例 ID
- Telegram Bot Token 和 Chat ID
- CDT 额度（默认 200 GB）
- CDT 安全启动阈值（默认比额度低 5 GB）
- Webhook 监听端口（默认 8080，由 Nginx 对外监听）

安装脚本会自动完成：
- 安装系统依赖（Python 3、pip、nginx）
- 安装 Python 依赖（Flask、Gunicorn、阿里云 SDK、requests）
- 生成配置和部署脚本到 `/opt/ecs-auto/`
- 创建 systemd 服务（webhook、tgbot、CDT 定时任务）
- 写入 Nginx 配置 `/etc/nginx/conf.d/ecs-webhook.conf`
- 启动并 enable 所有服务

### 2. 配置阿里云云监控事件订阅

1. 登录 [云监控控制台](https://cloudmonitor.console.alibabacloud.com/) → **事件中心** → **事件订阅**
2. 点击 **创建订阅策略**，按以下步骤配置：

**① 基本信息**：填写策略名称（如 `ECS自动保活`）

**② 报警订阅**：
- 订阅类型：**系统事件**
- 产品：**云服务器 ECS**
- 事件类型：**状态通知**
- 事件名称：**实例状态改变通知**
- 事件等级：全部勾选
- 事件资源：留空（由脚本过滤）
- 事件内容：留空

**③ 合并降噪**：选择 **直接触发，不抑制**

**④ 推送与集成**：
- 点击 **添加渠道**，创建 Webhook 推送渠道：
  - 渠道名称：`ECS状态变化Webhook`
  - 目标类型：`Webhook`
  - 请求方法：`POST`
  - 数据格式：`JSON`
  - 地址：`http://<您的VPS公网IP>:8080/webhook/ecs`
  - 自定义 Header：无需添加
  - 签名混淆字符串：留空
  - 通知模板：可选
- 在推送渠道列表中选择刚创建的渠道

3. 点击 **提交** 完成创建

---

## 手动控制 API

> 以下 API 均为本地调用（Nginx 只对外放行 `/webhook/ecs` 路径）

```bash
curl -X POST http://localhost:8080/api/start   # 开机（检查CDT）
curl -X POST http://localhost:8080/api/stop    # 关机（节省停机）
curl http://localhost:8080/api/status          # 查询状态 + CDT
curl http://localhost:8080/health              # 健康检查
```

> 如果 Nginx 已启用本地限制，可用 `127.0.0.1:8080` 代替 `localhost`。

---

## Telegram Bot 命令

向 Bot 发送以下指令：

| 命令 | 功能 |
|------|------|
| `/start` | 手动开机（自动检查CDT） |
| `/stop` | 手动关机（节省停机） |
| `/status` | 查询实例状态 + CDT 流量 |
| `/cdt` | 仅查询当前 CDT 流量 |
| `/help` | 显示帮助 |

---

## 查看日志

```bash
journalctl -u ecs-webhook -f        # Webhook 服务（Nginx → Gunicorn）
journalctl -u ecs-tgbot -f          # Telegram 监听服务
journalctl -u cdt-stop.service -f   # CDT 超额关机执行日志
journalctl -u cdt-report.service -f # 每日报告执行日志
```

Nginx 日志：

```bash
tail -f /var/log/nginx/access.log   # 访问日志
tail -f /var/log/nginx/error.log    # 错误日志
```

---

## 卸载

```bash
sudo ./install.sh --uninstall
```

卸载脚本会：

- 停止并 disable 所有相关 systemd 服务
- 删除 systemd 单元文件
- 删除 Nginx 配置并 reload
- 询问是否删除 `/opt/ecs-auto/`（配置和脚本）

> 卸载不会删除 nginx 软件包本身，也不会卸载 Python 依赖，避免影响系统上其他服务。

---

## RAM 权限要求

脚本需要以下权限，建议授予对应系统策略：

| 权限 | 策略名称 |
|------|----------|
| ECS 开关机、查询状态 | `AliyunECSFullAccess` |
| CDT 流量查询 | `AliyunCDTFullAccess` |

> 如追求最小权限，可自建自定义策略，仅授予 `StartInstances`、`StopInstances`、`DescribeInstances`、`DescribeDisks`、`ListCdtInternetTraffic` 等 Action。

---

## 配置文件

安装后配置文件位于 `/opt/ecs-auto/config.json`，权限 `600`：

```json
{
    "access_key_id": "...",
    "access_key_secret": "...",
    "region_id": "cn-hongkong",
    "ecs_instance_id": "i-xxxxxxxx",
    "tg_bot_token": "...",
    "tg_chat_id": "...",
    "cdt_limit_gb": 200,
    "cdt_safe_gb": 195,
    "webhook_port": 8080,
    "lock_file": "/var/run/ecs-auto.lock",
    "alert_interval_minutes": 60,
    "alert_threshold_gb": 10
}
```

修改后重启相关服务生效：

```bash
systemctl restart ecs-webhook ecs-tgbot
```

---

## 目录结构

```
/opt/ecs-auto/
├── config.json            # 配置文件
├── ecs_webhook.py         # 主程序（Flask 应用 + 业务逻辑）
├── tg_bot.py              # Telegram 独立监听服务入口
├── cdt_auto_stop.py       # CDT 超额关机
└── cdt_daily_report.py    # 每日报告

/etc/systemd/system/
├── ecs-webhook.service    # Gunicorn 服务
├── ecs-tgbot.service      # Telegram 监听服务
├── cdt-stop.service       # CDT 检查（oneshot）
├── cdt-stop.timer         # 每 20-40 分钟触发
├── cdt-report.service     # 每日报告
└── cdt-report.timer       # 每天 09:00 / 20:00 触发

/etc/nginx/conf.d/
└── ecs-webhook.conf       # Nginx 反向代理配置

运行时文件：
/run/ecs-webhook.sock      # Gunicorn Unix Socket（权限 777）
/var/run/ecs-auto.lock     # 文件锁
/var/run/cdt_history.json  # CDT 流量历史（用于突增检测）
```

---

## 常见问题

### Webhook 服务日志中出现 `WORKER TIMEOUT` 与 `SIGKILL`

**原因**：旧版本使用 Gunicorn `sync` worker，worker 阻塞在 `sock.recv` 等待恶意慢连接，触发默认 30 秒超时被杀。

**解决**：v2 已改为 `gthread` worker，并将 Nginx 前置作为安全屏障。若仍遇到，请确认：

```bash
systemctl cat ecs-webhook | grep -A2 "ExecStart"
# 应包含 --worker-class gthread
```

### Telegram Bot 无响应或反复重启

**原因**：可能是 Token 配置错误、VPS 无法访问 Telegram API，或多个进程同时长轮询。

**排查**：

```bash
journalctl -u ecs-tgbot -n 50
# 确认只有 ecs-tgbot 服务在长轮询
ps aux | grep tg_bot
```

### Nginx 无法启动或报 `Address already in use`

**原因**：端口 8080 被其他程序占用。

**排查**：

```bash
ss -tlnp | grep 8080
systemctl status nginx
nginx -t
```

### 事件未触发自动开机

- 确认云监控订阅策略中事件名称、推送渠道 URL 配置正确
- 在云监控控制台使用"事件调试"功能发送测试事件，验证 Webhook 连通性
- 查看 VPS 日志：

```bash
journalctl -u ecs-webhook -f
```

- 本地手动模拟推送：

```bash
curl -X POST http://localhost:8080/webhook/ecs \
  -H 'Content-Type: application/json' \
  -d '{"resourceId":"i-xxxxxxxx","state":"Stopped"}'
```

### 如何验证 Nginx 只放行 webhook 路径

```bash
# 应返回 {"code":0,"msg":"ok"}
curl -X POST http://localhost:8080/webhook/ecs \
  -H 'Content-Type: application/json' -d '{}'

# 应返回 "Empty reply from server"（即 444 断连）
curl -v http://localhost:8080/anything
```

---

## 参考资料

- [使用系统事件报警回调（推荐）](https://help.aliyun.com/zh/cms/cloudmonitor-1-0/user-guide/configure-callbacks-for-system-event-triggered-alerts-recommended) - 云监控 Webhook 配置官方文档
- [管理事件订阅（推荐）](https://help.aliyun.com/zh/cms/cloudmonitor-1-0/user-guide/manage-notification-policies) - 事件订阅管理指南
- [Gunicorn Design](https://docs.gunicorn.org/en/stable/design.html) - worker 类型对比与选型建议
