#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDT超额自动关机（节省停机）"""
import json
import logging
import sys
import time
import requests as tg_requests
from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.request import CommonRequest
from aliyunsdkecs.request.v20140526 import StopInstancesRequest, DescribeInstancesRequest

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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)
client = AcsClient(ACCESS_KEY_ID, ACCESS_KEY_SECRET, REGION_ID)

def send_tg(msg):
    try:
        tg_requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                         json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        logger.error(f"TG发送失败: {e}")

def query_cdt():
    try:
        req = CommonRequest(); req.set_domain('cdt.aliyuncs.com'); req.set_version('2021-08-13')
        req.set_action_name('ListCdtInternetTraffic'); req.set_method('POST')
        resp = client.do_action_with_exception(req)
        data = json.loads(resp.decode('utf-8'))
        return sum(d.get('Traffic', 0) for d in data.get('TrafficDetails', [])) / (1024**3)
    except Exception as e:
        logger.error(f"CDT查询失败: {e}"); return None

def get_status():
    try:
        req = DescribeInstancesRequest.DescribeInstancesRequest(); req.set_InstanceIds([ECS_INSTANCE_ID])
        resp = client.do_action_with_exception(req)
        instances = json.loads(resp.decode('utf-8')).get('Instances', {}).get('Instance', [])
        return instances[0].get('Status') if instances else None
    except Exception as e:
        logger.error(f"状态查询失败: {e}"); return None

def main():
    traffic = query_cdt()
    if traffic is None:
        return
    logger.info(f"CDT: {traffic:.2f}GB")

    if traffic < CDT_LIMIT_GB:
        logger.info("未超限")
        return

    status = get_status()
    if status != 'Running':
        logger.info("实例已停止")
        return

    logger.warning("流量超限，执行关机")
    try:
        req = StopInstancesRequest.StopInstancesRequest()
        req.set_InstanceIds([ECS_INSTANCE_ID]); req.set_ForceStop(False); req.set_StoppedMode("StopCharging")
        client.do_action_with_exception(req)
        send_tg(f"🛑 CDT超额自动关机\n实例: {ECS_INSTANCE_ID}\n流量: {traffic:.2f}GB")
    except Exception as e:
        logger.error(f"关机失败: {e}")

if __name__ == '__main__':
    main()
