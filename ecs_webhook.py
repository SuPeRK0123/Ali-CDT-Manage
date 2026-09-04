#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阿里云抢占式实例自动保活 Webhook 服务
- 事件驱动开机（CDT前置检查）
- Telegram 交互式控制（/start, /stop, /status, /cdt）
- 文件锁防重复触发
"""
import json
import logging
import sys
import threading
import time
import fcntl
import os
import requests as tg_requests
from flask import Flask, request, jsonify
from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.request import CommonRequest
from aliyunsdkecs.request.v20140526 import (
    StartInstancesRequest, StopInstancesRequest, DescribeInstancesRequest
)

# ================== 加载配置 ==================
CONFIG_FILE = '/opt/ecs-auto/config.json'
with open(CONFIG_FILE) as f:
    config = json.load(f)

ACCESS_KEY_ID = config['access_key_id']
ACCESS_KEY_SECRET = config['access_key_secret']
REGION_ID = config['region_id']
ECS_INSTANCE_ID = config['ecs_instance_id']
TG_BOT_TOKEN = config['tg_bot_token']
TG_CHAT_ID = config['tg_chat_id']
CDT_LIMIT_GB = config['cdt_limit_gb']
CDT_SAFE_GB = config['cdt_safe_gb']
WEBHOOK_PORT = config['webhook_port']
LOCK_FILE = config.get('lock_file', '/var/run/ecs-auto.lock')

MAX_RETRIES = 5
RETRY_INTERVAL = 10

# ================== 日志 ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# ================== 初始化客户端 ==================
client = AcsClient(ACCESS_KEY_ID, ACCESS_KEY_SECRET, REGION_ID)

# ================== 文件锁（持久化防重叠） ==================
class PIDLock:
    def __init__(self, lock_file=LOCK_FILE):
        self.lock_file = lock_file
        self.fp = None

    def acquire(self):
        try:
            self.fp = open(self.lock_file, 'w')
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fp.write(str(os.getpid()))
            self.fp.flush()
            return True
        except (IOError, OSError):
            if self.fp:
                self.fp.close()
                self.fp = None
            return False

    def release(self):
        if self.fp:
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_UN)
            self.fp.close()
            self.fp = None
            try:
                os.remove(self.lock_file)
            except OSError:
                pass

    def __enter__(self):
        if not self.acquire():
            logger.warning("获取文件锁失败，可能存在并发操作")
            raise RuntimeError("Could not acquire lock")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

# ================== Telegram 通知 ==================
def send_tg_message(message, chat_id=None):
    if chat_id is None:
        chat_id = TG_CHAT_ID
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        tg_requests.post(url, json=payload, timeout=10)
        logger.info("Telegram 通知已发送")
    except Exception as e:
        logger.error(f"发送TG消息失败: {e}")

# ================== CDT 流量查询 ==================
def query_cdt_traffic():
    try:
        request = CommonRequest()
        request.set_domain('cdt.aliyuncs.com')
        request.set_version('2021-08-13')
        request.set_action_name('ListCdtInternetTraffic')
        request.set_method('POST')
        response = client.do_action_with_exception(request)
        data = json.loads(response.decode('utf-8'))
        total_bytes = sum(d.get('Traffic', 0) for d in data.get('TrafficDetails', []))
        return total_bytes / (1024 ** 3)
    except Exception as e:
        logger.error(f"查询CDT失败: {e}")
        return None

# ================== ECS 状态查询 ==================
def get_instance_status():
    try:
        request = DescribeInstancesRequest.DescribeInstancesRequest()
        request.set_InstanceIds([ECS_INSTANCE_ID])
        response = client.do_action_with_exception(request)
        data = json.loads(response.decode('utf-8'))
        instances = data.get('Instances', {}).get('Instance', [])
        if not instances:
            return None
        return instances[0].get('Status')
    except Exception as e:
        logger.error(f"查询实例状态失败: {e}")
        return None

# ================== 开机（带重试） ==================
def start_instance_with_retry():
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            status = get_instance_status()
            if status == 'Running':
                return True, "Already Running"
            if status == 'Starting':
                time.sleep(RETRY_INTERVAL)
                continue

            request = StartInstancesRequest.StartInstancesRequest()
            request.set_InstanceIds([ECS_INSTANCE_ID])
            request.set_accept_format('json')
            client.do_action_with_exception(request)
            logger.info(f"开机请求已发送 (尝试 {attempt}/{MAX_RETRIES})")

            time.sleep(8)
            new_status = get_instance_status()
            if new_status == 'Running':
                return True, "Started Successfully"
            elif new_status == 'Starting':
                return True, "Starting in progress"

        except Exception as e:
            logger.error(f"开机尝试 {attempt} 失败: {e}")

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_INTERVAL)

    return False, "Max retries exceeded"

# ================== 关机 ==================
def stop_instance():
    try:
        request = StopInstancesRequest.StopInstancesRequest()
        request.set_InstanceIds([ECS_INSTANCE_ID])
        request.set_ForceStop(False)
        request.set_StoppedMode("StopCharging")
        client.do_action_with_exception(request)
        return True, "Stopped (StopCharging)"
    except Exception as e:
        logger.error(f"关机失败: {e}")
        return False, str(e)

# ================== 核心开机逻辑（含CDT检查） ==================
def auto_start_with_check(chat_id=None):
    # 1. 检查CDT
    traffic = query_cdt_traffic()
    if traffic is None:
        send_tg_message("⚠️ CDT流量查询失败，放弃自动开机", chat_id)
        return False

    if traffic >= CDT_LIMIT_GB:
        send_tg_message(
            f"🚫 拒绝自动开机 - CDT已超限\n"
            f"当前: {traffic:.2f} GB / {CDT_LIMIT_GB} GB",
            chat_id
        )
        logger.warning(f"CDT超限({traffic:.2f}GB)，拒绝开机")
        return False

    if traffic >= CDT_SAFE_GB:
        send_tg_message(f"⚠️ CDT接近上限 ({traffic:.2f}GB)，仍尝试开机", chat_id)

    # 2. 执行开机
    success, msg = start_instance_with_retry()
    status_text = "✅ 开机成功" if success else "❌ 开机失败"
    send_tg_message(
        f"{status_text}\n实例: {ECS_INSTANCE_ID}\n状态: {msg}\nCDT: {traffic:.2f} GB",
        chat_id
    )
    return success

# ================== Webhook 事件处理 ==================
def handle_state_change(event_data):
    content = event_data.get('content', {})
    instance_id = content.get('resourceId')
    new_state = content.get('state')

    if instance_id != ECS_INSTANCE_ID:
        return
    if new_state != 'Stopped':
        return

    # 使用文件锁防止重复
    try:
        with PIDLock():
            logger.info(f"实例 {ECS_INSTANCE_ID} 被关机，触发自动开机")
            auto_start_with_check()
    except RuntimeError:
        logger.info("其他进程正在处理开机，跳过")

# ================== Flask 路由 ==================
app = Flask(__name__)

@app.route('/webhook/ecs', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"code": 0, "msg": "ok"}), 200

        if 'source' in data and data.get('source') == 'acs.ecs':
            event_data = data.get('data', {})
            if 'content' in event_data:
                handle_state_change(event_data)
            else:
                handle_state_change(data)
        else:
            handle_state_change(data)

        return jsonify({"code": 0, "msg": "ok"}), 200
    except Exception as e:
        logger.error(f"Webhook异常: {e}")
        return jsonify({"code": 500, "msg": str(e)}), 500

# ================== 手动控制 API ==================
@app.route('/api/start', methods=['POST'])
def manual_start():
    with PIDLock():
        success = auto_start_with_check()
    return jsonify({"success": success})

@app.route('/api/stop', methods=['POST'])
def manual_stop():
    success, msg = stop_instance()
    return jsonify({"success": success, "message": msg})

@app.route('/api/status', methods=['GET'])
def manual_status():
    status = get_instance_status()
    traffic = query_cdt_traffic()
    return jsonify({
        "instance_id": ECS_INSTANCE_ID,
        "status": status,
        "cdt_traffic_gb": round(traffic, 2) if traffic else None,
        "cdt_limit_gb": CDT_LIMIT_GB
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

# ================== Telegram 交互式控制（后台线程） ==================
def tg_command_listener():
    """长轮询 Telegram 更新，处理 /start /stop /status /cdt"""
    offset = 0
    logger.info("Telegram 交互式控制已启动")
    while True:
        try:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getUpdates"
            resp = tg_requests.get(url, params={"offset": offset, "timeout": 20}, timeout=25)
            if resp.status_code != 200:
                time.sleep(5)
                continue

            updates = resp.json().get('result', [])
            for update in updates:
                offset = update['update_id'] + 1
                message = update.get('message')
                if not message:
                    continue
                chat_id = message.get('chat', {}).get('id')
                text = message.get('text', '').strip()

                # 忽略非命令消息
                if not text.startswith('/'):
                    continue

                # 处理命令
                if text == '/start':
                    with PIDLock():
                        auto_start_with_check(chat_id)
                elif text == '/stop':
                    success, msg = stop_instance()
                    send_tg_message(f"🛑 关机指令\n结果: {'成功' if success else '失败'}\n信息: {msg}", chat_id)
                elif text == '/status':
                    status = get_instance_status()
                    traffic = query_cdt_traffic()
                    msg = (f"📊 实例状态\nID: {ECS_INSTANCE_ID}\n状态: {status or '未知'}\n"
                           f"CDT流量: {traffic:.2f} GB / {CDT_LIMIT_GB} GB" if traffic else "CDT查询失败")
                    send_tg_message(msg, chat_id)
                elif text == '/cdt':
                    traffic = query_cdt_traffic()
                    msg = f"📊 当前CDT流量: {traffic:.2f} GB / {CDT_LIMIT_GB} GB" if traffic else "查询失败"
                    send_tg_message(msg, chat_id)
                elif text == '/help':
                    help_text = (
                        "🤖 可用命令：\n"
                        "/start - 手动开机\n"
                        "/stop - 手动关机（节省停机）\n"
                        "/status - 查询实例状态+CDT\n"
                        "/cdt - 仅查询CDT流量\n"
                        "/help - 显示帮助"
                    )
                    send_tg_message(help_text, chat_id)
                else:
                    send_tg_message(f"未知命令: {text}\n发送 /help 查看帮助", chat_id)

        except Exception as e:
            logger.error(f"TG监听异常: {e}")
            time.sleep(10)

# ================== 启动服务 ==================
if __name__ == '__main__':
    # 启动 TG 监听线程
    tg_thread = threading.Thread(target=tg_command_listener, daemon=True)
    tg_thread.start()
    # 启动 Flask
    app.run(host='0.0.0.0', port=WEBHOOK_PORT, debug=False)
