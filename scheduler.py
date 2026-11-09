#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定时开关机调度器
- 每分钟检查 schedule.json
- 到达 stop_time / start_time 时，通过本地 API 触发动作
- 动作复用 ecs_webhook 的 CDT 检查与维护模式管理
"""
import json
import logging
import sys
import time
from datetime import datetime

import requests

SCHEDULE_FILE = '/opt/ecs-auto/schedule.json'
API_BASE = 'http://127.0.0.1:8080'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


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


def call_api(action):
    """
    调用本地 API。
    - 返回 True 表示 API 已被成功调用（HTTP 2xx），无论业务结果成功与否
    - 返回 False 表示调用失败（网络/HTTP 错误），下一分钟会重试
    """
    url = f"{API_BASE}/api/{action}"
    try:
        resp = requests.post(url, timeout=180)
        if 200 <= resp.status_code < 300:
            body = resp.text[:300]
            logger.info(f"调用 /api/{action} 成功 (HTTP {resp.status_code}): {body}")
            return True
        logger.error(f"调用 /api/{action} 返回异常状态码 {resp.status_code}: {resp.text[:300]}")
        return False
    except Exception as e:
        logger.error(f"调用 /api/{action} 失败: {e}")
        return False


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
                for action, key in [('stop', 'stop_time'), ('start', 'start_time')]:
                    target = schedule.get(key)
                    if target and current_hhmm == target and executed[action] != today:
                        logger.info(f"触发定时动作: {action} (目标时间 {target})")
                        if call_api(action):
                            executed[action] = today
                            logger.info(f"已记录 {action} 于 {today}")
        except Exception as e:
            logger.error(f"调度器循环异常: {e}")

        # 对齐到下一分钟的开始时刻
        wait = 60 - datetime.now().second
        time.sleep(wait)


if __name__ == '__main__':
    main()
