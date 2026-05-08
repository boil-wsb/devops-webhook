from src.config.loader import load_config, get_config

WEBHOOK_CONFIG, DEFAULT_TARGET_URL, MINIO_CONFIG, SKIP_TIMEOUT_CHECK, TIMEOUT_SECONDS = load_config()

__all__ = [
    'WEBHOOK_CONFIG',
    'DEFAULT_TARGET_URL',
    'MINIO_CONFIG',
    'SKIP_TIMEOUT_CHECK',
    'TIMEOUT_SECONDS',
    'load_config',
    'get_config',
]
