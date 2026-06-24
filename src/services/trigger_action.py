import hashlib
import os
import sys
import threading
import logging
import yaml
from datetime import datetime

logger = logging.getLogger('app_logger')

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'scripts')
TRIGGER_ACTIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'trigger_actions.yaml')
WORKORDER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'workorder')

_trigger_actions_cache = {'actions': None, 'mtime': 0}
_trigger_actions_lock = threading.Lock()

# 执行历史记录（内存存储，最多保留 200 条）
_trigger_history = []
_trigger_history_lock = threading.Lock()
_TRIGGER_HISTORY_MAX = 200


def _strip_ref_prefix(ref):
    for prefix in ('refs/heads/', 'refs/tags/', 'refs/remotes/'):
        if ref.startswith(prefix):
            return ref.replace(prefix, '', 1)
    return ref


def _load_trigger_actions():
    with _trigger_actions_lock:
        try:
            mtime = os.path.getmtime(TRIGGER_ACTIONS_FILE)
        except OSError:
            logger.warning("trigger_action | config_not_found | file=trigger_actions.yaml")
            return []

        # 文件未修改，返回缓存
        if _trigger_actions_cache['actions'] is not None and _trigger_actions_cache['mtime'] == mtime:
            return _trigger_actions_cache['actions']

        # 文件已修改，重新加载
        try:
            with open(TRIGGER_ACTIONS_FILE, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            actions = config.get('trigger_actions', []) if config else []
            _trigger_actions_cache['actions'] = actions
            _trigger_actions_cache['mtime'] = mtime
            logger.info(f"trigger_action | config_reloaded | actions_count={len(actions)}")
            return actions
        except Exception as e:
            logger.error(f"trigger_action | config_load_failed | error={e}")
            return _trigger_actions_cache['actions'] or []


def _match_trigger(action, path_with_namespace, ref):
    project_pattern = action.get('project_pattern', '')
    ref_pattern = action.get('ref_pattern', '')
    ref_patterns = action.get('ref_patterns', [])

    if not project_pattern:
        return False
    if project_pattern not in (path_with_namespace or ''):
        return False

    clean_ref = _strip_ref_prefix(ref)

    # 支持 ref_patterns 列表（优先）和 ref_pattern 单值（兼容）
    if ref_patterns:
        return clean_ref in ref_patterns
    if ref_pattern:
        return clean_ref == ref_pattern
    return False


def _build_env_prefix(variables, path_with_namespace, ref, project_name, pipeline_iid=None):
    env_parts = []
    if variables and isinstance(variables, dict):
        for k, v in variables.items():
            env_parts.append(f"{k}={_shell_quote(str(v))}")
    env_parts.append(f"PROJECTNAME={_shell_quote(project_name or '')}")
    env_parts.append(f"PROJECT={_shell_quote(path_with_namespace or '')}")
    env_parts.append(f"REF={_shell_quote(_strip_ref_prefix(ref))}")
    if pipeline_iid is not None:
        env_parts.append(f"PIPELINE_IID={_shell_quote(str(pipeline_iid))}")
    return ' '.join(env_parts)


def _shell_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def _download_workorder(action):
    """当 action 配置 workorder: true 时，从 MinIO 下载 deploy.zip（MD5 一致则跳过），并解压"""
    import zipfile
    from minio import Minio

    variables = action.get('variables', {})
    endpoint = variables.get('MINIO_ENDPOINT', '')
    access_key = variables.get('MINIO_ACCESS_KEY', '')
    secret_key = variables.get('MINIO_SECRET_KEY', '')

    if not all([endpoint, access_key, secret_key]):
        logger.error(f"trigger_action | workorder_config_incomplete | action={action.get('name')}, missing MINIO_ENDPOINT/ACCESS_KEY/SECRET_KEY")
        return False

    bucket = 'workorder'
    remote_zip = 'deploy.zip'
    remote_md5 = 'deploy.zip.md5'

    os.makedirs(WORKORDER_DIR, exist_ok=True)
    local_zip = os.path.join(WORKORDER_DIR, remote_zip)
    local_md5 = os.path.join(WORKORDER_DIR, remote_md5)
    extract_dir = os.path.join(WORKORDER_DIR, 'deploy')

    try:
        client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)

        # 下载远程 MD5 文件
        client.fget_object(bucket, remote_md5, local_md5)
        with open(local_md5, 'r') as f:
            remote_md5_value = f.read().strip()

        # 检查本地文件 MD5
        need_download = True
        if os.path.exists(local_zip):
            local_md5_value = _calc_md5(local_zip)
            if local_md5_value == remote_md5_value:
                need_download = False
                logger.info(f"trigger_action | workorder_skip_download | local_md5={local_md5_value}, remote_md5={remote_md5_value}")
            else:
                logger.info(f"trigger_action | workorder_mismatch | local_md5={local_md5_value}, remote_md5={remote_md5_value}, re_downloading")

        # 下载 deploy.zip
        if need_download:
            logger.info(f"trigger_action | workorder_downloading | bucket={bucket}, file={remote_zip}")
            client.fget_object(bucket, remote_zip, local_zip)

            # 校验下载后的 MD5
            local_md5_value = _calc_md5(local_zip)
            if local_md5_value != remote_md5_value:
                logger.error(f"trigger_action | workorder_verify_failed | local_md5={local_md5_value}, remote_md5={remote_md5_value}")
                return False

            logger.info(f"trigger_action | workorder_downloaded | file={local_zip}, md5={local_md5_value}")

        # 检查是否需要解压（MD5 变化或解压目录不存在）
        extract_md5_file = os.path.join(extract_dir, '.zip_md5')
        need_extract = need_download or not os.path.exists(extract_dir) or not os.path.exists(extract_md5_file)

        if not need_extract and os.path.exists(extract_md5_file):
            with open(extract_md5_file, 'r') as f:
                cached_md5 = f.read().strip()
            if cached_md5 == remote_md5_value:
                logger.info(f"trigger_action | workorder_skip_extract | extract_dir={extract_dir}")
                return True

        # 解压 deploy.zip
        logger.info(f"trigger_action | workorder_extracting | zip={local_zip}, dir={extract_dir}")
        with zipfile.ZipFile(local_zip, 'r') as zf:
            zf.extractall(extract_dir)

        # 写入解压标记
        with open(extract_md5_file, 'w') as f:
            f.write(remote_md5_value)

        logger.info(f"trigger_action | workorder_extracted | dir={extract_dir}, md5={remote_md5_value}")
        return True
    except Exception as e:
        logger.error(f"trigger_action | workorder_failed | error={e}")
        return False


def _calc_md5(filepath):
    md5 = hashlib.md5()
    with open(filepath, 'rb') as f:
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                break
            md5.update(chunk)
    return md5.hexdigest()


def _execute_local(action, path_with_namespace, ref, project_name, pipeline_iid=None, trigger_source='auto', start_time=None):
    import subprocess
    if start_time is None:
        start_time = datetime.now()
    name = action.get('name', 'unknown')
    script_name = action.get('script', '')
    variables = action.get('variables', {})

    if not script_name:
        logger.error(f"trigger_action | no_script | action={name}")
        return

    script_path = os.path.normpath(os.path.join(SCRIPTS_DIR, script_name))
    if not os.path.exists(script_path):
        logger.error(f"trigger_action | script_not_found | action={name}, path={script_path}")
        return

    env = os.environ.copy()
    if variables and isinstance(variables, dict):
        for k, v in variables.items():
            env[k] = str(v)
    env['PROJECTNAME'] = project_name or ''
    env['PROJECT'] = path_with_namespace or ''
    env['REF'] = _strip_ref_prefix(ref)
    if pipeline_iid is not None:
        env['PIPELINE_IID'] = str(pipeline_iid)

    try:
        if script_name.endswith('.py'):
            cmd = [sys.executable, script_path]
        else:
            cmd = ['bash', script_path]
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=300
        )
        success = result.returncode == 0
        output = result.stdout
        error_output = result.stderr

        if success:
            logger.info(f"trigger_action | local_result | action={name}, script={script_name}, exit_code={result.returncode}")
        else:
            logger.error(f"trigger_action | local_result | action={name}, script={script_name}, exit_code={result.returncode}, stderr_len={len(error_output.strip())}, stdout_tail={(output or '').strip()[-500:]!r}")

        _notify_result(name, project_name, ref, success, output, error_output, result.returncode, 'local', variables, trigger_source, pipeline_iid, start_time)
    except subprocess.TimeoutExpired:
        logger.error(f"trigger_action | local_result | action={name}, script={script_name}, result=timeout")
        _notify_result(name, project_name, ref, False, '', '本地执行超时(300s)', None, 'local', variables, trigger_source, pipeline_iid, start_time)
    except Exception as e:
        logger.error(f"trigger_action | local_result | action={name}, script={script_name}, result=exception, error={e}")
        _notify_result(name, project_name, ref, False, '', str(e), None, 'local', variables, trigger_source, pipeline_iid, start_time)


def _execute_ssh(action, path_with_namespace, ref, project_name, pipeline_iid=None, trigger_source='auto', start_time=None):
    import paramiko
    if start_time is None:
        start_time = datetime.now()
    name = action.get('name', 'unknown')
    host = action.get('ssh_host', '')
    port = action.get('ssh_port', 22)
    user = action.get('ssh_user', '')
    password = action.get('ssh_password', '')
    script_name = action.get('script', '')
    ssh_command = action.get('ssh_command', '')
    variables = action.get('variables', {})

    if not all([host, user, password]):
        logger.error(f"trigger_action | ssh_config_incomplete | action={name}")
        return

    if not script_name and not ssh_command:
        logger.error(f"trigger_action | no_command | action={name}")
        return

    env_prefix = _build_env_prefix(variables, path_with_namespace, ref, project_name, pipeline_iid)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=host, port=port, username=user, password=password, timeout=30)

        if script_name:
            script_path = os.path.normpath(os.path.join(SCRIPTS_DIR, script_name))
            if not os.path.exists(script_path):
                logger.error(f"trigger_action | script_not_found | action={name}, path={script_path}")
                return

            remote_script = f"/tmp/_trigger_{name}_{os.getpid()}.sh"
            sftp = client.open_sftp()
            try:
                sftp.put(script_path, remote_script)
            finally:
                sftp.close()

            command = f"{env_prefix} bash {remote_script} && rm -f {remote_script}"
        else:
            command = f"{env_prefix} bash -c {_shell_quote(ssh_command)}"

        stdin, stdout, stderr = client.exec_command(command)
        exit_code = stdout.channel.recv_exit_status()
        output = stdout.read().decode('utf-8', errors='replace')
        error_output = stderr.read().decode('utf-8', errors='replace')

        success = exit_code == 0
        if success:
            logger.info(f"trigger_action | ssh_result | action={name}, host={host}, script={script_name or ssh_command}, exit_code={exit_code}")
        else:
            logger.error(f"trigger_action | ssh_result | action={name}, host={host}, script={script_name or ssh_command}, exit_code={exit_code}, stderr_len={len(error_output.strip())}, stdout_tail={(output or '').strip()[-500:]!r}")
            if script_name:
                client.exec_command(f"rm -f {remote_script}")

        _notify_result(name, project_name, ref, success, output, error_output, exit_code, host, variables, trigger_source, pipeline_iid, start_time)
    except paramiko.AuthenticationException:
        logger.error(f"trigger_action | ssh_result | action={name}, host={host}:{port}, result=auth_failed")
        _notify_result(name, project_name, ref, False, '', f'SSH 认证失败: {user}@{host}:{port}', None, host, variables, trigger_source, pipeline_iid, start_time)
    except paramiko.SSHException as e:
        logger.error(f"trigger_action | ssh_result | action={name}, host={host}, result=ssh_exception, error={e}")
        _notify_result(name, project_name, ref, False, '', f'SSH 连接异常: {e}', None, host, variables, trigger_source, pipeline_iid, start_time)
    except Exception as e:
        logger.error(f"trigger_action | ssh_result | action={name}, host={host}, result=exception, error={e}")
        _notify_result(name, project_name, ref, False, '', str(e), None, host, variables, trigger_source, pipeline_iid, start_time)
    finally:
        client.close()


def _notify_result(action_name, project_name, ref, success, output='', error_output='', exit_code=None, ssh_host='', variables=None, trigger_source='auto', pipeline_iid=None, start_time=None):
    # 记录执行历史
    _record_history(action_name, project_name, ref, success, output, error_output, exit_code, ssh_host, trigger_source, pipeline_iid, start_time)
    try:
        from src.services.feishu_notify import send_action_result
        send_action_result(action_name, project_name, ref, success, output, error_output, exit_code, ssh_host, variables)
    except Exception as e:
        logger.error(f"trigger_action | notify_failed | error={e}")


def _record_history(action_name, project_name, ref, success, output, error_output, exit_code, ssh_host, trigger_source, pipeline_iid, start_time):
    """记录执行历史到内存列表"""
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds() if start_time else 0
    record = {
        'action_name': action_name,
        'project_name': project_name,
        'ref': ref,
        'pipeline_iid': pipeline_iid,
        'success': success,
        'exit_code': exit_code,
        'start_time': start_time.strftime('%Y-%m-%d %H:%M:%S') if start_time else '',
        'end_time': end_time.strftime('%Y-%m-%d %H:%M:%S'),
        'duration': round(duration, 1),
        'output_tail': (output or '').strip()[-500:] if output else '',
        'error_tail': (error_output or '').strip()[-500:] if error_output else '',
        'ssh_host': ssh_host or 'local',
        'trigger_source': trigger_source,
    }
    with _trigger_history_lock:
        _trigger_history.insert(0, record)
        if len(_trigger_history) > _TRIGGER_HISTORY_MAX:
            _trigger_history.pop()


def get_trigger_actions_config():
    """获取当前 trigger_actions 配置（从内存缓存）"""
    actions = _load_trigger_actions()
    result = []
    for action in actions:
        result.append({
            'name': action.get('name', ''),
            'project_pattern': action.get('project_pattern', ''),
            'ref_pattern': action.get('ref_pattern', ''),
            'ref_patterns': action.get('ref_patterns', []),
            'script': action.get('script', ''),
            'ssh_host': action.get('ssh_host', ''),
            'ssh_port': action.get('ssh_port', 22),
            'workorder': action.get('workorder', False),
            'variables_keys': list(action.get('variables', {}).keys()) if action.get('variables') else [],
        })
    return result


def get_trigger_history(limit=50):
    """获取执行历史记录"""
    with _trigger_history_lock:
        return _trigger_history[:limit]


def manual_trigger(action_name, ref='', pipeline_iid=None):
    """手动触发某个 action"""
    actions = _load_trigger_actions()
    action = next((a for a in actions if a.get('name') == action_name), None)
    if not action:
        return {'success': False, 'message': f'未找到 action: {action_name}'}

    project_pattern = action.get('project_pattern', '')
    path_with_namespace = project_pattern
    project_name = project_pattern.split('/')[-1] if '/' in project_pattern else project_pattern

    has_ssh = action.get('ssh_host')
    target = _execute_ssh if has_ssh else _execute_local

    def _wrapped():
        _start = datetime.now()
        target(action, path_with_namespace, ref, project_name, pipeline_iid, trigger_source='manual', start_time=_start)

    thread = threading.Thread(target=_wrapped, daemon=True)
    thread.start()
    return {'success': True, 'message': f'已触发 action: {action_name}'}


def check_and_trigger(path_with_namespace, ref, project_name='', pipeline_iid=None):
    trigger_actions = _load_trigger_actions()
    if not trigger_actions:
        return

    matched = [a for a in trigger_actions if _match_trigger(a, path_with_namespace, ref)]
    if not matched:
        return

    for action in matched:
        name = action.get('name', 'unknown')
        logger.info(f"trigger_action | condition_match | action={name}, project={project_name}, ref={ref}")

        # workorder 下载（配置 workorder: true 时触发）
        if action.get('workorder'):
            _download_workorder(action)

        # 有 SSH 配置则远程执行，否则本地执行
        has_ssh = action.get('ssh_host')
        target = _execute_ssh if has_ssh else _execute_local

        thread = threading.Thread(target=target, args=(action, path_with_namespace, ref, project_name, pipeline_iid), daemon=True)
        thread.start()
