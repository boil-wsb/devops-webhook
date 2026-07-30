import os
import yaml
import logging

logger = logging.getLogger('app_logger')

# mtime 缓存：避免每次调用都读磁盘+解析 YAML（参考 trigger_action._load_trigger_actions）
_config_cache = {'config': None, 'mtime': 0}


def _find_config_path(config_file='config.yaml'):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.join(base_dir, '..', '..')
    return os.path.normpath(os.path.join(project_root, config_file))


def get_config(config_file='config.yaml'):
    config_path = _find_config_path(config_file)

    if not os.path.exists(config_path):
        logger.error(f"配置文件不存在: {config_path}")
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    try:
        mtime = os.path.getmtime(config_path)
    except OSError:
        mtime = 0

    # mtime 缓存命中，直接返回缓存（支持热更新：文件修改后 mtime 变化自动重载）
    if _config_cache['config'] is not None and _config_cache['mtime'] == mtime:
        return _config_cache['config']

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        logger.error(f"YAML 配置文件解析失败 [{config_path}]: {str(e)}")
        raise

    if not config or not isinstance(config, dict):
        logger.error(f"配置文件格式无效 [{config_path}]: 期望非空字典")
        raise ValueError(f"配置文件格式无效: {config_path}")

    _config_cache['config'] = config
    _config_cache['mtime'] = mtime
    return config


def load_config(config_file='config.yaml'):
    config = get_config(config_file)
    notify_config = config.get('notify_config', {})
    return (
        config.get('webhook_config', {}),
        config.get('default_target_url', ''),
        config.get('minio_config', {}),
        config.get('skip_timeout_check', []),
        config.get('timeout_seconds', {}),
        notify_config.get('route_chat_id_map', {}),
    )
