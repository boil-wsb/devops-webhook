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
    import logging
    # 使用标准的logging模块，避免导入问题
    app_logger = logging.getLogger('app_logger')
    
    try:
        # 处理push_record
        processed_record = push_record.copy()
        
        # 1. 按timestamp从晚到早排序commits记录
        if isinstance(processed_record.get('commits'), list):
            # 按timestamp从晚到早排序
            processed_record['commits'].sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            
            # 2. 使用commits的最晚一项记录作为push_records的push时间
            if processed_record['commits']:
                # 获取最晚的commit时间
                latest_commit = processed_record['commits'][0]
                processed_record['push_time'] = latest_commit.get('timestamp', '')
            else:
                # 如果没有commits，使用当前时间
                processed_record['push_time'] = datetime.now().isoformat()
        else:
            # 如果commits不是列表，使用当前时间
            processed_record['push_time'] = datetime.now().isoformat()
        
        # 3. 处理每个commit的pipeline_iid和source字段
        # 遍历当前push_record中的每个commit
        for i, commit in enumerate(processed_record['commits']):
            commit_url = commit.get('url', '')
            if commit_url:
                # 获取当前记录的source
                current_source = processed_record.get('source', '')
                
                # 收集所有具有相同commit_url的source
                sources = [current_source] if current_source else []
                
                # 遍历所有现有的push记录，寻找相同的commit url
                for existing_push_record in push_records:
                    if isinstance(existing_push_record.get('commits'), list):
                        for existing_commit in existing_push_record['commits']:
                            if existing_commit.get('url') == commit_url:
                                # 保留现有的pipeline_iid字段
                                if 'pipeline_iid' in existing_commit:
                                    processed_record['commits'][i]['pipeline_iid'] = existing_commit['pipeline_iid']
                                
                                # 收集现有记录的source
                                existing_source = existing_push_record.get('source', '')
                                if existing_source and existing_source not in sources:
                                    sources.append(existing_source)
                                break
                
                # 将source列表添加到当前commit中
                if sources:
                    processed_record['commits'][i]['source'] = sources
    
        with push_records_lock:
            # 添加到全局列表
            push_records.append(processed_record)
            
            # 限制内存中最多存储5000条记录
            max_records = 5000
            if len(push_records) > max_records:
                # 保留最新的1000条记录，删除最旧的记录
                # 先按push_time从晚到早排序，然后截断列表
                push_records.sort(key=lambda x: x.get('push_time', ''), reverse=True)
                del push_records[max_records:]
            
            # 写入文件
            file_path = 'push_records.json'
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(push_records, f, ensure_ascii=False, indent=2)
            
        # 使用print语句代替app_logger，避免导入问题
        print(f"已记录push事件: {push_record['project_name']} {push_record['ref']}")
    except Exception as e:
        # 使用标准的logging模块记录错误
        app_logger.error(f"记录push事件时发生错误: {str(e)}")


def record_pipeline_event(payload, subpath, pipeline_records, pipeline_records_lock, push_records, push_records_lock):
    """
    记录流水线事件到全局变量中
    Args:
        payload: webhook 请求的 payload 数据
        subpath: 路由的子路径
        pipeline_records: 全局流水线记录字典
        pipeline_records_lock: 锁对象，确保线程安全
        push_records: 全局push事件列表
        push_records_lock: 锁对象，确保线程安全
    """
    import logging
    # 使用标准的logging模块，避免导入问题
    app_logger = logging.getLogger('app_logger')
    
    try:
        # 提取所需信息
        namespace = payload.get('project', {}).get('namespace', '')
        project_name = payload.get('project', {}).get('name', '')
        path_with_namespace = payload.get('project', {}).get('path_with_namespace', '')
        git_url = payload.get('project', {}).get('web_url', '')
        pipeline_iid = payload.get('object_attributes', {}).get('iid', '')
        latest_triggered_by = payload.get('user', {}).get('name', 'unknown')
        record_time = payload.get('object_attributes', {}).get('created_at', datetime.now().isoformat())
        branch = payload.get('object_attributes', {}).get('ref', '')
        commit_url = payload.get('commit', {}).get('url', '')
        pipeline_status = payload.get('object_attributes', {}).get('status', '')
        
        # 提取variables中的IP变量，使用正则表达式匹配IP相关变量
        variables = payload.get('object_attributes', {}).get('variables', [])
        deploy_ip = ''
        
        # IP地址正则表达式
        ip_pattern = re.compile(r'^((25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(25[0-5]|2[0-4]\d|[01]?\d\d?)$')
        
        # IP相关变量名正则表达式
        ip_var_pattern = re.compile(r'(IP|ip|HOST|host|REMOTE|remote)', re.IGNORECASE)
        
        for var in variables:
            if isinstance(var, dict):
                key = var.get('key', '')
                value = var.get('value', '')
                
                # 首先检查变量名是否与IP相关
                if ip_var_pattern.search(key) and value:
                    # 然后检查值是否为有效的IP地址
                    if ip_pattern.match(value):
                        deploy_ip = value
                        break
                    # 如果不是有效的IP地址，但变量名包含DEPLOY_REMOTE_HOST，也作为备选
                    elif key == 'DEPLOY_REMOTE_HOST':
                        deploy_ip = value
                        break
        
        # 提取builds信息，包括stage、name、状态和deploy_ip
        builds = payload.get('builds', [])
        build_stages = []
        for build in builds:
            if isinstance(build, dict):
                stage = build.get('stage', '')
                name = build.get('name', '')
                status = build.get('status', '')
                if stage:
                    # 创建job记录
                    job_record = {
                        'stage': stage,
                        'name': name,
                        'status': status
                    }
                    
                    # 将deploy_ip添加到对应的job记录中，作为列表类型
                    # 特别是deploy阶段的job，应该包含deploy_ip
                    if deploy_ip and stage.lower() == 'deploy':
                        job_record['deploy_ip'] = [deploy_ip]  # 初始化为列表
                    
                    build_stages.append(job_record)
        
        # 如果缺少必要信息，直接返回
        if not namespace or not project_name:
            return
        
        # 注意：子pipeline（source为parent_pipeline）也需要记录，只要有commit_url
        # 这里不检查pipeline的source，只要有commit_url就处理
        
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
            'record_time': record_time,
            'branch': branch,
            'commit_url': commit_url
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
                # 使用标准的logging模块记录错误
                app_logger.error(f"写入project.json文件时发生错误: {str(e)}")
            
            # 检查push_records.json是否存在，并且存在一致的commit url记录，将pipeline_iid插入对应记录内
        try:
            push_file_path = 'push_records.json'
            if os.path.exists(push_file_path) and commit_url:
                # 使用全局的push_records列表，而不是从文件中重新读取
                # 这样可以确保全局列表与文件内容一致
                updated = False
                for push_record in push_records:
                    # 检查push_record是否包含commits字段，并且commits是列表
                    if isinstance(push_record.get('commits'), list):
                        for commit in push_record['commits']:
                            # 检查commit url是否匹配
                            if commit.get('url') == commit_url:
                                # 插入pipeline_iid
                                commit['pipeline_iid'] = pipeline_iid
                                
                                # 添加subpath信息到push_record
                                push_record['subpath'] = subpath
                                
                                # 记录build stages，根据stage和name更新status，避免重复
                                if build_stages:
                                    # 确保stages字段存在且是列表
                                    if 'stages' not in commit:
                                        commit['stages'] = []
                                    
                                    # 遍历当前build_stages，更新或添加stage记录
                                    for new_stage in build_stages:
                                        stage_name = new_stage.get('stage', '')
                                        job_name = new_stage.get('name', '')
                                        stage_status = new_stage.get('status', '')
                                        new_deploy_ip = new_stage.get('deploy_ip', [])
                                        
                                        if stage_name:
                                            # 查找是否已存在相同stage和name的记录
                                            found = False
                                            for existing_stage in commit['stages']:
                                                if existing_stage.get('stage') == stage_name and existing_stage.get('name') == job_name:
                                                    # 更新现有记录的status
                                                    existing_stage['status'] = stage_status
                                                    
                                                    # 更新deploy_ip列表，添加新的deploy_ip（如果有）
                                                    if new_deploy_ip:
                                                        # 确保现有记录的deploy_ip是列表
                                                        if 'deploy_ip' not in existing_stage:
                                                            existing_stage['deploy_ip'] = []
                                                        elif not isinstance(existing_stage['deploy_ip'], list):
                                                            # 如果是字符串，转换为列表
                                                            existing_stage['deploy_ip'] = [existing_stage['deploy_ip']]
                                                        
                                                        # 添加新的deploy_ip，确保不重复
                                                        for ip in new_deploy_ip:
                                                            if ip not in existing_stage['deploy_ip']:
                                                                existing_stage['deploy_ip'].append(ip)
                                                                updated = True
                                                    
                                                    found = True
                                                    updated = True
                                                    break
                                            
                                            if not found:
                                                # 添加新的stage记录
                                                commit['stages'].append(new_stage)
                                                updated = True
                                
                                # 记录流水线状态
                                if pipeline_status:
                                    commit['pipeline_status'] = pipeline_status
                                
                                updated = True
                
                # 如果有更新，写入文件
                if updated:
                    # 写入文件时使用锁确保线程安全
                    with push_records_lock:
                        with open(push_file_path, 'w', encoding='utf-8') as f:
                            json.dump(push_records, f, ensure_ascii=False, indent=2)
        except Exception as e:
            # 使用标准的logging模块记录错误
            app_logger.error(f"更新push_records.json文件时发生错误: {str(e)}")
            
        
    except Exception as e:
        # 使用标准的logging模块记录错误
        app_logger.error(f"记录流水线事件时发生错误: {str(e)}")
