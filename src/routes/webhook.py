import json
import uuid
import logging
from flask import request, jsonify
from logger import webhook_logger, monitor_logger
from logger.context import set_request_context, clear_request_context

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
from src.config import WEBHOOK_CONFIG, DEFAULT_TARGET_URL, ROUTE_CHAT_ID_MAP, get_config
from src.utils import convert_utc_to_utc8


def process_webhook(request, route_name, subpath=None):
    """
    处理webhook请求的通用逻辑
    """
    import logging
    global app_logger
    app_logger = logging.getLogger('app_logger')

    if not request.is_json:
        return jsonify({"error": "无效的JSON格式"}), 400

    try:
        set_request_context(request_id=uuid.uuid4().hex[:8], route_name=route_name)

        raw_body = request.get_data(as_text=True)
        webhook_logger.log_request(route_name, request.headers, raw_body)
        payload = request.get_json()

        set_request_context(
            project_name=payload.get('project', {}).get('name', ''),
            pipeline_iid=payload.get('object_attributes', {}).get('iid', '')
        )

        object_kind = payload.get('object_kind')
        
        if object_kind == 'push':
            try:
                project_name = payload.get('project', {}).get('name', '')
                ref = payload.get('ref', '')
                user_name = payload.get('user_name', '')
                commits = payload.get('commits', [])
                git_url = payload.get('project', {}).get('web_url', '')
                
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
                
                record_push_event(push_record, push_records, push_records_lock)
                
                monitor_logger.log_event(route_name, request.headers, json.dumps(push_record))
                
                return jsonify({"message": f"已接收并处理 {route_name} 的推送事件"}), 200
            except Exception as e:
                error_msg = f"webhook | push_event_failed | error={e}"
                monitor_logger.log_event(route_name, request.headers, error_msg)
                from logger import app_logger
                app_logger.error(error_msg)
                return jsonify({"error": error_msg}), 500
        else:
            try:
                source = payload.get('object_attributes', {}).get('source', '')
                if source in ('parent_pipeline', 'merge_request_event'):
                    app_logger.info(f"webhook | skip_source | source={source}, route={route_name}")
                    return jsonify({"message": f"已忽略 {source} 类型事件"}), 200

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
                        app_logger.info(f"webhook | send_result | route={route_name}, method={result.get('method')}, success={result.get('success')}")

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
                                app_logger.error(f"webhook | trigger_action_failed | error={e}")
                except Exception as e:
                    app_logger.error(f"webhook | format_message_failed | error={e}")
                
                return jsonify({"message": f"已接收并处理 {route_name} 的流水线事件"}), 200
            except KeyError as e:
                error_msg = f"缺少必要字段: {str(e)}"
                monitor_logger.log_event(route_name, request.headers, error_msg)
                return jsonify({"error": error_msg}), 500
            except Exception as e:
                error_msg = f"处理流水线事件出错: {str(e)}"
                monitor_logger.log_event(route_name, request.headers, error_msg)
                return jsonify({"error": error_msg}), 500
    except Exception as e:
        error_msg = f"处理webhook出错: {str(e)}"
        monitor_logger.log_event(route_name, request.headers, error_msg)
        return jsonify({"error": error_msg}), 500
    finally:
        clear_request_context()


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
                app_logger.info(f"webhook | fetch_job_log | source=gitlab_api, job_id={job_id}, job_name={job_name}, log_size={len(log_content)}")
            else:
                app_logger.warning(f"webhook | fetch_job_log_failed | source=gitlab_api, error={log_content}, fallback=local")
        else:
            app_logger.warning(f"webhook | get_failed_job_failed | error={error_msg}, fallback=local")
    else:
        app_logger.warning(f"webhook | fetch_job_log | project_id=missing, fallback=local")

    if not error_info:
        app_logger.info(f"webhook | fetch_job_log | source=local, project={project_name}, pipeline_iid={pipeline_iid}")
        local_logs = get_build_logs(project_name, branch_clean, pipeline_iid)
        if local_logs and local_logs.get('exists') and local_logs.get('full_log'):
            log_content = local_logs.get('full_log', '')
            log_source = 'local_file'
            if not failed_job_name and local_logs.get('failed_job_name'):
                failed_job_name = local_logs.get('failed_job_name', '')
            error_info = parse_error_from_logs(log_content)
            app_logger.info(f"webhook | fetch_job_log | source=local, log_size={len(log_content)}")
        else:
            app_logger.warning(f"webhook | fetch_job_log | source=local, result=not_found")

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
        chat_id = ROUTE_CHAT_ID_MAP.get('default_webhook') or ROUTE_CHAT_ID_MAP.get(route_name)
        if not chat_id:
            notify_config = get_config().get('notify_config', {})
            chat_id = notify_config.get('route_chat_id_map', {}).get('default_webhook')
        if chat_id and not ROUTE_CHAT_ID_MAP.get('default_webhook'):
            app_logger.info(f"webhook | error_log_fallback | chat_source=default_webhook, chat_id={chat_id}")
        result = send_notification(route_name, error_message, chat_id=chat_id)
        if result.get('success'):
            app_logger.info(f"webhook | error_log_sent | log_source={log_source or '-'}, method={result.get('method')}")
        else:
            app_logger.error(f"webhook | error_log_send_failed | project={project_name}, pipeline_iid={pipeline_iid}")
    except Exception as e:
        app_logger.error(f"webhook | error_log_send_failed | error={e}")

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
            app_logger.info(f"webhook | save_build_log | source={log_source}")
        except Exception as e:
            app_logger.error(f"webhook | save_build_log_failed | error={e}")
