#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日CDT流量报告"""
import json
import logging
import sys
import os
import time
from datetime import datetime
import requests as tg_requests
from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.request import CommonRequest
from aliyunsdkecs.request.v20140526 import DescribeInstancesRequest
from aliyunsdkcore.acs_exception.exceptions import ClientException

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
NON_RETRYABLE_CODES = {
    'InvalidAccessKeyId',
    'InvalidAccessKeyId.NotFound',
    'Forbidden',
    'SignatureDoesNotMatch',
    'NoPermission',
    'UnauthorizedOperation',
}

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
    """单次查询 CDT 流量；失败时抛出异常，由调用方决定是否重试"""
    req = CommonRequest()
    req.set_domain('cdt.aliyuncs.com')
    req.set_version('2021-08-13')
    req.set_action_name('ListCdtInternetTraffic')
    req.set_method('POST')
    resp = client.do_action_with_exception(req)
    data = json.loads(resp.decode('utf-8'))
    return sum(d.get('Traffic', 0) for d in data.get('TrafficDetails', [])) / (1024 ** 3)


def query_cdt_with_retry(max_attempts=3, base_delay=5):
    """
    带重试与退避的 CDT 查询。
    - 不可重试错误码：立即返回 None
    - 其他错误（含网络超时、5xx）：退避重试，最多 max_attempts 次
    成功返回流量 GB，全部失败返回 None。
    """
    for attempt in range(1, max_attempts + 1):
        try:
            traffic = query_cdt()
            if attempt > 1:
                logger.info(f"CDT 查询在第 {attempt} 次尝试后成功: {traffic:.2f} GB")
            return traffic
        except ClientException as e:
            code = ''
            try:
                code = e.get_error_code() or ''
            except Exception:
                pass
            logger.error(f"CDT 查询失败 (尝试 {attempt}/{max_attempts}): [{code}] {e}")
            if code in NON_RETRYABLE_CODES:
                logger.error(f"错误码 {code} 属于不可重试类型，终止")
                return None
            if attempt < max_attempts:
                delay = base_delay * attempt
                logger.warning(f"{delay}s 后重试...")
                time.sleep(delay)
        except Exception as e:
            logger.error(f"CDT 查询异常 (尝试 {attempt}/{max_attempts}): {e}")
            if attempt < max_attempts:
                delay = base_delay * attempt
                logger.warning(f"{delay}s 后重试...")
                time.sleep(delay)
    logger.error(f"CDT 查询在 {max_attempts} 次尝试后仍失败")
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

        # 优先 EIP
        eip = inst.get('EipAddress', {})
        if eip.get('IpAddress'):
            return eip['IpAddress']

        # 其次公网 IP（安全取值）
        public_ip_obj = inst.get('PublicIpAddress', {})
        public_ips = public_ip_obj.get('IpAddress', [])
        if public_ips:
            return public_ips[0]

        return "无"
    except Exception as e:
        logger.error(f"IP查询失败: {e}")
        return "获取失败"

# ================== 主程序 ==================
def main():
    # 1. 查询流量（带重试）
    traffic = query_cdt_with_retry()
    if traffic is None:
        logger.error("CDT 查询失败，发送告警并跳过本次报告")
        send_tg(
            "⚠️ <b>CDT 每日报告查询失败</b>\n\n"
            "CDT 流量接口连续超时或鉴权失败，无法获取当前用量。\n"
            "💡 请检查 VPS 到 cdt.aliyuncs.com 的网络，或 AccessKey 权限。\n"
            "🕐 本次报告已跳过。"
        )
        return

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
