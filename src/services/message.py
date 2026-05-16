import json
import logging
import requests
from src.utils import format_duration, calculate_interval, convert_utc_to_utc8, find_similar_pipeline_records
from src.config import WEBHOOK_CONFIG, DEFAULT_TARGET_URL, ROUTE_CHAT_ID_MAP


def send_formatted_message(target_url, message):
    headers = {'Content-Type': 'application/json'}
    response = requests.post(target_url, headers=headers, data=json.dumps(message))
    if response.status_code not in [200, 201]:
        raise Exception(f"Failed to send message. Status code: {response.status_code}, Response: {response.text}")
    return True


def convert_webhook_card_to_api_card(webhook_message):
    card = webhook_message.get('card', {})
    header = card.get('header', {})
    i18n_elements = card.get('i18n_elements', {})
    zh_cn_elements = i18n_elements.get('zh_cn', [])

    api_elements = []
    for element in zh_cn_elements:
        tag = element.get('tag', '')
        if tag == 'markdown':
            api_elem = {
                "tag": "markdown",
                "content": element.get('content', '')
            }
            text_align = element.get('text_align')
            text_size = element.get('text_size')
            icon = element.get('icon')
            if text_align:
                api_elem["text_align"] = text_align
            if text_size:
                api_elem["text_size"] = text_size
            if icon:
                api_elem["icon"] = icon
            api_elements.append(api_elem)
        elif tag == 'hr':
            api_elements.append({"tag": "hr"})
        elif tag == 'action':
            actions = element.get('actions', [])
            for action_item in actions:
                if action_item.get('tag') == 'button':
                    btn = {
                        "tag": "button",
                        "type": action_item.get('type', 'default'),
                        "text": action_item.get('text', {}),
                    }
                    url = action_item.get('url') or (action_item.get('multi_url') or {}).get('url')
                    if url:
                        btn["behaviors"] = [
                            {
                                "type": "open_url",
                                "default_url": url,
                                "android_url": url,
                                "ios_url": url,
                                "pc_url": url
                            }
                        ]
                    api_elements.append(btn)
        else:
            api_elements.append(element)

    subtitle_val = header.get('subtitle', '')
    if isinstance(subtitle_val, dict):
        subtitle_val = subtitle_val.get('content', '')

    text_tag_list = header.get('text_tag_list', [])
    icon_token = header.get('icon_token') or (header.get('ud_icon') or {}).get('token', '')
    icon_tag = header.get('ud_icon', {}).get('tag', '')
    api_header = {
        "title": header.get('title', {}),
        "template": header.get('template', 'blue')
    }
    if subtitle_val:
        api_header["subtitle"] = {"tag": "plain_text", "content": subtitle_val}
    if icon_token and icon_tag == 'standard_icon':
        api_header["icon"] = {
            "tag": "standard_icon",
            "token": icon_token
        }
    if text_tag_list:
        api_header["text_tag_list"] = text_tag_list

    card_content = {
        "schema": "2.0",
        "header": api_header,
        "body": {
            "elements": api_elements
        }
    }

    config = card.get('config', {})
    if config.get('update_multi'):
        card_content["config"] = {"update_multi": True}

    template = header.get('template', '')
    if template == 'wathet':
        if "config" not in card_content:
            card_content["config"] = {}
        card_content["config"]["streaming_mode"] = True

    card_link = card.get('card_link')
    if card_link:
        card_content["card_link"] = card_link

    return card_content


import copy
import re


def _strip_commit_from_webhook_message(message):
    if message.get('msg_type') != 'interactive' or 'card' not in message:
        return message
    stripped = copy.deepcopy(message)
    i18n = stripped['card'].get('i18n_elements', {})
    for lang, elements in i18n.items():
        new_elements = []
        for elem in elements:
            if elem.get('tag') == 'markdown' and elem.get('content', '').startswith('***Commit***'):
                continue
            if elem.get('tag') == 'markdown':
                content = elem.get('content', '')
                content = re.sub(r'<at\s+id="[^"]*"\s*/>\s*</at>', '', content).strip()
                if content:
                    elem = dict(elem)
                    elem['content'] = content
            new_elements.append(elem)
        stripped['card']['i18n_elements'][lang] = new_elements
    return stripped


def send_notification(route_name, message, chat_id=None, message_id=None, callback_id=None):
    app_logger = logging.getLogger('app_logger')

    if message.get('schema') == '2.0':
        card_content = message
    else:
        card_content = convert_webhook_card_to_api_card(message)

    if message_id and callback_id:
        try:
            from src.services.feishu_notify import update_card_via_api
            result = update_card_via_api(card_content, message_id, callback_id)
            if result and result.get('success'):
                app_logger.info(f"飞书通知通过API更新成功: route={route_name}, method=api_update")
                return {
                    "success": True,
                    "method": "api_update",
                    "message_id": message_id
                }
            else:
                app_logger.warning(f"飞书通知API更新失败，降级为Webhook: route={route_name}")
        except Exception as e:
            app_logger.warning(f"飞书通知API更新异常，降级为Webhook: route={route_name}, error={str(e)}")
    else:
        try:
            from src.services.feishu_notify import send_card_via_api
            result = send_card_via_api(
                card_content,
                chat_id=chat_id,
                callback_id=callback_id
            )
            if result and result.get('success'):
                app_logger.info(f"飞书通知通过API发送成功: route={route_name}, method=api")
                return {
                    "success": True,
                    "method": "api",
                    "message_id": result.get('message_id')
                }
            else:
                app_logger.warning(f"飞书通知API发送失败，降级为Webhook: route={route_name}")
        except Exception as e:
            app_logger.warning(f"飞书通知API发送异常，降级为Webhook: route={route_name}, error={str(e)}")

    target_url = WEBHOOK_CONFIG.get(route_name, DEFAULT_TARGET_URL)
    if not target_url:
        app_logger.error(f"路由 {route_name} 未配置目标URL，降级发送也失败")
        return {"success": False, "method": None, "message_id": None}

    try:
        webhook_message = _strip_commit_from_webhook_message(message)
        send_formatted_message(target_url, webhook_message)
        app_logger.info(f"飞书通知通过Webhook降级发送成功: route={route_name}, method=webhook")
        return {
            "success": True,
            "method": "webhook",
            "message_id": None
        }
    except Exception as e:
        app_logger.error(f"飞书通知Webhook降级发送也失败: route={route_name}, error={str(e)}")
        return {"success": False, "method": "webhook", "message_id": None}


def _find_similar_pipeline(project_name, branch, pipeline_iid, source, builds_name):
    """
    查找相似的pipeline记录
    """
    import logging
    app_logger = logging.getLogger('app_logger')
    
    pipeline_iid_prev = None
    if builds_name == "deploy_custom_branch":
        similar_records = find_similar_pipeline_records(project_name, branch, pipeline_iid, source)
        if similar_records:
            for record in similar_records:
                # 将上一个非WEB构建记录的IID重新赋值
                pipeline_iid_prev = record['pipeline_iid']
                # app_logger.info(f"找到相似pipeline记录: pipeline_iid={pipeline_iid_prev}, project_name={project_name}, branch={branch}")
                break  # 只取第一条记录
        else:
            # app_logger.info(f"未找到相同project_name={project_name}, branch={branch}的其他pipeline记录")
            pipeline_iid_prev = None
    return pipeline_iid_prev


def _build_text_tag_list(pipeline_id, pipeline_iid, source):
    """
    构建基础的text_tag_list
    """
    return [
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
    ]


def _record_running_build(running_builds, running_builds_lock, pipeline_iid, project_name, branch, user_name, start_time, detail_url, route_name, commit_url, app_logger, chat_id=None):
    if running_builds is not None and running_builds_lock is not None:
        from datetime import datetime
        try:
            with running_builds_lock:
                running_builds[pipeline_iid] = {
                    'pipeline_iid': pipeline_iid,
                    'project_name': project_name,
                    'branch': branch,
                    'user_name': user_name,
                    'start_time': datetime.now(),
                    'start_time_str': start_time,
                    'detail_url': detail_url,
                    'route_name': route_name,
                    'commit_url': commit_url,
                    'chat_id': chat_id,
                    'message_id': None,
                    'callback_id': f"pipeline_{pipeline_iid}"
                }
        except Exception as e:
            app_logger.error(f"❌ 记录运行中构建失败: {str(e)}")
            import traceback
            app_logger.error(traceback.format_exc())
    else:
        app_logger.warning(f"❌ running_builds或running_builds_lock为None，无法记录运行中构建")


def _remove_completed_build(running_builds, running_builds_lock, pipeline_iid, app_logger):
    """
    从running_builds中移除已完成的构建
    """
    if running_builds and running_builds_lock:
        try:
            # 移除已完成构建
            with running_builds_lock:
                if pipeline_iid in running_builds:
                    del running_builds[pipeline_iid]
                    app_logger.info(f"已移除完成构建: {pipeline_iid}")
        except Exception as e:
            app_logger.error(f"移除完成构建失败: {str(e)}")


def _find_deploy_ip(commit_url, push_records, push_records_lock, payload, app_logger):
    """
    查找部署IP
    """
    deploy_ip = ''
    if commit_url:
        try:
            # 从payload中提取当前的ref
            current_ref = payload.get('object_attributes', {}).get('ref', '')
            app_logger.info(f"从payload获取的当前ref: {current_ref}")
            
            # 处理current_ref，去除前缀
            processed_current_ref = current_ref
            if processed_current_ref.startswith('refs/heads/'):
                processed_current_ref = processed_current_ref.replace('refs/heads/', '')
            elif processed_current_ref.startswith('refs/tags/'):
                processed_current_ref = processed_current_ref.replace('refs/tags/', '')
            elif processed_current_ref.startswith('refs/remotes/'):
                processed_current_ref = processed_current_ref.replace('refs/remotes/', '')
            app_logger.info(f"处理后的当前ref: {processed_current_ref}")
            
            # 从push_records中查找对应的deploy_ip
            if push_records and push_records_lock:
                app_logger.info(f"从push_records中搜索deploy_ip，commit_url: {commit_url}，ref: {processed_current_ref}")
                with push_records_lock:
                    app_logger.info(f"当前push_records数量: {len(push_records)}")
                    found_deploy_ip = False
                    
                    for push_record in push_records:
                        # 处理push_record的ref，去除前缀
                        record_ref = push_record.get('ref', '')
                        processed_record_ref = record_ref
                        if processed_record_ref.startswith('refs/heads/'):
                            processed_record_ref = processed_record_ref.replace('refs/heads/', '')
                        elif processed_record_ref.startswith('refs/tags/'):
                            processed_record_ref = processed_record_ref.replace('refs/tags/', '')
                        elif processed_record_ref.startswith('refs/remotes/'):
                            processed_record_ref = processed_record_ref.replace('refs/remotes/', '')
                        
                        # 查找匹配的commit_url和相同ref的记录
                        commits = push_record.get('commits', [])
                        if not isinstance(commits, list):
                            continue
                        
                        # 首先检查ref是否匹配
                        if processed_record_ref == processed_current_ref:
                            # 然后查找commit_url匹配的记录
                            matching_commit = next((c for c in commits if c.get('url') == commit_url), None)
                            if matching_commit:
                                app_logger.info(f"在push_records中找到相同ref的匹配commit")
                                stages = matching_commit.get('stages', [])
                                deploy_stage = next((s for s in stages if isinstance(s, dict) and s.get('deploy_ip')), None)
                                if deploy_stage:
                                    # app_logger.info(f"检查stage: {s}")
                                    deploy_ip = deploy_stage.get('deploy_ip', '')
                                    if deploy_ip:
                                        # app_logger.info(f"从push_records中找到deploy_ip: {deploy_ip}")
                                        found_deploy_ip = True
                                        break
                            if found_deploy_ip:
                                break
            
            # 如果push_records中没有找到，尝试从payload中查找
            if not deploy_ip:
                # app_logger.info(f"在push_records中未找到deploy_ip，尝试从payload中查找")
                # 从payload的builds中查找deploy_ip
                builds = payload.get('builds', [])
                for build in builds:
                    if isinstance(build, dict) and build.get('stage', '').lower() == 'deploy':
                        deploy_ip = build.get('deploy_ip', '')
                        if deploy_ip:
                            app_logger.info(f"从payload中找到deploy_ip: {deploy_ip}")
                            break
            
            # 如果没有找到，尝试从variables中查找
            if not deploy_ip:
                variables = payload.get('object_attributes', {}).get('variables', [])
                for var in variables:
                    if isinstance(var, dict):
                        key = var.get('key', '')
                        value = var.get('value', '')
                        if key == 'DEPLOY_REMOTE_HOST' and value:
                            deploy_ip = value
                            app_logger.info(f"从variables中找到deploy_ip: {deploy_ip}")
                            break
        except Exception as e:
            app_logger.error(f"查找deploy_ip失败: {str(e)}")
            import traceback
            app_logger.error(traceback.format_exc())
    
    # 将deploy_ip数组转换为字符串格式
    if isinstance(deploy_ip, list):
        deploy_ip = ', '.join(deploy_ip)

    if deploy_ip:
        app_logger.info(f"部署IP: {deploy_ip}")

    return deploy_ip


def _replace_duration_with_deploy_ip(elements, deploy_ip, app_logger):
    """
    替换持续时间项为部署IP
    """
    for i, element in enumerate(elements):
        if element['icon'] == 'burnlife-notime_outlined':
            elements[i] = {
                'icon': 'location_outlined',
                'content': f"***部署机器***：{deploy_ip}",
            }
                # app_logger.info(f"将持续时间替换为部署IP: {deploy_ip}")
            break
    return elements


def _get_failed_stages(commit_url, push_records, push_records_lock, payload, app_logger):
    """
    获取失败的stage列表
    """
    failed_stages = []
    
    # 1. 优先从push_records中获取failed_stages
    if push_records and push_records_lock and commit_url:
        # app_logger.info(f"从push_records中获取commit_url: {commit_url}的failed_stages")
        with push_records_lock:
            # 查找匹配的commit
            matching_commit = None
            for push_record in push_records:
                commits = push_record.get('commits', [])
                if isinstance(commits, list):
                    matching_commit = next((c for c in commits if c.get('url') == commit_url), None)
                    if matching_commit:
                        break
            
            if matching_commit:
                # app_logger.info(f"在push_records中找到匹配的commit")
                stages = matching_commit.get('stages', [])
                # 从stages中获取status为failed的stage
                for stage in stages:
                    if isinstance(stage, dict):
                        stage_status = stage.get('status', '')
                        if stage_status == 'failed':
                            stage_name = stage.get('name', stage.get('stage', 'Unknown'))
                            failed_stages.append(stage_name)
                
                # app_logger.info(f"从push_records中获取的失败stage: {failed_stages}")
    
    # 2. 如果push_records中没有找到，从payload.get('builds', [])中获取
    if not failed_stages:
        # app_logger.info("在push_records中未找到failed_stages，从payload中获取")
        builds = payload.get('builds', [])
        
        for build in builds:
            if isinstance(build, dict):
                build_status = build.get('status', '')
                build_name = build.get('name', 'Unknown')
                # app_logger.info(f"检查构建: {build_name}, 状态: {build_status}")
                if build_status == 'failed':
                    failed_stages.append(build_name)
        
        # app_logger.info(f"从payload中获取的失败stage: {failed_stages}")
    
    # 3. 如果都没有找到，使用默认值"deploy"
    if not failed_stages:
        failed_stages = ["deploy"]
        app_logger.info("使用默认失败stage: deploy")
    
    return failed_stages


def _update_failed_status_tag_list(text_tag_list, payload, commit_url, push_records, push_records_lock, app_logger):
    """
    更新失败状态的text_tag_list
    """
    failed_stages = _get_failed_stages(commit_url, push_records, push_records_lock, payload, app_logger)
    
    # 添加失败的builds信息到text_tag_list中
    failed_stages_text = ', '.join(failed_stages)
    
    # 移除最后一项
    if text_tag_list:
        text_tag_list.pop()
    
    # 添加失败的stage信息作为新的最后一项
    text_tag_list.append({
        "tag": "text_tag",
        "text": {
            "tag": "plain_text",
            "content": f"failed job：{failed_stages_text}"
        },
        "color": "red"
    })
    
    return text_tag_list


def _build_message(project_name, subtitle, detail_url, message_config, text_tag_list):
    """
    构建最终的消息结构
    """
    return {
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
                    "content": subtitle
                },
                "text_tag_list": text_tag_list,
                "template": message_config['header']['template'],
                "ud_icon": {
                    "tag": "standard_icon",
                    "token": message_config['header']['icon_token'],
                }
            }
        }
    }


def format_message(payload, running_builds=None, running_builds_lock=None, route_name=None, push_records=None, push_records_lock=None):
    """
    格式化消息
    """
    import logging
    # 使用标准的logging模块，避免导入问题
    app_logger = logging.getLogger('app_logger')
    
    # 1. 提取基本信息
    status = payload['object_attributes']['status']
    pipeline_id = payload['object_attributes']['id']
    pipeline_iid = payload['object_attributes']['iid']
    created_at = payload['object_attributes']['created_at']
    start_time = convert_utc_to_utc8(created_at)
    user_name = payload.get('user', {}).get('name', 'unknown')
    branch = payload['object_attributes']['ref']
    detail_url = payload['object_attributes']['url']
    commit_title = payload.get('commit', {}).get('title', 'unknown')
    project_name = payload.get('project', {}).get('name', 'unknown')
    # 安全获取builds_name，避免列表索引超出范围
    builds_name = payload['builds'][0]['name'] if payload.get('builds') and len(payload['builds']) > 0 else "unknown"
    source = payload['object_attributes']['source']
    
    # 获取commit_url用于查找push_records
    commit_url = payload.get('commit', {}).get('url', '')
    app_logger.info(f"当前commit_url: {commit_url}")

    # 2. 处理parent_pipeline类型，不生成通知
    if 'parent_pipeline' == source:
        return None

    # 3. 处理deploy_custom_branch类型，查找相似记录
    pipeline_iid_prev = _find_similar_pipeline(project_name, branch, pipeline_iid, source, builds_name)

    # 4. 根据不同状态处理
    if status == 'running':
        # 处理commit_title中的换行符，确保在markdown中正确显示
        formatted_commit_title = commit_title.replace('\n', '  \n')
        
        # 构建运行中状态的elements
        elements = [
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
                'content': f"***Commit***：{formatted_commit_title}",
            },
        ]
        
        # 记录运行中的构建
        chat_id = ROUTE_CHAT_ID_MAP.get(route_name)
        _record_running_build(running_builds, running_builds_lock, pipeline_iid, project_name, branch, user_name, start_time, detail_url, route_name, commit_url, app_logger, chat_id=chat_id)

        # 构建消息配置
        message_config = {
            'elements': elements,
            'header': {
                'template': 'wathet',
                'icon_token': 'bell_filled'
            },
        }
        
        # 构建副标题
        subtitle = f"Pipeline版本号：{pipeline_iid_prev if pipeline_iid_prev else pipeline_iid}"
        
        # 构建text_tag_list
        text_tag_list = _build_text_tag_list(pipeline_id, pipeline_iid, source)
        
        # 生成并返回消息
        return _build_message(project_name, subtitle, detail_url, message_config, text_tag_list)
    elif status in ['success', 'failed', 'canceled']:
        # 从running_builds中移除已完成的构建
        _remove_completed_build(running_builds, running_builds_lock, pipeline_iid, app_logger)
        
        # 对于canceled状态，不需要生成消息
        if status == 'canceled':
            return None
        
        # 计算持续时间
        duration = payload['object_attributes']['duration']
        if duration:
            duration = format_duration(duration)
        else:
            end_time = convert_utc_to_utc8(payload['object_attributes']['finished_at'])
            duration = calculate_interval(start_time, end_time)
        
        formatted_commit_title = commit_title.replace('\n', '  \n')

        display_user_name = user_name
        if status == 'failed':  
            display_user_name = f'<at id="{user_name}"></at>'

        elements = [
            {
                'icon': 'member_outlined',
                'content': f"***提交人员***：{display_user_name}",
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
            {
                'icon': 'doc_outlined',
                'content': f"***Commit***：{formatted_commit_title}",
            },
        ]
        
        # 查找deploy_ip
        deploy_ip = _find_deploy_ip(commit_url, push_records, push_records_lock, payload, app_logger)
        
        # 如果pipeline失败，替换持续时间项为部署IP
        if status == 'failed' and deploy_ip:
            elements = _replace_duration_with_deploy_ip(elements, deploy_ip, app_logger)
        
        # 构建消息配置
        message_config = {
            'elements': elements,
            'header': {
                'template': f"{'green' if status == 'success' else 'red'}",
                'icon_token': f"{'succeed_filled' if status == 'success' else 'error_filled'}"
            },
        }
        
        # 构建基础的text_tag_list
        text_tag_list = _build_text_tag_list(pipeline_id, pipeline_iid, source)
        
        # 如果状态是failed，更新text_tag_list
        if status == 'failed':
            text_tag_list = _update_failed_status_tag_list(text_tag_list, payload, commit_url, push_records, push_records_lock, app_logger)
        
        # 构建副标题，包含部署设备信息（如果有）
        subtitle = f"Pipeline版本号：{pipeline_iid_prev if pipeline_iid_prev else pipeline_iid}"
        if status == 'success' and deploy_ip:
            subtitle += f"，部署设备：{deploy_ip}"
            app_logger.info(f"副标题:{subtitle}")
        
        # 生成并返回消息
        message = _build_message(project_name, subtitle, detail_url, message_config, text_tag_list)
        app_logger.info(f"message:{message}")
        return message

    return None


def format_error_log_message(project_name, pipeline_iid, branch, error_info, detail_url, user_name, start_time, end_time):
    """
    构建错误日志详情消息

    Args:
        project_name: 项目名称
        pipeline_iid: Pipeline IID
        branch: 分支名称
        error_info: parse_error_from_logs 返回的错误信息
        detail_url: Pipeline 详情链接
        user_name: 构建人员
        start_time: 开始时间
        end_time: 结束时间

    Returns:
        dict: 飞书消息体
    """
    error_summary = error_info.get('summary', '')
    last_error_context = error_info.get('last_error_context', '')
    error_line = error_info.get('error_line', '')

    max_summary_lines = 30
    summary_lines = error_summary.split('\n')
    if len(summary_lines) > max_summary_lines:
        truncated_summary = '\n'.join(summary_lines[-max_summary_lines:])
        error_summary_display = f"...\n{truncated_summary}"
    else:
        error_summary_display = error_summary

    return {
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
                        "content": f"**📋 错误摘要**\n```\n{error_summary_display[:2000] if error_summary_display else '无日志内容'}\n```",
                        "text_align": "left",
                        "text_size": "normal"
                    },
                    {
                        "tag": "markdown",
                        "content": f"**⏱️ 时间**: {start_time} → {end_time}\n**👤 构建人员**: {user_name}",
                        "text_align": "left",
                        "text_size": "normal"
                    }
                ]
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"🔴 构建失败日志 - {project_name}"
                },
                "subtitle": {
                    "tag": "plain_text",
                    "content": f"Pipeline IID: {pipeline_iid} | 分支: {branch}"
                },
                "template": "red"
            }
        }
    }
