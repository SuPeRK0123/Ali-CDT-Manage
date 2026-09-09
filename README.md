# Ali-CDT-Manage

阿里云抢占式实例自动保活 + CDT 流量管理套件

## 功能

- **⚡ 自动开机**：实例被关机时，通过云监控事件订阅触发自动开机（带 CDT 前置检查）
- **🛑 超额关机**：每 20-40 分钟随机检查 CDT 流量，超限自动执行节省停机（StopCharging）
- **⚠️ 流量突增告警**：短时流量突增时主动推送 Telegram 告警
- **🤖 Telegram 交互**：发送 `/start`、`/stop`、`/status`、`/cdt` 远程操控
- **📊 每日报告**：定时推送 CDT 用量、公网 IP（不含余额）
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
- 确认云监控订阅策略中事件名称、推送渠道 URL 配置正确
- 在云监控控制台使用“事件调试”功能发送测试事件，验证 Webhook 连通性
- 查看 VPS 日志：`journalctl -u ecs-webhook -f`
