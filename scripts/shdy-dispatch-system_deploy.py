"""shdy-dispatch-system 部署脚本
从 Nexus docker-hosted 仓库查询匹配分支的 Docker 镜像，pull + save + 上传 MinIO
"""
import os
import sys
import re
import requests

# 添加脚本目录到路径，导入公共工具
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deploy_utils import (
    ensure_dependencies, run_cmd, get_minio_client,
    ensure_workorder_dirs, orchestrate_image_deploy,
)


def search_docker_image(nexus_url, nexus_user, nexus_password, branch, iid=None):
    """在 Nexus docker-hosted 仓库中按分支名或 IID 查找镜像"""
    session = requests.Session()
    session.auth = (nexus_user, nexus_password)

    continuation_token = None
    matched = []

    while True:
        url = f"http://{nexus_url}/service/rest/v1/components?repository=docker-hosted"
        if continuation_token:
            url += f"&continuationToken={continuation_token}"
        resp = session.get(url, timeout=10)
        if resp.status_code != 200:
            break
        data = resp.json()
        for item in data.get('items', []):
            name = item.get('name', '')
            version = item.get('version', '')

            # 提取 IID（适用于 wms-application 风格 tag，如 bulk.v1.4798）
            comp_iid_match = re.search(r'\.v\d+\.(\d+)$', version) or re.search(r'\.v(\d+)$', version)
            comp_iid = int(comp_iid_match.group(1)) if comp_iid_match else 0

            # 规则1: IID 精确匹配
            iid_match = (iid is not None and comp_iid == iid)
            # 规则2: 版本号等于分支名（精确匹配，如 dev-1.0.250624 == dev-1.0.250624）
            exact_match = (version == branch)
            # 规则3: 分支名是版本号前缀（如 dev-1.0.250624 是 dev-1.0.250624-10677 的前缀）
            branch_match = branch in version

            if iid_match or exact_match or branch_match:
                if iid_match:
                    reason = 'IID'
                elif exact_match:
                    reason = 'exact'
                else:
                    reason = 'branch'
                matched.append({'name': name, 'version': version, 'iid': comp_iid, 'reason': reason})

        continuation_token = data.get('continuationToken')
        if not continuation_token:
            break

    if not matched:
        return None

    # 优先级: IID 精确 > 分支名精确匹配 > 分支名前缀匹配
    # 前缀匹配内部按版本号长度升序（短的优先，即无后缀的 tag 优先于带后缀的）
    priority = {'IID': 0, 'exact': 1, 'branch': 2}
    matched.sort(key=lambda x: (priority[x['reason']], len(x['version']), -x['iid']))
    return matched[0]


def main():
    # 确保 workorder 目录存在
    ensure_workorder_dirs()

    # 检查依赖
    ensure_dependencies(['docker'])

    project_name = os.environ.get('PROJECTNAME', '')
    ref = os.environ.get('REF', '')
    pipeline_iid = os.environ.get('PIPELINE_IID', '')
    nexus_url = os.environ.get('NEXUS_URL', '')
    docker_registry_url = os.environ.get('DOCKER_REGISTRY_URL', '')
    nexus_user = os.environ.get('NEXUS_USER', '')
    nexus_password = os.environ.get('NEXUS_PASSWORD', '')
    minio_endpoint = os.environ.get('MINIO_ENDPOINT', '')
    minio_access_key = os.environ.get('MINIO_ACCESS_KEY', '')
    minio_secret_key = os.environ.get('MINIO_SECRET_KEY', '')
    minio_bucket = os.environ.get('MINIO_BUCKET', 'workorder')

    # 从环境变量读取 projectcode 与 image_type（由 trigger_action 注入）
    projectcode = os.environ.get('PROJECTCODE', '')
    image_type = os.environ.get('IMAGE_TYPE', '')

    iid = int(pipeline_iid) if pipeline_iid.isdigit() else None

    print(f"=== shdy-dispatch-system deploy ===")
    print(f"PROJECTNAME={project_name}, REF={ref}, IID={iid}, PROJECTCODE={projectcode}, IMAGE_TYPE={image_type}")
    print(f"NEXUS_URL={nexus_url}, DOCKER_REGISTRY_URL={docker_registry_url}, MINIO_ENDPOINT={minio_endpoint}")

    # 1. 查询 Nexus 匹配镜像
    image = search_docker_image(nexus_url, nexus_user, nexus_password, ref, iid)
    if not image:
        print(f"ERROR: 未找到分支 {ref} (iid={iid}) 对应的 Docker 镜像")
        sys.exit(1)

    image_full = f"{docker_registry_url}/{image['name']}:{image['version']}"
    print(f"找到镜像: {image_full} (iid={image['iid']}, match={image['reason']})")

    # 2. Docker login + pull
    run_cmd(['docker', 'login', docker_registry_url, '-u', nexus_user, '-p', nexus_password])
    run_cmd(['docker', 'pull', image_full])

    # 3. 调用统一编排函数处理 save/命名/MD5/状态上报/打包/增量上传
    if projectcode and image_type and minio_endpoint and minio_access_key and minio_secret_key:
        minio_config = {
            'endpoint': minio_endpoint,
            'access_key': minio_access_key,
            'secret_key': minio_secret_key,
            'bucket': minio_bucket,
        }
        orchestrate_image_deploy(
            image_full=image_full,
            projectcode=projectcode,
            image_type=image_type,
            branch=ref,
            minio_config=minio_config,
        )
    else:
        print("ERROR: 缺少 PROJECTCODE/IMAGE_TYPE/MinIO 配置，无法执行编排部署")
        sys.exit(1)

    print("部署完成")


if __name__ == '__main__':
    main()
