import logging
import time
import requests

logger = logging.getLogger('app_logger')

_token_cache = {'token': None, 'expires_at': 0}


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
    username = config.get('api_username', '')
    password = config.get('api_password', '')
    if not all([base_url, username, password]):
        logger.error("飞书通知配置不完整")
        return None
    token = _login(base_url, username, password)
    if token:
        _token_cache['token'] = token
        _token_cache['expires_at'] = time.time() + 3600
    return token


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
    if not token:
        logger.error("无法获取飞书通知 API Token，跳过通知")
        return

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
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

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
        if e.response is not None and e.response.status_code == 401:
            logger.warning("飞书通知 Token 过期，尝试重新登录")
            token = _get_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
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
    if not token:
        logger.error("无法获取飞书通知 API Token，跳过通知")
        return None

    payload = {
        "card_content": card_content,
        "chat_id": chat_id if has_chat_id else None,
        "user": notify_user if has_user else None,
    }
    if callback_id and callback_id.strip():
        payload["callback_id"] = callback_id.strip()

    notify_url = f"{base_url.rstrip('/')}/api/v1/feishu/notify"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

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
        if e.response is not None and e.response.status_code == 401:
            logger.warning("飞书通知 Token 过期，尝试重新登录")
            token = _get_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
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
    if not token:
        logger.error("无法获取飞书通知 API Token，跳过卡片更新")
        return None

    payload = {
        "card_content": card_content,
        "callback_id": callback_id
    }

    update_url = f"{base_url.rstrip('/')}/api/v1/feishu/notify/{message_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

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
        if e.response is not None and e.response.status_code == 401:
            logger.warning("飞书通知 Token 过期，尝试重新登录")
            token = _get_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
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
