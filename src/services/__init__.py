import json
import os
import threading

# 导入功能模块
from src.services.message import send_formatted_message, format_message, format_error_log_message
from src.services.record import record_pipeline_event, record_push_event
from src.services.monitor import parse_alertmanager_request, send_monitor_message
from src.services.build_monitor import start_build_monitor_thread
from src.services.gitlab_logger import get_job_logs, get_failed_job_id, get_project_id_by_name
from src.services.log_parser import parse_error_from_logs
from src.services.log_storage import save_build_logs, get_build_logs, get_log_file_path_for_download
from src.services.trigger_action import check_and_trigger

# 导入数据库模块
from src.services.database import init_database, cleanup_old_records, start_cleanup_thread

# 导入日志处理模块（延迟导入，避免循环导入）
from logger import webhook_logger, monitor_logger

# 导出所有公共API
__all__ = [
    # 日志记录器
    'webhook_logger',
    'monitor_logger',

    # 消息相关
    'send_formatted_message',
    'format_message',
    'format_error_log_message',

    # 记录相关
    'record_pipeline_event',
    'record_push_event',

    # 监控相关
    'parse_alertmanager_request',
    'send_monitor_message',

    # 构建监控
    'start_build_monitor',

    # GitLab 日志
    'get_job_logs',
    'get_failed_job_id',
    'get_project_id_by_name',

    # 日志解析
    'parse_error_from_logs',

    # 日志存储
    'save_build_logs',
    'get_build_logs',
    'get_log_file_path_for_download',

    # 触发动作
    'check_and_trigger',

    # 数据库
    'init_database',
    'cleanup_old_records',
    'start_cleanup_thread',

    # 全局变量
    'running_builds',
    'pipeline_records',
    'push_records',

    # 锁对象
    'running_builds_lock',
    'pipeline_records_lock',
    'push_records_lock'
]

# 全局变量：记录运行中的构建
running_builds = {}
# 全局变量：记录流水线事件
pipeline_records = {}
# 全局变量：记录push事件
push_records = []
# 锁，确保线程安全
running_builds_lock = threading.Lock()
pipeline_records_lock = threading.Lock()
push_records_lock = threading.Lock()


def _load_records_from_files():
    """从 JSON 文件加载数据"""
    from logger import app_logger

    if os.path.exists('project.json'):
        try:
            with open('project.json', 'r', encoding='utf-8') as f:
                project_data = json.load(f)
            with pipeline_records_lock:
                pipeline_records.update(project_data)
            app_logger.info(f"已从project.json加载 {len(project_data)} 条流水线记录")
        except Exception as e:
            app_logger.error(f"加载project.json时发生错误: {str(e)}")

    if os.path.exists('push_records.json'):
        try:
            with open('push_records.json', 'r', encoding='utf-8') as f:
                loaded_records = json.load(f)
            with push_records_lock:
                push_records.clear()
                push_records.extend(loaded_records)
            app_logger.info(f"已从push_records.json加载 {len(push_records)} 条push事件记录")
        except Exception as e:
            app_logger.error(f"加载push_records.json时发生错误: {str(e)}")


def _load_records_from_db():
    """从 SQLite 数据库加载数据"""
    from logger import app_logger

    try:
        from src.services.database import (
            PushRecordDB, PipelineRecordDB, get_db_config
        )

        config = get_db_config()
        db_path = config.get('db_path', 'webhook.db')

        if not os.path.exists(db_path):
            app_logger.info("数据库文件不存在，将使用 JSON 文件")
            return False

        init_database()

        db_push_records = PushRecordDB.get_all()
        if db_push_records:
            with push_records_lock:
                for record in db_push_records:
                    if record.get('commits') and isinstance(record['commits'], str):
                        record['commits'] = json.loads(record['commits'])
                push_records.extend(db_push_records)
            app_logger.info(f"已从数据库加载 {len(db_push_records)} 条 push 记录")

        db_pipeline_records = PipelineRecordDB.get_all()
        if db_pipeline_records:
            with pipeline_records_lock:
                for record in db_pipeline_records:
                    key = record.get('path_with_namespace')
                    if key:
                        pipeline_records[key] = record
            app_logger.info(f"已从数据库加载 {len(db_pipeline_records)} 条 pipeline 记录")

        return len(db_push_records) > 0 or len(db_pipeline_records) > 0
    except Exception as e:
        app_logger.error(f"从数据库加载记录失败: {str(e)}")
        return False


def _initialize_data():
    """初始化数据加载"""
    from logger import app_logger

    db_loaded = _load_records_from_db()

    if not db_loaded:
        _load_records_from_files()

    try:
        init_database()
        cleanup_old_records()
        start_cleanup_thread()
    except Exception as e:
        app_logger.error(f"启动数据库清理任务失败: {str(e)}")


_initialize_data()


# 包装 start_build_monitor 函数，传递全局变量
def start_build_monitor():
    """启动构建监控线程"""
    start_build_monitor_thread(running_builds, running_builds_lock)
