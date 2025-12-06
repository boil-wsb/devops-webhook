import json
import logging
from flask import request, jsonify
from logger import webhook_logger, monitor_logger

# 使用标准的logging模块，避免导入问题
app_logger = logging.getLogger('app_logger')
from src.services import (
    format_message, 
    record_pipeline_event, 
    record_push_event,
    send_formatted_message,
    pipeline_records,
    pipeline_records_lock,
    push_records,
    push_records_lock,
    running_builds,
    running_builds_lock
)
from src.config import WEBHOOK_CONFIG, DEFAULT_TARGET_URL


def process_webhook(request, route_name, subpath=None):
    """
    处理webhook请求的通用逻辑
    """
    # 确保app_logger在所有地方都可用
    import logging
    global app_logger
    app_logger = logging.getLogger('app_logger')
    
    if not request.is_json:
        return jsonify({"error": "Invalid JSON"}), 400

    try:
        # 获取原始请求体并记录到日志
        raw_body = request.get_data(as_text=True)
        # 使用 webhook_logger 记录请求，确保日志持久化到 webhook_backup.log
        webhook_logger.log_request(route_name, request.headers, raw_body)
        payload = request.get_json()
        
        # 检查事件类型
        object_kind = payload.get('object_kind')
        
        if object_kind == 'push':
            try:
                # 处理 push 事件，记录所需字段
                project_name = payload.get('project', {}).get('name', '')
                ref = payload.get('ref', '')
                user_name = payload.get('user_name', '')
                commits = payload.get('commits', [])
                git_url = payload.get('project', {}).get('web_url', '')
                
                # 构建 push 事件记录
                push_record = {
                    'project_name': project_name,
                    'ref': ref,
                    'user_name': user_name,
                    'git_url': git_url,
                    'commits': [
                        {
                            'url': commit.get('url', ''),
                            'message': commit.get('message', ''),
                            'timestamp': commit.get('timestamp', '')
                        } for commit in commits
                    ]
                }
                
                # 记录 push 事件到全局变量和文件
                record_push_event(push_record, push_records, push_records_lock)
                
                # 记录 push 事件到日志
                monitor_logger.log_event(route_name, request.headers, json.dumps(push_record))
                
                # 返回成功响应
                return jsonify({"message": f"Push event for {route_name} received and processed"}), 200
            except Exception as e:
                # 记录详细错误日志
                error_msg = f"Error processing push event: {str(e)}"
                monitor_logger.log_event(route_name, request.headers, error_msg)
                # 延迟导入app_logger，避免循环导入问题
                from logger import app_logger
                app_logger.error(error_msg)
                return jsonify({"error": error_msg}), 500
        else:
            # 处理流水线事件
            try:
                # 记录流水线事件
                record_pipeline_event(payload, subpath, pipeline_records, pipeline_records_lock, push_records, push_records_lock)
                
                try:
                    message = format_message(payload, running_builds, running_builds_lock, route_name)
                    
                    if message:
                        # 根据路由名称获取对应的目标URL
                        target_url = WEBHOOK_CONFIG.get(route_name, DEFAULT_TARGET_URL)
                        if not target_url:
                            raise Exception(f"No target URL configured for route: {route_name}")
                        # 延迟导入app_logger，避免循环导入问题
                        from logger import app_logger
                        app_logger.info(f"route_name: {route_name}, target_url: {target_url}")
                        send_formatted_message(target_url, message)
                except Exception as e:
                    # 延迟导入app_logger，避免循环导入问题
                    from logger import app_logger
                    app_logger.error(f"❌ format_message调用失败: {str(e)}")
                    import traceback
                    app_logger.error(traceback.format_exc())
                
                # 返回成功响应
                return jsonify({"message": f"Pipeline event for {route_name} received and processed"}), 200
            except KeyError as e:
                # 处理缺少必要字段的情况
                error_msg = f"Missing required field: {str(e)}"
                monitor_logger.log_event(route_name, request.headers, error_msg)
                return jsonify({"error": error_msg}), 500
            except Exception as e:
                # 处理其他异常
                error_msg = f"Error processing pipeline event: {str(e)}"
                monitor_logger.log_event(route_name, request.headers, error_msg)
                return jsonify({"error": error_msg}), 500
    except Exception as e:
        # 记录详细错误日志
        error_msg = f"Error processing webhook: {str(e)}"
        monitor_logger.log_event(route_name, request.headers, error_msg)
        return jsonify({"error": error_msg}), 500
