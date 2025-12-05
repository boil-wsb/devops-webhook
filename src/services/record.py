import json
import os
import re
from datetime import datetime


def record_push_event(push_record, push_records, push_records_lock):
    """
    记录push事件到全局变量和文件中
    Args:
        push_record: push事件记录
        push_records: 全局push事件列表
        push_records_lock: 锁对象，确保线程安全
    """
    try:
        with push_records_lock:
            # 添加到全局列表
            push_records.append(push_record)
            
            # 安全保存到文件：先读取现有内容，再合并写入
            file_path = 'push_records.json'
            existing_records = []
            
            # 尝试读取现有文件内容
            if os.path.exists(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        existing_records = json.load(f)
                except (json.JSONDecodeError, IOError) as e:
                    print(f"读取现有push记录文件失败，将创建新文件: {str(e)}")
                    existing_records = []
            
            # 确保existing_records是列表
            if not isinstance(existing_records, list):
                existing_records = []
            
            # 合并记录：使用全局列表，确保数据一致性
            # 注意：这里使用push_records而不是existing_records，因为push_records包含了最新的所有记录
            # 这样可以确保文件内容与内存中的数据完全一致
            all_records = push_records
            
            # 写入文件
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(all_records, f, ensure_ascii=False, indent=2)
            
        print(f"已记录push事件: {push_record['project_name']} {push_record['ref']}")
    except Exception as e:
        print(f"记录push事件时发生错误: {str(e)}")


def record_pipeline_event(payload, subpath, pipeline_records, pipeline_records_lock):
    """
    记录流水线事件到全局变量中
    Args:
        payload: webhook 请求的 payload 数据
        subpath: 路由的子路径
        pipeline_records: 全局流水线记录字典
        pipeline_records_lock: 锁对象，确保线程安全
    """
    try:
        # 提取所需信息
        namespace = payload.get('project', {}).get('namespace', '')
        project_name = payload.get('project', {}).get('name', '')
        path_with_namespace = payload.get('project', {}).get('path_with_namespace', '')
        git_url = payload.get('project', {}).get('web_url', '')
        pipeline_iid = payload.get('object_attributes', {}).get('iid', '')
        latest_triggered_by = payload.get('user', {}).get('name', 'unknown')
        record_time = payload.get('object_attributes', {}).get('created_at', datetime.now().isoformat())
        
        # 如果缺少必要信息，直接返回
        if not namespace or not project_name:
            return
        
        # 组装流水线路径: 保留 detail_url 中 /-/ 后的内容
        detail_url = payload.get('object_attributes', {}).get('url', '')
        if detail_url:
            # 如果URL包含 /-/pipelines/，则将数字替换为 news
            if '/-/pipelines/' in detail_url:
                # 使用正则表达式替换 /pipelines/后面的数字为 /pipelines/news
                pipeline_path = re.sub(r'/-/pipelines/\d+', '/-/pipelines/new', detail_url)
            elif '/-/' in detail_url:
                # 保留其他包含 /-/ 的URL
                pipeline_path = detail_url
            else:
                # 对于不包含 /-/ 的URL，添加 /-/pipelines/news
                pipeline_path = detail_url.rstrip('/') + '/-/pipelines/new'
        else:
            pipeline_path = f"/pipelines/new"
        
        # 创建记录
        record = {
            'namespace': namespace,
            'project_name': project_name,
            'pipeline_path': pipeline_path,
            'path_with_namespace': path_with_namespace,
            'pipeline_iid': pipeline_iid,
            'git_url': git_url,
            'subpath': subpath,
            'latest_triggered_by': latest_triggered_by,
            'record_time': record_time
        }
        
        # 使用锁确保线程安全
        with pipeline_records_lock:
            # 使用 namespace 和 project_name 作为键
            key = f"{path_with_namespace}"
            pipeline_records[key] = record
            
            # 将记录同步落地到project.json文件
            try:
                # 读取现有数据
                project_data = {}
                if os.path.exists('project.json'):
                    with open('project.json', 'r', encoding='utf-8') as f:
                        project_data = json.load(f)
                
                # 更新数据：保留原有记录中不在当前record中的字段
                existing_record = project_data.get(key, {})
                # 合并记录，新record中的字段会覆盖原有字段，但原有字段不在新record中时会保留
                merged_record = {**existing_record, **record}
                project_data[key] = merged_record
                
                # 写入文件
                with open('project.json', 'w', encoding='utf-8') as f:
                    json.dump(project_data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"写入project.json文件时发生错误: {str(e)}")
            
        
    except Exception as e:
        print(f"记录流水线事件时发生错误: {str(e)}")
