"""部署脚本通用工具函数"""
import os
import re
import json
import shutil
import hashlib
import zipfile
import sys
import subprocess
import threading
from datetime import datetime
from minio import Minio
from minio.error import S3Error


def check_dependencies(required_commands):
    """检查所需命令是否存在于系统 PATH 中

    Args:
        required_commands: 需要检查的命令列表，如 ['docker']

    Returns:
        list: 缺失的命令列表
    """
    missing = []
    for cmd in required_commands:
        if shutil.which(cmd) is None:
            missing.append(cmd)
    return missing


def _get_project_root():
    """获取项目根目录（scripts/ 的上级目录）"""
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(scripts_dir)


def ensure_workorder_dirs():
    """确保 workorder 目录树存在，不存在则创建

    创建以下目录:
      - workorder/
      - workorder/deploy/
      - workorder/deploy/images/

    Returns:
        str: workorder/deploy 目录的绝对路径
    """
    project_root = _get_project_root()
    workorder_dir = os.path.join(project_root, 'workorder')
    deploy_dir = os.path.join(workorder_dir, 'deploy')
    images_dir = os.path.join(deploy_dir, 'images')
    for d in (workorder_dir, deploy_dir, images_dir):
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            print(f"  创建目录: {d}")
    return deploy_dir


def get_workorder_images_dir():
    """获取 workorder/deploy/images 目录路径（用于保存 docker 镜像 tar 文件）

    目录相对于项目根目录（scripts/ 的上级），不存在时自动创建。

    Returns:
        str: images 目录的绝对路径
    """
    ensure_workorder_dirs()
    project_root = _get_project_root()
    return os.path.join(project_root, 'workorder', 'deploy', 'images')


def get_workorder_deploy_dir():
    """获取 workorder/deploy 目录路径

    目录不存在时自动创建。

    Returns:
        str: deploy 目录的绝对路径
    """
    ensure_workorder_dirs()
    project_root = _get_project_root()
    return os.path.join(project_root, 'workorder', 'deploy')


def _ensure_base_deploy_package(minio_config, projectcode=None):
    """确保基准部署包已从 MinIO 下载并解压到 workorder/deploy/

    在 orchestrate_image_deploy / orchestrate_file_deploy 处理前调用。
    从 MinIO workorder 路径下载通用 deploy.zip（基准包），解压到
    workorder/deploy/ 作为后续镜像 save 和打包的基础。

    流程：
    1. 增量模式（first_pack_completed=True）下跳过下载解压，避免覆盖产物
    2. 首次模式下从 MinIO 下载 deploy.zip.md5
    3. 本地 MD5 一致则跳过下载
    4. 否则下载 deploy.zip
    5. 已解压且 MD5 标记一致则跳过解压
    6. 否则解压到 workorder/deploy/ 并写入 .zip_md5 标记

    Args:
        minio_config: MinIO 配置 dict（endpoint/access_key/secret_key/bucket）
        projectcode: 工单号，用于检查 first_pack_completed 状态（增量模式跳过下载）
    """
    import zipfile

    if not minio_config:
        ensure_workorder_dirs()
        return

    # P0 修复: 增量模式下跳过基准包下载解压，避免覆盖 deploy/images/ 中的产物
    if projectcode:
        try:
            status = load_projectcode_status(projectcode, minio_config)
            if status.get('first_pack_completed'):
                print(f"  基准包: 增量模式跳过下载解压（first_pack_completed=True）")
                ensure_workorder_dirs()
                return
        except Exception as e:
            print(f"  基准包: 状态检查失败，按首次模式处理: {e}")

    try:
        client = get_minio_client(
            minio_config['endpoint'],
            minio_config['access_key'],
            minio_config['secret_key'],
        )
        bucket = minio_config.get('bucket', 'workorder')

        project_root = _get_project_root()
        workorder_dir = os.path.join(project_root, 'workorder')
        local_zip = os.path.join(workorder_dir, 'deploy.zip')
        local_md5 = os.path.join(workorder_dir, 'deploy.zip.md5')
        extract_dir = os.path.join(workorder_dir, 'deploy')

        os.makedirs(workorder_dir, exist_ok=True)

        # 1. 下载远程 MD5
        remote_md5_value = ''
        try:
            client.fget_object(bucket, 'deploy.zip.md5', local_md5)
            with open(local_md5, 'r') as f:
                remote_md5_value = f.read().strip()
        except Exception:
            print("  基准包: MinIO 无 deploy.zip.md5，仅确保目录存在")
            ensure_workorder_dirs()
            return

        # 2. 检查本地是否需要下载
        need_download = True
        if os.path.exists(local_zip):
            local_md5_value = _calc_md5(local_zip)
            if local_md5_value == remote_md5_value:
                need_download = False
                print(f"  基准包: 本地 MD5 一致，跳过下载")
            else:
                print(f"  基准包: MD5 不一致，重新下载")

        # 3. 下载 deploy.zip
        if need_download:
            client.fget_object(bucket, 'deploy.zip', local_zip)
            local_md5_value = _calc_md5(local_zip)
            if local_md5_value != remote_md5_value:
                print(f"  基准包: 下载后 MD5 校验失败")
            else:
                print(f"  基准包: 下载完成")

        # 4. 检查是否需要解压
        extract_md5_file = os.path.join(extract_dir, '.zip_md5')
        need_extract = need_download or not os.path.exists(extract_dir) or not os.path.exists(extract_md5_file)

        if not need_extract and os.path.exists(extract_md5_file):
            with open(extract_md5_file, 'r') as f:
                cached_md5 = f.read().strip()
            if cached_md5 == remote_md5_value:
                print(f"  基准包: 已解压且 MD5 一致，跳过解压")
                images_dir = os.path.join(extract_dir, 'images')
                os.makedirs(images_dir, exist_ok=True)
                return

        # 5. 解压
        with zipfile.ZipFile(local_zip, 'r') as zf:
            zf.extractall(extract_dir)

        with open(extract_md5_file, 'w') as f:
            f.write(remote_md5_value)

        print(f"  基准包: 解压完成到 {extract_dir}")

        # 确保 images 目录存在
        images_dir = os.path.join(extract_dir, 'images')
        os.makedirs(images_dir, exist_ok=True)

    except Exception as e:
        print(f"  警告: 基准包准备失败: {e}")
        ensure_workorder_dirs()


def update_compose_image(compose_path, image_name, new_image_full):
    """更新 docker-compose.yml 中匹配 image_name 的服务镜像

    逐行扫描，找到 image: 行且包含 image_name 的行，替换为新镜像。
    保留原文件缩进和注释，仅替换匹配行。

    Args:
        compose_path: docker-compose.yml 文件路径
        image_name: 镜像名称（如 wms-bulk），用于匹配服务
        new_image_full: 新的完整镜像地址（如 192.168.100.213:8083/wms-bulk:xxx）

    Returns:
        bool: 是否成功更新
    """
    if not os.path.exists(compose_path):
        print(f"  提示: docker-compose.yml 不存在（workorder 尚未下载），跳过更新: {compose_path}")
        return False

    with open(compose_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    updated = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('image:') and image_name in stripped:
            indent = line[:len(line) - len(line.lstrip())]
            old_image = stripped.split('image:', 1)[1].strip()
            lines[i] = f"{indent}image: {new_image_full}\n"
            updated = True
            print(f"  更新服务镜像: {old_image} -> {new_image_full}")
            break

    if updated:
        with open(compose_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
    else:
        print(f"  提示: 未在 docker-compose.yml 中找到镜像名包含 '{image_name}' 的服务")

    return updated


def ensure_dependencies(required_commands):
    """确保所需命令都存在，否则打印错误信息并退出
    
    Args:
        required_commands: 需要检查的命令列表
    """
    missing = check_dependencies(required_commands)
    if missing:
        print(f"ERROR: 缺少必要的命令行工具: {', '.join(missing)}", file=sys.stderr)
        print(file=sys.stderr)
        print("解决方案:", file=sys.stderr)
        if 'docker' in missing:
            print("  1. 确保 Docker 命令可用", file=sys.stderr)
            print("     - 如果应用运行在 Docker 容器中，请在 docker-compose.yml 中挂载：", file=sys.stderr)
            print("       - /var/run/docker.sock:/var/run/docker.sock", file=sys.stderr)
            print("       - /usr/bin/docker:/usr/bin/docker (根据宿主机 docker 实际路径调整)", file=sys.stderr)
            print("     - 或者将此脚本配置为通过 SSH 远程执行到有 Docker 环境的机器上", file=sys.stderr)
        print(file=sys.stderr)
        sys.exit(1)


def run_cmd(cmd, check=True, timeout=600, **kwargs):
    """运行子进程命令，带友好的错误处理
    
    Args:
        cmd: 命令列表
        check: 是否检查返回码
        timeout: 超时秒数（默认 600s），避免 docker 命令无限挂起
        **kwargs: 传递给 subprocess.run 的其他参数
    
    Returns:
        subprocess.CompletedProcess
    """
    try:
        print(f"  执行: {' '.join(cmd)} (timeout={timeout}s)")
        result = subprocess.run(cmd, check=check, capture_output=True, text=True, timeout=timeout, **kwargs)
        if result.stdout:
            print(f"  输出: {result.stdout.strip()[:500]}")
        return result
    except subprocess.TimeoutExpired as e:
        print(f"ERROR: 命令超时 ({timeout}s): {' '.join(cmd)}", file=sys.stderr)
        if e.stdout:
            print(f"STDOUT: {str(e.stdout)[:500]}", file=sys.stderr)
        if e.stderr:
            print(f"STDERR: {str(e.stderr)[:500]}", file=sys.stderr)
        raise
    except subprocess.CalledProcessError as e:
        print(f"ERROR: 命令执行失败 (exit code {e.returncode})", file=sys.stderr)
        if e.stdout:
            print(f"STDOUT: {e.stdout}", file=sys.stderr)
        if e.stderr:
            print(f"STDERR: {e.stderr}", file=sys.stderr)
        raise
    except FileNotFoundError as e:
        print(f"ERROR: 命令不存在: {e.filename}", file=sys.stderr)
        print(f"请确保已安装所需工具并且在 PATH 环境变量中", file=sys.stderr)
        sys.exit(1)


def get_minio_client(endpoint, access_key, secret_key, secure=False):
    """创建 MinIO 客户端
    
    Args:
        endpoint: MinIO 服务地址 (host:port)
        access_key: Access Key
        secret_key: Secret Key
        secure: 是否使用 HTTPS
    
    Returns:
        Minio: MinIO 客户端实例
    """
    try:
        client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        print(f"  已连接 MinIO: {endpoint}")
        return client
    except Exception as e:
        print(f"ERROR: 连接 MinIO 失败: {e}", file=sys.stderr)
        sys.exit(1)


def upload_to_minio(client, bucket_name, file_path, object_name=None, make_bucket=False):
    """上传文件到 MinIO
    
    Args:
        client: MinIO 客户端实例
        bucket_name: 存储桶名称
        file_path: 本地文件路径
        object_name: 对象名称（默认为文件名）
        make_bucket: 如果存储桶不存在是否创建
    """
    if not os.path.exists(file_path):
        print(f"ERROR: 文件不存在: {file_path}", file=sys.stderr)
        sys.exit(1)
    
    if object_name is None:
        object_name = os.path.basename(file_path)
    
    try:
        # 检查存储桶是否存在
        if not client.bucket_exists(bucket_name):
            if make_bucket:
                client.make_bucket(bucket_name)
                print(f"  已创建存储桶: {bucket_name}")
            else:
                print(f"ERROR: 存储桶不存在: {bucket_name}", file=sys.stderr)
                sys.exit(1)
        
        # 上传文件
        file_size = os.path.getsize(file_path)
        print(f"  上传文件: {file_path} -> {bucket_name}/{object_name} ({file_size} bytes)")
        client.fput_object(bucket_name, object_name, file_path)
        print(f"  上传完成")
    except S3Error as e:
        print(f"ERROR: MinIO 上传失败: {e}", file=sys.stderr)
        sys.exit(1)


def upload_dir_to_minio(client, bucket_name, local_dir, prefix='', make_bucket=False):
    """递归上传目录到 MinIO
    
    Args:
        client: MinIO 客户端实例
        bucket_name: 存储桶名称
        local_dir: 本地目录路径
        prefix: 对象前缀
        make_bucket: 如果存储桶不存在是否创建
    """
    if not os.path.isdir(local_dir):
        print(f"ERROR: 目录不存在: {local_dir}", file=sys.stderr)
        sys.exit(1)
    
    try:
        # 检查存储桶是否存在
        if not client.bucket_exists(bucket_name):
            if make_bucket:
                client.make_bucket(bucket_name)
                print(f"  已创建存储桶: {bucket_name}")
            else:
                print(f"ERROR: 存储桶不存在: {bucket_name}", file=sys.stderr)
                sys.exit(1)
        
        count = 0
        for root, dirs, files in os.walk(local_dir):
            for filename in files:
                local_path = os.path.join(root, filename)
                relative_path = os.path.relpath(local_path, local_dir)
                # 转换为 Linux 风格路径
                relative_path = relative_path.replace('\\', '/')
                object_name = f"{prefix}/{relative_path}" if prefix else relative_path
                
                file_size = os.path.getsize(local_path)
                print(f"  上传: {relative_path} ({file_size} bytes)")
                client.fput_object(bucket_name, object_name, local_path)
                count += 1
        
        print(f"  目录上传完成，共上传 {count} 个文件")
    except S3Error as e:
        print(f"ERROR: MinIO 目录上传失败: {e}", file=sys.stderr)
        sys.exit(1)


# ========== Projectcode 编排相关 ==========

# projectcode 状态文件锁（防止并发写入冲突）
_projectcode_lock = threading.Lock()

# 跨平台文件锁（用于首次打包并发保护）
_pack_lock = threading.Lock()


def _get_projectcode_status_dir():
    """获取 projectcode 状态文件目录"""
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(scripts_dir)
    status_dir = os.path.join(project_root, 'workorder', '.projectcode_status')
    os.makedirs(status_dir, exist_ok=True)
    return status_dir


def _get_local_status_path(projectcode):
    """获取 projectcode 本地状态文件路径"""
    return os.path.join(_get_projectcode_status_dir(), f"{projectcode}.json")


def _calc_md5(filepath):
    """计算文件 MD5"""
    md5 = hashlib.md5()
    with open(filepath, 'rb') as f:
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                break
            md5.update(chunk)
    return md5.hexdigest()


def generate_md5_file(filepath):
    """生成与目标文件同名的 .md5 文件，内容为该文件的 MD5 哈希值

    Args:
        filepath: 目标文件路径

    Returns:
        str: 生成的 .md5 文件路径
    """
    md5_value = _calc_md5(filepath)
    md5_file_path = filepath + '.md5'
    with open(md5_file_path, 'w') as f:
        f.write(md5_value)
    print(f"  生成 MD5 文件: {md5_file_path} (md5={md5_value})")
    return md5_file_path


def _get_artifact_minio_path(projectcode, image_type, image_name):
    """生成增量产物在 MinIO 的归档路径

    格式: {projectcode}/{image_type}/{yyyymmdd}/{image_name}
    例如: JE250058/WCS/20260707/JE250058-WCS-20260707-01.image

    Args:
        projectcode: 项目代码（如 JE250058）
        image_type: 镜像类型（WMS/WCS/FRONTEND）
        image_name: 产物文件名（如 JE250058-WCS-20260707-01.image）

    Returns:
        str: MinIO 对象路径
    """
    # 从 image_name 提取日期（格式: projectcode-TYPE-yyyymmdd-序号.image）
    parts = image_name.split('-')
    if len(parts) >= 3 and len(parts[2]) == 8 and parts[2].isdigit():
        date_str = parts[2]
    else:
        date_str = datetime.now().strftime('%Y%m%d')
    return f"{projectcode}/{image_type}/{date_str}/{image_name}"


def _get_artifact_minio_prefix(projectcode, image_type):
    """生成增量产物在 MinIO 的扫描前缀（用于 list_objects）

    格式: {projectcode}/{image_type}/

    Args:
        projectcode: 项目代码（如 JE250058）
        image_type: 镜像类型（WMS/WCS/FRONTEND）

    Returns:
        str: MinIO 扫描前缀
    """
    return f"{projectcode}/{image_type}/"


def generate_canonical_image_name(projectcode, image_type, scan_dir=None, minio_client=None, minio_bucket='workorder'):
    """生成规范化镜像文件名 [projectcode]-[TYPE]-[yyyymmdd]-[序号].image

    序号自增逻辑：扫描本地目录或 MinIO 已有文件，取最大序号 +1

    Args:
        projectcode: 项目代码（如 JE250629）
        image_type: 镜像类型（WMS/WCS/FRONTEND）
        scan_dir: 本地扫描目录（优先）
        minio_client: MinIO 客户端（本地无目录时回退扫描）
        minio_bucket: MinIO 存储桶名

    Returns:
        str: 规范化文件名（不含路径）
    """
    today = datetime.now().strftime('%Y%m%d')
    prefix = f"{projectcode}-{image_type}-{today}-"

    max_seq = 0
    pattern = re.compile(re.escape(prefix) + r'(\d+)\.image$')

    # 扫描本地目录
    if scan_dir and os.path.isdir(scan_dir):
        for filename in os.listdir(scan_dir):
            m = pattern.match(filename)
            if m:
                seq = int(m.group(1))
                if seq > max_seq:
                    max_seq = seq

    # 扫描 MinIO（本地未找到时回退）
    # P1 优化: 路径格式 {projectcode}/{image_type}/{yyyymmdd}/
    if max_seq == 0 and minio_client is not None:
        try:
            prefix_minio = _get_artifact_minio_prefix(projectcode, image_type)
            objects = minio_client.list_objects(minio_bucket, prefix=prefix_minio, recursive=True)
            for obj in objects:
                filename = os.path.basename(obj.object_name)
                m = pattern.match(filename)
                if m:
                    seq = int(m.group(1))
                    if seq > max_seq:
                        max_seq = seq
        except Exception as e:
            print(f"  警告: 扫描 MinIO 已有文件失败: {e}")

    next_seq = max_seq + 1
    filename = f"{prefix}{next_seq:02d}.image"
    print(f"  生成镜像文件名: {filename}")
    return filename


def load_projectcode_status(projectcode, minio_config=None):
    """加载 projectcode 状态，优先本地，其次 MinIO，再不存在则初始化

    Args:
        projectcode: 项目代码
        minio_config: MinIO 配置 dict（endpoint/access_key/secret_key/bucket）

    Returns:
        dict: 状态字典
    """
    local_path = _get_local_status_path(projectcode)

    # 1. 优先读本地
    if os.path.exists(local_path):
        with open(local_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    # 2. 读 MinIO
    if minio_config:
        try:
            client = get_minio_client(
                minio_config['endpoint'],
                minio_config['access_key'],
                minio_config['secret_key'],
            )
            bucket = minio_config.get('bucket', 'workorder')
            local_tmp = local_path + '.tmp'
            client.fget_object(bucket, f"{projectcode}/status.json", local_tmp)
            with open(local_tmp, 'r', encoding='utf-8') as f:
                status = json.load(f)
            # 缓存到本地
            os.replace(local_tmp, local_path)
            return status
        except Exception:
            pass  # MinIO 不存在则初始化

    # 3. 初始化
    expected = collect_expected_branches(projectcode)

    # P0 修复: status.json 丢失时，检查 MinIO 是否已有 {projectcode}/deploy.zip
    # 如果已有 deploy.zip，说明首次打包已完成，避免误走首次模式导致重复打包
    first_pack_completed = False
    if minio_config:
        try:
            client = get_minio_client(
                minio_config['endpoint'],
                minio_config['access_key'],
                minio_config['secret_key'],
            )
            bucket = minio_config.get('bucket', 'workorder')
            try:
                client.stat_object(bucket, f"{projectcode}/deploy.zip")
                first_pack_completed = True
                print(f"  status.json 丢失但 MinIO 已有 {projectcode}/deploy.zip，标记 first_pack_completed=True")
            except Exception:
                pass  # deploy.zip 不存在，保持 first_pack_completed=False
        except Exception:
            pass  # MinIO 连接失败，保持 first_pack_completed=False

    status = {
        'projectcode': projectcode,
        'expected_branches': expected,
        'completed_branches': [],
        'first_pack_completed': first_pack_completed,
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat(),
    }
    save_projectcode_status(projectcode, status, minio_config)
    return status


def save_projectcode_status(projectcode, status, minio_config=None):
    """保存 projectcode 状态到本地 + MinIO（双写）

    Args:
        projectcode: 项目代码
        status: 状态字典
        minio_config: MinIO 配置
    """
    with _projectcode_lock:
        status['updated_at'] = datetime.now().isoformat()
        local_path = _get_local_status_path(projectcode)

        # 写本地
        with open(local_path, 'w', encoding='utf-8') as f:
            json.dump(status, f, ensure_ascii=False, indent=2)

        # 写 MinIO
        if minio_config:
            try:
                client = get_minio_client(
                    minio_config['endpoint'],
                    minio_config['access_key'],
                    minio_config['secret_key'],
                )
                bucket = minio_config.get('bucket', 'workorder')
                object_name = f"{projectcode}/status.json"
                client.fput_object(bucket, object_name, local_path)
            except Exception as e:
                print(f"  警告: 同步状态到 MinIO 失败: {e}")


def collect_expected_branches(projectcode):
    """扫描所有 trigger_actions 中 ref_projectcodes 里 projectcode 匹配的分支

    Args:
        projectcode: 项目代码

    Returns:
        list: 期望分支列表（去重）
    """
    try:
        import yaml
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(os.path.dirname(scripts_dir), 'trigger_actions.yaml')
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
        actions = config.get('trigger_actions', [])
        branches = set()
        for action in actions:
            ref_projectcodes = action.get('ref_projectcodes', {}) or {}
            for branch, pc in ref_projectcodes.items():
                if pc == projectcode:
                    branches.add(branch)
        return sorted(branches)
    except Exception as e:
        print(f"  警告: 收集期望分支失败: {e}")
        return []


def _get_branch_image_name(branch_images, branch):
    """从 branch_images 中提取镜像文件名，兼容新旧格式

    新格式: {branch: {'image_name': str, 'image_full': str}}
    旧格式: {branch: 'filename.image'}

    Args:
        branch_images: status['branch_images'] dict
        branch: 分支名

    Returns:
        str: 镜像文件名，不存在则返回 ''
    """
    val = branch_images.get(branch)
    if val is None:
        return ''
    if isinstance(val, dict):
        return val.get('image_name', '')
    return val  # 旧格式直接是字符串


def report_branch_completed(projectcode, branch, image_name, minio_config=None, image_full=None):
    """上报分支完成状态，并检查是否触发首次打包

    Args:
        projectcode: 项目代码
        branch: 完成的分支名
        image_name: 生成的规范化镜像文件名（docker save 产物，如 JE250058-WCS-20260703-01.image）
        minio_config: MinIO 配置
        image_full: 原始镜像地址（如 192.168.100.213:8083/app/wcs:dev-1.0.250509-shenhuo），
                    用于更新 docker-compose.yml 的 image 字段。前端 zip 类无此值则为 None。

    Returns:
        bool: 是否触发了首次打包
    """
    status = load_projectcode_status(projectcode, minio_config)

    if branch not in status['completed_branches']:
        status['completed_branches'].append(branch)
        # 记录分支与镜像信息的映射（image_name=产物文件名, image_full=原始镜像地址）
        if 'branch_images' not in status:
            status['branch_images'] = {}
        status['branch_images'][branch] = {
            'image_name': image_name,
            'image_full': image_full or '',
        }

    save_projectcode_status(projectcode, status, minio_config)

    # 检查是否所有期望分支都已完成
    expected = set(status['expected_branches'])
    completed = set(status['completed_branches'])
    if expected and expected.issubset(completed) and not status['first_pack_completed']:
        print(f"  projectcode={projectcode} 所有分支已完成，触发首次打包")
        pack_and_upload_deploy(projectcode, minio_config)
        return True
    return False


_reconcile_lock = threading.Lock()  # 保留用于内部子步骤保护


# 方案 4: projectcode 级别并发控制（同 projectcode 串行，不同 projectcode 并行）
_projectcode_locks = {}
_projectcode_locks_guard = threading.Lock()


def _get_projectcode_lock(projectcode):
    """获取 projectcode 级别的锁（惰性创建）

    使用 RLock（可重入锁）：orchestrate_image_deploy 持有锁后会调用
    report_branch_completed → pack_and_upload_deploy，后者会再次获取
    同一把锁。Lock() 不可重入会导致死锁，必须用 RLock。
    """
    with _projectcode_locks_guard:
        if projectcode not in _projectcode_locks:
            _projectcode_locks[projectcode] = threading.RLock()
        return _projectcode_locks[projectcode]


def _reconcile_nexus_state(projectcode, current_branch, status):
    """对账 Nexus 仓库真实产物，补全本地 completed_branches

    首次模式触发时调用。Nexus 是构建产物的源头（source of truth），
    本函数确认相同 projectcode 的项目对应分支是否已在 Nexus 存在产物：
    1. 扫描 trigger_actions.yaml，找到相同 projectcode 的所有项目分支
       （每个分支对应一个 action：project_pattern + image_type + script + Nexus 凭证）
    2. 对每个项目分支（跳过当前触发的分支和已完成分支）：
       a. 确认该分支在 Nexus 是否已存在产物（不传 IID，取最新产物）
       b. 存在 → 拉取最新产物 + save/下载到本地 workorder/deploy/images/ → 标记完成
       c. 不存在 → 跳过，等待该分支 pipeline 触发

    注意：本函数仅查询 Nexus，不涉及 MinIO。调用方负责后续的 save_projectcode_status。
    并发安全：调用方（orchestrate_image_deploy / orchestrate_file_deploy）已持有
    _get_projectcode_lock(projectcode)，同 projectcode 串行执行，无需额外加锁。

    Args:
        projectcode: 项目代码
        current_branch: 当前触发的分支名（避免重复拉取）
        status: 当前本地状态 dict

    Returns:
        dict: 对账后的 status
    """
    try:
        import yaml
        import importlib.util

        # 1. 扫描 trigger_actions.yaml，找到相同 projectcode 的所有项目分支
        #    例如 projectcode=JE250629 关联：
        #      - wms-application / dev-bulk (WMS)
        #      - shdy-dispatch-system / dev-1.0.250624 (WCS)
        #      - acre-web / wms-east-hope (FRONTEND)
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(os.path.dirname(scripts_dir), 'trigger_actions.yaml')
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
        actions = config.get('trigger_actions', [])

        project_branches = {}  # branch -> action config
        for action in actions:
            ref_projectcodes = action.get('ref_projectcodes', {}) or {}
            for branch, pc in ref_projectcodes.items():
                if pc == projectcode:
                    project_branches[branch] = action

        if not project_branches:
            return status

        # 2. 确认相同 projectcode 的项目对应分支是否已在 Nexus 存在产物
        images_dir = get_workorder_images_dir()
        os.makedirs(images_dir, exist_ok=True)
        existing_completed = set(status.get('completed_branches', []))

        for branch, action in project_branches.items():
            # 跳过当前触发的分支（由调用方处理下载）
            if branch == current_branch:
                continue
            # 跳过已完成分支（但需验证对应产物文件实际存在，否则重新下载）
            if branch in existing_completed:
                cached_image = _get_branch_image_name(status.get('branch_images', {}), branch)
                if cached_image:
                    cached_path = os.path.join(images_dir, cached_image)
                    if os.path.exists(cached_path) and os.path.exists(cached_path + '.md5'):
                        continue  # 产物文件存在，跳过
                    print(f"  对账: {branch} 已标记完成但产物缺失，重新下载")
                else:
                    print(f"  对账: {branch} 已标记完成但无文件名记录，重新下载")

            variables = action.get('variables', {}) or {}
            nexus_url = variables.get('NEXUS_URL', '')
            nexus_user = variables.get('NEXUS_USER', '')
            nexus_password = variables.get('NEXUS_PASSWORD', '')
            docker_registry_url = variables.get('DOCKER_REGISTRY_URL', '')
            script_name = action.get('script', '')
            image_type = action.get('image_type', '')

            if not nexus_url or not nexus_user:
                print(f"  对账跳过: {branch} (缺少 Nexus 配置)")
                continue

            # 动态导入对应脚本模块
            script_path = os.path.join(scripts_dir, script_name)
            if not os.path.exists(script_path):
                print(f"  对账跳过: {branch} (脚本 {script_name} 不存在)")
                continue

            try:
                mod_name = script_name.replace('.py', '').replace('-', '_')
                spec = importlib.util.spec_from_file_location(mod_name, script_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)

                image_full = ''  # docker 镜像类会赋值，zip 类保持空

                # 3a. Docker 镜像类：确认 Nexus docker-hosted 是否有该分支最新镜像
                if hasattr(mod, 'search_docker_image'):
                    # 不传 IID，取该分支最新产物
                    image = mod.search_docker_image(nexus_url, nexus_user, nexus_password, branch)
                    if not image:
                        print(f"  对账: {branch} 在 Nexus 未找到镜像，等待 pipeline 触发")
                        continue

                    image_full = f"{docker_registry_url}/{image['name']}:{image['version']}"
                    print(f"  对账: {branch} 找到最新镜像 {image_full} (iid={image['iid']})")

                    # 生成规范化文件名 + docker login + pull + save
                    image_name = generate_canonical_image_name(
                        projectcode, image_type, scan_dir=images_dir
                    )
                    image_path = os.path.join(images_dir, image_name)
                    run_cmd(['docker', 'login', docker_registry_url, '-u', nexus_user, '-p', nexus_password])
                    run_cmd(['docker', 'pull', image_full])
                    run_cmd(['docker', 'save', '-o', image_path, image_full])
                    generate_md5_file(image_path)
                    print(f"  对账: {branch} 镜像已保存 {image_name}")

                # 3b. 前端 zip 类：下载 zip 并解压到 deploy/nginx/dist
                elif hasattr(mod, 'search_binary_libs'):
                    # 不传 IID，取该分支最新产物
                    binary = mod.search_binary_libs(nexus_url, nexus_user, nexus_password, branch)
                    if not binary:
                        print(f"  对账: {branch} 在 Nexus 未找到 zip，等待 pipeline 触发")
                        continue

                    download_url = binary.get('download_url', '')
                    if not download_url:
                        print(f"  对账: {branch} 无下载 URL")
                        continue

                    print(f"  对账: {branch} 找到最新 zip {binary['name']}")

                    # 下载 zip 到临时文件
                    import tempfile as _tempfile
                    tmp_zip = _tempfile.mktemp(prefix='frontend_', suffix='.zip')
                    import requests as _requests
                    session = _requests.Session()
                    session.auth = (nexus_user, nexus_password)
                    resp = session.get(download_url, stream=True, timeout=120)
                    if resp.status_code == 401 and session.auth:
                        session.auth = None
                        resp = session.get(download_url, stream=True, timeout=120)
                    if resp.status_code != 200:
                        print(f"  对账: {branch} 下载失败 status={resp.status_code}")
                        continue
                    with open(tmp_zip, 'wb') as f:
                        for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                            f.write(chunk)

                    # 解压到 deploy/nginx/dist（清空旧内容后解压）
                    import shutil as _shutil
                    import zipfile as _zipfile
                    deploy_dir = get_workorder_deploy_dir()
                    dist_dir = os.path.join(deploy_dir, 'nginx', 'dist')
                    os.makedirs(dist_dir, exist_ok=True)
                    for item in os.listdir(dist_dir):
                        item_path = os.path.join(dist_dir, item)
                        if os.path.isdir(item_path):
                            _shutil.rmtree(item_path)
                        else:
                            os.remove(item_path)
                    with _zipfile.ZipFile(tmp_zip, 'r') as zf:
                        zf.extractall(dist_dir)
                    os.remove(tmp_zip)

                    # 生成规范化文件名（仅用于状态记录，不生成 .image 文件）
                    image_name = generate_canonical_image_name(
                        projectcode, image_type, scan_dir=images_dir
                    )
                    print(f"  对账: {branch} 前端 zip 已解压到 {dist_dir}")

                else:
                    print(f"  对账跳过: {branch} (脚本 {script_name} 无 search 函数)")
                    continue

                # 标记该分支完成
                if branch not in status['completed_branches']:
                    status['completed_branches'].append(branch)
                if 'branch_images' not in status:
                    status['branch_images'] = {}
                status['branch_images'][branch] = {
                    'image_name': image_name,
                    'image_full': image_full,
                }

            except Exception as e:
                print(f"  对账查询失败: {branch}, error={e}")
                continue

        print(f"  对账完成: completed_branches={status.get('completed_branches', [])}")
        return status
    except Exception as e:
        print(f"  警告: 对账 Nexus 失败: {e}")
        return status


def orchestrate_image_deploy(image_full, projectcode, image_type, branch, minio_config, docker_registry_url=None, nexus_user=None, nexus_password=None):
    """统一编排镜像部署流程（方案 4: projectcode 级别加锁，同 pc 串行）"""
    with _get_projectcode_lock(projectcode):
        return _orchestrate_image_deploy_impl(image_full, projectcode, image_type, branch, minio_config, docker_registry_url=docker_registry_url, nexus_user=nexus_user, nexus_password=nexus_password)


def _orchestrate_image_deploy_impl(image_full, projectcode, image_type, branch, minio_config, docker_registry_url=None, nexus_user=None, nexus_password=None):
    """统一编排镜像部署流程实现

    Args:
        image_full: 完整镜像地址（如 192.168.100.213:8083/wms-bulk:v1）
        projectcode: 项目代码
        image_type: 镜像类型（WMS/WCS/FRONTEND）
        branch: 当前分支名
        minio_config: MinIO 配置 dict
        docker_registry_url: Docker 仓库地址（用于 login）
        nexus_user: Docker 仓库用户名
        nexus_password: Docker 仓库密码

    Returns:
        str: 生成的镜像文件名
    """
    # 0. 确保基准包已从 MinIO 下载并解压到 workorder/deploy/
    #    首次模式下载基准包，增量模式跳过（避免覆盖 deploy/images/ 中的产物）
    _ensure_base_deploy_package(minio_config, projectcode)

    # 1. 加载状态判断模式
    status = load_projectcode_status(projectcode, minio_config)
    first_pack_completed = status.get('first_pack_completed', False)

    # 1.5 首次模式：对账 Nexus 仓库真实产物，补全本地 completed_branches
    # 注意：对账跳过当前分支（由本函数后续步骤处理），因此对账后不能立即
    # 检查"所有分支完成"并触发首次打包——当前分支产物还未 save。
    # 首次打包由 report_branch_completed 在当前分支 save 完成后自然触发。
    if not first_pack_completed:
        status = _reconcile_nexus_state(projectcode, branch, status)
        save_projectcode_status(projectcode, status, minio_config)

    # 1.6 如果当前分支已在对账完成的列表中，验证产物文件存在性
    if branch in status.get('completed_branches', []):
        cached_image = _get_branch_image_name(status.get('branch_images', {}), branch)
        # 首次模式：检查本地 deploy/images/ 是否有对应产物文件
        if not first_pack_completed and cached_image:
            cached_path = os.path.join(get_workorder_images_dir(), cached_image)
            if os.path.exists(cached_path) and os.path.exists(cached_path + '.md5'):
                print(f"  当前分支 {branch} 已对账完成且产物存在，跳过下载")
                return cached_image
            print(f"  当前分支 {branch} 已标记完成但产物缺失，重新生成")
            # 从 completed_branches 中移除，让后续步骤重新 save
            status['completed_branches'] = [b for b in status.get('completed_branches', []) if b != branch]
        elif not cached_image:
            # 已标记完成但无产物文件名记录，需要重新生成
            print(f"  当前分支 {branch} 已标记完成但无产物记录，重新生成")
            status['completed_branches'] = [b for b in status.get('completed_branches', []) if b != branch]
        else:
            # P0 修复: 增量模式下不跳过 save，因为 pipeline 触发了新版本
            # 首次模式的跳过逻辑已在上面 if not first_pack_completed 分支处理
            print(f"  当前分支 {branch} 已标记完成，增量模式继续 save 新版本")

    # 2. 生成规范化文件名
    if first_pack_completed:
        # 增量模式：扫描 MinIO 的 projectcode/images/ 路径
        minio_client = get_minio_client(
            minio_config['endpoint'],
            minio_config['access_key'],
            minio_config['secret_key'],
        )
        image_name = generate_canonical_image_name(
            projectcode, image_type,
            minio_client=minio_client,
            minio_bucket=minio_config.get('bucket', 'workorder'),
        )
    else:
        # 首次模式：扫描本地 workorder/deploy/images/
        images_dir = get_workorder_images_dir()
        image_name = generate_canonical_image_name(projectcode, image_type, scan_dir=images_dir)

    # 3. docker save 为规范化文件名
    if first_pack_completed:
        # 增量模式：save 到临时目录
        import tempfile
        tmp_dir = tempfile.mkdtemp(prefix='deploy_')
        image_path = os.path.join(tmp_dir, image_name)
    else:
        # 首次模式：save 到 workorder/deploy/images/
        images_dir = get_workorder_images_dir()
        image_path = os.path.join(images_dir, image_name)

    run_cmd(['docker', 'save', '-o', image_path, image_full])
    print(f"  镜像已保存: {image_path}")

    # 4. 生成 MD5 文件
    md5_path = generate_md5_file(image_path)

    # 5. 根据模式处理
    if first_pack_completed:
        # 增量模式：直接上传到 MinIO {projectcode}/{image_type}/{yyyymmdd}/
        minio_client = get_minio_client(
            minio_config['endpoint'],
            minio_config['access_key'],
            minio_config['secret_key'],
        )
        bucket = minio_config.get('bucket', 'workorder')
        artifact_path = _get_artifact_minio_path(projectcode, image_type, image_name)
        upload_to_minio(minio_client, bucket, image_path, artifact_path)
        upload_to_minio(minio_client, bucket, md5_path, artifact_path + '.md5')
        # 更新状态
        report_branch_completed(projectcode, branch, image_name, minio_config, image_full=image_full)
        # 清理临时文件
        try:
            os.remove(image_path)
            os.remove(md5_path)
            os.rmdir(tmp_dir)
        except OSError:
            pass
    else:
        # 首次模式：文件已保存在 workorder/deploy/images/，更新状态并检查是否触发打包
        report_branch_completed(projectcode, branch, image_name, minio_config, image_full=image_full)

    return image_name


def orchestrate_file_deploy(local_file_path, projectcode, image_type, branch, minio_config):
    """统一编排文件类部署流程（方案 4: projectcode 级别加锁）"""
    with _get_projectcode_lock(projectcode):
        return _orchestrate_file_deploy_impl(local_file_path, projectcode, image_type, branch, minio_config)


def _orchestrate_file_deploy_impl(local_file_path, projectcode, image_type, branch, minio_config):
    """统一编排文件类（非 Docker 镜像，如前端 zip）部署流程实现

    FRONTEND 类型处理：下载 zip 后直接解压到 deploy/nginx/dist 目录，
    替换前端静态资源。不生成 .image 文件，不存入 images 目录。

    Args:
        local_file_path: 已下载的本地文件路径（如 zip）
        projectcode: 项目代码
        image_type: 镜像类型（通常为 FRONTEND）
        branch: 当前分支名
        minio_config: MinIO 配置 dict

    Returns:
        str: 产物标识（用于状态记录）
    """
    import shutil as _shutil
    import zipfile as _zipfile

    # 0. 确保基准包已从 MinIO 下载并解压到 workorder/deploy/
    #    首次模式下载基准包，增量模式跳过（避免覆盖 deploy/nginx/dist 中的产物）
    _ensure_base_deploy_package(minio_config, projectcode)

    # 1. 加载状态判断模式
    status = load_projectcode_status(projectcode, minio_config)
    first_pack_completed = status.get('first_pack_completed', False)

    # 1.5 首次模式：对账 Nexus 仓库真实产物，补全本地 completed_branches
    # 注意：对账跳过当前分支（由本函数后续步骤处理），因此对账后不能立即
    # 检查"所有分支完成"并触发首次打包——当前分支产物还未处理。
    # 首次打包由 report_branch_completed 在当前分支处理完成后自然触发。
    if not first_pack_completed:
        status = _reconcile_nexus_state(projectcode, branch, status)
        save_projectcode_status(projectcode, status, minio_config)

    # 1.6 如果当前分支已在对账完成的列表中，验证产物文件存在性
    if branch in status.get('completed_branches', []):
        cached_image = _get_branch_image_name(status.get('branch_images', {}), branch)
        # 首次模式：检查本地 deploy/nginx/dist 是否已有产物
        if not first_pack_completed and cached_image:
            deploy_dir = get_workorder_deploy_dir()
            dist_dir = os.path.join(deploy_dir, 'nginx', 'dist')
            if os.path.isdir(dist_dir) and os.listdir(dist_dir):
                print(f"  当前分支 {branch} 已对账完成且产物存在，跳过下载")
                return cached_image
            print(f"  当前分支 {branch} 已标记完成但产物缺失，重新生成")
            # 从 completed_branches 中移除，让后续步骤重新处理
            status['completed_branches'] = [b for b in status.get('completed_branches', []) if b != branch]
        elif not cached_image:
            # 已标记完成但无产物文件名记录，需要重新生成
            print(f"  当前分支 {branch} 已标记完成但无产物记录，重新生成")
            status['completed_branches'] = [b for b in status.get('completed_branches', []) if b != branch]
        else:
            # P0 修复: 增量模式下不跳过处理，因为 pipeline 触发了新版本
            print(f"  当前分支 {branch} 已标记完成，增量模式继续处理新版本")

    minio_client = get_minio_client(
        minio_config['endpoint'],
        minio_config['access_key'],
        minio_config['secret_key'],
    )
    bucket = minio_config.get('bucket', 'workorder')

    # 2. 生成规范化文件名（用于状态记录，实际不生成 .image 文件）
    if first_pack_completed:
        image_name = generate_canonical_image_name(
            projectcode, image_type,
            minio_client=minio_client,
            minio_bucket=bucket,
        )
    else:
        images_dir = get_workorder_images_dir()
        image_name = generate_canonical_image_name(projectcode, image_type, scan_dir=images_dir)

    # 3. 解压 zip 到 deploy/nginx/dist 目录（替换前端静态资源）
    deploy_dir = get_workorder_deploy_dir()
    dist_dir = os.path.join(deploy_dir, 'nginx', 'dist')
    os.makedirs(dist_dir, exist_ok=True)

    # 清空旧 dist 内容
    if os.path.isdir(dist_dir):
        for item in os.listdir(dist_dir):
            item_path = os.path.join(dist_dir, item)
            if os.path.isdir(item_path):
                _shutil.rmtree(item_path)
            else:
                os.remove(item_path)
        print(f"  已清空 dist 目录: {dist_dir}")

    # 解压 zip
    with _zipfile.ZipFile(local_file_path, 'r') as zf:
        zf.extractall(dist_dir)
    print(f"  前端 zip 已解压到: {dist_dir}")

    # 4. 根据模式处理
    if first_pack_completed:
        # 增量模式：重新打包 deploy.zip 上传 MinIO
        # P1 优化: 归档路径 {projectcode}/FRONTEND/{yyyymmdd}/{image_name 但扩展名改为 .zip}
        local_zip = os.path.join(deploy_dir, '..', 'deploy.zip')
        print(f"  增量打包: {deploy_dir} -> {local_zip}")
        with _zipfile.ZipFile(local_zip, 'w', _zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(deploy_dir):
                for filename in files:
                    file_path = os.path.join(root, filename)
                    arcname = os.path.relpath(file_path, deploy_dir)
                    zf.write(file_path, arcname)
        md5_path = generate_md5_file(local_zip)
        # 前端 zip 归档文件名：将 .image 扩展名改为 .zip
        archive_name = image_name.rsplit('.', 1)[0] + '.zip' if image_name.endswith('.image') else image_name
        artifact_path = _get_artifact_minio_path(projectcode, image_type, archive_name)
        upload_to_minio(minio_client, bucket, local_zip, artifact_path)
        upload_to_minio(minio_client, bucket, md5_path, artifact_path + '.md5')
        # 更新状态
        report_branch_completed(projectcode, branch, image_name, minio_config, image_full='')
        # 清理临时文件
        try:
            os.remove(local_zip)
            os.remove(md5_path)
        except OSError:
            pass
    else:
        # 首次模式：文件已解压到 deploy/nginx/dist，更新状态并检查是否触发打包
        report_branch_completed(projectcode, branch, image_name, minio_config, image_full='')

    # 清理下载的 zip 文件
    try:
        os.remove(local_file_path)
    except OSError:
        pass

    return image_name


def pack_and_upload_deploy(projectcode, minio_config):
    """首次打包：更新 docker-compose.yml，打包 deploy.zip，上传 MinIO

    P1 修复: 使用 projectcode 级别锁替代全局 _pack_lock，
    避免不同 projectcode 的 pack 串行化。

    Args:
        projectcode: 项目代码
        minio_config: MinIO 配置 dict
    """
    with _get_projectcode_lock(projectcode):
        # 二次检查 first_pack_completed
        status = load_projectcode_status(projectcode, minio_config)
        if status.get('first_pack_completed'):
            print(f"  projectcode={projectcode} 已完成首次打包，跳过")
            return

        deploy_dir = get_workorder_deploy_dir()
        compose_path = os.path.join(deploy_dir, 'docker-compose.yml')

        # 1. 更新 docker-compose.yml 中所有 projectcode 相关服务的 image 字段
        if os.path.exists(compose_path):
            _update_compose_for_projectcode(compose_path, projectcode, status)
        else:
            print(f"  警告: docker-compose.yml 不存在，跳过更新")

        # 2. 打包 deploy.zip
        local_zip = os.path.join(deploy_dir, '..', 'deploy.zip')
        print(f"  打包: {deploy_dir} -> {local_zip}")
        with zipfile.ZipFile(local_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(deploy_dir):
                for filename in files:
                    file_path = os.path.join(root, filename)
                    arcname = os.path.relpath(file_path, deploy_dir)
                    zf.write(file_path, arcname)

        # 3. 生成 deploy.zip.md5
        md5_path = generate_md5_file(local_zip)

        # 4. 上传到 MinIO {projectcode}/ 路径
        if minio_config:
            minio_client = get_minio_client(
                minio_config['endpoint'],
                minio_config['access_key'],
                minio_config['secret_key'],
            )
            bucket = minio_config.get('bucket', 'workorder')
            upload_to_minio(minio_client, bucket, local_zip, f"{projectcode}/deploy.zip")
            upload_to_minio(minio_client, bucket, md5_path, f"{projectcode}/deploy.zip.md5")
        else:
            print(f"  警告: 未配置 MinIO，跳过上传（本地已打包: {local_zip}）")

        # 5. 标记 first_pack_completed=true
        status['first_pack_completed'] = True
        save_projectcode_status(projectcode, status, minio_config)

        # P0 修复: 同步更新 .zip_md5 标记，避免下次 _ensure_base_deploy_package 误判 MD5 不一致
        # pack_and_upload_deploy 打包后本地 deploy.zip 已更新，.zip_md5 需同步为新 MD5
        try:
            extract_md5_file = os.path.join(deploy_dir, '.zip_md5')
            new_md5 = _calc_md5(local_zip)
            with open(extract_md5_file, 'w') as f:
                f.write(new_md5)
        except Exception as e:
            print(f"  警告: 更新 .zip_md5 标记失败: {e}")

        print(f"  projectcode={projectcode} 首次打包完成")


def _update_compose_for_projectcode(compose_path, projectcode, status):
    """更新 docker-compose.yml 中所有 projectcode 相关服务的 image 字段

    根据 status['branch_images'] 中记录的 image_full（原始镜像地址）更新对应服务的 image 字段。
    注意：image 字段必须是 docker pull 能识别的地址（如 192.168.100.213:8083/app/wcs:tag），
    而不是 docker save 产物文件名（如 JE250058-WCS-20260703-01.image）。

    Args:
        compose_path: docker-compose.yml 路径
        projectcode: 项目代码
        status: projectcode 状态
    """
    branch_images = status.get('branch_images', {})
    if not branch_images:
        print(f"  警告: projectcode={projectcode} 无分支镜像记录，跳过 compose 更新")
        return

    with open(compose_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # image_type → docker-compose 服务名映射
    type_to_service = {
        'WMS': ['wms', 'wms-application'],
        'WCS': ['wcs', 'shdy-dispatch-system'],
        'FRONTEND': ['frontend', 'acre-web'],
    }

    # 按 image_type 分组，收集每个类型对应的 image_full（原始镜像地址）
    type_image_full = {}
    for branch, info in branch_images.items():
        # 兼容旧格式（info 为字符串）
        if isinstance(info, dict):
            image_name = info.get('image_name', '')
            image_full = info.get('image_full', '')
        else:
            image_name = info
            image_full = ''
        # 从文件名解析类型：JE250058-WCS-20260703-01.image → WCS
        parts = image_name.split('-')
        if len(parts) >= 2:
            img_type = parts[1]
            if img_type not in type_image_full and image_full:
                type_image_full[img_type] = image_full

    updated = 0
    for img_type, image_full in type_image_full.items():
        services = type_to_service.get(img_type, [])
        for service in services:
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('image:') and service in stripped.lower():
                    indent = line[:len(line) - len(line.lstrip())]
                    old_image = stripped.split('image:', 1)[1].strip()
                    lines[i] = f"{indent}image: {image_full}\n"
                    print(f"  更新服务 {service}: {old_image} -> {image_full}")
                    updated += 1
                    break

    if updated > 0:
        with open(compose_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        print(f"  docker-compose.yml 已更新 {updated} 处")
    else:
        print(f"  警告: 未找到需要更新的服务镜像")
