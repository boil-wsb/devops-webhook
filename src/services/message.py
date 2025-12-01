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


def format_message(payload, running_builds=None, running_builds_lock=None, route_name=None):
    """
    格式化消息
    """
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
                print(f"❌ 记录运行中构建失败: {str(e)}")
                import traceback
                traceback.print_exc()
        else:
            print(f"❌ running_builds或running_builds_lock为None，无法记录运行中构建")
    elif 'success' == status or 'failed' == status or 'canceled' == status:
        # 构建完成或取消，从运行中构建记录中移除
        
        # 从running_builds中移除已完成的构建
        if running_builds and running_builds_lock:
            try:
                with running_builds_lock:
                    if pipeline_iid in running_builds:
                        del running_builds[pipeline_iid]
                        print(f"已移除完成构建: {pipeline_iid}")
            except Exception as e:
                print(f"移除完成构建失败: {str(e)}")
        
        # 对于canceled状态，不需要生成消息
        if 'canceled' == status:
            return None
        
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

    # 如果状态是failed，添加失败的builds信息到text_tag_list
    if 'failed' == status:
        failed_stages = []
        # 遍历builds数组，找出状态为failed的作业
        for build in payload.get('builds', []):
            if build.get('status') == 'failed':
                failed_stages.append(build.get('name', 'Unknown'))
        
        # 如果有失败的builds，添加到text_tag_list中
        if failed_stages:
            text_tag_list.append({
                "tag": "text_tag",
                "text": {
                    "tag": "plain_text",
                    "content": f"str({', '.join(failed_stages)})"
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
