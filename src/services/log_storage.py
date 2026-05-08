import os
import logging
from typing import Dict, Optional

app_logger = logging.getLogger('app_logger')


def get_log_storage_config():
    from src.config import get_config
    config = get_config()
    default_storage_config = {
        'log_storage_path': 'logs',
        'retention_days': 30
    }
    return config.get('log_storage_config', default_storage_config)


def sanitize_filename(filename: str) -> str:
    """
    清理文件名，移除非法字符

    Args:
        filename: 原始文件名

    Returns:
        str: 清理后的文件名
    """
    invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    cleaned = filename
    for char in invalid_chars:
        cleaned = cleaned.replace(char, '_')
    return cleaned


def get_log_file_path(project_name: str, branch: str, pipeline_iid: int) -> str:
    """
    获取日志文件存储路径

    Args:
        project_name: 项目名称
        branch: 分支名称
        pipeline_iid: Pipeline IID

    Returns:
        str: 日志目录路径
    """
    storage_config = get_log_storage_config()
    base_path = storage_config.get('log_storage_path', 'logs')

    project_clean = sanitize_filename(project_name)
    branch_clean = sanitize_filename(branch)

    log_dir = os.path.join(base_path, project_clean, branch_clean, str(pipeline_iid))

    return log_dir


def save_build_logs(project_name: str, branch: str, pipeline_iid: int,
                    log_content: str, error_summary: str, failed_job_name: str = "") -> bool:
    """
    保存构建日志到本地文件

    Args:
        project_name: 项目名称
        branch: 分支名称
        pipeline_iid: Pipeline IID
        log_content: 完整日志内容
        error_summary: 错误摘要
        failed_job_name: 失败的Job名称

    Returns:
        bool: 保存是否成功
    """
    try:
        log_dir = get_log_file_path(project_name, branch, pipeline_iid)

        os.makedirs(log_dir, exist_ok=True)

        full_log_path = os.path.join(log_dir, 'full.log')
        with open(full_log_path, 'w', encoding='utf-8') as f:
            f.write(log_content)
        app_logger.info(f"完整日志已保存: {full_log_path}")

        error_summary_path = os.path.join(log_dir, 'error_summary.txt')
        with open(error_summary_path, 'w', encoding='utf-8') as f:
            f.write(error_summary)
        app_logger.info(f"错误摘要已保存: {error_summary_path}")

        if failed_job_name:
            job_info_path = os.path.join(log_dir, 'job_info.txt')
            with open(job_info_path, 'w', encoding='utf-8') as f:
                f.write(f"failed_job_name: {failed_job_name}\n")
            app_logger.info(f"失败Job信息已保存: {job_info_path}")

        return True

    except Exception as e:
        app_logger.error(f"保存构建日志失败: {str(e)}")
        import traceback
        app_logger.error(traceback.format_exc())
        return False


def get_build_logs(project_name: str, branch: str, pipeline_iid: int) -> Optional[Dict]:
    """
    获取构建日志

    Args:
        project_name: 项目名称
        branch: 分支名称
        pipeline_iid: Pipeline IID

    Returns:
        dict: {
            'full_log': str,
            'error_summary': str,
            'failed_job_name': str,
            'exists': bool
        } 或 None
    """
    try:
        log_dir = get_log_file_path(project_name, branch, pipeline_iid)

        full_log_path = os.path.join(log_dir, 'full.log')
        error_summary_path = os.path.join(log_dir, 'error_summary.txt')
        job_info_path = os.path.join(log_dir, 'job_info.txt')

        result = {
            'exists': False,
            'full_log': '',
            'error_summary': '',
            'failed_job_name': ''
        }

        if os.path.exists(full_log_path):
            with open(full_log_path, 'r', encoding='utf-8') as f:
                result['full_log'] = f.read()
            result['exists'] = True

        if os.path.exists(error_summary_path):
            with open(error_summary_path, 'r', encoding='utf-8') as f:
                result['error_summary'] = f.read()

        if os.path.exists(job_info_path):
            with open(job_info_path, 'r', encoding='utf-8') as f:
                content = f.read()
                for line in content.split('\n'):
                    if line.startswith('failed_job_name:'):
                        result['failed_job_name'] = line.split(':', 1)[1].strip()

        return result

    except Exception as e:
        app_logger.error(f"获取构建日志失败: {str(e)}")
        return None


def get_log_file_path_for_download(project_name: str, branch: str, pipeline_iid: int) -> Optional[str]:
    """
    获取日志文件路径用于下载

    Args:
        project_name: 项目名称
        branch: 分支名称
        pipeline_iid: Pipeline IID

    Returns:
        str: 日志文件路径，或 None
    """
    try:
        log_dir = get_log_file_path(project_name, branch, pipeline_iid)
        full_log_path = os.path.join(log_dir, 'full.log')

        if os.path.exists(full_log_path):
            return full_log_path

        return None

    except Exception as e:
        app_logger.error(f"获取日志文件路径失败: {str(e)}")
        return None
