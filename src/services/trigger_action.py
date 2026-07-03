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

# 本地执行子进程默认超时（秒），可被 action.timeout 覆盖
DEFAULT_LOCAL_EXEC_TIMEOUT = 1800

_trigger_actions_cache = {'actions': None, 'mtime': 0}
_trigger_actions_lock = threading.Lock()

# 内存缓存（最近 200 条，用于快速访问；持久化到数据库）
_trigger_history_cache = []
_trigger_history_lock = threading.Lock()
_TRIGGER_HISTORY_CACHE_MAX = 200


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
    ref_projectcodes = action.get('ref_projectcodes', {}) or {}

    if not project_pattern:
        return False
    if project_pattern not in (path_with_namespace or ''):
        return False

    clean_ref = _strip_ref_prefix(ref)

    # 优先使用 ref_projectcodes 的 key 作为匹配列表（合并 ref_patterns，避免重复配置）
    if ref_projectcodes:
        return clean_ref in ref_projectcodes
    # 兼容 ref_patterns 列表和 ref_pattern 单值
    if ref_patterns:
        return clean_ref in ref_patterns
    if ref_pattern:
        return clean_ref == ref_pattern
    return False


def _build_env_prefix(variables, path_with_namespace, ref, project_name, pipeline_iid=None, action=None):
    env_parts = []
    if variables and isinstance(variables, dict):
        for k, v in variables.items():
            env_parts.append(f"{k}={_shell_quote(str(v))}")
    env_parts.append(f"PROJECTNAME={_shell_quote(project_name or '')}")
    env_parts.append(f"PROJECT={_shell_quote(path_with_namespace or '')}")
    env_parts.append(f"REF={_shell_quote(_strip_ref_prefix(ref))}")
    if pipeline_iid is not None:
        env_parts.append(f"PIPELINE_IID={_shell_quote(str(pipeline_iid))}")

    # 注入 projectcode 与 image_type（从 action 的 ref_projectcodes 与 image_type 解析）
    if action:
        clean_ref = _strip_ref_prefix(ref)
        ref_projectcodes = action.get('ref_projectcodes', {}) or {}
        if clean_ref in ref_projectcodes:
            env_parts.append(f"PROJECTCODE={_shell_quote(ref_projectcodes[clean_ref])}")
        image_type = action.get('image_type', '')
        if image_type:
            env_parts.append(f"IMAGE_TYPE={_shell_quote(image_type)}")

    return ' '.join(env_parts)


def _shell_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def _ensure_projectcode_status_dir():
    """确保 projectcode 状态目录存在"""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    status_dir = os.path.join(project_root, 'workorder', '.projectcode_status')
    os.makedirs(status_dir, exist_ok=True)


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


def _kill_process_group(proc):
    """杀掉子进程及其进程组（兼容 Linux/Windows）"""
    import signal
    try:
        if sys.platform != 'win32':
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            # Windows: taskkill /T 杀进程树
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           capture_output=True, timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _execute_local(action, path_with_namespace, ref, project_name, pipeline_iid=None, trigger_source='auto', start_time=None):
    import subprocess
    if start_time is None:
        start_time = datetime.now()
    name = action.get('name', 'unknown')
    script_name = action.get('script', '')
    variables = action.get('variables', {})

    # per-action 超时覆盖（秒）
    try:
        timeout = int(action.get('timeout', DEFAULT_LOCAL_EXEC_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_LOCAL_EXEC_TIMEOUT
    if timeout <= 0:
        timeout = DEFAULT_LOCAL_EXEC_TIMEOUT

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

    # 注入 projectcode 与 image_type（从 ref_projectcodes 与 image_type 解析）
    clean_ref = _strip_ref_prefix(ref)
    ref_projectcodes = action.get('ref_projectcodes', {}) or {}
    if clean_ref in ref_projectcodes:
        env['PROJECTCODE'] = ref_projectcodes[clean_ref]
        logger.info(f"trigger_action | inject_projectcode | action={name}, ref={clean_ref}, projectcode={env['PROJECTCODE']}")
    image_type = action.get('image_type', '')
    if image_type:
        env['IMAGE_TYPE'] = image_type

    try:
        if script_name.endswith('.py'):
            cmd = [sys.executable, script_path]
        else:
            cmd = ['bash', script_path]

        # 使用 Popen 而非 subprocess.run：
        # POSIX 上 subprocess.run 超时后只 wait()，不填充 exc.output/exc.stderr
        # 改用 Popen + communicate(timeout=...)，超时后 kill 进程组再 communicate 取输出
        popen_kwargs = {
            'env': env,
            'stdout': subprocess.PIPE,
            'stderr': subprocess.PIPE,
            'text': True,
        }
        # Linux 用 os.setsid 创建进程组，便于 kill 整组（docker pull 可能有子进程）
        # Windows 用 CREATE_NEW_PROCESS_GROUP
        if sys.platform != 'win32':
            popen_kwargs['preexec_fn'] = os.setsid
        else:
            popen_kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP

        proc = subprocess.Popen(cmd, **popen_kwargs)
        try:
            output, error_output = proc.communicate(timeout=timeout)
            success = proc.returncode == 0

            if success:
                logger.info(f"trigger_action | local_result | action={name}, script={script_name}, exit_code={proc.returncode}")
            else:
                logger.error(f"trigger_action | local_result | action={name}, script={script_name}, exit_code={proc.returncode}, stderr_len={len((error_output or '').strip())}, stdout_tail={(output or '').strip()[-500:]!r}")

            _notify_result(name, project_name, ref, success, output, error_output, proc.returncode, 'local', variables, trigger_source, pipeline_iid, start_time)
        except subprocess.TimeoutExpired:
            # 超时：kill 整个进程组（避免 docker pull 等子进程残留），再 communicate 取已捕获输出
            _kill_process_group(proc)
            try:
                output, error_output = proc.communicate(timeout=10)
            except Exception:
                output, error_output = '', ''

            partial_out = (output or '').strip()[-500:]
            partial_err = (error_output or '').strip()[-500:]
            logger.error(f"trigger_action | local_result | action={name}, script={script_name}, result=timeout, timeout={timeout}s, stdout_tail={partial_out!r}, stderr_tail={partial_err!r}")
            error_msg = f'本地执行超时({timeout}s)'
            if partial_err:
                error_msg = f'{error_msg}\nstderr_tail: {partial_err}'
            if partial_out:
                error_msg = f'{error_msg}\nstdout_tail: {partial_out}'
            _notify_result(name, project_name, ref, False, partial_out, error_msg, None, 'local', variables, trigger_source, pipeline_iid, start_time)
    except Exception as e:
        logger.error(f"trigger_action | local_result | action={name}, script={script_name}, result=exception, error={e}")
        _notify_result(name, project_name, ref, False, '', str(e), None, 'local', variables, trigger_source, pipeline_iid, start_time)


def _execute_ssh(action, path_with_namespace, ref, project_name, pipeline_iid=None, trigger_source='auto', start_time=None):
    import paramiko
    import socket
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

    # per-action 超时覆盖（秒）
    try:
        timeout = int(action.get('timeout', DEFAULT_LOCAL_EXEC_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_LOCAL_EXEC_TIMEOUT
    if timeout <= 0:
        timeout = DEFAULT_LOCAL_EXEC_TIMEOUT

    if not all([host, user, password]):
        logger.error(f"trigger_action | ssh_config_incomplete | action={name}")
        return

    if not script_name and not ssh_command:
        logger.error(f"trigger_action | no_command | action={name}")
        return

    env_prefix = _build_env_prefix(variables, path_with_namespace, ref, project_name, pipeline_iid, action=action)

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

        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
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
    except socket.timeout:
        # 远程执行超时（channel 无数据流动超过 timeout 秒）
        logger.error(f"trigger_action | ssh_result | action={name}, host={host}, script={script_name or ssh_command}, result=timeout, timeout={timeout}s")
        if script_name:
            try:
                client.exec_command(f"rm -f {remote_script}")
            except Exception:
                pass
        _notify_result(name, project_name, ref, False, '', f'SSH 远程执行超时({timeout}s)', None, host, variables, trigger_source, pipeline_iid, start_time)
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
    # 记录执行历史（持久化到数据库 + 内存缓存）
    _record_history(action_name, project_name, ref, success, output, error_output, exit_code, ssh_host, trigger_source, pipeline_iid, start_time)
    try:
        from src.services.feishu_notify import send_action_result
        send_action_result(action_name, project_name, ref, success, output, error_output, exit_code, ssh_host, variables)
    except Exception as e:
        logger.error(f"trigger_action | notify_failed | error={e}")


def _record_history(action_name, project_name, ref, success, output, error_output, exit_code, ssh_host, trigger_source, pipeline_iid, start_time):
    """记录执行历史到数据库和内存缓存"""
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds() if start_time else 0
    
    # 处理 pipeline_iid 类型
    if pipeline_iid is not None:
        try:
            pipeline_iid_int = int(pipeline_iid)
        except (ValueError, TypeError):
            pipeline_iid_int = None
    else:
        pipeline_iid_int = None
    
    record = {
        'action_name': action_name,
        'project_name': project_name,
        'ref': ref,
        'pipeline_iid': pipeline_iid_int,
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
    
    # 写入内存缓存
    with _trigger_history_lock:
        _trigger_history_cache.insert(0, record)
        if len(_trigger_history_cache) > _TRIGGER_HISTORY_CACHE_MAX:
            _trigger_history_cache.pop()
    
    # 异步写入数据库（不阻塞通知流程）
    def _db_write():
        try:
            from src.services.database import TriggerActionHistoryDB
            TriggerActionHistoryDB.insert(
                action_name=action_name,
                project_name=project_name,
                ref=ref,
                pipeline_iid=pipeline_iid_int,
                success=success,
                exit_code=exit_code,
                start_time=start_time,
                end_time=end_time,
                duration=round(duration, 1),
                output_tail=record['output_tail'],
                error_tail=record['error_tail'],
                ssh_host=ssh_host or 'local',
                trigger_source=trigger_source,
            )
        except Exception as e:
            logger.error(f"trigger_action | db_record_failed | error={e}")
    
    db_thread = threading.Thread(target=_db_write, daemon=True)
    db_thread.start()


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
            'image_type': action.get('image_type', ''),
            'ref_projectcodes': action.get('ref_projectcodes', {}),
            'variables_keys': list(action.get('variables', {}).keys()) if action.get('variables') else [],
        })
    return result


def get_trigger_history(limit=50, offset=0, action_name=None, project_name=None, success=None):
    """获取执行历史记录（优先从数据库读取，支持分页和筛选）
    
    Args:
        limit: 每页条数，默认 50
        offset: 偏移量，默认 0
        action_name: 按 action 名称筛选（可选）
        project_name: 按项目名称筛选（可选）
        success: 按执行结果筛选（可选）
    
    Returns:
        dict: 包含 records、pagination 等信息的字典
    """
    try:
        from src.services.database import TriggerActionHistoryDB
        records, total = TriggerActionHistoryDB.get_list(
            limit=limit,
            offset=offset,
            action_name=action_name,
            project_name=project_name,
            success=success
        )
        
        # 如果数据库没有数据（首次运行），返回内存缓存
        if total == 0:
            with _trigger_history_lock:
                records = _trigger_history_cache[:limit]
                total = len(_trigger_history_cache)
        
        return {
            'records': records,
            'pagination': {
                'total': total,
                'limit': limit,
                'offset': offset,
                'has_more': offset + limit < total
            }
        }
    except Exception as e:
        logger.error(f"trigger_action | get_history_failed | error={e}")
        # 数据库查询失败时降级到内存缓存
        with _trigger_history_lock:
            return {
                'records': _trigger_history_cache[:limit],
                'pagination': {
                    'total': len(_trigger_history_cache),
                    'limit': limit,
                    'offset': offset,
                    'has_more': False
                }
            }


def clear_trigger_history():
    """清空所有执行历史记录（内存缓存 + 数据库）"""
    try:
        # 清空内存缓存
        with _trigger_history_lock:
            cleared_cache = len(_trigger_history_cache)
            _trigger_history_cache.clear()
        # 清空数据库
        from src.services.database import TriggerActionHistoryDB
        deleted_db = TriggerActionHistoryDB.clear()
        logger.info(f"trigger_action | clear_history | cache_cleared={cleared_cache}, db_deleted={deleted_db}")
        return {'success': True, 'message': f'已清空执行历史（内存 {cleared_cache} 条，数据库 {deleted_db} 条）'}
    except Exception as e:
        logger.error(f"trigger_action | clear_history_failed | error={e}")
        return {'success': False, 'message': f'清空失败: {e}'}


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
            # 若配置了 ref_projectcodes，确保 projectcode 状态目录存在
            if action.get('ref_projectcodes'):
                _ensure_projectcode_status_dir()
            _download_workorder(action)

        # 有 SSH 配置则远程执行，否则本地执行
        has_ssh = action.get('ssh_host')
        target = _execute_ssh if has_ssh else _execute_local

        thread = threading.Thread(target=target, args=(action, path_with_namespace, ref, project_name, pipeline_iid), daemon=True)
        thread.start()
