# Ali-CDT-Manage

阿里云抢占式实例自动保活 + CDT 流量管理套件

## 功能

- **⚡ 自动开机**：实例被关机时，通过 EventBridge 事件触发自动开机（带 CDT 前置检查）
- **🛑 超额关机**：每 30 分钟检查 CDT 流量，超限自动执行节省停机（StopCharging）
- **🤖 Telegram 交互**：发送 `/start`、`/stop`、`/status`、`/cdt` 远程操控
- **📊 每日报告**：定时推送 CDT 用量、账户余额、公网 IP
- **🔒 防重复触发**：文件锁防止并发操作

---

## 快速开始

### 1. 一键安装

```
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
- 余额预警阈值（默认 10 CNY）
- Webhook 监听端口（默认 8080）

安装完成后，服务自动启动。

### 2. 配置阿里云 EventBridge（事件推送）

1. 登录 [EventBridge 控制台](https://eventbridge.console.aliyun.com/) → 进入 **default** 事件总线
2. 创建规则：
   - **事件源**：`acs.ecs`
   - **事件类型**：`ecs:Instance:StateChange`
3. 添加目标：
   - **服务类型**：HTTP
   - **URL**：`http://<您的VPS公网IP>:8080/webhook/ecs`（端口与安装时一致）
   - **网络类型**：公网
   - **Body**：完整事件
4. 保存规则

---

## 手动控制 API

```bash
curl -X POST http://localhost:8080/api/start   # 开机（检查CDT）
curl -X POST http://localhost:8080/api/stop    # 关机（节省停机）
curl http://localhost:8080/api/status          # 查询状态 + CDT
curl http://localhost:8080/health              # 健康检查
```

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
journalctl -u ecs-webhook -f      # Webhook 服务实时日志
journalctl -u cdt-stop.service -f # 超额关机执行日志
journalctl -u cdt-report.service -f # 每日报告执行日志
```

---

## RAM 权限要求

脚本需要以下权限，建议授予对应系统策略：

| 权限 | 策略名称 |
|------|----------|
| ECS 开关机、查询状态 | `AliyunECSFullAccess` |
| CDT 流量查询 | `AliyunCDTFullAccess` |
| 账户余额查询（可选） | `AliyunBSSReadOnlyAccess` |

> 按最小权限原则，也可自定义策略仅包含 `ecs:StartInstances`、`ecs:StopInstances`、`ecs:DescribeInstances`、`cdt:ListCdtInternetTraffic`、`bssapi:QueryAccountBalance`。

---

## 配置文件

安装后配置文件位于 `/opt/ecs-auto/config.json`，修改后重启服务生效：

```bash
systemctl restart ecs-webhook
```

---

## 常见问题

### Webhook 服务无法启动
- 检查端口是否被占用：`ss -tlnp | grep 8080`
- 查看详细日志：`journalctl -u ecs-webhook -n 50`

### Telegram Bot 无响应
- 确认 Token 和 Chat ID 正确
- 检查 VPS 能否访问 Telegram API：`curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates"`

### 事件未触发自动开机
- 确认 EventBridge 规则中 URL、网络类型、Body 配置正确
- 在 EventBridge 控制台使用“事件追踪”查看投递记录
