from flask import Flask, request, jsonify
from datetime import datetime, timedelta
import requests
import json

app = Flask(__name__)


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


# Webhook 路由
@app.route('/vendor_bot', methods=['POST'])
def handle_webhook():
    if not request.is_json:
        return jsonify({"error": "Invalid JSON"}), 400

    try:
        payload = request.get_json()
        message = format_message(payload)

        if message:
            # 目标连接 URL
            # print(json.dumps(message))
            target_url = "https://open.feishu.cn/open-apis/bot/v2/hook/2d1a1d9f-c5f0-444d-a65d-12ae2af8478e"
            send_formatted_message(target_url, message)

        return jsonify({"message": "Webhook received and processed"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
