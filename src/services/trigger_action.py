import os
import threading
import logging

logger = logging.getLogger('app_logger')

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'scripts')


def _strip_ref_prefix(ref):
    for prefix in ('refs/heads/', 'refs/tags/', 'refs/remotes/'):
        if ref.startswith(prefix):
            return ref.replace(prefix, '', 1)
    return ref


def _match_trigger(action, path_with_namespace, ref):
    project_pattern = action.get('project_pattern', '')
    ref_pattern = action.get('ref_pattern', '')
    if not project_pattern or not ref_pattern:
        return False
    if project_pattern not in (path_with_namespace or ''):
        return False
    clean_ref = _strip_ref_prefix(ref)
    if clean_ref != ref_pattern:
        return False
    return True


def _build_env_prefix(variables, path_with_namespace, ref, project_name):
    env_parts = []
    if variables and isinstance(variables, dict):
        for k, v in variables.items():
            env_parts.append(f"{k}={_shell_quote(str(v))}")
    env_parts.append(f"PROJECTNAME={_shell_quote(project_name or '')}")
    env_parts.append(f"PROJECT={_shell_quote(path_with_namespace or '')}")
    env_parts.append(f"REF={_shell_quote(_strip_ref_prefix(ref))}")
    return ' '.join(env_parts)


def _shell_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def _execute_ssh(action, path_with_namespace, ref, project_name):
    import paramiko
    name = action.get('name', 'unknown')
    host = action.get('ssh_host', '')
    port = action.get('ssh_port', 22)
    user = action.get('ssh_user', '')
    password = action.get('ssh_password', '')
    script_name = action.get('script', '')
    ssh_command = action.get('ssh_command', '')
    variables = action.get('variables', {})

    if not all([host, user, password]):
        logger.error(f"触发动作 [{name}] SSH 配置不完整，跳过执行")
        return

    if not script_name and not ssh_command:
        logger.error(f"触发动作 [{name}] 未配置 script 或 ssh_command，跳过执行")
        return

    env_prefix = _build_env_prefix(variables, path_with_namespace, ref, project_name)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        logger.info(f"触发动作 [{name}] 正在连接 {user}@{host}:{port} ...")
        client.connect(hostname=host, port=port, username=user, password=password, timeout=30)

        if script_name:
            script_path = os.path.normpath(os.path.join(SCRIPTS_DIR, script_name))
            if not os.path.exists(script_path):
                logger.error(f"触发动作 [{name}] 脚本文件不存在: {script_path}")
                return

            remote_script = f"/tmp/_trigger_{name}_{os.getpid()}.sh"
            sftp = client.open_sftp()
            try:
                sftp.put(script_path, remote_script)
                logger.info(f"触发动作 [{name}] 已上传脚本 {script_name} -> {remote_script}")
            finally:
                sftp.close()

            command = f"{env_prefix} bash {remote_script} && rm -f {remote_script}"
        else:
            command = f"{env_prefix} bash -c {_shell_quote(ssh_command)}"

        logger.info(f"触发动作 [{name}] 执行命令: {command}")
        stdin, stdout, stderr = client.exec_command(command)
        exit_code = stdout.channel.recv_exit_status()
        output = stdout.read().decode('utf-8', errors='replace')
        error_output = stderr.read().decode('utf-8', errors='replace')

        success = exit_code == 0
        if success:
            logger.info(f"触发动作 [{name}] 执行成功 (exit_code={exit_code})")
            if output.strip():
                logger.info(f"触发动作 [{name}] 输出:\n{output.strip()}")
        else:
            logger.error(f"触发动作 [{name}] 执行失败 (exit_code={exit_code})")
            if error_output.strip():
                logger.error(f"触发动作 [{name}] 错误输出:\n{error_output.strip()}")
            if output.strip():
                logger.info(f"触发动作 [{name}] 标准输出:\n{output.strip()}")
            if script_name:
                client.exec_command(f"rm -f {remote_script}")

        _notify_result(name, project_name, ref, success, output, error_output, exit_code, host, variables)
    except paramiko.AuthenticationException:
        logger.error(f"触发动作 [{name}] SSH 认证失败: {user}@{host}:{port}")
        _notify_result(name, project_name, ref, False, '', f'SSH 认证失败: {user}@{host}:{port}', None, host, variables)
    except paramiko.SSHException as e:
        logger.error(f"触发动作 [{name}] SSH 连接异常: {str(e)}")
        _notify_result(name, project_name, ref, False, '', f'SSH 连接异常: {str(e)}', None, host, variables)
    except Exception as e:
        logger.error(f"触发动作 [{name}] 执行异常: {str(e)}")
        _notify_result(name, project_name, ref, False, '', str(e), None, host, variables)
    finally:
        client.close()


def _notify_result(action_name, project_name, ref, success, output='', error_output='', exit_code=None, ssh_host='', variables=None):
    try:
        from src.services.feishu_notify import send_action_result
        send_action_result(action_name, project_name, ref, success, output, error_output, exit_code, ssh_host, variables)
    except Exception as e:
        logger.error(f"发送飞书通知异常: {str(e)}")


def check_and_trigger(path_with_namespace, ref, project_name=''):
    from src.config import get_config
    config = get_config()
    trigger_actions = config.get('trigger_actions', [])
    if not trigger_actions:
        return

    matched = [a for a in trigger_actions if _match_trigger(a, path_with_namespace, ref)]
    if not matched:
        return

    for action in matched:
        name = action.get('name', 'unknown')
        logger.info(f"触发动作 [{name}] 条件匹配: project={path_with_namespace}, projectName={project_name}, ref={ref}")
        thread = threading.Thread(target=_execute_ssh, args=(action, path_with_namespace, ref, project_name), daemon=True)
        thread.start()
