from flask import request, jsonify, render_template
from src.routes.webhook import process_webhook


def register_routes(app):
    # 延迟导入app_logger，避免循环导入问题
    from logger import app_logger
    """
    注册所有路由
    """
    # Webhook 路由 - vendor_bot
    @app.route('/vendor_bot/v2/<path:subpath>', methods=['POST'])
    def vendor_bot_v2_subpath_route(subpath):
        """
        处理 vendor_bot/v2/<subpath> 格式的 Webhook 请求
        """
        route_name = f'vendor_bot/v2/{subpath}'
        return process_webhook(request, route_name, subpath)
    
    @app.route('/vendor_bot/v2', methods=['POST'])
    def vendor_bot_v2_route():
        """
        处理 vendor_bot/v2 格式的 Webhook 请求
        """
        route_name = 'vendor_bot/v2'
        return process_webhook(request, route_name, None)
    
    @app.route('/vendor_bot', methods=['POST'])
    def vendor_bot_v1_route():
        """
        处理 vendor_bot 格式的 Webhook 请求
        """
        route_name = 'vendor_bot'
        return process_webhook(request, route_name, None)
    
    @app.route('/vendor_bot/itreporter', methods=['POST'])
    def vendor_bot_itreporter_route():
        """
        处理来自远程服务器的JSON请求
        接收JSON数据，根据report_path从MinIO下载文件到本地
        """
        from src.services.message import send_notification
        from src.services.report_parser import parse_health_check_report, build_inspection_card_elements
        from src.config import WEBHOOK_CONFIG, DEFAULT_TARGET_URL, MINIO_CONFIG, ROUTE_CHAT_ID_MAP
        from minio import Minio
        from minio.error import S3Error
        import json
        import os
        from datetime import timedelta
        
        try:
            # 获取JSON请求数据
            json_data = request.get_json()
            
            if json_data is None:
                return jsonify({'status': 'error', 'message': '无效的JSON数据'}), 400
            
            # 获取report_path字段
            report_path = json_data.get('report_path')
            if not report_path:
                return jsonify({'status': 'error', 'message': '缺少report_path字段'}), 400
            
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
            
            # 生成预签名URL（有效期2小时）
            try:
                presigned_url = minio_client.presigned_get_object(
                    bucket_name, 
                    report_path, 
                    expires=timedelta(hours=2)
                )
                
                file_size = os.path.getsize(local_filepath)
                file_size_mb = round(file_size / (1024 * 1024), 2)

                report_data = None
                try:
                    with open(local_filepath, 'r', encoding='utf-8') as f:
                        html_content = f.read()
                    report_data = parse_health_check_report(html_content)
                    if report_data:
                        app_logger.info(f"巡检报告解析成功: 正常={report_data['ok_count']}, 警告={report_data['warning_count']}, 严重={report_data['critical_count']}")
                    else:
                        app_logger.warning("巡检报告解析返回空结果")
                except Exception as e:
                    app_logger.error(f"解析巡检报告失败: {str(e)}")

                card_elements, card_template = build_inspection_card_elements(
                    report_data, presigned_url, file_size_mb, report_path
                )

                feishu_card_message = {
                    "schema": "2.0",
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": "📊 IT系统健康巡检报告"
                        },
                        "subtitle": {
                            "tag": "plain_text",
                            "content": os.path.basename(report_path)
                        },
                        "template": card_template
                    },
                    "body": {
                        "elements": card_elements
                    },
                    "config": {
                        "update_multi": True
                    }
                }
                
                route_name = 'vendor_bot/itreporter'
                chat_id = ROUTE_CHAT_ID_MAP.get(route_name)
                if presigned_url:
                    try:
                        result = send_notification(route_name, feishu_card_message, chat_id=chat_id)
                        if result.get('success'):
                            app_logger.info(f"IT报告通知发送成功: method={result.get('method')}")
                        else:
                            app_logger.error(f"IT报告通知发送失败")
                    except Exception as e:
                        app_logger.error(f"❌ 飞书消息发送失败: {str(e)}")
                else:
                    app_logger.warning(f"⚠️ 未发送飞书消息: presigned_url={presigned_url}")

                
            except Exception as e:
                app_logger.error(f"生成预签名URL失败: {str(e)}")
                presigned_url = None
            
            # 返回成功响应，包含预签名URL
            response_data = {
                'status': 'success', 
                'message': '文件从MinIO下载成功',
                'data': {
                    'file_size': os.path.getsize(local_filepath),
                    'presigned_url': presigned_url,
                    'url_expires_in': '2小时' if presigned_url else None,
                    'feishu_message_sent': presigned_url is not None
                }
            }
            
            return jsonify(response_data), 200
            
        except S3Error as e:
            return jsonify({
                'status': 'error', 
                'message': f'MinIO错误: {str(e)}'
            }), 500
        except Exception as e:
            return jsonify({
                'status': 'error', 
                'message': str(e)
            }), 500
    
    @app.route('/monitor/event', methods=['POST'])
    def monitor_event_route():
        """
        处理来自alertmanager的监控事件请求
        """
        from src.services import (
            monitor_logger, 
            parse_alertmanager_request, 
            send_monitor_message
        )
        from src.config import WEBHOOK_CONFIG
        import json
        from datetime import datetime
        
        try:
            # 获取原始请求体
            request_body = request.get_data().decode('utf-8')
            
            # 使用专用的监控日志记录器记录请求信息
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
                target_url = WEBHOOK_CONFIG.get('/monitor/event')
                if target_url:
                    # 发送格式化后的消息到默认webhook
                    send_monitor_message(target_url, formatted_message)
            except json.JSONDecodeError:
                # 请求体不是有效的JSON格式，不影响日志记录
                pass
            
            # 返回成功响应
            return jsonify({'status': 'success', 'message': '事件已接收并记录'}), 200
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
    
    @app.route('/monitor/event/api/v2/alerts', methods=['POST'])
    def monitor_event_v2_alerts_route():
        """
        处理来自 alertmanager 的 alerts v2 API 请求
        遵循 Alertmanager Webhook API v2 规范
        参考: https://prometheus.io/docs/alerting/latest/webhook/#webhook-api-v2
        """
        from src.services import monitor_logger
        from datetime import datetime
        import json
        
        try:
            request_body = request.get_data().decode('utf-8')
            
            monitor_logger.log_event(
                route_name='monitor/event/api/v2/alerts',
                request_headers=request.headers,
                request_body=request_body
            )
            
            try:
                data = json.loads(request_body)
                
                if isinstance(data, list):
                    version = 'v2 (array format)'
                    alerts = data
                    app_logger.info(
                        f"收到 Alertmanager {version} 告警事件, alerts_count={len(alerts)}"
                    )
                    
                    for alert in alerts:
                        if isinstance(alert, dict):
                            alert_status = alert.get('status', '')
                            alert_name = alert.get('labels', {}).get('alertname', 'unknown') if isinstance(alert.get('labels'), dict) else 'unknown'
                            app_logger.info(
                                f"告警: {alert_name}, status={alert_status}, "
                                f"startsAt={alert.get('startsAt')}, endsAt={alert.get('endsAt')}"
                            )
                else:
                    version = data.get('version', 'v1')
                    group_key = data.get('groupKey', '')
                    status = data.get('status', '')
                    receiver = data.get('receiver', '')
                    alerts = data.get('alerts', [])
                    
                    app_logger.info(
                        f"收到 Alertmanager {version} 告警事件: "
                        f"groupKey={group_key}, status={status}, receiver={receiver}, alerts_count={len(alerts)}"
                    )
                    
                    for alert in alerts:
                        alert_status = alert.get('status', '')
                        alert_name = alert.get('labels', {}).get('alertname', 'unknown') if isinstance(alert.get('labels'), dict) else 'unknown'
                        app_logger.info(
                            f"告警: {alert_name}, status={alert_status}, "
                            f"startsAt={alert.get('startsAt')}, endsAt={alert.get('endsAt')}"
                        )
                    
            except json.JSONDecodeError:
                app_logger.warning(f"收到无效的JSON请求体: {request_body[:200]}")
            
            return jsonify({'status': 'success'}), 200
            
        except Exception as e:
            app_logger.error(f"处理 /monitor/event/api/v2/alerts 请求失败: {str(e)}")
            import traceback
            app_logger.error(traceback.format_exc())
            
            try:
                monitor_logger.log_event(
                    route_name='monitor/event/api/v2/alerts',
                    request_headers=request.headers,
                    request_body=str({'error': str(e)})
                )
            except:
                pass
            
            return jsonify({'status': 'failed', 'error': str(e)}), 200
    
    @app.route('/pipelines/records', methods=['GET'])
    def pipeline_records_route():
        """
        获取所有流水线记录
        """
        from src.services import pipeline_records, pipeline_records_lock
        from src.services.database import PipelineRecordDB

        try:
            db_records = PipelineRecordDB.get_all()

            with pipeline_records_lock:
                memory_records = list(pipeline_records.values())

            all_records = db_records if db_records else memory_records

            return jsonify({
                'status': 'success',
                'data': all_records,
                'count': len(all_records)
            }), 200
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/pipelines/records/view')
    @app.route('/pipelines/records/view/<namespace>')
    def pipeline_records_view_route(namespace=None, subpath=None):
        """
        以HTML页面形式展示所有流水线记录
        支持按命名空间筛选：/pipelines/records/view/PD4
        """
        from src.services import pipeline_records, pipeline_records_lock
        from src.services.database import PipelineRecordDB
        from datetime import datetime

        try:
            db_records = PipelineRecordDB.get_all()

            with pipeline_records_lock:
                memory_records = list(pipeline_records.values())

            records_list = db_records if db_records else memory_records
        except Exception as e:
            app_logger.error(f"获取流水线记录失败: {str(e)}")
            with pipeline_records_lock:
                records_list = list(pipeline_records.values())

        if namespace:
            records_list = [
                record for record in records_list
                if (namespace in (record.get('subpath') or '') or namespace in (record.get('namespace') or ''))
            ]

            if not records_list:
                app_logger.info(f"未找到命名空间 {namespace} 的记录")
                return render_template('no_records.html', namespace=namespace)
        
        # 排序：先按record_time降序，无record_time的按project_name升序
        def get_sort_key(x):
            project_name = x.get('project_name', '').lower()
            record_time = x.get('record_time')
            
            if not record_time:
                # 没有record_time的记录，使用0作为默认时间戳（对应1970-01-01 00:00:00 UTC）
                return (0, project_name)
            
            try:
                # 尝试直接解析ISO格式
                if 'T' in record_time and 'Z' in record_time:
                    # ISO 8601格式，如：2023-01-01T00:00:00.000Z
                    dt = datetime.strptime(record_time, '%Y-%m-%dT%H:%M:%S.%fZ')
                    return (-dt.timestamp(), project_name)
                elif 'T' in record_time:
                    # ISO 8601格式，如：2023-01-01T00:00:00.000
                    dt = datetime.strptime(record_time.split('.')[0], '%Y-%m-%dT%H:%M:%S')
                    return (-dt.timestamp(), project_name)
                elif '+' in record_time:
                    # 带时区的普通格式，如：2023-01-01 00:00:00 +0800
                    dt = datetime.strptime(record_time[:19], '%Y-%m-%d %H:%M:%S')
                    return (-dt.timestamp(), project_name)
                else:
                    # 普通格式，如：2023-01-01 00:00:00
                    dt = datetime.strptime(record_time[:19], '%Y-%m-%d %H:%M:%S')
                    return (-dt.timestamp(), project_name)
            except Exception as e:
                # 所有解析都失败，使用0作为默认时间戳
                return (0, project_name)
        
        records_list.sort(key=get_sort_key)
        
        app_logger.info(f"生成的HTML页面包含 {len(records_list)} 条记录")
        return render_template('base.html', records=records_list)
    
    @app.route('/pipelines/records/json')
    def pipeline_records_json_route():
        """
        获取JSON格式的流水线记录
        """
        from src.services import pipeline_records, pipeline_records_lock
        from src.services.database import PipelineRecordDB

        try:
            db_records = PipelineRecordDB.get_all()

            with pipeline_records_lock:
                memory_records = list(pipeline_records.values())

            all_records = db_records if db_records else memory_records

            return jsonify({
                'status': 'success',
                'data': all_records,
                'count': len(all_records)
            }), 200
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/push_records/latest/<path:git_url>', methods=['GET'])
    def latest_push_record_route(git_url):
        """
        获取特定项目的最近10条push记录
        """
        from src.services import push_records, push_records_lock
        from src.services.database import PushRecordDB
        import json
        import urllib.parse

        try:
            decoded_git_url = urllib.parse.unquote(git_url)

            db_records = PushRecordDB.get_by_git_url(decoded_git_url, limit=10)

            if db_records:
                for record in db_records:
                    if record.get('commits') and isinstance(record['commits'], str):
                        record['commits'] = json.loads(record['commits'])
                return jsonify({
                    'status': 'success',
                    'data': db_records,
                    'count': len(db_records)
                }), 200

            with push_records_lock:
                project_push_records = [
                    record for record in push_records
                    if record.get('git_url') == decoded_git_url
                ]

            if project_push_records:
                project_push_records.sort(key=lambda x: x.get('push_time', ''), reverse=True)
                latest_records = project_push_records[:10]
                return jsonify({
                    'status': 'success',
                    'data': latest_records,
                    'count': len(latest_records)
                }), 200
            else:
                return jsonify({
                    'status': 'success',
                    'data': [],
                    'message': f'未找到git_url为 {decoded_git_url} 的push记录'
                }), 200
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500
    
    @app.route('/api/cd-records', methods=['GET'])
    def cd_records_api():
        """
        获取CD记录API，返回存在pipeline_iid的记录
        支持根据subpath参数筛选记录
        """
        from src.services import push_records, push_records_lock
        from src.services.database import PushRecordDB
        import json
        import logging

        try:
            subpath_filter = request.args.get('subpath')

            db_records = PushRecordDB.get_cd_records(subpath=subpath_filter)

            all_records = []
            if db_records:
                all_records = db_records
            else:
                with push_records_lock:
                    all_records = push_records.copy()

            cd_records = []
            for record in all_records:
                if subpath_filter and record.get('subpath') != subpath_filter:
                    continue

                commits = record.get('commits', [])
                if isinstance(commits, str):
                    try:
                        commits = json.loads(commits)
                    except:
                        commits = []

                if isinstance(commits, list):
                    for commit in commits:
                        if commit.get('pipeline_iid'):
                            deploy_ips = []
                            stages = commit.get('stages', [])
                            if isinstance(stages, str):
                                try:
                                    stages = json.loads(stages)
                                except:
                                    stages = []
                            for stage in stages:
                                if isinstance(stage, dict):
                                    ip = stage.get('deploy_ip')
                                    if ip:
                                        if isinstance(ip, list):
                                            deploy_ips.extend(ip)
                                        else:
                                            deploy_ips.append(ip)

                            deploy_ips = list(set(deploy_ips))

                            if commit.get('pipeline_iid') and deploy_ips:
                                original_ref = record.get('ref', '')
                                processed_ref = original_ref
                                if processed_ref.startswith('refs/heads/'):
                                    processed_ref = processed_ref.replace('refs/heads/', '')
                                elif processed_ref.startswith('refs/tags/'):
                                    processed_ref = processed_ref.replace('refs/tags/', '')

                                cd_record = {
                                    'project_name': record.get('project_name', ''),
                                    'ref': processed_ref,
                                    'user_name': record.get('user_name', ''),
                                    'pipeline_iid': commit.get('pipeline_iid'),
                                    'push_time': commit.get('timestamp', ''),
                                    'pipeline_status': commit.get('pipeline_status', ''),
                                    'deploy_ips': deploy_ips,
                                    'message': commit.get('message', ''),
                                    'subpath': record.get('subpath', '')
                                }
                                cd_records.append(cd_record)

            return jsonify({
                'status': 'success',
                'records': cd_records,
                'count': len(cd_records)
            }), 200
        except Exception as e:
            logging.error(f"获取CD记录失败: {str(e)}")
            import traceback
            logging.error(traceback.format_exc())

            return jsonify({
                'status': 'error',
                'message': str(e),
                'records': [],
                'count': 0
            }), 200
    
    @app.route('/cd-records')
    @app.route('/cd-records/<path:return_url>')
    def cd_records_view(return_url=None):
        """
        CD记录管理页面
        """
        if return_url:
            return_url = return_url.replace('__', '/')
            full_return_url = f"/pipelines/records/view/{return_url}"
        else:
            full_return_url = "/pipelines/records/view"
        return render_template('cd_records.html', return_url=full_return_url)

    @app.route('/api/build-logs/<path:project>/<int:pipeline_iid>', methods=['GET'])
    def get_build_logs_api(project, pipeline_iid):
        """
        获取构建日志 API
        """
        from src.services import get_build_logs
        import urllib.parse

        decoded_project = urllib.parse.unquote(project)

        parts = decoded_project.rsplit('/', 1)
        if len(parts) == 2:
            project_name = parts[1]
        else:
            project_name = decoded_project

        result = get_build_logs(project_name, '', pipeline_iid)

        if result and result.get('exists'):
            return jsonify({
                'status': 'success',
                'data': {
                    'full_log': result.get('full_log', ''),
                    'error_summary': result.get('error_summary', ''),
                    'failed_job_name': result.get('failed_job_name', ''),
                    'project_name': project_name,
                    'pipeline_iid': pipeline_iid
                }
            }), 200
        else:
            return jsonify({
                'status': 'error',
                'message': '日志文件不存在'
            }), 404

    @app.route('/api/feishu/card-action', methods=['POST'])
    def feishu_card_action_route():
        """
        处理飞书卡片动作回调
        当用户点击卡片中的 callback 按钮时，飞书会向此端点发送回调请求
        """
        import json
        try:
            data = request.get_json(force=True)
            app_logger.info(f"收到飞书卡片动作回调: {json.dumps(data, ensure_ascii=False)[:500]}")

            if data.get('type') == 'url_verification':
                return jsonify({"challenge": data.get('challenge')})

            from src.services.feishu_notify import handle_card_action_callback
            result = handle_card_action_callback(data)

            if result:
                return jsonify(result)
            else:
                return jsonify({})

        except Exception as e:
            app_logger.error(f"处理飞书卡片动作回调失败: {str(e)}")
            return jsonify({})

    @app.route('/api/build-logs/<path:project>/<int:pipeline_iid>/download', methods=['GET'])
    def download_build_logs_api(project, pipeline_iid):
        """
        下载构建日志 API
        """
        from src.services import get_log_file_path_for_download
        from flask import send_file
        import urllib.parse

        decoded_project = urllib.parse.unquote(project)

        parts = decoded_project.rsplit('/', 1)
        if len(parts) == 2:
            project_name = parts[1]
        else:
            project_name = decoded_project

        log_path = get_log_file_path_for_download(project_name, '', pipeline_iid)

        if log_path:
            filename = f"{project_name}_pipeline_{pipeline_iid}.log"
            return send_file(
                log_path,
                mimetype='text/plain',
                as_attachment=True,
                download_name=filename
            )
        else:
            return jsonify({
                'status': 'error',
                'message': '日志文件不存在'
            }), 404

