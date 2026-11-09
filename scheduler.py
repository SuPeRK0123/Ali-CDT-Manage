#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定时开关机调度器
- 每分钟检查 schedule.json
- 到达 stop_time / start_time 时，通过本地 API 触发动作
- 动作复用 ecs_webhook 的 CDT 检查与维护模式管理
- 执行结果通过 Telegram 主动推送
"""
import json
import logging
import sys
import time
from datetime import datetime

import requests

SCHEDULE_FILE = '/opt/ecs-auto/schedule.json'
CONFIG_FILE = '/opt/ecs-auto/config.json'
API_BASE = 'http://127.0.0.1:8080'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


# ================== 读取 TG 配置 ==================
try:
    with open(CONFIG_FILE) as _f:
        _cfg = json.load(_f)
    TG_BOT_TOKEN = _cfg['tg_bot_token']
    TG_CHAT_ID = _cfg['tg_chat_id']
except Exception as _e:
    logger.error(f"读取 config.json 失败，TG 通知将不可用: {_e}")
    TG_BOT_TOKEN = None
    TG_CHAT_ID = None


def send_tg(msg):
    """发送 Telegram 通知（失败不重试，日志记录即可）"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        logger.warning("TG 配置缺失，跳过通知")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": TG_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        logger.error(f"发送 TG 通知失败: {e}")


# ================== 读取 schedule ==================
def load_schedule():
    """读取 schedule.json；失败返回 None"""
    try:
        with open(SCHEDULE_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"读取 schedule.json 失败: {e}")
        return None
    except Exception as e:
        logger.error(f"读取 schedule.json 异常: {e}")
        return None


# ================== 调用本地 API ==================
def call_api(action):
    """
    调用本地 API。
    返回 (http_ok, biz_ok)：
      - http_ok: HTTP 2xx，API 调用成功（决定是否标记 executed）
      - biz_ok:  业务执行成功（用于通知内容）
    """
    url = f"{API_BASE}/api/{action}"
    try:
        resp = requests.post(url, timeout=180)
        if 200 <= resp.status_code < 300:
            try:
                body = resp.json()
            except Exception:
                body = {}
            logger.info(f"调用 /api/{action} 成功: {resp.text[:300]}")
            return True, bool(body.get('success', True))
        logger.error(f"调用 /api/{action} 返回异常状态码 {resp.status_code}: {resp.text[:300]}")
        return False, False
    except Exception as e:
        logger.error(f"调用 /api/{action} 失败: {e}")
        return False, False


# ================== 主循环 ==================
def main():
    executed = {'stop': None, 'start': None}  # 记录 'YYYY-MM-DD'
    logger.info("定时调度器已启动")

    while True:
        try:
            now = datetime.now()
            today = now.strftime('%Y-%m-%d')
            current_hhmm = now.strftime('%H:%M')

            schedule = load_schedule()
            if schedule and schedule.get('enabled'):
                for action, key, label, emoji in [
                    ('stop',  'stop_time',  '关机', '🔴'),
                    ('start', 'start_time', '开机', '🟢'),
                ]:
                    target = schedule.get(key)
                    if not target or current_hhmm != target or executed[action] == today:
                        continue

                    logger.info(f"触发定时{label} (目标时间 {target})")
                    http_ok, biz_ok = call_api(action)
                    if http_ok:
                        executed[action] = today
                        logger.info(f"已记录 {action} 于 {today}")
                        result_line = "✅ 执行成功" if biz_ok else "❌ 执行失败（详见 /status）"
                        send_tg(
                            f"⏰ <b>定时{label}</b>\n\n"
                            f"{emoji} 目标时间: {target}\n"
                            f"{result_line}\n"
                            f"🕐 执行时间: {datetime.now().strftime('%H:%M:%S')}"
                        )
                    else:
                        logger.warning("API 调用失败，下一分钟将重试")
        except Exception as e:
            logger.error(f"调度器循环异常: {e}")

        # 对齐到下一分钟的开始时刻
        wait = 60 - datetime.now().second
        time.sleep(wait)


if __name__ == '__main__':
    main()
