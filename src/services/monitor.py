import json
import time
import hashlib
import logging
import threading
import requests
from datetime import datetime
from logger import monitor_logger
from src.config import WEBHOOK_CONFIG

app_logger = logging.getLogger('app_logger')

# 方案 6: 告警去重缓存（{dedup_key: last_send_time}）
_alert_dedup_cache = {}
_alert_dedup_lock = threading.Lock()
_alert_dedup_window = 300  # 5 分钟去重窗口


def _get_alert_dedup_key(alert):
    """生成告警去重 key（基于 alertname + instance + job + status）

    P0 修复: key 包含 status，避免 firing 和 resolved 互相去重导致 resolved 通知丢失。
    """
    labels = alert.get('labels', {})
    if not isinstance(labels, dict):
        labels = {}
    key_fields = ['alertname', 'instance', 'job']
    key_data = '|'.join(f'{k}={labels.get(k, "")}' for k in key_fields)
    # 加入 status 区分 firing / resolved
    status = alert.get('status', '')
    key_data = f'{key_data}|status={status}'
    return hashlib.md5(key_data.encode('utf-8')).hexdigest()


def _should_send_alert(dedup_key):
    """判断是否应该发送告警（去重窗口内只发一次）

    P1 修复: 仅检查是否在去重窗口内，不在此处更新 cache。
    cache 更新由调用方在发送成功后执行 _mark_alert_sent。
    """
    now = time.time()
    with _alert_dedup_lock:
        if dedup_key in _alert_dedup_cache:
            if now - _alert_dedup_cache[dedup_key] < _alert_dedup_window:
                return False
    return True


def _mark_alert_sent(dedup_key):
    """标记告警已发送（仅在发送成功后调用）"""
    now = time.time()
    with _alert_dedup_lock:
        _alert_dedup_cache[dedup_key] = now


def _cleanup_dedup_cache():
    """清理过期去重 key（>1 小时）"""
    now = time.time()
    with _alert_dedup_lock:
        expired = [k for k, t in _alert_dedup_cache.items() if now - t > 3600]
        for k in expired:
            del _alert_dedup_cache[k]
    if expired:
        app_logger.info(f"monitor | dedup_cleanup | removed={len(expired)}")


def parse_alertmanager_request(data):
    """
    解析alertmanager的请求体，提取关键信息并格式化
    
    Args:
        data: alertmanager发送的JSON数据
    
    Returns:
        dict: 格式化后的消息结构
    """
    # 提取基本信息
    status = data.get('status', 'unknown')
    group_labels = data.get('groupLabels', {})
    common_labels = data.get('commonLabels', {})
    common_annotations = data.get('commonAnnotations', {})
    alerts = data.get('alerts', [])
    
    # 格式化告警信息
    formatted_alerts = []
    for alert in alerts:
        alert_labels = alert.get('labels', {})
        alert_annotations = alert.get('annotations', {})
        starts_at = alert.get('startsAt', '')
        ends_at = alert.get('endsAt', '')
        
        # 合并标签和注解
        merged_labels = {**common_labels, **alert_labels}
        merged_annotations = {**common_annotations, **alert_annotations}
        
        # 格式化告警条目
        formatted_alert = {
            'status': alert.get('status', 'unknown'),
            'labels': merged_labels,
            'annotations': merged_annotations,
            'startsAt': starts_at,
            'endsAt': ends_at
        }
        formatted_alerts.append(formatted_alert)
    
    # 构建完整消息
    message = {
        'status': status,
        'groupLabels': group_labels,
        'alerts': formatted_alerts,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    }
    
    return message


def send_monitor_message(target_url, message):
    """
    将监控消息发送到指定的webhook地址（方案 6: 带告警去重）

    Args:
        target_url: 目标webhook地址
        message: 要发送的消息内容

    Returns:
        bool: 发送是否成功
    """
    try:
        # 方案 6: 告警去重过滤
        alerts = message.get('alerts', [])
        sent_keys = []  # P1 修复: 记录已发送的 key，发送成功后才标记
        if alerts:
            # 清理过期 key
            _cleanup_dedup_cache()

            unique_alerts = []
            deduped_count = 0
            for alert in alerts:
                dedup_key = _get_alert_dedup_key(alert)
                if _should_send_alert(dedup_key):
                    unique_alerts.append(alert)
                    sent_keys.append(dedup_key)
                else:
                    deduped_count += 1

            if deduped_count > 0:
                app_logger.info(f"monitor | dedup | total={len(alerts)}, deduped={deduped_count}, unique={len(unique_alerts)}")

            # 所有告警都被去重，不发送
            if not unique_alerts:
                app_logger.info(f"monitor | all_deduplicated | skip_send")
                return True

            # 用过滤后的告警替换
            message = dict(message)
            message['alerts'] = unique_alerts

        headers = {'Content-Type': 'application/json'}
        response = requests.post(target_url, headers=headers, data=json.dumps(message))

        success = response.status_code in [200, 201]

        # P1 修复: 仅在发送成功后才标记去重，失败则下次可重试
        if success and sent_keys:
            for dedup_key in sent_keys:
                _mark_alert_sent(dedup_key)

        # 记录发送结果
        log_entry = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
            'action': 'send_monitor_message',
            'target_url': target_url,
            'status_code': response.status_code,
            'success': success
        }

        # 只记录到日志，不打印到控制台
        monitor_logger.log_event(
            route_name='monitor/event/send',
            request_headers={},
            request_body=str(log_entry)
        )

        return success
    except Exception as e:
        # 记录发送失败
        error_log = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
            'action': 'send_monitor_message',
            'target_url': target_url,
            'error': str(e)
        }
        
        try:
            monitor_logger.log_event(
                route_name='monitor/event/send',
                request_headers={},
                request_body=str(error_log)
            )
        except:
            pass
        
        return False
