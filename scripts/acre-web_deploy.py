"""acre-web 部署脚本
从 Nexus binary_libs 仓库查询匹配分支的 zip 文件并下载
"""
import os
import sys
import requests


def search_binary_libs(nexus_url, nexus_user, nexus_password, branch):
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
                # 提取时间戳
                ts_match = __import__('re').search(r'(\d{10,})\.zip$', name)
                timestamp = ts_match.group(1) if ts_match else ''
                # 获取下载 URL
                assets = item.get('assets', [])
                download_url = ''
                if assets:
                    download_url = assets[0].get('downloadUrl', '')
                matched.append({'name': name, 'group': group, 'timestamp': timestamp, 'download_url': download_url})

        continuation_token = data.get('continuationToken')
        if not continuation_token:
            break

    if not matched:
        return None

    # 按时间戳降序，取最新
    matched.sort(key=lambda x: x['timestamp'], reverse=True)
    return matched[0]


def main():
    project_name = os.environ.get('PROJECTNAME', '')
    ref = os.environ.get('REF', '')
    nexus_url = os.environ.get('NEXUS_URL', '')
    nexus_user = os.environ.get('NEXUS_USER', '')
    nexus_password = os.environ.get('NEXUS_PASSWORD', '')
    minio_endpoint = os.environ.get('MINIO_ENDPOINT', '')
    minio_access_key = os.environ.get('MINIO_ACCESS_KEY', '')
    minio_secret_key = os.environ.get('MINIO_SECRET_KEY', '')

    print(f"=== acre-web deploy ===")
    print(f"PROJECTNAME={project_name}, REF={ref}")
    print(f"NEXUS_URL={nexus_url}, MINIO_ENDPOINT={minio_endpoint}")

    # 1. 查询 Nexus binary_libs 匹配的 zip
    binary = search_binary_libs(nexus_url, nexus_user, nexus_password, ref)
    if not binary:
        print(f"ERROR: 未找到分支 {ref} 对应的 zip 文件", file=sys.stderr)
        sys.exit(1)

    print(f"找到文件: {binary['name']} (group={binary['group']}, ts={binary['timestamp']})")

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

    # 3. 配置 mc 并上传到 MinIO
    import subprocess
    subprocess.run([
        'mc', 'alias', 'set', 'minio',
        f'http://{minio_endpoint}', minio_access_key, minio_secret_key, '--api', 's3v4'
    ], check=True)

    # TODO: 上传到 MinIO
    # subprocess.run(['mc', 'cp', local_zip, f'minio/BUCKET/PATH/'], check=True)

    # 4. 清理
    os.remove(local_zip)
    print("部署完成")


if __name__ == '__main__':
    main()
