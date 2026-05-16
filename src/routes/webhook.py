import json
import logging
from flask import request, jsonify
from logger import webhook_logger, monitor_logger

app_logger = logging.getLogger('app_logger')
from src.services import (
    format_message,
    format_error_log_message,
    record_pipeline_event,
    record_push_event,
    send_formatted_message,
    pipeline_records,
    pipeline_records_lock,
    push_records,
    push_records_lock,
    running_builds,
    running_builds_lock,
    get_job_logs,
    get_failed_job_id,
    get_project_id_by_name,
    parse_error_from_logs,
    save_build_logs,
    get_build_logs
)
from src.services.trigger_action import check_and_trigger
from src.services.message import send_notification
from src.config import WEBHOOK_CONFIG, DEFAULT_TARGET_URL, ROUTE_CHAT_ID_MAP
from src.utils import convert_utc_to_utc8


def process_webhook(request, route_name, subpath=None):
    """
    处理webhook请求的通用逻辑
    """
    # 确保app_logger在所有地方都可用
    import logging
    global app_logger
    app_logger = logging.getLogger('app_logger')
    
    if not request.is_json:
        return jsonify({"error": "无效的JSON格式"}), 400

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
                    'subpath': subpath,
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
                return jsonify({"message": f"已接收并处理 {route_name} 的推送事件"}), 200
            except Exception as e:
                # 记录详细错误日志
                error_msg = f"处理推送事件出错: {str(e)}"
                monitor_logger.log_event(route_name, request.headers, error_msg)
                # 延迟导入app_logger，避免循环导入问题
                from logger import app_logger
                app_logger.error(error_msg)
                return jsonify({"error": error_msg}), 500
        else:
            try:
                record_pipeline_event(payload, subpath, pipeline_records, pipeline_records_lock, push_records, push_records_lock)

                status = payload['object_attributes'].get('status', '')

                chat_id = ROUTE_CHAT_ID_MAP.get(route_name)
                message_id = None
                callback_id = None
                if status in ['success', 'failed'] and running_builds and running_builds_lock:
                    with running_builds_lock:
                        build_info = running_builds.get(payload['object_attributes'].get('iid'))
                        if build_info:
                            message_id = build_info.get('message_id')
                            callback_id = build_info.get('callback_id')
                            if not chat_id:
                                chat_id = build_info.get('chat_id')

                try:
                    message = format_message(payload, running_builds, running_builds_lock, route_name, push_records, push_records_lock)
                    if message:
                        if status == 'running' and not callback_id and running_builds and running_builds_lock:
                            with running_builds_lock:
                                build_info = running_builds.get(payload['object_attributes'].get('iid'))
                                if build_info:
                                    callback_id = build_info.get('callback_id')

                        result = send_notification(route_name, message, chat_id=chat_id, message_id=message_id, callback_id=callback_id)
                        app_logger.info(f"路由名称: {route_name}, 发送结果: method={result.get('method')}, success={result.get('success')}")

                        if status == 'running' and result.get('method') == 'api' and result.get('message_id'):
                            with running_builds_lock:
                                pipeline_iid = payload['object_attributes'].get('iid')
                                if pipeline_iid in running_builds:
                                    running_builds[pipeline_iid]['message_id'] = result['message_id']
                                    running_builds[pipeline_iid]['chat_id'] = chat_id

                        if status == 'failed':
                            _handle_failed_pipeline(payload, route_name)

                        if status == 'success':
                            path_with_namespace = payload.get('project', {}).get('path_with_namespace', '')
                            project_name = payload.get('project', {}).get('name', '')
                            ref = payload['object_attributes'].get('ref', '')
                            try:
                                check_and_trigger(path_with_namespace, ref, project_name=project_name)
                            except Exception as e:
                                app_logger.error(f"触发动作执行异常: {str(e)}")
                except Exception as e:
                    app_logger.error(f"❌ format_message调用失败: {str(e)}")
                    import traceback
                    app_logger.error(traceback.format_exc())
                
                # 返回成功响应
                return jsonify({"message": f"已接收并处理 {route_name} 的流水线事件"}), 200
            except KeyError as e:
                # 处理缺少必要字段的情况
                error_msg = f"缺少必要字段: {str(e)}"
                monitor_logger.log_event(route_name, request.headers, error_msg)
                return jsonify({"error": error_msg}), 500
            except Exception as e:
                # 处理其他异常
                error_msg = f"处理流水线事件出错: {str(e)}"
                monitor_logger.log_event(route_name, request.headers, error_msg)
                return jsonify({"error": error_msg}), 500
    except Exception as e:
        error_msg = f"处理webhook出错: {str(e)}"
        monitor_logger.log_event(route_name, request.headers, error_msg)
        return jsonify({"error": error_msg}), 500


def _handle_failed_pipeline(payload, route_name):
    """
    处理失败构建的日志获取和通知发送
    当 GitLab API 失败时，尝试使用本地日志作为降级方案

    Args:
        payload: Webhook payload
        route_name: 当前路由名称
    """
    project_id = payload.get('project', {}).get('id')
    pipeline_id = payload['object_attributes']['id']
    pipeline_iid = payload['object_attributes']['iid']
    project_name = payload.get('project', {}).get('name', '')
    path_with_namespace = payload.get('project', {}).get('path_with_namespace', '')
    branch = payload['object_attributes']['ref']
    user_name = payload.get('user', {}).get('name', 'unknown')
    detail_url = payload['object_attributes']['url']
    created_at = payload['object_attributes']['created_at']
    finished_at = payload.get('object_attributes', {}).get('finished_at', '')
    start_time = convert_utc_to_utc8(created_at)
    end_time = convert_utc_to_utc8(finished_at) if finished_at else ''

    branch_clean = branch.replace('refs/heads/', '').replace('refs/tags/', '').replace('refs/remotes/', '')

    log_content = None
    error_info = None
    log_source = None
    failed_job_name = ""

    if project_id:
        success, job_id, error_msg, job_name = get_failed_job_id(pipeline_id, project_id)
        if success and job_id:
            failed_job_name = job_name
            success_get_logs, log_content = get_job_logs(project_id, job_id, max_lines=100)
            if success_get_logs and log_content:
                log_source = 'gitlab_api'
                error_info = parse_error_from_logs(log_content)
                app_logger.info(f"成功从 GitLab API 获取 Job {job_id} ({job_name}) 的日志，长度: {len(log_content)}")
            else:
                app_logger.warning(f"从 GitLab API 获取日志失败: {log_content}，将尝试本地日志")
        else:
            app_logger.warning(f"获取失败 Job ID 失败: {error_msg}，将尝试本地日志")
    else:
        app_logger.warning("无法获取 project_id，将尝试本地日志")

    if not error_info:
        app_logger.info(f"尝试从本地获取日志: project={project_name}, pipeline_iid={pipeline_iid}")
        local_logs = get_build_logs(project_name, branch_clean, pipeline_iid)
        if local_logs and local_logs.get('exists') and local_logs.get('full_log'):
            log_content = local_logs.get('full_log', '')
            log_source = 'local_file'
            if not failed_job_name and local_logs.get('failed_job_name'):
                failed_job_name = local_logs.get('failed_job_name', '')
            error_info = parse_error_from_logs(log_content)
            app_logger.info(f"成功从本地获取日志，长度: {len(log_content)}")
        else:
            app_logger.warning("本地日志也不存在")

    if not error_info:
        error_info = {
            'summary': '无法获取构建日志（GitLab API 和本地日志均不可用）',
            'last_error_context': '',
            'error_line': ''
        }

    error_message = format_error_log_message(
        project_name=project_name,
        pipeline_iid=pipeline_iid,
        branch=branch_clean,
        error_info=error_info,
        detail_url=detail_url,
        user_name=user_name,
        start_time=start_time,
        end_time=end_time
    )

    try:
        chat_id = ROUTE_CHAT_ID_MAP.get(route_name)
        result = send_notification(route_name, error_message, chat_id=chat_id)
        if result.get('success'):
            app_logger.info(f"已发送错误日志消息 (日志来源: {log_source or '无'}, method={result.get('method')})")
        else:
            app_logger.error(f"发送错误日志消息失败")
    except Exception as e:
        app_logger.error(f"发送错误日志消息失败: {str(e)}")

    if log_content and log_source:
        try:
            save_build_logs(
                project_name=project_name,
                branch=branch_clean,
                pipeline_iid=pipeline_iid,
                log_content=log_content,
                error_summary=error_info.get('last_error_context', '') if error_info else '',
                failed_job_name=failed_job_name
            )
            app_logger.info(f"已保存构建日志到本地 (来源: {log_source})")
        except Exception as e:
            app_logger.error(f"保存构建日志失败: {str(e)}")
