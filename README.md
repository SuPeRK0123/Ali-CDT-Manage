# Ali-CDT-Manage

阿里云抢占式实例自动保活 + CDT 流量管理套件

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.6%2B-blue.svg)](https://www.python.org/)

## 📌 项目简介

本套件用于保障阿里云国际站**抢占式实例（Spot Instance）** 的高可用性，同时确保 **CDT（云数据传输）** 公网流量始终维持在免费额度（200GB）之内。

| 功能 | 说明 |
|------|------|
| **⚡ 秒级自动开机** | 实例因资源紧张被关机时，通过 EventBridge 事件触发自动开机 |
| **🛑 CDT 超额关机** | 每 30 分钟检查一次 CDT 流量，超限则自动执行节省停机 |
| **🤖 Telegram 交互控制** | 通过 Bot 远程执行 `/start`、`/stop`、`/status` 等指令 |
| **📊 每日流量报告** | 每天定时推送 CDT 用量、账户余额、公网 IP 等信息 |
| **🔒 防重复触发** | 文件锁机制防止并发操作导致 API 冲突 |
| **🔄 开机重试** | 开机失败自动重试 5 次，间隔 10 秒，避免 API 限频 |

## 📂 文件清单

| 文件 | 说明 |
|------|------|
| `install.sh` | 一键安装脚本，自动部署所有服务 |
| `ecs_webhook.py` | Webhook 服务（事件驱动开机 + Telegram 交互控制） |
| `cdt_auto_stop.py` | CDT 超额自动关机（由 systemd-timer 触发） |
| `cdt_daily_report.py` | 每日流量报告 + 账户余额监控 |
