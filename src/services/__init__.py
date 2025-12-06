import json
import os
import threading

# 导入功能模块
from src.services.message import send_formatted_message, format_message
from src.services.record import record_pipeline_event, record_push_event
from src.services.monitor import parse_alertmanager_request, send_monitor_message
from src.services.build_monitor import start_build_monitor_thread

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
    
    # 记录相关
    'record_pipeline_event',
    'record_push_event',
    
    # 监控相关
    'parse_alertmanager_request',
    'send_monitor_message',
    
    # 构建监控
    'start_build_monitor',
    
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

# 初始化时加载project.json数据到pipeline_records
try:
    # 延迟导入app_logger，避免循环导入
    from logger import app_logger
    
    if os.path.exists('project.json'):
        with open('project.json', 'r', encoding='utf-8') as f:
            project_data = json.load(f)
    
        with pipeline_records_lock:
            pipeline_records.update(project_data)
    
    app_logger.info(f"已从project.json加载 {len(project_data)} 条流水线记录")
except Exception as e:
    # 延迟导入app_logger，避免循环导入
    from logger import app_logger
    app_logger.error(f"加载project.json时发生错误: {str(e)}")

# 初始化时加载push_records.json数据到push_records
try:
    # 延迟导入app_logger，避免循环导入
    from logger import app_logger
    
    if os.path.exists('push_records.json'):
        with open('push_records.json', 'r', encoding='utf-8') as f:
            loaded_records = json.load(f)
        
        with push_records_lock:
            # 清空现有列表并添加加载的记录
            push_records.clear()
            push_records.extend(loaded_records)
    
    app_logger.info(f"已从push_records.json加载 {len(push_records)} 条push事件记录")
except Exception as e:
    # 延迟导入app_logger，避免循环导入
    from logger import app_logger
    app_logger.error(f"加载push_records.json时发生错误: {str(e)}")


# 包装 start_build_monitor 函数，传递全局变量
def start_build_monitor():
    """启动构建监控线程"""
    start_build_monitor_thread(running_builds, running_builds_lock)
