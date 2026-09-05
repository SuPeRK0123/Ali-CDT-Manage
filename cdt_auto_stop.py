#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDT超额自动关机 + 流量突增告警"""
import json
import logging
import sys
import time
import os
from datetime import datetime
import requests as tg_requests
from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.request import CommonRequest
from aliyunsdkecs.request.v20140526 import StopInstancesRequest, DescribeInstancesRequest

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

# 流量突增告警配置（可从 config.json 读取，或直接定义）
ALERT_INTERVAL_MINUTES = 60      # 监控窗口（分钟）
ALERT_THRESHOLD_GB = 10          # 阈值（GB）
HISTORY_FILE = '/var/run/cdt_history.json'

# ================== 日志 ==================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
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

# ================== 实例状态查询 ==================
def get_status():
    try:
        req = DescribeInstancesRequest.DescribeInstancesRequest()
        req.set_InstanceIds([ECS_INSTANCE_ID])
        resp = client.do_action_with_exception(req)
        instances = json.loads(resp.decode('utf-8')).get('Instances', {}).get('Instance', [])
        return instances[0].get('Status') if instances else None
    except Exception as e:
        logger.error(f"状态查询失败: {e}")
        return None

# ================== 流量突增检测 ==================
def load_cdt_history():
    """读取上次记录的 CDT 流量和记录时间"""
    try:
        with open(HISTORY_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def save_cdt_history(traffic):
    """保存当前 CDT 流量和记录时间"""
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, 'w') as f:
            json.dump({
                'traffic': traffic,
                'record_time': time.time()
            }, f)
    except Exception as e:
        logger.error(f"保存CDT历史记录失败: {e}")

def check_traffic_spike(current_traffic):
    """
    检查是否有流量突增
    返回: (是否突增, 增量GB, 时间差分钟)
    """
    history = load_cdt_history()
    if history is None:
        # 首次运行，保存当前值并跳过检查
        save_cdt_history(current_traffic)
        logger.info("首次运行，已记录基准流量")
        return False, 0, 0

    last_traffic = history.get('traffic', 0)
    last_time = history.get('record_time', 0)

    # 计算时间差（分钟）
    time_diff = (time.time() - last_time) / 60

    # 计算流量增量
    traffic_diff = current_traffic - last_traffic

    # 保存当前值（用于下次比较）
    save_cdt_history(current_traffic)

    # 判断：在窗口时间内且增量超过阈值
    if time_diff <= ALERT_INTERVAL_MINUTES and traffic_diff >= ALERT_THRESHOLD_GB:
        return True, traffic_diff, time_diff

    return False, traffic_diff, time_diff

# ================== 主程序 ==================
def main():
    # 1. 查询 CDT 流量
    traffic = query_cdt()
    if traffic is None:
        logger.error("CDT查询失败，退出")
        return
    logger.info(f"当前CDT流量: {traffic:.2f} GB")

    # 2. 流量突增检测
    is_spike, diff, time_diff = check_traffic_spike(traffic)
    if is_spike:
        # 计算平均速率（GB/小时）
        avg_rate = diff / (time_diff / 60)
        send_tg(
            f"⚠️ <b>CDT 流量突增告警</b>\n\n"
            f"📊 当前总流量: {traffic:.2f} GB\n"
            f"📈 短时增量: {diff:.2f} GB\n"
            f"⏱ 时间窗口: {time_diff:.1f} 分钟\n"
            f"📊 平均速率: {avg_rate:.2f} GB/小时\n"
            f"🕐 检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    # 3. 超额自动关机（原有逻辑）
    if traffic < CDT_LIMIT_GB:
        logger.info("CDT未超限，无需关机")
        return

    status = get_status()
    if status != 'Running':
        logger.info("实例已停止，无需重复关机")
        return

    logger.warning(f"CDT超限 ({traffic:.2f}GB >= {CDT_LIMIT_GB}GB)，执行关机")
    try:
        req = StopInstancesRequest.StopInstancesRequest()
        req.set_InstanceIds([ECS_INSTANCE_ID])
        req.set_ForceStop(False)
        req.set_StoppedMode("StopCharging")
        client.do_action_with_exception(req)
        send_tg(
            f"🛑 <b>CDT 超额自动关机</b>\n\n"
            f"🖥 实例: {ECS_INSTANCE_ID}\n"
            f"📊 当前流量: {traffic:.2f} GB\n"
            f"🔴 已超限: {CDT_LIMIT_GB} GB\n"
            f"💡 停机模式: StopCharging（节省停机）\n"
            f"🕐 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    except Exception as e:
        logger.error(f"关机失败: {e}")

if __name__ == '__main__':
    main()
