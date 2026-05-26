import logging
from .webhook_logger import WebhookLogger, webhook_logger, MonitorLogger, monitor_logger, AccessLogger, access_logger, AppLogger, app_logger

_LOGGER_MAP = {
    'app': 'app_logger',
    'webhook': 'webhook_logger',
    'monitor': 'monitor_event_logger',
    'access': 'access_logger',
}


def get_logger(name='app'):
    return logging.getLogger(_LOGGER_MAP.get(name, name))


__all__ = [
    'WebhookLogger', 'webhook_logger',
    'MonitorLogger', 'monitor_logger',
    'AccessLogger', 'access_logger',
    'AppLogger', 'app_logger',
    'get_logger',
]
