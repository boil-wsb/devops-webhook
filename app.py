from flask import Flask, request, jsonify
from datetime import datetime, timedelta
import requests
import json

app = Flask(__name__)

"""
DevOps Webhook 服务器

该应用提供了一个灵活的webhook处理系统，可以根据不同的路由配置将webhook事件转发到不同的目标URL。
主要功能：
1. 接收来自各种DevOps工具的webhook请求
2. 根据配置将消息格式化为飞书卡片消息
3. 根据路由选择对应的目标URL发送消息
"""

# 配置不同路由对应的目标URL
# 格式: {'路由名称': '目标URL'}
WEBHOOK_CONFIG = {
    'vendor_bot': 'https://open.feishu.cn/open-apis/bot/v2/hook/2d1a1d9f-c5f0-444d-a65d-12ae2af8478e',
    'vendor_bot/v2': 'https://open.feishu.cn/open-apis/bot/v2/hook/6373a601-09e7-4cc9-ae64-4d22ed0f0961',
    # 示例：添加更多的路由和对应的URL
    'gitlab_pipeline': 'https://open.feishu.cn/open-apis/bot/v2/hook/example-gitlab-pipeline',
    'jenkins_build': 'https://open.feishu.cn/open-apis/bot/v2/hook/example-jenkins-build',
}

# 全局配置，当路由没有配置对应的URL时使用
# 可以通过环境变量或配置文件覆盖
DEFAULT_TARGET_URL = WEBHOOK_CONFIG.get('vendor_bot', '')


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
            "i18n_header": {
                "zh_cn": {
                    "title": {
                        "tag": "plain_text",
                        "content": project_name
                    },
                    "subtitle": {
                        "tag": "plain_text",
                        "content": ""
                    },
                    "template": message_config['header']['template'],
                    "ud_icon": {
                        "tag": "standard_icon",
                        "token": message_config['header']['icon_token'],
                    }
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

# 为配置中的路由添加处理函数
@app.route('/gitlab_pipeline', methods=['POST'])
def handle_gitlab_pipeline():
    return process_webhook(request, 'gitlab_pipeline')

@app.route('/jenkins_build', methods=['POST'])
def handle_jenkins_build():
    return process_webhook(request, 'jenkins_build')

# 可以在这里添加更多的路由处理函数
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
