from flask import Flask, request, jsonify
from datetime import datetime, timedelta
import requests
import json
import os
import sys
import re
from pathlib import Path
import threading
import time
from minio import Minio
from minio.error import S3Error

# 导入日志处理模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from logger import webhook_logger, monitor_logger

# 全局变量：记录运行中的构建
running_builds = {}
# 锁，确保线程安全
running_builds_lock = threading.Lock()

def check_long_running_builds():
    """
    后台线程函数，定期检查运行中的构建是否超时
    每30秒检查一次，超过5分钟（300秒）没有结果的构建发送告警
    """
    while True:
        try:
            current_time = datetime.now()
            builds_to_remove = []
            
            with running_builds_lock:
                for pipeline_iid, build_info in running_builds.items():
                    # 计算已经运行的时间
                    elapsed_time = (current_time - build_info['start_time']).total_seconds()
                    
                    if elapsed_time > 300:  # 超过5分钟
                        # 发送超时告警
                        send_long_build_alert(build_info)
                        # 标记为需要移除
                        builds_to_remove.append(pipeline_iid)
            
            # 移除已经处理超时告警的构建
            with running_builds_lock:
                for pipeline_iid in builds_to_remove:
                    if pipeline_iid in running_builds:
                        del running_builds[pipeline_iid]
                        print(f"已移除超时构建记录: {pipeline_iid}")
            
            # 每30秒检查一次
            time.sleep(30)
            
        except Exception as e:
            print(f"检查运行中构建时发生错误: {str(e)}")
            time.sleep(30)

def send_long_build_alert(build_info):
    """
    发送构建超时告警
    Args:
        build_info: 构建信息字典
    """
    try:
        duration_minutes = int((datetime.now() - build_info['start_time']).total_seconds() / 60)
        
        long_build_message = {
            "msg_type": "interactive",
            "card": {
                "config": {
                    "update_multi": True
                },
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"⚠️ 构建超时告警 - {build_info['project_name']}"
                    },
                    "subtitle": {
                        "tag": "plain_text",
                        "content": f"构建已运行 {duration_minutes} 分钟，仍未完成"
                    },
                    "template": "yellow"
                },
                "i18n_elements": {
                    "zh_cn": [
                        {
                            "tag": "markdown",
                            "content": f"**项目**：{build_info['project_name']}\n"
                                        f"**分支**：{build_info['branch']}\n"
                                        f"**提交人员**：{build_info['user_name']}\n"
                                        f"**开始时间**：{build_info['start_time_str']}\n"
                                        f"**Pipeline IID**：{build_info['pipeline_iid']}\n"
                                        f"**状态**：运行中（超过5分钟）\n"
                                        f"**建议**：检查构建过程是否卡死或存在性能问题",
                            "text_align": "left",
                            "text_size": "normal"
                        }
                    ]
                }
            }
        }
        
        # 发送告警
        target_url = WEBHOOK_CONFIG.get('vendor_bot/v2', DEFAULT_TARGET_URL)
        if target_url:
            send_formatted_message(target_url, long_build_message)
            print(f"已发送构建超时告警: {build_info['pipeline_iid']}")
        
    except Exception as e:
        print(f"发送构建超时告警失败: {str(e)}")

# 启动后台检查线程
def start_build_monitor():
    """启动构建监控线程"""
    monitor_thread = threading.Thread(target=check_long_running_builds, daemon=True)
    monitor_thread.start()
    print("构建监控线程已启动")

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
        tuple: (WEBHOOK_CONFIG, DEFAULT_TARGET_URL, MINIO_CONFIG)
    """
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), config_file)
    
    # 默认配置，当配置文件不存在或加载失败时使用
    default_config = {
        'webhook_config': {
            'vendor_bot': 'https://open.feishu.cn/open-apis/bot/v2/hook/2d1a1d9f-c5f0-444d-a65d-12ae2af8478e',
            'vendor_bot/v2': 'https://open.feishu.cn/open-apis/bot/v2/hook/6373a601-09e7-4cc9-ae64-4d22ed0f0961',
            'vendor_bot/itreporter': 'https://open.feishu.cn/open-apis/bot/v2/hook/1b78f2d5-0cd0-4035-85fe-a2d8a4b207c6',
        },
        'default_target_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/1b78f2d5-0cd0-4035-85fe-a2d8a4b207c6',
        'minio_config': {
            'minio_endpoint': 'http://192.168.23.36:9000',
            'minio_access_key': 'fYIukgJZaLOivnFimLVX',
            'minio_secret_key': 'shdyfYIukgJZaLOivnFimLVX123',
        }
    }
    
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                webhook_config = config.get('webhook_config', default_config['webhook_config'])
                default_target_url = config.get('default_target_url', default_config['default_target_url'])
                minio_config = config.get('minio_config', default_config['minio_config'])
                return webhook_config, default_target_url, minio_config
        else:
            # 如果配置文件不存在，记录日志并使用默认配置
            app.logger.warning(f"Config file {config_path} not found, using default config")
            return default_config['webhook_config'], default_config['default_target_url'], default_config['minio_config']
    except json.JSONDecodeError as e:
        # 配置文件格式错误，记录日志并使用默认配置
        app.logger.error(f"Failed to parse config file: {str(e)}")
        return default_config['webhook_config'], default_config['default_target_url'], default_config['minio_config']

# 加载配置
WEBHOOK_CONFIG, DEFAULT_TARGET_URL, MINIO_CONFIG = load_config()


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
    builds_name = payload['builds'][0]['name']
    source = payload['object_attributes']['source']

    if 'parent_pipeline' == source:
        return None

    # 当builds_name等于"deploy_custom_branch"时，查找相似记录
    pipeline_iid_prev = None  # 初始化变量
    if builds_name == "deploy_custom_branch":
        similar_records = find_similar_pipeline_records(project_name, branch, pipeline_iid, source)
        if similar_records:
            for record in similar_records:
                # 将上一个非WEB构建记录的IID重新赋值
                pipeline_iid_prev = record['pipeline_iid']
                break  # 只取第一条记录
        else:
            print(f"未找到相同project_name={project_name}, branch={branch}的其他pipeline记录")

    if 'running' == status:
        # 记录运行中的构建
        with running_builds_lock:
            running_builds[str(pipeline_iid)] = {
                'pipeline_iid': str(pipeline_iid),
                'project_name': project_name,
                'branch': branch,
                'user_name': user_name,
                'start_time': datetime.now(),
                'start_time_str': start_time,
                'commit_title': commit_title
            }
            #print(f"已记录运行中的构建: {pipeline_iid}")
        
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
        # 构建完成，从运行中构建记录中移除
        with running_builds_lock:
            if str(pipeline_iid) in running_builds:
                del running_builds[str(pipeline_iid)]
                #print(f"构建完成，移除记录: {pipeline_iid}")
        
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
                    "content": f"镜像版本号：{pipeline_iid_prev if pipeline_iid_prev else pipeline_iid}"
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
                    },
                    {
                        "tag": "text_tag",
                        "text": {
                            "tag": "plain_text",
                            "content": str(source)
                        },
                        "color": "yellow"
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
    return True


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
        # webhook_logger.log_request(route_name, request.headers, raw_body)
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

@app.route('/vendor_bot/itreporter', methods=['POST'])
def handle_vendor_bot_itreporter():
    """
    处理来自远程服务器的JSON请求
    接收JSON数据，根据report_path从MinIO下载文件到本地
    """
    try:
        # 获取JSON请求数据
        json_data = request.get_json()
        
        if json_data is None:
            return jsonify({'status': 'error', 'message': 'Invalid JSON data'}), 400
        
        # 记录接收到的请求
        # print(f"收到IT Reporter请求: {json.dumps(json_data, ensure_ascii=False)}")
        
        # 获取report_path字段
        report_path = json_data.get('report_path')
        if not report_path:
            return jsonify({'status': 'error', 'message': 'Missing report_path field'}), 400
        
        # 初始化MinIO客户端
        minio_client = Minio(
            MINIO_CONFIG['minio_endpoint'].replace('http://', '').replace('https://', ''),
            access_key=MINIO_CONFIG['minio_access_key'],
            secret_key=MINIO_CONFIG['minio_secret_key'],
            secure=MINIO_CONFIG['minio_endpoint'].startswith('https://')
        )
        
        bucket_name = json_data.get('minio_bucket')
        
        # 生成本地文件路径
        local_filename = os.path.basename(report_path)
        local_filepath = os.path.join('downloads', local_filename)
        
        # 确保下载目录存在
        os.makedirs('downloads', exist_ok=True)
        
        # 从MinIO下载文件到本地
        minio_client.fget_object(bucket_name, report_path, local_filepath)
        
        #print(f"文件下载成功: {local_filepath}")
        
        # 生成预签名URL（有效期2小时）
        try:
            presigned_url = minio_client.presigned_get_object(
                bucket_name, 
                report_path, 
                expires=timedelta(hours=2)
            )
            print(f"预签名URL生成成功: {presigned_url}")
            
            # 组装飞书卡片消息 - 优化markdown格式
            file_size = os.path.getsize(local_filepath)
            file_size_mb = round(file_size / (1024 * 1024), 2)
            
            feishu_card_message = {
                "msg_type": "interactive",
                "card": {
                    "config": {
                        "update_multi": True,
                        "enable_forward": True
                    },
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": "📊 IT系统运行报告"
                        },
                        "subtitle": {
                            "tag": "plain_text", 
                            "content": f"📋 {os.path.basename(report_path)}"
                        },
                        "template": "blue"
                    },
                    "i18n_elements": {
                        "zh_cn": [
                            {
                                "tag": "div",
                                "text": {
                                    "tag": "lark_md",
                                    "content": f"📁 报告路径 : {report_path} \n"
                                              f"🗄️ 存储桶 : {bucket_name} \n"
                                              f"📏 文件大小 : {file_size_mb} MB \n"
                                              f"⏱️ 有效期 : 2小时 |\n\n"
                                              f"> ⚠️ **安全提醒**: 下载链接有效期为2小时，请尽快下载"
                                }
                            },
                            {
                                "tag": "hr"
                            },
                            {
                                "tag": "action",
                                "actions": [
                                    {
                                        "tag": "button",
                                        "type": "primary",
                                        "text": {
                                            "tag": "plain_text",
                                            "content": "📥 下载报告"
                                        },
                                        "url": presigned_url,
                                        "multi_url": {
                                            "url": presigned_url,
                                            "android_url": presigned_url,
                                            "ios_url": presigned_url,
                                            "pc_url": presigned_url
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
            
            # 发送到对应的webhook
            target_url = WEBHOOK_CONFIG.get('vendor_bot/itreporter', DEFAULT_TARGET_URL)

                
        except Exception as e:
            print(f"生成预签名URL失败: {str(e)}")
            presigned_url = None
        
        # 返回成功响应，包含预签名URL
        response_data = {
            'status': 'success', 
            'message': 'File downloaded successfully from MinIO',
            'data': {
                'file_size': os.path.getsize(local_filepath),
                'presigned_url': presigned_url,
                'url_expires_in': '2 hours' if presigned_url else None,
                'feishu_message_sent': presigned_url is not None
            }
        }
        
        return jsonify(response_data), 200
        
    except S3Error as e:
        return jsonify({
            'status': 'error', 
            'message': f'MinIO error: {str(e)}'
        }), 500
    except Exception as e:
        return jsonify({
            'status': 'error', 
            'message': str(e)
        }), 500

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


def find_similar_pipeline_records(project_name, branch, current_pipeline_iid, build_type):
    """
    从webhook_backup.log中查找相同project_name和branch但不同pipeline_iid的记录
    Args:
        project_name: 项目名称
        branch: 分支名称
        current_pipeline_iid: 当前pipeline的iid，用于排除
    Returns:
        list: 找到的相关记录列表
    """
    # 修正日志文件路径
    log_file = Path("logs/monitor_event.log")
    
    if not log_file.exists():
        return []
    
    similar_records = []
    
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        # 从后往前读取，获取最新记录
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
                
            try:
                # 分割日志行
                parts = line.split(' - INFO - ', 1)
                # 使用ast.literal_eval处理单引号JSON
                import ast
                log_data = ast.literal_eval(parts[1].strip())
                
                # 获取body内容
                body_str = log_data.get('body', '')
                # 解析body中的JSON
                body_data = json.loads(body_str)
                # 提取所需信息
                obj_attrs = body_data.get('object_attributes', {})
                project_data = body_data.get('project', {})
                
                
                # 检查是否匹配project_name和branch
                found_project = str(project_data.get('name', '')) == str(project_name)
                found_branch = str(obj_attrs.get('ref', '')) == str(branch)
                found_build_type = str(obj_attrs.get('source', '')) != str(build_type)

                if found_project and found_branch and found_build_type:
                    pipeline_iid = obj_attrs.get('iid')
                    
                    # 排除当前pipeline_iid
                    if str(pipeline_iid) != str(current_pipeline_iid):
                        record = {
                            'project_name': str(project_name),
                            'branch': str(branch),
                            'pipeline_iid': str(pipeline_iid),
                            'timestamp': str(log_data.get('timestamp', '')),
                            'status': str(obj_attrs.get('status', 'unknown'))
                        }
                        similar_records.append(record)
                        # 找到第一个匹配的记录即可停止
                        break
                
            except (json.JSONDecodeError, KeyError, ValueError, SyntaxError) as e:
                continue
                
    except Exception as e:
        # 文件读取错误，静默处理
        pass
    
    return similar_records

if __name__ == '__main__':
    # 启动构建监控线程
    start_build_monitor()
    app.run(host='0.0.0.0', port=8080)