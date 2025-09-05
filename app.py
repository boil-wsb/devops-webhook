from flask import Flask, request, jsonify
from datetime import datetime, timedelta
import requests
import json
import os
import sys

# 导入日志处理模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from logger import webhook_logger, monitor_logger

app = Flask(__name__)

"""
DevOps Webhook 服务器
该应用提供了一个灵活的webhook处理系统，可以根据不同的路由配置将webhook事件转发到不同的目标URL。
主要功能：
1. 接收来自各种DevOps工具的webhook请求
2. 根据配置将消息格式化为飞书卡片消息
3. 根据路由选择对应的目标URL发送消息
"""

def load_config(config_file='config.conf'):
    """
    从配置文件加载配置信息
    Args:
        config_file: 配置文件路径 
    Returns:
        tuple: (WEBHOOK_CONFIG, DEFAULT_TARGET_URL)
    """
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), config_file)
    
    # 默认配置，当配置文件不存在或加载失败时使用
    default_config = {
        'webhook_config': {
            'vendor_bot': 'https://open.feishu.cn/open-apis/bot/v2/hook/2d1a1d9f-c5f0-444d-a65d-12ae2af8478e',
            'vendor_bot/v2': 'https://open.feishu.cn/open-apis/bot/v2/hook/6373a601-09e7-4cc9-ae64-4d22ed0f0961',
        },
        'default_target_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/6373a601-09e7-4cc9-ae64-4d22ed0f0961'
    }
    
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                webhook_config = config.get('webhook_config', default_config['webhook_config'])
                default_target_url = config.get('default_target_url', default_config['default_target_url'])
                return webhook_config, default_target_url
        else:
            # 如果配置文件不存在，记录日志并使用默认配置
            app.logger.warning(f"Config file {config_path} not found, using default config")
            return default_config['webhook_config'], default_config['default_target_url']
    except json.JSONDecodeError as e:
        # 配置文件格式错误，记录日志并使用默认配置
        app.logger.error(f"Failed to parse config file: {str(e)}")
        return default_config['webhook_config'], default_config['default_target_url']

# 加载配置
WEBHOOK_CONFIG, DEFAULT_TARGET_URL = load_config()


def format_duration(seconds):
    """
    根据持续时间格式化为 秒 或 分秒
    :param seconds: 持续时间（单位：秒）
    :return: 格式化后的字符串
    """
    if seconds < 60:
        return f"{seconds}秒"
    else:
        minutes = seconds // 60
        remaining_seconds = seconds % 60
        return f"{minutes}分{remaining_seconds}秒"


def calculate_interval(start_time_str, end_time_str, time_format="%Y-%m-%d %H:%M:%S"):
    """
    计算开始时间和结束时间的间隔，并格式化输出
    :param start_time_str: 开始时间字符串，例如 "2024-12-18 12:00:00"
    :param end_time_str: 结束时间字符串，例如 "2024-12-18 14:30:45"
    :param time_format: 时间字符串格式，默认 "%Y-%m-%d %H:%M:%S"
    :return: 格式化的时间间隔
    """
    # 解析时间字符串为 datetime 对象
    start_time = datetime.strptime(start_time_str, time_format)
    end_time = datetime.strptime(end_time_str, time_format)
    # 计算时间间隔（timedelta 对象）
    delta = end_time - start_time
    seconds = delta.seconds
    return format_duration(seconds)


# 格式化后的消息模板
def format_message(payload):
    status = payload['object_attributes']['status']
    pipeline_id = payload['object_attributes']['id']
    pipeline_iid = payload['object_attributes']['iid']
    start_time = convert_utc_to_utc8(payload['object_attributes']['created_at'])
    user_name = payload['user']['name']
    branch = payload['object_attributes']['ref']
    detail_url = payload['object_attributes']['url']
    commit_title = payload['commit']['title']
    project_name = payload['project']['name']
    source = payload['object_attributes']['source']

    if 'parent_pipeline' == source:
        return None

    if 'running' == status:
        message_config = {
            'elements': [
                {
                    'icon': 'member_outlined',
                    'content': f"***提交人员***：{user_name}",
                },
                {
                    'icon': 'time_outlined',
                    'content': f"***开始时间***：{start_time}",
                },
                {
                    'icon': 'mindnote_outlined',
                    'content': f"***分      支***：{branch}",
                },
                {
                    'icon': 'doc_outlined',
                    'content': f"***Commit***：{commit_title}",
                },
            ],
            'header': {
                'template': 'wathet',
                'icon_token': 'bell_filled'
            },
        }
    elif 'success' == status or 'failed' == status:
        duration = payload['object_attributes']['duration']
        if duration:
            duration = format_duration(duration)
        else:
            end_time = convert_utc_to_utc8(payload['object_attributes']['finished_at'])
            duration = calculate_interval(start_time, end_time)

        message_config = {
            'elements': [
                {
                    'icon': 'member_outlined',
                    'content': f"***提交人员***：{user_name}",
                },
                {
                    'icon': 'time_outlined',
                    'content': f"***开始时间***：{start_time}",
                },
                {
                    'icon': 'burnlife-notime_outlined',
                    'content': f"***持续时间***：{duration}",
                },
                {
                    'icon': 'mindnote_outlined',
                    'content': f"***分      支***：{branch}",
                },
            ],
            'header': {
                'template': f"{'green' if status == 'success' else 'red'}",
                'icon_token': f"{'succeed_filled' if status == 'success' else 'error_filled'}"
            },
        }
    else:
        return None

    message = {
        "msg_type": "interactive",
        "card": {
            "config": {
                "update_multi": True
            },
            "card_link": {
                "url": detail_url
            },
            "i18n_elements": {
                "zh_cn": [
                    {
                        "tag": "markdown",
                        "content": element['content'],
                        "text_align": "left",
                        "text_size": "normal",
                        "icon": {
                            "tag": "standard_icon",
                            "token": element['icon'],
                            "color": "grey"
                        }
                    } for element in message_config['elements']
                ]
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": project_name
                },
                "subtitle": {
                    "tag": "plain_text",
                    "content": f"版本号：{pipeline_iid}"
                    },
                "text_tag_list": [
                    {
                        "tag": "text_tag",
                        "text": {
                            "tag": "plain_text",
                            "content": str(pipeline_id)
                        },
                        "color": "orange"
                    },
                    {
                        "tag": "text_tag",
                        "text": {
                            "tag": "plain_text",
                            "content": str(pipeline_iid)
                        },
                        "color": "purple"
                    }
                ],
                "template": message_config['header']['template'],
                "ud_icon": {
                    "tag": "standard_icon",
                    "token": message_config['header']['icon_token'],
                }
            }
        }
    }
    return message


# 将消息发送到指定连接
def send_formatted_message(target_url, message):
    headers = {'Content-Type': 'application/json'}
    response = requests.post(target_url, headers=headers, data=json.dumps(message))
    if response.status_code not in [200, 201]:
        raise Exception(f"Failed to send message. Status code: {response.status_code}, Response: {response.text}")


# 通用的webhook处理逻辑
def process_webhook(request, route_name):
    """
    处理webhook请求的通用逻辑
    Args:
        request: Flask请求对象
        route_name: 路由名称，用于从配置中获取对应的目标URL
    Returns:
        tuple: (Flask响应对象, HTTP状态码)
    """
    if not request.is_json:
        return jsonify({"error": "Invalid JSON"}), 400

    try:
        # 获取原始请求体并记录到日志
        raw_body = request.get_data(as_text=True)
        #webhook_logger.log_request(route_name, request.headers, raw_body)
        monitor_logger.log_event(route_name, request.headers, raw_body)
        
        payload = request.get_json()
        message = format_message(payload)

        if message:
            # 根据路由名称获取对应的目标URL
            target_url = WEBHOOK_CONFIG.get(route_name, DEFAULT_TARGET_URL)
            if not target_url:
                raise Exception(f"No target URL configured for route: {route_name}")
            
            send_formatted_message(target_url, message)

        return jsonify({"message": f"Webhook for {route_name} received and processed"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Webhook 路由 - vendor_bot
@app.route('/vendor_bot', methods=['POST'])
def handle_vendor_bot():
    return process_webhook(request, 'vendor_bot')

@app.route('/vendor_bot/v2', methods=['POST'])
def handle_vendor_bot_v2():
    return process_webhook(request, 'vendor_bot/v2')

@app.route('/monitor/event', methods=['POST'])
def monitor_event():
    """
    处理来自alertmanager的监控事件请求
    请求记录存储在日志文件中，但不打印到控制台
    支持扩展自定义解析请求体并推送到指定webhook地址
    """
    try:
        # 获取原始请求体
        request_body = request.get_data().decode('utf-8')
        
        # 使用专用的监控日志记录器记录请求信息
        # 注意：monitor_logger只记录到文件，不打印到控制台
        monitor_logger.log_event(
            route_name='monitor/event',
            request_headers=request.headers,
            request_body=request_body
        )
        
        # 尝试解析请求体
        try:
            data = json.loads(request_body)
            
            # 解析alertmanager请求体
            formatted_message = parse_alertmanager_request(data)
            
            # 检查是否配置了特定的webhook地址用于推送
            if WEBHOOK_CONFIG['/monitor/event'] == '' or WEBHOOK_CONFIG['/monitor/event'] is None:
                pass
            else:
                target_url = WEBHOOK_CONFIG['/monitor/event']
                # 发送格式化后的消息到默认webhook
                send_monitor_message(target_url, formatted_message)
        except json.JSONDecodeError:
            # 请求体不是有效的JSON格式，不影响日志记录
            pass
        
        # 返回成功响应
        return jsonify({'status': 'success', 'message': 'Event received and logged'}), 200
    except Exception as e:
        # 即使发生错误，也记录异常信息
        try:
            error_info = {
                'error': str(e),
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            }
            monitor_logger.log_event(
                route_name='monitor/event',
                request_headers=request.headers,
                request_body=str(error_info)
            )
        except:
            # 如果连日志都记录失败，就忽略
            pass
        
        # 返回错误响应
        return jsonify({'status': 'error', 'message': str(e)}), 500

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

# 在这里添加更多的路由处理函数
# @app.route('/custom_route', methods=['POST'])
# def handle_custom_route():
#     return process_webhook(request, 'custom_route')


def convert_utc_to_utc8(utc_time_str):
    """
    将 UTC 时间字符串（格式：2024-12-18 12:53:35 UTC）转换为 UTC+8 时间
    :param utc_time_str: UTC 时间字符串，例如 "2024-12-18 12:53:35 UTC"
    :return: 转换后的 UTC+8 时间字符串
    """
    if not utc_time_str or not utc_time_str.strip():
        return None
    # 去掉 "UTC" 并将字符串解析为 datetime 对象
    utc_time = datetime.strptime(utc_time_str.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S")
    # 添加 8 小时的偏移量
    utc8_time = utc_time + timedelta(hours=8)
    # 返回格式化后的 UTC+8 时间字符串
    return utc8_time.strftime("%Y-%m-%d %H:%M:%S")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
