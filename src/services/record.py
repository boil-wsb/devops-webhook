import json
import os
import re
from datetime import datetime


def record_push_event(push_record, push_records, push_records_lock):
    """
    记录push事件到全局变量和数据库
    Args:
        push_record: push事件记录
        push_records: 全局push事件列表
        push_records_lock: 锁对象，确保线程安全
    """
    import logging
    app_logger = logging.getLogger('app_logger')

    try:
        processed_record = push_record.copy()

        if isinstance(processed_record.get('commits'), list):
            processed_record['commits'].sort(key=lambda x: x.get('timestamp', ''), reverse=True)

            if processed_record['commits']:
                latest_commit = processed_record['commits'][0]
                processed_record['push_time'] = latest_commit.get('timestamp', '')
            else:
                processed_record['push_time'] = datetime.now().isoformat()
        else:
            processed_record['push_time'] = datetime.now().isoformat()

        for i, commit in enumerate(processed_record['commits']):
            commit_url = commit.get('url', '')
            if commit_url:
                current_source = processed_record.get('source', '')

                sources = [current_source] if current_source else []

                for existing_push_record in push_records:
                    if isinstance(existing_push_record.get('commits'), list):
                        for existing_commit in existing_push_record['commits']:
                            if existing_commit.get('url') == commit_url:
                                if 'pipeline_iid' in existing_commit:
                                    processed_record['commits'][i]['pipeline_iid'] = existing_commit['pipeline_iid']

                                existing_source = existing_push_record.get('source', '')
                                if existing_source and existing_source not in sources:
                                    sources.append(existing_source)
                                break

                if sources:
                    processed_record['commits'][i]['source'] = sources

        with push_records_lock:
            push_records.append(processed_record)

            max_records = 5000
            if len(push_records) > max_records:
                push_records.sort(key=lambda x: x.get('push_time', ''), reverse=True)
                del push_records[max_records:]

            try:
                from src.services.database import PushRecordDB, init_database
                init_database()
                PushRecordDB.insert(
                    project_name=processed_record.get('project_name'),
                    ref=processed_record.get('ref'),
                    user_name=processed_record.get('user_name'),
                    git_url=processed_record.get('git_url'),
                    subpath=processed_record.get('subpath'),
                    push_time=processed_record.get('push_time'),
                    commits=processed_record.get('commits')
                )
            except Exception as e:
                app_logger.error(f"record | save_push_to_db_failed | error={e}")

        app_logger.info(f"record | push_recorded | project={push_record['project_name']}, ref={push_record['ref']}")
    except Exception as e:
        app_logger.error(f"record | record_push_failed | error={e}")


def record_pipeline_event(payload, subpath, pipeline_records, pipeline_records_lock, push_records, push_records_lock):
    """
    记录流水线事件到全局变量和数据库中
    Args:
        payload: webhook 请求的 payload 数据
        subpath: 路由的子路径
        pipeline_records: 全局流水线记录字典
        pipeline_records_lock: 锁对象，确保线程安全
        push_records: 全局push事件列表
        push_records_lock: 锁对象，确保线程安全
    """
    import logging
    app_logger = logging.getLogger('app_logger')

    try:
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

        variables = payload.get('object_attributes', {}).get('variables', [])
        deploy_ip = ''

        ip_pattern = re.compile(r'^((25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(25[0-5]|2[0-4]\d|[01]?\d\d?)$')
        ip_var_pattern = re.compile(r'(IP|ip|HOST|host|REMOTE|remote)', re.IGNORECASE)

        for var in variables:
            if isinstance(var, dict):
                key = var.get('key', '')
                value = var.get('value', '')

                if ip_var_pattern.search(key) and value:
                    if ip_pattern.match(value):
                        deploy_ip = value
                        break
                    elif key == 'DEPLOY_REMOTE_HOST':
                        deploy_ip = value
                        break

        builds = payload.get('builds', [])
        build_stages = []
        for build in builds:
            if isinstance(build, dict):
                stage = build.get('stage', '')
                name = build.get('name', '')
                status = build.get('status', '')
                if stage:
                    job_record = {
                        'stage': stage,
                        'name': name,
                        'status': status
                    }

                    if deploy_ip and stage.lower() == 'deploy':
                        job_record['deploy_ip'] = [deploy_ip]

                    build_stages.append(job_record)

        if not namespace or not project_name:
            return

        detail_url = payload.get('object_attributes', {}).get('url', '')
        if detail_url:
            if '/-/pipelines/' in detail_url:
                pipeline_path = re.sub(r'/-/pipelines/\d+', '/-/pipelines/new', detail_url)
            elif '/-/' in detail_url:
                pipeline_path = detail_url
            else:
                pipeline_path = detail_url.rstrip('/') + '/-/pipelines/new'
        else:
            pipeline_path = f"/pipelines/new"

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

        with pipeline_records_lock:
            key = f"{path_with_namespace}"
            pipeline_records[key] = record

            try:
                from src.services.database import PipelineRecordDB, init_database
                init_database()
                PipelineRecordDB.upsert(
                    path_with_namespace=path_with_namespace,
                    namespace=namespace,
                    project_name=project_name,
                    pipeline_path=pipeline_path,
                    pipeline_iid=pipeline_iid,
                    git_url=git_url,
                    subpath=subpath,
                    latest_triggered_by=latest_triggered_by,
                    record_time=record_time,
                    branch=branch,
                    commit_url=commit_url
                )
            except Exception as e:
                app_logger.error(f"record | save_pipeline_to_db_failed | error={e}")

        try:
            push_file_path = 'push_records.json'
            if os.path.exists(push_file_path):
                updated = False
                for push_record in push_records:
                    record_ref = push_record.get('ref', '')
                    if record_ref.startswith('refs/heads/'):
                        processed_record_ref = record_ref.replace('refs/heads/', '')
                    elif record_ref.startswith('refs/tags/'):
                        processed_record_ref = record_ref.replace('refs/tags/', '')
                    elif record_ref.startswith('refs/remotes/'):
                        processed_record_ref = record_ref.replace('refs/remotes/', '')
                    else:
                        processed_record_ref = record_ref

                    processed_branch = branch
                    if processed_branch.startswith('refs/heads/'):
                        processed_branch = processed_branch.replace('refs/heads/', '')
                    elif processed_branch.startswith('refs/tags/'):
                        processed_branch = processed_branch.replace('refs/tags/', '')
                    elif processed_branch.startswith('refs/remotes/'):
                        processed_branch = processed_branch.replace('refs/remotes/', '')

                    if isinstance(push_record.get('commits'), list) and processed_record_ref == processed_branch:
                        for commit in push_record['commits']:
                            if commit.get('url') == commit_url:
                                commit['pipeline_iid'] = pipeline_iid

                                push_record['subpath'] = subpath

                                if build_stages:
                                    if 'stages' not in commit:
                                        commit['stages'] = []

                                    for new_stage in build_stages:
                                        stage_name = new_stage.get('stage', '')
                                        job_name = new_stage.get('name', '')
                                        stage_status = new_stage.get('status', '')
                                        new_deploy_ip = new_stage.get('deploy_ip', [])

                                        if stage_name:
                                            found = False
                                            for existing_stage in commit['stages']:
                                                if existing_stage.get('stage') == stage_name and existing_stage.get('name') == job_name:
                                                    existing_stage['status'] = stage_status

                                                    if new_deploy_ip:
                                                        if 'deploy_ip' not in existing_stage:
                                                            existing_stage['deploy_ip'] = []
                                                        elif not isinstance(existing_stage['deploy_ip'], list):
                                                            existing_stage['deploy_ip'] = [existing_stage['deploy_ip']]

                                                        for ip in new_deploy_ip:
                                                            if ip not in existing_stage['deploy_ip']:
                                                                existing_stage['deploy_ip'].append(ip)
                                                                updated = True

                                                    found = True
                                                    updated = True
                                                    break

                                            if not found:
                                                commit['stages'].append(new_stage)
                                                updated = True

                                if pipeline_status:
                                    commit['pipeline_status'] = pipeline_status

                                updated = True

                if updated:
                    with push_records_lock:
                        with open(push_file_path, 'w', encoding='utf-8') as f:
                            json.dump(push_records, f, ensure_ascii=False, indent=2)

                    try:
                        from src.services.database import PushRecordDB, init_database
                        init_database()
                        PushRecordDB.update_commit_pipeline_info(
                            git_url=git_url,
                            commit_url=commit_url,
                            pipeline_iid=pipeline_iid,
                            pipeline_status=pipeline_status,
                            deploy_ip=deploy_ip if deploy_ip else None,
                            stages=build_stages,
                            subpath=subpath
                        )
                    except Exception as e:
                        app_logger.error(f"record | update_push_pipeline_failed | error={e}")

        except Exception as e:
            app_logger.error(f"record | update_json_failed | error={e}")


    except Exception as e:
        app_logger.error(f"record | record_pipeline_failed | error={e}")


def load_records_from_db(push_records, pipeline_records, push_records_lock, pipeline_records_lock):
    """
    从数据库加载记录到内存
    Args:
        push_records: 全局push事件列表
        pipeline_records: 全局流水线记录字典
        push_records_lock: 锁对象
        pipeline_records_lock: 锁对象
    Returns:
        bool: 是否从数据库加载了数据
    """
    import logging
    app_logger = logging.getLogger('app_logger')

    try:
        from src.services.database import (
            PushRecordDB, PipelineRecordDB,
            init_database, get_db_config
        )

        config = get_db_config()
        db_path = config.get('db_path', 'webhook.db')

        if not os.path.exists(db_path):
            app_logger.info("record | db_not_found | skip_load=true")
            return False

        init_database()

        db_push_records = PushRecordDB.get_all()
        if db_push_records:
            with push_records_lock:
                for record in db_push_records:
                    if record.get('commits') and isinstance(record['commits'], str):
                        record['commits'] = json.loads(record['commits'])
                push_records.extend(db_push_records)
            app_logger.info(f"record | load_push_from_db | count={len(db_push_records)}")

        db_pipeline_records = PipelineRecordDB.get_all()
        if db_pipeline_records:
            with pipeline_records_lock:
                for record in db_pipeline_records:
                    key = record.get('path_with_namespace')
                    if key:
                        pipeline_records[key] = record
            app_logger.info(f"record | load_pipeline_from_db | count={len(db_pipeline_records)}")

        return len(db_push_records) > 0 or len(db_pipeline_records) > 0

    except Exception as e:
        app_logger.error(f"record | load_records_from_db_failed | error={e}")
        return False
