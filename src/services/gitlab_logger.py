import requests
import logging
from typing import Optional, Tuple

app_logger = logging.getLogger('app_logger')


def get_gitlab_config():
    """
    获取 GitLab 配置
    Returns:
        tuple: (gitlab_url, private_token)
    """
    from src.config import load_config
    config_path = 'config.conf'
    import os
    config_full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', config_path)

    default_gitlab_config = {
        'gitlab_url': '',
        'private_token': ''
    }

    try:
        if os.path.exists(config_full_path):
            import json
            with open(config_full_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                gitlab_config = config.get('gitlab_config', default_gitlab_config)
                return gitlab_config.get('gitlab_url', ''), gitlab_config.get('private_token', '')
    except Exception as e:
        app_logger.error(f"读取 GitLab 配置失败: {str(e)}")

    return default_gitlab_config['gitlab_url'], default_gitlab_config['private_token']


def get_job_logs(project_id: int, job_id: int, max_lines: int = 100) -> Tuple[bool, str]:
    """
    通过 GitLab API 获取 job 日志

    Args:
        project_id: GitLab 项目 ID
        job_id: Job ID
        max_lines: 最大获取行数

    Returns:
        tuple: (success, log_content 或 error_message)
    """
    gitlab_url, private_token = get_gitlab_config()

    if not gitlab_url or not private_token:
        app_logger.warning("GitLab 配置不完整，无法获取日志")
        return False, "GitLab 配置不完整"

    url = f"{gitlab_url.rstrip('/')}/api/v4/projects/{project_id}/jobs/{job_id}/trace"

    headers = {
        'PRIVATE-TOKEN': private_token
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        full_log = response.text

        lines = full_log.split('\n')
        if len(lines) > max_lines:
            truncated_log = '\n'.join(lines[-max_lines:])
            app_logger.info(f"日志已截取至最后 {max_lines} 行")
            return True, truncated_log

        return True, full_log

    except requests.exceptions.HTTPError as e:
        app_logger.error(f"GitLab API HTTP 错误: {str(e)}")
        return False, f"HTTP 错误: {str(e)}"
    except requests.exceptions.Timeout:
        app_logger.error("GitLab API 请求超时")
        return False, "请求超时"
    except Exception as e:
        app_logger.error(f"获取 Job 日志失败: {str(e)}")
        return False, str(e)


def get_failed_job_id(pipeline_id: int, project_id: int) -> Tuple[bool, int, str]:
    """
    获取 pipeline 中失败的 job ID

    Args:
        pipeline_id: Pipeline ID
        project_id: GitLab 项目 ID

    Returns:
        tuple: (success, job_id 或 0, error_message)
    """
    gitlab_url, private_token = get_gitlab_config()

    if not gitlab_url or not private_token:
        app_logger.warning("GitLab 配置不完整，无法获取 Job ID")
        return False, 0, "GitLab 配置不完整"

    url = f"{gitlab_url.rstrip('/')}/api/v4/projects/{project_id}/pipelines/{pipeline_id}/jobs"

    headers = {
        'PRIVATE-TOKEN': private_token
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        jobs = response.json()

        for job in jobs:
            if job.get('status') == 'failed':
                app_logger.info(f"找到失败的 Job: {job.get('id')}, 名称: {job.get('name')}")
                return True, job.get('id'), ""

        app_logger.warning(f"Pipeline {pipeline_id} 中没有失败的 Job")
        return False, 0, "没有找到失败的 Job"

    except requests.exceptions.HTTPError as e:
        app_logger.error(f"GitLab API HTTP 错误: {str(e)}")
        return False, 0, f"HTTP 错误: {str(e)}"
    except requests.exceptions.Timeout:
        app_logger.error("GitLab API 请求超时")
        return False, 0, "请求超时"
    except Exception as e:
        app_logger.error(f"获取 Pipeline Jobs 失败: {str(e)}")
        return False, 0, str(e)


def get_project_id_by_name(project_path_with_namespace: str) -> Tuple[bool, int, str]:
    """
    通过项目路径获取项目 ID

    Args:
        project_path_with_namespace: 项目完整路径 (如 "group/project")

    Returns:
        tuple: (success, project_id 或 0, error_message)
    """
    gitlab_url, private_token = get_gitlab_config()

    if not gitlab_url or not private_token:
        app_logger.warning("GitLab 配置不完整，无法获取项目 ID")
        return False, 0, "GitLab 配置不完整"

    encoded_path = requests.utils.quote(project_path_with_namespace, safe='')
    url = f"{gitlab_url.rstrip('/')}/api/v4/projects/{encoded_path}"

    headers = {
        'PRIVATE-TOKEN': private_token
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        project_data = response.json()
        project_id = project_data.get('id')

        if project_id:
            app_logger.info(f"找到项目 {project_path_with_namespace}, ID: {project_id}")
            return True, project_id, ""

        return False, 0, "项目 ID 未找到"

    except requests.exceptions.HTTPError as e:
        app_logger.error(f"GitLab API HTTP 错误: {str(e)}")
        return False, 0, f"HTTP 错误: {str(e)}"
    except requests.exceptions.Timeout:
        app_logger.error("GitLab API 请求超时")
        return False, 0, "请求超时"
    except Exception as e:
        app_logger.error(f"获取项目 ID 失败: {str(e)}")
        return False, 0, str(e)
