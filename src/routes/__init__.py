from flask import request, jsonify, render_template
from src.routes.webhook import process_webhook

def register_routes(app):
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
        from src.services import send_formatted_message
        from src.config import WEBHOOK_CONFIG, DEFAULT_TARGET_URL, MINIO_CONFIG
        from minio import Minio
        from minio.error import S3Error
        import json
        import os
        from datetime import timedelta
        
        try:
            # 获取JSON请求数据
            json_data = request.get_json()
            
            if json_data is None:
                return jsonify({'status': 'error', 'message': 'Invalid JSON data'}), 400
            
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
            
            # 生成预签名URL（有效期2小时）
            try:
                presigned_url = minio_client.presigned_get_object(
                    bucket_name, 
                    report_path, 
                    expires=timedelta(hours=2)
                )
                
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
                                    "tag": "markdown",
                                    "content": f"📁 报告路径 : {report_path} \n"
                                                f"🗄️ 存储桶 : {bucket_name} \n"
                                                f"📏 文件大小 : {file_size_mb} MB \n"
                                                f"⏱️ 有效期 : 2小时 \n\n"
                                                f"> ⚠️ **安全提醒**: 下载链接有效期为2小时，请尽快下载",
                                    "text_align": "left",
                                    "text_size": "normal"
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
                if target_url and presigned_url:
                    try:
                        send_formatted_message(target_url, feishu_card_message)
                    except Exception as e:
                        print(f"❌ 飞书消息发送失败: {str(e)}")
                else:
                    print(f"⚠️ 未发送飞书消息: target_url={target_url}, presigned_url={presigned_url}")

                
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
                    'url_expires_in': '2小时' if presigned_url else None,
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
    
    @app.route('/pipelines/records', methods=['GET'])
    def pipeline_records_route():
        """
        获取所有流水线记录
        """
        from src.services import pipeline_records, pipeline_records_lock
        
        try:
            with pipeline_records_lock:
                # 返回所有记录的列表
                records_list = list(pipeline_records.values())
                return jsonify({
                    'status': 'success',
                    'data': records_list,
                    'count': len(records_list)
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
        
        with pipeline_records_lock:
            # 获取所有记录的列表
            records_list = list(pipeline_records.values())
            
        # 合并筛选条件
        if namespace:
            records_list = [
                record for record in records_list 
                if (namespace in (record.get('subpath') or '') or namespace in (record.get('namespace') or ''))
            ]
        # 如果没有记录，尝试从project.json加载
        if not records_list:
            import os
            import json
            project_json_path = 'project.json'
            if os.path.exists(project_json_path):
                try:
                    with open(project_json_path, 'r', encoding='utf-8') as f:
                        project_data = json.load(f)
                        records_list = list(project_data.values())
                        print(f"从project.json加载了 {len(records_list)} 条记录")
                except Exception as e:
                    print(f"读取project.json失败: {str(e)}")
            else:
                # project.json不存在时使用默认示例记录
                records_list = [
                    {
                        'namespace': 'default',
                        'project_name': '示例项目',
                        'pipeline_path': '#',
                        'path_with_namespace': 'default/example-project',
                        'git_url': 'http://example.com/default/example-project',
                        'pipeline_iid': 'N/A',
                        'subpath': None
                    }
                ]
                print("添加了默认示例记录")
            
        print(f"生成的HTML页面包含 {len(records_list)} 条记录")
        return render_template('base.html', records=records_list)
    
    @app.route('/pipelines/records/json')
    def pipeline_records_json_route():
        """
        获取JSON格式的流水线记录
        """
        from src.services import pipeline_records, pipeline_records_lock
        
        try:
            with pipeline_records_lock:
                # 返回所有记录的列表
                records_list = list(pipeline_records.values())
                return jsonify({
                    'status': 'success',
                    'data': records_list,
                    'count': len(records_list)
                }), 200
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500

