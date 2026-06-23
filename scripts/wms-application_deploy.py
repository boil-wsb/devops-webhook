"""wms-application 部署脚本
从 Nexus docker-hosted 仓库查询匹配分支的 Docker 镜像，pull + save + 上传 MinIO
"""
import os
import sys
import re
import subprocess
import requests


def search_docker_image(nexus_url, nexus_user, nexus_password, branch, iid=None):
    """在 Nexus docker-hosted 仓库中按分支名或 IID 查找镜像

    分支名到镜像版本前缀映射:
      dev-bulk        → bulk
      release-shenhuo → shenhuo
      其他            → 去掉 dev-/release- 前缀后的部分
    """
    # 分支名 → 版本号前缀映射
    BRANCH_VERSION_MAP = {
        'dev-bulk': 'bulk',
        'release-shenhuo': 'shenhuo',
    }
    version_prefix = BRANCH_VERSION_MAP.get(branch)
    if not version_prefix:
        # 兜底：去掉 dev-/release- 前缀
        for prefix in ('dev-', 'release-'):
            if branch.startswith(prefix):
                version_prefix = branch[len(prefix):]
                break
        if not version_prefix:
            version_prefix = branch

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
            print(f"Nexus API 返回 {resp.status_code}, 认证可能失败")
            break
        data = resp.json()
        for item in data.get('items', []):
            name = item.get('name', '')
            version = item.get('version', '')
            comp_iid_match = re.search(r'\.v\d+\.(\d+)$', version) or re.search(r'\.v(\d+)$', version)
            comp_iid = int(comp_iid_match.group(1)) if comp_iid_match else 0

            # 规则1: IID 精确匹配
            iid_match = (iid is not None and comp_iid == iid)
            # 规则2: 版本前缀匹配（如 bulk 匹配 bulk.v1.4798）
            branch_match = version.startswith(version_prefix)

            if iid_match or branch_match:
                reason = 'IID' if iid_match else 'branch'
                matched.append({'name': name, 'version': version, 'iid': comp_iid, 'reason': reason})

        continuation_token = data.get('continuationToken')
        if not continuation_token:
            break

    if not matched:
        return None

    # IID 匹配优先，其次按 IID 降序
    matched.sort(key=lambda x: (0 if x['reason'] == 'IID' else 1, -x['iid']))
    return matched[0]


def main():
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

    iid = int(pipeline_iid) if pipeline_iid.isdigit() else None

    print(f"=== wms-application deploy ===")
    print(f"PROJECTNAME={project_name}, REF={ref}, IID={iid}")
    print(f"NEXUS_URL={nexus_url}, DOCKER_REGISTRY_URL={docker_registry_url}, MINIO_ENDPOINT={minio_endpoint}")

    # 1. 查询 Nexus 匹配镜像
    image = search_docker_image(nexus_url, nexus_user, nexus_password, ref, iid)
    if not image:
        print(f"ERROR: 未找到分支 {ref} (iid={iid}) 对应的 Docker 镜像")
        sys.exit(1)

    image_full = f"{docker_registry_url}/{image['name']}:{image['version']}"
    print(f"找到镜像: {image_full} (iid={image['iid']}, match={image['reason']})")

    # 2. Docker login + pull + save
    subprocess.run(['docker', 'login', docker_registry_url, '-u', nexus_user, '-p', nexus_password], check=True)
    subprocess.run(['docker', 'pull', image_full], check=True)

    image_tar = f"{project_name}_{ref}.tar"
    subprocess.run(['docker', 'save', '-o', image_tar, image_full], check=True)
    print(f"镜像已保存: {image_tar}")

    # 3. 配置 mc 并上传到 MinIO
    subprocess.run([
        'mc', 'alias', 'set', 'minio',
        f'http://{minio_endpoint}', minio_access_key, minio_secret_key, '--api', 's3v4'
    ], check=True)

    # TODO: 组装安装包并上传
    # subprocess.run(['mc', 'cp', install_package, f'minio/BUCKET/PATH/'], check=True)

    # 4. 清理
    os.remove(image_tar)
    print("部署完成")


if __name__ == '__main__':
    main()
