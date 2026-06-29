"""acre-web 部署脚本
从 Nexus binary_libs 仓库查询匹配分支的 zip 文件并下载，然后上传到 MinIO
"""
import os
import sys
import re
import requests

# 添加脚本目录到路径，导入公共工具
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deploy_utils import (
    get_minio_client, upload_to_minio,
    ensure_workorder_dirs, orchestrate_file_deploy,
)


def search_binary_libs(nexus_url, nexus_user, nexus_password, branch, iid=None):
    """在 Nexus binary_libs 仓库中按分支名查找最新 zip"""
    session = requests.Session()
    session.auth = (nexus_user, nexus_password)

    continuation_token = None
    matched = []

    while True:
        url = f"http://{nexus_url}/service/rest/v1/components?repository=binary_libs"
        if continuation_token:
            url += f"&continuationToken={continuation_token}"
        resp = session.get(url, timeout=10)
        if resp.status_code == 401 and session.auth:
            # 认证失败，回退到匿名访问
            session.auth = None
            continue
        if resp.status_code != 200:
            break
        data = resp.json()
        for item in data.get('items', []):
            name = item.get('name', '')
            group = item.get('group', '')
            # 路径格式: /shdy/acre-web/{分支名}/{分支名}-{时间戳}.zip
            if f'acre-web/{branch}' in group or f'acre-web/{branch}' in name:
                # 提取时间戳或 IID
                ts_match = re.search(r'(\d{10,})\.zip$', name)
                iid_match = re.search(r'-(\d+)\.zip$', name)
                timestamp = ts_match.group(1) if ts_match else ''
                comp_iid = int(iid_match.group(1)) if iid_match else 0
                # 获取下载 URL
                assets = item.get('assets', [])
                download_url = ''
                if assets:
                    download_url = assets[0].get('downloadUrl', '')
                
                # IID 精确匹配优先
                iid_match_flag = (iid is not None and comp_iid == iid)
                matched.append({
                    'name': name, 
                    'group': group, 
                    'timestamp': timestamp, 
                    'iid': comp_iid,
                    'download_url': download_url,
                    'reason': 'IID' if iid_match_flag else 'branch'
                })

        continuation_token = data.get('continuationToken')
        if not continuation_token:
            break

    if not matched:
        return None

    # IID 匹配优先，其次按时间戳降序
    matched.sort(key=lambda x: (0 if x['reason'] == 'IID' else 1, x['timestamp']), reverse=True)
    return matched[0]


def main():
    # 确保 workorder 目录存在
    ensure_workorder_dirs()

    project_name = os.environ.get('PROJECTNAME', '')
    ref = os.environ.get('REF', '')
    pipeline_iid = os.environ.get('PIPELINE_IID', '')
    nexus_url = os.environ.get('NEXUS_URL', '')
    nexus_user = os.environ.get('NEXUS_USER', '')
    nexus_password = os.environ.get('NEXUS_PASSWORD', '')
    minio_endpoint = os.environ.get('MINIO_ENDPOINT', '')
    minio_access_key = os.environ.get('MINIO_ACCESS_KEY', '')
    minio_secret_key = os.environ.get('MINIO_SECRET_KEY', '')
    minio_bucket = os.environ.get('MINIO_BUCKET', 'workorder')

    # 从环境变量读取 projectcode 与 image_type（由 trigger_action 注入）
    projectcode = os.environ.get('PROJECTCODE', '')
    image_type = os.environ.get('IMAGE_TYPE', 'FRONTEND')

    iid = int(pipeline_iid) if pipeline_iid.isdigit() else None

    print(f"=== acre-web deploy ===")
    print(f"PROJECTNAME={project_name}, REF={ref}, IID={iid}, PROJECTCODE={projectcode}, IMAGE_TYPE={image_type}")
    print(f"NEXUS_URL={nexus_url}, MINIO_ENDPOINT={minio_endpoint}")

    # 1. 查询 Nexus binary_libs 匹配的 zip
    binary = search_binary_libs(nexus_url, nexus_user, nexus_password, ref, iid)
    if not binary:
        print(f"ERROR: 未找到分支 {ref} (iid={iid}) 对应的 zip 文件", file=sys.stderr)
        sys.exit(1)

    print(f"找到文件: {binary['name']} (group={binary['group']}, ts={binary['timestamp']}, match={binary['reason']})")

    local_zip = None
    try:
        # 2. 下载 zip
        if binary['download_url']:
            local_zip = os.path.basename(binary['name'])
            print(f"下载: {binary['download_url']}")
            session = requests.Session()
            session.auth = (nexus_user, nexus_password)
            resp = session.get(binary['download_url'], stream=True, timeout=120)
            if resp.status_code == 401 and session.auth:
                # 认证失败，回退到匿名访问
                session.auth = None
                resp = session.get(binary['download_url'], stream=True, timeout=120)
            if resp.status_code != 200:
                print(f"ERROR: 下载失败 status={resp.status_code}", file=sys.stderr)
                sys.exit(1)
            with open(local_zip, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                    f.write(chunk)
            print(f"已下载: {local_zip}")
        else:
            print("ERROR: 无下载 URL", file=sys.stderr)
            sys.exit(1)

        # 3. 调用统一编排函数处理文件（生成规范化文件名 + MD5 + 上传 MinIO + 状态上报）
        if projectcode and minio_endpoint and minio_access_key and minio_secret_key:
            minio_config = {
                'endpoint': minio_endpoint,
                'access_key': minio_access_key,
                'secret_key': minio_secret_key,
                'bucket': minio_bucket,
            }
            # 调用统一编排函数（适配前端 zip 文件，跳过 docker save）
            canonical_name = orchestrate_file_deploy(
                local_file_path=local_zip,
                projectcode=projectcode,
                image_type=image_type,
                branch=ref,
                minio_config=minio_config,
            )
            # 文件已被编排函数移动/上传，避免 finally 清理时报错
            local_zip = None
            print(f"编排完成: {canonical_name}")
        else:
            # 兼容旧逻辑：直接上传 MinIO
            if minio_endpoint and minio_access_key and minio_secret_key:
                minio_client = get_minio_client(minio_endpoint, minio_access_key, minio_secret_key)
                upload_to_minio(minio_client, minio_bucket, local_zip, f'web/{project_name}/{os.path.basename(local_zip)}')
                print(f"MinIO 上传完成（旧模式）")
            else:
                print("提示: 未配置完整的 MinIO 信息，跳过上传")

        print("部署完成")
    finally:
        # 4. 清理
        if local_zip and os.path.exists(local_zip):
            os.remove(local_zip)


if __name__ == '__main__':
    main()
