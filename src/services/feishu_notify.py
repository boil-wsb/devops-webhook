import logging
import time
import requests
import threading

logger = logging.getLogger('app_logger')

_token_cache = {'token': None, 'expires_at': 0}
_open_id_cache = {}
_sent_cards = {}
_sent_cards_lock = threading.Lock()


def _get_notify_config():
    from src.config import get_config
    config = get_config()
    return config.get('notify_config', {})


def _login(base_url, username, password):
    url = f"{base_url.rstrip('/')}/api/v1/auth/login"
    try:
        resp = requests.post(url, json={"username": username, "password": password}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get('access_token')
    except Exception as e:
        logger.error(f"飞书通知 API 登录失败: {str(e)}")
        return None


def _get_token():
    if _token_cache['token'] and time.time() < _token_cache['expires_at']:
        return _token_cache['token']

    config = _get_notify_config()
    if not config:
        return None
    base_url = config.get('api_base_url', '')
    if not base_url:
        logger.error("飞书通知配置不完整: 缺少 api_base_url")
        return None

    username = config.get('api_username', '')
    password = config.get('api_password', '')

    if not username or not password:
        logger.info("飞书通知未配置 api_username/api_password，使用内网免认证模式")
        return None

    token = _login(base_url, username, password)
    if token:
        _token_cache['token'] = token
        _token_cache['expires_at'] = time.time() + 3600
    return token


def _build_headers(token):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _has_credentials():
    config = _get_notify_config()
    if not config:
        return False
    return bool(config.get('api_username', '') and config.get('api_password', ''))


def _refresh_token_on_401(e, headers):
    if e.response is None or e.response.status_code != 401:
        return None
    if not _has_credentials():
        logger.warning("内网免认证模式下收到 401，无法通过重新登录重试")
        return None
    logger.warning("飞书通知 Token 过期，尝试重新登录")
    global _token_cache
    _token_cache = {'token': None, 'expires_at': 0}
    token = _get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        return token
    return None


def get_user_open_id(user_name):
    if not user_name or not user_name.strip():
        return None

    if user_name in _open_id_cache:
        cached = _open_id_cache[user_name]
        if time.time() < cached.get('expires_at', 0):
            return cached.get('open_id')

    config = _get_notify_config()
    if not config:
        return None

    base_url = config.get('api_base_url', '')
    if not base_url:
        logger.error("飞书通知配置不完整: 缺少 api_base_url")
        return None

    token = _get_token()
    headers = _build_headers(token)
    payload = {"name": user_name.strip()}

    lookup_url = f"{base_url.rstrip('/')}/api/v1/open-id"

    try:
        resp = requests.get(lookup_url, params=payload, headers=headers, timeout=10)
        if resp.status_code == 404:
            logger.info(f"open_id 查询接口不可用，尝试通过通知记录获取: user={user_name}")
            return _get_open_id_from_records(user_name, base_url, headers)

        if resp.status_code == 422:
            logger.warning(f"open_id 查询接口参数格式不正确: user={user_name}, 尝试通过通知记录获取")
            return _get_open_id_from_records(user_name, base_url, headers)

        resp.raise_for_status()
        result = resp.json()
        open_id = result.get('feishu_open_id') or result.get('feishuOpenId')
        items = result.get('items', [])
        if not open_id and items:
            open_id = items[0].get('feishuOpenId') or items[0].get('feishu_open_id')
        if open_id:
            _open_id_cache[user_name] = {
                'open_id': open_id,
                'expires_at': time.time() + 3600
            }
            logger.info(f"获取用户 open_id 成功: user={user_name}, open_id={open_id}")
            return open_id
        else:
            logger.warning(f"未找到用户 open_id: user={user_name}, result={result}")
            return _get_open_id_from_records(user_name, base_url, headers)
    except requests.exceptions.HTTPError as e:
        new_token = _refresh_token_on_401(e, headers)
        if new_token:
            try:
                resp = requests.get(lookup_url, params=payload, headers=headers, timeout=10)
                if resp.status_code == 404:
                    return _get_open_id_from_records(user_name, base_url, headers)
                if resp.status_code == 422:
                    return _get_open_id_from_records(user_name, base_url, headers)
                resp.raise_for_status()
                result = resp.json()
                open_id = result.get('feishu_open_id') or result.get('feishuOpenId')
                items = result.get('items', [])
                if not open_id and items:
                    open_id = items[0].get('feishuOpenId') or items[0].get('feishu_open_id')
                if open_id:
                    _open_id_cache[user_name] = {
                        'open_id': open_id,
                        'expires_at': time.time() + 3600
                    }
                    logger.info(f"获取用户 open_id 重试成功: user={user_name}")
                    return open_id
                return _get_open_id_from_records(user_name, base_url, headers)
            except Exception as retry_e:
                logger.error(f"获取用户 open_id 重试异常: {str(retry_e)}")
                return _get_open_id_from_records(user_name, base_url, headers)
        else:
            logger.error(f"获取用户 open_id 失败: {str(e)}")
            return _get_open_id_from_records(user_name, base_url, headers)
    except Exception as e:
        logger.error(f"获取用户 open_id 异常: {str(e)}")
        return _get_open_id_from_records(user_name, base_url, headers)


def _get_open_id_from_records(user_name, base_url, headers):
    records_url = f"{base_url.rstrip('/')}/api/v1/notification-records"
    params = {"user": user_name.strip(), "page_size": 10, "page": 1}

    try:
        resp = requests.get(records_url, params=params, headers=headers, timeout=10)
        if resp.status_code == 422:
            params = {"user": user_name.strip()}
            resp = requests.get(records_url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        items = result.get('items', [])
        for item in items:
            open_id = item.get('feishu_open_id')
            if open_id:
                _open_id_cache[user_name] = {
                    'open_id': open_id,
                    'expires_at': time.time() + 3600
                }
                logger.info(f"通过通知记录获取 open_id 成功: user={user_name}, open_id={open_id}")
                return open_id
        logger.warning(f"通知记录中未找到用户 open_id: user={user_name}")
        return None
    except Exception as e:
        logger.error(f"通过通知记录获取 open_id 异常: {str(e)}")
        return None


def send_action_result(action_name, project_name, ref, success, output='', error_output='', exit_code=None, ssh_host='', variables=None):
    config = _get_notify_config()
    if not config:
        return

    base_url = config.get('api_base_url', '')
    chat_id = config.get('chat_id', '')
    notify_user = config.get('notify_user', '')
    callback_id = config.get('callback_id', '')

    if not base_url:
        logger.error("飞书通知配置不完整: 缺少 api_base_url")
        return

    has_chat_id = bool(chat_id and chat_id.strip())
    has_user = bool(notify_user and notify_user.strip())

    if not has_chat_id and not has_user:
        logger.error("飞书通知配置不完整: 缺少 chat_id 或 notify_user")
        return

    token = _get_token()
    headers = _build_headers(token)

    project_ref = f"{project_name or 'N/A'}:{ref or 'N/A'}"
    deploy_dir = (variables or {}).get('DEPLOY_DIR', '')
    deploy_info = f"{ssh_host}:{deploy_dir}" if deploy_dir else ssh_host or 'N/A'

    if success:
        status_text = "✅ 部署成功"
        template = "green"
        result_line = f"{project_ref} 已deploy至环境"
    else:
        status_text = "❌ 部署失败"
        template = "red"
        exit_info = f"，退出码 {exit_code}" if exit_code is not None else ""
        result_line = f"{project_ref} deploy失败{exit_info}，需检查"

    content_lines = [
        f"**deploy**：{deploy_info}",
        f"**结果**：{result_line}",
    ]

    if not success and error_output and error_output.strip():
        error_display = error_output.strip()
        if len(error_display) > 500:
            error_display = error_display[:500] + "\n...(已截断)"
        content_lines.append(f"**错误输出**：\n```\n{error_display}\n```")

    elements = [{"tag": "markdown", "content": line} for line in content_lines]

    card_content = {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": f"{status_text} - {action_name}"},
            "template": template
        },
        "body": {
            "elements": elements
        }
    }

    payload = {
        "card_content": card_content,
        "user": notify_user if has_user else None,
        "chat_id": chat_id if has_chat_id else None,
    }
    if callback_id and callback_id.strip():
        payload["callback_id"] = callback_id.strip()

    notify_url = f"{base_url.rstrip('/')}/api/v1/feishu/notify"

    try:
        resp = requests.post(notify_url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if result.get('success'):
            target = f"chat_id={chat_id}" if has_chat_id else f"user={notify_user}"
            logger.info(f"飞书通知已发送: action={action_name}, {target}")
        else:
            logger.error(f"飞书通知发送失败: {result.get('error')}")
    except requests.exceptions.HTTPError as e:
        new_token = _refresh_token_on_401(e, headers)
        if new_token:
            try:
                resp = requests.post(notify_url, json=payload, headers=headers, timeout=15)
                resp.raise_for_status()
                result = resp.json()
                if result.get('success'):
                    logger.info(f"飞书通知重试成功: action={action_name}")
                else:
                    logger.error(f"飞书通知重试失败: {result.get('error')}")
            except Exception as retry_e:
                logger.error(f"飞书通知重试异常: {str(retry_e)}")
        else:
            logger.error(f"飞书通知发送失败: {str(e)}")
    except Exception as e:
        logger.error(f"飞书通知发送异常: {str(e)}")


def send_card_via_api(card_content, chat_id=None, notify_user=None, callback_id=None):
    config = _get_notify_config()
    if not config:
        return None

    base_url = config.get('api_base_url', '')
    if not base_url:
        logger.error("飞书通知配置不完整: 缺少 api_base_url")
        return None

    has_chat_id = bool(chat_id and chat_id.strip())
    has_user = bool(notify_user and notify_user.strip())

    if not has_chat_id and not has_user:
        default_chat_id = config.get('chat_id', '')
        if default_chat_id and default_chat_id.strip():
            chat_id = default_chat_id
            has_chat_id = True
        else:
            logger.error("飞书通知配置不完整: 缺少 chat_id 或 notify_user")
            return None

    token = _get_token()
    headers = _build_headers(token)

    payload = {
        "card_content": card_content,
        "chat_id": chat_id if has_chat_id else None,
        "user": notify_user if has_user else None,
    }
    if callback_id and callback_id.strip():
        payload["callback_id"] = callback_id.strip()

    notify_url = f"{base_url.rstrip('/')}/api/v1/feishu/notify"

    try:
        resp = requests.post(notify_url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if result.get('success'):
            target = f"chat_id={chat_id}" if has_chat_id else f"user={notify_user}"
            logger.info(f"飞书卡片通知已发送: {target}, message_id={result.get('message_id')}")
            return result
        else:
            logger.error(f"飞书卡片通知发送失败: {result.get('error')}")
            return None
    except requests.exceptions.HTTPError as e:
        new_token = _refresh_token_on_401(e, headers)
        if new_token:
            try:
                resp = requests.post(notify_url, json=payload, headers=headers, timeout=15)
                resp.raise_for_status()
                result = resp.json()
                if result.get('success'):
                    logger.info("飞书卡片通知重试成功")
                    return result
                else:
                    logger.error(f"飞书卡片通知重试失败: {result.get('error')}")
                    return None
            except Exception as retry_e:
                logger.error(f"飞书卡片通知重试异常: {str(retry_e)}")
                return None
        else:
            logger.error(f"飞书卡片通知发送失败: {str(e)}")
            return None
    except Exception as e:
        logger.error(f"飞书卡片通知发送异常: {str(e)}")
        return None


def update_card_via_api(card_content, message_id, callback_id):
    config = _get_notify_config()
    if not config:
        return None

    base_url = config.get('api_base_url', '')
    if not base_url:
        logger.error("飞书通知配置不完整: 缺少 api_base_url")
        return None

    token = _get_token()
    headers = _build_headers(token)

    payload = {
        "card_content": card_content,
        "callback_id": callback_id
    }

    update_url = f"{base_url.rstrip('/')}/api/v1/feishu/notify/{message_id}"

    try:
        resp = requests.patch(update_url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if result.get('success'):
            logger.info(f"飞书卡片通知已更新: message_id={message_id}")
            return result
        else:
            logger.error(f"飞书卡片通知更新失败: {result.get('error')}")
            return None
    except requests.exceptions.HTTPError as e:
        new_token = _refresh_token_on_401(e, headers)
        if new_token:
            try:
                resp = requests.patch(update_url, json=payload, headers=headers, timeout=15)
                resp.raise_for_status()
                result = resp.json()
                if result.get('success'):
                    logger.info("飞书卡片通知更新重试成功")
                    return result
                else:
                    logger.error(f"飞书卡片通知更新重试失败: {result.get('error')}")
                    return None
            except Exception as retry_e:
                logger.error(f"飞书卡片通知更新重试异常: {str(retry_e)}")
                return None
        else:
            logger.error(f"飞书卡片通知更新失败: {str(e)}")
            return None
    except Exception as e:
        logger.error(f"飞书卡片通知更新异常: {str(e)}")
        return None


def store_sent_card(callback_id, card_content, chat_id=None):
    if not callback_id:
        return
    with _sent_cards_lock:
        _sent_cards[callback_id] = {
            'card_content': card_content,
            'chat_id': chat_id,
            'stored_at': time.time()
        }
        if len(_sent_cards) > 500:
            sorted_keys = sorted(_sent_cards.keys(), key=lambda k: _sent_cards[k]['stored_at'])
            for k in sorted_keys[:100]:
                del _sent_cards[k]


def get_sent_card(callback_id):
    with _sent_cards_lock:
        return _sent_cards.get(callback_id)


def forward_card_to_assignee(callback_id, assignee_open_id):
    card_info = get_sent_card(callback_id)
    if not card_info:
        logger.error(f"未找到已发送卡片: callback_id={callback_id}")
        return None

    card_content = card_info['card_content']
    result = send_card_via_api(card_content, notify_user=assignee_open_id)
    if result and result.get('success'):
        logger.info(f"卡片已转发给负责人: callback_id={callback_id}, assignee={assignee_open_id}")
    else:
        logger.error(f"卡片转发失败: callback_id={callback_id}, result={result}")
    return result


def handle_card_action_callback(data):
    action = data.get('action', {})
    value = action.get('value', {})
    action_type = value.get('action', '')

    if action_type == 'forward_to_assignee':
        callback_id = value.get('callback_id', '')
        assignee_open_id = value.get('assignee_open_id', '')
        operator_open_id = data.get('open_id', '')

        logger.info(f"收到卡片转发回调: callback_id={callback_id}, operator={operator_open_id}, assignee={assignee_open_id}")

        result = forward_card_to_assignee(callback_id, assignee_open_id)

        if result and result.get('success'):
            return {
                "toast": {"type": "success", "content": "已转交负责人处理"}
            }
        else:
            return {
                "toast": {"type": "error", "content": "转交失败，请稍后重试"}
            }

    return None
