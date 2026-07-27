from pathlib import Path
import json


def make_build_key(project_id, pipeline_iid):
    """构造 running_builds 字典 key，加入项目维度避免跨项目 pipeline_iid 冲突。

    背景: 不同 GitLab 项目可能有相同的 pipeline_iid（每个项目内部从 1 开始），
    若仅用 pipeline_iid 作为 key，项目B 会误读项目A 的 build_info，
    导致走 PATCH 更新分支覆盖项目A 的卡片。

    Args:
        project_id: GitLab 项目 ID（payload['project']['id']，全局唯一）
        pipeline_iid: 项目内 pipeline 序号

    Returns:
        str: 形如 "42_5010" 的隔离 key
    """
    return f"{project_id}_{pipeline_iid}"


def make_callback_id(project_id, pipeline_iid):
    """构造 callback_id 业务标识，加入项目维度避免跨项目 pipeline_iid 冲突。

    用于 ops-manager 侧 PATCH /notify-by-open-id/{open_message_id} 时按 callback_id
    过滤更新目标（项目维度隔离）。

    Args:
        project_id: GitLab 项目 ID
        pipeline_iid: 项目内 pipeline 序号

    Returns:
        str: 形如 "pipeline_42_5010" 的业务标识
    """
    return f"pipeline_{project_id}_{pipeline_iid}"


def find_similar_pipeline_records(project_name, branch, current_pipeline_iid, build_type):
    """
    从webhook_backup.log中查找相同project_name和branch但不同pipeline_iid的记录
    Args:
        project_name: 项目名称
        branch: 分支名称
        current_pipeline_iid: 当前pipeline的iid，用于排除
        build_type: 构建类型，用于排除相同类型的构建
    Returns:
        list: 找到的相关记录列表
    """
    # 修正日志文件路径
    log_file = Path("logs/monitor_event.log")

    if not log_file.exists():
        return []

    similar_records = []

    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # 从后往前读取，获取最新记录
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue

            try:
                # 分割日志行
                parts = line.split(' - INFO - ', 1)
                # 使用ast.literal_eval处理单引号JSON
                import ast
                log_data = ast.literal_eval(parts[1].strip())

                # 获取body内容
                body_str = log_data.get('body', '')
                # 解析body中的JSON
                body_data = json.loads(body_str)
                # 提取所需信息
                obj_attrs = body_data.get('object_attributes', {})
                project_data = body_data.get('project', {})

                # 检查是否匹配project_name和branch
                found_project = str(project_data.get('name', '')) == str(project_name)
                found_branch = str(obj_attrs.get('ref', '')) == str(branch)
                found_build_type = str(obj_attrs.get('source', '')) != str(build_type)

                if found_project and found_branch and found_build_type:
                    pipeline_iid = obj_attrs.get('iid')

                    # 排除当前pipeline_iid
                    if str(pipeline_iid) != str(current_pipeline_iid):
                        record = {
                            'project_name': str(project_name),
                            'branch': str(branch),
                            'pipeline_iid': str(pipeline_iid),
                            'timestamp': str(log_data.get('timestamp', '')),
                            'status': str(obj_attrs.get('status', 'unknown'))
                        }
                        similar_records.append(record)
                        # 找到第一个匹配的记录即可停止
                        break

            except (json.JSONDecodeError, KeyError, ValueError, SyntaxError) as e:
                continue

    except Exception as e:
        # 文件读取错误，静默处理
        pass

    return similar_records
