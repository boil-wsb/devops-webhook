"""部署脚本通用工具函数"""
import os
import shutil
import sys
import subprocess
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


def get_workorder_images_dir():
    """获取 workorder/deploy/images 目录路径（用于保存 docker 镜像 tar 文件）

    目录相对于项目根目录（scripts/ 的上级），不存在时自动创建。

    Returns:
        str: images 目录的绝对路径
    """
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(scripts_dir)
    images_dir = os.path.join(project_root, 'workorder', 'deploy', 'images')
    os.makedirs(images_dir, exist_ok=True)
    return images_dir


def get_workorder_deploy_dir():
    """获取 workorder/deploy 目录路径

    Returns:
        str: deploy 目录的绝对路径
    """
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(scripts_dir)
    return os.path.join(project_root, 'workorder', 'deploy')


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
        print(f"ERROR: docker-compose 文件不存在: {compose_path}", file=sys.stderr)
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


def run_cmd(cmd, check=True, **kwargs):
    """运行子进程命令，带友好的错误处理
    
    Args:
        cmd: 命令列表
        check: 是否检查返回码
        **kwargs: 传递给 subprocess.run 的其他参数
    
    Returns:
        subprocess.CompletedProcess
    """
    try:
        print(f"  执行: {' '.join(cmd)}")
        result = subprocess.run(cmd, check=check, capture_output=True, text=True, **kwargs)
        if result.stdout:
            print(f"  输出: {result.stdout.strip()[:500]}")
        return result
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
