import time
from datetime import datetime
import threading
from src.config import WEBHOOK_CONFIG, DEFAULT_TARGET_URL, SKIP_TIMEOUT_CHECK, TIMEOUT_SECONDS
from src.services.message import send_formatted_message




def send_long_build_alert(build_info, route_name):
    """
    发送构建超时告警
    Args:
        build_info: 构建信息字典
        route_name: webhook路由名称
    """
    import logging
    # 使用标准的logging模块，避免导入问题
    app_logger = logging.getLogger('app_logger')
    try:
        duration_minutes = int((datetime.now() - build_info['start_time']).total_seconds() / 60)
        
        long_build_message = {
            "msg_type": "interactive",
            "card": {
                "config": {
                    "update_multi": True
                },
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"⚠️ 构建超时告警 - {build_info['project_name']}"
                    },
                    "subtitle": {
                        "tag": "plain_text",
                        "content": f"构建已运行 {duration_minutes} 分钟，仍未完成"
                    },
                    "template": "yellow"
                },
                "i18n_elements": {
                    "zh_cn": [
                        {
                            "tag": "markdown",
                            "content": f"**项目**：{build_info['project_name']}\n"
                                        f"**分支**：{build_info['branch']}\n"
                                        f"**提交人员**：{build_info['user_name']}\n"
                                        f"**开始时间**：{build_info['start_time_str']}\n"
                                        f"**Pipeline IID**：{build_info['pipeline_iid']}\n"
                                        f"**状态**：运行中（超过5分钟）\n"
                                        f"**建议**：检查构建过程是否卡死或存在性能问题",
                            "text_align": "left",
                            "text_size": "normal"
                        }
                    ]
                }
            }
        }
        
        # 发送告警，使用传入的route_name获取对应的target_url
        target_url = WEBHOOK_CONFIG.get(route_name, DEFAULT_TARGET_URL)
        if target_url:
            send_formatted_message(target_url, long_build_message)
            app_logger.info(f"已发送构建超时告警: {build_info['pipeline_iid']}，使用路由: {route_name}")
        
    except Exception as e:
            app_logger.error(f"发送构建超时告警失败: {str(e)}")


def check_long_running_builds(running_builds, running_builds_lock):
    """
    后台线程函数，定期检查运行中的构建是否超时
    每30秒检查一次，超过5分钟（300秒）没有结果的构建发送告警
    Args:
        running_builds: 全局运行中构建字典
        running_builds_lock: 锁对象，确保线程安全
    """
    import logging
    # 使用标准的logging模块，避免导入问题
    app_logger = logging.getLogger('app_logger')
    while True:
        try:
            current_time = datetime.now()
            builds_to_remove = []
            
            with running_builds_lock:
                build_count = len(running_builds)
                # 只有当运行中构建数量大于0时，才打印日志
                if build_count > 0:
                    # 打印当前running_builds的内容，用于调试
                    app_logger.debug(f"📊 当前运行中构建数量: {build_count}")
                    for pipeline_iid, build_info in running_builds.items():
                        app_logger.debug(f"🔍 检查构建: {pipeline_iid}, 开始时间: {build_info['start_time']}")
                        elapsed_time = (current_time - build_info['start_time']).total_seconds()
                        app_logger.debug(f"⏱️  已运行时间: {elapsed_time} 秒")
                        
                        # 检查项目是否在跳过超时检查列表中，或者commit_url是否包含跳过关键词
                        commit_url = build_info.get('commit_url', '')
                        should_skip = False
                        
                        if build_info['project_name'] in SKIP_TIMEOUT_CHECK:
                            should_skip = True
                            app_logger.debug(f"⏱️  项目 {build_info['project_name']} 在跳过超时检查列表中，跳过本次检查")
                        elif any(skip_keyword in commit_url for skip_keyword in SKIP_TIMEOUT_CHECK):
                            should_skip = True
                            for skip_keyword in SKIP_TIMEOUT_CHECK:
                                if skip_keyword in commit_url:
                                    app_logger.debug(f"⏱️  commit_url 包含 '{skip_keyword}' 在跳过超时检查列表中，跳过本次检查")
                                    break
                        
                        if not should_skip:
                            # 获取项目的自定义超时时间，如果没有配置则使用默认值300秒
                            timeout_seconds = TIMEOUT_SECONDS.get(build_info['project_name'], 300)
                            
                            if elapsed_time > timeout_seconds:
                                # 发送超时告警
                                app_logger.warning(f"🚨 构建超时，发送告警: {pipeline_iid}")
                                # 从构建信息中获取route_name
                                build_route_name = build_info.get('route_name', '')
                                send_long_build_alert(build_info, build_route_name)
                                # 标记为需要移除
                                builds_to_remove.append(pipeline_iid)
                            else:
                                app_logger.debug(f"⏱️  构建 {pipeline_iid} 已运行 {elapsed_time} 秒，未超过配置的超时时间 {timeout_seconds} 秒")
            
            # 移除已经处理超时告警的构建
            with running_builds_lock:
                for pipeline_iid in builds_to_remove:
                    if pipeline_iid in running_builds:
                        del running_builds[pipeline_iid]
                        app_logger.info(f"已移除超时构建记录: {pipeline_iid}")
            
            # 每30秒检查一次
            time.sleep(60)
            
        except Exception as e:
            app_logger.error(f"检查运行中构建时发生错误: {str(e)}")
            time.sleep(30)


def start_build_monitor_thread(running_builds, running_builds_lock):
    """启动构建监控线程
    Args:
        running_builds: 全局运行中构建字典
        running_builds_lock: 锁对象，确保线程安全
    """
    import logging
    # 使用标准的logging模块，避免导入问题
    app_logger = logging.getLogger('app_logger')
    monitor_thread = threading.Thread(target=check_long_running_builds, args=(running_builds, running_builds_lock), daemon=True)
    monitor_thread.start()
    app_logger.info("构建监控线程已启动")
