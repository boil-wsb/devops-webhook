# 导入配置加载器
from src.config.loader import load_config

# 加载配置
WEBHOOK_CONFIG, DEFAULT_TARGET_URL, MINIO_CONFIG = load_config()

# 导出所有公共API
__all__ = [
    'WEBHOOK_CONFIG',
    'DEFAULT_TARGET_URL',
    'MINIO_CONFIG'
]
