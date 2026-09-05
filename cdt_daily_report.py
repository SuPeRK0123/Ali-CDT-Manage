#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日CDT流量报告"""
import json
import logging
import sys
import os
from datetime import datetime
import requests as tg_requests
from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.request import CommonRequest
from aliyunsdkecs.request.v20140526 import DescribeInstancesRequest

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

# ================== 日志 ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# ================== 初始化客户端 ==================
client = AcsClient(ACCESS_KEY_ID, ACCESS_KEY_SECRET, REGION_ID)

# ================== Telegram 通知 ==================
def send_tg(msg):
    try:
        tg_requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        logger.error(f"TG发送失败: {e}")

# ================== CDT 流量查询 ==================
def query_cdt():
    try:
        req = CommonRequest()
        req.set_domain('cdt.aliyuncs.com')
        req.set_version('2021-08-13')
        req.set_action_name('ListCdtInternetTraffic')
        req.set_method('POST')
        resp = client.do_action_with_exception(req)
        data = json.loads(resp.decode('utf-8'))
        return sum(d.get('Traffic', 0) for d in data.get('TrafficDetails', [])) / (1024 ** 3)
    except Exception as e:
        logger.error(f"CDT查询失败: {e}")
        return None

# ================== 实例名称查询 ==================
def get_instance_name():
    try:
        req = DescribeInstancesRequest.DescribeInstancesRequest()
        req.set_InstanceIds([ECS_INSTANCE_ID])
        resp = client.do_action_with_exception(req)
        instances = json.loads(resp.decode('utf-8')).get('Instances', {}).get('Instance', [])
        if not instances:
            return "无实例"
        inst = instances[0]
        return inst.get('InstanceName', '未命名')
    except Exception as e:
        logger.error(f"实例名称查询失败: {e}")
        return "获取失败"

# ================== 公网 IP 查询 ==================
def get_public_ip():
    try:
        req = DescribeInstancesRequest.DescribeInstancesRequest()
        req.set_InstanceIds([ECS_INSTANCE_ID])
        resp = client.do_action_with_exception(req)
        instances = json.loads(resp.decode('utf-8')).get('Instances', {}).get('Instance', [])
        if not instances:
            return "无实例"
        inst = instances[0]
        return inst.get('EipAddress', {}).get('IpAddress') or inst.get('PublicIpAddress', {}).get('IpAddress', ['无'])[0]
    except Exception as e:
        logger.error(f"IP查询失败: {e}")
        return "获取失败"

# ================== 主程序 ==================
def main():
    # 1. 查询流量
    traffic = query_cdt()
    if traffic is None:
        traffic = 0.0

    # 2. 计算使用情况
    remain = max(0, CDT_LIMIT_GB - traffic)
    pct = (traffic / CDT_LIMIT_GB * 100) if CDT_LIMIT_GB > 0 else 0
    color = "🟢 正常" if pct < 60 else ("🟡 注意" if pct < 90 else "🔴 危险")

    # 3. 获取额外信息
    instance_name = get_instance_name()
    ip = get_public_ip()

    # 4. 构建消息
    msg = f"<b>📊 CDT 每日流量报告</b>\n\n"
    msg += f"🖥 名称: {instance_name}\n"
    msg += f"🌐 公网IP: {ip}\n"
    msg += f"📈 已用: <b>{traffic:.2f} GB</b>\n"
    msg += f"💾 剩余: <b>{remain:.2f} GB</b>\n"
    msg += f"🔥 使用率: <b>{pct:.1f}%</b> {color}\n\n"
    msg += f"⏰ 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    # 5. 发送 Telegram 通知
    send_tg(msg)

    # 6. 写入本地日志
    log_dir = '/var/log/cdt_daily_report'
    os.makedirs(log_dir, exist_ok=True)
    with open(f"{log_dir}/cdt_report_{datetime.now().strftime('%Y%m')}.log", 'a') as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M')} | {traffic:.2f}GB | {pct:.1f}%\n")

if __name__ == '__main__':
    main()
