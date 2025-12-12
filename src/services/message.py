import json
import requests
from src.utils import format_duration, calculate_interval, convert_utc_to_utc8, find_similar_pipeline_records
from src.config import WEBHOOK_CONFIG, DEFAULT_TARGET_URL




def send_formatted_message(target_url, message):
    """
    将格式化后的消息发送到指定URL
    """
    headers = {'Content-Type': 'application/json'}
    response = requests.post(target_url, headers=headers, data=json.dumps(message))
    if response.status_code not in [200, 201]:
        raise Exception(f"Failed to send message. Status code: {response.status_code}, Response: {response.text}")
    return True


def format_message(payload, running_builds=None, running_builds_lock=None, route_name=None, push_records=None, push_records_lock=None):
    """
    格式化消息
    """
    import logging
    # 使用标准的logging模块，避免导入问题
    app_logger = logging.getLogger('app_logger')
    
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
    app_logger.info(f"Current commit_url: {commit_url}")

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
            app_logger.info(f"未找到相同project_name={project_name}, branch={branch}的其他pipeline记录")

    if 'running' == status:
        # 记录运行中的构建
        # 处理commit_title中的换行符，确保在markdown中正确显示
        formatted_commit_title = commit_title.replace('\n', '  \n')
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
                    'content': f"***Commit***：{formatted_commit_title}",
                },
            ],
            'header': {
                'template': 'wathet',
                'icon_token': 'bell_filled'
            },
        }
        
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
                        'route_name': route_name
                    }
            except Exception as e:
                app_logger.error(f"❌ 记录运行中构建失败: {str(e)}")
                import traceback
                app_logger.error(traceback.format_exc())
        else:
            app_logger.warning(f"❌ running_builds或running_builds_lock为None，无法记录运行中构建")
    elif 'success' == status or 'failed' == status or 'canceled' == status:
        # 构建完成或取消，从运行中构建记录中移除
        
        # 从running_builds中移除已完成的构建
        if running_builds and running_builds_lock:
            try:
                with running_builds_lock:
                    if pipeline_iid in running_builds:
                        del running_builds[pipeline_iid]
                        app_logger.info(f"已移除完成构建: {pipeline_iid}")
            except Exception as e:
                app_logger.error(f"移除完成构建失败: {str(e)}")
        
        # 对于canceled状态，不需要生成消息
        if 'canceled' == status:
            return None
        
        duration = payload['object_attributes']['duration']
        if duration:
            duration = format_duration(duration)
        else:
            end_time = convert_utc_to_utc8(payload['object_attributes']['finished_at'])
            duration = calculate_interval(start_time, end_time)

        # 基础元素列表
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
                'icon': 'burnlife-notime_outlined',
                'content': f"***持续时间***：{duration}",
            },
            {
                'icon': 'mindnote_outlined',
                'content': f"***分      支***：{branch}",
            },
        ]

        # 如果pipeline失败，尝试从push_records中查找deploy_ip并替换持续时间项
        if status == 'failed' and commit_url:
            try:
                deploy_ip = ''
                
                # 从push_records中查找对应的deploy_ip，优化查找效率
                if push_records and push_records_lock:
                    app_logger.info(f"Searching deploy_ip from push_records for commit_url: {commit_url}")
                    with push_records_lock:
                        app_logger.info(f"Current push_records count: {len(push_records)}")
                        found_deploy_ip = False
                        
                        # 遍历push_records，找到匹配的commit_url
                        for push_record in push_records:
                            commits = push_record.get('commits', [])
                            if not isinstance(commits, list):
                                continue
                            
                            # 查找匹配commit_url的commit
                            matching_commit = next((c for c in commits if c.get('url') == commit_url), None)
                            if matching_commit:
                                app_logger.info(f"Found matching commit in push_records")
                                
                                # 从commit的stages中查找包含deploy_ip的stage
                                stages = matching_commit.get('stages', [])
                                deploy_stage = next((s for s in stages if isinstance(s, dict) and s.get('deploy_ip')), None)
                                if deploy_stage:
                                    deploy_ip = deploy_stage.get('deploy_ip', '')
                                    if deploy_ip:
                                        app_logger.info(f"Found deploy_ip from push_records: {deploy_ip}")
                                        found_deploy_ip = True
                                        break
                            if found_deploy_ip:
                                break
                
                # 如果push_records中没有找到，尝试从payload中查找
                if not deploy_ip:
                    app_logger.info(f"No deploy_ip found in push_records, trying payload")
                    # 从payload的builds中查找deploy_ip
                    builds = payload.get('builds', [])
                    for build in builds:
                        if isinstance(build, dict) and build.get('stage', '').lower() == 'deploy':
                            deploy_ip = build.get('deploy_ip', '')
                            if deploy_ip:
                                app_logger.info(f"Found deploy_ip from payload: {deploy_ip}")
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
                                    app_logger.info(f"Found deploy_ip from variables: {deploy_ip}")
                                    break
                
                # 如果找到deploy_ip，替换元素列表中的持续时间项
                if deploy_ip:
                    # 查找持续时间项的索引
                    for i, element in enumerate(elements):
                        if element['icon'] == 'burnlife-notime_outlined':
                            # 替换持续时间项为部署IP
                            elements[i] = {
                                'icon': 'location_outlined',
                                'content': f"***部署机器***：{deploy_ip}",
                            }
                            app_logger.info(f"Replaced duration with deploy_ip: {deploy_ip}")
                            break
            except Exception as e:
                app_logger.error(f"Failed to replace duration with deploy_ip: {str(e)}")
                import traceback
                app_logger.error(traceback.format_exc())

        message_config = {
            'elements': elements,
            'header': {
                'template': f"{'green' if status == 'success' else 'red'}",
                'icon_token': f"{'succeed_filled' if status == 'success' else 'error_filled'}"
            },
        }
    else:
        return None

    # 创建基础的text_tag_list
    text_tag_list = [
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

    # 如果状态是failed，替换text_tag_list最后一项为失败的builds信息
    if 'failed' == status:
        failed_stages = []
        
        # 1. 优先从push_records中获取failed_stages
        if push_records and push_records_lock and commit_url:
            app_logger.info(f"Getting failed_stages from push_records for commit_url: {commit_url}")
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
                    app_logger.info(f"Found matching commit in push_records")
                    stages = matching_commit.get('stages', [])
                    # 从stages中获取status为failed的stage
                    for stage in stages:
                        if isinstance(stage, dict):
                            stage_status = stage.get('status', '')
                            if stage_status == 'failed':
                                stage_name = stage.get('name', stage.get('stage', 'Unknown'))
                                failed_stages.append(stage_name)
                    
                    app_logger.info(f"Failed stages from push_records: {failed_stages}")
        
        # 2. 如果push_records中没有找到，从payload.get('builds', [])中获取
        if not failed_stages:
            app_logger.info("No failed_stages from push_records, getting from payload")
            builds = payload.get('builds', [])
            
            for build in builds:
                if isinstance(build, dict):
                    build_status = build.get('status', '')
                    build_name = build.get('name', 'Unknown')
                    app_logger.info(f"Checking build: {build_name}, status: {build_status}")
                    if build_status == 'failed':
                        failed_stages.append(build_name)
            
            app_logger.info(f"Failed stages from payload: {failed_stages}")
        
        # 3. 如果都没有找到，使用默认值"deploy"
        if not failed_stages:
            failed_stages = ["deploy"]
            app_logger.info("Using default failed stage: deploy")
        
        # 添加失败的builds信息到text_tag_list中
        failed_stages_text = ', '.join(failed_stages)
        
        # 移除最后一项
        if text_tag_list:
            removed_item = text_tag_list.pop()
        
        # 添加失败的stage信息作为新的最后一项
        text_tag_list.append({
            "tag": "text_tag",
            "text": {
                "tag": "plain_text",
                "content": f"failed job：{failed_stages_text}"
            },
            "color": "red"
        })

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
                    "content": f"Pipeline版本号：{pipeline_iid_prev if pipeline_iid_prev else pipeline_iid}"
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
    return message
