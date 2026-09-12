#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot 独立监听服务
从 ecs_webhook.py 拆分出来，避免被 Gunicorn worker 生命周期影响。
由 systemd 服务 ecs-tgbot.service 拉起。
"""
import sys
import logging
from ecs_webhook import tg_command_listener, TG_BOT_TOKEN, TG_CHAT_ID

logger = logging.getLogger(__name__)

if __name__ == '__main__':
    # 前置校验：Token 和 Chat ID 必须已配置
    if not TG_BOT_TOKEN or not str(TG_BOT_TOKEN).strip():
        logger.error("配置缺失：tg_bot_token 未设置，无法启动 Telegram 监听")
        sys.exit(1)
    if not TG_CHAT_ID:
        logger.error("配置缺失：tg_chat_id 未设置，无法启动 Telegram 监听")
        sys.exit(1)

    logger.info(f"Telegram Bot 监听服务启动 (Chat ID: {TG_CHAT_ID})")
    tg_command_listener()
