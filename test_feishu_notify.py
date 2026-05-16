import sys
import os
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('WEBHOOK_CONFIG', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml'))

from src.services.feishu_notify import (
    get_user_open_id, send_card_via_api, _get_notify_config,
    _get_token, _build_headers, _has_credentials
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('test_logger')


def test_intranet_mode():
    config = _get_notify_config()
    has_creds = _has_credentials()
    token = _get_token()

    logger.info("=" * 60)
    logger.info("内网免认证模式测试")
    logger.info("=" * 60)
    logger.info(f"api_base_url: {config.get('api_base_url', '')}")
    logger.info(f"api_username: {'***已配置***' if config.get('api_username') else '未配置'}")
    logger.info(f"api_password: {'***已配置***' if config.get('api_password') else '未配置'}")
    logger.info(f"has_credentials: {has_creds}")
    logger.info(f"token: {'已获取' if token else 'None (内网免认证模式)'}")
    logger.info(f"headers: {_build_headers(token)}")

    if not has_creds and not token:
        logger.info("✅ 内网免认证模式已激活: 无需 api_username/api_password")
    elif has_creds and token:
        logger.info("✅ Token 认证模式: 使用 api_username/api_password 登录获取 Token")
    elif has_creds and not token:
        logger.warning("⚠️ 已配置凭据但登录失败，请检查 api_username/api_password 是否正确")
    else:
        logger.warning("⚠️ 未配置凭据且不在内网，API 调用可能失败")


def test_open_id_and_card():
    logger.info("=" * 60)
    logger.info("测试 open_id 获取和卡片发送")
    logger.info("=" * 60)

    test_user = "王仕彬"
    callback_id = f"test_at_{int(time.time())}"

    open_id = get_user_open_id(test_user)
    if open_id:
        at_content = f'<at id="{open_id}"></at>'
        logger.info(f"✅ 获取 open_id 成功: {open_id}")
    else:
        at_content = test_user
        logger.warning(f"⚠️ 未获取到 open_id，使用纯文本: {test_user}")

    config = _get_notify_config()
    chat_id = config.get('chat_id', '')

    failed_card = {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": "❌ 测试-构建失败"},
            "template": "red"
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": f"***提交人员***：{at_content}"},
                {"tag": "markdown", "content": "***持续时间***：30秒"},
                {"tag": "markdown", "content": "***分      支***：test-branch"},
                {"tag": "markdown", "content": "***Commit***：fix: 测试"},
            ]
        },
        "config": {"update_multi": True}
    }

    logger.info(f"发送卡片, chat_id={chat_id}, callback_id={callback_id}")
    result = send_card_via_api(failed_card, chat_id=chat_id, callback_id=callback_id)

    if result and result.get('success'):
        logger.info(f"✅ 卡片发送成功: message_id={result.get('message_id')}")
    else:
        logger.error(f"❌ 发送失败: {result}")


if __name__ == '__main__':
    test_intranet_mode()
    test_open_id_and_card()
