import time
from datetime import datetime
import threading
from concurrent.futures import ThreadPoolExecutor
from src.config import WEBHOOK_CONFIG, DEFAULT_TARGET_URL, SKIP_TIMEOUT_CHECK, TIMEOUT_SECONDS, ROUTE_CHAT_ID_MAP
from src.services.message import send_formatted_message, send_notification




def send_long_build_alert(build_info, route_name):
    """
    发送构建超时告警，复用原卡片更新（避免多次发送新卡片）

    - 首次告警（info 级别）：发送新卡片，记录 message_id 和 callback_id
    - 后续升级告警（warning/critical）：用 message_id + callback_id 更新原卡片

    Args:
        build_info: 构建信息字典，含 alert_level / alert_message_id / alert_callback_id
        route_name: webhook路由名称
    """
    import logging
    from logger.context import set_request_context, clear_request_context
    # 使用标准的logging模块，避免导入问题
    app_logger = logging.getLogger('app_logger')

    # 透传原始请求 ID，形成链路日志
    req_id = build_info.get('req_id', '')
    if req_id:
        set_request_context(request_id=req_id)

    try:
        duration_minutes = int((datetime.now() - build_info['start_time']).total_seconds() / 60)
        detail_url = build_info.get('detail_url', '')
        alert_level = build_info.get('alert_level', 'info')
        existing_callback_id = build_info.get('alert_callback_id')

        # 按 alert_level 选择标题颜色
        level_template = {
            'info': 'yellow',
            'warning': 'orange',
            'critical': 'red',
        }.get(alert_level, 'yellow')

        level_text = {
            'info': '构建超时告警',
            'warning': '构建超时升级（warning）',
            'critical': '构建超时严重（critical）',
        }.get(alert_level, '构建超时告警')

        # callback_id 同时作为 open_message_id，首次生成，后续沿用用于更新原卡片
        build_key = build_info.get('build_key') or build_info.get('pipeline_iid')
        callback_id = existing_callback_id or f"build_timeout_{build_key}"

        long_build_message = {
            "msg_type": "interactive",
            "card": {
                "config": {
                    "update_multi": True
                },
                "card_link": {
                    "url": detail_url
                },
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"⚠️ {level_text} - {build_info['project_name']}"
                    },
                    "subtitle": {
                        "tag": "plain_text",
                        "content": f"构建已运行 {duration_minutes} 分钟，仍未完成（级别：{alert_level}）"
                    },
                    "template": level_template
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
                                        f"**状态**：运行中（已超时 {duration_minutes} 分钟）\n"
                                        f"**告警级别**：{alert_level}\n"
                                        f"**建议**：检查构建过程是否卡死或存在性能问题",
                            "text_align": "left",
                            "text_size": "normal"
                        }
                    ]
                }
            }
        }

        chat_id = ROUTE_CHAT_ID_MAP.get(route_name) or build_info.get('chat_id')

        # 首次告警（无 existing_callback_id）：发送新卡片，open_message_id=None 走首次发送分支
        # 后续升级告警（有 existing_callback_id）：用 open_message_id 更新原卡片，避免发新卡片
        result = send_notification(
            route_name, long_build_message, chat_id=chat_id,
            open_message_id=existing_callback_id, callback_id=callback_id,
        )

        if result.get('success'):
            # 回写 callback_id 到 build_info，供后续升级告警复用（作为 open_message_id）
            build_info['alert_callback_id'] = callback_id
            app_logger.info(f"build_monitor | timeout_alert_sent | pipeline_iid={build_info['pipeline_iid']}, route={route_name}, level={alert_level}, method={result.get('method')}, open_message_id={callback_id}")
        else:
            app_logger.error(f"build_monitor | timeout_alert_failed | pipeline_iid={build_info['pipeline_iid']}, level={alert_level}")

    except Exception as e:
        app_logger.error(f"build_monitor | timeout_alert_failed | error={e}")
    finally:
        if req_id:
            clear_request_context()


def check_long_running_builds(running_builds, running_builds_lock):
    """
    后台线程函数，定期检查运行中的构建是否超时
    每60秒检查一次，超过5分钟（300秒）没有结果的构建发送告警

    方案 3 优化：检测/标记/通知解耦
    - 检测：锁内快速扫描，识别超时构建
    - 标记：标记 alerted=True（不移除），下次循环跳过避免重复告警
    - 通知：锁外异步发送告警，避免阻塞监控循环
    - 失败重试：告警发送失败重置 alerted，下次重试
    - 分级告警：info(1x)/warning(3x)/critical(6x)

    Args:
        running_builds: 全局运行中构建字典
        running_builds_lock: 锁对象，确保线程安全
    """
    import logging
    app_logger = logging.getLogger('app_logger')
    while True:
        try:
            current_time = datetime.now()
            builds_to_alert = []  # 待告警列表（锁外处理）

            with running_builds_lock:
                build_count = len(running_builds)
                if build_count > 0:
                    app_logger.debug(f"build_monitor | check | running_count={build_count}")
                    for build_key, build_info in running_builds.items():
                        # 跳过已取消的构建
                        if build_info.get('status') == 'canceled':
                            app_logger.debug(f"build_monitor | skip_canceled | build_key={build_key}")
                            continue

                        elapsed_time = (current_time - build_info['start_time']).total_seconds()

                        # 检查是否在跳过列表中
                        commit_url = build_info.get('commit_url', '')
                        should_skip = False
                        if build_info['project_name'] in SKIP_TIMEOUT_CHECK:
                            should_skip = True
                        elif any(skip_keyword in commit_url for skip_keyword in SKIP_TIMEOUT_CHECK):
                            should_skip = True

                        if not should_skip:
                            timeout_seconds = TIMEOUT_SECONDS.get(build_info['project_name'], 300)
                            if elapsed_time > timeout_seconds:
                                # P0 修复: 分级告警，每个级别只发一次
                                alert_level = _get_alert_level(elapsed_time, timeout_seconds)
                                alerted_levels = build_info.get('alerted_levels', set())
                                if alert_level in alerted_levels:
                                    continue  # 该级别已发过，跳过
                                # 标记该级别已告警（不移除构建记录）
                                alerted_levels.add(alert_level)
                                build_info['alerted_levels'] = alerted_levels
                                build_info['alert_level'] = alert_level
                                # 拷贝 build_info 避免锁外读取时被其他线程修改
                                builds_to_alert.append((build_key, dict(build_info)))
                                app_logger.warning(f"build_monitor | timeout_alert | build_key={build_key}, level={alert_level}")
                            else:
                                app_logger.debug(f"build_monitor | check_build | build_key={build_key}, elapsed={elapsed_time}, timeout={timeout_seconds}")

            # P1 修复: 锁外用线程池并行发送告警，避免阻塞监控循环
            if builds_to_alert:
                def _send_single(item):
                    build_key, info = item
                    try:
                        route_name = info.get('route_name', '')
                        send_long_build_alert(info, route_name)
                        # 返回 info 中可能被回写的 callback_id（作为后续更新的 open_message_id）
                        return build_key, True, None, info.get('alert_callback_id')
                    except Exception as e:
                        return build_key, False, e, None

                with ThreadPoolExecutor(max_workers=3) as pool:
                    results = list(pool.map(_send_single, builds_to_alert))

                for build_key, success, err, cb_id in results:
                    if success:
                        app_logger.info(f"build_monitor | alert_sent | build_key={build_key}")
                        # 回写 callback_id 到原 running_builds，供后续升级告警作为 open_message_id 复用
                        if cb_id:
                            with running_builds_lock:
                                if build_key in running_builds:
                                    running_builds[build_key]['alert_callback_id'] = cb_id
                    else:
                        app_logger.error(f"build_monitor | alert_failed | build_key={build_key}, error={err}")
                        # 告警失败：从 alerted_levels 移除该级别，下次循环重试
                        with running_builds_lock:
                            if build_key in running_builds:
                                info = running_builds[build_key]
                                lvl = info.get('alert_level')
                                if lvl and 'alerted_levels' in info:
                                    info['alerted_levels'].discard(lvl)

            time.sleep(60)

        except Exception as e:
            app_logger.error(f"build_monitor | check_failed | error={e}")
            time.sleep(30)


def _get_alert_level(elapsed, timeout):
    """根据超时倍数返回告警级别

    Args:
        elapsed: 已运行秒数
        timeout: 超时阈值秒数

    Returns:
        str: 'info' / 'warning' / 'critical'
    """
    ratio = elapsed / timeout if timeout > 0 else 0
    if ratio >= 6:    # 30 分钟（6 倍 5 分钟）
        return 'critical'
    elif ratio >= 3:  # 15 分钟
        return 'warning'
    else:             # 5-15 分钟
        return 'info'


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
    app_logger.info("build_monitor | thread_started")
