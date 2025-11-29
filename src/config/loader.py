import os
import json


def load_config(config_file='config.conf'):
    """
    从配置文件加载配置信息
    Args:
        config_file: 配置文件路径 
    Returns:
        tuple: (WEBHOOK_CONFIG, DEFAULT_TARGET_URL, MINIO_CONFIG)
    """
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', config_file)
    
    # 默认配置，当配置文件不存在或加载失败时使用
    default_config = {
        'webhook_config': {
            'vendor_bot': 'https://open.feishu.cn/open-apis/bot/v2/hook/2d1a1d9f-c5f0-444d-a65d-12ae2af8478e',
            'vendor_bot/v2': 'https://open.feishu.cn/open-apis/bot/v2/hook/6373a601-09e7-4cc9-ae64-4d22ed0f0961',
            'vendor_bot/itreporter': 'https://open.feishu.cn/open-apis/bot/v2/hook/1b78f2d5-0cd0-4035-85fe-a2d8a4b207c6',
        },
        'default_target_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/1b78f2d5-0cd0-4035-85fe-a2d8a4b207c6',
        'minio_config': {
            'minio_endpoint': 'http://192.168.23.36:9000',
            'minio_access_key': 'fYIukgJZaLOivnFimLVX',
            'minio_secret_key': 'shdyfYIukgJZaLOivnFimLVX123',
        }
    }
    
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                webhook_config = config.get('webhook_config', default_config['webhook_config'])
                default_target_url = config.get('default_target_url', default_config['default_target_url'])
                minio_config = config.get('minio_config', default_config['minio_config'])
                return webhook_config, default_target_url, minio_config
        else:
            # 如果配置文件不存在，使用默认配置
            return default_config['webhook_config'], default_config['default_target_url'], default_config['minio_config']
    except json.JSONDecodeError as e:
        # 配置文件格式错误，使用默认配置
        return default_config['webhook_config'], default_config['default_target_url'], default_config['minio_config']
