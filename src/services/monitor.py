import json
import requests
from datetime import datetime
from logger import monitor_logger
from src.config import WEBHOOK_CONFIG


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
    将监控消息发送到指定的webhook地址
    
    Args:
        target_url: 目标webhook地址
        message: 要发送的消息内容
    
    Returns:
        bool: 发送是否成功
    """
    try:
        headers = {'Content-Type': 'application/json'}
        response = requests.post(target_url, headers=headers, data=json.dumps(message))
        
        # 记录发送结果
        log_entry = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
            'action': 'send_monitor_message',
            'target_url': target_url,
            'status_code': response.status_code,
            'success': response.status_code in [200, 201]
        }
        
        # 只记录到日志，不打印到控制台
        monitor_logger.log_event(
            route_name='monitor/event/send',
            request_headers={},
            request_body=str(log_entry)
        )
        
        return response.status_code in [200, 201]
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
